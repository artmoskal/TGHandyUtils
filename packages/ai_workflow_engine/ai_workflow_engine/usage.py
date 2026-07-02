"""Workflow usage metering — compatibility facade + the metered-call entry points.

H3 split: budgets/gates live in ``budget``, sinks + event recording in ``usage_events``,
provider metadata extraction in ``provider_usage``, price tables/cost math in ``pricing``,
token heuristics in ``token_estimation``, display in ``usage_rendering``. Public names keep
importing from ``ai_workflow_engine.usage`` (preserved through the next tag); this module
retains the metered-call orchestration (``invoke_metered_chat``/``record_image_usage``)
that composes those pieces around an actual provider call.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Literal, Optional

from langchain_core.messages import BaseMessage

from ai_workflow_engine._runtime_state import current_observation_capture
from ai_workflow_engine.budget import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
    WorkflowUsageContext,
    budget_from_config,
    check_budget_before_call,
    check_images_per_call,
    check_input_tokens_per_call,
    current_usage_context,
    workflow_usage_scope,
    _enforce_per_call_budget,
    _enforce_usd_budget,
)
from ai_workflow_engine.models import WorkflowUsageEvent, WorkflowUsageSummary
from ai_workflow_engine.observability_capture import (
    is_engine_worker_observation_active,
    langchain_messages_payload,
    langchain_response_payload,
)
from ai_workflow_engine.pricing import (
    estimate_cost_usd,
    _DEFAULT_PRICE_TABLE,
    _price_for_model,
    _price_overrides,
)
from ai_workflow_engine.provider_usage import _usage_event_from_chat_output
from ai_workflow_engine.token_estimation import estimate_text_tokens
from ai_workflow_engine.usage_events import (
    AsyncQueueUsageSink,
    InMemoryUsageSink,
    JsonlUsageSink,
    TeeUsageSink,
    UsageSink,
    record_usage_event,
)
from ai_workflow_engine.usage_rendering import format_usage_summary
from ai_workflow_engine.usage_support import (
    _details,
    _limit_float,
    _limit_int,
    _model_or_dict,
    _positive_float,
    _positive_int,
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
    config: Any = None,
    cost_class: Literal["metered", "subscription_notional"] = "metered",
    notional_usd: Optional[float] = None,
    provider: str = "openai",
) -> Any:
    """Invoke a LangChain chat model and record usage metadata when the provider returns it."""
    message_list = list(messages)
    if config is not None and not getattr(config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
        _record_metered_chat_request(node, message_list, attempt, model, metadata)
        try:
            output = llm.invoke(message_list)
        except Exception as exc:
            _record_metered_chat_error(node, exc, attempt, model, metadata)
            raise
        _record_metered_chat_response(node, output, attempt, model, metadata)
        return output
    check_input_tokens_per_call(estimate_text_tokens(message_list), node)
    check_budget_before_call("chat", node)
    _record_metered_chat_request(node, message_list, attempt, model, metadata)
    start = time.monotonic()
    try:
        output = llm.invoke(message_list)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _record_metered_chat_response(node, output, attempt, model, metadata)
        record_usage_event(
            _usage_event_from_chat_output(
                output,
                node=node,
                model=model,
                attempt=attempt,
                elapsed_ms=elapsed_ms,
                metadata=metadata,
                config=config,
                cost_class=cost_class,
                notional_usd=notional_usd,
                provider=provider,
            )
        )
        return output
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _record_metered_chat_error(node, exc, attempt, model, metadata)
        record_usage_event(
            WorkflowUsageEvent(
                operation="chat",
                node=node,
                model=model,
                attempt=attempt,
                elapsed_ms=elapsed_ms,
                success=False,
                error=str(exc)[:500],
                metadata=metadata or {},
                cost_class=cost_class,
                notional_usd=notional_usd,
            )
        )
        raise


def _record_metered_chat_request(
    node: str,
    messages: list[BaseMessage],
    attempt: int,
    model: str,
    metadata: Optional[dict[str, Any]],
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
    )


def _record_metered_chat_response(
    node: str,
    output: Any,
    attempt: int,
    model: str,
    metadata: Optional[dict[str, Any]],
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
    )


def _record_metered_chat_error(
    node: str,
    exc: Exception,
    attempt: int,
    model: str,
    metadata: Optional[dict[str, Any]],
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
    config: Any = None,
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
            config=config,
        ),
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        success=success,
        error=error,
        metadata=metadata or {},
    )
    record_usage_event(event)
    return event

