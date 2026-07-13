"""Pure execution-window resolver — table tests (v0.10 Phase 2.1).

Deterministic, no sleeps: the resolver receives explicit remaining-time numbers. Locks the
precedence (minimum hard wins), named limiting sources + clamps, reserve/soft arithmetic, and
loud rejection of impossible windows and non-finite inputs.
"""

from __future__ import annotations

import math

import pytest

from ai_workflow_engine.execution_window import (
    ExecutionWindowInputs,
    TaskExecutionRequest,
    resolve_execution_window,
)

pytestmark = pytest.mark.unit


def _resolve(**kw):
    request = None
    if "timeout_s" in kw or "reserve" in kw or "source" in kw:
        request = TaskExecutionRequest(
            timeout_s=kw.pop("timeout_s", None),
            completion_reserve_s=kw.pop("reserve", 0.0),
            source=kw.pop("source", "planner_task"),
        )
    return resolve_execution_window(
        ExecutionWindowInputs(request=request, **kw),
        enforcement=kw.pop("enforcement", "none") if False else "none",
    )


def test_no_bounds_is_an_unbounded_window():
    d = resolve_execution_window(ExecutionWindowInputs())
    assert d.hard_timeout_s is None and d.soft_timeout_s is None
    assert not d.is_bounded
    assert d.limiting_sources == [] and d.clamps == []


@pytest.mark.parametrize(
    "kwargs, expected_hard, expected_sources, expected_clamps",
    [
        # only a task request → task_request bounds it, no clamp
        ({"timeout_s": 10.0}, 10.0, ["task_request"], []),
        # capability tighter than request → capability wins + clamp
        ({"timeout_s": 10.0, "capability_timeout_s": 4.0}, 4.0, ["capability_limit"], ["capability"]),
        # run-remaining is the tightest → run_limit + run_remaining clamp
        (
            {"timeout_s": 10.0, "capability_timeout_s": 8.0, "run_remaining_s": 3.0},
            3.0,
            ["run_limit"],
            ["capability", "run_remaining"],
        ),
        # parent soft-remaining tightest → parent clamp
        (
            {"timeout_s": 10.0, "parent_soft_remaining_s": 2.0},
            2.0,
            ["parent_window"],
            ["parent_soft_remaining"],
        ),
        # no request, only a run limit → it bounds and clamps
        ({"run_remaining_s": 5.0}, 5.0, ["run_limit"], ["run_remaining"]),
        # F5: capability(3) already tightest; a LOOSER run(8) must NOT be named a clamp
        (
            {"timeout_s": 10.0, "capability_timeout_s": 3.0, "run_remaining_s": 8.0},
            3.0,
            ["capability_limit"],
            ["capability"],
        ),
    ],
)
def test_minimum_hard_wins_and_every_bound_is_named(kwargs, expected_hard, expected_sources, expected_clamps):
    d = _resolve(**kwargs)
    assert d.hard_timeout_s == expected_hard
    assert sorted(d.limiting_sources) == sorted(expected_sources)
    assert sorted(d.clamps) == sorted(expected_clamps)
    assert d.soft_timeout_s == expected_hard  # reserve 0 → soft == hard


def test_tie_at_the_minimum_names_all_limiting_sources():
    d = _resolve(timeout_s=5.0, capability_timeout_s=5.0, run_remaining_s=5.0)
    assert d.hard_timeout_s == 5.0
    assert sorted(d.limiting_sources) == ["capability_limit", "run_limit", "task_request"]


def test_completion_reserve_carves_soft_below_hard():
    d = _resolve(timeout_s=10.0, reserve=2.0)
    assert d.hard_timeout_s == 10.0
    assert d.soft_timeout_s == 8.0
    assert d.completion_reserve_s == 2.0


def test_reserve_meeting_or_exceeding_hard_is_an_impossible_window():
    with pytest.raises(ValueError, match="impossible execution window"):
        _resolve(timeout_s=3.0, reserve=3.0)
    with pytest.raises(ValueError, match="impossible execution window"):
        _resolve(timeout_s=3.0, capability_timeout_s=2.0, reserve=2.5)  # clamps to 2, reserve 2.5


def test_reserve_without_any_bound_is_rejected():
    with pytest.raises(ValueError, match="only meaningful inside a finite window"):
        _resolve(reserve=1.0)


