from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from ai_workflow_engine import (
    WorkflowBuilder,
    WorkflowEngine,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.budget import (
    WorkflowBudget,
    WorkflowUsageContext,
    workflow_usage_scope,
)
from ai_workflow_engine.config_loader import load_workflow_config
from ai_workflow_engine.models import (
    WorkflowRunContext,
    WorkflowUsageEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.usage_contract import (
    CatalogNotionalPricingPolicy,
    NormalizedTokenUsage,
    NotionalPricingConfig,
    NotionalPricingResult,
    NotionalRate,
    default_notional_pricing_policy,
)
from ai_workflow_engine.usage_events import record_usage_event

pytestmark = pytest.mark.unit


def _codex_usage() -> NormalizedTokenUsage:
    return NormalizedTokenUsage(
        counter_schema="codex_inclusive",
        uncached_input_tokens=100,
        cache_read_input_tokens=20,
        cache_creation_input_tokens=0,
        non_reasoning_output_tokens=20,
        reasoning_output_tokens=10,
        raw_input_tokens=120,
        raw_output_tokens=30,
        raw_total_tokens=150,
    )


def _policy(*, source="configured_public_rate", input_rate=2.5):
    return CatalogNotionalPricingPolicy(
        NotionalPricingConfig(
            catalog_version="test-v1",
            rates=(
                NotionalRate(
                    provider="codex_exec",
                    model_prefix="gpt-test",
                    rate_version="rate-v1",
                    source=source,
                    uncached_input_per_1m=input_rate,
                    cached_input_per_1m=0.25,
                    cache_creation_input_per_1m=3.0,
                    output_per_1m=15.0,
                ),
            ),
        )
    )


def test_pricing_uses_uncached_cached_and_inclusive_output_without_reasoning_double_count():
    result = _policy().price(
        provider="codex_exec",
        model="gpt-test-codex",
        usage=_codex_usage(),
        usage_error=None,
        provider_reported_usd=None,
    )

    assert result.source == "configured_public_rate"
    assert result.amount_usd == pytest.approx(0.000705)
    assert result.rate is not None
    assert result.rate.rate_version == "rate-v1"


@pytest.mark.parametrize(
    ("model", "input_rate", "cached_rate", "cache_write_rate", "output_rate"),
    [
        ("gpt-5.6", 5.0, 0.5, 6.25, 30.0),
        ("gpt-5.6-sol", 5.0, 0.5, 6.25, 30.0),
        ("gpt-5.6-terra", 2.5, 0.25, 3.125, 15.0),
        ("gpt-5.6-luna", 1.0, 0.1, 1.25, 6.0),
        ("gpt-5.5", 5.0, 0.5, 5.0, 30.0),
        ("gpt-5.4", 2.5, 0.25, 2.5, 15.0),
        ("gpt-5.4-mini", 0.75, 0.075, 0.75, 4.5),
        ("gpt-5.4-nano", 0.2, 0.02, 0.2, 1.25),
    ],
)
def test_default_public_catalog_prices_each_supported_codex_family_exactly(
    model,
    input_rate,
    cached_rate,
    cache_write_rate,
    output_rate,
):
    usage = NormalizedTokenUsage(
        counter_schema="claude_disjoint_cache",
        uncached_input_tokens=10,
        cache_read_input_tokens=4,
        cache_creation_input_tokens=3,
        non_reasoning_output_tokens=5,
        reasoning_output_tokens=2,
        raw_input_tokens=10,
        raw_output_tokens=7,
        raw_total_tokens=17,
    )

    result = default_notional_pricing_policy().price(
        provider="codex_exec",
        model=model,
        usage=usage,
        usage_error=None,
        provider_reported_usd=None,
    )

    assert result.rate is not None
    assert result.rate.uncached_input_per_1m == input_rate
    assert result.rate.cached_input_per_1m == cached_rate
    assert result.rate.cache_creation_input_per_1m == cache_write_rate
    assert result.rate.output_per_1m == output_rate
    assert result.amount_usd == pytest.approx(
        (
            10 * input_rate
            + 4 * cached_rate
            + 3 * cache_write_rate
            + 7 * output_rate
        )
        / 1_000_000
    )


def test_provider_reported_cost_wins_over_catalog_calculation():
    result = _policy(input_rate=999).price(
        provider="codex_exec",
        model="gpt-test",
        usage=_codex_usage(),
        usage_error=None,
        provider_reported_usd=0.123,
    )

    assert result == NotionalPricingResult(
        source="provider_reported",
        amount_usd=0.123,
        catalog_version="provider-reported",
    )


def test_configured_proxy_rate_never_masquerades_as_public_pricing():
    result = _policy(source="configured_proxy_rate").price(
        provider="codex_exec",
        model="gpt-test",
        usage=_codex_usage(),
        usage_error=None,
        provider_reported_usd=None,
    )

    assert result.source == "configured_proxy_rate"
    assert result.rate is not None
    assert result.rate.source == "configured_proxy_rate"


def test_known_sub_microdollar_event_never_rounds_to_fake_zero():
    usage = NormalizedTokenUsage(
        counter_schema="claude_disjoint_cache",
        uncached_input_tokens=1,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        non_reasoning_output_tokens=0,
        reasoning_output_tokens=0,
        raw_input_tokens=1,
        raw_output_tokens=0,
        raw_total_tokens=1,
    )
    result = _policy(input_rate=0.2).price(
        provider="codex_exec",
        model="gpt-test",
        usage=usage,
        usage_error=None,
        provider_reported_usd=None,
    )

    assert result.amount_usd == 0.0000002
    assert result.amount_usd > 0


def test_pricing_formula_is_monotonic_for_every_quantity_and_prices_reasoning_once():
    policy = _policy()
    base = NormalizedTokenUsage(
        counter_schema="claude_disjoint_cache",
        uncached_input_tokens=10,
        cache_read_input_tokens=4,
        cache_creation_input_tokens=3,
        non_reasoning_output_tokens=5,
        reasoning_output_tokens=2,
        raw_input_tokens=10,
        raw_output_tokens=7,
        raw_total_tokens=17,
    )

    def amount(usage):
        result = policy.price(
            provider="codex_exec",
            model="gpt-test",
            usage=usage,
            usage_error=None,
            provider_reported_usd=None,
        )
        assert result.amount_usd is not None
        return result.amount_usd

    baseline = amount(base)
    changes = (
        (
            base.model_copy(
                update={
                    "uncached_input_tokens": 11,
                    "raw_input_tokens": 11,
                    "raw_total_tokens": 18,
                }
            ),
            2.5 / 1_000_000,
        ),
        (base.model_copy(update={"cache_read_input_tokens": 5}), 0.25 / 1_000_000),
        (
            base.model_copy(update={"cache_creation_input_tokens": 4}),
            3.0 / 1_000_000,
        ),
        (
            base.model_copy(
                update={
                    "non_reasoning_output_tokens": 6,
                    "raw_output_tokens": 8,
                    "raw_total_tokens": 18,
                }
            ),
            15.0 / 1_000_000,
        ),
        (
            base.model_copy(
                update={
                    "reasoning_output_tokens": 3,
                    "raw_output_tokens": 8,
                    "raw_total_tokens": 18,
                }
            ),
            15.0 / 1_000_000,
        ),
    )
    for changed, expected_delta in changes:
        changed = NormalizedTokenUsage.model_validate(changed.model_dump())
        assert amount(changed) - baseline == pytest.approx(expected_delta)


@pytest.mark.parametrize(
    ("usage_error", "model", "reason"),
    [
        ("usage_event_missing", "gpt-test", "usage_event_missing"),
        ("invalid_token_counters", "gpt-test", "invalid_token_counters"),
        (None, "", "model_missing"),
        (None, "other", "rate_missing"),
    ],
)
def test_unknown_pricing_is_typed_and_never_fake_zero(usage_error, model, reason):
    result = _policy().price(
        provider="codex_exec",
        model=model,
        usage=None if usage_error else _codex_usage(),
        usage_error=usage_error,
        provider_reported_usd=None,
    )

    assert result.source == "unknown"
    assert result.amount_usd is None
    assert result.unknown_reason == reason


@pytest.mark.parametrize(
    "update",
    [
        {"cached_input_per_1m": -1},
        {"output_per_1m": float("inf")},
        {"output_per_1m": True},
        {"provider": ""},
        {"rate_version": ""},
    ],
)
def test_rate_contract_rejects_invalid_values(update):
    payload = {
        "provider": "codex_exec",
        "model_prefix": "gpt-test",
        "rate_version": "v1",
        "source": "configured_public_rate",
        "uncached_input_per_1m": 1,
        "cached_input_per_1m": 1,
        "cache_creation_input_per_1m": 1,
        "output_per_1m": 1,
        **update,
    }
    with pytest.raises(ValidationError):
        NotionalRate.model_validate(payload)


def test_usage_event_rejects_scalar_counters_that_contradict_normalized_usage():
    with pytest.raises(ValidationError, match="scalar token totals"):
        WorkflowUsageEvent(
            provider="codex_exec",
            operation="tool",
            cost_class="subscription_notional",
            node="agent",
            model="gpt-test",
            input_tokens=0,
            output_tokens=30,
            total_tokens=30,
            normalized_usage=_codex_usage(),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "cost_class": "subscription_notional",
            "estimated_usd": 0.1,
        },
        {
            "cost_class": "metered",
            "notional_usd": 0.1,
        },
        {
            "cost_class": "metered",
            "provider_reported_notional_usd": 0.1,
        },
        {
            "cost_class": "metered",
            "notional_pricing": NotionalPricingResult(
                source="provider_reported",
                amount_usd=0.1,
                catalog_version="provider-reported",
            ),
        },
    ],
)
def test_usage_event_rejects_cross_booked_metered_and_subscription_cost(payload):
    with pytest.raises(ValidationError, match="cannot carry"):
        WorkflowUsageEvent(node="agent", **payload)


