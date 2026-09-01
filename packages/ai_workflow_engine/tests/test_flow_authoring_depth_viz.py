"""Flow-as-data (authored flows), bounded recursive planning, workflow-capability fanout, viz."""

import pytest

from ai_workflow_engine import (
    parse_flow_node,
    FlowArtifact,
    FlowNodeSpec,
    PlanArtifact,
    StepFlowNode,
    PlanTask,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowValidationError,
)
from ai_workflow_engine.models import CapabilityResult, CapabilitySpec, SafetyPolicy, WorkflowProfile
from ai_workflow_viewer import workflow_to_html, workflow_to_mermaid

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
        spec=CapabilitySpec(name="sub_planner", kind="llm", is_planner=True),
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
        spec=CapabilitySpec(name="a_planner", kind="llm", is_planner=True),
    )
    builder.register_capability(
        "send_external",
        lambda ctx, p: p,
        spec=CapabilitySpec(name="send_external", kind="tool", side_effects=["network"]),
    )
    builder.register_capability(
        "author_flow",
        lambda ctx, p: p,
        # v0.9 clean contract: the typed is_flow_author field IS the marker (the old
        # metadata={"flow_author": True} read was removed).
        spec=CapabilitySpec(name="author_flow", kind="llm", is_flow_author=True),
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
            parse_flow_node({"kind": "step", "id": "send_external"}),
            parse_flow_node({"kind": "branch", "id": "route", "branches": {"bad": "missing"}}),
            parse_flow_node({"kind": "evaluate", "id": "gate", "target": "summarize", "on_reject": "fallback"}),
            parse_flow_node({"kind": "step", "id": "recursive_author", "capability": "author_flow"}),
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


# ------------------------------------------------- authored fanout (FlowArtifact v1.5a, task 2.3)

from pydantic import ValidationError as _PydanticValidationError  # noqa: E402

from ai_workflow_engine.models import RuntimeLimits as _RuntimeLimits  # noqa: E402


def _fanout_engine(*, limits: "_RuntimeLimits | None" = None, fail_on: str | None = "b"):
    """Engine with a collector + a per-item analyzer (fails on one item to prove isolation)."""

    analyzed: list = []
    builder = WorkflowEngineBuilder()
    builder.register_capability(
        "collect", lambda ctx, p: {"frames": ["a", "b", "c"]}, kind="deterministic"
    )

    def analyze(ctx, item):
        if fail_on is not None and item == fail_on:
            raise RuntimeError(f"analyzer choked on {item!r}")
        analyzed.append(item)
        return f"analyzed:{item}"

    builder.register_capability("analyze", analyze, kind="deterministic")
    builder.register_capability(
        "a_planner", lambda ctx, p: p,
        spec=CapabilitySpec(name="a_planner", kind="llm", is_planner=True),
    )
    profile = WorkflowProfile(
        workflow_type="fanout_authoring",
        limits=limits or _RuntimeLimits(),
        safety=SafetyPolicy(allowed_side_effects=["read_only"]),
    )
    engine = builder.with_profile(profile).build()
    return engine, analyzed


def _fanout_artifact(**fanout_overrides):
    fanout_node = {
        "kind": "fanout",
        "id": "analyze_frames",
        "capability": "analyze",
        "items_key": "collect.frames",
        "max_parallel": 2,
        "max_items": 5,
    }
    fanout_node.update(fanout_overrides)
    return {
        "flow_id": "frame_scan",
        "goal": "collect frames then analyze each",
        "nodes": [{"kind": "step", "id": "collect"}, fanout_node],
    }


async def test_authored_fanout_compiles_runs_bounded_with_partial_isolation():
    engine, analyzed = _fanout_engine(fail_on="b")

    result = await engine.run_authored_flow(_fanout_artifact(), {"source": "camera"})

    assert result.status == "partial", "one failed item must isolate, not sink the fanout"
    assert sorted(result.output) == ["analyzed:a", "analyzed:c"]
    assert sorted(analyzed) == ["a", "c"]
    fanout_events = [e for e in engine.trace_sink.events if e.decision == "fanout"]
    assert fanout_events and fanout_events[0].metadata["max_parallel"] == 2
    assert fanout_events[0].metadata == {
        **fanout_events[0].metadata, "total": 3, "succeeded": 2, "failed": 1,
    }


async def test_authored_fanout_requires_max_items():
    engine, _ = _fanout_engine()
    artifact = _fanout_artifact()
    del artifact["nodes"][1]["max_items"]

    with pytest.raises(WorkflowValidationError, match="REQUIRES max_items"):
        await engine.run_authored_flow(artifact, {})


async def test_authored_fanout_rejects_non_positive_and_over_cap_max_items():
    small_cap = _RuntimeLimits(max_authored_fanout_items=5)
    engine, _ = _fanout_engine(limits=small_cap)

    with pytest.raises(WorkflowValidationError, match="max_items=0 must be between 1 and"):
        await engine.run_authored_flow(_fanout_artifact(max_items=0), {})
    with pytest.raises(
        WorkflowValidationError, match="max_items=6 must be between 1 and .*=5"
    ):
        await engine.run_authored_flow(_fanout_artifact(max_items=6), {})


async def test_authored_fanout_rejects_invalid_max_parallel_never_clamps():
    engine, _ = _fanout_engine()  # default max_parallel_children = 4

    with pytest.raises(WorkflowValidationError, match="max_parallel=0 must be between 1 and"):
        await engine.run_authored_flow(_fanout_artifact(max_parallel=0), {})
    with pytest.raises(
        WorkflowValidationError, match="max_parallel=99 .* never silently clamped"
    ):
        await engine.run_authored_flow(_fanout_artifact(max_parallel=99), {})


async def test_authored_fanout_firewall_registration_and_side_effect_buckets():
    engine, _ = _fanout_engine()

    with pytest.raises(WorkflowValidationError, match="not registered"):
        await engine.run_authored_flow(_fanout_artifact(capability="ghost_analyzer"), {})
    with pytest.raises(WorkflowValidationError, match="AI-writers"):
        await engine.run_authored_flow(_fanout_artifact(capability="a_planner"), {})
    with pytest.raises(WorkflowValidationError, match="non-empty items_key"):
        await engine.run_authored_flow(_fanout_artifact(items_key=""), {})


async def test_authored_unknown_fields_are_forbidden():
    engine, _ = _fanout_engine()
    artifact = _fanout_artifact()
    artifact["nodes"][1]["max_itemz"] = 3  # AI typo must FAIL validation, not vanish

    with pytest.raises(_PydanticValidationError, match="max_itemz"):
        await engine.run_authored_flow(artifact, {})


async def test_irrelevant_per_kind_fields_rejected_both_directions():
    """R6: per-kind field discipline lives in the discriminated SCHEMA — foreign fields fail
    pydantic validation with a useful location, in both directions."""

    engine, _ = _fanout_engine()

    step_with_fanout_fields = {
        "flow_id": "bad_step",
        "nodes": [{"kind": "step", "id": "collect", "items_key": "x", "max_items": 3}],
    }
    with pytest.raises(_PydanticValidationError, match="items_key"):
        await engine.run_authored_flow(step_with_fanout_fields, {})

    fanout_with_branch_fields = _fanout_artifact(branches={"x": "collect"})
    with pytest.raises(_PydanticValidationError, match="branches"):
        await engine.run_authored_flow(fanout_with_branch_fields, {})


async def test_runtime_oversize_item_list_fails_loudly_never_truncates():
    engine, analyzed = _fanout_engine(fail_on=None)
    engine.register_capability(
        "collect_many",
        lambda ctx, p: {"frames": ["a", "b", "c", "d"]},
        kind="deterministic",
    )
    artifact = {
        "flow_id": "oversize",
        "goal": "declared bound must hold at run time",
        "nodes": [
            {"kind": "step", "id": "collect_many"},
            {
                "kind": "fanout",
                "id": "analyze_frames",
                "capability": "analyze",
                "items_key": "collect_many.frames",
                "max_items": 3,
            },
        ],
    }

    result = await engine.run_authored_flow(artifact, {})

    assert result.status == "failed"
    assert "exceeding its declared max_items=3" in (result.error or "")
    assert "refusing to truncate" in (result.error or "")
    assert analyzed == [], "no item may be processed once the declared bound is exceeded"


async def test_hand_written_fanout_unchanged_without_max_items():
    builder = WorkflowEngineBuilder()
    builder.register_capability(
        "spread", lambda ctx, p: {"items": list(range(7))}, kind="deterministic"
    )
    builder.register_capability("double", lambda ctx, item: item * 2, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("hand_fanout")
        .step("spread")
        .fanout("doubler", capability="double", items_key="spread.items")
        .build()
    )
    engine = builder.build()

    result = await engine.run("hand_fanout", {})

    assert result.status == "completed"
    assert sorted(result.output) == [0, 2, 4, 6, 8, 10, 12]


# --------------------------------------- turnkey author + two-engine proof (tasks 2.4/2.5/2.1)

import json as _json  # noqa: E402

from ai_workflow_engine import LLMResponse, build_flow_author_capability  # noqa: E402
from ai_workflow_engine.engine import InMemoryDetailSink as _DetailSink  # noqa: E402


class ScriptedAuthorLLM:
    """Plain-callable client returning scripted texts in order (repeats the last one)."""

    provider_label = "scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        text = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        return LLMResponse(text=text, input_tokens=7, output_tokens=13, total_tokens=20)


def _good_artifact_json(**fanout_overrides):
    fanout = {
        "kind": "fanout",
        "id": "analyze_frames",
        "capability": "analyze",
        "items_key": "collect.frames",
        "max_parallel": 2,
        "max_items": 5,
    }
    fanout.update(fanout_overrides)
    return _json.dumps(
        {
            "flow_id": "frame_scan",
            "goal": "collect frames then analyze each",
            "nodes": [{"kind": "step", "id": "collect"}, fanout],
        }
    )


def _author_engine_for(processing_engine, llm, **factory_overrides):
    """Engine A hosting the PUBLIC factory built against engine B's target contract."""

    target_limits = (
        processing_engine.default_profile.limits
        if processing_engine.default_profile is not None
        else None
    )
    kwargs = dict(
        registry=processing_engine.registry,
        model_profiles=processing_engine.model_profiles,
        limits=target_limits,
        allowed_side_effects=["read_only"],
        max_repair_rounds=1,
    )
    kwargs.update(factory_overrides)
    spec, handler = build_flow_author_capability(llm, **kwargs)

    builder = WorkflowEngineBuilder()
    builder.register_capability_spec(spec, handler)
    builder.register_workflow(WorkflowBuilder("authoring").step("author_flow").build())
    return builder.build(), spec


async def test_two_engine_proof_public_factory_authors_and_peer_engine_runs():
    """Task 2.1: engine A's PUBLIC author capability emits the artifact; REAL engine B
    validates and executes it — trace/usage intact, registry isolation loud, firewall locked."""

    processing, analyzed = _fanout_engine(fail_on=None)
    llm = ScriptedAuthorLLM([_good_artifact_json()])
    author_engine, spec = _author_engine_for(processing, llm)

    authored = await author_engine.run("authoring", {"goal": "scan the frames", "context": {}})

    assert authored.status == "completed"
    artifact_dict = authored.output
    assert artifact_dict["flow_id"] == "frame_scan"
    assert spec.is_flow_author is True
    assert len(authored.usage.events) == 1, "authoring attempt must be metered on engine A"

    executed = await processing.run_authored_flow(artifact_dict, {"source": "camera"})

    assert executed.status == "completed"
    assert sorted(executed.output) == ["analyzed:a", "analyzed:b", "analyzed:c"]
    assert sorted(analyzed) == ["a", "b", "c"]
    assert any(e.decision == "flow:authored" for e in processing.trace_sink.events)
    assert any(e.decision == "fanout" for e in processing.trace_sink.events)


async def test_two_engine_registry_isolation_rejects_foreign_capability():
    processing, _ = _fanout_engine()
    foreign = _json.loads(_good_artifact_json())
    foreign["nodes"][1]["capability"] = "ghost_only_engine_c_has_this"

    with pytest.raises(WorkflowValidationError, match="not registered"):
        await processing.run_authored_flow(foreign, {})


async def test_two_engine_firewall_rejects_author_capability_inside_authored_flow():
    processing, _ = _fanout_engine()
    spec, handler = build_flow_author_capability(
        ScriptedAuthorLLM(["{}"]),
        registry=processing.registry,
        allowed_side_effects=["read_only"],
    )
    processing.register_capability_spec(spec, handler)
    recursive = {
        "flow_id": "recursive",
        "nodes": [{"kind": "step", "id": "meta", "capability": "author_flow"}],
    }

    with pytest.raises(WorkflowValidationError, match="recursion through generated structure"):
        await processing.run_authored_flow(recursive, {})


async def test_stale_artifact_rejected_when_target_limits_shrink_after_authoring():
    """Task 2.1(d): execution revalidates against the CURRENT target contract."""

    processing, _ = _fanout_engine()
    llm = ScriptedAuthorLLM([_good_artifact_json()])  # declares max_items=5
    author_engine, _spec = _author_engine_for(processing, llm)
    authored = await author_engine.run("authoring", {"goal": "scan", "context": {}})

    processing.default_profile = processing.default_profile.model_copy(
        update={"limits": _RuntimeLimits(max_authored_fanout_items=3)}
    )

    with pytest.raises(WorkflowValidationError, match="max_items=5 must be between 1 and .*=3"):
        await processing.run_authored_flow(authored.output, {})


async def test_author_repair_loop_feeds_validation_errors_and_recovers():
    """Task 2.5(a): invalid first attempt → repair prompt carries the verbatim error →
    second attempt is valid AND runs on the processing engine."""

    processing, analyzed = _fanout_engine(fail_on=None)
    bad = _good_artifact_json()
    bad = bad.replace('"capability": "analyze"', '"capability": "ghost_analyzer"')
    llm = ScriptedAuthorLLM([bad, _good_artifact_json()])
    author_engine, _spec = _author_engine_for(processing, llm)

    authored = await author_engine.run("authoring", {"goal": "scan", "context": {}})

    assert authored.status == "completed"
    assert len(llm.requests) == 2
    repair_prompt = llm.requests[1].user
    assert "ghost_analyzer" in repair_prompt and "not registered" in repair_prompt, (
        "the repair prompt must carry the target-registry validation error verbatim"
    )
    executed = await processing.run_authored_flow(authored.output, {})
    assert executed.status == "completed"
    assert sorted(analyzed) == ["a", "b", "c"]


async def test_author_exhaustion_fails_loudly_with_final_errors_and_per_attempt_usage():
    """Task 2.5(b): always-invalid LLM → loud failure after exactly 1 + max_repair_rounds
    attempts, one usage event per attempt, no double counting."""

    processing, _ = _fanout_engine()
    bad = _good_artifact_json().replace('"capability": "analyze"', '"capability": "ghost"')
    llm = ScriptedAuthorLLM([bad])
    author_engine, _spec = _author_engine_for(processing, llm, max_repair_rounds=2)

    result = await author_engine.run("authoring", {"goal": "scan", "context": {}})

    assert result.status == "failed"
    assert "ghost" in (result.error or "") and "not registered" in (result.error or "")
    assert len(llm.requests) == 3, "exactly 1 + max_repair_rounds attempts"
    assert len(result.usage.events) == 3, "every attempt metered exactly once"


async def test_author_rejects_byte_unsafe_context_and_data_uri_output():
    """Task 2.5(c): privacy edges on BOTH sides of the boundary."""

    processing, _ = _fanout_engine()
    llm = ScriptedAuthorLLM([_good_artifact_json()])
    author_engine, _spec = _author_engine_for(processing, llm)

    unsafe_context = await author_engine.run(
        "authoring", {"goal": "scan", "context": {"frame": b"raw-bytes"}}
    )
    assert unsafe_context.status == "failed"
    assert "raw bytes" in (unsafe_context.error or "")
    assert llm.requests == [], "unsafe context must be rejected BEFORE any prompt is sent"

    data_uri_goal = _json.loads(_good_artifact_json())
    data_uri_goal["goal"] = "data:image/png;base64,AAAA"
    llm2 = ScriptedAuthorLLM([_json.dumps(data_uri_goal)])
    author_engine2, _ = _author_engine_for(processing, llm2)
    result = await author_engine2.run("authoring", {"goal": "scan", "context": {}})
    assert result.status == "failed"
    assert "data URI" in (result.error or "")


async def test_author_observation_detail_contains_prompt_and_response():
    """Task 2.5(d): the authoring attempt is observable — rendered prompt + response text."""

    processing, _ = _fanout_engine(fail_on=None)
    llm = ScriptedAuthorLLM([_good_artifact_json()])
    target_limits = processing.default_profile.limits
    spec, handler = build_flow_author_capability(
        llm,
        registry=processing.registry,
        limits=target_limits,
        allowed_side_effects=["read_only"],
    )
    detail_sink = _DetailSink()
    builder = WorkflowEngineBuilder().with_detail_sink(detail_sink).with_detail_text_capture(True)
    builder.register_capability_spec(spec, handler)
    builder.register_workflow(WorkflowBuilder("authoring").step("author_flow").build())
    author_engine = builder.build()

    await author_engine.run("authoring", {"goal": "find the odd frame", "context": {}})

    payloads = _json.dumps([d.body.model_dump() for d in detail_sink.details], default=str)
    assert "find the odd frame" in payloads, "rendered prompt must be captured"
    assert "frame_scan" in payloads, "LLM response must be captured"


async def test_author_budget_blocks_repair_attempt_before_client_call():
    """Task 2.5(e): a text-call ceiling denies the SECOND (repair) attempt pre-call."""

    processing, _ = _fanout_engine()
    bad = _good_artifact_json().replace('"capability": "analyze"', '"capability": "ghost"')
    llm = ScriptedAuthorLLM([bad])
    target_limits = processing.default_profile.limits
    spec, handler = build_flow_author_capability(
        llm,
        registry=processing.registry,
        limits=target_limits,
        allowed_side_effects=["read_only"],
        max_repair_rounds=2,
    )
    profile = WorkflowProfile(
        workflow_type="authoring",
        limits=_RuntimeLimits(max_text_calls=1),
        safety=SafetyPolicy(allowed_side_effects=["read_only"]),
    )
    builder = WorkflowEngineBuilder().with_profile(profile)
    builder.register_capability_spec(spec, handler)
    builder.register_workflow(WorkflowBuilder("authoring").step("author_flow").build())
    author_engine = builder.build()

    result = await author_engine.run("authoring", {"goal": "scan", "context": {}})

    assert result.status == "failed"
    assert "max_text_calls" in (result.error or "")
    assert len(llm.requests) == 1, "the repair attempt must be denied BEFORE the client is called"


async def test_author_default_prompt_contract_and_wholesale_override():
    """Task 2.5(f): first request carries catalog, goal, schema, and the effective bounds;
    prompt_template= replaces the default wholesale and still renders strictly."""

    processing, _ = _fanout_engine(
        limits=_RuntimeLimits(max_parallel_children=3, max_authored_fanout_items=7)
    )
    llm = ScriptedAuthorLLM([_good_artifact_json(max_parallel=2, max_items=7)])
    author_engine, _spec = _author_engine_for(processing, llm)

    await author_engine.run("authoring", {"goal": "scan the odd frames", "context": {"cam": 3}})

    first = llm.requests[0].user
    assert "scan the odd frames" in first, "goal missing from the default prompt"
    assert "- analyze (deterministic)" in first, "target capability catalog missing"
    assert "FlowArtifact" in first or "flow_id" in first, "output schema missing"
    assert "at most 12 nodes" in first
    assert "1..7" in first and "1..3" in first, "effective fanout bounds missing"
    assert '"cam": 3' in first, "request context missing"

    override = ScriptedAuthorLLM([_good_artifact_json(max_parallel=2, max_items=7)])
    engine2, _ = _author_engine_for(
        processing,
        override,
        prompt_template=(
            "CUSTOM AUTHOR {goal} | {context} | {catalog} | {max_nodes} | "
            "{max_items_cap} | {max_parallel_cap} | {format_instructions}"
        ),
    )
    await engine2.run("authoring", {"goal": "custom run", "context": {}})
    assert override.requests[0].user.startswith("CUSTOM AUTHOR custom run")


# ---------------------------------------------------- Phase R2: input/limit boundary hardening


async def test_author_goal_data_uri_rejected_before_any_prompt():
    """R2 (review finding #2): request.goal feeds the prompt and must be byte-safe like
    context — pre-fix it reached the model unchecked."""

    processing, _ = _fanout_engine()
    llm = ScriptedAuthorLLM([_good_artifact_json()])
    author_engine, _spec = _author_engine_for(processing, llm)

    result = await author_engine.run(
        "authoring", {"goal": "data:image/png;base64,AAAA", "context": {}}
    )

    assert result.status == "failed"
    assert "flow_author.goal" in (result.error or "")
    assert llm.requests == [], "unsafe goal must be rejected BEFORE any prompt is sent"


def test_max_parallel_children_rejects_non_positive_by_schema():
    """R2 (review finding #4): zero/negative structural concurrency is a config error —
    never silently rewritten to a default by a falsy fallback."""

    with pytest.raises(_PydanticValidationError):
        _RuntimeLimits(max_parallel_children=0)
    with pytest.raises(_PydanticValidationError):
        _RuntimeLimits(max_parallel_children=-1)


async def test_configured_parallel_cap_is_used_exactly_never_rewritten():
    """A non-default cap (2) must bound authored max_parallel at exactly 2 — proving no
    `or 4` literal rewrites the configured value anywhere on the validation path."""

    engine, _ = _fanout_engine(limits=_RuntimeLimits(max_parallel_children=2))

    with pytest.raises(
        WorkflowValidationError, match="max_parallel=3 must be between 1 and .*=2"
    ):
        await engine.run_authored_flow(_fanout_artifact(max_parallel=3), {})

    result = await engine.run_authored_flow(
        _fanout_artifact(max_parallel=2), {"source": "cam"}
    )
    assert result.status in ("completed", "partial")


# ------------------------------------------------------- Phase R3: typed AI-writer firewall


def test_capability_spec_rejects_typo_marker_loudly():
    """R3: a typo'd firewall marker must fail at registration, never silently vanish."""

    with pytest.raises(_PydanticValidationError, match="is_planer"):
        CapabilitySpec(name="p", kind="llm", is_planer=True)


def test_legacy_metadata_planner_marker_is_no_longer_a_firewall_input():
    """Clean v0.9 contract: metadata['planner'] and handler attributes are NOT markers —
    only the typed field counts, in BOTH the firewall and the catalog."""

    from ai_workflow_engine.flow_authoring import render_capability_catalog

    builder = WorkflowEngineBuilder()
    builder.register_capability(
        "typed_planner", lambda ctx, p: p,
        spec=CapabilitySpec(name="typed_planner", kind="llm", is_planner=True),
    )
    builder.register_capability(
        "legacy_marked", lambda ctx, p: p,
        spec=CapabilitySpec(name="legacy_marked", kind="llm", metadata={"planner": True}),
    )
    engine = builder.build()

    catalog = render_capability_catalog(engine.registry, ["read_only"])
    typed_line = next(l for l in catalog.splitlines() if "typed_planner" in l)
    legacy_line = next(l for l in catalog.splitlines() if "legacy_marked" in l)
    assert "NOT-AUTHORABLE" in typed_line
    assert "NOT-AUTHORABLE" not in legacy_line


# ------------------------------------------------ Phase R4: prompt-override contract locked


async def test_reduced_goal_only_override_renders_and_runs():
    """R4 (review #9 disproved-then-locked): a wholesale override may reference FEWER
    variables than the default — LangChain renders it; the contract test pins that."""

    processing, analyzed = _fanout_engine(fail_on=None)
    llm = ScriptedAuthorLLM([_good_artifact_json()])
    author_engine, _spec = _author_engine_for(
        processing, llm, prompt_template="ONLY {goal}\n{format_instructions}"
    )

    authored = await author_engine.run("authoring", {"goal": "scan frames", "context": {}})

    assert authored.status == "completed"
    assert llm.requests[0].user.startswith("ONLY scan frames")
    executed = await processing.run_authored_flow(authored.output, {})
    assert executed.status == "completed"


async def test_unknown_placeholder_in_override_fails_before_provider():
    """A template referencing an UNDEFINED variable must fail loudly pre-call."""

    processing, _ = _fanout_engine()
    llm = ScriptedAuthorLLM([_good_artifact_json()])
    author_engine, _spec = _author_engine_for(
        processing, llm, prompt_template="Broken {nonexistent_var} {format_instructions}"
    )

    result = await author_engine.run("authoring", {"goal": "scan", "context": {}})

    assert result.status == "failed"
    assert "nonexistent_var" in (result.error or "")
    assert llm.requests == [], "template errors must never reach the provider"


# --------------------------------- Phase R6: normal serializable discriminated FlowArtifact


def _per_kind_artifact() -> FlowArtifact:
    return FlowArtifact(
        flow_id="round_trip",
        goal="cover every kind",
        nodes=[
            {"kind": "step", "id": "fetch"},
            {"kind": "branch", "id": "route",
             "branches": {"long": "summarize", "short": "expand"},
             "branch_bounds": {"long": 2}, "describe": {"long": "take when long"}},
            {"kind": "step", "id": "summarize"},
            {"kind": "evaluate", "id": "gate", "target": "summarize", "on_reject": "retry"},
            {"kind": "step", "id": "expand"},
            {"kind": "fanout", "id": "spread", "capability": "analyze",
             "items_key": "fetch.frames", "max_parallel": 2, "max_items": 5},
        ],
        metadata={"origin": "test"},
    )


def test_flow_artifact_ordinary_round_trip_preserves_validity_and_equality():
    """R6 (review finding #17): model_dump()->model_validate() and the JSON pair must
    round-trip EVERY node kind without changing validity — no exclude_unset ritual."""

    artifact = _per_kind_artifact()

    via_python = FlowArtifact.model_validate(artifact.model_dump())
    via_json = FlowArtifact.model_validate_json(artifact.model_dump_json())

    assert via_python == artifact
    assert via_json == artifact
    # the round-tripped artifact still validates against a registry with these capabilities
    kinds = [node.kind for node in via_python.nodes]
    assert kinds == ["step", "branch", "step", "evaluate", "step", "fanout"]


async def test_round_tripped_artifact_executes_on_peer_engine():
    """The serialized handoff: dump on engine A's side, validate+run on engine B."""

    processing, analyzed = _fanout_engine(fail_on=None)
    artifact = FlowArtifact.model_validate(_fanout_artifact())

    wire_dict = artifact.model_dump()
    rehydrated = FlowArtifact.model_validate_json(artifact.model_dump_json())
    assert rehydrated == artifact

    result = await processing.run_authored_flow(wire_dict, {"source": "camera"})

    assert result.status == "completed"
    assert sorted(analyzed) == ["a", "b", "c"]


# ------------------------------ Phase R7: run-scoped provenance + direct-entry byte-safety


async def test_flow_authored_provenance_lives_inside_the_run():
    """R7 (review finding #18): flow:authored is recorded THROUGH the run session —
    run-id stamped, present in result.trace, and never an orphan on the global sink."""

    processing, _ = _fanout_engine(fail_on=None)

    result = await processing.run_authored_flow(_fanout_artifact(), {"source": "cam"})

    in_result = [e for e in result.trace if e.decision == "flow:authored"]
    assert len(in_result) == 1, "exactly one provenance event in the run result"
    assert in_result[0].run_id, "provenance must carry the run id"
    assert in_result[0].metadata["flow_id"] == "frame_scan"

    in_global = [e for e in processing.trace_sink.events if e.decision == "flow:authored"]
    assert len(in_global) == 1, "no orphan duplicate on the global sink"
    assert in_global[0].run_id == in_result[0].run_id


async def test_flow_authored_provenance_reaches_the_observation_bundle(tmp_path):
    from ai_workflow_engine import ObservationConfig, WorkflowEngineBuilder as _WEB

    builder = _WEB()
    builder.register_capability(
        "collect", lambda ctx, p: {"frames": ["a"]}, kind="deterministic"
    )
    builder.register_capability("analyze", lambda ctx, item: f"ok:{item}", kind="deterministic")
    profile = WorkflowProfile(
        workflow_type="fanout_authoring",
        limits=_RuntimeLimits(),
        safety=SafetyPolicy(allowed_side_effects=["read_only"]),
    )
    engine = builder.with_profile(profile).with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(tmp_path / "bundles"))
    ).build()

    result = await engine.run_authored_flow(_fanout_artifact(), {"source": "cam"})

    assert result.observation_bundle_path
    from pathlib import Path as _P

    trace_lines = (_P(result.observation_bundle_path) / "trace.jsonl").read_text()
    assert "flow:authored" in trace_lines, "provenance must be part of the run's bundle"


