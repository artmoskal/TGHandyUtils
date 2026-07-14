"""State-machine round (spec §2e): pre-set loop gates, deterministic guards, durable
suspend/resume with fast-forward replay, and machine-as-data round-trip."""

from typing import Any

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.unit

from ai_workflow_engine import (
    LocalWaitPolicy,
    AgentCapability,
    AgentRunRequest,
    CapabilityRegistry,
    CapabilityRuntime,
    CapabilitySpec,
    EvidenceRef,
    FlowArtifact,
    FlowNodeSpec,
    parse_flow_node,
    LLMAgentPlanner,
    LLMRequest,
    LLMResponse,
    MachineSnapshot,
    ToolCallRequest,
    ToolSpec,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.workflow import (
    BranchDecision,
    Retrace,
    WorkflowBuilder,
    WorkflowDefinition,
    WorkflowValidationError,
    render_machine_card,
)


class ClarificationOut(BaseModel):
    status: str
    value: Any = None


class AgentCaption(BaseModel):
    caption: str


# ======================================================================================
# SM2 — pre-set loop gates + deterministic guards
# ======================================================================================


def _loop_engine(bounds, exhausted=None, labels=None):
    """draft -> polish_gate {again: draft, done: finish} -> finish; guard scripted via labels."""

    calls = {"draft": 0, "finish": 0}
    script = list(labels or [])

    builder = WorkflowEngineBuilder()

    def draft(_context, payload):
        calls["draft"] += 1
        return f"draft{calls['draft']}"

    def finish(_context, payload):
        calls["finish"] += 1
        return f"final:{payload}"

    builder.register_capability("draft", draft, kind="deterministic")
    builder.register_capability("finish", finish, kind="deterministic")
    builder.register_guard("polish_gate", lambda p: script.pop(0) if script else "again")

    workflow = (
        WorkflowBuilder("loop_machine")
        .step("draft")
        .branch(
            "polish_gate",
            {"again": "draft", "done": "finish"},
            bounds=bounds,
            exhausted=exhausted,
        )
        .step("finish")
        .build()
    )
    builder.register_workflow(workflow)
    return builder.build(), calls


async def test_bounded_loop_exhausts_to_declared_escape_label():
    engine, calls = _loop_engine({"again": 2}, {"again": "done"})
    result = await engine.run("loop_machine", "seed")

    assert result.status == "completed"
    assert calls["draft"] == 3  # initial pass + exactly 2 gated traversals
    assert calls["finish"] == 1
    assert result.snapshot is None  # snapshots exist ONLY for suspended runs
    exhausted = [e for e in result.trace if e.decision == "transition:exhausted"]
    assert exhausted and exhausted[-1].metadata["rerouted_to"] == "done"
    assert exhausted[-1].metadata["max_traversals"] == 2


async def test_bounded_loop_without_escape_fails_loudly_at_the_gate():
    engine, calls = _loop_engine({"again": 1})
    result = await engine.run("loop_machine", "seed")

    assert result.status == "failed"
    assert "max_traversals=1" in (result.error or "")
    assert calls["draft"] == 2  # initial pass + the single allowed traversal, then the gate trips
    assert calls["finish"] == 0


def test_unbounded_decision_cycle_rejected_at_build_time():
    with pytest.raises(WorkflowValidationError) as exc:
        (
            WorkflowBuilder("runaway")
            .step("draft")
            .branch("gate", {"again": "draft", "done": "finish"})
            .step("finish")
            .build()
        )
    message = str(exc.value)
    assert "unbounded cycle" in message
    assert "max_traversals" in message


async def test_guard_routes_deterministically_with_zero_llm_usage():
    engine, _calls = _loop_engine({"again": 2}, {"again": "done"}, labels=["done"])
    result = await engine.run("loop_machine", "seed")

    assert result.status == "completed"
    assert result.usage.text_call_count == 0  # navigation cost: zero LLM calls
    branch_events = [e for e in result.trace if e.decision == "branch" and e.node == "polish_gate"]
    assert branch_events and branch_events[-1].metadata["decision_policy"] == "deterministic"
    taken = [e for e in result.trace if e.decision == "transition:taken"]
    assert taken and taken[-1].metadata["label"] == "done"


# ======================================================================================
# SM3 — durable suspend/resume
# ======================================================================================


def _clarify_engine():
    """a -> ask (human) -> b; the clarification answers only when a resume event arrives."""

    calls = {"a": 0, "ask": 0, "b": 0}
    builder = WorkflowEngineBuilder()

    def cap_a(_context, payload):
        calls["a"] += 1
        return {"prepared": payload}

    def ask(context, _payload):
        calls["ask"] += 1
        event = context.metadata.get("resume_event")
        if event is None:
            return ClarificationOut(status="pending")
        return ClarificationOut(status="answered", value=event)

    def cap_b(_context, payload):
        calls["b"] += 1
        answer = payload.value if isinstance(payload, ClarificationOut) else payload
        return {"done": True, "answer": answer}

    builder.register_capability("a", cap_a, kind="tool")
    builder.register_capability("ask", ask, kind="deterministic")
    builder.register_capability("b", cap_b, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("clarify_machine").step("a").human("ask", wait_policy=LocalWaitPolicy()).step("b").build())
    return builder.build(), calls


class _MemoryProbeLLM:
    def __init__(self, *, fail_if_called: bool = False):
        self.fail_if_called = fail_if_called
        self.requests: list[LLMRequest] = []

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.fail_if_called:
            raise AssertionError("resume must fast-forward recorded agent state, not invoke live memory")
        if len(self.requests) == 1:
            return LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        call_id="shot-1",
                        name="screenshot",
                        arguments={"selector": "body"},
                    )
                ]
            )
        return LLMResponse(text='{"caption": "recorded evidence"}')


