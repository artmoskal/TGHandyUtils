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
    "retry_after_seconds",
    "sanitize_browser_error",
]
