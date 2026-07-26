"""v0.10 runtime-truth contracts (MageQA E0): semantic reproducers written RED first.

Each test names one confirmed defect in engine-v0.9.2 and fails on the pre-fix code for
that semantic reason (Phase 0 of the v0.10 plan). They become the permanent locks as the
fixes land — do not weaken an assertion to green a phase.
"""

from __future__ import annotations

import asyncio

import pytest

from ai_workflow_engine import (
    CapabilityResult,
    CapabilitySpec,
    WorkflowArtifact,
    PlanArtifact,
    PlanTask,
    WorkflowBuilder,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.models import (
    RuntimeLimits,
    SafetyPolicy,
    WorkflowProfile,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _profile(workflow_type: str, *, timeout_s: float | None = None) -> WorkflowProfile:
    return WorkflowProfile(
        workflow_type=workflow_type,
        limits=RuntimeLimits(timeout_s=timeout_s),
        safety=SafetyPolicy(allowed_side_effects=[]),
    )


async def test_partial_planned_task_is_terminal_truth_not_done():
    """Defect 1: a planned capability returning ``partial`` must stay a PARTIAL task with
    its error and evidence preserved — never be rewritten to ``done`` with error cleared,
    and a plan whose only outcome is partial must not read as green."""

    def planner(_ctx, _payload):
        return PlanArtifact(
            goal="audit pages",
            tasks=[
                PlanTask(task_id="ok", description="full page", capability="full", payload=1),
                PlanTask(task_id="cut", description="times out", capability="cut_short", payload=2),
            ],
        )

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("planned_partial"))
        .register_capability("planner", planner, kind="llm")
        .register_capability("full", lambda _c, p: {"audited": p}, kind="deterministic")
        .register_capability(
            "cut_short",
            lambda _c, _p: CapabilityResult(
                status="partial",
                output={"pages_done": 3},
                error="incomplete: 3 of 10 pages audited",
                artifacts=[WorkflowArtifact(path="artifacts/page3.png", artifact_id="frame-3")],
                metadata={"pages_done": 3, "pages_total": 10},
            ),
            kind="deterministic",
        )
        .register_workflow(
            WorkflowBuilder("planned_partial").plan("audit", capability="planner").build()
        )
        .build()
    )

    result = await engine.run("planned_partial", {})

    tasks = {task.task_id: task for task in result.output.tasks}
    assert tasks["cut"].status == "partial", (
        "a partial capability result must stay a PARTIAL task — v0.9.2 rewrote it to done"
    )
    assert tasks["cut"].error == "incomplete: 3 of 10 pages audited", (
        "the partial error is evidence and must be preserved — v0.9.2 cleared it to None"
    )
    assert tasks["cut"].output_ref, "partial output is salvaged evidence and must stay referenced"
    # real evidence survives onto the task (stable ids + byte-safe metadata, no raw bytes)
    assert tasks["cut"].artifact_refs == ["frame-3"]
    assert tasks["cut"].result_metadata == {"pages_done": 3, "pages_total": 10}
    assert tasks["ok"].status == "done"
    assert result.status == "partial", "a plan containing partial work must not read completed"
    assert result.node("audit").status == "partial"
    assert any(event.decision == "plan:task_partial" for event in result.trace), (
        "trace must distinguish partial from done/failed"
    )
    # the whole artifact round-trips through JSON with its evidence intact (persistable truth)
    from ai_workflow_engine import PlanArtifact as _PA

    restored = _PA.model_validate_json(result.output.model_dump_json())
    rt = {t.task_id: t for t in restored.tasks}["cut"]
    assert rt.status == "partial" and rt.artifact_refs == ["frame-3"]
    assert rt.result_metadata == {"pages_done": 3, "pages_total": 10}


async def test_partial_task_metadata_that_leaks_bytes_fails_loudly():
    """F4: task result_metadata is PERSISTED into plan state — raw bytes must fail the
    byte-free-state guard, never be smuggled into a durable snapshot/bundle."""

    def planner(_ctx, _payload):
        return PlanArtifact(
            goal="leak",
            tasks=[PlanTask(task_id="leaky", description="leaks", capability="leaky", payload=1)],
        )

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("leaky_meta"))
        .register_capability("planner", planner, kind="llm")
        .register_capability(
            "leaky",
            lambda _c, _p: CapabilityResult(status="partial", output={}, metadata={"blob": b"\x89PNG"}),
            kind="deterministic",
        )
        .register_workflow(WorkflowBuilder("leaky_meta").plan("audit", capability="planner").build())
        .build()
    )
    with pytest.raises(Exception) as exc:
        await engine.run("leaky_meta", {})
    assert "byte" in str(exc.value).lower() or "bytes" in str(exc.value).lower()


async def test_run_timeout_actually_bounds_slow_async_work(caplog):
    """Defect 2: ``RuntimeLimits.timeout_s`` is a declared contract — a run whose async
    capability exceeds it must be stopped and reported honestly, not allowed to run to
    completion as if no limit existed."""

    async def slow(_ctx, _payload):
        await asyncio.sleep(1.5)
        return {"finished": "should not have"}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("bounded", timeout_s=0.3))
        .register_capability("slow", slow, kind="deterministic")
        .register_workflow(WorkflowBuilder("bounded").step("slow").build())
        .build()
    )

    result = await engine.run("bounded", {})

    assert result.status != "completed", (
        "RuntimeLimits(timeout_s=0.3) must bound a 1.5s capability — v0.9.2 declared the "
        "limit but never enforced it, so the run completed"
    )
    # the timeout is truthful PARTIAL machine data, not an anonymous failure
    node = result.node("slow")
    assert result.status == "partial"
    assert node is not None and node.status == "partial"
    assert '"outcome": "partial"' in caplog.text


async def test_remaining_finite_run_refuses_uninterruptible_downstream_work_as_typed_partial():
    calls = {"after": 0}

    async def consume(_ctx, _payload):
        await asyncio.sleep(1.0)
        return {"unreachable": True}

    def after(_ctx, _payload):
        calls["after"] += 1
        return {"also": "unreachable"}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("exhausted", timeout_s=0.2))
        .register_capability("consume", consume, kind="deterministic")
        .register_capability("after", after, kind="deterministic")
        .register_workflow(WorkflowBuilder("exhausted").step("consume").step("after").build())
        .build()
    )

    result = await engine.run("exhausted", {})

    assert result.status == "partial"
    assert calls["after"] == 0, "an exhausted run must refuse work before calling its handler"
    after_result = result.node("after")
    assert after_result is not None and after_result.status == "partial"
    terminal = next(
        event
        for event in result.trace
        if event.node == "after" and event.decision == "partial"
    )
    assert terminal.metadata["timeout_reason"] == "execution_window_exceeded"
    remaining_hard = terminal.metadata["execution_window"]["hard_timeout_s"]
    assert 0 <= remaining_hard < 0.06
    assert "run_limit" in terminal.metadata["execution_window"]["limiting_sources"]


async def test_snapshot_active_elapsed_duration_is_strict_finite_and_non_negative():
    from ai_workflow_engine.snapshot import MachineSnapshot

    common = {
        "schema_version": "v0.11", "workflow_id": "w", "suspended_node": "gate",
        "goal": {"workflow_type": "w", "objective": "t", "goal_id": "g-w"},
        "run_context": {"workflow_id": "w-run", "workflow_type": "w", "goal_id": "g-w"},
    }
    assert MachineSnapshot(**common, active_elapsed_s=1.25).active_elapsed_s == 1.25
    for invalid in (-1.0, float("nan"), float("inf"), True, "1.0"):
        with pytest.raises(ValueError):
            MachineSnapshot(**common, active_elapsed_s=invalid)


async def test_timeout_bounds_are_recomputed_from_run_remaining_across_a_capability_limit():
    """Phase 2.2: the run-remaining budget and the capability limit intersect — the tighter
    of the two bounds the work, and a run near its deadline stops sooner than the cap limit."""

    import asyncio

    async def slow(_ctx, _payload):
        await asyncio.sleep(1.0)
        return {"done": True}

    # capability limit is generous (5s) but the run limit is tight (0.3s) → run limit wins
    from ai_workflow_engine import CapabilitySpec

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("bounded_by_run", timeout_s=0.3))
        .register_capability(
            "slow", slow, spec=CapabilitySpec(name="slow", kind="deterministic", timeout_s=5.0)
        )
        .register_workflow(WorkflowBuilder("bounded_by_run").step("slow").build())
        .build()
    )
    result = await engine.run("bounded_by_run", {})
    assert result.status == "partial", "the tighter RUN limit must bound the work, not the 5s cap"


async def test_retraced_capability_receives_typed_provenance():
    """Defect 3: the retraced target must receive typed retrace provenance (round,
    evaluator, source, target) as machine data — not infer control state from criticism
    prose in its payload."""

    seen: list = []

    def draft(ctx, payload):
        seen.append(getattr(ctx, "retrace_provenance", None))
        # improve after criticism so the second evaluation accepts
        return {"draft": "v2" if len(seen) > 1 else "v1"}

    def gate(_ctx, payload):
        drafted = (payload or {}).get("draft") if isinstance(payload, dict) else None
        if drafted == "v1":
            return CapabilityResult(status="rejected", error="too shallow")
        return CapabilityResult(status="accepted", output={"ok": True})

    from ai_workflow_engine import Retrace

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("retraced"))
        .register_capability("draft", draft, kind="llm")
        .register_capability("gate", gate, kind="llm")
        .register_workflow(
            WorkflowBuilder("retraced")
            .step("draft")
            .evaluate("qa", target="draft", evaluator="gate", on_reject=Retrace("draft"))
            .build()
        )
        .build()
    )

    result = await engine.run("retraced", {})
    assert result.status == "completed"
    assert len(seen) == 2, "one original + one retraced invocation expected"
    assert seen[0] is None, "the FIRST invocation is not a retrace and must carry no provenance"
    provenance = seen[1]
    assert provenance is not None, (
        "the retraced invocation must carry typed provenance — v0.9.2 delivered only "
        "criticism prose in the payload"
    )
    assert provenance.round == 1
    assert provenance.source_node == "qa" or provenance.evaluator_node == "qa"
    assert provenance.target_node == "draft"