def _agent_memory_replay_engine(
    client: _MemoryProbeLLM,
    *,
    memory=None,
    calls: dict[str, int],
):
    registry = CapabilityRegistry()

    def image_loader(_ref: EvidenceRef) -> bytes:
        calls["loads"] += 1
        return b"raw-image-bytes"

    async def screenshot(_context, _payload):
        calls["screenshots"] += 1
        return EvidenceRef(role="screenshot", uri="memory://frame-1", media_type="image/png")

    registry.register(
        CapabilitySpec(name="screenshot", kind="tool", description="Capture a checkpoint-safe frame ref"),
        screenshot,
    )
    planner = LLMAgentPlanner(
        client,
        tool_specs={
            "screenshot": ToolSpec(
                name="screenshot",
                description="Capture a screenshot",
                input_schema={"type": "object", "properties": {"selector": {"type": "string"}}},
            )
        },
        output_model=AgentCaption,
        node_name="memory_probe_agent",
        image_loader=image_loader,
        memory=memory,
    )
    agent = AgentCapability(
        planner,
        CapabilityRuntime(registry),
        name="agent_memory_probe",
    )
    builder = WorkflowEngineBuilder()
    builder.register_capability_spec(agent.spec, agent)

    def ask(context, _payload):
        calls["ask"] += 1
        event = context.metadata.get("resume_event")
        if event is None:
            return ClarificationOut(status="pending")
        return ClarificationOut(status="answered", value=event)

    def finish(_context, payload):
        calls["finish"] += 1
        answer = payload.value if isinstance(payload, ClarificationOut) else payload
        return {"resumed": True, "answer": answer}

    builder.register_capability("ask", ask, kind="deterministic")
    builder.register_capability("finish", finish, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("agent_memory_replay")
        .step("agent", capability="agent_memory_probe")
        .human("ask", wait_policy=LocalWaitPolicy())
        .step("finish")
        .build()
    )
    return builder.build()


async def test_suspension_captures_a_complete_machine_snapshot():
    engine, _calls = _clarify_engine()
    result = await engine.run("clarify_machine", {"question": "what color?"})

    assert result.status == "requires_user_input"
    snapshot = result.snapshot
    assert snapshot is not None
    assert snapshot.suspended_node == "ask"
    assert snapshot.workflow_id == "clarify_machine"
    assert snapshot.node_status["a"] == "accepted"
    assert snapshot.routes.get("a") != "halt"  # steps record routes only when halting
    assert snapshot.node_status["ask"] == "requires_user_input"
    assert snapshot.node_results  # position is complete, not a stub

    from ai_workflow_viewer import workflow_to_mermaid

    diagram = workflow_to_mermaid(engine.workflows["clarify_machine"], result)
    assert "class n_ask susp;" in diagram  # suspended state rendered distinctly


