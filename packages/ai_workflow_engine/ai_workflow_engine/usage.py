"""Workflow usage metering — the metered-call entry points.

Owns the metered-call orchestration (``invoke_metered_chat``/``record_image_usage``) that
composes budgets, pricing, token estimation, and event recording around an actual provider
call. It is NOT an import facade: budgets/gates live in ``budget``, sinks + event recording in
``usage_events``, price math in ``pricing``, token heuristics in ``token_estimation``, display
in ``usage_rendering`` — import public names from ``ai_workflow_engine`` (package root) and
internals from their owners. The v0.10-era re-export block was removed by the v0.11
clean-contract line (manifest row M3): importing those names from here fails loudly.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Literal, Optional

from langchain_core.messages import BaseMessage

from ai_workflow_engine._runtime_state import current_observation_capture
from ai_workflow_engine.budget import (
    check_budget_before_call,
    check_input_tokens_per_call,
)
from ai_workflow_engine.models import WorkflowUsageEvent
from ai_workflow_engine.observability_capture import (
    is_engine_worker_observation_active,
    langchain_messages_payload,
    langchain_response_payload,
)
from ai_workflow_engine.pricing import estimate_cost_usd
from ai_workflow_engine.provider_usage import _usage_event_from_chat_output
from ai_workflow_engine.token_estimation import estimate_text_tokens
from ai_workflow_engine.usage_events import record_usage_event
from ai_workflow_engine.usage_contract import NormalizedTokenUsage, new_provider_invocation_id
from ai_workflow_engine.usage_support import (
    _details,
    _model_or_dict,
    _safe_int,
)

logger = logging.getLogger(__name__)


def invoke_metered_chat(
    llm: Any,
    messages: Iterable[BaseMessage],
    *,
    node: str,
    model: str = "",
    attempt: int = 1,
    metadata: Optional[dict[str, Any]] = None,
    cost_class: Optional[Literal["metered", "subscription_notional"]] = None,
    notional_usd: Optional[float] = None,
    provider: Optional[str] = None,
    invocation_id: str | None = None,
) -> Any:
    """Invoke a LangChain chat model and record usage metadata when the provider returns it.

    Budget checks run UNCONDITIONALLY (v0.9): the former usage-tracking flag bypass is gone —
    disabling product usage display must never disable engine budget gates.
    """
    message_list = list(messages)
    if cost_class is not None and cost_class not in ("metered", "subscription_notional"):
        raise ValueError(f"unsupported cost_class {cost_class!r}")
    client_cost_class = getattr(llm, "cost_class", None)
    resolved_cost_class = cost_class or (
        client_cost_class
        if client_cost_class in ("metered", "subscription_notional")
        else "metered"
    )
    if provider is not None and (not isinstance(provider, str) or not provider.strip()):
        raise ValueError("provider must be a non-blank string")
    client_provider = getattr(llm, "provider_label", None)
    resolved_provider = provider or (
        client_provider
        if isinstance(client_provider, str) and client_provider.strip()
        else "openai"
    )
    invocation_id = invocation_id or new_provider_invocation_id()
    check_input_tokens_per_call(estimate_text_tokens(message_list), node)
    check_budget_before_call("chat", node)
    _record_metered_chat_request(
        node, message_list, attempt, model, metadata, invocation_id
    )
    start = time.monotonic()
    try:
        from ai_workflow_engine.llm_protocol import (
            LLMUsageIdentity,
            llm_usage_identity_scope,
        )

        with llm_usage_identity_scope(
            LLMUsageIdentity(
                node=node,
                attempt=attempt,
                model=model,
                provider=resolved_provider,
                cost_class=resolved_cost_class,
                invocation_id=invocation_id,
            )
        ):
            output = llm.invoke(message_list)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _record_metered_chat_error(
            node, exc, attempt, model, metadata, invocation_id
        )
        provider_reported = getattr(exc, "notional_usd", None)
        if type(provider_reported) not in (int, float):
            provider_reported = notional_usd
        normalized_usage = getattr(exc, "normalized_usage", None)
        if not isinstance(normalized_usage, NormalizedTokenUsage):
            normalized_usage = None
        usage_error = getattr(exc, "usage_error", None)
        if usage_error not in (
            "usage_event_missing",
            "usage_event_malformed",
            "invalid_token_counters",
        ):
            usage_error = None
        usage_diagnostic = getattr(exc, "usage_diagnostic", None)
        if not isinstance(usage_diagnostic, str):
            usage_diagnostic = None
        record_usage_event(
            WorkflowUsageEvent(
                operation="chat",
                # Failed calls keep honest attribution too (codex ship-review finding:
                # the error path dropped provider, mislabeling failed non-OpenAI calls).
                provider=resolved_provider,
                node=node,
                model=model,
                attempt=attempt,
                invocation_id=invocation_id,
                elapsed_ms=elapsed_ms,
                success=False,
                error=str(exc)[:500],
                input_tokens=(
                    normalized_usage.raw_input_tokens
                    if normalized_usage is not None
                    else 0
                ),
                output_tokens=(
                    normalized_usage.raw_output_tokens
                    if normalized_usage is not None
                    else 0
                ),
                total_tokens=(
                    normalized_usage.raw_input_tokens
                    + normalized_usage.raw_output_tokens
                    if normalized_usage is not None
                    else 0
                ),
                input_token_details=(
                    {
                        "cache_read": normalized_usage.cache_read_input_tokens,
                        "cache_creation": normalized_usage.cache_creation_input_tokens,
                    }
                    if normalized_usage is not None
                    else {}
                ),
                output_token_details=(
                    {"reasoning": normalized_usage.reasoning_output_tokens}
                    if normalized_usage is not None
                    else {}
                ),
                normalized_usage=normalized_usage,
                usage_error=usage_error,
                usage_diagnostic=usage_diagnostic,
                provider_reported_notional_usd=(
                    provider_reported
                    if resolved_cost_class == "subscription_notional"
                    else None
                ),
                estimated_usd=(
                    provider_reported if resolved_cost_class == "metered" else None
                ),
                metadata=metadata or {},
                cost_class=resolved_cost_class,
            )
        )
        raise
    elapsed_ms = int((time.monotonic() - start) * 1000)
    _record_metered_chat_response(
        node, output, attempt, model, metadata, invocation_id
    )
    # Accounting is deliberately OUTSIDE the provider-call try/except. A budget or
    # pricing-policy refusal happens after a successful provider response; treating it as
    # a provider failure would emit a second usage event for the same invocation.
    record_usage_event(
        _usage_event_from_chat_output(
            output,
            node=node,
            model=model,
            attempt=attempt,
            elapsed_ms=elapsed_ms,
            metadata=metadata,
            cost_class=resolved_cost_class,
            notional_usd=notional_usd,
            provider=resolved_provider,
            invocation_id=invocation_id,
        )
    )
    return output


def _record_metered_chat_request(
    node: str,
    messages: list[BaseMessage],
    attempt: int,
    model: str,
    metadata: Optional[dict[str, Any]],
    invocation_id: str,
) -> None:
    capture = current_observation_capture()
    if capture is None or is_engine_worker_observation_active():
        return
    capture.record(
        node=node,
        attempt=attempt,
        decision="llm:request",
        phase="llm:request",
        kind="rendered_prompt",
        payload=langchain_messages_payload(messages),
        metadata=_metered_chat_observation_metadata(model, metadata),
        digest_metadata_key="prompt_digest",
        invocation_id=invocation_id,
    )


def _record_metered_chat_response(
    node: str,
    output: Any,
    attempt: int,
    model: str,
    metadata: Optional[dict[str, Any]],
    invocation_id: str,
) -> None:
    capture = current_observation_capture()
    if capture is None or is_engine_worker_observation_active():
        return
    capture.record(
        node=node,
        attempt=attempt,
        decision="llm:response",
        phase="llm:response",
        kind="llm_response",
        payload=langchain_response_payload(output, text=_output_text(output)),
        metadata=_metered_chat_observation_metadata(model, metadata),
        digest_metadata_key="response_digest",
        invocation_id=invocation_id,
    )


def _record_metered_chat_error(
    node: str,
    exc: Exception,
    attempt: int,
    model: str,
    metadata: Optional[dict[str, Any]],
    invocation_id: str,
) -> None:
    capture = current_observation_capture()
    if capture is None or is_engine_worker_observation_active():
        return
    error = str(exc) or exc.__class__.__name__
    capture.record(
        node=node,
        attempt=attempt,
        decision="llm:response",
        phase="llm:response",
        kind="llm_response",
        payload={"error_type": exc.__class__.__name__, "error": error},
        severity="error",
        error=error,
        metadata={
            **_metered_chat_observation_metadata(model, metadata),
            "error_type": exc.__class__.__name__,
        },
        digest_metadata_key="response_digest",
        invocation_id=invocation_id,
    )


def _metered_chat_observation_metadata(
    model: str,
    metadata: Optional[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "transport": "langchain",
        "model": model,
        "metered_chat": True,
        **(metadata or {}),
    }


def _output_text(output: Any) -> str:
    content = getattr(output, "content", output)
    if isinstance(content, str):
        return content
    return str(content)


def record_image_usage(
    *,
    node: str,
    model: str,
    provider: str = "openai",
    usage: Any = None,
    request_id: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
    success: bool = True,
    error: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> WorkflowUsageEvent:
    usage_dict = _model_or_dict(usage)
    input_details = _details(usage_dict.get("input_tokens_details") or usage_dict.get("input_token_details"))
    output_details = _details(usage_dict.get("output_tokens_details") or usage_dict.get("output_token_details"))
    input_tokens = _safe_int(usage_dict.get("input_tokens"))
    output_tokens = _safe_int(usage_dict.get("output_tokens"))
    total_tokens = _safe_int(usage_dict.get("total_tokens"), input_tokens + output_tokens)
    event = WorkflowUsageEvent(
        provider=provider,
        operation="image",
        node=node,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_token_details=input_details,
        output_token_details=output_details,
        estimated_usd=estimate_cost_usd(
            model,
            "image",
            input_tokens,
            output_tokens,
            input_details,
            output_details,
        ),
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        success=success,
        error=error,
        metadata=metadata or {},
    )
    record_usage_event(event)
    return event