async def test_retry_route_never_delivers_retrace_provenance():
    """Task 1.3 isolation: a retry/repair round must NOT impersonate a retrace — the
    re-invoked capability sees no retrace provenance (only an actual retrace round does)."""

    from ai_workflow_engine.models import RuntimeLimits
    from ai_workflow_engine import Retry

    seen: list = []

    def draft(ctx, _payload):
        seen.append(getattr(ctx, "retrace_provenance", None))
        return {"draft": "v2" if len(seen) > 1 else "v1"}

    def gate(_ctx, payload):
        d = (payload or {}).get("draft") if isinstance(payload, dict) else None
        return CapabilityResult(status="accepted" if d == "v2" else "rejected", error="shallow")

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("retry_iso"))
        .register_capability("draft", draft, kind="llm")
        .register_capability("gate", gate, kind="llm")
        .register_workflow(
            WorkflowBuilder("retry_iso")
            .step("draft")
            .evaluate("qa", target="draft", evaluator="gate", on_reject=Retry(2))
            .build()
        )
        .build()
    )
    result = await engine.run("retry_iso", {})
    assert result.status == "completed"
    assert len(seen) == 2 and all(p is None for p in seen), (
        "a retry round must never carry retrace provenance"
    )


async def test_terminal_partial_survives_real_engine_resume_snapshot():
    """F3: a real suspend -> snapshot serialization -> engine.resume. The plan's terminal
    partial task and its evidence survive the snapshot round-trip, and the plan capability
    is NOT re-invoked on resume."""

    from ai_workflow_engine import LocalWaitPolicy

    planner_calls = {"n": 0}

    def planner(_ctx, _payload):
        planner_calls["n"] += 1
        return PlanArtifact(
            goal="audit then gate",
            tasks=[PlanTask(task_id="cut", description="partial", capability="cut_short", payload=1)],
        )

    from pydantic import BaseModel as _BM

    class _Gate(_BM):
        status: str
        answer: str = ""

    def ask(context, _payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return _Gate(status="pending")
        return _Gate(status="answered", answer=str(event))

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("resume_partial"))
        .register_capability("planner", planner, kind="llm")
        .register_capability(
            "cut_short",
            lambda _c, _p: CapabilityResult(status="partial", output={"half": 1}, error="cut",
                                            metadata={"pages": 3}),
            kind="deterministic",
        )
        .register_capability("gate", ask, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("resume_partial")
            .plan("audit", capability="planner")
            .human("gate", wait_policy=LocalWaitPolicy())
            .build()
        )
        .build()
    )

    first = await engine.run("resume_partial", {})
    assert first.status == "requires_user_input"
    assert first.snapshot is not None
    assert planner_calls["n"] == 1

    # serialize the snapshot and resume on a FRESH engine instance
    from ai_workflow_engine.snapshot import MachineSnapshot

    wire = first.snapshot.model_dump_json()
    restored_snapshot = MachineSnapshot.model_validate_json(wire)

    engine2 = (
        WorkflowEngineBuilder()
        .with_profile(_profile("resume_partial"))
        .register_capability("planner", planner, kind="llm")
        .register_capability(
            "cut_short",
            lambda _c, _p: CapabilityResult(status="partial", output={"half": 1}, error="cut",
                                            metadata={"pages": 3}),
            kind="deterministic",
        )
        .register_capability("gate", ask, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("resume_partial")
            .plan("audit", capability="planner")
            .human("gate", wait_policy=LocalWaitPolicy())
            .build()
        )
        .build()
    )
    resumed = await engine2.resume(restored_snapshot, "approved")
    # the surviving partial task legitimately keeps the whole run partial after resume —
    # partial truth is preserved across snapshot serialization, not laundered to completed.
    assert resumed.status == "partial"
    assert planner_calls["n"] == 1, "resume must NOT re-invoke the planner (fast-forward)"
    # the plan node was RESTORED, not rerun (fast-forward), and its partial status is what
    # keeps the resumed run partial — so the partial survived the snapshot round-trip.
    assert any(
        e.node == "audit" and e.decision == "machine:fastforward" for e in resumed.trace
    ), "the already-partial plan node must fast-forward (restored, not rerun) on resume"


async def test_replan_keeps_terminal_partial_immutable():
    """F3: an evaluator-driven replan must NOT rerun a terminal partial task — merge keeps
    done/partial/failed immutable and only (re)executes pending work."""

    from ai_workflow_engine import Replan

    cut_calls = {"n": 0}

    def make_plan(_ctx, _payload):
        return PlanArtifact(
            goal="replan-keeps-partial",
            tasks=[PlanTask(task_id="cut", description="partial once", capability="cut_short", payload=1)],
        )

    def cut_short(_c, _p):
        cut_calls["n"] += 1
        return CapabilityResult(status="partial", output={"half": 1}, error="cut")

    # gate rejects once (forcing a replan), then accepts
    gate_calls = {"n": 0}

    def gate(_c, _p):
        gate_calls["n"] += 1
        return CapabilityResult(status="accepted" if gate_calls["n"] > 1 else "rejected", error="retry")

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("replan_partial"))
        .register_capability(
            "make_plan", make_plan,
            spec=CapabilitySpec(name="make_plan", kind="llm", is_planner=True),
        )
        .register_capability("cut_short", cut_short, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_workflow(
            WorkflowBuilder("replan_partial")
            .plan("plan_node", capability="make_plan", max_replans=2)
            .evaluate("qa", target="plan_node", evaluator="gate", on_reject=Replan("plan_node"))
            .build()
        )
        .build()
    )
    result = await engine.run("replan_partial", {})
    assert gate_calls["n"] == 2, "the evaluator must have rejected once then accepted"
    assert any(e.node == "qa" and e.decision == "replan" for e in result.trace), (
        "a replan round must actually have occurred (trace route), or this test proves nothing"
    )
    assert cut_calls["n"] == 1, "a terminal partial task must not re-run on replan"


async def test_retrace_provenance_rounds_increment_across_successive_retraces():
    """F2: successive retraces expose rounds 1 then 2 (retry/replan never increment)."""

    from ai_workflow_engine import Retrace

    rounds_seen: list = []

    def draft(ctx, _payload):
        prov = getattr(ctx, "retrace_provenance", None)
        rounds_seen.append(prov.round if prov else None)
        return {"v": len([r for r in rounds_seen if r]) + 1}

    def gate(_c, payload):
        v = (payload or {}).get("v") if isinstance(payload, dict) else 0
        return CapabilityResult(status="accepted" if v and v >= 3 else "rejected", error="again")

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("round2"))
        .register_capability("draft", draft, kind="llm")
        .register_capability("gate", gate, kind="llm")
        .register_workflow(
            WorkflowBuilder("round2")
            .step("draft")
            .evaluate("qa", target="draft", evaluator="gate", on_reject=Retrace("draft", max_retrace=2))
            .build()
        )
        .build()
    )
    await engine.run("round2", {})
    assert rounds_seen[0] is None
    assert [r for r in rounds_seen if r is not None] == [1, 2], (
        f"successive retraces must expose rounds 1 then 2, got {rounds_seen}"
    )


async def test_retrace_targeting_a_planner_node_delivers_provenance_generically():
    """F2 (codex): the retrace target here is a PLANNER node, not a step — provenance must
    arrive through the generic node boundary, proving no step-only consumption remains."""

    from ai_workflow_engine import Retrace

    planner_provenance: list = []
    downstream_provenance: list = []

    def make_plan(ctx, _payload):
        planner_provenance.append(getattr(ctx, "retrace_provenance", None))
        return PlanArtifact(
            goal="planned draft",
            tasks=[PlanTask(task_id="t1", description="emit", capability="emit", payload=1)],
        )

    def emit(_c, payload):
        return {"quality": payload}

    def after(ctx, payload):
        downstream_provenance.append(getattr(ctx, "retrace_provenance", None))
        return payload

    gate_calls = {"n": 0}

    def gate(_c, _payload):
        # reject the first plan (forcing exactly one retrace of the PLANNER node), accept next
        gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if gate_calls["n"] > 1 else "rejected", error="bad plan"
        )

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("plan_retrace"))
        .register_capability(
            "make_plan", make_plan, spec=CapabilitySpec(name="make_plan", kind="llm", is_planner=True)
        )
        .register_capability("emit", emit, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_capability("after", after, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("plan_retrace")
            .plan("plan_node", capability="make_plan")
            .evaluate("qa", target="plan_node", evaluator="gate", on_reject=Retrace("plan_node"))
            .step("after")
            .build()
        )
        .build()
    )
    result = await engine.run("plan_retrace", {})
    assert len(planner_provenance) == 2, "planner must run original + retraced invocation"
    assert planner_provenance[0] is None
    prov = planner_provenance[1]
    assert prov is not None, "a PLANNER retrace target must receive typed provenance (generic boundary)"
    assert prov.round == 1
    assert prov.evaluator_node == "qa"
    assert prov.target_node == "plan_node"
    assert downstream_provenance == [None], (
        "retrace provenance belongs to the target node invocation only and must be cleared "
        "before the next node"
    )
    assert result.status == "completed"


