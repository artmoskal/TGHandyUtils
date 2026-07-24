"""Conformance kit for OpenAI chat-completions compatible clients.

The kit is plain-assert and dependency-free apart from the engine protocol itself.
Products run it in their own suite with a factory for the client they actually compose.
The in-process HTTP server proves request/response behavior; products should add a live
endpoint test for provider-specific deviations.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Any, Callable

from ai_workflow_engine.llm_protocol import (
    ChatMessage,
    LLMRequest,
    ToolCallRequest,
    ToolResult,
    ToolSpec,
)
from ai_workflow_engine.transport_models import ImageInput


@dataclass(frozen=True)
class RecordedChatRequest:
    path: str
    headers: dict[str, str]
    payload: Any


@dataclass(frozen=True)
class ProviderTestResponse:
    status: int
    payload: Any
    headers: dict[str, str] = field(default_factory=dict)
    delay_s: float = 0.0
    raw_body: bytes | None = None
    chunk_size: int | None = None
    chunk_delay_s: float = 0.0


ResponseFactory = Callable[[RecordedChatRequest], ProviderTestResponse]
ClientFactory = Callable[[str, str], Any]


class OpenAICompatibleTestServer:
    """Threaded loopback server with recorded requests and programmable responses."""

    def __init__(self, response_factory: ResponseFactory) -> None:
        self._response_factory = response_factory
        self._requests: list[RecordedChatRequest] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                try:
                    length = int(self.headers.get("content-length", "0"))
                except ValueError:
                    self.send_error(400)
                    return
                if length < 0 or length > 16 << 20:
                    self.send_error(413)
                    return
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = None
                recorded = RecordedChatRequest(
                    path=self.path,
                    headers={name.lower(): value for name, value in self.headers.items()},
                    payload=payload,
                )
                with owner._lock:
                    owner._requests.append(recorded)
                response = owner._response_factory(recorded)
                if response.delay_s:
                    time.sleep(response.delay_s)
                body = (
                    response.raw_body
                    if response.raw_body is not None
                    else json.dumps(response.payload, separators=(",", ":")).encode(
                        "utf-8"
                    )
                )
                try:
                    self.send_response(response.status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    for name, value in response.headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    _write_response_body(self.wfile, body, response)
                except (BrokenPipeError, ConnectionResetError):
                    return

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="openai-compatible-test-server",
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1"

    @property
    def requests(self) -> list[RecordedChatRequest]:
        with self._lock:
            return list(self._requests)

    def __enter__(self) -> "OpenAICompatibleTestServer":
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _write_response_body(
    stream: Any,
    body: bytes,
    response: ProviderTestResponse,
) -> None:
    if response.chunk_size is None:
        stream.write(body)
        return
    if response.chunk_size <= 0:
        raise ValueError("ProviderTestResponse.chunk_size must be positive")
    for offset in range(0, len(body), response.chunk_size):
        stream.write(body[offset : offset + response.chunk_size])
        stream.flush()
        if response.chunk_delay_s:
            time.sleep(response.chunk_delay_s)


async def run_openai_compatible_provider_conformance(
    make_client: ClientFactory,
) -> None:
    """Prove the common text/image/tool/usage/error/cancellation contract."""

    secret = "conformance-secret"
    with OpenAICompatibleTestServer(_conformance_response) as server:
        client = make_client(server.base_url, secret)
        try:
            simple = await client(
                LLMRequest(
                    system="Answer plainly.",
                    user="conformance:simple",
                    invocation_id="inv-simple",
                )
            )
            assert simple.text == "simple-ok", "simple text response must round-trip"
            assert simple.invocation_id == "inv-simple", (
                "response must retain the request's invocation identity"
            )
            assert simple.normalized_usage is not None, (
                "present provider usage must be normalized, never discarded"
            )
            assert simple.normalized_usage.uncached_input_tokens == 7
            assert simple.normalized_usage.cache_read_input_tokens == 3
            assert simple.normalized_usage.non_reasoning_output_tokens == 3
            assert simple.normalized_usage.reasoning_output_tokens == 2

            image = ImageInput(
                source="base64",
                data="iVBORw0KGgo=",
                media_type="image/png",
                role="reference",
            )
            tool_response = await client(
                LLMRequest(
                    user="conformance:inspect-image",
                    images=[image],
                    tools=[
                        ToolSpec(
                            name="inspect",
                            description="Inspect one image",
                            input_schema={
                                "type": "object",
                                "properties": {"detail": {"type": "string"}},
                            },
                        )
                    ],
                    tool_choice="auto",
                    invocation_id="inv-tool",
                )
            )
            assert tool_response.tool_calls == [
                ToolCallRequest(
                    call_id="call-inspect",
                    name="inspect",
                    arguments={"detail": "full"},
                )
            ], "image/tool request must preserve the provider's structured tool call"

            final = await client(
                LLMRequest(
                    messages=[
                        ChatMessage(role="system", content="Finish after the tool."),
                        ChatMessage(role="user", content="conformance:tool-result"),
                        ChatMessage(
                            role="assistant",
                            tool_calls=tool_response.tool_calls,
                        ),
                        ChatMessage(
                            role="tool",
                            tool_results=[
                                ToolResult(
                                    call_id="call-inspect",
                                    content='{"visible":true}',
                                )
                            ],
                        ),
                    ],
                    invocation_id="inv-final",
                )
            )
            assert final.text == "tool-result-ok"
            assert final.normalized_usage is None
            assert final.usage_error == "usage_event_missing", (
                "absent provider usage must remain typed unknown, never fabricated as zero"
            )

            before_401 = len(server.requests)
            try:
                await client(
                    LLMRequest(
                        user="conformance:unauthorized",
                        invocation_id="inv-auth",
                    )
                )
            except Exception as exc:
                assert getattr(exc, "failure_kind", None) == "authentication", (
                    "401 must remain a typed authentication failure"
                )
                assert secret not in repr(exc) and secret not in str(exc), (
                    "provider failures must never expose credentials"
                )
            else:
                raise AssertionError("401 must fail loudly, never fall back")
            assert len(server.requests) == before_401 + 1, (
                "authentication failures must not be retried by the adapter"
            )

            concurrent = await asyncio.gather(
                client(
                    LLMRequest(
                        user="conformance:concurrent-a",
                        invocation_id="inv-a",
                    )
                ),
                client(
                    LLMRequest(
                        user="conformance:concurrent-b",
                        invocation_id="inv-b",
                    )
                ),
            )
            assert [(row.text, row.invocation_id) for row in concurrent] == [
                ("concurrent-a", "inv-a"),
                ("concurrent-b", "inv-b"),
            ], "concurrent invocations must not share response or identity state"

            before_cancel = len(server.requests)
            pending = asyncio.create_task(
                client(
                    LLMRequest(
                        user="conformance:cancel",
                        invocation_id="inv-cancel",
                    )
                )
            )
            await _wait_for_request_count(server, before_cancel + 1)
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("caller cancellation must propagate unchanged")
            await asyncio.sleep(0.02)
            assert len(server.requests) == before_cancel + 1, (
                "cancelled requests must not be retried"
            )

            records = server.requests
            assert all(row.path == "/v1/chat/completions" for row in records)
            assert all(
                row.headers.get("authorization") == f"Bearer {secret}"
                for row in records
            ), "the configured API key must reach every request as Bearer auth"
            request_ids = [
                row.headers.get("x-client-request-id")
                for row in records
            ]
            assert len(request_ids) == len(set(request_ids)), (
                "provider requests must carry distinct invocation identities"
            )
            by_marker = {
                _request_marker(row.payload.get("messages", [])): row
                for row in records
                if isinstance(row.payload, dict)
            }
            assert by_marker["conformance:simple"].payload["messages"] == [
                {"role": "system", "content": "Answer plainly."},
                {"role": "user", "content": "conformance:simple"},
            ], "simple system/user turns must map without hidden prompt rewriting"
            image_payload = by_marker["conformance:inspect-image"].payload
            image_parts = image_payload["messages"][0]["content"]
            assert image_parts[0] == {
                "type": "text",
                "text": "conformance:inspect-image",
            }
            assert image_parts[1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            ), "base64 images must use the common OpenAI image_url data-URL shape"
            assert image_payload["tools"][0]["function"]["name"] == "inspect"
            assert image_payload["tool_choice"] == "auto"
            result_payload = by_marker["conformance:tool-result"].payload
            assert result_payload["messages"][-2]["tool_calls"][0]["id"] == (
                "call-inspect"
            )
            assert result_payload["messages"][-1] == {
                "role": "tool",
                "tool_call_id": "call-inspect",
                "content": '{"visible":true}',
            }, "tool results must preserve the provider call identity"
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


async def _wait_for_request_count(
    server: OpenAICompatibleTestServer,
    expected: int,
) -> None:
    deadline = time.monotonic() + 2.0
    while len(server.requests) < expected:
        if time.monotonic() >= deadline:
            raise AssertionError(f"server did not receive {expected} requests")
        await asyncio.sleep(0.005)


def _conformance_response(request: RecordedChatRequest) -> ProviderTestResponse:
    messages = request.payload.get("messages", []) if isinstance(request.payload, dict) else []
    marker = _request_marker(messages)
    if marker == "conformance:unauthorized":
        return ProviderTestResponse(
            401,
            {"error": {"code": "invalid_api_key", "message": "denied"}},
        )
    if marker == "conformance:cancel":
        return ProviderTestResponse(
            200,
            _completion("too-late"),
            delay_s=0.5,
        )
    if marker == "conformance:inspect-image":
        return ProviderTestResponse(
            200,
            {
                "model": "conformance-model",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-inspect",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect",
                                        "arguments": '{"detail":"full"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": _usage(),
            },
        )
    if marker == "conformance:tool-result":
        return ProviderTestResponse(200, _completion("tool-result-ok", usage=None))
    if marker == "conformance:concurrent-a":
        return ProviderTestResponse(200, _completion("concurrent-a"))
    if marker == "conformance:concurrent-b":
        return ProviderTestResponse(200, _completion("concurrent-b"))
    if marker == "conformance:simple":
        return ProviderTestResponse(
            200,
            _completion("simple-ok", usage=_usage()),
            headers={"x-request-id": "provider-simple"},
        )
    return ProviderTestResponse(400, {"error": {"message": f"unknown marker {marker}"}})


def _request_marker(messages: list[Any]) -> str:
    for row in messages:
        if not isinstance(row, dict) or row.get("role") != "user":
            continue
        marker = _text_content(row.get("content"))
        if marker:
            return marker
    return ""


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            return str(part.get("text") or "")
    return ""


def _completion(text: str, *, usage: Any = ...) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "conformance-model",
        "choices": [
            {
                "message": {"content": text},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is ...:
        payload["usage"] = _usage()
    elif usage is not None:
        payload["usage"] = usage
    return payload


def _usage() -> dict[str, Any]:
    return {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "prompt_tokens_details": {"cached_tokens": 3},
        "completion_tokens_details": {"reasoning_tokens": 2},
    }


__all__ = [
    "OpenAICompatibleTestServer",
    "ProviderTestResponse",
    "RecordedChatRequest",
    "run_openai_compatible_provider_conformance",
]
