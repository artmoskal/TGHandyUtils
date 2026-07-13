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


async def test_exhausted_run_refuses_downstream_work_as_typed_partial():
    calls = {"after": 0}

    async def consume(_ctx, _payload):
        await asyncio.sleep(1.0)
        return {"unreachable": True}

    def after(_ctx, _payload):
        calls["after"] += 1
        return {"also": "unreachable"}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("exhausted", timeout_s=0.05))
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
    assert terminal.metadata["execution_window"]["hard_timeout_s"] == 0


async def test_snapshot_active_elapsed_duration_is_strict_finite_and_non_negative():
    from ai_workflow_engine.snapshot import MachineSnapshot

    common = {"workflow_id": "w", "suspended_node": "gate"}
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


async def test_inline_capability_with_declared_window_is_rejected_before_running():
    """Phase 2.3 (none): an uninterruptible inline capability that DECLARES a finite window
    (spec.timeout_s) is refused before the handler runs — the engine never claims a hard stop
    it cannot perform. A run-limit-only window on inline work stays boundary-only (allowed)."""

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