def test_usage_event_rejects_bare_subscription_amount_without_typed_provenance():
    with pytest.raises(ValidationError, match="requires typed notional_pricing provenance"):
        WorkflowUsageEvent(
            node="agent",
            cost_class="subscription_notional",
            notional_usd=0.1,
        )


@pytest.mark.parametrize(
    "update",
    [
        {"notional_usd": 0.2},
        {"provider_reported_notional_usd": 0.2},
    ],
)
def test_persisted_provider_reported_pricing_rejects_contradictory_amounts(update):
    payload = {
        "node": "agent",
        "cost_class": "subscription_notional",
        "notional_usd": 0.1,
        "provider_reported_notional_usd": 0.1,
        "notional_pricing": NotionalPricingResult(
            source="provider_reported",
            amount_usd=0.1,
            catalog_version="provider-reported",
        ),
        **update,
    }

    with pytest.raises(ValidationError, match="must match"):
        WorkflowUsageEvent(**payload)


def test_persisted_configured_pricing_revalidates_provider_model_and_arithmetic():
    pricing = NotionalPricingResult(
        source="configured_public_rate",
        amount_usd=0.000705,
        catalog_version="test-v1",
        rate=_policy().config.rates[0],
    )
    base = {
        "provider": "codex_exec",
        "operation": "tool",
        "cost_class": "subscription_notional",
        "node": "agent",
        "model": "gpt-test",
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "normalized_usage": _codex_usage(),
        "notional_usd": 0.000705,
        "notional_pricing": pricing,
    }

    assert WorkflowUsageEvent(**base).notional_usd == pytest.approx(0.000705)
    with pytest.raises(ValidationError, match="provider and model"):
        WorkflowUsageEvent(**{**base, "model": "other"})
    with pytest.raises(ValidationError, match="token/rate basis"):
        WorkflowUsageEvent(
            **{
                **base,
                "notional_usd": 0.5,
                "notional_pricing": pricing.model_copy(update={"amount_usd": 0.5}),
            }
        )


