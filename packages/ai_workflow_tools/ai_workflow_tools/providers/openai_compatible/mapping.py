from __future__ import annotations

import json
from typing import Any

from ai_workflow_engine.llm_protocol import (
    ChatMessage,
    LLMRequest,
    LLMResponse,
    ToolCallRequest,
    ToolResult,
)
from ai_workflow_engine.usage_contract import NormalizedTokenUsage, UsageError

from ai_workflow_tools.providers.openai_compatible.models import (
    OpenAICompatibleProviderConfig,
    OpenAICompatibleProviderError,
    sanitize_provider_text,
    secret_values,
)


def request_payload(
    request: LLMRequest,
    config: OpenAICompatibleProviderConfig,
) -> dict[str, Any]:
    messages = (
        _simple_messages(request)
        if not request.messages
        else _conversation_messages(request.messages)
    )
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
    }
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in request.tools
        ]
    if request.tool_choice is not None:
        payload["tool_choice"] = _tool_choice(request.tool_choice, request)
    if config.temperature is not None:
        payload["temperature"] = config.temperature
    if config.max_completion_tokens is not None:
        payload["max_completion_tokens"] = config.max_completion_tokens
    return payload


def response_from_payload(
    payload: Any,
    config: OpenAICompatibleProviderConfig,
    *,
    invocation_id: str,
    provider_request_id: str | None,
    elapsed_ms: int,
) -> LLMResponse:
    if not isinstance(payload, dict):
        raise _invalid_response(
            "provider response must be a JSON object",
            config,
            invocation_id,
            provider_request_id,
            elapsed_ms,
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _invalid_response(
            "provider response requires a nonempty choices array",
            config,
            invocation_id,
            provider_request_id,
            elapsed_ms,
        )
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise _invalid_response(
            "provider choice requires a message object",
            config,
            invocation_id,
            provider_request_id,
            elapsed_ms,
        )
    usage, usage_error, usage_diagnostic = _normalized_usage(payload.get("usage"))
    model = _bounded_label(payload.get("model"), config.model, limit=256)
    stop_reason = _bounded_label(choice.get("finish_reason"), "", limit=128) or None
    refusal = _bounded_label(message.get("refusal"), "", limit=128) or None
    if refusal is not None or stop_reason == "content_filter":
        refusal_fact = (
            sanitize_provider_text(
                refusal,
                secrets=secret_values(config),
                limit=128,
            )
            if refusal is not None
            else "content_filter"
        )
        raise OpenAICompatibleProviderError(
            f"provider refused the request ({refusal_fact})",
            failure_kind="refusal",
            model=model,
            invocation_id=invocation_id,
            provider_request_id=provider_request_id,
            elapsed_ms=elapsed_ms,
            normalized_usage=usage,
            usage_error=usage_error,
            usage_diagnostic=usage_diagnostic,
        )
    text = _response_text(
        message.get("content"),
        config,
        invocation_id,
        provider_request_id,
        elapsed_ms,
    )
    tool_calls = _response_tool_calls(
        message.get("tool_calls"),
        config,
        invocation_id,
        provider_request_id,
        elapsed_ms,
    )
    if not text.strip() and not tool_calls:
        raise _invalid_response(
            "provider response carried neither text nor tool calls",
            config,
            invocation_id,
            provider_request_id,
            elapsed_ms,
        )
    input_tokens = usage.raw_input_tokens if usage is not None else 0
    output_tokens = usage.raw_output_tokens if usage is not None else 0
    total_tokens = (
        usage.raw_total_tokens
        if usage is not None and usage.raw_total_tokens is not None
        else input_tokens + output_tokens
    )
    return LLMResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_usd=None,
        cost_class="metered",
        normalized_usage=usage,
        usage_error=usage_error,
        usage_diagnostic=usage_diagnostic,
        invocation_id=invocation_id,
        elapsed_ms=elapsed_ms,
        metadata={
            "provider_request_id": provider_request_id,
            "provider": config.provider,
        },
    )


