"""Flow-as-data (authored flows), bounded recursive planning, workflow-capability fanout, viz."""

import pytest

from ai_workflow_engine import (
    FlowArtifact,
    FlowNodeSpec,
    PlanArtifact,
    PlanTask,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowValidationError,
    workflow_to_html,
    workflow_to_mermaid,
)
from ai_workflow_engine.models import CapabilityResult, CapabilitySpec, SafetyPolicy, WorkflowProfile

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- bounded recursive planning

def _planner_engine(*, depth: int, total: int = 32):
    builder = WorkflowEngineBuilder()

    def top_planner(ctx, payload):
        return PlanArtifact(goal="audit", tasks=[
            PlanTask(task_id="t1", description="plain work", capability="work"),
            PlanTask(task_id="t2", description="delegate", capability="sub_planner"),
        ])

    def sub_planner(ctx, payload):
        return PlanArtifact(goal="sub", tasks=[
            PlanTask(task_id="s1", description="child work", capability="work"),
            PlanTask(task_id="s2", description="child work", capability="work"),
        ])

    calls = {"work": 0}

    def work(ctx, payload):
        calls["work"] += 1
        return {"n": calls["work"]}

    builder.register_capability("plan_it", top_planner, kind="llm")
    builder.register_capability(
        "sub_planner", sub_planner,
        spec=CapabilitySpec(name="sub_planner", kind="llm", metadata={"planner": True}),
    )
    builder.register_capability("work", work, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("planned").plan(
            "plan_it", max_plan_depth=depth, max_total_planned_tasks=total
        ).build()
    )
    return builder.build(), calls


async def test_recursive_planning_allowed_when_depth_budget_remains():
    engine, calls = _planner_engine(depth=2)
    result = await engine.run("planned", {"goal": "go"})

    assert result.status == "completed"
    plan = result.output
    assert [t.status for t in plan.tasks] == ["done", "done"]
    assert calls["work"] == 3  # t1 + child s1 + s2
    assert any(e.decision == "plan:subplan_started" for e in result.trace)
    assert any(e.decision == "plan:subplan_done" for e in result.trace)


async def test_recursive_planning_rejected_at_default_depth_one():
    engine, calls = _planner_engine(depth=1)
    result = await engine.run("planned", {"goal": "go"})

    assert result.status == "failed"
    assert "max_plan_depth=1" in (result.error or "")
    assert calls["work"] == 0  # validation aborts BEFORE any task executes
    assert any(e.decision == "plan:validation_failed" for e in result.trace)


async def test_cumulative_task_budget_caps_total_work_across_levels():
    engine, calls = _planner_engine(depth=2, total=2)
    result = await engine.run("planned", {"goal": "go"})

    # top t1 consumes 1, t2 (planner) consumes 1, child tasks exhausted -> failed loudly
    assert result.status in ("partial", "failed")
    assert calls["work"] == 1
    # exhaustion is loud on the child tasks' trace events + the subplan summary
    assert any("max_total_planned_tasks=2" in (e.error or "") for e in result.trace)
    assert any(e.decision == "plan:subplan_partial" for e in result.trace)


# ---------------------------------------------------------------- flow-as-data (authored flows)

def _authoring_engine():
    builder = WorkflowEngineBuilder()
    builder.register_capability("fetch", lambda ctx, p: {"text": str(p)}, kind="deterministic")
    builder.register_capability("route", lambda ctx, p: "long" if len(p["text"]) > 5 else "short",
                                kind="deterministic")
    builder.register_capability("summarize", lambda ctx, p: {"summary": p["text"][:5]},
                                kind="deterministic")
    builder.register_capability("expand", lambda ctx, p: {"summary": p["text"] + "!"},
                                kind="deterministic")
    builder.register_capability(
        "gate",
        lambda ctx, p: CapabilityResult(status="accepted", output=p),
        kind="deterministic",
    )
    builder.register_capability(
        "a_planner", lambda ctx, p: p,
        spec=CapabilitySpec(name="a_planner", kind="llm", metadata={"planner": True}),
    )
    builder.register_capability(
        "send_external",
        lambda ctx, p: p,
        spec=CapabilitySpec(name="send_external", kind="tool", side_effects=["network"]),
    )
    builder.register_capability(
        "author_flow",
        lambda ctx, p: p,
        spec=CapabilitySpec(name="author_flow", kind="llm", metadata={"flow_author": True}),
    )
    return builder.build()


def _artifact(**overrides):
    base = {
        "flow_id": "summarizer",
        "goal": "fetch then summarize",
        "nodes": [
            {"kind": "step", "id": "fetch"},
            {"kind": "branch", "id": "route",
             "branches": {"long": "summarize", "short": "expand"}},
            {"kind": "step", "id": "summarize"},
            {"kind": "evaluate", "id": "gate", "target": "summarize", "on_reject": "retry"},
            {"kind": "step", "id": "expand"},
        ],
    }
    base.update(overrides)
    return base


