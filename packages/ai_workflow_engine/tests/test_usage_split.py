"""H3 focused tests: the split usage modules keep their invariants independently."""

from types import SimpleNamespace

import pytest

from ai_workflow_engine.budget import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
    WorkflowUsageContext,
    check_budget_before_call,
    workflow_usage_scope,
)
from ai_workflow_engine.models import (
    WorkflowRunContext,
    WorkflowUsageEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.pricing import estimate_cost_usd
from ai_workflow_engine.provider_usage import _usage_event_from_chat_output
from ai_workflow_engine.token_estimation import estimate_text_tokens
from ai_workflow_engine.usage import format_usage_summary  # facade import stays valid
from ai_workflow_engine.usage_rendering import _event_display_cost

pytestmark = pytest.mark.unit


def _ctx(budget: WorkflowBudget) -> WorkflowUsageContext:
    return WorkflowUsageContext(
        run_context=WorkflowRunContext(workflow_id="r1", workflow_type="t"),
        summary=WorkflowUsageSummary(),
        budget=budget,
    )


def test_precall_budget_denies_before_the_call():
    context = _ctx(WorkflowBudget(max_text_calls=0))
    with workflow_usage_scope(context):
        with pytest.raises(WorkflowBudgetExceeded):
            check_budget_before_call("chat", "planner")


def test_provider_metadata_primary_shape_usage_metadata():
    output = SimpleNamespace(
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_token_details": {"cache_read": 80},
        },
        response_metadata={"model_name": "gpt-5.4-mini", "id": "req-1"},
    )
    event = _usage_event_from_chat_output(
        output, node="n", model="fallback", attempt=1, elapsed_ms=5, metadata=None
    )
    assert event.model == "gpt-5.4-mini"  # provider name wins over the caller fallback
    assert (event.input_tokens, event.output_tokens, event.total_tokens) == (100, 20, 120)
    assert event.input_token_details == {"cache_read": 80}
    assert event.request_id == "req-1"
    assert event.estimated_usd is not None  # metered → priced


def test_provider_metadata_fallback_shape_token_usage():
    output = SimpleNamespace(
        usage_metadata=None,
        response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
    )
    event = _usage_event_from_chat_output(
        output, node="n", model="m", attempt=1, elapsed_ms=5, metadata=None
    )
    assert (event.input_tokens, event.output_tokens, event.total_tokens) == (10, 4, 14)


def test_subscription_notional_is_never_priced_as_metered():
    output = SimpleNamespace(usage_metadata={"input_tokens": 5, "output_tokens": 5}, response_metadata={})
    event = _usage_event_from_chat_output(
        output,
        node="n",
        model="gpt-5.4",  # priced model — must still NOT get metered USD
        attempt=1,
        elapsed_ms=5,
        metadata=None,
        cost_class="subscription_notional",
        notional_usd=0.5,
    )
    assert event.estimated_usd is None
    assert event.notional_usd == 0.5
    assert _event_display_cost(event) == 0.5  # display shows notional, not phantom metered


def test_cached_tokens_are_priced_at_the_cached_rate():
    full = estimate_cost_usd("gpt-5.4", "chat", 1_000_000, 0)
    cached = estimate_cost_usd(
        "gpt-5.4", "chat", 1_000_000, 0, input_details={"cache_read": 1_000_000}
    )
    assert full == 2.5 and cached == 0.25  # 10x cached discount from the price table


def test_image_and_text_token_estimation_shapes():
    assert estimate_text_tokens("abcd" * 10) == 10  # ~4 chars/token
    assert estimate_text_tokens({"type": "image_url", "image_url": {"url": "data:..."}}) == 0
    assert estimate_text_tokens(["ab", {"text": "cdef"}]) >= 2


def test_summary_formatting_separates_billed_from_subscription_value():
    """The caption must never let plan value read as real spend (owner req 2026-07-03)."""

    metered = WorkflowUsageSummary()
    metered.add_event(WorkflowUsageEvent(node="a", model="m", estimated_usd=0.01, total_tokens=10))
    rendered = format_usage_summary(metered)
    assert "billed (API): $0.0100" in rendered
    assert "subscription" not in rendered  # no flat-rate usage ≠ unknown flat-rate cost

    mixed = WorkflowUsageSummary()
    mixed.add_event(WorkflowUsageEvent(node="a", model="m", estimated_usd=0.01))
    mixed.add_event(
        WorkflowUsageEvent(node="b", model="m", cost_class="subscription_notional", notional_usd=0.2)
    )
    rendered = format_usage_summary(mixed)
    assert "billed (API): $0.0100" in rendered
    assert "subscription: ~$0.2000 plan value, no extra charge" in rendered
    assert "~$0.2000" in rendered.splitlines()[3]  # subscription ROW carries the ~ marker
    # GATE-A money-honesty rider (v0.9): the two cost classes are NEVER summed into one number.
    assert "0.2100" not in rendered and "$0.21" not in rendered


def test_provider_detail_dump_failure_warns_instead_of_vanishing(caplog):
    """Task 1.4: a provider usage payload whose model_dump() raises must degrade LOUDLY —
    token details may drop, but never silently (cost-honesty)."""

    import logging

    from ai_workflow_engine.usage_support import _model_or_dict

    class ExplodingUsage:
        def model_dump(self):
            raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger="ai_workflow_engine.usage_support"):
        assert _model_or_dict(ExplodingUsage()) == {}

    assert any("ExplodingUsage" in record.getMessage() for record in caplog.records), (
        "the dropped payload type must be named in a warning"
    )


def test_summary_formatting_pure_subscription_run_shows_true_zero_billed():
    subscription = WorkflowUsageSummary()
    subscription.add_event(
        WorkflowUsageEvent(node="q", model="claude-p", cost_class="subscription_notional", notional_usd=0.05)
    )
    rendered = format_usage_summary(subscription)
    assert "billed (API): $0" in rendered  # NO metered events -> genuinely nothing billed
    assert "plan value, no extra charge" in rendered


def test_summary_formatting_unknown_subscription_value_says_plan_covered():
    unknown = WorkflowUsageSummary()
    unknown.add_event(
        WorkflowUsageEvent(node="img", model="chatgpt-web", cost_class="subscription_notional")
    )
    rendered = format_usage_summary(unknown)
    assert "plan-covered (value unknown)" in rendered
    assert "incl." in rendered  # row shows included-in-plan, never a scary '?'
    assert "billed (API): $0" in rendered


def test_failed_call_usage_event_keeps_provider_and_cost_class():
    """Codex ship-review finding: the invoke_metered_chat error path dropped provider —
    failed non-OpenAI/subscription calls were mislabeled as metered OpenAI."""

    from ai_workflow_engine.models import WorkflowRunContext
    from ai_workflow_engine.usage import (
        WorkflowBudget,
        WorkflowUsageContext,
        invoke_metered_chat,
        workflow_usage_scope,
    )

    class ExplodingLLM:
        def invoke(self, messages):
            raise RuntimeError("browser down")

    summary = WorkflowUsageSummary()
    scope = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf-err", workflow_type="t"),
        summary,
        WorkflowBudget(),
    )
    with workflow_usage_scope(scope):
        with pytest.raises(RuntimeError, match="browser down"):
            invoke_metered_chat(
                ExplodingLLM(),
                [],
                node="render",
                model="chatgpt-web",
                cost_class="subscription_notional",
                provider="chatgpt_browser",
            )

    event = summary.events[0]
    assert event.success is False
    assert event.provider == "chatgpt_browser"
    assert event.cost_class == "subscription_notional"
    assert event.estimated_usd is None