def test_non_finite_and_non_positive_inputs_fail_loudly():
    with pytest.raises(ValueError):
        TaskExecutionRequest(timeout_s=0.0)
    with pytest.raises(ValueError):
        TaskExecutionRequest(timeout_s=math.inf)
    with pytest.raises(ValueError):
        TaskExecutionRequest(timeout_s=-1.0)
    with pytest.raises(ValueError):
        TaskExecutionRequest(completion_reserve_s=math.nan)
    with pytest.raises(ValueError):
        TaskExecutionRequest(source="   ")
    with pytest.raises(ValueError):
        ExecutionWindowInputs(run_remaining_s=math.inf)
    with pytest.raises(ValueError):
        ExecutionWindowInputs(capability_timeout_s=-0.5)


def test_decision_is_serializable_and_deterministic():
    d = _resolve(timeout_s=10.0, capability_timeout_s=6.0, run_remaining_s=9.0, reserve=1.0)
    again = _resolve(timeout_s=10.0, capability_timeout_s=6.0, run_remaining_s=9.0, reserve=1.0)
    assert d.model_dump() == again.model_dump()
    assert d.hard_timeout_s == 6.0 and d.soft_timeout_s == 5.0
    assert d.request_source == "planner_task"
    # round-trips through JSON without a monotonic timestamp or process object
    from ai_workflow_engine.execution_window import ExecutionWindowDecision

    assert ExecutionWindowDecision.model_validate_json(d.model_dump_json()).hard_timeout_s == 6.0


def test_exhausted_run_is_a_typed_zero_window_not_an_invalid_decision():
    from ai_workflow_engine.execution_window import ExecutionWindowDecision

    exhausted = resolve_execution_window(ExecutionWindowInputs(run_remaining_s=0.0))

    assert exhausted.is_bounded and exhausted.is_exhausted
    assert exhausted.hard_timeout_s == exhausted.soft_timeout_s == 0
    assert exhausted.limiting_sources == ["run_limit"]
    assert exhausted.clamps == ["run_remaining"]
    assert ExecutionWindowDecision.model_validate_json(exhausted.model_dump_json()) == exhausted

    with pytest.raises(ValueError, match="must come from exhausted run/parent"):
        ExecutionWindowDecision(
            capability_timeout_s=0.0,
            hard_timeout_s=0.0,
            soft_timeout_s=0.0,
            limiting_sources=["capability_limit"],
            clamps=["capability"],
        )


def test_decision_model_seals_lying_windows():
    """F5: ExecutionWindowDecision cannot be CONSTRUCTED in a lying shape — a bounded window
    with no limiting source, a soft that doesn't match hard-minus-reserve, or a bounded label
    without a hard limit are all rejected at model validation."""

    from ai_workflow_engine.execution_window import ExecutionWindowDecision

    with pytest.raises(ValueError, match="limiting_sources must exactly"):
        ExecutionWindowDecision(run_remaining_s=5.0, hard_timeout_s=5.0, soft_timeout_s=5.0)
    with pytest.raises(ValueError, match="soft_timeout_s must equal"):
        ExecutionWindowDecision(
            run_remaining_s=5.0,
            hard_timeout_s=5.0,
            soft_timeout_s=1.0,
            limiting_sources=["run_limit"],
            clamps=["run_remaining"],
        )
    with pytest.raises(ValueError, match="unbounded window must carry no"):
        ExecutionWindowDecision(soft_timeout_s=3.0)
    # a truthful bounded decision constructs fine
    ok = ExecutionWindowDecision(
        run_remaining_s=5.0, hard_timeout_s=5.0, soft_timeout_s=4.0, completion_reserve_s=1.0,
        limiting_sources=["run_limit"], clamps=["run_remaining"],
    )
    assert ok.is_bounded


def test_directly_constructed_decision_cannot_lie_about_durations_or_ordering():
    """Codex Phase-2.1 recheck: the persisted/enforced decision seals ALL durations
    (finite, non-negative) and the full ordering (0 <= reserve < hard, 0 <= soft <= hard),
    not just soft == hard - reserve."""

    from ai_workflow_engine.execution_window import ExecutionWindowDecision

    # soft > hard via a negative reserve whose arithmetic "matches"
    with pytest.raises(ValueError):
        ExecutionWindowDecision(
            run_remaining_s=5.0, hard_timeout_s=5.0,
            completion_reserve_s=-1.0, soft_timeout_s=6.0,
            limiting_sources=["run_limit"], clamps=["run_remaining"],
        )
    # a negative source duration
    with pytest.raises(ValueError):
        ExecutionWindowDecision(
            hard_timeout_s=5.0, soft_timeout_s=5.0, limiting_sources=["run_limit"],
            run_remaining_s=-3.0,
        )
    # reserve == hard leaves no work time
    with pytest.raises(ValueError):
        ExecutionWindowDecision(
            run_remaining_s=5.0, hard_timeout_s=5.0,
            completion_reserve_s=5.0, soft_timeout_s=0.0,
            limiting_sources=["run_limit"], clamps=["run_remaining"],
        )


