"""Plain-callable LLM protocol — bring your own client, no LangChain costume.

Any ``async def __call__(request: LLMRequest) -> LLMResponse`` object can serve as the ``llm=`` of
a structured node (``kind="llm"`` capability). The engine applies the same parse/repair/pre_parse,
metering, budget, and capability-timeout mechanics as for LangChain-shaped clients.

Cost integrity (review requirement RC2): a callable that reports no cost never yields a phantom
``$0.00``. Resolution order per call:
1. ``LLMResponse.estimated_usd`` when provided (``cost_source="callable"``);
2. the engine price table by ``LLMResponse.model`` + token counts (``cost_source="price_table"``);
3. otherwise the usage event records ``estimated_usd=None`` with ``cost_known=False`` — visibly
   unknown, never silently zero.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator

from ai_workflow_engine.models import WorkflowUsageEvent
from ai_workflow_engine.usage import estimate_cost_usd, record_usage_event
from ai_workflow_engine.vision import ImageInput


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

    @model_validator(mode="after")
    def _require_user_or_messages(self) -> "LLMRequest":
        if not self.user and not self.messages:
            raise ValueError("LLMRequest requires either user text or messages")
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
    raw: Any = None


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
    config: Any = None,
    cost_class: Literal["metered", "subscription_notional"] = "metered",
    notional_usd: Optional[float] = None,
) -> None:
    """Meter one plain-callable LLM call with honest cost attribution (RC2)."""

    estimated = response.estimated_usd if cost_class == "metered" else None
    cost_source = "subscription_notional" if cost_class == "subscription_notional" else "callable"
    cost_known = estimated is not None or notional_usd is not None
    if cost_class == "metered" and estimated is None:
        has_tokens = bool(response.total_tokens or response.input_tokens or response.output_tokens)
        if response.model and has_tokens:
            estimated = estimate_cost_usd(
                response.model,
                "chat",
                response.input_tokens,
                response.output_tokens,
                config=config,
            )
            cost_source = "price_table"
        if estimated is None:
            cost_source = "unknown"
        cost_known = estimated is not None
    elif cost_class == "subscription_notional" and notional_usd is None:
        cost_source = "unknown"
    record_usage_event(
        WorkflowUsageEvent(
            provider="custom",
            operation="chat",
            cost_class=cost_class,
            node=node,
            model=response.model,
            attempt=attempt,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            total_tokens=response.total_tokens or (response.input_tokens + response.output_tokens),
            estimated_usd=estimated,
            notional_usd=notional_usd,
            metadata={
                **(metadata or {}),
                "cost_known": cost_known,
                "cost_source": cost_source,
            },
        )
    )
