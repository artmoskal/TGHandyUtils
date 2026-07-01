"""Provider-neutral token estimation heuristics (pre-call budget gates)."""

from __future__ import annotations

import json
from typing import Any, Iterable

from langchain_core.messages import BaseMessage


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