def test_record_usage_event_prices_before_capture_summary_and_sink():
    summary = WorkflowUsageSummary()
    context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="pricing-run", workflow_type="pricing"),
        summary,
        WorkflowBudget(),
        notional_pricing_policy=_policy(),
    )
    event = WorkflowUsageEvent(
        provider="codex_exec",
        operation="tool",
        cost_class="subscription_notional",
        node="agent",
        model="gpt-test-codex",
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        normalized_usage=_codex_usage(),
    )

    with workflow_usage_scope(context):
        record_usage_event(event)

    assert summary.events == [event]
    assert summary.events[0] is event
    assert event.notional_usd == pytest.approx(0.000705)
    assert event.notional_pricing is not None
    assert event.notional_pricing.catalog_version == "test-v1"


def test_pricing_config_is_strict_when_loaded_from_workflow_yaml_shape():
    bundle = load_workflow_config(
        defaults={
            "workflow": {"workflow_type": "priced"},
            "pricing": _policy().config.model_dump(),
        }
    )

    assert bundle.pricing == _policy().config
    with pytest.raises(Exception, match="pricing validation failed"):
        load_workflow_config(
            defaults={
                "workflow": {"workflow_type": "priced"},
                "pricing": {
                    **_policy().config.model_dump(),
                    "surprise": True,
                },
            }
        )


