"""Usage-summary rendering (display only — never budget enforcement side effects)."""

from __future__ import annotations

from typing import Any, Optional

from ai_workflow_engine.models import WorkflowUsageEvent, WorkflowUsageSummary
from ai_workflow_engine.usage_support import _safe_int


def format_usage_summary(summary: Optional[WorkflowUsageSummary]) -> str:
    if not summary or not summary.events:
        return ""
    tool_total = f"{summary.tool_call_count} tool, " if summary.tool_call_count else ""
    char_total = f"{_compact_character_total(summary.tool_character_count)} chars, " if summary.tool_call_count else ""
    rows = [
        "AI usage:",
        "node/provider        op       in   cache     out      cost",
    ]
    rows.extend(_format_usage_event_row(event) for event in summary.events)
    # Money honesty in the caption (owner requirement 2026-07-03): "billed" is REAL money
    # charged by API providers; "subscription" is plan value already paid for — never money
    # spent by this run. Absent is not unknown: with NO metered events, billed is a true $0;
    # with metered events of unknown price it stays "?". The subscription segment appears
    # only when subscription calls exist ("no flat-rate usage" must not read as "unknown").
    has_metered = any(
        getattr(event, "cost_class", "metered") == "metered" for event in summary.events
    )
    has_subscription = any(
        getattr(event, "cost_class", "metered") == "subscription_notional" for event in summary.events
    )
    if has_metered:
        billed_segment = f"billed (API): {_format_usage_cost(summary.metered_usd)}"
    else:
        billed_segment = "billed (API): $0"
    if has_subscription:
        if summary.notional_usd is not None:
            subscription_segment = (
                f" · subscription: ~{_format_usage_cost(summary.notional_usd)} plan value, no extra charge"
            )
        else:
            subscription_segment = " · subscription: plan-covered (value unknown), no extra charge"
    else:
        subscription_segment = ""
    total_line = (
        "total: "
        f"{summary.text_call_count} text, {summary.image_call_count} image, "
        f"{tool_total}"
        f"{_compact_token_count(summary.input_tokens)} in, "
        f"{_compact_token_count(summary.cached_input_tokens)} cached, "
        f"{_compact_token_count(summary.output_tokens)} out, "
        f"{char_total}"
    )
    rows.append(total_line.rstrip(", "))
    rows.append(f"{billed_segment}{subscription_segment}")
    return "\n".join(rows)


def _format_usage_event_row(event: WorkflowUsageEvent) -> str:
    label = _usage_event_label(event)
    operation = getattr(event, "operation", "chat")
    op = {"chat": "txt", "image": "img", "tool": "tool"}.get(operation, str(operation)[:4] or "call")
    input_tokens = _safe_int(getattr(event, "input_tokens", 0))
    cached_tokens = _cached_input_tokens_for_event(event)
    output_tokens = _safe_int(getattr(event, "output_tokens", 0))
    input_value = _compact_tool_input(event) if operation == "tool" else _compact_token_count(input_tokens)
    return (
        f"{label:<20} {op:<4} "
        f"{input_value:>7} "
        f"{_compact_token_count(cached_tokens):>7} "
        f"{_compact_token_count(output_tokens):>7} "
        f"{_format_event_cost(event):>9}"
    )


def _format_event_cost(event: WorkflowUsageEvent) -> str:
    """Per-row cost: real money verbatim; subscription value prefixed '~'; a subscription
    call with unknown value shows 'incl.' (covered by the plan) — never a scary '?'."""

    cost = _event_display_cost(event)
    if getattr(event, "cost_class", "metered") == "subscription_notional":
        return f"~{_format_usage_cost(cost)}" if cost is not None else "incl."
    return _format_usage_cost(cost)


def _usage_event_label(event: WorkflowUsageEvent) -> str:
    node = (getattr(event, "node", "") or getattr(event, "operation", "") or "provider_call").strip()
    provider = (getattr(event, "provider", "") or "").strip()
    if getattr(event, "operation", "") == "image" and provider and provider != "openai":
        node = f"{node}:{provider}"
    attempt = _safe_int(getattr(event, "attempt", 1), 1)
    if attempt > 1:
        node = f"{node}#{attempt}"
    return _fit_usage_label(node, 20)


def _fit_usage_label(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    head = max(width - 4, 1)
    return f"{value[:head]}..."


def _cached_input_tokens_for_event(event: WorkflowUsageEvent) -> int:
    details = getattr(event, "input_token_details", {}) or {}
    if not isinstance(details, dict):
        return 0
    typed_cached = _safe_int(details.get("text_cached_tokens") or details.get("cached_text_tokens"))
    typed_cached += _safe_int(details.get("image_cached_tokens") or details.get("cached_image_tokens"))
    generic_cached = _safe_int(details.get("cache_read") or details.get("cached_tokens"))
    return typed_cached if typed_cached else generic_cached


def _compact_token_count(value: Any) -> str:
    tokens = _safe_int(value)
    sign = "-" if tokens < 0 else ""
    tokens = abs(tokens)
    if tokens >= 1_000_000:
        return f"{sign}{tokens / 1_000_000:.1f}m".replace(".0m", "m")
    if tokens >= 1_000:
        return f"{sign}{tokens / 1_000:.1f}k".replace(".0k", "k")
    return f"{sign}{tokens}"


def _compact_tool_input(event: WorkflowUsageEvent) -> str:
    details = getattr(event, "input_token_details", {}) or {}
    metadata = getattr(event, "metadata", {}) or {}
    characters = _safe_int(details.get("characters") or metadata.get("character_count"))
    if characters:
        return _compact_character_count(characters)
    return _compact_token_count(getattr(event, "input_tokens", 0))


def _compact_character_count(value: Any) -> str:
    chars = _safe_int(value)
    if chars <= 0:
        return "0"
    if chars >= 1000:
        return f"{chars / 1000:.1f}kch".replace(".0kch", "kch")
    return f"{chars}ch"


def _compact_character_total(value: Any) -> str:
    chars = _safe_int(value)
    if chars <= 0:
        return "0"
    if chars >= 1000:
        return f"{chars / 1000:.1f}k".replace(".0k", "k")
    return str(chars)


def _format_usage_cost(value: Any) -> str:
    if value is None:
        return "?"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "?"
    if 0 < amount < 0.0001:
        return "<$0.0001"
    return f"${amount:.4f}"


def _event_display_cost(event: WorkflowUsageEvent) -> Optional[float]:
    if getattr(event, "cost_class", "metered") == "subscription_notional":
        return getattr(event, "notional_usd", None)
    return getattr(event, "estimated_usd", None)