async def test_planner_retrace_preserves_prior_and_follow_up_task_outputs(tmp_path):
    """GAP-1: a deepening round adds evidence; it must not erase the first round."""

    import json
    from pathlib import Path

    from ai_workflow_engine import ObservationConfig, Retrace
    from ai_workflow_engine.models import (
        CriticismEnvelope,
        EvaluationDecision,
        WorkflowUsageEvent,
    )
    from ai_workflow_engine.usage_events import record_usage_event

    def planner(context, _payload):
        provenance = getattr(context, "retrace_provenance", None)
        round_no = int(getattr(provenance, "round", 0) or 0)
        task_id = "initial" if round_no == 0 else "follow_up"
        return PlanArtifact(
            goal="retain evidence across deepening",
            tasks=[
                PlanTask(
                    task_id=task_id,
                    description=task_id,
                    capability="observe",
                    payload={"value": task_id},
                )
            ],
        )

    def coverage(_context, plan):
        if plan.revision == 0:
            return EvaluationDecision(
                action="retrace_to",
                retrace_to="plan",
                rationale="one gap remains",
                criticism=CriticismEnvelope(
                    observed="follow-up missing",
                    expected="run one follow-up",
                    target_capability="observe",
                ),
            )
        return EvaluationDecision(action="accept", rationale="both rounds present")

    def observe(_context, payload):
        marker = payload["value"]
        record_usage_event(
            WorkflowUsageEvent(
                provider="fixture",
                operation="tool",
                node="observe",
                model=marker,
                total_tokens=1,
                estimated_usd=0.001,
                metadata={"round_marker": marker},
            )
        )
        return payload

    engine = (
        WorkflowEngineBuilder()
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(tmp_path)))
        .register_capability("planner", planner, kind="llm")
        .register_capability("observe", observe, kind="deterministic")
        .register_capability("coverage", coverage, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("retrace_outputs")
            .plan("plan", capability="planner", execution="fanout")
            .evaluate(
                "coverage",
                target="plan",
                evaluator="coverage",
                on_reject=Retrace("plan", max_retrace=1),
            )
            .build()
        )
        .build()
    )

    result = await engine.run("retrace_outputs", {})

    assert result.status == "completed"
    plan = result.node("plan").output
    assert [task.task_id for task in plan.tasks] == ["initial", "follow_up"]
    assert plan.metadata["task_outputs"] == {
        "plan.initial": {"value": "initial"},
        "plan.follow_up": {"value": "follow_up"},
    }
    terminal_tasks = [
        event.metadata["task_id"]
        for event in result.trace
        if event.decision == "plan:task_done"
    ]
    assert terminal_tasks == ["initial", "follow_up"]
    assert [event.metadata["round_marker"] for event in result.usage.events] == [
        "initial",
        "follow_up",
    ]

    bundle = Path(result.observation_bundle_path)
    trace_rows = [json.loads(line) for line in (bundle / "trace.jsonl").read_text().splitlines()]
    usage_rows = [json.loads(line) for line in (bundle / "usage.jsonl").read_text().splitlines()]
    assert [
        row["metadata"]["task_id"]
        for row in trace_rows
        if row.get("decision") == "plan:task_done"
    ] == ["initial", "follow_up"]
    assert [row["metadata"]["round_marker"] for row in usage_rows] == [
        "initial",
        "follow_up",
    ]


@pytest.mark.parametrize(
    ("follow_up_status", "follow_up_output", "expected_status", "has_follow_up_output"),
    [
        ("accepted", None, "done", False),
        ("failed", None, "failed", False),
        ("partial", {"value": "partial"}, "partial", True),
    ],
)
async def test_planner_retrace_retains_outputs_across_non_successful_follow_up_edges(
    follow_up_status,
    follow_up_output,
    expected_status,
    has_follow_up_output,
):
    """GAP-1 edges: no-output/failure/partial rounds retain prior evidence."""

    from ai_workflow_engine import Retrace
    from ai_workflow_engine.models import EvaluationDecision

    def planner(context, _payload):
        provenance = getattr(context, "retrace_provenance", None)
        is_follow_up = int(getattr(provenance, "round", 0) or 0) > 0
        task_id = "follow_up" if is_follow_up else "initial"
        return PlanArtifact(
            goal="retain edge evidence",
            tasks=[
                PlanTask(
                    task_id=task_id,
                    description=task_id,
                    capability="observe",
                    payload={"round": task_id},
                )
            ],
            metadata={"task_outputs": {"shared": task_id}},
        )

    def observe(_context, payload):
        if payload["round"] == "initial":
            return {"value": "initial"}
        return CapabilityResult(
            status=follow_up_status,
            output=follow_up_output,
            error="follow-up did not complete" if follow_up_status != "accepted" else None,
        )

    gate_calls = {"count": 0}

    def coverage(_context, _plan):
        gate_calls["count"] += 1
        if gate_calls["count"] == 1:
            return EvaluationDecision(
                action="retrace_to",
                retrace_to="plan",
                rationale="deepen once",
            )
        return EvaluationDecision(action="accept", rationale="done")

    engine = (
        WorkflowEngineBuilder()
        .register_capability("planner", planner, kind="llm")
        .register_capability("observe", observe, kind="deterministic")
        .register_capability("coverage", coverage, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("retrace_output_edges")
            .plan("plan", capability="planner")
            .evaluate(
                "coverage",
                target="plan",
                evaluator="coverage",
                on_reject=Retrace("plan", max_retrace=1),
            )
            .build()
        )
        .build()
    )

    result = await engine.run("retrace_output_edges", {})
    plan = result.node("plan").output
    tasks = {task.task_id: task for task in plan.tasks}

    assert tasks["initial"].status == "done"
    assert tasks["follow_up"].status == expected_status
    assert plan.metadata["task_outputs"]["plan.initial"] == {"value": "initial"}
    assert plan.metadata["task_outputs"]["shared"] == "follow_up"
    assert ("plan.follow_up" in plan.metadata["task_outputs"]) is has_follow_up_output
    if has_follow_up_output:
        assert plan.metadata["task_outputs"]["plan.follow_up"] == follow_up_output
        assert tasks["follow_up"].output_ref == "plan.follow_up"
    else:
        assert tasks["follow_up"].output_ref is None


async def test_retrace_provenance_never_leaks_across_barrier_interleaved_runs():
    """F2 (codex): TRUE concurrency isolation — two runs interleave at an asyncio barrier
    while one is mid-retrace; every provenance observation is associated with ITS run's
    marker, and the non-retracing run must observe none."""

    import asyncio as _a

    from ai_workflow_engine import Retrace

    retrace_window_open = _a.Event()
    clean_run_done = _a.Event()
    observations: list[tuple[str, object]] = []  # (run_marker, provenance-or-None)

    async def draft(ctx, payload):
        marker = payload["marker"] if isinstance(payload, dict) else "?"
        prov = getattr(ctx, "retrace_provenance", None)
        observations.append((marker, prov))
        if marker == "retracer" and prov is None:
            return {"quality": "bad", "marker": marker}
        if marker == "retracer" and prov is not None:
            # Hold the SECOND invocation, while the retrace provenance scope is genuinely
            # active. The clean run must complete inside this interval.
            retrace_window_open.set()
            await _a.wait_for(clean_run_done.wait(), timeout=5)
        return {"quality": "good", "marker": marker}

    def gate(_c, payload):
        return CapabilityResult(
            status="accepted" if (payload or {}).get("quality") == "good" else "rejected",
            error="bad",
        )

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("iso"))
        .register_capability("draft", draft, kind="llm")
        .register_capability("gate", gate, kind="llm")
        .register_workflow(
            WorkflowBuilder("iso")
            .step("draft")
            .evaluate("qa", target="draft", evaluator="gate", on_reject=Retrace("draft"))
            .build()
        )
        .build()
    )

    async def clean_run():
        await _a.wait_for(retrace_window_open.wait(), timeout=5)
        try:
            return await engine.run("iso", {"marker": "clean"})
        finally:
            clean_run_done.set()

    retracer, clean = await _a.gather(
        engine.run("iso", {"marker": "retracer"}), clean_run()
    )
    assert retracer.status == "completed" and clean.status == "completed"
    clean_obs = [prov for marker, prov in observations if marker == "clean"]
    retracer_obs = [prov for marker, prov in observations if marker == "retracer"]
    assert clean_obs == [None], (
        f"the clean run executed INSIDE the other run's retrace window and must never see "
        f"provenance, got {clean_obs}"
    )
    assert retracer_obs[0] is None and retracer_obs[1] is not None
    assert retracer_obs[1].target_node == "draft"


async def test_retrace_targeting_fanout_delivers_provenance_to_item_invocations():
    """The generic retrace boundary includes fanout, whose item calls bypass invoke_bound."""

    from ai_workflow_engine import Retrace

    observations: list[tuple[int, object]] = []

    def items(_ctx, _payload):
        return [1, 2]

    def worker(ctx, item):
        provenance = getattr(ctx, "retrace_provenance", None)
        observations.append((item, provenance))
        return {"item": item, "quality": "good" if provenance is not None else "bad"}

    def gate(_ctx, payload):
        accepted = bool(payload) and all(row.get("quality") == "good" for row in payload)
        return CapabilityResult(status="accepted" if accepted else "rejected", error="retry fanout")

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("fanout_retrace"))
        .register_capability("items", items, kind="deterministic")
        .register_capability("worker", worker, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_workflow(
            WorkflowBuilder("fanout_retrace")
            .step("items")
            .fanout("fan", capability="worker", items_key="items", max_parallel=2)
            .evaluate("qa", target="fan", evaluator="gate", on_reject=Retrace("fan"))
            .build()
        )
        .build()
    )

    result = await engine.run("fanout_retrace", {})
    assert result.status == "completed"
    assert len(observations) == 4
    assert all(provenance is None for _, provenance in observations[:2])
    for _, provenance in observations[2:]:
        assert provenance is not None
        assert provenance.round == 1
        assert provenance.target_node == "fan"

def test_tools_wheel_identity_advanced_for_changed_code():
    """Defect 4: ai_workflow_tools source changed between engine-v0.8.1 and engine-v0.9.2
    (cli_agents assembly/console/models) while both tags shipped as 0.3.0 — two different
    wheels must never share name+version. v0.10 advanced to 0.4.0; v0.10.1 changed the tools
    process-I/O surfacing again (0.4.1); the v0.11 latest-only line changed the tools surface
    again, advancing the identity to 0.5.0; native Codex image attachment advances the
    next distinguishable tools wheel to 0.5.1; structured CLI usage advanced it to 0.5.2;
    secured browser consumers advanced it to 0.5.3; the reusable OpenAI-compatible
    provider pack remains available on the corrective 0.6.1 tools surface; 0.6.2 re-pinned the
    engine floor to 0.11.11 because engine-v0.11.10 was never published; 0.6.3 gave the
    clean-checkout release a unique wheel identity; 0.6.4 carries the same provider behavior
    under the corrected source-only release-evidence contract and requires engine 0.11.13."""

    import ai_workflow_tools

    assert ai_workflow_tools.__version__ == "0.6.8", (
        "tools code changed since the last released wheel identity; the version must "
        f"advance (found {ai_workflow_tools.__version__})"
    )


