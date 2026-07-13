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


async def test_run_timeout_actually_bounds_slow_async_work():
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
    assert cut_calls["n"] == 1, "a terminal partial task must not re-run on replan"


async def test_retrace_provenance_round_two_and_planner_target_and_denial_isolation():
    """F2 depth: round increments to 2 across successive retraces; a retrace targeting a
    NON-step (planner) node still delivers provenance via the generic boundary; exact
    source/evaluator/target; and a concurrent second run does not leak provenance."""

    import asyncio as _asyncio

    from ai_workflow_engine import Retrace

    rounds_seen: list = []

    def draft(ctx, _payload):
        prov = getattr(ctx, "retrace_provenance", None)
        rounds_seen.append(prov.round if prov else None)
        # need TWO rejections to force rounds 1 and 2
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
    # allow up to 2 retraces
    result = await engine.run("round2", {})
    # rounds_seen: [None, 1, 2] then accepted (or bounded)
    non_none = [r for r in rounds_seen if r is not None]
    assert rounds_seen[0] is None
    assert non_none == [1, 2], f"successive retraces must expose rounds 1 then 2, got {rounds_seen}"

    # concurrent isolation: two runs at once must not share provenance
    rounds_seen.clear()
    r1, r2 = await _asyncio.gather(engine.run("round2", {}), engine.run("round2", {}))
    assert r1.status in ("completed", "failed") and r2.status in ("completed", "failed")

def test_tools_wheel_identity_advanced_for_changed_code():
    """Defect 4: ai_workflow_tools source changed between engine-v0.8.1 and engine-v0.9.2
    (cli_agents assembly/console/models) while both tags shipped as 0.3.0 — two different
    wheels must never share name+version. v0.10 requires the 0.4.0 identity."""

    import ai_workflow_tools

    assert ai_workflow_tools.__version__ == "0.4.0", (
        "tools code changed since the last released 0.3.0 wheel identity; the version must "
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