def test_decision_recomputes_bound_provenance_instead_of_trusting_claims():
    from ai_workflow_engine.execution_window import ExecutionWindowDecision

    with pytest.raises(ValueError, match="proposed execution bound requires"):
        ExecutionWindowDecision(requested_timeout_s=5.0)
    with pytest.raises(ValueError, match="unbounded window must carry no"):
        ExecutionWindowDecision(completion_reserve_s=1.0)
    with pytest.raises(ValueError, match="limiting_sources must exactly"):
        ExecutionWindowDecision(
            run_remaining_s=5.0,
            hard_timeout_s=5.0,
            soft_timeout_s=5.0,
            limiting_sources=["capability_limit"],
            clamps=["run_remaining"],
        )
    with pytest.raises(ValueError, match="clamps must exactly"):
        ExecutionWindowDecision(
            requested_timeout_s=10.0,
            capability_timeout_s=5.0,
            hard_timeout_s=5.0,
            soft_timeout_s=5.0,
            limiting_sources=["capability_limit"],
            clamps=[],
        )


def test_request_source_label_is_bounded_strict_and_byte_safe():
    with pytest.raises(ValueError):
        TaskExecutionRequest(source="x" * 200)      # over 128
    with pytest.raises(ValueError):
        TaskExecutionRequest(source="bad\x00label")  # control char
    with pytest.raises(ValueError):
        TaskExecutionRequest(source=123)             # non-str (strict)
    assert TaskExecutionRequest(source="  llm_planner  ").source == "llm_planner"

    from ai_workflow_engine.execution_window import ExecutionWindowDecision

    with pytest.raises(ValueError):
        ExecutionWindowDecision(request_source="bad\x00label")
    with pytest.raises(ValueError):
        ExecutionWindowDecision(request_source="x" * 129)
    with pytest.raises(ValueError):
        ExecutionWindowDecision(request_source=b"planner")


def test_invocation_bound_narrows_only_and_carves_represented_cleanup_headroom():
    """5R: the ONE invocation-bound surface for process-backed doors — an explicit product
    timeout narrows the engine bound, never widens it; a reserveless window carves visible
    cleanup headroom so terminate/kill/reap always completes BEFORE the hard deadline."""

    from ai_workflow_engine.execution_window import (
        PROCESS_MIN_CLEANUP_HEADROOM_S,
        resolve_invocation_bound,
    )

    # explicit narrows an ample engine bound
    b = resolve_invocation_bound(engine_soft_s=100.0, engine_hard_s=100.0, explicit_timeout_s=5.0)
    assert b.timeout_s == 5.0 and b.source == "explicit" and b.headroom_s == 0.0

    # explicit can NEVER widen: engine window wins; the reserve hosts the kill grace
    b = resolve_invocation_bound(engine_soft_s=8.0, engine_hard_s=10.0, explicit_timeout_s=100.0)
    assert b.timeout_s == 8.0 and b.source == "engine_window"
    assert b.timeout_s + b.kill_grace_s <= 10.0

    # reserveless window (soft == hard): represented headroom carved from work
    b = resolve_invocation_bound(engine_soft_s=5.0, engine_hard_s=5.0)
    assert b.timeout_s == pytest.approx(5.0 - PROCESS_MIN_CLEANUP_HEADROOM_S)
    assert b.headroom_s == PROCESS_MIN_CLEANUP_HEADROOM_S
    assert b.timeout_s + b.kill_grace_s <= 5.0

    # tiny windows stay usable: headroom scales to a quarter of the hard bound
    b = resolve_invocation_bound(engine_soft_s=1.2, engine_hard_s=1.2)
    assert b.headroom_s == pytest.approx(0.3) and b.timeout_s == pytest.approx(0.9)
    assert b.timeout_s + b.kill_grace_s <= 1.2

    # ambient-only (no hard in sight): the owner's reserve hosts cleanup; no carve
    b = resolve_invocation_bound(engine_soft_s=7.0, explicit_timeout_s=240.0)
    assert b.timeout_s == 7.0 and b.headroom_s == 0.0 and b.source == "engine_window"

    # no bound anywhere: unbounded unless the caller REQUIRES one (loud, no hidden default)
    assert resolve_invocation_bound(engine_soft_s=None).timeout_s is None
    required = resolve_invocation_bound(engine_soft_s=None, require_bound=True, owner="cli_agent 'x'")
    assert required.error is not None and "no execution bound" in required.error

    # non-finite inputs are rejected, never silently trusted
    assert resolve_invocation_bound(engine_soft_s=math.inf).error is not None
    assert resolve_invocation_bound(engine_soft_s=5.0, explicit_timeout_s=-1.0).error is not None