def test_cli_request_has_no_hidden_default_timeout():
    """Defect 5: ``CliAgentRequest.timeout_s`` silently defaulted to 600s, so a missing
    engine window selected a ten-minute bound nobody declared. The default must be None
    (explicit timeout or engine-provided window required downstream)."""

    from ai_workflow_tools.cli_agents.models import CliAgentRequest

    request = CliAgentRequest(prompt="inspect")
    assert request.timeout_s is None, (
        f"no one declared {request.timeout_s}s — a missing window must be explicit, "
        "never a hidden 600s default"
    )


async def test_partial_propagates_through_fanout_nested_roundtrip_and_resume():
    """Task 1.2: one incomplete child can never leave a green parent — across concurrent
    fanout siblings, a nested child plan, JSON round-trip, and resume immutability."""

    import json

    # fanout: accepted/partial/failed siblings stay isolated; parent goes partial
    def planner(_ctx, _payload):
        return PlanArtifact(
            goal="mixed fanout",
            tasks=[
                PlanTask(task_id="a", description="ok", capability="ok_cap", payload=1),
                PlanTask(task_id="b", description="cut", capability="partial_cap", payload=2),
                PlanTask(task_id="c", description="boom", capability="fail_cap", payload=3),
            ],
        )

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("fanout_partial"))
        .register_capability("planner", planner, kind="llm")
        .register_capability("ok_cap", lambda _c, p: {"ok": p}, kind="deterministic")
        .register_capability(
            "partial_cap",
            lambda _c, _p: CapabilityResult(status="partial", output={"half": True}, error="cut short"),
            kind="deterministic",
        )
        .register_capability(
            "fail_cap",
            lambda _c, _p: CapabilityResult(status="failed", error="boom"),
            kind="deterministic",
        )
        .register_workflow(
            WorkflowBuilder("fanout_partial")
            .plan("audit", capability="planner", execution="fanout")
            .build()
        )
        .build()
    )
    result = await engine.run("fanout_partial", {})
    tasks = {t.task_id: t for t in result.output.tasks}
    assert tasks["a"].status == "done"
    assert tasks["b"].status == "partial" and tasks["b"].error == "cut short"
    assert tasks["c"].status == "failed" and tasks["c"].error == "boom"
    assert result.status == "partial"
    assert any(e.decision == "plan:task_partial" and e.metadata["task_id"] == "b" for e in result.trace)

    # JSON round-trip preserves the partial terminal state + evidence fields
    dumped = result.output.model_dump_json()
    from ai_workflow_engine import PlanArtifact as PA

    restored = PA.model_validate_json(dumped)
    assert {t.task_id: t.status for t in restored.tasks} == {"a": "done", "b": "partial", "c": "failed"}
    assert restored.tasks[1].result_metadata == tasks["b"].result_metadata

    # resume from the restored artifact: terminal partial is IMMUTABLE (no rerun)
    calls = {"partial_cap": 0}

    def counting_partial(_c, _p):
        calls["partial_cap"] += 1
        return CapabilityResult(status="partial", output={"half": True}, error="cut short")

    engine2 = (
        WorkflowEngineBuilder()
        .with_profile(_profile("fanout_partial"))
        .register_capability("planner", lambda _c, _p: restored, kind="llm")
        .register_capability("ok_cap", lambda _c, p: {"ok": p}, kind="deterministic")
        .register_capability("partial_cap", counting_partial, kind="deterministic")
        .register_capability("fail_cap", lambda _c, _p: CapabilityResult(status="failed", error="boom"), kind="deterministic")
        .register_workflow(
            WorkflowBuilder("fanout_partial").plan("audit", capability="planner", execution="fanout").build()
        )
        .build()
    )
    result2 = await engine2.run("fanout_partial", {})
    assert calls["partial_cap"] == 0, "terminal partial must NOT rerun on resume from artifact"
    assert {t.task_id: t.status for t in result2.output.tasks}["b"] == "partial"


async def test_nested_child_plan_partial_reaches_the_parent():
    """A partial task inside a CHILD plan must surface as a partial parent task and a
    partial workflow — nesting cannot launder incomplete work into done."""

    def child_planner(_ctx, _payload):
        return PlanArtifact(
            goal="child work",
            tasks=[PlanTask(task_id="deep", description="cut", capability="partial_cap", payload=1)],
        )

    def parent_planner(_ctx, _payload):
        return PlanArtifact(
            goal="parent",
            tasks=[PlanTask(task_id="delegate", description="child plan", capability="child_planner", payload=1)],
        )

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("nested_partial"))
        .register_capability("parent_planner", parent_planner, kind="llm")
        .register_capability(
            "child_planner",
            child_planner,
            spec=CapabilitySpec(name="child_planner", kind="llm", is_planner=True),
        )
        .register_capability(
            "partial_cap",
            lambda _c, _p: CapabilityResult(status="partial", output={"half": True}, error="cut short"),
            kind="deterministic",
        )
        .register_workflow(
            WorkflowBuilder("nested_partial")
            .plan("outer", capability="parent_planner", max_plan_depth=2)
            .build()
        )
        .build()
    )
    result = await engine.run("nested_partial", {})
    outer_tasks = {t.task_id: t for t in result.output.tasks}
    assert outer_tasks["delegate"].status == "partial", (
        "a child plan containing partial work must leave the delegating parent task partial"
    )
    assert result.status == "partial"
    assert any(e.decision == "plan:subplan_partial" for e in result.trace)


async def test_cooperative_clean_cancellation_is_partial():
    """Phase 2.3 (cooperative): an async handler cancelled at the hard boundary that stops
    cleanly (lets CancelledError propagate) is a truthful PARTIAL."""

    import asyncio

    async def slow(_ctx, _payload):
        await asyncio.sleep(2.0)  # never returns; cancelled at the boundary
        return {"unreached": True}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("coop_clean", timeout_s=0.3))
        .register_capability("slow", slow, kind="deterministic")
        .register_workflow(WorkflowBuilder("coop_clean").step("slow").build())
        .build()
    )
    result = await engine.run("coop_clean", {})
    assert result.status == "partial"
    node = next(e for e in result.trace if e.node == "slow" and e.phase == "tool:result")
    assert node.metadata.get("timeout_reason") == "execution_window_exceeded"


async def test_cooperative_cancellation_suppression_is_a_containment_failure():
    """Phase 2.3: a handler that SWALLOWS cancellation and returns anyway must NOT be reported
    as a clean partial — it is a containment FAILURE (unstoppable side effects may have run)."""

    import asyncio

    async def sneaky(_ctx, _payload):
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            return {"pretended_to_stop": True}  # suppress + return
        return {"finished": True}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("coop_suppress", timeout_s=0.3))
        .register_capability("sneaky", sneaky, kind="deterministic")
        .register_workflow(WorkflowBuilder("coop_suppress").step("sneaky").build())
        .build()
    )
    result = await engine.run("coop_suppress", {})
    assert result.status == "failed", "suppressed cancellation is a failure, not a clean partial"
    assert "cancellation" in (result.error or "").lower() or "suppress" in (result.error or "").lower()
    node = next(e for e in result.trace if e.node == "sneaky" and e.phase == "tool:result")
    assert node.metadata["timeout_reason"] == "cancellation_containment_failed"
    assert node.metadata["execution_window"]["enforcement"] == "cooperative"


async def test_inline_capability_with_declared_window_is_rejected_before_running():
    """Phase 2.3 (none): an uninterruptible inline capability that DECLARES a finite window
    (task/spec/run) is refused before the handler runs — the engine never claims a hard stop
    it cannot perform."""

    from ai_workflow_engine import CapabilitySpec

    ran = {"n": 0}

    def inline(_ctx, _payload):  # synchronous → enforcement "none"
        ran["n"] += 1
        return {"ok": True}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("inline_decl"))
        .register_capability(
            "inline", inline, spec=CapabilitySpec(name="inline", kind="deterministic", timeout_s=0.5)
        )
        .register_workflow(WorkflowBuilder("inline_decl").step("inline").build())
        .build()
    )
    result = await engine.run("inline_decl", {})
    assert ran["n"] == 0, "the inline handler with a declared hard window must NOT run"
    assert result.status in ("failed", "partial")
    assert "cannot be interrupted" in (result.error or "") or "process-backed" in (result.error or "")

    run_bounded = (
        WorkflowEngineBuilder()
        .with_profile(_profile("inline_run_bound", timeout_s=2.0))
        .register_capability("inline", inline, kind="deterministic")
        .register_workflow(WorkflowBuilder("inline_run_bound").step("inline").build())
        .build()
    )
    second = await run_bounded.run("inline_run_bound", {})
    assert ran["n"] == 0, "a run-level hard window cannot make inline Python interruptible"
    assert second.status in ("failed", "partial")


async def test_inline_capability_without_declared_window_still_runs():
    """Phase 2.3 degradation: a simple inline capability with NO finite window is unchanged."""

    def inline(_ctx, _payload):
        return {"ok": True}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("inline_plain"))  # no run timeout
        .register_capability("inline", inline, kind="deterministic")
        .register_workflow(WorkflowBuilder("inline_plain").step("inline").build())
        .build()
    )
    result = await engine.run("inline_plain", {})
    assert result.status == "completed" and result.output == {"ok": True}


async def test_normal_bounded_async_work_completes_and_returns_its_result():
    """Phase 2R #1 (the corruption blind spot): a FAST async capability under a run window
    must return its real result and complete — NOT be misread as cancellation suppression."""

    async def quick(_ctx, payload):
        import asyncio
        await asyncio.sleep(0.01)
        return {"echo": payload}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("quick_bounded", timeout_s=2.0))
        .register_capability("quick", quick, kind="deterministic")
        .register_workflow(WorkflowBuilder("quick_bounded").step("quick").build())
        .build()
    )
    result = await engine.run("quick_bounded", {"x": 1})
    assert result.status == "completed"
    assert result.output == {"echo": {"x": 1}}