def test_invalid_policy_result_leaves_event_and_summary_unmodified():
    class BrokenPolicy:
        def price(self, **_kwargs):
            return {"source": "configured_public_rate", "amount_usd": 1.0}

    summary = WorkflowUsageSummary()
    context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="broken", workflow_type="pricing"),
        summary,
        WorkflowBudget(),
        notional_pricing_policy=BrokenPolicy(),
    )
    event = WorkflowUsageEvent(
        provider="codex_exec",
        operation="tool",
        cost_class="subscription_notional",
        node="agent",
        model="gpt-test",
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        normalized_usage=_codex_usage(),
    )

    with workflow_usage_scope(context):
        with pytest.raises(ValidationError):
            record_usage_event(event)

    assert summary.events == []
    assert event.notional_pricing is None
    assert event.notional_usd is None
    assert event.run_id is None
    assert event.metadata == {}


def test_policy_cannot_persist_a_false_amount_or_mismatched_rate_source():
    class LyingAmountPolicy:
        def price(self, **_kwargs):
            return NotionalPricingResult(
                source="configured_public_rate",
                amount_usd=99.0,
                catalog_version="lie-v1",
                rate=_policy().config.rates[0],
            )

    summary = WorkflowUsageSummary()
    context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="lie", workflow_type="pricing"),
        summary,
        WorkflowBudget(),
        notional_pricing_policy=LyingAmountPolicy(),
    )
    event = WorkflowUsageEvent(
        provider="codex_exec",
        operation="tool",
        cost_class="subscription_notional",
        node="agent",
        model="gpt-test",
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        normalized_usage=_codex_usage(),
    )
    with workflow_usage_scope(context):
        with pytest.raises(ValueError, match="does not match"):
            record_usage_event(event)
    assert summary.events == []
    assert event.notional_pricing is None
    assert event.notional_usd is None

    with pytest.raises(ValidationError, match="source must match"):
        NotionalPricingResult(
            source="configured_proxy_rate",
            amount_usd=0.000705,
            catalog_version="lie-v1",
            rate=_policy().config.rates[0],
        )


