from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from ai_workflow_engine.usage_contract import NormalizedTokenUsage, UsageError


ProviderLabel = Annotated[
    StrictStr,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]


class ApiKeyAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["api_key"] = "api_key"
    api_key: SecretStr = Field(repr=False)

    @field_validator("api_key")
    @classmethod
    def _nonblank_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must be nonblank")
        return value


class NoAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["none"] = "none"


ProviderAuth = Annotated[ApiKeyAuth | NoAuth, Field(discriminator="mode")]


class OpenAICompatibleProviderConfig(BaseModel):
    """Explicit transport configuration; products own env/secret resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: StrictStr = Field(min_length=1, max_length=2048)
    model: StrictStr = Field(min_length=1, max_length=256)
    provider: ProviderLabel
    auth: ProviderAuth
    timeout_s: StrictFloat = Field(gt=0, allow_inf_nan=False)
    max_response_bytes: StrictInt = Field(default=8 << 20, ge=1024, le=64 << 20)
    temperature: StrictFloat | None = Field(default=None, allow_inf_nan=False)
    max_completion_tokens: StrictInt | None = Field(default=None, gt=0)
    default_headers: dict[StrictStr, SecretStr] = Field(
        default_factory=dict,
        repr=False,
    )

    @field_validator("base_url")
    @classmethod
    def _explicit_safe_base_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        parsed = urlsplit(text)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "base_url must be an explicit http(s) endpoint without credentials, "
                "query, or fragment"
            )
        return text

    @field_validator("model")
    @classmethod
    def _model_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or not normalized.isprintable():
            raise ValueError("model must be nonblank printable text")
        return normalized

    @field_validator("temperature")
    @classmethod
    def _temperature_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("temperature must be finite")
        return value

    @field_validator("default_headers")
    @classmethod
    def _headers_safe(
        cls,
        value: dict[str, SecretStr],
    ) -> dict[str, SecretStr]:
        reserved = {
            "authorization",
            "content-type",
            "content-length",
            "host",
            "x-client-request-id",
        }
        normalized: dict[str, SecretStr] = {}
        for name, secret in value.items():
            key = name.strip()
            if (
                not key
                or key.lower() in reserved
                or re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", key) is None
            ):
                raise ValueError(f"default_headers contains invalid or reserved name {name!r}")
            if "\r" in secret.get_secret_value() or "\n" in secret.get_secret_value():
                raise ValueError(f"default_headers[{name!r}] contains a line break")
            normalized[key] = secret
        return normalized

    @model_validator(mode="after")
    def _auth_is_explicit(self) -> "OpenAICompatibleProviderConfig":
        if isinstance(self.auth, ApiKeyAuth) and not self.auth.api_key.get_secret_value():
            raise ValueError("api-key auth requires a key")
        return self

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"


class OpenAICompatibleProviderError(RuntimeError):
    """Bounded provider failure facts suitable for engine usage recording."""

    def __init__(
        self,
        message: str,
        *,
        failure_kind: Literal[
            "authentication",
            "rate_limit",
            "timeout",
            "provider",
            "transport",
            "invalid_response",
        ],
        model: str,
        invocation_id: str,
        status_code: int | None = None,
        retry_after_s: float | None = None,
        provider_request_id: str | None = None,
        elapsed_ms: int | None = None,
        normalized_usage: NormalizedTokenUsage | None = None,
        usage_error: UsageError | None = None,
        usage_diagnostic: str | None = None,
    ) -> None:
        bounded = str(message or failure_kind).strip()[:500]
        super().__init__(bounded)
        self.failure_kind = failure_kind
        self.model = model
        self.invocation_id = invocation_id
        self.status_code = status_code
        self.retry_after_s = retry_after_s
        self.provider_request_id = provider_request_id
        self.elapsed_ms = elapsed_ms
        self.normalized_usage = normalized_usage
        self.usage_error = usage_error
        self.usage_diagnostic = (
            str(usage_diagnostic)[:500] if usage_diagnostic is not None else None
        )


def secret_values(config: OpenAICompatibleProviderConfig) -> tuple[str, ...]:
    values = [
        secret.get_secret_value()
        for secret in config.default_headers.values()
        if secret.get_secret_value()
    ]
    if isinstance(config.auth, ApiKeyAuth):
        values.append(config.auth.api_key.get_secret_value())
    return tuple(values)


def sanitize_provider_text(
    value: Any,
    *,
    secrets: tuple[str, ...],
    limit: int = 500,
) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:limit]


__all__ = [
    "ApiKeyAuth",
    "NoAuth",
    "OpenAICompatibleProviderConfig",
    "OpenAICompatibleProviderError",
    "ProviderAuth",
    "sanitize_provider_text",
    "secret_values",
]