async def test_direct_run_authored_flow_rejects_unsafe_artifact_before_anything(tmp_path):
    """R7 (review finding #19): the PUBLIC execution entry validates the complete artifact —
    a data-URI goal or byte-bearing metadata fails before compile, trace, or bundle writes."""

    from ai_workflow_engine import ObservationConfig, WorkflowEngineBuilder as _WEB

    builder = _WEB()
    builder.register_capability(
        "collect", lambda ctx, p: {"frames": ["a"]}, kind="deterministic"
    )
    builder.register_capability("analyze", lambda ctx, item: item, kind="deterministic")
    profile = WorkflowProfile(
        workflow_type="fanout_authoring",
        limits=_RuntimeLimits(),
        safety=SafetyPolicy(allowed_side_effects=["read_only"]),
    )
    bundles = tmp_path / "bundles"
    engine = builder.with_profile(profile).with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(bundles))
    ).build()

    unsafe_goal = _fanout_artifact()
    unsafe_goal["goal"] = "data:image/png;base64,AAAA"
    with pytest.raises(ValueError, match="data URI"):
        await engine.run_authored_flow(unsafe_goal, {})

    unsafe_meta = _fanout_artifact()
    unsafe_meta["metadata"] = {"thumb": b"raw-bytes"}
    with pytest.raises((ValueError, _PydanticValidationError)):
        await engine.run_authored_flow(unsafe_meta, {})

    assert not bundles.exists(), "no bundle may be opened for a rejected unsafe artifact"
    assert engine.trace_sink.events == [], "no trace event may precede artifact validation"


