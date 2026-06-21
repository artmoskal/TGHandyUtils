"""Planner-node contract tests.

These tests intentionally exercise engine-owned behavior, not product loops: validation,
task execution, plan state mutation, trace events, bounded replan, resume from a PlanArtifact,
and shared run budget.
"""

from __future__ import annotations

import pytest

from ai_workflow_engine import (
    CapabilityResult,
    CapabilitySpec,
    PlanArtifact,
    PlanTask,
    Replan,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowValidationError,
)
from ai_workflow_engine.models import (
    CriticismEnvelope,
    EvaluationDecision,
    RuntimeLimits,
    SafetyPolicy,
    WorkflowProfile,
)


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _profile(
    workflow_type: str,
    *,
    allowed_side_effects: list[str] | None = None,
    max_estimated_usd: float | None = None,
) -> WorkflowProfile:
    return WorkflowProfile(
        workflow_type=workflow_type,
        limits=RuntimeLimits(max_estimated_usd=max_estimated_usd),
        safety=SafetyPolicy(allowed_side_effects=allowed_side_effects or []),
    )


async def test_replan_target_must_be_a_planner_node():
    with pytest.raises(WorkflowValidationError) as exc:
        (
            WorkflowBuilder("bad_replan")
            .step("draft")
            .step("not_planner")
            .evaluate("qa", target="not_planner", evaluator="gate", on_reject=Replan("not_planner"))
            .build()
        )

    assert "replan target is not a planner" in str(exc.value)


async def test_planner_executes_tasks_with_partial_failure_isolation_and_trace():
    def planner(_ctx, _payload):
        return PlanArtifact(
            goal="summarize evidence",
            tasks=[
                PlanTask(task_id="extract", description="extract fact", capability="extract", payload=1),
                PlanTask(task_id="bad", description="prove isolation", capability="bad", payload=2),
                PlanTask(task_id="render", description="render result", capability="render", payload=3),
            ],
        )

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("planned"))
        .register_capability("planner", planner, kind="llm")
        .register_capability("extract", lambda _ctx, payload: payload * 10, kind="deterministic")
        .register_capability(
            "bad",
            lambda _ctx, _payload: CapabilityResult(status="failed", error="boom"),
            kind="deterministic",
        )
        .register_capability("render", lambda _ctx, payload: {"rendered": payload}, kind="deterministic")
        .register_workflow(WorkflowBuilder("planned").plan("make_plan", capability="planner").build())
        .build()
    )

    result = await engine.run("planned", {})

    assert result.status == "partial"
    assert isinstance(result.output, PlanArtifact)
    assert [(task.task_id, task.status) for task in result.output.tasks] == [
        ("extract", "done"),
        ("bad", "failed"),
        ("render", "done"),
    ]
    assert result.output.tasks[0].output_ref == "make_plan.extract"
    assert result.node("make_plan").status == "partial"
    assert any(event.decision == "plan:task_started" for event in result.trace)
    assert any(event.decision == "plan:task_failed" and event.metadata["task_id"] == "bad" for event in result.trace)
    assert result.output.model_dump_json()


async def test_planner_aborts_invalid_plan_before_any_task_execution():
    calls = {"writer": 0}

    def writer(_ctx, payload):
        calls["writer"] += 1
        return payload

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("invalid_plan", allowed_side_effects=["safe"]))
        .register_capability(
            "planner",
            lambda _ctx, _payload: PlanArtifact(
                goal="unsafe write",
                tasks=[
                    PlanTask(
                        task_id="write",
                        description="write externally",
                        capability="writer",
                        payload={"x": 1},
                    )
                ],
            ),
            kind="llm",
        )
        .register_capability("writer", writer, kind="tool", side_effects=["external_write"])
        .register_workflow(
            WorkflowBuilder("invalid_plan").plan("make_plan", capability="planner").build()
        )
        .build()
    )

    result = await engine.run("invalid_plan", {})

    assert result.status == "failed"
    assert calls["writer"] == 0
    assert "side effects denied" in (result.error or "")
    assert any(event.decision == "plan:validation_failed" for event in result.trace)


