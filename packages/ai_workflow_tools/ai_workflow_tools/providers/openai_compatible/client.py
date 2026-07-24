from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
import json
import math
import time
from typing import Any

from ai_workflow_engine.execution_window import invocation_window_remaining_s
from ai_workflow_engine.llm_protocol import LLMRequest, LLMResponse
from ai_workflow_engine.usage_contract import new_provider_invocation_id

from ai_workflow_tools.providers.openai_compatible.mapping import (
    request_payload,
    response_from_payload,
)
from ai_workflow_tools.providers.openai_compatible.models import (
    ApiKeyAuth,
    OpenAICompatibleProviderConfig,
    OpenAICompatibleProviderError,
    sanitize_provider_text,
    secret_values,
)


class OpenAICompatibleLLMClient:
    """One non-streaming OpenAI chat-completions transport."""

    def __init__(
        self,
        config: OpenAICompatibleProviderConfig,
        *,
        http_client: Any | None = None,
    ) -> None:
        if not isinstance(config, OpenAICompatibleProviderConfig):
            raise TypeError("config must be an OpenAICompatibleProviderConfig")
        self.config = config
        self.model = config.model
        self.provider_label = config.provider
        self._secrets = secret_values(config)
        self._owns_http_client = http_client is None
        self._http_client = http_client or self._build_http_client()

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        if not isinstance(request, LLMRequest):
            raise TypeError("request must be an LLMRequest")
        invocation_id = request.invocation_id or new_provider_invocation_id()
        request = request.model_copy(update={"invocation_id": invocation_id})
        payload = request_payload(request, self.config)
        timeout_s = self._effective_timeout_s(invocation_id)
        started = time.monotonic()
        try:
            raw, status_code, provider_request_id, response_headers = (
                await asyncio.wait_for(
                    self._exchange(
                        payload,
                        invocation_id=invocation_id,
                        timeout_s=timeout_s,
                        started=started,
                    ),
                    timeout=timeout_s,
                )
            )
        except asyncio.CancelledError:
            raise
        except OpenAICompatibleProviderError:
            raise
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if isinstance(exc, asyncio.TimeoutError) or self._is_timeout_exception(exc):
                raise self._error(
                    "provider request timed out",
                    "timeout",
                    invocation_id,
                    elapsed_ms=elapsed_ms,
                ) from None
            raise self._error(
                f"provider transport failed: {sanitize_provider_text(exc, secrets=self._secrets)}",
                "transport",
                invocation_id,
                elapsed_ms=elapsed_ms,
            ) from None

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if status_code < 200 or status_code >= 300:
            try:
                data = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
            raise self._http_error(
                data,
                response_headers=response_headers,
                status_code=status_code,
                invocation_id=invocation_id,
                provider_request_id=provider_request_id,
                elapsed_ms=elapsed_ms,
            )
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise self._error(
                "provider response was not valid JSON",
                "invalid_response",
                invocation_id,
                status_code=status_code,
                provider_request_id=provider_request_id,
                elapsed_ms=elapsed_ms,
            ) from None
        return response_from_payload(
            data,
            self.config,
            invocation_id=invocation_id,
            provider_request_id=provider_request_id,
            elapsed_ms=elapsed_ms,
        )

    async def _exchange(
        self,
        payload: dict[str, Any],
        *,
        invocation_id: str,
        timeout_s: float,
        started: float,
    ) -> tuple[bytes, int, str | None, Any]:
        async with self._http_client.stream(
            "POST",
            self.config.chat_completions_url,
            json=payload,
            headers={"X-Client-Request-Id": invocation_id},
            timeout=timeout_s,
        ) as response:
            provider_request_id = self._provider_request_id(response)
            status_code = int(getattr(response, "status_code", 0) or 0)
            raw = await self._read_bounded(
                response,
                invocation_id=invocation_id,
                status_code=status_code,
                provider_request_id=provider_request_id,
                started=started,
            )
            return raw, status_code, provider_request_id, response.headers

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    async def __aenter__(self) -> "OpenAICompatibleLLMClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.aclose()

    def _build_http_client(self) -> Any:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                'OpenAI-compatible providers require `pip install "ai-workflow-tools[openai]"`'
            ) from exc
        headers = {
            name: value.get_secret_value()
            for name, value in self.config.default_headers.items()
        }
        if isinstance(self.config.auth, ApiKeyAuth):
            headers["Authorization"] = (
                f"Bearer {self.config.auth.api_key.get_secret_value()}"
            )
        return httpx.AsyncClient(
            headers=headers,
            follow_redirects=False,
            timeout=self.config.timeout_s,
        )

    async def _read_bounded(
        self,
        response: Any,
        *,
        invocation_id: str,
        status_code: int,
        provider_request_id: str | None,
        started: float,
    ) -> bytes:
        retained = bytearray()
        async for chunk in response.aiter_bytes():
            if not isinstance(chunk, bytes):
                chunk = bytes(chunk)
            remaining = self.config.max_response_bytes - len(retained)
            if len(chunk) > remaining:
                raise self._error(
                    "provider response exceeded configured byte limit",
                    "invalid_response",
                    invocation_id,
                    status_code=status_code,
                    provider_request_id=provider_request_id,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            retained.extend(chunk)
        return bytes(retained)

    def _effective_timeout_s(self, invocation_id: str) -> float:
        remaining = invocation_window_remaining_s()
        timeout_s = self.config.timeout_s
        if remaining is not None:
            timeout_s = min(timeout_s, remaining.soft_s)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise self._error(
                "provider request refused because the engine invocation window is exhausted",
                "timeout",
                invocation_id,
                elapsed_ms=0,
            )
        return timeout_s

    @staticmethod
    def _is_timeout_exception(exc: BaseException) -> bool:
        try:
            import httpx
        except ImportError:
            return False
        return isinstance(exc, httpx.TimeoutException)

    def _http_error(
        self,
        payload: Any,
        *,
        response_headers: Any,
        status_code: int,
        invocation_id: str,
        provider_request_id: str | None,
        elapsed_ms: int,
    ) -> OpenAICompatibleProviderError:
        kind = (
            "authentication"
            if status_code in {401, 403}
            else "rate_limit"
            if status_code == 429
            else "provider"
        )
        detail = self._safe_error_detail(payload)
        retry_after = (
            self._retry_after_s(response_headers) if status_code == 429 else None
        )
        return self._error(
            f"provider HTTP {status_code}: {detail}",
            kind,
            invocation_id,
            status_code=status_code,
            retry_after_s=retry_after,
            provider_request_id=provider_request_id,
            elapsed_ms=elapsed_ms,
        )

    def _safe_error_detail(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return "request failed"
        error = payload.get("error")
        if isinstance(error, dict):
            code = sanitize_provider_text(error.get("code"), secrets=self._secrets, limit=80)
            message = sanitize_provider_text(
                error.get("message"),
                secrets=self._secrets,
                limit=300,
            )
            return ": ".join(part for part in (code, message) if part) or "request failed"
        return sanitize_provider_text(error, secrets=self._secrets, limit=300) or "request failed"

    @staticmethod
    def _provider_request_id(response: Any) -> str | None:
        headers = getattr(response, "headers", {})
        for name in ("x-request-id", "request-id", "x-ollama-request-id"):
            value = headers.get(name)
            if value:
                return str(value)[:128]
        return None

    @staticmethod
    def _retry_after_s(headers: Any) -> float | None:
        raw = headers.get("retry-after")
        if raw is None:
            return None
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(str(raw))
                seconds = max(0.0, parsed.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                return None
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return min(seconds, 3600.0)

    def _error(
        self,
        message: str,
        failure_kind: Any,
        invocation_id: str,
        **facts: Any,
    ) -> OpenAICompatibleProviderError:
        return OpenAICompatibleProviderError(
            sanitize_provider_text(message, secrets=self._secrets),
            failure_kind=failure_kind,
            model=self.config.model,
            invocation_id=invocation_id,
            usage_error="usage_event_missing",
            **facts,
        )


__all__ = ["OpenAICompatibleLLMClient"]