# ------------------------------------ Phase R9: catalog/validator one coherent target contract


async def test_capability_registered_after_factory_is_visible_and_authorable():
    """R9 (review finding #21): the prompt catalog renders per invocation from the SAME live
    registry validation uses — late registrations are advertised AND validate."""

    processing, analyzed = _fanout_engine(fail_on=None)
    artifact_using_late_cap = _good_artifact_json().replace(
        '"capability": "analyze"', '"capability": "late_analyzer"'
    )
    llm = ScriptedAuthorLLM([artifact_using_late_cap])
    author_engine, _spec = _author_engine_for(processing, llm)

    # capability arrives AFTER build_flow_author_capability() was constructed
    processing.register_capability(
        "late_analyzer", lambda ctx, item: f"late:{item}", kind="deterministic"
    )

    authored = await author_engine.run("authoring", {"goal": "scan", "context": {}})

    assert authored.status == "completed", (
        "validation must accept the late capability the catalog now advertises"
    )
    assert "late_analyzer" in llm.requests[0].user, (
        "the prompt catalog must include capabilities registered after factory construction"
    )
    executed = await processing.run_authored_flow(authored.output, {})
    assert executed.status == "completed"
    assert sorted(executed.output) == ["late:a", "late:b", "late:c"]


# --------------------------- Phase R10: FlowNodeSpec is a REAL discriminated base class