@pytest.mark.parametrize("lie", ["reported", "provider", "model", "suppressed_reported"])
def test_policy_provenance_must_match_the_event_facts(lie):
    event_kwargs = {}

    class LyingPolicy:
        def price(self, **_kwargs):
            if lie == "reported":
                return NotionalPricingResult(
                    source="provider_reported",
                    amount_usd=0.2,
                    catalog_version="provider-reported",
                )
            if lie == "suppressed_reported":
                return NotionalPricingResult(
                    source="unknown",
                    unknown_reason="rate_missing",
                )
            rate = _policy().config.rates[0].model_copy(
                update={
                    "provider": "other" if lie == "provider" else "codex_exec",
                    "model_prefix": "other" if lie == "model" else "gpt-test",
                }
            )
            return NotionalPricingResult(
                source="configured_public_rate",
                amount_usd=0.000705,
                catalog_version="lie-v1",
                rate=rate,
            )

    if lie == "reported":
        event_kwargs["provider_reported_notional_usd"] = 0.1
    elif lie == "suppressed_reported":
        event_kwargs["provider_reported_notional_usd"] = 0.1
    context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="lie-provenance", workflow_type="pricing"),
        WorkflowUsageSummary(),
        WorkflowBudget(),
        notional_pricing_policy=LyingPolicy(),
    )
    event = WorkflowUsageEvent(
        provider="codex_exec",
        operation="tool",
        cost_class="subscription_notional",
        node="agent",
        model="gpt-test",
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        normalized_usage=_codex_usage(),
        **event_kwargs,
    )

    with workflow_usage_scope(context):
        with pytest.raises(ValueError, match="provider-reported|provider and model"):
            record_usage_event(event)

    assert context.summary.events == []
    assert event.notional_pricing is None
    assert event.notional_usd is None


async def test_builder_and_yaml_policies_match_and_concurrent_engines_do_not_leak():
    arrivals = 0
    both_arrived = asyncio.Event()

    async def emit_usage(_context, payload):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=1)
        record_usage_event(
            WorkflowUsageEvent(
                provider="codex_exec",
                operation="tool",
                cost_class="subscription_notional",
                node="priced",
                model="gpt-test",
                input_tokens=120,
                output_tokens=30,
                total_tokens=150,
                normalized_usage=_codex_usage(),
            )
        )
        return payload

    workflow = WorkflowBuilder("priced").step("priced").build()
    low_policy = _policy(input_rate=1.0)
    high_policy = _policy(input_rate=9.0)

    direct = (
        WorkflowEngineBuilder()
        .with_notional_pricing_policy(low_policy)
        .register_capability("priced", emit_usage)
        .register_workflow(workflow)
        .build()
    )
    configured_bundle = load_workflow_config(
        defaults={
            "workflow": {"workflow_type": "priced"},
            "pricing": high_policy.config.model_dump(),
        }
    )
    configured = WorkflowEngine.from_config(configured_bundle)
    configured.register_capability("priced", emit_usage)
    configured.register_workflow(workflow)

    low_result, high_result = await asyncio.gather(
        direct.run("priced", {"side": "low"}),
        configured.run("priced", {"side": "high"}),
    )

    low = low_result.usage.events[0].notional_pricing
    high = high_result.usage.events[0].notional_pricing
    assert low is not None and high is not None
    assert low.catalog_version == high.catalog_version == "test-v1"
    assert low.rate is not None and low.rate.uncached_input_per_1m == 1.0
    assert high.rate is not None and high.rate.uncached_input_per_1m == 9.0
    assert low.amount_usd == pytest.approx(0.000555)
    assert high.amount_usd == pytest.approx(0.001355)


def test_builder_rejects_an_object_without_the_policy_protocol():
    with pytest.raises(TypeError, match="NotionalPricingPolicy"):
        WorkflowEngineBuilder().with_notional_pricing_policy(object())
