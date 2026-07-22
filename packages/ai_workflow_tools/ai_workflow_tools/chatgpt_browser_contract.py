"""Shared transport contract for the authenticated ChatGPT-browser service."""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Any
from urllib.parse import urlsplit


_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


class ChatGptBrowserError(RuntimeError):
    """The browser service request cannot be made or trusted."""


def browser_headers(base_url: str, bearer_token: str | None) -> dict[str, str]:
    """Build one safe header set and reject unauthenticated remote traffic."""

    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ChatGptBrowserError("chatgpt browser base_url is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ChatGptBrowserError("chatgpt browser base_url must be an HTTP(S) URL with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ChatGptBrowserError("chatgpt browser base_url must not contain credentials")
    del port  # Accessing it above validates the optional port syntax.

    token = str(bearer_token or "").strip()
    if "\r" in token or "\n" in token:
        raise ChatGptBrowserError("chatgpt browser token contains invalid header characters")
    if not token and not _is_loopback_hostname(hostname):
        raise ChatGptBrowserError(
            "a bearer token (CHATGPT_BROWSER_API_TOKEN) is required for a non-loopback "
            "chatgpt browser URL"
        )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def bounded_wait_budget(value: Any) -> float:
    try:
        budget = float(value)
    except (TypeError, ValueError) as exc:
        raise ChatGptBrowserError("chatgpt browser rate-limit wait budget must be finite") from exc
    if not math.isfinite(budget) or budget < 0:
        raise ChatGptBrowserError(
            "chatgpt browser rate-limit wait budget must be finite and non-negative"
        )
    return budget


def positive_timeout(value: Any) -> float:
    """Normalize a request timeout before it reaches HTTP or provider payloads."""

    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ChatGptBrowserError("chatgpt browser timeout must be finite and positive") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ChatGptBrowserError("chatgpt browser timeout must be finite and positive")
    return timeout


def retry_after_seconds(response: Any, data: Any) -> float:
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw is None and isinstance(data, dict):
        raw = data.get("retry_after")
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        return 20.0
    return max(1.0, delay) if math.isfinite(delay) else 20.0


def sanitize_browser_error(value: Any, bearer_token: str | None) -> str:
    """Remove credentials and provider URLs before an error can be persisted."""

    text = str(value)
    token = str(bearer_token or "").strip()
    if token:
        text = text.replace(token, "[REDACTED]")
    return _URL_RE.sub("[REDACTED_URL]", text)


def provider_error_detail(status_code: int, data: Any) -> str:
    """Project only the provider facts that are safe and useful after a failure.

    Provider bodies are external input and can contain prompts, DOM, signed URLs, or image
    bytes. This closed projection intentionally keeps five facts and ignores every sibling.
    FastAPI-style ``{"detail": {...}}`` and direct product envelopes share the same rule.
    The caller applies credential/URL redaction after this structural projection.
    """

    root = data if isinstance(data, dict) else {}
    nested = root.get("detail") if isinstance(root.get("detail"), dict) else {}

    code, message = _provider_error_code_and_message(root, nested)

    parts: list[str] = []
    for key, value in (
        ("status", _first_provider_value(root, nested, "status")),
        ("task_id", _first_provider_value(root, nested, "task_id")),
        ("code", code),
    ):
        rendered = _safe_provider_scalar(value)
        if rendered:
            parts.append(f"{key}={rendered}")
    retry_flag = _first_provider_value(
        root, nested, "retry_requires_new_idempotency_key"
    )
    if type(retry_flag) is bool:
        parts.append(
            "retry_requires_new_idempotency_key=" + str(retry_flag).lower()
        )
    rendered_message = _safe_provider_scalar(message, limit=500)
    if rendered_message:
        parts.append(f"error={rendered_message}")

    detail = "; ".join(parts) if parts else "unrecognized error envelope"
    text = f"chatgpt browser service HTTP {status_code}: {detail}"
    if status_code == 502:
        text += " (502 usually means the logged-in ChatGPT browser/extension is down)"
    return text


def _safe_provider_scalar(value: Any, *, limit: int = 160) -> str:
    if type(value) not in (str, int):
        return ""
    text = " ".join(str(value).split())
    return text[:limit]


def _first_provider_value(root: dict[str, Any], nested: dict[str, Any], *keys: str) -> Any:
    for container in (root, nested):
        for key in keys:
            if key in container and container[key] is not None:
                return container[key]
    return None


def _provider_error_code_and_message(
    root: dict[str, Any], nested: dict[str, Any]
) -> tuple[Any, Any]:
    error_value = _first_provider_value(root, nested, "error")
    error_object = error_value if isinstance(error_value, dict) else {}
    code = _first_provider_value(root, nested, "code", "error_code") or error_object.get("code")
    message = (
        error_object.get("message")
        or error_object.get("detail")
        or (error_value if not isinstance(error_value, (dict, list)) else None)
    )
    if message is None and isinstance(root.get("detail"), str):
        message = root["detail"]
    return code, message


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


__all__ = [
    "ChatGptBrowserError",
    "bounded_wait_budget",
    "browser_headers",
    "positive_timeout",
    "provider_error_detail",
    "retry_after_seconds",
    "sanitize_browser_error",
]