def test_parse_flow_node_dispatches_and_supports_isinstance_and_schema():
    """v0.11 clean contract (M2): parse_flow_node is the one dispatch door — concrete subtype
    instances, isinstance against the base TYPE, and the union schema advertised where dicts
    actually enter (FlowArtifact.nodes); each subclass keeps its per-kind schema."""

    step = parse_flow_node({"kind": "step", "id": "s1"})
    fan = parse_flow_node(
        {"kind": "fanout", "id": "f1", "capability": "analyze",
         "items_key": "collect.frames", "max_items": 3}
    )
    assert type(step).__name__ == "StepFlowNode"
    assert type(fan).__name__ == "FanoutFlowNode"
    assert isinstance(step, FlowNodeSpec) and isinstance(fan, FlowNodeSpec)

    branch = parse_flow_node({"id": "b1", "kind": "branch", "branches": {"ok": "s1"}})
    assert type(branch).__name__ == "BranchFlowNode"
    assert isinstance(branch, FlowNodeSpec)

    # the artifact schema (where node dicts actually enter) advertises the full union
    schema_blob = _json.dumps(FlowArtifact.model_json_schema())
    for kind in ("step", "branch", "evaluate", "fanout"):
        assert f'"{kind}"' in schema_blob, f"artifact schema must advertise kind={kind}"
    assert "id" in StepFlowNode.model_json_schema()["properties"]


