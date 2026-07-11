"""v0.6.2 fix-wave regressions: B1 stale machine, B2 nested suspension, B3 fanout budget,
B5 session-bundle finalize, B6 envelope trace with file sinks."""

import asyncio

import pytest

from ai_workflow_engine import (
    LocalWaitPolicy,
    JsonlTraceSink,
    WorkflowBuilder,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.models import CapabilityResult, EvaluationDecision
from ai_workflow_engine.observation_bundle import open_observation_run_bundle
from ai_workflow_engine.run_session import WorkflowRunSession
from ai_workflow_engine.workflow import WorkflowValidationError  # noqa: F401 (parity with guards)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- B1: machine identity
def test_reregistered_same_id_definition_executes_the_new_machine():
    calls = []
    builder = WorkflowEngineBuilder()

    async def first(context, payload):
        calls.append("first")
        return {"who": "first"}

    async def second(context, payload):
        calls.append("second")
        return {"who": "second"}

    builder.register_capability("first", first, kind="deterministic")
    builder.register_capability("second", second, kind="deterministic")
    engine = builder.build()

    flow_v1 = WorkflowBuilder("same_id_flow").step("first").build()
    flow_v2 = WorkflowBuilder("same_id_flow").step("second").build()

    async def scenario():
        r1 = await engine.run(flow_v1, {"seed": 1})
        r2 = await engine.run(flow_v2, {"seed": 2})
        return r1, r2

    r1, r2 = asyncio.run(scenario())

    assert r1.output == {"who": "first"}
    # The B1 bug executed the STALE compiled graph here (calls == ["first", "first"]).
    assert r2.output == {"who": "second"}
    assert calls == ["first", "second"]
    # Registries agree about the current machine for the id.
    assert engine.workflows["same_id_flow"].definition_digest() == flow_v2.definition_digest()
    assert engine.executor.subworkflows["same_id_flow"].definition_digest() == flow_v2.definition_digest()


# ---------------------------------------------------------------- B2: nested suspension
def _noop(context, payload):
    return {"ok": True}


def test_preflight_rejects_human_node_inside_subworkflow():
    builder = WorkflowEngineBuilder()
    builder.register_capability("prepare", _noop, kind="deterministic")
    builder.register_capability("ask", _noop, kind="deterministic")
    builder.register_capability("after", _noop, kind="deterministic")
    child = WorkflowBuilder("child_with_human").human("ask", wait_policy=LocalWaitPolicy()).build()
    parent = (
        WorkflowBuilder("parent_flow")
        .step("prepare")
        .subworkflow("delegate", workflow=child)
        .step("after")
        .build()
    )
    engine = builder.build()
    engine.register_workflow(child)
    engine.register_workflow(parent)

    result = asyncio.run(engine.run("parent_flow", {"seed": 1}))

    assert result.status == "failed"
    assert "nested suspension is not supported" in (result.error or "")


def test_runtime_child_suspension_fails_loudly_instead_of_accepting():
    calls = []
    builder = WorkflowEngineBuilder()

    async def work(context, payload):
        return {"draft": True}

    async def judge(context, payload):
        return CapabilityResult(
            status="rejected",
            output=EvaluationDecision(action="ask_user", rationale="need a human"),
        )

    async def after(context, payload):
        calls.append("after")
        return {"done": True}

    builder.register_capability("work", work, kind="deterministic")
    builder.register_capability("judge", judge, kind="deterministic")
    builder.register_capability("after", after, kind="deterministic")
    # Child suspends at runtime via an evaluator ask_user (no declared human node,
    # so preflight cannot catch it — the subworkflow node must).
    child = WorkflowBuilder("suspending_child").step("work").evaluate("judge").build()
    parent = (
        WorkflowBuilder("parent_of_suspender")
        .subworkflow("delegate", workflow=child)
        .step("after")
        .build()
    )
    engine = builder.build()
    engine.register_workflow(child)
    engine.register_workflow(parent)

    result = asyncio.run(engine.run("parent_of_suspender", {"seed": 1}))

    assert result.status == "failed"
    delegate = result.node("delegate")
    assert delegate is not None and delegate.status == "failed"
    assert "nested suspension is not supported" in (delegate.error or "")
    assert calls == []  # the parent did NOT continue past the lost wait


# ---------------------------------------------------------------- B3: fanout budget
def test_fanout_planner_enforces_cumulative_task_budget():
    invoked = []
    builder = WorkflowEngineBuilder()

    async def planner(context, payload):
        return {
            "goal": "spread",
            "tasks": [
                {"task_id": f"t{i}", "description": f"task {i}", "capability": "item"}
                for i in range(4)
            ],
        }

    async def item(context, payload):
        invoked.append(payload)
        return {"ok": True}

    builder.register_capability("plan_it", planner, kind="llm")
    builder.register_capability("item", item, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("fanout_budget_flow")
        .plan("plan_it", execution="fanout", max_tasks=8, max_total_planned_tasks=2)
        .build()
    )
    engine = builder.build()

    result = asyncio.run(engine.run("fanout_budget_flow", {"seed": 1}))

    assert len(invoked) == 2, f"budget bypassed: {len(invoked)} tasks invoked"
    plan = result.node("plan_it").output
    statuses = {task.task_id: task.status for task in plan.tasks}
    assert sorted(statuses.values()) == ["done", "done", "failed", "failed"]
    over_budget_errors = [
        task.error for task in plan.tasks if task.status == "failed"
    ]
    assert all("max_total_planned_tasks" in (err or "") for err in over_budget_errors)


# ---------------------------------------------------------------- B5: real-bundle close
def test_session_close_finalizes_a_real_observation_bundle(tmp_path):
    definition = WorkflowBuilder("bundle_flow").step("s").build()
    bundle = open_observation_run_bundle(tmp_path, "run-b5")
    from ai_workflow_engine.models import CapabilityContext, WorkflowGoal, WorkflowRunContext

    session = WorkflowRunSession(
        workflow_id="bundle_flow",
        context=CapabilityContext(
            goal=WorkflowGoal(workflow_type="t", objective="o"),
            run_context=WorkflowRunContext(workflow_id="run-b5", workflow_type="t"),
        ),
        bundle=bundle,
        definition=definition,
    )

    session.close("failed")

    import json

    meta = json.loads((tmp_path / "run-b5" / "meta.json").read_text())
    assert meta["status"] == "failed"
    assert meta["workflow_id"] == "bundle_flow"


# ---------------------------------------------------------------- B6: file-sink envelope trace
def test_result_trace_is_populated_with_jsonl_only_sink(tmp_path):
    builder = WorkflowEngineBuilder().with_trace_sink(JsonlTraceSink(tmp_path / "trace.jsonl"))
    builder.register_capability("solo", _noop, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("jsonl_flow").step("solo").build())
    engine = builder.build()

    result = asyncio.run(engine.run("jsonl_flow", {"seed": 1}))

    # The B6 bug: result.trace was ALWAYS [] for file-backed sinks.
    assert result.trace, "envelope trace empty with Jsonl sink"
    assert {event.run_id for event in result.trace} == {result.trace[0].run_id}
    decisions = {event.decision for event in result.trace}
    assert "accepted" in decisions or "start" in decisions
