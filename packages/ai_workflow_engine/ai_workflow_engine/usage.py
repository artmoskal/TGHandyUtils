"""Workflow-local provider usage metering and budget checks."""

from __future__ import annotations

import json
import logging
import time
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Protocol

from langchain_core.messages import BaseMessage

from ai_workflow_engine._runtime_state import current_observation_capture
from ai_workflow_engine.models import WorkflowRunContext, WorkflowUsageEvent, WorkflowUsageSummary
from ai_workflow_engine.observability_capture import (
    is_engine_worker_observation_active,
    langchain_messages_payload,
    langchain_response_payload,
)

logger = logging.getLogger(__name__)


class WorkflowBudgetExceeded(Exception):
    """Raised before/after a provider call would exceed a workflow budget."""

    def __init__(self, message: str, *, decision: Optional[str] = None):
        super().__init__(message)
        self.decision = decision


class UsageSink(Protocol):
    """Receives usage events from the runtime."""

    def record(self, event: WorkflowUsageEvent) -> None:
        """Store one usage event."""


class InMemoryUsageSink:
    """Simple usage sink suitable for tests and short in-process runs."""

    def __init__(self) -> None:
        self.events: list[WorkflowUsageEvent] = []

    def record(self, event: WorkflowUsageEvent) -> None:
        self.events.append(event)