async def test_planner_reports_plan_artifact_validation_reason():
    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("malformed_plan"))
        .register_capability(
            "planner",
            lambda _ctx, _payload: {
                "goal": "malformed",
                "tasks": [{"task_id": "missing-required-fields"}],
            },
            kind="llm",
        )
        .register_workflow(WorkflowBuilder("malformed_plan").plan("make_plan", capability="planner").build())
        .build()
    )

    result = await engine.run("malformed_plan", {})

    assert result.status == "failed"
    assert "PlanArtifact-compatible output" in (result.error or "")
    assert "description" in (result.error or "")
    assert "capability" in (result.error or "")


async def test_planner_enforces_max_tasks_and_depth_one_capability_rule():
    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("invalid_shape"))
        .register_capability(
            "planner",
            lambda _ctx, _payload: PlanArtifact(
                goal="too much",
                tasks=[
                    PlanTask(task_id="a", description="first", capability="noop"),
                    PlanTask(task_id="b", description="second", capability="planner"),
                ],
            ),
            kind="llm",
        )
        .register_capability("noop", lambda _ctx, payload: payload, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("invalid_shape")
            .plan("make_plan", capability="planner", max_tasks=1)
            .build()
        )
        .build()
    )

    result = await engine.run("invalid_shape", {})

    assert result.status == "failed"
    assert "exceeds max_tasks=1" in (result.error or "")
    assert "cannot call its planner capability" in (result.error or "")


async def test_plan_context_injection_is_opt_in_per_node():
    seen = {"with_plan": "", "without_plan": None}

    def see_plan(ctx, _payload):
        seen["with_plan"] = ctx.metadata["plan"]
        return "seen"

    def no_plan(ctx, _payload):
        seen["without_plan"] = ctx.metadata.get("plan")
        return "not-seen"

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("inject_plan"))
        .register_capability(
            "planner",
            lambda _ctx, _payload: PlanArtifact(
                goal="context",
                tasks=[PlanTask(task_id="noop", description="do nothing", capability="noop")],
            ),
            kind="llm",
        )
        .register_capability("noop", lambda _ctx, payload: payload, kind="deterministic")
        .register_capability("see_plan", see_plan, kind="deterministic")
        .register_capability("no_plan", no_plan, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("inject_plan")
            .plan("make_plan", capability="planner")
            .step("see", capability="see_plan", inject_plan=True)
            .step("no", capability="no_plan")
            .build()
        )
        .build()
    )

    result = await engine.run("inject_plan", {})

    assert result.status == "completed"
    assert "Goal: context" in seen["with_plan"]
    assert "- [done] noop" in seen["with_plan"]
    assert seen["without_plan"] is None


async def test_evaluator_can_boundedly_replan_without_rerunning_done_tasks():
    calls = {"planner": 0, "a": 0, "b": 0}

    def planner(_ctx, payload):
        calls["planner"] += 1
        if calls["planner"] == 1:
            return PlanArtifact(
                goal="need full coverage",
                tasks=[PlanTask(task_id="a", description="first fact", capability="task_a")],
            )
        assert isinstance(payload, dict)
        assert payload["plan_artifact"]["tasks"][0]["status"] == "done"
        assert payload["_criticism"]["observed"] == "missing second fact"
        return PlanArtifact(
            goal="need full coverage",
            tasks=[
                PlanTask(task_id="a", description="do not mutate done fact", capability="task_a"),
                PlanTask(task_id="b", description="second fact", capability="task_b"),
            ],
        )

    def task_a(_ctx, _payload):
        calls["a"] += 1
        return "A"

    def task_b(_ctx, _payload):
        calls["b"] += 1
        return "B"

    def gate(_ctx, plan):
        if calls["planner"] == 1:
            return EvaluationDecision(
                action="replan",
                criticism=CriticismEnvelope(
                    observed="missing second fact",
                    expected="two covered facts",
                ),
            )
        return CapabilityResult(status="accepted", output=plan)

    workflow = (
        WorkflowBuilder("replan_demo")
        .plan("make_plan", capability="planner")
        .evaluate("qa", target="make_plan", evaluator="gate", on_reject=Replan("make_plan", max_replans=1))
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("replan_demo"))
        .register_capability("planner", planner, kind="llm")
        .register_capability("task_a", task_a, kind="deterministic")
        .register_capability("task_b", task_b, kind="deterministic")
        .register_capability("gate", gate, kind="deterministic")
        .register_workflow(workflow)
        .build()
    )

    result = await engine.run("replan_demo", {})

    assert result.status == "completed"
    assert calls == {"planner": 2, "a": 1, "b": 1}
    assert result.output.revision == 1
    assert [(task.task_id, task.status) for task in result.output.tasks] == [
        ("a", "done"),
        ("b", "done"),
    ]