async def test_resume_continues_without_reexecuting_completed_nodes():
    engine, calls = _clarify_engine()
    first = await engine.run("clarify_machine", {"question": "what color?"})

    resumed = await engine.resume(first.snapshot, {"answer": "blue"})

    assert resumed.status == "completed"
    assert resumed.output == {"done": True, "answer": {"answer": "blue"}}
    assert calls["a"] == 1  # upstream ran exactly once across BOTH halves
    assert calls["b"] == 1
    assert calls["ask"] == 2  # pending once, answered once
    assert any(e.decision == "machine:resumed" for e in resumed.trace)
    assert any(e.decision == "machine:fastforward" and e.node == "a" for e in resumed.trace)


async def test_snapshot_json_roundtrip_resumes_on_a_fresh_engine():
    engine, _calls = _clarify_engine()
    first = await engine.run("clarify_machine", "plain-question")
    blob = first.snapshot.to_json()

    fresh_engine, fresh_calls = _clarify_engine()  # new process, same registrations
    resumed = await fresh_engine.resume(blob, "blue")

    assert resumed.status == "completed"
    assert fresh_calls["a"] == 0  # the first half is NEVER re-executed, even cross-process
    assert fresh_calls["b"] == 1


async def test_memory_projection_is_input_only_for_snapshot_replay_determinism():
    calls = {"screenshots": 0, "loads": 0, "ask": 0, "finish": 0}
    client = _MemoryProbeLLM()
    engine = _agent_memory_replay_engine(
        client,
        memory={"mode": "image_evicting", "keep_last_images": 0},
        calls=calls,
    )
    first = await engine.run(
        "agent_memory_replay",
        AgentRunRequest(
            prompt="Capture and describe.",
            allowed_tools=["screenshot"],
            max_steps=3,
        ),
    )

    assert first.status == "requires_user_input"
    assert calls == {"screenshots": 1, "loads": 0, "ask": 1, "finish": 0}
    assert len(client.requests) == 2
    tool_results = client.requests[1].messages[-1].tool_results
    assert tool_results and tool_results[0].images == []  # real ImageEvicting prompt view
    assert "memory:image_evicted" in tool_results[0].content

    blob = first.snapshot.to_json()
    assert "memory://frame-1" in blob
    assert "raw-image-bytes" not in blob
    recorded = MachineSnapshot.model_validate_json(blob)
    recorded_agent = recorded.node_outputs["agent"]
    assert recorded_agent["steps"][0]["output"]["uri"] == "memory://frame-1"

    fresh_calls = {"screenshots": 0, "loads": 0, "ask": 0, "finish": 0}
    fresh_client = _MemoryProbeLLM(fail_if_called=True)
    fresh_engine = _agent_memory_replay_engine(fresh_client, memory=None, calls=fresh_calls)
    resumed = await fresh_engine.resume(blob, {"answer": "blue"})

    assert resumed.status == "completed"
    assert resumed.output == {"resumed": True, "answer": {"answer": "blue"}}
    assert fresh_client.requests == []
    assert fresh_calls == {"screenshots": 0, "loads": 0, "ask": 1, "finish": 1}
    assert any(e.decision == "machine:fastforward" and e.node == "agent" for e in resumed.trace)


async def test_resume_on_engine_without_the_workflow_is_loud():
    engine, _calls = _clarify_engine()
    first = await engine.run("clarify_machine", "q")
    empty_engine = WorkflowEngineBuilder().build()

    with pytest.raises(KeyError) as exc:
        await empty_engine.resume(first.snapshot, "answer")
    assert "register it before resuming" in str(exc.value)


async def test_loop_counters_survive_suspensions_and_still_gate_after_resume():
    """ask (human) -> gate {again: ask ⟲≤1, done: fin}: the machine suspends inside a bounded
    loop twice; the pre-set gate counts traversals ACROSS suspensions and trips on resume."""

    calls = {"ask": 0}
    builder = WorkflowEngineBuilder()

    def ask(context, _payload):
        calls["ask"] += 1
        event = context.metadata.get("resume_event")
        if event is None:
            return ClarificationOut(status="pending")
        return ClarificationOut(status="answered", value=event)

    script = ["again", "again"]
    builder.register_capability("ask", ask, kind="deterministic")
    builder.register_capability("fin", lambda _c, p: "fin", kind="deterministic")
    builder.register_guard("gate", lambda p: script.pop(0) if script else "done")
    builder.register_workflow(
        WorkflowBuilder("nag_machine")
        .human("ask", wait_policy=LocalWaitPolicy())
        .branch("gate", {"again": "ask", "done": "fin"}, bounds={"again": 1})
        .step("fin")
        .build()
    )
    engine = builder.build()

    first = await engine.run("nag_machine", "q")
    assert first.status == "requires_user_input"

    second = await engine.resume(first.snapshot, "first-answer")
    assert second.status == "requires_user_input"  # looped once, waiting again
    assert second.snapshot.transition_counts == {"gate|again": 1}

    third = await engine.resume(second.snapshot, "second-answer")
    assert third.status == "failed"  # gate counted the pre-suspension traversal
    assert "max_traversals=1" in (third.error or "")