class JsonlUsageSink:
    """Append usage events to a JSONL file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: WorkflowUsageEvent) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json())
            fh.write("\n")


class AsyncQueueUsageSink:
    """Non-blocking asyncio.Queue usage feed for live observers."""

    def __init__(
        self,
        queue: asyncio.Queue[WorkflowUsageEvent] | None = None,
        *,
        maxsize: int = 1000,
    ) -> None:
        self.queue = queue or asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def record(self, event: WorkflowUsageEvent) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                dropped = False
            else:
                self.queue.task_done()
                dropped = True
            if dropped:
                self.dropped += 1
        self.queue.put_nowait(event)


class TeeUsageSink:
    """Fan each usage event out to multiple sinks."""

    def __init__(self, *sinks: UsageSink) -> None:
        self.sinks = list(sinks)

    def record(self, event: WorkflowUsageEvent) -> None:
        for sink in self.sinks:
            sink.record(event)


@dataclass(frozen=True)
class WorkflowBudget:
    max_text_calls: Optional[int] = None
    max_image_calls: Optional[int] = None
    max_estimated_usd: Optional[float] = None
    max_worker_calls: Optional[int] = None
    max_input_tokens_per_call: Optional[int] = None
    max_output_tokens_per_call: Optional[int] = None
    max_images_per_call: Optional[int] = None
    max_estimated_usd_per_call: Optional[float] = None


@dataclass
class WorkflowUsageContext:
    run_context: WorkflowRunContext
    summary: WorkflowUsageSummary
    budget: WorkflowBudget
    usage_sink: Optional[UsageSink] = None
    worker_call_count: int = 0


_ACTIVE_USAGE: ContextVar[Optional[WorkflowUsageContext]] = ContextVar("workflow_usage", default=None)

_DEFAULT_PRICE_TABLE: dict[str, dict[str, float]] = {
    # Official OpenAI pricing page, checked 2026-06-04. Keep override support because prices move.
    "gpt-5.5": {
        "input_per_1m": 5.0,
        "cached_input_per_1m": 0.5,
        "output_per_1m": 30.0,
    },
    "gpt-5.4": {
        "input_per_1m": 2.5,
        "cached_input_per_1m": 0.25,
        "output_per_1m": 15.0,
    },
    "gpt-5.4-mini": {
        "input_per_1m": 0.75,
        "cached_input_per_1m": 0.075,
        "output_per_1m": 4.5,
    },
    "gpt-5.4-nano": {
        "input_per_1m": 0.20,
        "cached_input_per_1m": 0.02,
        "output_per_1m": 1.25,
    },
    "gpt-image-2": {
        "text_input_per_1m": 5.0,
        "text_cached_input_per_1m": 1.25,
        "image_input_per_1m": 8.0,
        "image_cached_input_per_1m": 2.0,
        "image_output_per_1m": 30.0,
    },
    # Google Gemini API pricing, checked 2026-06-06. Generated images are metered as output image
    # tokens by resolution; provider adapters normalize image output into output_token_details.
    # Image-model pricing docs do not list cached-input discounts, so cached input is priced the
    # same as normal input unless WORKFLOW_MODEL_PRICE_OVERRIDES_JSON says otherwise.
    "gemini-2.5-flash-image": {
        "text_input_per_1m": 0.30,
        "text_cached_input_per_1m": 0.30,
        "image_input_per_1m": 0.30,
        "image_cached_input_per_1m": 0.30,
        "image_output_per_1m": 30.0,
    },
    "gemini-3.1-flash-image": {
        "text_input_per_1m": 0.50,
        "text_cached_input_per_1m": 0.50,
        "image_input_per_1m": 0.50,
        "image_cached_input_per_1m": 0.50,
        "image_output_per_1m": 60.0,
    },
    "gemini-3-pro-image": {
        "text_input_per_1m": 2.0,
        "text_cached_input_per_1m": 2.0,
        "image_input_per_1m": 2.0,
        "image_cached_input_per_1m": 2.0,
        "image_output_per_1m": 120.0,
    }
}


@contextmanager
def workflow_usage_scope(context: WorkflowUsageContext):
    token = _ACTIVE_USAGE.set(context)
    try:
        yield context
    finally:
        _ACTIVE_USAGE.reset(token)


def current_usage_context() -> Optional[WorkflowUsageContext]:
    return _ACTIVE_USAGE.get()


def budget_from_config(config: Any, *, limits: Any = None) -> WorkflowBudget:
    if config is not None and not getattr(config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
        return WorkflowBudget()
    if limits is None and config is not None:
        profile = getattr(config, "profile", None)
        limits = getattr(profile, "limits", None)
    config_budget = WorkflowBudget(
        max_text_calls=_positive_int(getattr(config, "WORKFLOW_MAX_TEXT_CALLS_PER_RUN", None)),
        max_image_calls=_positive_int(getattr(config, "WORKFLOW_MAX_IMAGE_CALLS_PER_RUN", None)),
        max_estimated_usd=_positive_float(getattr(config, "WORKFLOW_MAX_ESTIMATED_USD_PER_RUN", None)),
        max_worker_calls=_positive_int(getattr(config, "WORKFLOW_MAX_WORKER_CALLS_PER_RUN", None)),
        max_input_tokens_per_call=_positive_int(getattr(config, "WORKFLOW_MAX_INPUT_TOKENS_PER_CALL", None)),
        max_output_tokens_per_call=_positive_int(getattr(config, "WORKFLOW_MAX_OUTPUT_TOKENS_PER_CALL", None)),
        max_images_per_call=_positive_int(getattr(config, "WORKFLOW_MAX_IMAGES_PER_CALL", None)),
        max_estimated_usd_per_call=_positive_float(
            getattr(config, "WORKFLOW_MAX_ESTIMATED_USD_PER_CALL", None)
        ),
    )
    if limits is None:
        return config_budget
    return WorkflowBudget(
        max_text_calls=config_budget.max_text_calls,
        max_image_calls=config_budget.max_image_calls,
        max_estimated_usd=_limit_float(getattr(limits, "max_estimated_usd", None), config_budget.max_estimated_usd),
        max_worker_calls=_limit_int(getattr(limits, "max_worker_calls", None), config_budget.max_worker_calls),
        max_input_tokens_per_call=_limit_int(
            getattr(limits, "max_input_tokens_per_call", None),
            config_budget.max_input_tokens_per_call,
        ),
        max_output_tokens_per_call=_limit_int(
            getattr(limits, "max_output_tokens_per_call", None),
            config_budget.max_output_tokens_per_call,
        ),
        max_images_per_call=_limit_int(getattr(limits, "max_images_per_call", None), config_budget.max_images_per_call),
        max_estimated_usd_per_call=_limit_float(
            getattr(limits, "max_estimated_usd_per_call", None),
            config_budget.max_estimated_usd_per_call,
        ),
    )


def check_budget_before_call(operation: str, node: str) -> None:
    context = current_usage_context()
    if not context:
        return
    if operation == "chat" and context.budget.max_text_calls is not None:
        if context.summary.text_call_count >= context.budget.max_text_calls:
            raise WorkflowBudgetExceeded(
                f"Workflow max_text_calls budget exceeded before node {node}: "
                f"{context.summary.text_call_count}/{context.budget.max_text_calls}"
            )
    if operation == "image" and context.budget.max_image_calls is not None:
        if context.summary.image_call_count >= context.budget.max_image_calls:
            raise WorkflowBudgetExceeded(
                f"Workflow max_image_calls budget exceeded before node {node}: "
                f"{context.summary.image_call_count}/{context.budget.max_image_calls}"
            )
    if operation in {"chat", "agent", "external"} and context.budget.max_worker_calls is not None:
        if context.worker_call_count >= context.budget.max_worker_calls:
            raise WorkflowBudgetExceeded(
                f"Workflow max_worker_calls budget exceeded before {operation} node {node}: "
                f"{context.worker_call_count}/{context.budget.max_worker_calls}"
            )
        context.worker_call_count += 1


def estimate_text_tokens(value: Any) -> int:
    """Cheap pre-call input-token heuristic for budget gates.

    This is intentionally conservative and provider-neutral. Authoritative token counts still come
    from provider usage events after the call.
    """

    if value is None:
        return 0
    if isinstance(value, BaseMessage):
        return estimate_text_tokens(value.content)
    if hasattr(value, "role") and hasattr(value, "tool_calls") and hasattr(value, "tool_results"):
        # ChatMessage (duck-typed to avoid an import cycle with llm_protocol): count content,
        # tool-call arguments, and tool-result text — never the pydantic repr (which would inflate
        # the estimate with field names and image fingerprints).
        parts: list[Any] = [value.content]
        parts.extend(call.arguments for call in value.tool_calls)
        parts.extend(result.content for result in value.tool_results)
        return estimate_text_tokens(parts)
    if isinstance(value, str):
        if not value:
            return 0
        return max(1, (len(value) + 3) // 4)
    if isinstance(value, dict):
        if value.get("type") == "image_url":
            return 0
        if "text" in value:
            return estimate_text_tokens(value["text"])
        if "content" in value:
            return estimate_text_tokens(value["content"])
        return estimate_text_tokens(json.dumps(value, sort_keys=True, default=str))
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return sum(estimate_text_tokens(item) for item in value)
    return estimate_text_tokens(str(value))


def check_input_tokens_per_call(input_tokens: int, node: str) -> None:
    context = current_usage_context()
    if not context or context.budget.max_input_tokens_per_call is None:
        return
    if input_tokens > context.budget.max_input_tokens_per_call:
        raise WorkflowBudgetExceeded(
            f"Workflow max_input_tokens_per_call budget exceeded before node {node}: "
            f"{input_tokens}/{context.budget.max_input_tokens_per_call}"
        )


def check_images_per_call(image_count: int, node: str) -> None:
    context = current_usage_context()
    if not context or context.budget.max_images_per_call is None:
        return
    if image_count > context.budget.max_images_per_call:
        raise WorkflowBudgetExceeded(
            f"Workflow max_images_per_call budget exceeded before node {node}: "
            f"{image_count}/{context.budget.max_images_per_call}"
        )


def record_usage_event(event: WorkflowUsageEvent) -> None:
    context = current_usage_context()
    if context:
        if event.run_id is None:
            event.run_id = context.run_context.workflow_id
        event.metadata.setdefault("run_id", context.run_context.workflow_id)
        event.metadata.setdefault("workflow_id", context.run_context.workflow_id)
        event.metadata.setdefault("workflow_type", context.run_context.workflow_type)
        event.metadata.setdefault("user_id", context.run_context.user_id)
        context.summary.add_event(event)
        if context.usage_sink is not None:
            context.usage_sink.record(event)
        logger.info("workflow_usage %s", json.dumps(event.model_dump(), sort_keys=True, default=str))
        _enforce_per_call_budget(event, context)
        _enforce_usd_budget(context)
        return
    logger.info("workflow_usage %s", json.dumps(event.model_dump(), sort_keys=True, default=str))


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


def estimate_cost_usd(
    model: str,
    operation: str,
    input_tokens: int,
    output_tokens: int,
    input_details: Optional[dict[str, int]] = None,
    output_details: Optional[dict[str, int]] = None,
    *,
    config: Any = None,
) -> Optional[float]:
    prices = _price_for_model(model, config)
    if not prices:
        return None

    input_details = input_details or {}
    output_details = output_details or {}
    if operation == "image":
        text_input = input_details.get("text_tokens", 0)
        image_input = input_details.get("image_tokens", max(input_tokens - text_input, 0))
        cached_text = input_details.get("text_cached_tokens", input_details.get("cached_text_tokens", 0))
        cached_image = input_details.get("image_cached_tokens", input_details.get("cached_image_tokens", 0))
        generic_cached = input_details.get("cache_read", input_details.get("cached_tokens", 0))
        if generic_cached and not (cached_text or cached_image):
            cached_text = min(text_input, generic_cached)
            cached_image = min(image_input, max(generic_cached - cached_text, 0))
        uncached_text = max(text_input - cached_text, 0)
        uncached_image = max(image_input - cached_image, 0)
        image_output = output_details.get("image_tokens", output_tokens)
        text_cost = uncached_text * prices.get("text_input_per_1m", 0.0) / 1_000_000
        text_cost += cached_text * prices.get("text_cached_input_per_1m", prices.get("text_input_per_1m", 0.0)) / 1_000_000
        image_in_cost = uncached_image * prices.get("image_input_per_1m", 0.0) / 1_000_000
        image_in_cost += cached_image * prices.get("image_cached_input_per_1m", prices.get("image_input_per_1m", 0.0)) / 1_000_000
        image_out_cost = image_output * prices.get("image_output_per_1m", 0.0) / 1_000_000
        return round(text_cost + image_in_cost + image_out_cost, 6)

    cached = input_details.get("cache_read", 0) or input_details.get("cached_tokens", 0)
    uncached_input = max(input_tokens - cached, 0)
    input_cost = uncached_input * prices.get("input_per_1m", 0.0) / 1_000_000
    cached_cost = cached * prices.get("cached_input_per_1m", prices.get("input_per_1m", 0.0)) / 1_000_000
    output_cost = output_tokens * prices.get("output_per_1m", 0.0) / 1_000_000
    return round(input_cost + cached_cost + output_cost, 6)


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
    # Absent is not unknown: the notional segment appears only when subscription events exist.
    # A pure-metered run must not render "notional ?" — that would conflate "no flat-rate usage"
    # with "flat-rate cost unknown" (the cost-honesty axis, inverted).
    has_subscription = any(
        getattr(event, "cost_class", "metered") == "subscription_notional" for event in summary.events
    )
    notional_segment = (
        f" / notional {_format_usage_cost(summary.notional_usd)}" if has_subscription else ""
    )
    rows.append(
        "total: "
        f"{summary.text_call_count} text, {summary.image_call_count} image, "
        f"{tool_total}"
        f"{_compact_token_count(summary.input_tokens)} in, "
        f"{_compact_token_count(summary.cached_input_tokens)} cached, "
        f"{_compact_token_count(summary.output_tokens)} out, "
        f"{char_total}"
        f"metered {_format_usage_cost(summary.metered_usd)}{notional_segment}"
    )
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
        f"{_format_usage_cost(_event_display_cost(event)):>9}"
    )


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


def _usage_event_from_chat_output(
    output: Any,
    *,
    node: str,
    model: str,
    attempt: int,
    elapsed_ms: int,
    metadata: Optional[dict[str, Any]],
    config: Any = None,
    cost_class: Literal["metered", "subscription_notional"] = "metered",
    notional_usd: Optional[float] = None,
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
            config=config,
        )
    return WorkflowUsageEvent(
        operation="chat",
        cost_class=cost_class,
        node=node,
        model=effective_model,
        attempt=attempt,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_token_details=input_details,
        output_token_details=output_details,
        estimated_usd=estimated_usd,
        notional_usd=notional_usd,
        request_id=response_metadata.get("id") or response_metadata.get("request_id"),
        elapsed_ms=elapsed_ms,
        metadata=metadata or {},
    )


def _enforce_usd_budget(context: WorkflowUsageContext) -> None:
    max_usd = context.budget.max_estimated_usd
    total = context.summary.metered_usd
    if max_usd is not None and total is not None and total > max_usd:
        raise WorkflowBudgetExceeded(f"Workflow estimated cost exceeded: ${total:.6f} > ${max_usd:.6f}")


def _enforce_per_call_budget(event: WorkflowUsageEvent, context: WorkflowUsageContext) -> None:
    max_output = context.budget.max_output_tokens_per_call
    if max_output is not None and event.output_tokens > max_output:
        # Spec (engine-completion §5): the output is already produced and paid for — record the
        # truncation loudly (usage-event metadata + structured log), do NOT retroactively fail
        # the call. Runaway protection across turns comes from max_worker_calls / USD caps.
        event.metadata["truncated_by_budget"] = True
        event.metadata["output_tokens_over_cap"] = f"{event.output_tokens}/{max_output}"
        logger.warning(
            "workflow_output_tokens_over_cap node=%s tokens=%s cap=%s",
            event.node,
            event.output_tokens,
            max_output,
        )
    max_usd = context.budget.max_estimated_usd_per_call
    if (
        max_usd is not None
        and event.cost_class == "metered"
        and event.estimated_usd is not None
        and event.estimated_usd > max_usd
    ):
        raise WorkflowBudgetExceeded(
            f"Workflow max_estimated_usd_per_call budget exceeded after node {event.node}: "
            f"${event.estimated_usd:.6f} > ${max_usd:.6f}"
        )


def _price_for_model(model: str, config: Any = None) -> Optional[dict[str, float]]:
    overrides = _price_overrides(config)
    if model in overrides:
        return overrides[model]
    if model in _DEFAULT_PRICE_TABLE:
        return _DEFAULT_PRICE_TABLE[model]
    price_table = {**_DEFAULT_PRICE_TABLE, **overrides}
    for key in sorted(price_table, key=len, reverse=True):
        if model.startswith(key):
            return price_table[key]
    return None


def _price_overrides(config: Any = None) -> dict[str, dict[str, float]]:
    raw = getattr(config, "WORKFLOW_MODEL_PRICE_OVERRIDES_JSON", "") if config else ""
    if not raw:
        return {}
    if not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid WORKFLOW_MODEL_PRICE_OVERRIDES_JSON")
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for model, prices in data.items():
        if isinstance(model, str) and isinstance(prices, dict):
            cleaned[model] = {str(k): float(v) for k, v in prices.items()}
    return cleaned


def _model_or_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:
            return {}
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _details(value: Any) -> dict[str, int]:
    data = _model_or_dict(value)
    return {str(key): _safe_int(val) for key, val in data.items() if isinstance(val, (int, float))}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _limit_int(value: Any, fallback: Optional[int]) -> Optional[int]:
    if value is None:
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _limit_float(value: Any, fallback: Optional[float]) -> Optional[float]:
    if value is None:
        return fallback
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback
