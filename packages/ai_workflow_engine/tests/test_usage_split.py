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
from ai_workflow_engine.usage_rendering import format_usage_summary
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
    from ai_workflow_engine.budget import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope
    from ai_workflow_engine.usage import invoke_metered_chat

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


def test_removed_usage_facade_names_fail_loudly():
    """v0.11 clean contract (manifest row M3): the v0.10-era re-export facade is GONE — every
    moved name now raises ImportError from ai_workflow_engine.usage, pointing importers at the
    real owners. invoke_metered_chat/record_image_usage remain usage.py's own entry points."""

    import importlib

    module = importlib.import_module("ai_workflow_engine.usage")
    for name in (
        "WorkflowBudget", "WorkflowBudgetExceeded", "WorkflowUsageContext",
        "workflow_usage_scope", "current_usage_context", "budget_from_limits",
        "check_images_per_call", "UsageSink", "InMemoryUsageSink", "JsonlUsageSink",
        "AsyncQueueUsageSink", "TeeUsageSink", "format_usage_summary",
    ):
        assert not hasattr(module, name), (
            f"ai_workflow_engine.usage still exposes removed facade name {name!r}"
        )
    assert hasattr(module, "invoke_metered_chat") and hasattr(module, "record_image_usage")


# ======================================================================================
# C2 — invocation-local usage capture (observation-only; canonical path unchanged)
# ======================================================================================


def _capture_event(model: str = "m", tokens: int = 1) -> "WorkflowUsageEvent":
    from ai_workflow_engine.models import WorkflowUsageEvent

    return WorkflowUsageEvent(node="n", operation="chat", model=model, total_tokens=tokens)


def test_capture_scope_sees_each_event_once_and_nested_scopes_are_inclusive():
    from ai_workflow_engine.usage_events import capture_usage_events, record_usage_event

    with capture_usage_events() as outer:
        record_usage_event(_capture_event("outer-1"))
        with capture_usage_events() as inner:
            record_usage_event(_capture_event("nested"))
        record_usage_event(_capture_event("outer-2"))

    assert [e.model for e in inner] == ["nested"], "inner sees exactly its own window"
    assert [e.model for e in outer] == ["outer-1", "nested", "outer-2"], (
        "outer capture is INCLUSIVE of nested work and sees each event exactly once"
    )


def test_capture_reset_survives_exceptions_and_no_capture_is_a_no_op():
    from ai_workflow_engine.usage_events import (
        _ACTIVE_USAGE_CAPTURES,
        capture_usage_events,
        record_usage_event,
    )

    assert _ACTIVE_USAGE_CAPTURES.get() == ()
    with pytest.raises(RuntimeError):
        with capture_usage_events():
            raise RuntimeError("boom")
    assert _ACTIVE_USAGE_CAPTURES.get() == (), "exception path must restore the stack"

    # no active capture: recording is exactly the pre-C2 path (no error, nothing retained)
    record_usage_event(_capture_event("uncaptured"))
    assert _ACTIVE_USAGE_CAPTURES.get() == ()


def test_capture_works_without_an_active_usage_context_and_with_one():
    """Events on BOTH record_usage_event branches (with/without WorkflowUsageContext) reach
    an active capture — and the canonical summary still counts each exactly once."""

    from ai_workflow_engine.budget import (
        WorkflowBudget,
        WorkflowUsageContext,
        workflow_usage_scope,
    )
    from ai_workflow_engine.models import WorkflowRunContext, WorkflowUsageSummary
    from ai_workflow_engine.usage_events import capture_usage_events, record_usage_event

    # branch 1: no ambient usage context (log-only path)
    with capture_usage_events() as bucket:
        record_usage_event(_capture_event("contextless"))
    assert [e.model for e in bucket] == ["contextless"]

    # branch 2: full context path — capture AND canonical aggregation, each once
    summary = WorkflowUsageSummary()
    context = WorkflowUsageContext(
        run_context=WorkflowRunContext(workflow_id="cap-run", workflow_type="cap"),
        summary=summary,
        budget=WorkflowBudget(),
    )
    with workflow_usage_scope(context):
        with capture_usage_events() as bucket_two:
            record_usage_event(_capture_event("contextful", tokens=5))
    assert [e.model for e in bucket_two] == ["contextful"]
    assert len(summary.events) == 1 and summary.total_tokens == 5, (
        "capture observes; canonical accounting still records exactly once"
    )
    assert bucket_two[0] is summary.events[0], (
        "the capture and the persisted ledger must reference the SAME semantic event"
    )


async def test_sibling_tasks_never_see_each_others_captured_events():
    import asyncio

    from ai_workflow_engine.usage_events import capture_usage_events, record_usage_event

    first_emitted = asyncio.Event()

    async def sibling(model: str, wait_first: bool) -> list:
        with capture_usage_events() as bucket:
            if wait_first:
                await asyncio.wait_for(first_emitted.wait(), timeout=10)
            record_usage_event(_capture_event(model))
            if not wait_first:
                first_emitted.set()
                await asyncio.sleep(0)  # yield so the sibling interleaves inside our scope
        return [e.model for e in bucket]

    seen_a, seen_b = await asyncio.gather(sibling("task-a", False), sibling("task-b", True))
    assert seen_a == ["task-a"] and seen_b == ["task-b"], (
        f"ContextVar task isolation must hold under interleaving: {seen_a} / {seen_b}"
    )