# ======================================================================================
# SM4 — machine-as-data: authored bounded loops + canonical round-trip
# ======================================================================================


def _authored_loop_artifact(with_bounds: bool) -> FlowArtifact:
    gate = dict(
        kind="branch",
        id="polish_gate",
        branches={"again": "draft", "done": "finish"},
    )
    if with_bounds:
        gate["branch_bounds"] = {"again": 2}
        gate["branch_exhausted"] = {"again": "done"}
    return FlowArtifact(
        flow_id="polish_loop",
        goal="draft then polish at most twice",
        nodes=[
            parse_flow_node({"kind": "step", "id": "draft"}),
            parse_flow_node(gate),
            parse_flow_node({"kind": "step", "id": "finish"}),
        ],
    )


async def test_authored_flow_may_declare_bounded_loops():
    engine, calls = _loop_engine({"again": 2}, {"again": "done"})
    result = await engine.run_authored_flow(_authored_loop_artifact(with_bounds=True), "seed")

    assert result.status == "completed"
    assert calls["draft"] == 3  # same gate semantics as hand-written machines


async def test_authored_back_edge_without_bound_is_rejected_before_compile():
    engine, _calls = _loop_engine({"again": 2}, {"again": "done"})
    with pytest.raises(WorkflowValidationError) as exc:
        await engine.run_authored_flow(_authored_loop_artifact(with_bounds=False), "seed")
    message = str(exc.value)
    assert "unbounded cycle" in message
    assert "again" in message


async def test_definition_is_canonical_machine_as_data():
    """JSON round-trip preserves the machine (incl. materialized control transitions,
    idempotently) and the round-tripped machine runs identically."""

    evaluated = (
        WorkflowBuilder("eval_machine")
        .step("extract")
        .evaluate("check", on_reject=Retrace("extract"))
        .step("write")
        .build()
    )
    restored = WorkflowDefinition.model_validate_json(evaluated.model_dump_json())
    assert len(restored.transitions) == len(evaluated.transitions)  # materializer is idempotent
    assert {(t.source, t.label, t.policy) for t in restored.transitions} == {
        (t.source, t.label, t.policy) for t in evaluated.transitions
    }
    assert restored.validate_graph() == []

    engine, calls = _loop_engine({"again": 2}, {"again": "done"})
    original = engine.workflows["loop_machine"]
    roundtripped = WorkflowDefinition.model_validate_json(original.model_dump_json()).model_copy(
        update={"workflow_id": "loop_machine_rt"}
    )
    result = await engine.run(roundtripped, "seed")
    assert result.status == "completed"
    assert [r.status for r in result.node_results] == ["accepted"] * len(result.node_results)


async def test_tampered_roundtrip_fails_preflight_loudly():
    engine, _calls = _loop_engine({"again": 2}, {"again": "done"})
    raw = engine.workflows["loop_machine"].model_dump()
    raw["workflow_id"] = "loop_machine_tampered"
    raw["nodes"][0]["capability"] = "ghost_cap"
    tampered = WorkflowDefinition.model_validate(raw)

    result = await engine.run(tampered, "seed")
    assert result.status == "failed"
    assert "ghost_cap" in (result.error or "")


# ======================================================================================
# V3 — self-describing machine: machine card, inject_machine, authoring catalog
# ======================================================================================


def _card_machine() -> WorkflowDefinition:
    return (
        WorkflowBuilder("card_machine")
        .step("draft")
        .branch(
            "polish_gate",
            {"again": "draft", "done": "finish"},
            bounds={"again": 2},
            exhausted={"again": "done"},
            describe={"again": "output needs another polish pass", "done": "quality sufficient"},
        )
        .step("finish")
        .build()
    )


