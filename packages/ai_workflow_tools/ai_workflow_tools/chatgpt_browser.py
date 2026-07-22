"""LLM client over the ChatGPT-browser HTTP service (subscription session, no API key).

The service (`POST /ask`) drives a logged-in ChatGPT browser on the always-on box and returns
the reply text. This client adapts it to the engine's ``LLMCallable`` protocol, so any
structured node can swap onto it exactly like ``ConsoleLLMClient`` swaps onto ``claude -p``:
text in, text out, engine-side parsing/repair unchanged.

Contract notes (mirror of the image provider in ``media/image_generation.py``):
- Subscription economics: responses carry ``cost_class="subscription_notional"`` with zero
  token counts and no USD — the service reports no usage, and pretending otherwise would
  break cost honesty.
- The service caches identical questions; clients append an internal variation token so
  structured retries cannot replay a previously rejected answer.
- One browser, sequential queue, slow calls: keep timeouts generous; failures are loud
  (HTTP 502 means the browser/extension side is down).
- No image support on ``/ask`` — requests with images are rejected loudly.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable

from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest, LLMResponse
from ai_workflow_tools.chatgpt_browser_contract import (
    ChatGptBrowserError,
    bounded_wait_budget,
    browser_headers,
    positive_timeout,
    provider_error_detail,
    retry_after_seconds,
    sanitize_browser_error,
)

logger = logging.getLogger(__name__)

# The service self-throttles to protect the shared ChatGPT account (HTTP 429 +
# Retry-After); the documented consumer contract is: back off and retry, don't hammer.
# The wait is bounded and every wait is logged — cooperation, never a silent stall.
DEFAULT_RATE_LIMIT_MAX_WAIT_S = 120.0


class ChatGptBrowserLLMClient:
    """LLMCallable over the ChatGPT-browser control server's ``/ask`` endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 200.0,
        bearer_token: str | None = None,
        rate_limit_max_wait_s: float = DEFAULT_RATE_LIMIT_MAX_WAIT_S,
        http_post: Callable[..., Any] | None = None,
        sleeper: Callable[[float], Any] | None = None,
    ) -> None:
        base_url = str(base_url or "").strip()
        if not base_url:
            raise ChatGptBrowserError(
                "ChatGptBrowserLLMClient requires an explicit base_url "
                "(CHATGPT_BROWSER_API_URL) — no default endpoint is assumed"
            )
        self.base_url = base_url.rstrip("/")
        self.timeout_s = positive_timeout(timeout_s)
        self._bearer_token = str(bearer_token or "").strip()
        self._headers = browser_headers(self.base_url, self._bearer_token)
        self.rate_limit_max_wait_s = bounded_wait_budget(rate_limit_max_wait_s)
        self._http_post = http_post
        self._sleeper = sleeper
        # Usage-event attribution for the engine's plain-callable metering path.
        self.provider_label = "chatgpt_browser"

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)
        question = _flatten_request(request)
        # Cache busting is invariant text behavior, not an image-conversation mode.
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
        sleep = self._sleeper or asyncio.sleep
        waited = 0.0
        while True:
            try:
                response = await asyncio.to_thread(
                    http_post,
                    url,
                    json=payload,
                    headers=dict(self._headers),
                    timeout=self.timeout_s + 30,
                )
            except Exception as exc:
                raise ChatGptBrowserError(
                    "chatgpt browser transport failed: "
                    f"{sanitize_browser_error(exc, self._bearer_token)}"
                ) from None
            try:
                data = response.json()
            except Exception as exc:
                raise ChatGptBrowserError("chatgpt browser response was not valid JSON") from exc
            status_code = int(getattr(response, "status_code", 200) or 200)
            if status_code == 429:
                # Documented consumer contract: the service self-throttles to protect the
                # shared ChatGPT account — honour Retry-After, bounded, loud when exhausted.
                delay = retry_after_seconds(response, data)
                if waited + delay > self.rate_limit_max_wait_s:
                    raise ChatGptBrowserError(
                        f"chatgpt browser rate-limited beyond the {self.rate_limit_max_wait_s:.0f}s "
                        f"wait budget (waited {waited:.0f}s, next retry_after={delay:.0f}s) — "
                        "the service may be in a circuit-breaker cooldown; try later"
                    )
                logger.info(
                    "chatgpt_browser rate_limited retry_after=%.0fs waited=%.0fs budget=%.0fs",
                    delay,
                    waited,
                    self.rate_limit_max_wait_s,
                )
                result = sleep(delay)
                if asyncio.iscoroutine(result):
                    await result
                waited += delay
                continue
            if status_code >= 400:
                # incident handoff 2026-07-22: structured facts preserved; 502-only hint
                raise ChatGptBrowserError(
                    sanitize_browser_error(
                        provider_error_detail(status_code, data), self._bearer_token
                    )
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
        if request.tools or request.tool_choice:
            # /ask cannot execute tool calls — flattening the specs into text and hoping
            # would be a silent capability drop (codex ship-review finding).
            raise ChatGptBrowserError(
                "ChatGptBrowserLLMClient cannot host tool-calling turns (/ask is a plain "
                "text pipe); use an API client or an agent-episode capability"
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
        bearer_token: str | None = None,
        rate_limit_max_wait_s: float = DEFAULT_RATE_LIMIT_MAX_WAIT_S,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        # Reuse the async client's transport/validation by composition.
        self._client = ChatGptBrowserLLMClient(
            base_url,
            timeout_s=timeout_s,
            bearer_token=bearer_token,
            rate_limit_max_wait_s=rate_limit_max_wait_s,
            http_post=http_post,
        )

    def invoke(self, messages: Any) -> Any:
        question = _flatten_langchain_messages(messages)
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
        sleep = self._client._sleeper or time.sleep
        budget = self._client.rate_limit_max_wait_s
        waited = 0.0
        while True:
            try:
                response = http_post(
                    url,
                    json=payload,
                    headers=dict(self._client._headers),
                    timeout=self._client.timeout_s + 30,
                )
            except Exception as exc:
                raise ChatGptBrowserError(
                    "chatgpt browser transport failed: "
                    f"{sanitize_browser_error(exc, self._client._bearer_token)}"
                ) from None
            try:
                data = response.json()
            except Exception as exc:
                raise ChatGptBrowserError("chatgpt browser response was not valid JSON") from exc
            status_code = int(getattr(response, "status_code", 200) or 200)
            if status_code == 429:
                delay = retry_after_seconds(response, data)
                if waited + delay > budget:
                    raise ChatGptBrowserError(
                        f"chatgpt browser rate-limited beyond the {budget:.0f}s wait budget "
                        f"(waited {waited:.0f}s, next retry_after={delay:.0f}s) — "
                        "the service may be in a circuit-breaker cooldown; try later"
                    )
                logger.info(
                    "chatgpt_browser rate_limited retry_after=%.0fs waited=%.0fs budget=%.0fs",
                    delay,
                    waited,
                    budget,
                )
                sleep(delay)
                waited += delay
                continue
            if status_code >= 400:
                # incident handoff 2026-07-22: structured facts preserved; 502-only hint
                raise ChatGptBrowserError(
                    sanitize_browser_error(
                        provider_error_detail(status_code, data), self._client._bearer_token
                    )
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
