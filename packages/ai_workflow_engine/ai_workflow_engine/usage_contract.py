"""Typed token-usage and notional-pricing contracts.

Provider adapters normalize their wire counters into these models. The engine owns
rate selection and arithmetic; viewers and products only project the persisted result.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Annotated, Literal, Protocol, Sequence, runtime_checkable
import uuid

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

ProviderInvocationId = Annotated[
    StrictStr,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
TokenCounterSchema = Literal["codex_inclusive", "claude_disjoint_cache", "generic"]
UsageError = Literal["usage_event_missing", "usage_event_malformed", "invalid_token_counters"]
NotionalPricingSource = Literal[
    "provider_reported",
    "configured_public_rate",
    "configured_proxy_rate",
    "unknown",
]
NotionalUnknownReason = Literal[
    "usage_event_missing",
    "usage_event_malformed",
    "invalid_token_counters",
    "model_missing",
    "rate_missing",
]


def new_provider_invocation_id() -> str:
    """Mint one opaque identity for a single provider attempt."""

    return f"inv-{uuid.uuid4()}"


class NormalizedTokenUsage(BaseModel):
    """Provider-independent quantities with the provider's raw counters retained."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    counter_schema: TokenCounterSchema
    uncached_input_tokens: StrictInt = Field(ge=0)
    cache_read_input_tokens: StrictInt = Field(ge=0)
    cache_creation_input_tokens: StrictInt = Field(ge=0)
    non_reasoning_output_tokens: StrictInt = Field(ge=0)
    reasoning_output_tokens: StrictInt = Field(ge=0)
    raw_input_tokens: StrictInt = Field(ge=0)
    raw_output_tokens: StrictInt = Field(ge=0)
    raw_total_tokens: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _seal_counter_relationships(self) -> "NormalizedTokenUsage":
        if self.raw_output_tokens != (
            self.non_reasoning_output_tokens + self.reasoning_output_tokens
        ):
            raise ValueError(
                "raw_output_tokens must equal non_reasoning_output_tokens + "
                "reasoning_output_tokens"
            )
        if self.counter_schema == "codex_inclusive":
            if self.raw_input_tokens != (
                self.uncached_input_tokens + self.cache_read_input_tokens
            ):
                raise ValueError(
                    "codex inclusive input must equal uncached_input_tokens + "
                    "cache_read_input_tokens"
                )
            expected_total = self.raw_input_tokens + self.raw_output_tokens
        elif self.counter_schema == "claude_disjoint_cache":
            if self.raw_input_tokens != self.uncached_input_tokens:
                raise ValueError(
                    "claude raw input is disjoint from cache-read/cache-creation tokens"
                )
            expected_total = self.raw_input_tokens + self.raw_output_tokens
        else:
            expected_total = self.raw_input_tokens + self.raw_output_tokens
        if self.raw_total_tokens is not None and self.raw_total_tokens != expected_total:
            raise ValueError(
                f"raw_total_tokens must equal raw input + raw output "
                f"({expected_total}), got {self.raw_total_tokens}"
            )
        return self

    @property
    def billable_input_tokens(self) -> int:
        return (
            self.uncached_input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    @property
    def output_tokens(self) -> int:
        return self.non_reasoning_output_tokens + self.reasoning_output_tokens


class NotionalRate(BaseModel):
    """One versioned provider/model rate used for observed plan-value calculation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: StrictStr
    model_prefix: StrictStr
    rate_version: StrictStr
    source: Literal["configured_public_rate", "configured_proxy_rate"]
    currency: Literal["USD"] = "USD"
    uncached_input_per_1m: StrictFloat = Field(ge=0, allow_inf_nan=False)
    cached_input_per_1m: StrictFloat = Field(ge=0, allow_inf_nan=False)
    cache_creation_input_per_1m: StrictFloat = Field(ge=0, allow_inf_nan=False)
    output_per_1m: StrictFloat = Field(ge=0, allow_inf_nan=False)

    @field_validator("provider", "model_prefix", "rate_version")
    @classmethod
    def _non_blank_printable(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("pricing identity fields must be non-blank")
        if not value.isprintable():
            raise ValueError("pricing identity fields must be printable")
        return value


class NotionalPricingResult(BaseModel):
    """Persisted pricing truth for one usage event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: NotionalPricingSource
    amount_usd: StrictFloat | None = Field(default=None, ge=0, allow_inf_nan=False)
    catalog_version: StrictStr | None = None
    rate: NotionalRate | None = None
    unknown_reason: NotionalUnknownReason | None = None

    @model_validator(mode="after")
    def _known_or_unknown(self) -> "NotionalPricingResult":
        if self.source == "unknown":
            if self.amount_usd is not None or self.rate is not None:
                raise ValueError("unknown pricing cannot carry an amount or rate")
            if self.unknown_reason is None:
                raise ValueError("unknown pricing requires unknown_reason")
            return self
        if self.amount_usd is None:
            raise ValueError("known pricing requires amount_usd")
        if not math.isfinite(self.amount_usd):
            raise ValueError("known pricing amount must be finite")
        if not self.catalog_version or not self.catalog_version.strip():
            raise ValueError("known pricing requires a non-blank catalog_version")
        if self.unknown_reason is not None:
            raise ValueError("known pricing cannot carry unknown_reason")
        if self.source == "provider_reported" and self.rate is not None:
            raise ValueError("provider-reported pricing must not claim a configured rate")
        if self.source != "provider_reported" and self.rate is None:
            raise ValueError("configured pricing requires the exact rate used")
        if self.rate is not None and self.rate.source != self.source:
            raise ValueError("pricing source must match the configured rate source")
        return self

    @property
    def known(self) -> bool:
        return self.source != "unknown"


class NotionalPricingConfig(BaseModel):
    """Strict product configuration for one coherent rate catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_version: StrictStr
    rates: tuple[NotionalRate, ...]

    @field_validator("catalog_version")
    @classmethod
    def _catalog_version_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("catalog_version must be non-blank")
        return value

    @model_validator(mode="after")
    def _unique_matches(self) -> "NotionalPricingConfig":
        seen: set[tuple[str, str]] = set()
        for rate in self.rates:
            key = (rate.provider, rate.model_prefix)
            if key in seen:
                raise ValueError(
                    f"duplicate pricing match for provider/model prefix {key!r}"
                )
            seen.add(key)
        return self


@runtime_checkable
class NotionalPricingPolicy(Protocol):
    """Pure engine-owned pricing policy."""

    def price(
        self,
        *,
        provider: str,
        model: str,
        usage: NormalizedTokenUsage | None,
        usage_error: UsageError | None,
        provider_reported_usd: float | None,
    ) -> NotionalPricingResult: ...


class CatalogNotionalPricingPolicy:
    """Longest-prefix catalog policy with exact Decimal arithmetic."""

    def __init__(self, config: NotionalPricingConfig) -> None:
        self.config = config

    def price(
        self,
        *,
        provider: str,
        model: str,
        usage: NormalizedTokenUsage | None,
        usage_error: UsageError | None,
        provider_reported_usd: float | None,
    ) -> NotionalPricingResult:
        if provider_reported_usd is not None:
            if not math.isfinite(provider_reported_usd) or provider_reported_usd < 0:
                raise ValueError("provider-reported notional cost must be finite and non-negative")
            return NotionalPricingResult(
                source="provider_reported",
                amount_usd=provider_reported_usd,
                catalog_version="provider-reported",
            )
        if usage is None:
            return NotionalPricingResult(
                source="unknown",
                unknown_reason=usage_error or "usage_event_missing",
            )
        if not model.strip():
            return NotionalPricingResult(source="unknown", unknown_reason="model_missing")
        rate = self._resolve_rate(provider, model)
        if rate is None:
            return NotionalPricingResult(source="unknown", unknown_reason="rate_missing")
        return NotionalPricingResult(
            source=rate.source,
            amount_usd=calculate_notional_amount(usage, rate),
            catalog_version=self.config.catalog_version,
            rate=rate,
        )

    def _resolve_rate(self, provider: str, model: str) -> NotionalRate | None:
        matches = [
            rate
            for rate in self.config.rates
            if rate.provider == provider and model.startswith(rate.model_prefix)
        ]
        return max(matches, key=lambda rate: len(rate.model_prefix), default=None)


def calculate_notional_amount(
    usage: NormalizedTokenUsage,
    rate: NotionalRate,
) -> float:
    """Apply the one token-pricing formula used by policies and event validation."""

    million = Decimal(1_000_000)
    amount = (
        Decimal(usage.uncached_input_tokens)
        * Decimal(str(rate.uncached_input_per_1m))
        + Decimal(usage.cache_read_input_tokens)
        * Decimal(str(rate.cached_input_per_1m))
        + Decimal(usage.cache_creation_input_tokens)
        * Decimal(str(rate.cache_creation_input_per_1m))
        + Decimal(usage.output_tokens) * Decimal(str(rate.output_per_1m))
    ) / million
    return float(amount)


def default_notional_pricing_config() -> NotionalPricingConfig:
    return NotionalPricingConfig(
        catalog_version="public-rates-2026-07-20",
        rates=_DEFAULT_NOTIONAL_RATES,
    )


def default_notional_pricing_policy() -> CatalogNotionalPricingPolicy:
    return CatalogNotionalPricingPolicy(default_notional_pricing_config())


def default_chat_rate_table() -> dict[str, dict[str, float]]:
    """Return the default chat rates without creating a second model-price authority."""

    return {
        rate.model_prefix: {
            "input_per_1m": rate.uncached_input_per_1m,
            "cached_input_per_1m": rate.cached_input_per_1m,
            "output_per_1m": rate.output_per_1m,
        }
        for rate in _DEFAULT_NOTIONAL_RATES
        if rate.provider == "codex_exec"
    }


_DEFAULT_NOTIONAL_RATES: Sequence[NotionalRate] = (
    NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.6-sol",
        rate_version="openai-2026-07-20",
        source="configured_public_rate",
        uncached_input_per_1m=5.0,
        cached_input_per_1m=0.5,
        cache_creation_input_per_1m=6.25,
        output_per_1m=30.0,
    ),
    NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.6-terra",
        rate_version="openai-2026-07-20",
        source="configured_public_rate",
        uncached_input_per_1m=2.5,
        cached_input_per_1m=0.25,
        cache_creation_input_per_1m=3.125,
        output_per_1m=15.0,
    ),
    NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.6-luna",
        rate_version="openai-2026-07-20",
        source="configured_public_rate",
        uncached_input_per_1m=1.0,
        cached_input_per_1m=0.1,
        cache_creation_input_per_1m=1.25,
        output_per_1m=6.0,
    ),
    NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.6",
        rate_version="openai-2026-07-20",
        source="configured_public_rate",
        uncached_input_per_1m=5.0,
        cached_input_per_1m=0.5,
        cache_creation_input_per_1m=6.25,
        output_per_1m=30.0,
    ),
    NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.4-mini",
        rate_version="openai-2026-07-20",
        source="configured_public_rate",
        uncached_input_per_1m=0.75,
        cached_input_per_1m=0.075,
        cache_creation_input_per_1m=0.75,
        output_per_1m=4.5,
    ),
    NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.4-nano",
        rate_version="openai-2026-07-20",
        source="configured_public_rate",
        uncached_input_per_1m=0.20,
        cached_input_per_1m=0.02,
        cache_creation_input_per_1m=0.20,
        output_per_1m=1.25,
    ),
    NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.4",
        rate_version="openai-2026-07-20",
        source="configured_public_rate",
        uncached_input_per_1m=2.5,
        cached_input_per_1m=0.25,
        cache_creation_input_per_1m=2.5,
        output_per_1m=15.0,
    ),
    NotionalRate(
        provider="codex_exec",
        model_prefix="gpt-5.5",
        rate_version="openai-2026-07-20",
        source="configured_public_rate",
        uncached_input_per_1m=5.0,
        cached_input_per_1m=0.5,
        cache_creation_input_per_1m=5.0,
        output_per_1m=30.0,
    ),
)


__all__ = [
    "CatalogNotionalPricingPolicy",
    "NormalizedTokenUsage",
    "NotionalPricingConfig",
    "NotionalPricingPolicy",
    "NotionalPricingResult",
    "NotionalRate",
    "ProviderInvocationId",
    "UsageError",
    "calculate_notional_amount",
    "default_chat_rate_table",
    "default_notional_pricing_config",
    "default_notional_pricing_policy",
    "new_provider_invocation_id",
]