def test_machine_card_lists_moves_descriptions_and_live_gates():
    wf = _card_machine()
    card = render_machine_card(wf, "polish_gate")
    assert "state: polish_gate (branch)" in card
    assert "- again -> draft — output needs another polish pass [gate: 2 of 2 remaining]" in card
    assert "- done -> finish — quality sufficient" in card

    live = render_machine_card(wf, "polish_gate", {"transition_counts": {"polish_gate|again": 1}})
    assert "[gate: 1 of 2 remaining]" in live

    spent = render_machine_card(wf, "polish_gate", {"transition_counts": {"polish_gate|again": 2}})
    assert "gate EXHAUSTED (2/2 used); exhausted -> done" in spent


def test_evaluator_routes_self_describe():
    wf = (
        WorkflowBuilder("eval_card")
        .step("extract")
        .evaluate("check", on_reject=Retrace("extract"))
        .step("write")
        .build()
    )
    card = render_machine_card(wf, "check")
    assert "[on_accept:accept] -> write — evaluator accepted — continue forward" in card
    assert "[on_reject:retrace] -> extract — evaluator rejected — go back to 'extract'" in card
    assert "[bounded <= 1]" in card


async def test_inject_machine_gives_decider_the_live_card():
    seen = []
    script = ["again", "done"]
    builder = WorkflowEngineBuilder()
    builder.register_capability("draft", lambda _c, p: "d", kind="deterministic")
    builder.register_capability("finish", lambda _c, p: "f", kind="deterministic")

    def decider(context, _payload):
        seen.append(context.metadata.get("machine"))
        return BranchDecision(label=script.pop(0))

    builder.register_capability("polish_gate", decider, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("inject_machine_wf")
        .step("draft")
        .branch(
            "polish_gate",
            {"again": "draft", "done": "finish"},
            bounds={"again": 2},
            describe={"again": "polish more", "done": "draft is good — finish"},
            inject_machine=True,
        )
        .step("finish")
        .build()
    )
    result = await builder.build().run("inject_machine_wf", "seed")

    assert result.status == "completed"
    assert len(seen) == 2 and all(seen)
    assert "polish more" in seen[0] and "[gate: 2 of 2 remaining]" in seen[0]
    assert "[gate: 1 of 2 remaining]" in seen[1]  # second visit sees the DECREMENTED budget


async def test_inject_machine_off_keeps_decider_metadata_clean():
    seen = []
    builder = WorkflowEngineBuilder()
    builder.register_capability("finish", lambda _c, p: "f", kind="deterministic")

    def decider(context, _payload):
        seen.append("machine" in context.metadata)
        return BranchDecision(label="done")

    builder.register_capability("gate", decider, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("plain_wf").branch("gate", {"done": "finish"}).step("finish").build()
    )
    result = await builder.build().run("plain_wf", "seed")
    assert result.status == "completed"
    assert seen == [False]  # default flag: zero metadata change


def test_capability_catalog_self_describes_with_firewall():
    from ai_workflow_engine import render_capability_catalog
    from ai_workflow_engine.models import CapabilitySpec

    builder = WorkflowEngineBuilder()
    builder.register_capability("extract", lambda _c, p: p, kind="deterministic")
    builder.register_capability_spec(
        CapabilitySpec(
            name="write_notes", kind="tool", description="persist notes",
            side_effects=["filesystem_write"],
        ),
        lambda _c, p: p,
    )
    builder.register_capability_spec(
        CapabilitySpec(name="make_plan", kind="llm", is_planner=True),
        lambda _c, p: p,
    )
    engine = builder.build()

    catalog = render_capability_catalog(engine.registry, allowed_side_effects=[])
    assert "- extract (deterministic)" in catalog
    assert "persist notes" in catalog and "filesystem_write (DENIED)" in catalog
    assert "make_plan" in catalog and "NOT-AUTHORABLE" in catalog


async def test_authored_flow_descriptions_flow_to_machine_card():
    engine, _calls = _loop_engine({"again": 2}, {"again": "done"})
    artifact = _authored_loop_artifact(with_bounds=True)
    artifact.nodes[1].describe = {"again": "needs more polish"}
    result = await engine.run_authored_flow(artifact, "seed")
    assert result.status == "completed"

    card = render_machine_card(engine.workflows["authored:polish_loop"], "polish_gate")
    assert "needs more polish" in card