def _simple_messages(request: LLMRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    messages.append(
        {
            "role": "user",
            "content": _content_with_images(request.user, request.images),
        }
    )
    return messages


def _conversation_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        rendered.extend(_conversation_message(message, index=index))
    return rendered


def _conversation_message(
    message: ChatMessage,
    *,
    index: int,
) -> list[dict[str, Any]]:
    if message.role in {"system", "user"}:
        return [_system_or_user_message(message, index=index)]
    if message.role == "assistant":
        return [_assistant_message(message, index=index)]
    if message.role == "tool":
        return _tool_messages(message, index=index)
    raise ValueError(f"messages[{index}] has unsupported role {message.role!r}")


def _system_or_user_message(
    message: ChatMessage,
    *,
    index: int,
) -> dict[str, Any]:
    if message.tool_calls or message.tool_results:
        raise ValueError(
            f"messages[{index}] role={message.role!r} cannot carry tool calls/results"
        )
    if message.images and message.role != "user":
        raise ValueError(
            f"messages[{index}] system images are not representable by "
            "OpenAI chat-completions"
        )
    return {
        "role": message.role,
        "content": _content_with_images(message.content or "", message.images),
    }


def _assistant_message(message: ChatMessage, *, index: int) -> dict[str, Any]:
    if message.images or message.tool_results:
        raise ValueError(
            f"messages[{index}] assistant images/tool results are not representable"
        )
    row: dict[str, Any] = {
        "role": "assistant",
        "content": message.content,
    }
    if message.tool_calls:
        row["tool_calls"] = [_assistant_tool_call(call) for call in message.tool_calls]
    return row


def _assistant_tool_call(call: ToolCallRequest) -> dict[str, Any]:
    return {
        "id": call.call_id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(
                call.arguments,
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    }


def _tool_messages(message: ChatMessage, *, index: int) -> list[dict[str, Any]]:
    if message.images or message.tool_calls:
        raise ValueError(f"messages[{index}] tool message cannot carry images/tool calls")
    if not message.tool_results:
        raise ValueError(f"messages[{index}] tool message requires tool_results")
    return [
        _tool_result_message(
            result,
            prefix=(message.content or "") if result_index == 0 else "",
        )
        for result_index, result in enumerate(message.tool_results)
    ]


def _tool_result_message(result: ToolResult, *, prefix: str) -> dict[str, Any]:
    if result.images:
        raise ValueError(
            "tool-result images are not representable by the common OpenAI "
            "chat-completions contract"
        )
    parts = [part for part in (prefix.strip(), result.content.strip()) if part]
    content = "\n".join(parts)
    if result.is_error:
        content = f"ERROR: {content}" if content else "ERROR"
    return {
        "role": "tool",
        "tool_call_id": result.call_id,
        "content": content,
    }


def _content_with_images(text: str, images: list[Any]) -> str | list[dict[str, Any]]:
    if not images:
        return text
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.extend(image.as_content_part() for image in images)
    return parts


def _tool_choice(value: str, request: LLMRequest) -> Any:
    if value in {"auto", "none", "required"}:
        return value
    names = {tool.name for tool in request.tools}
    if value not in names:
        raise ValueError(
            f"tool_choice {value!r} is neither auto/none/required nor a declared tool"
        )
    return {"type": "function", "function": {"name": value}}


def _response_text(
    value: Any,
    config: OpenAICompatibleProviderConfig,
    invocation_id: str,
    provider_request_id: str | None,
    elapsed_ms: int,
) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text_parts = [
            str(part.get("text") or "")
            for part in value
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        ]
        return "".join(text_parts)
    raise _invalid_response(
        "provider message content must be text, text parts, or null",
        config,
        invocation_id=invocation_id,
        provider_request_id=provider_request_id,
        elapsed_ms=elapsed_ms,
    )


def _response_tool_calls(
    value: Any,
    config: OpenAICompatibleProviderConfig,
    invocation_id: str,
    provider_request_id: str | None,
    elapsed_ms: int,
) -> list[ToolCallRequest]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid_response(
            "provider tool_calls must be an array",
            config,
            invocation_id,
            provider_request_id,
            elapsed_ms,
        )
    calls: list[ToolCallRequest] = []
    for index, row in enumerate(value):
        calls.append(
            _response_tool_call(
                row,
                index=index,
                config=config,
                invocation_id=invocation_id,
                provider_request_id=provider_request_id,
                elapsed_ms=elapsed_ms,
            )
        )
    return calls


def _response_tool_call(
    row: Any,
    *,
    index: int,
    config: OpenAICompatibleProviderConfig,
    invocation_id: str,
    provider_request_id: str | None,
    elapsed_ms: int,
) -> ToolCallRequest:
    function = row.get("function") if isinstance(row, dict) else None
    if not isinstance(row, dict) or not isinstance(function, dict):
        raise _invalid_response(
            f"provider tool_calls[{index}] must be a function call object",
            config,
            invocation_id,
            provider_request_id,
            elapsed_ms,
        )
    parsed = _tool_arguments(
        function.get("arguments"),
        index=index,
        config=config,
        invocation_id=invocation_id,
        provider_request_id=provider_request_id,
        elapsed_ms=elapsed_ms,
    )
    call_id = _bounded_label(row.get("id"), "", limit=256)
    name = _bounded_label(function.get("name"), "", limit=256)
    if not call_id or not name:
        raise _invalid_response(
            f"provider tool_calls[{index}] requires nonblank id and function name",
            config,
            invocation_id,
            provider_request_id,
            elapsed_ms,
        )
    return ToolCallRequest(call_id=call_id, name=name, arguments=parsed)


def _tool_arguments(
    value: Any,
    *,
    index: int,
    config: OpenAICompatibleProviderConfig,
    invocation_id: str,
    provider_request_id: str | None,
    elapsed_ms: int,
) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise _invalid_response(
            f"provider tool_calls[{index}] arguments are not valid JSON",
            config,
            invocation_id,
            provider_request_id,
            elapsed_ms,
        ) from exc
    if not isinstance(parsed, dict):
        raise _invalid_response(
            f"provider tool_calls[{index}] arguments must decode to an object",
            config,
            invocation_id,
            provider_request_id,
            elapsed_ms,
        )
    return parsed


def _normalized_usage(
    value: Any,
) -> tuple[NormalizedTokenUsage | None, UsageError | None, str | None]:
    if value is None:
        return None, "usage_event_missing", None
    if not isinstance(value, dict):
        return None, "usage_event_malformed", "usage must be an object"
    try:
        raw_input = _strict_counter(value, "prompt_tokens")
        raw_output = _strict_counter(value, "completion_tokens")
        raw_total = _strict_counter(value, "total_tokens", required=False)
        prompt_details = value.get("prompt_tokens_details") or {}
        completion_details = value.get("completion_tokens_details") or {}
        if not isinstance(prompt_details, dict) or not isinstance(completion_details, dict):
            raise ValueError("token detail fields must be objects")
        cached = _strict_counter(prompt_details, "cached_tokens", required=False) or 0
        cache_creation = (
            _strict_counter(prompt_details, "cache_creation_tokens", required=False) or 0
        )
        reasoning = (
            _strict_counter(completion_details, "reasoning_tokens", required=False) or 0
        )
        if cached > raw_input:
            raise ValueError("cached input tokens exceed prompt tokens")
        if reasoning > raw_output:
            raise ValueError("reasoning output tokens exceed completion tokens")
        normalized = NormalizedTokenUsage(
            counter_schema="generic",
            uncached_input_tokens=raw_input - cached,
            cache_read_input_tokens=cached,
            cache_creation_input_tokens=cache_creation,
            non_reasoning_output_tokens=raw_output - reasoning,
            reasoning_output_tokens=reasoning,
            raw_input_tokens=raw_input,
            raw_output_tokens=raw_output,
            raw_total_tokens=raw_total,
        )
    except (TypeError, ValueError) as exc:
        return None, "invalid_token_counters", str(exc)[:500]
    return normalized, None, None


def _strict_counter(
    data: dict[str, Any],
    name: str,
    *,
    required: bool = True,
) -> int | None:
    value = data.get(name)
    if value is None and not required:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _bounded_label(value: Any, fallback: str, *, limit: int) -> str:
    text = str(value if value is not None else "").strip()
    if not text or not text.isprintable():
        text = str(fallback).strip()
    return text[:limit]


def _invalid_response(
    message: str,
    config: OpenAICompatibleProviderConfig,
    invocation_id: str,
    provider_request_id: str | None,
    elapsed_ms: int,
) -> OpenAICompatibleProviderError:
    return OpenAICompatibleProviderError(
        message,
        failure_kind="invalid_response",
        model=config.model,
        invocation_id=invocation_id,
        provider_request_id=provider_request_id,
        elapsed_ms=elapsed_ms,
        usage_error="usage_event_missing",
    )


__all__ = ["request_payload", "response_from_payload"]