def test_parse_flow_node_rejects_foreign_fields_not_smuggles_them():
    """R6 stays strict at the one door: a step payload carrying fanout-only fields is REJECTED
    by the discriminated schema; kind is REQUIRED (no v0.10-era defaulting)."""

    with pytest.raises(_PydanticValidationError):
        parse_flow_node({"kind": "step", "id": "x", "items_key": "smuggled.key"})
    with pytest.raises(_PydanticValidationError):
        parse_flow_node({"id": "x", "max_items": 5})  # no kind -> refused at the door


# --------------------------- Phase R11: per-invocation immutable registry snapshot


async def test_capability_registered_mid_call_is_not_validated_in_flight():
    """R11 (recheck finding #2): ONE registry snapshot per author invocation — a capability
    registered while the call is in flight is neither advertised in the catalog nor accepted
    by validation (TOCTOU closed); it becomes visible to the NEXT invocation (R9 preserved)."""

    processing, _analyzed = _fanout_engine(fail_on=None)
    artifact_json = _good_artifact_json().replace(
        '"capability": "analyze"', '"capability": "mid_call_cap"'
    )

    class MidCallRegisteringLLM(ScriptedAuthorLLM):
        """Registers the referenced capability AFTER the catalog render, BEFORE validation."""

        def __init__(self, responses, target_engine):
            super().__init__(responses)
            self._target = target_engine
            self._registered = False

        async def __call__(self, request):
            if not self._registered:
                self._registered = True
                self._target.register_capability(
                    "mid_call_cap", lambda ctx, item: f"mid:{item}", kind="deterministic"
                )
            return await super().__call__(request)

    llm = MidCallRegisteringLLM([artifact_json], processing)
    author_engine, _spec = _author_engine_for(processing, llm, max_repair_rounds=0)

    first = await author_engine.run("authoring", {"goal": "scan", "context": {}})
    assert first.status == "failed", (
        "in-flight registration must NOT be validated against — the snapshot wins"
    )
    assert "mid_call_cap" not in llm.requests[0].user, (
        "the catalog must render from the same snapshot validation uses"
    )

    second = await author_engine.run("authoring", {"goal": "scan", "context": {}})
    assert second.status == "completed", (
        "the NEXT invocation must see the registration (fresh snapshot per call)"
    )
    assert "mid_call_cap" in llm.requests[1].user


