"""LLM client over the ChatGPT-browser HTTP service (subscription session, no API key).

The service (`POST /ask`) drives a logged-in ChatGPT browser on the always-on box and returns
the reply text. This client adapts it to the engine's ``LLMCallable`` protocol, so any
structured node can swap onto it exactly like ``ConsoleLLMClient`` swaps onto ``claude -p``:
text in, text out, engine-side parsing/repair unchanged.

Contract notes (mirror of the image provider in ``media/image_generation.py``):
- Subscription economics: responses carry ``cost_class="subscription_notional"`` with zero
  token counts and no USD — the service reports no usage, and pretending otherwise would
  break cost honesty.
- The service caches identical questions; ``force_fresh`` (default on) appends a variation
  token so retries produce fresh runs instead of replaying the cache.
- One browser, sequential queue, slow calls: keep timeouts generous; failures are loud
  (HTTP 502 means the browser/extension side is down).
- No image support on ``/ask`` — requests with images are rejected loudly.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable

from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest, LLMResponse


class ChatGptBrowserError(RuntimeError):
    """Raised when the ChatGPT-browser service cannot produce a usable reply."""


class ChatGptBrowserLLMClient:
    """LLMCallable over the ChatGPT-browser control server's ``/ask`` endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 200.0,
        force_fresh: bool = True,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        base_url = str(base_url or "").strip()
        if not base_url:
            raise ChatGptBrowserError(
                "ChatGptBrowserLLMClient requires an explicit base_url "
                "(CHATGPT_BROWSER_API_URL) — no default endpoint is assumed"
            )
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.force_fresh = force_fresh
        self._http_post = http_post

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)
        question = _flatten_request(request)
        if self.force_fresh:
            # The service caches identical questions; without this a structured-node retry
            # would replay the same (already rejected) answer forever.
            question = f"{question}\n\n(request {uuid.uuid4().hex[:8]})"
        data = await self._post_json(
            f"{self.base_url}/ask",
            {"question": question, "timeout": int(self.timeout_s)},
        )
        reply = str(data.get("reply") or "")
        if data.get("status") != "completed" or not reply.strip():
            raise ChatGptBrowserError(
                f"chatgpt browser /ask returned no reply (status={data.get('status')!r})"
            )
        return LLMResponse(
            text=reply,
            model="chatgpt-web",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_usd=None,
            cost_class="subscription_notional",
            notional_usd=None,
            raw=data,
        )

    async def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        http_post = self._http_post
        if http_post is None:
            import requests

            http_post = requests.post
        response = await asyncio.to_thread(
            http_post,
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout_s + 30,
        )
        try:
            data = response.json()
        except Exception as exc:
            raise ChatGptBrowserError("chatgpt browser response was not valid JSON") from exc
        status_code = int(getattr(response, "status_code", 200) or 200)
        if status_code >= 400:
            detail = data.get("detail") or data.get("error") if isinstance(data, dict) else None
            raise ChatGptBrowserError(
                f"chatgpt browser service HTTP {status_code}: "
                f"{detail or getattr(response, 'text', '')} "
                "(502 usually means the logged-in ChatGPT browser/extension is down)"
            )
        if not isinstance(data, dict):
            raise ChatGptBrowserError("chatgpt browser response JSON was not an object")
        return data

    @staticmethod
    def _validate_request(request: LLMRequest) -> None:
        if request.images or any(message.images for message in request.messages):
            raise ChatGptBrowserError(
                "ChatGptBrowserLLMClient cannot receive images (/ask is text-only); use an "
                "API vision client for image-grounded calls"
            )


def _flatten_request(request: LLMRequest) -> str:
    parts: list[str] = []
    if request.system:
        parts.append(f"system: {request.system}")
    if request.user:
        parts.append(f"user: {request.user}")
    for message in request.messages:
        parts.extend(_flatten_message(message))
    return "\n\n".join(parts)