async def test_bounded_async_that_raises_its_own_error_is_a_normal_failure_not_a_timeout():
    """Phase 2R #1: an ordinary exception inside a bounded async handler surfaces as a normal
    failure, not corrupted into a timeout/containment outcome."""

    async def boom(_ctx, _payload):
        import asyncio
        await asyncio.sleep(0.01)
        raise ValueError("real bug")

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("boom_bounded", timeout_s=2.0))
        .register_capability("boom", boom, kind="deterministic")
        .register_workflow(WorkflowBuilder("boom_bounded").step("boom").build())
        .build()
    )
    result = await engine.run("boom_bounded", {})
    assert result.status == "failed"
    assert "real bug" in (result.error or "")


async def test_async_callable_object_is_bounded_not_escaped_as_none():
    """Phase 2R #4: a capability that is an OBJECT with async __call__ must be classified
    async (cooperative), so a run window actually bounds it — not defaulted to none and escaped."""

    class AsyncWorker:
        async def __call__(self, _ctx, _payload):
            import asyncio
            await asyncio.sleep(2.0)
            return {"unreached": True}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("callable_obj", timeout_s=0.3))
        .register_capability("worker", AsyncWorker(), kind="deterministic")
        .register_workflow(WorkflowBuilder("callable_obj").step("worker").build())
        .build()
    )
    result = await engine.run("callable_obj", {})
    assert result.status == "partial", "an async __call__ object must be run-window-bounded"


async def test_planned_task_execution_request_bounds_the_task():
    """Phase 2R #3: a PlanTask.execution timeout must actually bound that task's capability
    (it reaches the resolver as request=), not be decorative."""

    from ai_workflow_engine.execution_window import TaskExecutionRequest

    def planner(_ctx, _payload):
        return PlanArtifact(
            goal="bounded task",
            tasks=[
                PlanTask(
                    task_id="slow", description="slow task", capability="slow", payload=1,
                    execution=TaskExecutionRequest(timeout_s=0.2, source="llm_planner"),
                )
            ],
        )

    async def slow(_ctx, _payload):
        import asyncio
        await asyncio.sleep(2.0)
        return {"unreached": True}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("planned_bounded"))  # NO run timeout — the TASK request bounds it
        .register_capability("planner", planner, kind="llm")
        .register_capability("slow", slow, kind="deterministic")
        .register_workflow(WorkflowBuilder("planned_bounded").plan("audit", capability="planner").build())
        .build()
    )
    result = await engine.run("planned_bounded", {})
    task = {t.task_id: t for t in result.output.tasks}["slow"]
    assert task.status == "partial", "the task's own execution timeout must bound it"
    assert result.status == "partial"


async def test_resolved_window_enforcement_is_truthful_on_the_context():
    """Phase 2R #2: the PERSISTED window records the actual enforcement mode, not always none."""

    seen = {}

    async def probe(ctx, _payload):
        w = ctx.execution_window
        seen["enforcement"] = w.enforcement if w else None
        seen["hard"] = w.hard_timeout_s if w else None
        return {"ok": True}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("enforce_truth", timeout_s=2.0))
        .register_capability("probe", probe, kind="deterministic")
        .register_workflow(WorkflowBuilder("enforce_truth").step("probe").build())
        .build()
    )
    await engine.run("enforce_truth", {})
    assert seen["enforcement"] == "cooperative", (
        f"an async handler under a window must record cooperative enforcement, got {seen}"
    )


async def test_graph_failsafe_stops_a_hang_outside_a_capability_boundary():
    """Graph code outside capability enforcement gets a bounded cancellation allowance.

    A graph that suppresses the first cancellation cannot hang the caller indefinitely; the
    returned failure and trace both say containment failed instead of claiming a clean stop.
    """

    import time as _t
    from types import SimpleNamespace

    from ai_workflow_engine.engine.capabilities import InMemoryTraceSink
    from ai_workflow_engine.engine.runner import WorkflowRunner
    from ai_workflow_engine.models import RuntimeLimits

    class _SuppressingGraph:
        async def ainvoke(self, _state, _config=None):
            try:
                await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                await asyncio.sleep(30.0)  # second cancellation after grace stops this
            return {"status": "completed"}

    sink = InMemoryTraceSink()
    runner = WorkflowRunner(trace_sink=sink)
    work_timeout_s = 0.25
    cancellation_grace_s = 0.25
    runner._GRAPH_CANCELLATION_GRACE_S = cancellation_grace_s
    started = _t.monotonic()
    result = await runner.run(
        _SuppressingGraph(),
        {"engine_context": SimpleNamespace(limits=RuntimeLimits(timeout_s=work_timeout_s))},
        workflow_type="hang",
    )
    elapsed = _t.monotonic() - started

    # The represented mechanism owns 0.50s. The extra second is scheduler/runner overhead,
    # not permission to settle arbitrarily late: a 2s delay after containment must fail.
    assert elapsed < work_timeout_s + cancellation_grace_s + 1.0, (
        f"cancellation suppression settled outside its represented window: {elapsed:.2f}s"
    )
    assert result["status"] == "failed"
    assert result["graph_failsafe"]["containment_failed"] is True
    event = next(e for e in sink.events if e.phase == "run:failsafe")
    assert event.metadata["graph_failsafe"] == result["graph_failsafe"]


async def test_exhausted_graph_failsafe_never_starts_graph_code():
    from ai_workflow_engine.engine.runner import (
        GraphFailsafeWindow,
        _GraphFailsafeExpired,
        _invoke_graph_with_failsafe,
    )

    calls = 0

    async def graph_work():
        nonlocal calls
        calls += 1
        return {"status": "completed"}

    with pytest.raises(_GraphFailsafeExpired):
        await _invoke_graph_with_failsafe(
            graph_work(),
            GraphFailsafeWindow(work_timeout_s=0.0, cancellation_grace_s=0.1),
        )
    assert calls == 0


async def test_nested_capability_invocation_receives_a_parent_clamped_window():
    """Phase 2R #6a / 5R: a REAL nested invocation — a bounded parent capability invoking a
    child through the runtime — hands the child an ExecutionWindowDecision clamped by the
    parent's remaining soft deadline (limiting source ``parent_window``, clamp
    ``parent_soft_remaining``), and concurrent parents with different windows stay isolated
    (one parent's tight window never leaks into the other's child)."""

    import asyncio as _a

    from ai_workflow_engine.engine.capabilities import CapabilityRegistry, CapabilityRuntime
    from ai_workflow_engine.execution_window import TaskExecutionRequest
    from ai_workflow_engine.models import (
        CapabilityContext,
        RuntimePlan,
        SafetyPolicy,
        WorkflowGoal,
        WorkflowRunContext,
    )

    registry = CapabilityRegistry()
    runtime = CapabilityRuntime(registry)
    child_windows: dict = {}

    async def child(ctx, payload):
        child_windows[payload["tag"]] = ctx.execution_window
        return {"ok": True}

    async def parent(ctx, payload):
        await _a.sleep(0.05)  # force real overlap between the two concurrent parents
        # the child must NOT inherit the parent's execution_request — its ONLY bound here
        # is the ambient parent clamp.
        child_ctx = ctx.model_copy(update={"execution_request": None})
        result = await runtime.invoke("child", {"tag": payload["tag"]}, child_ctx)
        return {"child_status": result.status}

    registry.register(CapabilitySpec(name="child", kind="deterministic"), child)
    registry.register(CapabilitySpec(name="parent", kind="deterministic"), parent)

    def _ctx(timeout_s: float) -> CapabilityContext:
        return CapabilityContext(
            goal=WorkflowGoal(workflow_type="nested_clamp", objective="clamp"),
            run_context=WorkflowRunContext(workflow_id=f"nested-{timeout_s}", workflow_type="nested_clamp"),
            plan=RuntimePlan(workflow_type="nested_clamp", safety=SafetyPolicy(allowed_side_effects=[])),
            execution_request=TaskExecutionRequest(timeout_s=timeout_s),
        )

    wide, tight = await _a.gather(
        runtime.invoke("parent", {"tag": "wide"}, _ctx(5.0)),
        runtime.invoke("parent", {"tag": "tight"}, _ctx(0.8)),
    )
    assert wide.status == "accepted" and tight.status == "accepted"

    win_wide = child_windows["wide"]
    win_tight = child_windows["tight"]
    # the child's window IS the parent clamp — named, not inferred
    for window in (win_wide, win_tight):
        assert window is not None and window.is_bounded
        assert window.limiting_sources == ["parent_window"]
        assert window.clamps == ["parent_soft_remaining"]
    # each child is bounded by ITS OWN parent's remaining soft budget (shorten-only) ...
    assert 0 < win_wide.hard_timeout_s <= 5.0
    assert 0 < win_tight.hard_timeout_s <= 0.8
    # ... and the tight parent's window never leaked into the wide parent's child
    assert win_wide.hard_timeout_s > 1.0, (
        f"concurrent isolation broken: wide child clamped to {win_wide.hard_timeout_s}"
    )


async def test_bounded_capability_records_its_window_on_the_success_trace_event():
    """v0.10 Phase 3: a bounded capability that SUCCEEDS records its resolved execution window
    on the result trace event, so the viewer can project soft/hard/enforcement on a normal node
    — not only on the timeout path. Unbounded runs stay lean (no window metadata)."""

    async def quick(_context, _payload):
        return {"ok": True}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("bounded_ok", timeout_s=5.0))
        .register_capability("quick", quick, kind="deterministic")
        .register_workflow(WorkflowBuilder("bounded_ok").step("quick").build())
        .build()
    )
    result = await engine.run("bounded_ok", {})
    assert result.status == "completed"

    windows = [
        e.metadata["execution_window"]
        for e in result.trace
        if e.node == "quick"
        and e.phase == "tool:result"
        and (e.metadata or {}).get("execution_window")
    ]
    assert windows, "a bounded successful capability must record its window for the viewer"
    hard = windows[-1]["hard_timeout_s"]
    assert hard is not None and 0 < hard <= 5.0

    # an UNbounded run records no window metadata — traces stay lean
    unbounded_engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("unbounded_ok"))
        .register_capability("quick", lambda _c, _p: {"ok": True}, kind="deterministic")
        .register_workflow(WorkflowBuilder("unbounded_ok").step("quick").build())
        .build()
    )
    unbounded = await unbounded_engine.run("unbounded_ok", {})
    assert not any(
        (e.metadata or {}).get("execution_window")
        for e in unbounded.trace
        if e.node == "quick"
    ), "an unbounded capability must not record a window"


