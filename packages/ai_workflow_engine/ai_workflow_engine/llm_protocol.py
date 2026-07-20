"""Plain-callable LLM protocol — bring your own client, no LangChain costume.

Any ``async def __call__(request: LLMRequest) -> LLMResponse`` object can serve as the ``llm=`` of
a structured node (``kind="llm"`` capability). The engine applies the same parse/repair/pre_parse,
metering, budget, and capability-timeout mechanics as for LangChain-shaped clients.

Cost integrity (review requirement RC2): a callable that reports no cost never yields a phantom
``$0.00``. Metered calls use ``LLMResponse.estimated_usd`` first, then the engine's default
rate catalog when model and token counts are known. Subscription calls carry typed normalized
usage/provider totals into the central pricing policy. Otherwise cost stays visibly unknown.
"""

from __future__ import annotations

import asyncio
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
import threading
from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator

from ai_workflow_engine.models import WorkflowUsageEvent
from ai_workflow_engine.pricing import estimate_cost_usd
from ai_workflow_engine.usage_events import record_usage_event
from ai_workflow_engine.transport_models import ImageInput
from ai_workflow_engine.usage_contract import (
    NormalizedTokenUsage,
    ProviderInvocationId,
    UsageError,
)


class ToolSpec(BaseModel):
    """JSON-schema description of a tool an LLM may request."""

    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    """One LLM-requested tool call."""

    call_id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Result of a tool call, optionally carrying transport-only images."""

    call_id: str
    content: str = ""
    images: List[ImageInput] = Field(default_factory=list)
    is_error: bool = False

    def image_fingerprints(self) -> List[Dict[str, Any]]:
        return [image.fingerprint() for image in self.images]


class ChatMessage(BaseModel):
    """One multi-turn chat message for tool-calling-capable clients."""

    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    images: List[ImageInput] = Field(default_factory=list)
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)
    tool_results: List[ToolResult] = Field(default_factory=list)


class LLMRequest(BaseModel):
    """One chat-style request to a product-owned LLM client."""

    system: Optional[str] = None
    user: str = ""
    images: List[ImageInput] = Field(default_factory=list)
    messages: List[ChatMessage] = Field(default_factory=list)
    tools: List[ToolSpec] = Field(default_factory=list)
    tool_choice: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    invocation_id: Optional[ProviderInvocationId] = None

    @model_validator(mode="after")
    def _one_explicit_mode(self) -> "LLMRequest":
        """v0.11 clean contract (manifest row M5): two DELIBERATE modes, no silent precedence.
        Simple mode: system?/user (+images), messages empty. Multi-turn mode: messages only —
        a system prompt goes in a role="system" ChatMessage and images ride on their message."""

        if not self.user and not self.messages:
            raise ValueError("LLMRequest requires either user text or messages")
        if self.messages and (self.user or self.system or self.images):
            raise ValueError(
                "LLMRequest modes are exclusive: with messages=[...], top-level system/user/"
                "images must be empty (put the system prompt in a role='system' ChatMessage "
                "and images on their ChatMessage)"
            )
        return self


class LLMResponse(BaseModel):
    """The client's answer plus whatever usage truth it can report."""

    model_config = {"arbitrary_types_allowed": True}

    text: str = ""
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)
    stop_reason: Optional[str] = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_usd: Optional[float] = None
    cost_class: Literal["metered", "subscription_notional"] = "metered"
    notional_usd: Optional[float] = None
    normalized_usage: Optional[NormalizedTokenUsage] = None
    usage_error: Optional[UsageError] = None
    usage_diagnostic: Optional[str] = Field(default=None, max_length=500)
    provider_reported_notional_usd: Optional[float] = None
    invocation_id: Optional[ProviderInvocationId] = None
    elapsed_ms: Optional[int] = Field(default=None, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    raw: Any = None


@dataclass(frozen=True)
class LLMUsageIdentity:
    node: str
    attempt: int
    model: str
    provider: str
    cost_class: Literal["metered", "subscription_notional"]
    invocation_id: str


_usage_identity: contextvars.ContextVar[LLMUsageIdentity | None] = (
    contextvars.ContextVar("ai_workflow_llm_usage_identity", default=None)
)


@contextmanager
def llm_usage_identity_scope(identity: LLMUsageIdentity):
    token = _usage_identity.set(identity)
    try:
        yield
    finally:
        _usage_identity.reset(token)


def current_llm_usage_identity() -> LLMUsageIdentity | None:
    return _usage_identity.get()


class BlockingCallCancellation:
    """Invocation-local bridge from an async caller to one process-backed sync adapter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._cancel_requested = False

    def bind(
        self,
        loop: asyncio.AbstractEventLoop,
        task: asyncio.Task[Any],
    ) -> None:
        with self._lock:
            if self._task is not None:
                raise RuntimeError("blocking-call cancellation token is already bound")
            self._loop = loop
            self._task = task
            cancel_requested = self._cancel_requested
        if cancel_requested:
            loop.call_soon_threadsafe(task.cancel)

    def unbind(self, task: asyncio.Task[Any]) -> None:
        with self._lock:
            if self._task is task:
                self._loop = None
                self._task = None

    def cancel(self) -> bool:
        with self._lock:
            self._cancel_requested = True
            loop = self._loop
            task = self._task
        if loop is None or task is None:
            return False
        loop.call_soon_threadsafe(task.cancel)
        return True


_blocking_cancellation: contextvars.ContextVar[BlockingCallCancellation | None] = (
    contextvars.ContextVar("ai_workflow_blocking_call_cancellation", default=None)
)


@contextmanager
def blocking_call_cancellation_scope(cancellation: BlockingCallCancellation):
    token = _blocking_cancellation.set(cancellation)
    try:
        yield
    finally:
        _blocking_cancellation.reset(token)


def current_blocking_call_cancellation() -> BlockingCallCancellation | None:
    return _blocking_cancellation.get()


@runtime_checkable
class LLMCallable(Protocol):
    """Minimal contract for a non-LangChain LLM client."""

    async def __call__(self, request: LLMRequest) -> LLMResponse: ...


def is_plain_llm_callable(llm: Any) -> bool:
    """A plain callable client: callable, but not LangChain-shaped (no ``.invoke``)."""

    return llm is not None and not hasattr(llm, "invoke") and callable(llm)


def record_callable_usage(
    response: LLMResponse,
    *,
    node: str,
    attempt: int,
    metadata: Optional[Dict[str, Any]] = None,
    cost_class: Literal["metered", "subscription_notional"] = "metered",
    notional_usd: Optional[float] = None,
    provider: str = "custom",
    invocation_id: str | None = None,
) -> None:
    """Meter one plain-callable LLM call with honest cost attribution (RC2)."""

    estimated = response.estimated_usd if cost_class == "metered" else None
    if cost_class == "metered" and estimated is None:
        has_tokens = bool(response.total_tokens or response.input_tokens or response.output_tokens)
        if response.model and has_tokens:
            estimated = estimate_cost_usd(
                response.model,
                "chat",
                response.input_tokens,
                response.output_tokens,
            )
    provider_reported = response.provider_reported_notional_usd
    if provider_reported is None:
        provider_reported = notional_usd
    resolved_invocation_id = invocation_id or response.invocation_id
    if (
        invocation_id is not None
        and response.invocation_id is not None
        and invocation_id != response.invocation_id
    ):
        raise ValueError(
            "plain-callable response invocation_id does not match the provider attempt"
        )
    record_usage_event(
        WorkflowUsageEvent(
            provider=provider,
            operation="chat",
            cost_class=cost_class,
            node=node,
            model=response.model,
            attempt=attempt,
            invocation_id=resolved_invocation_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            total_tokens=response.total_tokens or (response.input_tokens + response.output_tokens),
            input_token_details=(
                {
                    "cache_read": response.normalized_usage.cache_read_input_tokens,
                    "cache_creation": response.normalized_usage.cache_creation_input_tokens,
                }
                if response.normalized_usage is not None
                else {}
            ),
            output_token_details=(
                {"reasoning": response.normalized_usage.reasoning_output_tokens}
                if response.normalized_usage is not None
                else {}
            ),
            estimated_usd=estimated,
            normalized_usage=response.normalized_usage,
            usage_error=response.usage_error,
            usage_diagnostic=response.usage_diagnostic,
            provider_reported_notional_usd=(
                provider_reported
                if cost_class == "subscription_notional"
                else None
            ),
            elapsed_ms=response.elapsed_ms,
            metadata=metadata or {},
        )
    )


def record_callable_failure_usage(
    exc: BaseException,
    *,
    node: str,
    attempt: int,
    model: str,
    provider: str,
    cost_class: Literal["metered", "subscription_notional"],
    metadata: Optional[Dict[str, Any]] = None,
    invocation_id: str | None = None,
    elapsed_ms: int | None = None,
) -> None:
    """Record structured facts retained by a failed or cancelled invocation once."""

    resolved_invocation_id = invocation_id or getattr(exc, "invocation_id", None)
    if resolved_invocation_id is not None:
        from ai_workflow_engine.budget import current_usage_context

        usage_context = current_usage_context()
        if usage_context is not None and any(
            event.invocation_id == resolved_invocation_id
            for event in usage_context.summary.events
        ):
            return
    normalized_usage = getattr(exc, "normalized_usage", None)
    consumed_usd = getattr(exc, "notional_usd", None)
    if type(consumed_usd) not in (int, float):
        consumed_usd = None
    usage_error = getattr(exc, "usage_error", None)
    if normalized_usage is None and usage_error is None:
        usage_error = "usage_event_missing"
    error = str(exc) or exc.__class__.__name__
    record_usage_event(
        WorkflowUsageEvent(
            provider=provider,
            operation="chat",
            cost_class=cost_class,
            node=node,
            model=model,
            attempt=attempt,
            invocation_id=resolved_invocation_id,
            success=False,
            error=error[:500],
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
            usage_diagnostic=getattr(exc, "usage_diagnostic", None),
            provider_reported_notional_usd=(
                consumed_usd if cost_class == "subscription_notional" else None
            ),
            estimated_usd=consumed_usd if cost_class == "metered" else None,
            elapsed_ms=(
                elapsed_ms
                if elapsed_ms is not None
                else getattr(exc, "elapsed_ms", None)
            ),
            metadata=metadata or {},
        )
    )