def _flatten_message(message: ChatMessage) -> list[str]:
    parts: list[str] = []
    if message.content:
        parts.append(f"{message.role}: {message.content}")
    for call in message.tool_calls:
        parts.append(f"assistant tool_call {call.name}: {call.arguments}")
    for result in message.tool_results:
        prefix = "tool error" if result.is_error else "tool"
        parts.append(f"{prefix} {result.call_id}: {result.content}")
    return parts


class ChatGptBrowserChatModel:
    """LangChain-shaped SYNC adapter over ``/ask`` for ``llm.invoke(messages)`` call sites.

    Product paths that meter LangChain chat calls (``invoke_metered_chat``) only need an
    object with ``.invoke(messages) -> something-with-.content`` — this provides exactly
    that without adding a langchain dependency to ``ai_workflow_tools``. The caller stays
    responsible for passing ``cost_class="subscription_notional"`` to its metering wrapper
    (the service reports no usage, and pretending metered $0 would break cost honesty).
    Multimodal message parts (image_url) are rejected loudly — ``/ask`` is text-only.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 200.0,
        force_fresh: bool = True,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        # Reuse the async client's transport/validation by composition.
        self._client = ChatGptBrowserLLMClient(
            base_url,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            http_post=http_post,
        )

    def invoke(self, messages: Any) -> Any:
        question = _flatten_langchain_messages(messages)
        if self._client.force_fresh:
            question = f"{question}\n\n(request {uuid.uuid4().hex[:8]})"
        data = self._post_json_sync(
            f"{self._client.base_url}/ask",
            {"question": question, "timeout": int(self._client.timeout_s)},
        )
        reply = str(data.get("reply") or "")
        if data.get("status") != "completed" or not reply.strip():
            raise ChatGptBrowserError(
                f"chatgpt browser /ask returned no reply (status={data.get('status')!r})"
            )
        return _ChatReply(content=reply, raw=data)

    def _post_json_sync(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        http_post = self._client._http_post
        if http_post is None:
            import requests

            http_post = requests.post
        response = http_post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self._client.timeout_s + 30,
        )
        try:
            data = response.json()
        except Exception as exc:
            raise ChatGptBrowserError("chatgpt browser response was not valid JSON") from exc
        status_code = int(getattr(response, "status_code", 200) or 200)
        if status_code >= 400:
            detail = data.get("detail") or data.get("error") if isinstance(data, dict) else None
            raise ChatGptBrowserError(
                f"chatgpt browser service HTTP {status_code}: "
                f"{detail or getattr(response, 'text', '')} "
                "(502 usually means the logged-in ChatGPT browser/extension is down)"
            )
        if not isinstance(data, dict):
            raise ChatGptBrowserError("chatgpt browser response JSON was not an object")
        return data


class _ChatReply:
    """Minimal chat-response shape: ``.content`` plus empty usage metadata attributes."""

    def __init__(self, content: str, raw: dict[str, Any]):
        self.content = content
        self.raw = raw
        self.usage_metadata = None
        self.response_metadata: dict[str, Any] = {}


def _flatten_langchain_messages(messages: Any) -> str:
    parts: list[str] = []
    for message in messages:
        role = getattr(message, "type", None) or getattr(message, "role", "user")
        content = getattr(message, "content", message)
        if isinstance(content, list):
            # LangChain multimodal parts: text is fine, images are not expressible on /ask.
            texts: list[str] = []
            for part in content:
                kind = part.get("type") if isinstance(part, dict) else None
                if kind == "text":
                    texts.append(str(part.get("text") or ""))
                elif kind:
                    raise ChatGptBrowserError(
                        f"ChatGptBrowserChatModel cannot send {kind!r} message parts "
                        "(/ask is text-only); keep vision roles on an API client"
                    )
            content = "\n".join(texts)
        if str(content).strip():
            parts.append(f"{role}: {content}")
    return "\n\n".join(parts)


__all__ = [
    "ChatGptBrowserChatModel",
    "ChatGptBrowserError",
    "ChatGptBrowserLLMClient",
]