async def test_cancelled_external_process_is_killed_and_reaped(tmp_path):
    """5R finding 2: cancellation DURING a subprocess run (the outer boundary fired) must
    kill AND reap the child and settle the reader tasks — the child never outlives the
    engine's claim that it stopped. A zombie or a survivor fails this test."""

    import os
    import signal
    import sys
    import time as _t

    from ai_workflow_engine.engine.external import (
        ExternalProcessCapability,
        ExternalProcessRequest,
    )

    cap = ExternalProcessCapability()
    pid_file = tmp_path / "pid.txt"
    script = (
        "import os,sys,time,pathlib; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )
    task = asyncio.ensure_future(
        cap(
            None,
            ExternalProcessRequest(
                command=[sys.executable, "-c", script, str(pid_file)],
                timeout_s=30.0,
            ),
        )
    )
    for _ in range(400):  # wait for the child to be alive and announced
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.025)
    else:
        task.cancel()
        raise AssertionError("child never started")
    pid = int(pid_file.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    deadline = _t.monotonic() + 3.0
    while _t.monotonic() < deadline:
        try:
            os.kill(pid, 0)  # raises ProcessLookupError once killed AND reaped
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)  # do not leak the child out of the test
        raise AssertionError(
            "cancelled external process left the child running (not killed/reaped)"
        )


def test_external_process_capability_declares_process_enforcement():
    """5R finding 3: the engine's real subprocess owner publishes an honest process-marked
    spec — direct registration must never be misclassified as merely 'cooperative'."""

    from ai_workflow_engine.engine.external import ExternalProcessCapability

    cap = ExternalProcessCapability()
    assert cap.spec.timeout_enforcement == "process"
    assert cap.spec.resolved_timeout_enforcement(is_async=True) == "process"
    # honest default ledger for an arbitrary subprocess
    assert "external_call" in cap.spec.side_effects


def test_published_capability_spec_cannot_silently_discard_registration_policy():
    """A handler-owned spec is the contract. Duplicate registration policy must fail loudly
    instead of silently dropping a stronger side-effect ledger."""

    from ai_workflow_engine.engine.external import ExternalProcessCapability

    cap = ExternalProcessCapability(name="run_process", side_effects=["external_call"])
    with pytest.raises(ValueError, match="would be ignored.*side_effects"):
        WorkflowEngineBuilder().register_capability(
            "run_process",
            cap,
            side_effects=["workspace_write", "external_call"],
        )

    configured = ExternalProcessCapability(
        name="run_process",
        side_effects=["workspace_write", "external_call"],
    )
    engine = WorkflowEngineBuilder().register_capability("run_process", configured).build()
    spec, _handler = engine.registry.get("run_process")
    assert spec.side_effects == ["workspace_write", "external_call"]


async def test_external_process_obeys_the_ambient_engine_window(tmp_path):
    """5R finding 1/2: the ambient invocation window CLAMPS the subprocess bound — a huge
    explicit request timeout cannot outlive the engine window; the child is stopped and the
    partial salvage path runs."""

    import sys
    import time as _t

    from ai_workflow_engine.engine.external import (
        ExternalProcessCapability,
        ExternalProcessRequest,
    )
    from ai_workflow_engine.execution_window import (
        publish_invocation_window,
        reset_invocation_window,
    )

    cap = ExternalProcessCapability()
    now = _t.monotonic()
    token = publish_invocation_window(
        soft_deadline_monotonic=now + 1.0,
        hard_deadline_monotonic=now + 2.0,
    )
    try:
        started = _t.monotonic()
        result = await cap(
            None,
            ExternalProcessRequest(
                command=[
                    sys.executable,
                    "-c",
                    (
                        "import signal,time; "
                        "signal.signal(signal.SIGTERM, lambda *_: None); "
                        "print('ready', flush=True); time.sleep(30)"
                    ),
                ],
                timeout_s=300.0,  # a huge explicit bound must NOT win over the engine window
                kill_grace_s=0.5,
            ),
        )
        elapsed = _t.monotonic() - started
    finally:
        reset_invocation_window(token)

    assert result.status == "partial"
    # The engine represents a 2s hard boundary. One additional second covers process spawn
    # and runner scheduling, while a 2s settlement regression still fails this fence.
    assert elapsed < 3.0, f"cleanup did not settle near the hard window (took {elapsed:.2f}s)"
    assert result.metadata["killed_after_grace"] is True
    assert "ready" in result.output["stdout"]
    assert result.metadata["bound_source"] == "engine_window"
    assert result.metadata["timeout_s"] <= 1.0
    assert result.metadata["requested_timeout_s"] == 300.0
    bound = result.metadata["process_execution_bound"]
    assert bound["engine_hard_s"] <= 2.0
    assert bound["settle_reserve_s"] > 0
    assert (
        bound["work_timeout_s"]
        + bound["kill_grace_s"]
        + bound["settle_reserve_s"]
        <= bound["engine_hard_s"]
    )


def test_graph_failsafe_budget_follows_the_session_remaining_not_the_full_timeout():
    """5R finding 4: after resume, the fail-safe must grant the REMAINING active budget —
    never a fresh full timeout — with cancellation grace represented separately."""

    from ai_workflow_engine.engine.runner import derive_graph_failsafe_window
    from ai_workflow_engine.models import (
        CapabilityContext,
        RuntimePlan,
        SafetyPolicy,
        WorkflowGoal,
        WorkflowRunContext,
    )
    from ai_workflow_engine.run_session import WorkflowRunSession

    def _session() -> WorkflowRunSession:
        return WorkflowRunSession(
            workflow_id="failsafe",
            context=CapabilityContext(
                goal=WorkflowGoal(workflow_type="failsafe", objective="x"),
                run_context=WorkflowRunContext(workflow_id="fs-1", workflow_type="failsafe"),
                plan=RuntimePlan(workflow_type="failsafe", safety=SafetyPolicy(allowed_side_effects=[])),
            ),
        )

    clock_now = [100.0]
    fresh = _session()
    fresh.start_execution_window(
        run_timeout_s=10.0,
        clock=lambda: clock_now[0],
        completion_reserve_s=2.0,
    )
    fresh_window = derive_graph_failsafe_window(
        10.0, fresh, cancellation_grace_s=2.0
    )
    assert fresh_window.work_timeout_s == pytest.approx(8.0)
    assert fresh_window.cancellation_grace_s == pytest.approx(2.0)
    assert (
        fresh_window.work_timeout_s + fresh_window.cancellation_grace_s
        == pytest.approx(10.0)
    )

    resumed = _session()
    resumed.start_execution_window(
        run_timeout_s=10.0,
        clock=lambda: clock_now[0],
        prior_active_elapsed_s=9.0,
        completion_reserve_s=2.0,
    )
    resumed_window = derive_graph_failsafe_window(
        10.0, resumed, cancellation_grace_s=2.0
    )
    assert resumed_window.work_timeout_s == pytest.approx(0.75), (
        "a resumed run that already consumed 9 of its 10s must NOT get a fresh full timeout"
    )
    assert resumed_window.cancellation_grace_s == pytest.approx(0.25)
    assert (
        resumed_window.work_timeout_s + resumed_window.cancellation_grace_s
        == pytest.approx(1.0)
    )

    # no session still reserves containment inside the declared wall time.
    no_session = derive_graph_failsafe_window(10.0, None, cancellation_grace_s=2.0)
    assert no_session.work_timeout_s == pytest.approx(8.0)
    assert no_session.cancellation_grace_s == pytest.approx(2.0)
    assert derive_graph_failsafe_window(None, fresh, cancellation_grace_s=2.0) is None
    with pytest.raises(ValueError, match="finite non-negative"):
        derive_graph_failsafe_window(10.0, fresh, cancellation_grace_s=float("inf"))


async def test_inner_timeout_error_is_not_relabeled_as_the_graph_failsafe():
    """5R finding 4 (typed ownership): a TimeoutError raised by code INSIDE the graph is that
    code's own error — it must propagate as itself, never be relabeled as the graph-level
    fail-safe 'hang outside a capability'."""

    from types import SimpleNamespace

    from ai_workflow_engine.engine.runner import WorkflowRunner
    from ai_workflow_engine.models import RuntimeLimits

    class _InnerTimeoutGraph:
        async def ainvoke(self, _state, _config=None):
            raise TimeoutError("inner adapter timed out")

    runner = WorkflowRunner()
    with pytest.raises(TimeoutError, match="inner adapter timed out"):
        await runner.run(
            _InnerTimeoutGraph(),
            {"engine_context": SimpleNamespace(limits=RuntimeLimits(timeout_s=5.0))},
            workflow_type="inner_timeout",
        )


# --------------------------------------------------------------------------------------
# R10.1 (engine-v0.10.1 corrective): the external-process RESULT-SETTLEMENT boundary must
# be as bounded as the execution window itself. Written RED against engine-v0.10.0: a child
# can defeat the deadline/memory contract AFTER its own reap via the result path (FIFO,
# symlink, oversize) or during capture (output flood). Each test is attack-shaped, bounded
# by its own watchdog, and leaks no process/file.
# --------------------------------------------------------------------------------------