# --------------------------- Phase R12: provenance channel is engine-owned, not public API


async def test_intro_trace_events_is_no_longer_public_run_api():
    """R12 (recheck finding #3): callers cannot inject arbitrary trace events into a run —
    the parameter is gone from the public engine.run surface."""

    engine, _analyzed = _fanout_engine(fail_on=None)
    engine.register_workflow(WorkflowBuilder("plain").step("collect").build())

    with pytest.raises(TypeError):
        await engine.run("plain", {}, intro_trace_events=[])


async def test_authored_provenance_is_engine_constructed_with_fresh_event_identity():
    """R12: the flow:authored event is built BY the executor per run — fresh event_id each
    run, run-id stamped, metadata limited to the typed provenance payload."""

    processing, _analyzed = _fanout_engine(fail_on=None)
    artifact = FlowArtifact.model_validate(_json.loads(_good_artifact_json()))

    first = await processing.run_authored_flow(artifact, {})
    second = await processing.run_authored_flow(artifact, {})
    assert first.status == "completed" and second.status == "completed"

    events = [e for e in processing.trace_sink.events if e.decision == "flow:authored"]
    assert len(events) == 2
    assert events[0].event_id and events[1].event_id
    assert events[0].event_id != events[1].event_id, (
        "each run must mint a FRESH provenance event — no shared/duplicated identity"
    )
    assert events[0].run_id != events[1].run_id
    assert set(events[0].metadata) == {"flow_id", "goal", "nodes"}, (
        "provenance metadata is the typed payload only — no caller-crafted extras"
    )


