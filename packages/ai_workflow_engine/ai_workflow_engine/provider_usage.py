"""Provider usage-metadata extraction: normalize chat output into a usage event."""

from __future__ import annotations

from typing import Any, Literal, Optional

from ai_workflow_engine.models import WorkflowUsageEvent
from ai_workflow_engine.pricing import estimate_cost_usd
from ai_workflow_engine.usage_contract import NormalizedTokenUsage
from ai_workflow_engine.usage_support import _details, _model_or_dict, _safe_int


def _usage_event_from_chat_output(
    output: Any,
    *,
    node: str,
    model: str,
    attempt: int,
    elapsed_ms: int,
    metadata: Optional[dict[str, Any]],
    cost_class: Literal["metered", "subscription_notional"] = "metered",
    notional_usd: Optional[float] = None,
    provider: str = "openai",
    invocation_id: str | None = None,
) -> WorkflowUsageEvent:
    usage = _model_or_dict(getattr(output, "usage_metadata", None))
    response_metadata = _model_or_dict(getattr(output, "response_metadata", None))
    token_usage = _model_or_dict(response_metadata.get("token_usage"))
    if not usage and token_usage:
        usage = {
            "input_tokens": token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0,
            "output_tokens": token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0,
            "total_tokens": token_usage.get("total_tokens") or 0,
        }
    input_tokens = _safe_int(usage.get("input_tokens"))
    output_tokens = _safe_int(usage.get("output_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens"), input_tokens + output_tokens)
    input_details = _details(usage.get("input_token_details") or usage.get("input_tokens_details"))
    output_details = _details(usage.get("output_token_details") or usage.get("output_tokens_details"))
    normalized_usage = getattr(output, "normalized_usage", None)
    if not isinstance(normalized_usage, NormalizedTokenUsage):
        normalized_usage = None
    usage_error = getattr(output, "usage_error", None)
    if usage_error not in (
        "usage_event_missing",
        "usage_event_malformed",
        "invalid_token_counters",
    ):
        usage_error = None
    usage_diagnostic = getattr(output, "usage_diagnostic", None)
    if not isinstance(usage_diagnostic, str):
        usage_diagnostic = None
    provider_reported = getattr(output, "provider_reported_notional_usd", None)
    if type(provider_reported) not in (int, float):
        provider_reported = None
    if provider_reported is None:
        provider_reported = notional_usd
    effective_model = response_metadata.get("model_name") or model
    estimated_usd = None
    if cost_class == "metered":
        estimated_usd = estimate_cost_usd(
            effective_model,
            "chat",
            input_tokens,
            output_tokens,
            input_details,
            output_details,
        )
    return WorkflowUsageEvent(
        operation="chat",
        provider=provider,
        cost_class=cost_class,
        node=node,
        model=effective_model,
        attempt=attempt,
        invocation_id=invocation_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_token_details=input_details,
        output_token_details=output_details,
        estimated_usd=estimated_usd,
        normalized_usage=normalized_usage,
        usage_error=usage_error,
        usage_diagnostic=usage_diagnostic,
        provider_reported_notional_usd=(
            provider_reported if cost_class == "subscription_notional" else None
        ),
        request_id=response_metadata.get("id") or response_metadata.get("request_id"),
        elapsed_ms=elapsed_ms,
        metadata=metadata or {},
    )