def test_fifo_result_file_cannot_block_the_engine_past_its_deadline(tmp_path):
    """R10.1 defect 1: the child replaces its declared result file with a FIFO and exits 0.
    ``exists()`` is true; a blocking ``open()``/``read_text()`` on a writer-less FIFO then
    freezes the ENTIRE event loop forever — after the child was reaped, past every declared
    window (the graph fail-safe cannot fire on a blocked loop). The settlement must return
    within the represented window instead. Runs the capability on a dedicated thread+loop so
    the test itself can never hang."""

    import os
    import subprocess
    import sys
    import threading

    from ai_workflow_engine.engine.external import (
        ExternalProcessCapability,
        ExternalProcessRequest,
    )

    result_file = tmp_path / "codex-last-message.txt"
    child = (
        "import os, sys; os.mkfifo(sys.argv[1])"  # declared result becomes a FIFO trap
    )
    outcome: dict = {}

    def run() -> None:
        async def call():
            cap = ExternalProcessCapability()
            return await cap(
                None,
                ExternalProcessRequest(
                    command=[sys.executable, "-c", child, str(result_file)],
                    cwd=str(tmp_path),
                    timeout_s=5.0,
                    result_file=str(result_file),
                ),
            )

        outcome["result"] = asyncio.run(call())

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=8.0)  # child exits instantly; 8s covers slow CI, not the hang
    if worker.is_alive():
        # unblock the trapped reader so the thread can die, then fail for the named reason
        try:
            fd = os.open(result_file, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
        except OSError:
            subprocess.run(  # last resort: a writer via the shell
                ["sh", "-c", f": > {result_file}"], timeout=5, check=False
            )
        worker.join(timeout=5.0)
        pytest.fail(
            "FIFO result file blocked the engine event loop past its declared window "
            "(settlement is not bounded)"
        )
    settled = outcome["result"]
    assert settled is not None
    # the settlement must be truthful about the unsafe result — never a clean acceptance
    assert settled.status == "failed", (
        f"a FIFO in place of the declared result is an unsafe settlement: {settled.status}"
    )


async def test_result_symlink_never_discloses_out_of_workspace_content(tmp_path):
    """R10.1 defect 3: the child symlinks its declared result file at a secret OUTSIDE the
    workspace and exits 0. Following it reads the secret into output/observation — a
    disclosure primitive. The settlement must reject the non-regular result and the sentinel
    must appear NOWHERE in the result envelope."""

    import json as _json
    import sys

    from ai_workflow_engine.engine.external import (
        ExternalProcessCapability,
        ExternalProcessRequest,
    )

    secret = tmp_path / "outside" / "host-secret.env"
    secret.parent.mkdir()
    secret.write_text("SENTINEL-DO-NOT-DISCLOSE-c0ffee", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result_file = workspace / "codex-last-message.txt"
    child = "import os, sys; os.symlink(sys.argv[2], sys.argv[1])"

    cap = ExternalProcessCapability()
    settled = await cap(
        None,
        ExternalProcessRequest(
            command=[sys.executable, "-c", child, str(result_file), str(secret)],
            cwd=str(workspace),
            timeout_s=5.0,
            result_file=str(result_file),
        ),
    )

    envelope = _json.dumps(
        {"status": settled.status, "output": settled.output, "error": settled.error,
         "metadata": {k: str(v) for k, v in settled.metadata.items()}}
    )
    assert "SENTINEL-DO-NOT-DISCLOSE-c0ffee" not in envelope, (
        "a result symlink was FOLLOWED — out-of-workspace content leaked into the envelope"
    )
    assert settled.status == "failed", (
        f"a symlinked result target is an unsafe settlement, not a clean success: {settled.status}"
    )


async def test_oversized_result_file_is_bounded_not_slurped(tmp_path):
    """R10.1 defect 2 (result side): an 8 MiB result file must not be read whole into engine
    state. Over the configured cap it is a bounded failed settlement — never an unbounded
    allocation reported as accepted."""

    import sys

    from ai_workflow_engine.engine.external import (
        ExternalProcessCapability,
        ExternalProcessRequest,
    )

    result_file = tmp_path / "codex-last-message.txt"
    # the child writes >4 MiB (the eventual default result cap) then exits 0
    child = "import sys; open(sys.argv[1], 'w').write('B' * (8 * 1024 * 1024))"

    cap = ExternalProcessCapability()
    settled = await cap(
        None,
        ExternalProcessRequest(
            command=[sys.executable, "-c", child, str(result_file)],
            cwd=str(tmp_path),
            timeout_s=10.0,
            result_file=str(result_file),
        ),
    )

    retained = settled.output.get("result") if isinstance(settled.output, dict) else ""
    assert len(retained) <= 5 * 1024 * 1024, (
        f"an oversized result was slurped whole ({len(retained)} bytes) — capture is unbounded"
    )
    assert settled.status == "failed", (
        f"an over-cap result file is a bounded failed settlement: {settled.status}"
    )


async def test_stdout_flood_is_drained_bounded_with_truthful_truncation(tmp_path):
    """R10.1 defect 2 (stream side): a noisy process must not exhaust memory. stdout beyond
    the cap is drained to EOF (no child pipe deadlock) but only a bounded head/tail is
    retained, with truthful total-bytes and truncation accounting."""

    import sys

    from ai_workflow_engine.engine.external import (
        ExternalProcessCapability,
        ExternalProcessRequest,
    )

    # ~6 MiB of stdout, well over the eventual 1 MiB stdout cap
    child = "import sys; sys.stdout.write('F' * (6 * 1024 * 1024))"

    cap = ExternalProcessCapability()
    settled = await cap(
        None,
        ExternalProcessRequest(
            command=[sys.executable, "-c", child],
            cwd=str(tmp_path),
            timeout_s=10.0,
        ),
    )

    stdout = settled.output.get("stdout") if isinstance(settled.output, dict) else ""
    assert len(stdout) <= 2 * 1024 * 1024, (
        f"stdout flood retained unbounded ({len(stdout)} bytes) — memory sink"
    )
    io_meta = settled.metadata.get("process_io") if isinstance(settled.metadata, dict) else None
    assert io_meta is not None, "bounded capture must record process_io truncation accounting"
    assert io_meta["stdout"]["total_bytes"] >= 6 * 1024 * 1024
    assert io_meta["stdout"]["truncated"] is True
    assert settled.status == "partial"
    assert "stdout-as-result was truncated" in (settled.error or "")


async def test_external_process_dict_request_validates_nested_io_limits(tmp_path):
    """Serialized workflow inputs can narrow process I/O without constructing engine objects."""

    import sys

    from ai_workflow_engine import ExternalProcessCapability, ProcessIOLimits

    assert ProcessIOLimits is not None  # documented top-level public configuration surface
    cap = ExternalProcessCapability(
        io_limits=ProcessIOLimits(
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
            max_result_bytes=1024,
        )
    )
    settled = await cap(
        None,
        {
            "command": [sys.executable, "-c", "print('X' * 500)"],
            "cwd": str(tmp_path),
            "timeout_s": 10.0,
            "io_limits": {
                "max_stdout_bytes": 64,
                "max_stderr_bytes": 128,
                "max_result_bytes": 256,
            },
        },
    )

    assert settled.status == "partial"
    process_io = settled.metadata["process_io"]
    assert process_io["stdout"]["limit_bytes"] == 64
    assert process_io["stdout"]["truncated"] is True


# ======================================================================================
# FENCE-4 (C1): retrace provenance is TARGET-ONLY — child workflows never see the parent's
# ======================================================================================


async def test_parent_retraced_subworkflow_keeps_child_capabilities_provenance_free():
    """C0 reproducer (FENCE-4, door 1 — declared subworkflow node): when the PARENT
    subworkflow node is the retrace target, the ambient provenance belongs to that parent
    invocation only. Child node capabilities must see None on every child run — a child
    planner keying on provenance.round would otherwise mistake its first local run for a
    follow-up round."""

    from ai_workflow_engine import Retrace

    child_prov: list = []

    def child_probe(context, _payload):
        prov = getattr(context, "retrace_provenance", None)
        child_prov.append(
            None
            if prov is None
            else {"round": prov.round, "evaluator": prov.evaluator_node, "target": prov.target_node}
        )
        return {"child_run": len(child_prov)}

    gate_calls = {"n": 0}

    def gate(_context, _payload):
        gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if gate_calls["n"] > 1 else "rejected", error="one more round"
        )

    child = WorkflowBuilder("iso_child").step("child_probe").build()
    engine = (
        WorkflowEngineBuilder()
        .register_capability("child_probe", child_probe, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_capability("fin", lambda _c, _p: "fin", kind="deterministic")
        .register_workflow(child)
        .register_workflow(
            WorkflowBuilder("iso_parent")
            .subworkflow("delegate", workflow=child)
            .evaluate("qa", target="delegate", evaluator="gate", on_reject=Retrace("delegate"))
            .step("fin")
            .build()
        )
        .build()
    )

    result = await engine.run("iso_parent", {"seed": 1})
    assert result.status == "completed"
    assert gate_calls["n"] == 2 and len(child_prov) == 2
    assert child_prov == [None, None], (
        f"child capabilities must never see the parent's retrace provenance: {child_prov}"
    )


async def test_parent_retraced_workflow_capability_keeps_child_capabilities_provenance_free():
    """C0 reproducer (FENCE-4, door 2 — workflow registered as an ordinary capability):
    the SAME isolation must hold when the child enters through
    register_workflow_capability -> _run_inner. The step's own capability (the workflow
    capability) legitimately receives typed provenance as the retrace target; the nodes
    INSIDE the child must not."""

    from ai_workflow_engine import Retrace

    child_prov: list = []

    def child_probe(context, _payload):
        prov = getattr(context, "retrace_provenance", None)
        child_prov.append(None if prov is None else {"round": prov.round})
        return {"child_run": len(child_prov)}

    gate_calls = {"n": 0}

    def gate(_context, _payload):
        gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if gate_calls["n"] > 1 else "rejected", error="one more round"
        )

    child = WorkflowBuilder("iso_child_cap").step("child_probe").build()
    engine = (
        WorkflowEngineBuilder()
        .register_capability("child_probe", child_probe, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_capability("fin", lambda _c, _p: "fin", kind="deterministic")
        .register_workflow(child)
        .register_workflow(
            WorkflowBuilder("iso_parent_cap")
            .step("delegate_cap", capability="child_cap")
            .evaluate("qa", target="delegate_cap", evaluator="gate", on_reject=Retrace("delegate_cap"))
            .step("fin")
            .build()
        )
        .build()
    )
    engine.register_workflow_capability("child_cap", "iso_child_cap")

    result = await engine.run("iso_parent_cap", {"seed": 1})
    assert result.status == "completed"
    assert len(child_prov) == 2
    assert child_prov == [None, None], (
        f"the second child door must be shielded exactly like the first: {child_prov}"
    )


async def test_child_internal_retrace_still_delivers_child_local_provenance():
    """The _run_inner shield must not disable retrace INSIDE the child: a child-internal
    evaluator retrace delivers the child's OWN provenance (child node ids, round 1) exactly
    once, while the parent's ambient provenance stays invisible throughout."""

    from ai_workflow_engine import Retrace

    child_prov: list = []

    def child_draft(context, _payload):
        prov = getattr(context, "retrace_provenance", None)
        child_prov.append(
            None
            if prov is None
            else {"round": prov.round, "evaluator": prov.evaluator_node, "target": prov.target_node}
        )
        return {"draft": len(child_prov)}

    child_gate_calls = {"n": 0}

    def child_gate(_context, _payload):
        child_gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if child_gate_calls["n"] > 1 else "rejected", error="deepen"
        )

    parent_gate_calls = {"n": 0}

    def parent_gate(_context, _payload):
        parent_gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if parent_gate_calls["n"] > 1 else "rejected", error="again"
        )

    child = (
        WorkflowBuilder("nested_retrace_child")
        .step("child_draft")
        .evaluate("child_qa", target="child_draft", evaluator="child_gate", on_reject=Retrace("child_draft"))
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .register_capability("child_draft", child_draft, kind="deterministic")
        .register_capability("child_gate", child_gate, kind="llm")
        .register_capability("parent_gate", parent_gate, kind="llm")
        .register_capability("fin", lambda _c, _p: "fin", kind="deterministic")
        .register_workflow(child)
        .register_workflow(
            WorkflowBuilder("nested_retrace_parent")
            .subworkflow("delegate", workflow=child)
            .evaluate("qa", target="delegate", evaluator="parent_gate", on_reject=Retrace("delegate"))
            .step("fin")
            .build()
        )
        .build()
    )

    result = await engine.run("nested_retrace_parent", {"seed": 1})
    assert result.status == "completed"
    assert parent_gate_calls["n"] == 2 and child_gate_calls["n"] == 3
    # Parent round 1: child draft (None) + child-internal retraced draft (child provenance).
    # Parent round 2 (parent retrace of the whole subworkflow): the child gate's third call
    # accepts immediately, so ONE more child draft — and it must see None: the shield holds
    # even while the PARENT node itself is the active retrace target.
    assert child_prov == [
        None,
        {"round": 1, "evaluator": "child_qa", "target": "child_draft"},
        None,
    ], f"child-local retrace must survive the shield with child ids only: {child_prov}"