async def test_unsafe_provenance_payload_is_rejected_before_any_trace_persists():
    """R12: the engine-owned provenance channel byte-checks its payload at the run boundary —
    a data-URI goal can never be persisted into trace/bundles through run birth."""

    from ai_workflow_engine.executor import _AUTHORED_PROVENANCE

    engine, _analyzed = _fanout_engine(fail_on=None)
    engine.register_workflow(WorkflowBuilder("plain_prov").step("collect").build())

    # White-box: stage the payload on the ENGINE-INTERNAL channel (the only route left —
    # R-C2-2 removed every public parameter) and prove the executor still byte-checks it.
    staged = _AUTHORED_PROVENANCE.set({"goal": "data:image/png;base64,AAAA", "flow_id": "x"})
    try:
        with pytest.raises(ValueError, match="authored_provenance"):
            await engine.run("plain_prov", {})
    finally:
        _AUTHORED_PROVENANCE.reset(staged)
    assert not any(e.decision == "flow:authored" for e in engine.trace_sink.events), (
        "an unsafe provenance payload must be rejected BEFORE any trace event is recorded"
    )


# --------------------------- Phase R-C2: post-implementation recheck remediation


async def test_mid_call_spec_mutation_cannot_bypass_the_side_effect_firewall():
    """R-C2-1 (codex probe): a capability advertised as DENIED must stay denied for the
    WHOLE invocation even if an attacker mutates the LIVE CapabilitySpec during the LLM
    call — the snapshot deep-copies the validation contract, not just the entry map."""

    processing, _analyzed = _fanout_engine(fail_on=None)
    processing.register_capability(
        "restricted_writer",
        lambda ctx, item: f"wrote:{item}",
        kind="deterministic",
        side_effects=["external_write"],  # NOT in allowed_side_effects=["read_only"]
    )
    live_spec, _handler = processing.registry.get("restricted_writer")
    artifact_json = _good_artifact_json().replace(
        '"capability": "analyze"', '"capability": "restricted_writer"'
    )

    class SpecMutatingLLM(ScriptedAuthorLLM):
        """Whitens the DENIED spec between catalog render and validation."""

        async def __call__(self, request):
            live_spec.side_effects = ["read_only"]  # the attack
            return await super().__call__(request)

    llm = SpecMutatingLLM([artifact_json])
    author_engine, _spec = _author_engine_for(processing, llm, max_repair_rounds=0)

    result = await author_engine.run("authoring", {"goal": "scan", "context": {}})

    assert result.status == "failed", (
        "the snapshot's frozen spec must keep the capability DENIED for this invocation"
    )
    assert "DENIED" in llm.requests[0].user, (
        "the catalog rendered from the same frozen contract must advertise the denial"
    )


async def test_hand_written_run_cannot_forge_authored_provenance():
    """R-C2-2 (codex probe): no public signature carries provenance — a plain run cannot
    emit flow:authored, and the old keyword is a TypeError, not a silent channel."""

    engine, _analyzed = _fanout_engine(fail_on=None)
    engine.register_workflow(WorkflowBuilder("plain_forge").step("collect").build())

    with pytest.raises(TypeError):
        await engine.run(
            "plain_forge", {}, _authored_provenance={"flow_id": "fake", "goal": "fake"}
        )

    result = await engine.run("plain_forge", {})
    assert result.status == "completed"
    assert not any(e.decision == "flow:authored" for e in engine.trace_sink.events), (
        "a hand-written run must never carry authored-flow provenance"
    )


async def test_provenance_channel_is_consumed_once_never_inherited():
    """R-C2-2: the staged payload is consumed by the authored run itself — a subsequent
    plain run in the SAME context must not inherit a stale flow:authored event."""

    processing, _analyzed = _fanout_engine(fail_on=None)
    processing.register_workflow(WorkflowBuilder("after_authored").step("collect").build())
    artifact = FlowArtifact.model_validate(_json.loads(_good_artifact_json()))

    authored = await processing.run_authored_flow(artifact, {})
    plain = await processing.run("after_authored", {})

    assert authored.status == "completed" and plain.status == "completed"
    stamped = [e for e in processing.trace_sink.events if e.decision == "flow:authored"]
    assert len(stamped) == 1, "exactly the authored run carries provenance — no inheritance"