def test_machine_snapshot_rejects_non_current_schemas_loudly():
    """v0.11 clean contract (manifest row M6): the snapshot schema is versioned and CLOSED —
    pre-v0.11 JSON (no schema_version), wrong versions, unknown keys, and missing run identity
    all fail with actionable errors; the current engine has no importer for old data."""

    import pytest as _pytest
    from pydantic import ValidationError as _VE
    from ai_workflow_engine.snapshot import SNAPSHOT_SCHEMA_VERSION, MachineSnapshot

    current = dict(
        schema_version=SNAPSHOT_SCHEMA_VERSION, workflow_id="w", suspended_node="g",
        goal={"workflow_type": "w", "objective": "o", "goal_id": "g-1"},
        run_context={"workflow_id": "r", "workflow_type": "w", "goal_id": "g-1"},
    )
    snap = MachineSnapshot(**current)
    assert MachineSnapshot.model_validate_json(snap.to_json()).schema_version == SNAPSHOT_SCHEMA_VERSION

    old_style = {k: v for k, v in current.items() if k != "schema_version"}
    with _pytest.raises(_VE, match="unsupported machine-snapshot schema"):
        MachineSnapshot.model_validate(old_style)          # v0.10-era JSON: no version field
    with _pytest.raises(_VE, match="unsupported machine-snapshot schema"):
        MachineSnapshot.model_validate({**current, "schema_version": "v0.10"})
    with _pytest.raises(_VE):
        MachineSnapshot.model_validate({**current, "mystery_key": 1})   # closed schema
    with _pytest.raises(_VE):
        MachineSnapshot.model_validate({k: v for k, v in current.items() if k != "goal"})
    with _pytest.raises(_VE):
        MachineSnapshot.model_validate({k: v for k, v in current.items() if k != "run_context"})
    # review finding 1: EMPTY/MALFORMED identity fails AT THE MODEL — resume can never see it
    with _pytest.raises(_VE):
        MachineSnapshot.model_validate({**current, "goal": {}})
    with _pytest.raises(_VE):
        MachineSnapshot.model_validate({**current, "run_context": {}})
    with _pytest.raises(_VE):
        MachineSnapshot.model_validate({**current, "goal": {"objective": 42}})
    # recheck R1: identity is SEALED — no minting, no conflicts, no unknown nested fields
    with _pytest.raises(_VE, match="goal.goal_id is missing"):
        MachineSnapshot.model_validate(
            {**current, "goal": {"workflow_type": "w", "objective": "o"}}  # no goal_id -> no minting
        )
    with _pytest.raises(_VE, match="conflicting identities"):
        MachineSnapshot.model_validate(
            {**current, "run_context": {"workflow_id": "r", "workflow_type": "w", "goal_id": "OTHER"}}
        )
    with _pytest.raises(_VE, match="conflicting workflow_type"):
        MachineSnapshot.model_validate(
            {**current, "run_context": {"workflow_id": "r", "workflow_type": "zzz", "goal_id": "g-1"}}
        )
    with _pytest.raises(_VE, match="unknown goal fields"):
        MachineSnapshot.model_validate(
            {**current, "goal": {**current["goal"], "smuggled_identity": "x"}}
        )
    with _pytest.raises(_VE, match="run_context.goal_id is missing"):
        MachineSnapshot.model_validate(
            {**current, "run_context": {"workflow_id": "r", "workflow_type": "w"}}
        )

    # JSON-schema truth: typed identity refs, const version, closed schema
    schema = MachineSnapshot.model_json_schema()
    assert schema.get("additionalProperties") is False
    version_schema = schema["properties"]["schema_version"]
    assert version_schema.get("const") == "v0.11" or version_schema.get("enum") == ["v0.11"]
    for field, model_name in (("goal", "WorkflowGoal"), ("run_context", "WorkflowRunContext")):
        ref = schema["properties"][field].get("$ref", "")
        assert model_name in ref, f"{field} must be the typed model, got {schema['properties'][field]}"
        assert field in schema.get("required", []), f"{field} must be required"