async def test_parent_scope_restores_after_child_failure_and_no_residue_remains():
    """The shield's token reset must restore on the FAILURE path too: a failing child leaves
    no ambient provenance residue for later runs in the same task."""

    from ai_workflow_engine import Retrace

    child_prov: list = []

    def exploding_child(context, _payload):
        prov = getattr(context, "retrace_provenance", None)
        child_prov.append(None if prov is None else {"round": prov.round})
        if len(child_prov) == 1:
            return {"first_round": "fine"}
        # the SECOND visit is the parent-retraced one: fail exactly while the parent's
        # provenance is active, so the restore claim is actually exercised
        raise RuntimeError("child blows up on the retraced visit")

    def probe(context, _payload):
        prov = getattr(context, "retrace_provenance", None)
        return {"residue": None if prov is None else {"round": prov.round}}

    gate_calls = {"n": 0}

    def gate(_context, _payload):
        gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if gate_calls["n"] > 1 else "rejected", error="retry it"
        )

    child = WorkflowBuilder("boom_child").step("exploding_child").build()
    # flow shape: child run 1 succeeds -> qa rejects -> parent retrace -> child run 2 fails
    # WHILE the parent subworkflow node is the active retrace target
    engine = (
        WorkflowEngineBuilder()
        .register_capability("exploding_child", exploding_child, kind="deterministic")
        .register_capability("probe", probe, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_workflow(child)
        .register_workflow(
            WorkflowBuilder("boom_parent")
            .subworkflow("delegate", workflow=child)
            .evaluate("qa", target="delegate", evaluator="gate", on_reject=Retrace("delegate"))
            .build()
        )
        .register_workflow(WorkflowBuilder("residue_probe").step("probe").build())
        .build()
    )

    first = await engine.run("boom_parent", {"seed": 1})
    assert first.status == "failed"
    assert child_prov == [None, None], (
        "the child must run twice (initial + parent-retraced) and the RETRACED visit — "
        f"failing while parent provenance is active — must still be shielded: {child_prov}"
    )

    followup = await engine.run("residue_probe", {"seed": 2})
    assert followup.status == "completed"
    assert followup.output == {"residue": None}, (
        "the failure path must restore the ambient scope — no provenance residue may leak "
        "into later runs in the same task"
    )


async def test_concurrent_retraced_parents_do_not_cross_leak_provenance():
    """Two retraced parent runs executing concurrently in one event loop must each keep
    their children provenance-free — ContextVar task isolation plus the shield make
    cross-run leakage structurally impossible, and this fence would catch any global-state
    replacement of either."""

    import asyncio

    from ai_workflow_engine import Retrace

    mine_here = asyncio.Event()
    other_here = asyncio.Event()

    def build_engine(bucket: list, arrive: "asyncio.Event", wait_for: "asyncio.Event"):
        async def child_probe(context, _payload):
            prov = getattr(context, "retrace_provenance", None)
            bucket.append(None if prov is None else {"round": prov.round})
            if len(bucket) == 2:
                # REAL overlap barrier: both runs' RETRACED child visits must be alive
                # at the same time before either may finish — a serialized execution
                # deadlocks here and the timeout fails the test loudly.
                arrive.set()
                await asyncio.wait_for(wait_for.wait(), timeout=10)
            return {"child_run": len(bucket)}

        gate_calls = {"n": 0}

        def gate(_context, _payload):
            gate_calls["n"] += 1
            return CapabilityResult(
                status="accepted" if gate_calls["n"] > 1 else "rejected", error="again"
            )

        child = WorkflowBuilder("conc_child").step("child_probe").build()
        return (
            WorkflowEngineBuilder()
            .register_capability("child_probe", child_probe, kind="deterministic")
            .register_capability("gate", gate, kind="llm")
            .register_capability("fin", lambda _c, _p: "fin", kind="deterministic")
            .register_workflow(child)
            .register_workflow(
                WorkflowBuilder("conc_parent")
                .subworkflow("delegate", workflow=child)
                .evaluate("qa", target="delegate", evaluator="gate", on_reject=Retrace("delegate"))
                .step("fin")
                .build()
            )
            .build()
        )

    bucket_one: list = []
    bucket_two: list = []
    engine_one = build_engine(bucket_one, mine_here, other_here)
    engine_two = build_engine(bucket_two, other_here, mine_here)

    result_one, result_two = await asyncio.gather(
        engine_one.run("conc_parent", {"seed": 1}),
        engine_two.run("conc_parent", {"seed": 2}),
    )
    assert result_one.status == "completed" and result_two.status == "completed"
    assert bucket_one == [None, None] and bucket_two == [None, None], (
        f"concurrent retraced parents leaked provenance: {bucket_one} / {bucket_two}"
    )


def test_retrace_provenance_scope_owner_restores_on_every_exit_path():
    """The single scope owner: nesting restores the previous value exactly, and the reset
    happens on exception paths too. No set/reset of the ContextVar exists outside it."""

    from pathlib import Path

    from ai_workflow_engine._runtime_state import (
        _ACTIVE_RETRACE_PROVENANCE,
        retrace_provenance_scope,
    )

    assert _ACTIVE_RETRACE_PROVENANCE.get() is None
    with retrace_provenance_scope("outer"):
        assert _ACTIVE_RETRACE_PROVENANCE.get() == "outer"
        with retrace_provenance_scope(None):
            assert _ACTIVE_RETRACE_PROVENANCE.get() is None
            with retrace_provenance_scope("inner"):
                assert _ACTIVE_RETRACE_PROVENANCE.get() == "inner"
            assert _ACTIVE_RETRACE_PROVENANCE.get() is None
        assert _ACTIVE_RETRACE_PROVENANCE.get() == "outer"
    assert _ACTIVE_RETRACE_PROVENANCE.get() is None

    with pytest.raises(RuntimeError):
        with retrace_provenance_scope("explodes"):
            raise RuntimeError("boom")
    assert _ACTIVE_RETRACE_PROVENANCE.get() is None, "exception path must restore"

    # One owner: no direct .set(/.reset( on the provenance var outside _runtime_state.py.
    package_root = Path(__file__).resolve().parents[1] / "ai_workflow_engine"
    offenders = []
    for path in package_root.rglob("*.py"):
        if path.name == "_runtime_state.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "_ACTIVE_RETRACE_PROVENANCE.set(" in source or "_ACTIVE_RETRACE_PROVENANCE.reset(" in source:
            offenders.append(str(path))
    assert not offenders, f"provenance publication has ONE owner; direct set/reset in: {offenders}"


async def test_run_inner_boundary_sanitizes_explicit_context_provenance():
    """CXR-1: the universal child boundary owns BOTH provenance surfaces — the ambient
    ContextVar AND an explicit stale `retrace_provenance` already carried by the incoming
    CapabilityContext. The built-in doors sanitize via build_child_context today, but the
    boundary must not rely on caller discipline: a direct child run with a provenance-
    carrying context must still deliver None to child capabilities."""

    from ai_workflow_engine.models import (
        CapabilityContext,
        RetraceProvenance,
        WorkflowGoal,
        WorkflowRunContext,
    )

    seen: list = []

    def child_probe(context, _payload):
        prov = getattr(context, "retrace_provenance", None)
        seen.append(None if prov is None else {"round": prov.round, "target": prov.target_node})
        return {"ok": True}

    child = WorkflowBuilder("boundary_child").step("child_probe").build()
    engine = (
        WorkflowEngineBuilder()
        .register_capability("child_probe", child_probe, kind="deterministic")
        .register_workflow(child)
        .build()
    )

    stale = CapabilityContext(
        goal=WorkflowGoal(workflow_type="boundary_child", objective="probe"),
        run_context=WorkflowRunContext(workflow_id="boundary-run", workflow_type="boundary_child"),
        retrace_provenance=RetraceProvenance(
            round=1,
            evaluator_node="parent_qa",
            source_node="parent_delegate",
            target_node="parent_delegate",
        ),
    )
    result = await engine.executor._run_inner(child, {"seed": 1}, stale)
    assert result.status == "completed"
    assert seen == [None], (
        f"the child boundary must sanitize explicit context provenance, got {seen}"
    )