async def test_replan_limit_fails_closed_without_infinite_loop():
    calls = {"planner": 0}

    def planner(_ctx, _payload):
        calls["planner"] += 1
        return PlanArtifact(goal="always rejected", tasks=[])

    workflow = (
        WorkflowBuilder("replan_exhaustion")
        .plan("make_plan", capability="planner")
        .evaluate("qa", target="make_plan", evaluator="gate", on_reject=Replan("make_plan", max_replans=1))
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("replan_exhaustion"))
        .register_capability("planner", planner, kind="llm")
        .register_capability(
            "gate",
            lambda _ctx, plan: CapabilityResult(status="rejected", output=plan, error="still bad"),
            kind="deterministic",
        )
        .register_workflow(workflow)
        .build()
    )

    result = await engine.run("replan_exhaustion", {})

    assert result.status == "failed"
    assert calls["planner"] == 2
    assert "evaluation policy exhausted" in (result.error or "")


async def test_planner_can_resume_from_checkpoint_safe_plan_artifact_payload():
    planner_calls = {"count": 0}

    def planner(_ctx, _payload):
        planner_calls["count"] += 1
        raise AssertionError("resume should use supplied PlanArtifact, not call planner")

    plan = PlanArtifact(
        goal="resume",
        tasks=[
            PlanTask(
                task_id="done",
                description="already done",
                capability="noop",
                status="done",
                output_ref="make_plan.done",
            ),
            PlanTask(task_id="pending", description="finish me", capability="finish", payload=2),
        ],
    )

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("resume_plan"))
        .register_capability("planner", planner, kind="llm")
        .register_capability("noop", lambda _ctx, payload: payload, kind="deterministic")
        .register_capability("finish", lambda _ctx, payload: payload * 5, kind="deterministic")
        .register_workflow(WorkflowBuilder("resume_plan").plan("make_plan", capability="planner").build())
        .build()
    )

    result = await engine.run("resume_plan", PlanArtifact.model_validate_json(plan.model_dump_json()))

    assert result.status == "completed"
    assert planner_calls["count"] == 0
    assert [(task.task_id, task.status) for task in result.output.tasks] == [
        ("done", "done"),
        ("pending", "done"),
    ]
    assert result.output.tasks[1].output_ref == "make_plan.pending"


async def test_planned_metered_task_uses_shared_run_budget_cap():
    calls = {"paid": 0}

    def paid(_ctx, payload):
        calls["paid"] += 1
        return payload

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("planned_budget", max_estimated_usd=0.0))
        .register_capability(
            "planner",
            lambda _ctx, _payload: PlanArtifact(
                goal="budget",
                tasks=[PlanTask(task_id="paid", description="paid call", capability="paid")],
            ),
            kind="llm",
        )
        .register_capability_spec(
            CapabilitySpec(name="paid", kind="llm", metered=True),
            paid,
        )
        .register_workflow(
            WorkflowBuilder("planned_budget").plan("make_plan", capability="planner").build()
        )
        .build()
    )

    result = await engine.run("planned_budget", {})

    assert result.status == "failed"
    assert calls["paid"] == 0
    assert result.output.tasks[0].status == "failed"
    assert "budget_exhausted" in (result.error or "")