async def test_public_resume_never_mints_a_replacement_identity():
    """Review finding 1 regression: a snapshot whose identity was hollowed out (goal -> {}) must
    fail BEFORE any capability executes — the old dict-truthiness path silently created a fresh
    goal/run identity and resumed under it."""

    import pytest as _pytest
    from pydantic import ValidationError as _VE

    from ai_workflow_engine import WorkflowEngine

    engine = WorkflowEngine()
    calls = {"n": 0}

    class Gate(BaseModel):
        status: str
        value: str = ""

    def gate(context, _payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        calls["n"] += 1
        return Gate(status="answered", value=str(event))

    engine.register_capability("gate", gate)
    engine.register_workflow(
        WorkflowBuilder("identity_flow").human("gate", wait_policy=LocalWaitPolicy()).build()
    )
    first = await engine.run("identity_flow", {})
    assert first.status == "requires_user_input" and first.snapshot is not None

    hollowed = first.snapshot.model_dump()
    hollowed["goal"] = {}
    blob = __import__("json").dumps(hollowed, default=str)
    with _pytest.raises(_VE):
        await engine.resume(blob, "answer")
    assert calls["n"] == 0, "a capability executed under a minted replacement identity"


def test_snapshot_identity_invariant_is_representation_independent():
    """Recheck RR1: the identity seal binds for TYPED and MIXED construction, not only wire
    dicts — no representation can build a conflicting or hollow-identity snapshot."""

    import pytest as _pytest
    from pydantic import ValidationError as _VE
    from ai_workflow_engine.models import WorkflowGoal, WorkflowRunContext
    from ai_workflow_engine.snapshot import MachineSnapshot

    goal = WorkflowGoal(workflow_type="w", objective="o", goal_id="g-1")
    good_rc = WorkflowRunContext(workflow_id="r", workflow_type="w", goal_id="g-1")

    ok = MachineSnapshot(
        schema_version="v0.11", workflow_id="w", suspended_node="n",
        goal=goal, run_context=good_rc,
    )
    assert ok.goal.goal_id == "g-1"

    # typed mismatch
    with _pytest.raises(_VE, match="conflicting identities"):
        MachineSnapshot(
            schema_version="v0.11", workflow_id="w", suspended_node="n",
            goal=goal,
            run_context=WorkflowRunContext(workflow_id="r", workflow_type="w", goal_id="OTHER"),
        )
    # typed missing run-context goal id
    with _pytest.raises(_VE, match="run_context.goal_id is missing"):
        MachineSnapshot(
            schema_version="v0.11", workflow_id="w", suspended_node="n",
            goal=goal,
            run_context=WorkflowRunContext(workflow_id="r", workflow_type="w"),
        )
    # typed workflow_type conflict
    with _pytest.raises(_VE, match="conflicting workflow_type"):
        MachineSnapshot(
            schema_version="v0.11", workflow_id="w", suspended_node="n",
            goal=goal,
            run_context=WorkflowRunContext(workflow_id="r", workflow_type="zzz", goal_id="g-1"),
        )
    # mixed dict/model mismatch
    with _pytest.raises(_VE, match="conflicting identities"):
        MachineSnapshot(
            schema_version="v0.11", workflow_id="w", suspended_node="n",
            goal={"workflow_type": "w", "objective": "o", "goal_id": "g-1"},
            run_context=WorkflowRunContext(workflow_id="r", workflow_type="w", goal_id="OTHER"),
        )


async def test_public_resume_reseals_already_built_snapshot_objects():
    """Recheck RR1: resume(MachineSnapshot) re-passes the FULL seal — a conflicting identity
    smuggled past validators via model_copy is rejected with ZERO capability calls."""

    import pytest as _pytest
    from pydantic import ValidationError as _VE
    from ai_workflow_engine import WorkflowEngine
    from ai_workflow_engine.models import WorkflowRunContext

    engine = WorkflowEngine()
    calls = {"n": 0}

    class Gate(BaseModel):
        status: str
        value: str = ""

    def gate(context, _payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        calls["n"] += 1
        return Gate(status="answered", value=str(event))

    engine.register_capability("gate", gate)
    engine.register_workflow(
        WorkflowBuilder("reseal_flow").human("gate", wait_policy=LocalWaitPolicy()).build()
    )
    first = await engine.run("reseal_flow", {})
    assert first.snapshot is not None

    smuggled = first.snapshot.model_copy(
        update={"run_context": WorkflowRunContext(workflow_id="r2", workflow_type="reseal_flow", goal_id="FORGED")}
    )
    with _pytest.raises(_VE, match="conflicting identities"):
        await engine.resume(smuggled, "answer")
    assert calls["n"] == 0, "a capability executed under a smuggled identity"