async def test_authored_flow_validates_compiles_and_runs():
    engine = _authoring_engine()
    result = await engine.run_authored_flow(_artifact(), "hello world")

    assert result.status == "completed"
    assert result.output == {"summary": "hello"}
    assert any(e.decision == "flow:authored" for e in engine.trace_sink.events)


async def test_authored_flow_rejects_violations_loudly_before_building():
    engine = _authoring_engine()
    bad = _artifact()
    bad["nodes"][0] = {"kind": "step", "id": "fetch", "capability": "ghost_cap"}
    bad["nodes"].append({"kind": "step", "id": "plan_more", "capability": "a_planner"})
    bad["nodes"].append({"kind": "evaluate", "id": "g2", "target": "summarize",
                         "on_reject": "retrace", "retrace_to": "nowhere"})

    with pytest.raises(WorkflowValidationError) as exc:
        await engine.run_authored_flow(bad, "x")
    message = str(exc.value)
    assert "ghost_cap" in message and "not registered" in message
    assert "a_planner" in message and "AI-writers" in message
    assert "nowhere" in message


async def test_authored_flow_node_cap():
    engine = _authoring_engine()
    with pytest.raises(WorkflowValidationError, match="max_nodes"):
        await engine.run_authored_flow(_artifact(), "x", max_nodes=2)


async def test_authored_flow_reports_branch_evaluate_side_effect_and_firewall_errors():
    engine = _authoring_engine()
    bad = FlowArtifact(
        flow_id="bad_validation_buckets",
        nodes=[
            FlowNodeSpec(kind="step", id="send_external"),
            FlowNodeSpec(kind="branch", id="route", branches={"bad": "missing"}),
            FlowNodeSpec(kind="evaluate", id="gate", target="summarize", on_reject="fallback"),
            FlowNodeSpec(kind="step", id="recursive_author", capability="author_flow"),
        ],
    )

    with pytest.raises(WorkflowValidationError) as exc:
        await engine.run_authored_flow(bad, "x")
    message = str(exc.value)
    assert "send_external" in message and "side effects denied: network" in message
    assert "branch 'bad' -> unknown node 'missing'" in message
    assert "on_reject=fallback requires fallback" in message
    assert "author_flow" in message and "recursion through generated structure is forbidden" in message


# ---------------------------------------------------------------- workflow capability fanout

async def test_fanout_over_workflow_capability_runs_children_in_parallel():
    builder = WorkflowEngineBuilder()
    builder.register_capability(
        "double",
        lambda ctx, p: (_ for _ in ()).throw(ValueError("boom")) if p == 13 else p * 2,
        kind="deterministic",
    )
    builder.register_capability("make_items", lambda ctx, p: [1, 13, 3], kind="deterministic")
    builder.register_workflow(WorkflowBuilder("child").step("double").build())
    builder.register_workflow(
        WorkflowBuilder("parent")
        .step("make_items")
        .fanout("run_children", capability="run_child_workflow", items_key="make_items")
        .build()
    )
    engine = builder.build()
    engine.register_workflow_capability("run_child_workflow", "child")

    result = await engine.run("parent", None)

    assert result.status == "partial"  # one child failed, isolated
    assert sorted(result.output) == [2, 6]


def test_register_workflow_capability_unknown_workflow_fails_loudly():
    engine = WorkflowEngineBuilder().build()
    with pytest.raises(ValueError, match="unknown workflow"):
        engine.register_workflow_capability("x", "ghost_flow")


# ---------------------------------------------------------------- visualizer

def test_workflow_to_mermaid_renders_structure_and_run_overlay():
    from ai_workflow_engine import Retrace

    definition = (
        WorkflowBuilder("viz_demo")
        .step("ingest")
        .branch("route", {"good": "process", "bad": "fallback_step"})
        .step("process")
        .evaluate("check", target="process", on_reject=Retrace("ingest"))
        .step("fallback_step")
        .build()
    )
    text = workflow_to_mermaid(definition)
    assert "flowchart TD" in text
    assert 'n_route{"route' in text          # branch shape
    assert "-->|good| n_process" in text      # labeled conditional edge
    assert "-.->|retrace \u22641| n_ingest" in text   # bounded back-edge, dashed
    assert 'n_check[/"check' in text          # evaluate shape

    class _Rec:
        def __init__(self, node_id, status, label=None):
            self.node_id, self.status, self.branch_label = node_id, status, label

    class _Result:
        node_results = [_Rec("ingest", "accepted"), _Rec("route", "accepted", "good"),
                        _Rec("process", "failed")]

    overlay = workflow_to_mermaid(definition, _Result())
    assert "class n_ingest ok;" in overlay
    assert "class n_process fail;" in overlay
    assert "-->|good ✓| n_process" in overlay  # taken branch check-marked

    html = workflow_to_html(definition, _Result())
    assert "<html>" in html and "mermaid" in html and "viz_demo" in html