def test_flow_node_spec_json_validation_dispatches_for_every_kind():
    """JSON payloads dispatch through the ONE parse door for all four kinds; the v0.10-era
    kind-defaulting shorthand is an explicit rejection now (M2)."""

    # v0.11 clean contract: kind is REQUIRED at the door — the old '{"id":"s1"}' defaulting
    # case is now a rejection (asserted below), not a dispatch case.
    cases = {
        '{"id":"s2","kind":"step"}': "StepFlowNode",
        '{"id":"b1","kind":"branch","branches":{"ok":"s1"}}': "BranchFlowNode",
        '{"id":"e1","kind":"evaluate"}': "EvaluateFlowNode",
        '{"id":"f1","kind":"fanout","capability":"c","items_key":"k","max_items":2}': "FanoutFlowNode",
    }
    for payload, expected in cases.items():
        node = parse_flow_node(_json.loads(payload))
        assert type(node).__name__ == expected, (payload, type(node).__name__)
        assert isinstance(node, FlowNodeSpec)

    with pytest.raises(_PydanticValidationError):
        parse_flow_node(_json.loads('{"id":"x","items_key":"smuggled"}'))
    with pytest.raises(_PydanticValidationError):
        parse_flow_node(_json.loads('{"id":"s1"}'))  # kind-defaulting removed (M2)


async def test_nested_run_inside_authored_flow_does_not_inherit_provenance():
    """R-C2-2 consume-once, the case that matters: a capability INSIDE the authored flow
    starting a nested engine.run in the same context must not stamp a second
    flow:authored — the executor consumes the staged payload before any node executes."""

    processing, _analyzed = _fanout_engine(fail_on=None)
    processing.register_workflow(WorkflowBuilder("inner_plain").step("analyze").build())

    async def collect_with_nested_run(_context, _payload):
        inner = await processing.run("inner_plain", "x")
        assert inner.status == "completed"
        return {"frames": ["a", "b", "c"]}

    # replace the artifact's collect step target with the nesting capability
    processing.register_capability(
        "collect_nested", collect_with_nested_run, kind="deterministic"
    )
    artifact_json = _good_artifact_json().replace('"id": "collect"', '"id": "collect_nested"').replace(
        '"items_key": "collect.frames"', '"items_key": "collect_nested.frames"'
    )
    artifact = FlowArtifact.model_validate(_json.loads(artifact_json))

    result = await processing.run_authored_flow(artifact, {})

    assert result.status == "completed"
    stamped = [e for e in processing.trace_sink.events if e.decision == "flow:authored"]
    assert len(stamped) == 1, (
        "the nested plain run must NOT inherit authored provenance — consume-once"
    )


async def test_flow_authored_events_carry_typed_status_and_text_is_never_terminal():
    """QRF follow-up, hardened to the v0.11 clean contract (M11): flow:authored events
    carry node_status='completed' (typed lifecycle everywhere), and the OLD decision-text
    fallback is a rejection — the bare string without the typed field projects progress
    at most, never a terminal."""

    from ai_workflow_engine.models import WorkflowTraceEvent
    from ai_workflow_viewer import build_observation_graph

    processing, _analyzed = _fanout_engine(fail_on=None)
    artifact = FlowArtifact.model_validate(_json.loads(_good_artifact_json()))
    result = await processing.run_authored_flow(artifact, {})
    assert result.status == "completed"

    authored_events = [e for e in result.trace if e.decision == "flow:authored"]
    assert authored_events, "authored run must record provenance"
    assert all(e.node_status == "completed" for e in authored_events), (
        "provenance events must carry the TYPED terminal status"
    )

    # pre-typed shape: decision only, no typed field — REJECTED as terminal evidence (M11)
    definition = WorkflowBuilder("legacy_flow").step("s").build()
    legacy = WorkflowTraceEvent(node="legacy_flow", decision="flow:authored", run_id="r1")
    assert legacy.node_status is None
    graph = build_observation_graph(definition, [legacy], run_id="r1")
    assert graph.nodes["legacy_flow"].status == "running", (
        "decision text without node_status is the pre-v0.11 shape — progress at most, "
        "never a projected terminal (inspect old bundles with their historical tag)"
    )



def test_flow_node_base_is_a_type_not_a_constructor():
    """v0.11 clean contract (manifest row M2): the v0.10-era kind-defaulting dispatch is gone —
    base construction and base model_validate fail loudly; parse_flow_node is the ONE door and
    requires an explicit kind."""

    import pytest as _pytest
    from ai_workflow_engine.flow_authoring import (
        BranchFlowNode,
        EvaluateFlowNode,
        FanoutFlowNode,
        FlowNodeSpec,
        StepFlowNode,
        parse_flow_node,
    )

    with _pytest.raises(TypeError, match="cannot be constructed"):
        FlowNodeSpec(kind="step", id="x")
    with _pytest.raises(TypeError, match="not a parse door"):
        FlowNodeSpec.model_validate({"kind": "step", "id": "x"})
    with _pytest.raises(TypeError, match="not a parse door"):
        FlowNodeSpec.model_validate_json('{"kind": "step", "id": "x"}')

    assert isinstance(parse_flow_node({"kind": "step", "id": "s"}), StepFlowNode)
    assert isinstance(parse_flow_node({"kind": "branch", "id": "b", "branches": {"a": "s"}}), BranchFlowNode)
    assert isinstance(parse_flow_node({"kind": "evaluate", "id": "e", "target": "s", "on_reject": "fallback"}), EvaluateFlowNode)
    assert isinstance(parse_flow_node({"kind": "fanout", "id": "f", "items_key": "s.items", "max_items": 4}), FanoutFlowNode)

    with _pytest.raises(Exception):
        parse_flow_node({"id": "no_kind_given"})       # kind is REQUIRED at the door
    with _pytest.raises(Exception):
        parse_flow_node({"kind": "mystery", "id": "x"})  # unknown kind fails
    with _pytest.raises(Exception):
        parse_flow_node({"kind": "step", "id": "x", "branches": {"a": "b"}})  # mixed fields fail
