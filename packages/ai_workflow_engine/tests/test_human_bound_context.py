"""Human-node bound-context contract (Iteration B / B0): FENCE-1, FENCE-2 and the two
sibling dormant decorations (memory, model_profile).

Human nodes are DECLARED nodes, so they must receive the same node decoration contract as
step/branch/evaluate nodes: typed retrace provenance, plan/machine card injection, agent-memory
config delivery, and model-profile binding — composed with resume-event metadata, never replacing
it. Written RED first against the v0.11.2 behavior (raw ``runtime.invoke`` bypass in
``nodes/human.py``); they become the permanent locks as the bound-door fix lands. Do not weaken
an assertion to green a phase.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

from ai_workflow_engine import (
    CapabilityResult,
    CapabilitySpec,
    LocalWaitPolicy,
    PlanArtifact,
    PlanTask,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.models import (
    ModelProfile,
    RuntimeLimits,
    SafetyPolicy,
    WorkflowProfile,
)
from ai_workflow_engine.workflow import Retrace, WorkflowBuilder


class ClarificationOut(BaseModel):
    status: str
    value: Any = None


def _profile(workflow_type: str) -> WorkflowProfile:
    return WorkflowProfile(
        workflow_type=workflow_type,
        limits=RuntimeLimits(timeout_s=None),
        safety=SafetyPolicy(allowed_side_effects=[]),
    )


def _answer_or_pend(context) -> ClarificationOut:
    event = context.metadata.get("resume_event")
    if event is None:
        return ClarificationOut(status="pending")
    return ClarificationOut(status="answered", value=event)


def _with_decoration(definition, node_id: str, **overrides):
    """Hand-declare decorations the builder cannot express yet: rebuild the definition with
    the target node's canonical ``WorkflowNode`` fields overridden (MageQA-style direct
    definitions set these fields without going through ``WorkflowBuilder``)."""

    nodes = [
        node.model_copy(update=overrides) if node.id == node_id else node
        for node in definition.nodes
    ]
    return definition.model_copy(update={"nodes": nodes})


# ======================================================================================
# FENCE-1 — typed retrace provenance must reach a retraced HUMAN target exactly once
# ======================================================================================


async def test_retraced_human_target_receives_provenance_and_criticism_once():
    """Four visits across two suspend/resume cycles: ask(pending) -> answer -> QA rejects ->
    retraced re-ask(pending) -> answer -> QA accepts. The typed provenance belongs to the
    retraced invocation ONLY; criticism content rides the retraced payload ONLY; resume
    events belong to the answer visits ONLY — and none of them may displace each other."""

    visits: list[dict[str, Any]] = []

    def ask(context, payload):
        visits.append(
            {
                "provenance": getattr(context, "retrace_provenance", None),
                "criticism": isinstance(payload, dict) and "_criticism" in payload,
                "resume_event": context.metadata.get("resume_event"),
            }
        )
        return _answer_or_pend(context)

    gate_calls = {"n": 0}

    def gate(_context, _payload):
        gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if gate_calls["n"] > 1 else "rejected",
            error="needs one more round",
        )

    def fin(_context, payload):
        return {"done": True}

    engine = (
        WorkflowEngineBuilder()
        .register_capability("ask", ask, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_capability("fin", fin, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("human_retrace")
            .human("ask", wait_policy=LocalWaitPolicy())
            .evaluate("qa", target="ask", evaluator="gate", on_reject=Retrace("ask"))
            .step("fin")
            .build()
        )
        .build()
    )

    first = await engine.run("human_retrace", {"question": "pick a color"})
    assert first.status == "requires_user_input"

    second = await engine.resume(first.snapshot, {"answer": "draft-1"})
    assert second.status == "requires_user_input", (
        "the rejected round must retrace to the human node and suspend again"
    )

    third = await engine.resume(second.snapshot, {"answer": "draft-2"})
    assert third.status == "completed"

    assert len(visits) == 4, "ask must run: initial, answer, retraced re-ask, answer"

    provenance = [v["provenance"] for v in visits]
    assert provenance[0] is None and provenance[1] is None and provenance[3] is None, (
        "provenance belongs to the retraced invocation only"
    )
    retraced = provenance[2]
    assert retraced is not None, (
        "FENCE-1: a retraced HUMAN target must receive typed retrace provenance through the "
        "generic node boundary, exactly like planner/step targets"
    )
    assert retraced.round == 1
    assert retraced.evaluator_node == "qa"
    assert retraced.target_node == "ask"

    assert [v["criticism"] for v in visits] == [False, False, True, False], (
        "criticism content rides the retraced payload only — the fix must not lose or spread it"
    )
    assert [v["resume_event"] for v in visits] == [
        None,
        {"answer": "draft-1"},
        None,
        {"answer": "draft-2"},
    ], "resume events belong to the answer visits and must survive context decoration"


# ======================================================================================
# FENCE-2 — .human(inject_plan=True / inject_machine=True) must deliver semantic cards
# ======================================================================================


async def test_human_inject_plan_and_machine_deliver_semantic_cards():
    """The public injection flags on a human node must produce REAL cards on every visit —
    the plan card renders the live plan, the machine card renders the human node's own
    state — not silently do nothing (v0.11.2 behavior)."""

    seen: list[dict[str, Any]] = []

    def make_plan(_context, _payload):
        return PlanArtifact(
            goal="approve the launch draft",
            tasks=[
                PlanTask(
                    task_id="t1",
                    description="emit the draft",
                    capability="emit",
                    payload={"value": 1},
                )
            ],
        )

    def emit(_context, payload):
        return {"draft": payload}

    def approve(context, _payload):
        seen.append(
            {
                "plan": context.metadata.get("plan"),
                "machine": context.metadata.get("machine"),
            }
        )
        return _answer_or_pend(context)

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("brief_flow"))
        .register_capability(
            "make_plan",
            make_plan,
            spec=CapabilitySpec(name="make_plan", kind="llm", is_planner=True),
        )
        .register_capability("emit", emit, kind="deterministic")
        .register_capability("approve", approve, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("brief_flow")
            .plan("plan_node", capability="make_plan")
            .human(
                "approve",
                wait_policy=LocalWaitPolicy(),
                inject_plan=True,
                inject_machine=True,
                description="human approval gate",
            )
            .build()
        )
        .build()
    )

    first = await engine.run("brief_flow", {})
    assert first.status == "requires_user_input"
    resumed = await engine.resume(first.snapshot, {"approved": True})
    assert resumed.status == "completed"

    assert len(seen) == 2, "approve must run on the suspend visit and the answer visit"
    for visit_no, visit in enumerate(seen):
        plan_card = visit["plan"]
        assert isinstance(plan_card, str), (
            f"FENCE-2 visit {visit_no}: inject_plan=True on a human node must deliver the "
            "rendered plan card"
        )
        assert "approve the launch draft" in plan_card, "plan card must carry the live goal"
        assert "t1" in plan_card and "emit the draft" in plan_card, (
            "plan card must carry the live task rows, not just exist"
        )
        machine_card = visit["machine"]
        assert isinstance(machine_card, str), (
            f"FENCE-2 visit {visit_no}: inject_machine=True on a human node must deliver the "
            "machine card"
        )
        assert "state: approve (human)" in machine_card, (
            "machine card must describe the human node's own state"
        )
        assert "human approval gate" in machine_card, (
            "machine card must carry the declared node description"
        )


# ======================================================================================
# Dormant sibling decoration: agent-memory config delivery (hand-declared node)
# ======================================================================================


async def test_hand_declared_human_memory_config_reaches_capability_context():
    """`WorkflowNode(kind='human', memory=...)` is valid today (hand-declared definitions)
    but the config silently never reaches the capability — the same dead-flag class as
    FENCE-2. After the bound-door fix it must arrive as metadata['agent_memory'] on every
    visit, composed with the resume event."""

    seen: list[Any] = []

    def ask(context, _payload):
        seen.append(context.metadata.get("agent_memory"))
        return _answer_or_pend(context)

    definition = _with_decoration(
        WorkflowBuilder("mem_flow").human("ask", wait_policy=LocalWaitPolicy()).build(),
        "ask",
        memory="structured_state",
    )

    engine = (
        WorkflowEngineBuilder()
        .register_capability("ask", ask, kind="deterministic")
        .register_workflow(definition)
        .build()
    )

    first = await engine.run("mem_flow", {"q": "state?"})
    assert first.status == "requires_user_input"
    resumed = await engine.resume(first.snapshot, {"answer": "ready"})
    assert resumed.status == "completed"

    assert seen == ["structured_state", "structured_state"], (
        "a human node's declared memory config must reach context.metadata['agent_memory'] "
        "on the suspend AND answer visits (it is silently dropped in v0.11.2)"
    )


# ======================================================================================
# Dormant sibling decoration: model-profile binding (hand-declared node)
# ======================================================================================


async def test_hand_declared_human_model_profile_binds_and_traces():
    """`WorkflowNode(kind='human', model_profile=...)` references a registered profile that
    v0.11.2 silently ignores. After the fix the capability must see the bound profile and
    the trace must record an honest model_binding decision for the human node."""

    seen: list[Any] = []

    def ask(context, _payload):
        seen.append(context.model_profile)
        return _answer_or_pend(context)

    definition = _with_decoration(
        WorkflowBuilder("bind_flow").human("ask", wait_policy=LocalWaitPolicy()).build(),
        "ask",
        model_profile="human_probe",
    )

    engine = (
        WorkflowEngineBuilder()
        .with_model_profile(
            ModelProfile(name="human_probe", provider="ollama", model="probe-1", temperature=0.0)
        )
        .register_capability("ask", ask, kind="deterministic")
        .register_workflow(definition)
        .build()
    )

    first = await engine.run("bind_flow", {"q": "which model?"})
    assert first.status == "requires_user_input"
    resumed = await engine.resume(first.snapshot, {"answer": "bound"})
    assert resumed.status == "completed"

    assert len(seen) == 2
    for visit_no, profile in enumerate(seen):
        assert profile is not None and profile.name == "human_probe", (
            f"visit {visit_no}: a declared model profile must bind on human-node invocations "
            "(silently ignored in v0.11.2)"
        )

    bindings = [
        event
        for result in (first, resumed)
        for event in result.trace
        if event.decision == "model_binding" and event.node == "ask"
    ]
    assert bindings, "the binding decision must be traced for human nodes like any bound node"
    assert all(
        event.metadata.get("model_profile_requested") == "human_probe" for event in bindings
    )


# ======================================================================================
# Builder parity: .human(memory=, model_profile=) is the SAME contract as hand-declared
# ======================================================================================


def test_builder_human_decorations_equal_hand_declared_definition():
    """`.human(memory=..., model_profile=...)` must produce the identical canonical
    ``WorkflowNode`` as a hand-declared definition — one contract, no builder-only or
    advanced-only split."""

    built = (
        WorkflowBuilder("parity_flow")
        .human(
            "ask",
            wait_policy=LocalWaitPolicy(),
            memory="structured_state",
            model_profile="human_probe",
        )
        .build()
    )
    declared = _with_decoration(
        WorkflowBuilder("parity_flow").human("ask", wait_policy=LocalWaitPolicy()).build(),
        "ask",
        memory="structured_state",
        model_profile="human_probe",
    )
    assert built.node("ask").model_dump() == declared.node("ask").model_dump()
    assert built.definition_digest() == declared.definition_digest(), (
        "machine-is-data: both construction paths must yield the same data identity"
    )


def test_builder_human_invalid_memory_config_fails_loudly_at_build():
    """The existing loud-at-build memory validation must cover the builder's human path."""

    from ai_workflow_engine.workflow import WorkflowValidationError

    with pytest.raises(WorkflowValidationError, match="memory config invalid"):
        (
            WorkflowBuilder("bad_mem")
            .human("ask", wait_policy=LocalWaitPolicy(), memory={"mode": "bogus_mode"})
            .build()
        )


# ======================================================================================
# Degradation fence: a DECORATED gate's timeout route still never enters the capability
# ======================================================================================


async def test_decorated_human_timeout_route_never_invokes_the_capability():
    """The bound door decorates INVOCATIONS only. The declared timeout transition resolves
    the node without invoking, so a fully decorated human gate (machine card + memory +
    model profile) must keep the timeout capability call count at exactly the initial ask —
    no decoration may drag the capability into the timeout path."""

    from datetime import datetime, timezone

    from ai_workflow_engine import DurableWaitPolicy, InMemoryWaitCoordinator

    calls = {"gate": 0}

    def gate(context, _payload):
        calls["gate"] += 1
        return _answer_or_pend(context)

    def escalate(_context, _payload):
        return {"escalated": True}

    def finish(_context, payload):
        return {"done": True, "from": getattr(payload, "value", None)}

    clock = lambda: datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731
    coordinator = InMemoryWaitCoordinator(clock=clock, shared_state={})

    engine = (
        WorkflowEngineBuilder()
        .with_wait_coordinator(coordinator, clock=clock)
        .with_model_profile(
            ModelProfile(name="gate_probe", provider="ollama", model="probe-1", temperature=0.0)
        )
        .register_capability("gate", gate, kind="deterministic")
        .register_capability("finish", finish, kind="deterministic")
        .register_capability("escalate", escalate, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("timed_flow")
            .human(
                "gate",
                wait_policy=DurableWaitPolicy(timeout_s=60),
                timeout_to="escalate",
                inject_machine=True,
                memory="structured_state",
                model_profile="gate_probe",
                description="decorated escalation gate",
            )
            .step("finish")
            .step("escalate")
            .build()
        )
        .build()
    )

    first = await engine.run("timed_flow", {})
    assert first.status == "requires_user_input"
    assert calls["gate"] == 1, "initial ask is the only invocation so far"

    outcome = await engine.deliver_wait_event(
        first.wait_handle.wait_id, {"kind": "timeout", "event_id": "evt-t", "payload": None}
    )
    assert outcome.kind == "executed"
    assert outcome.run_result.status == "completed"
    assert outcome.run_result.output.get("escalated") is True
    assert calls["gate"] == 1, (
        "timeout resolution must NOT re-enter the gate capability — decorations bind at "
        "invocation time and the timeout path has no invocation"
    )
    assert any(e.decision == "wait:timeout_route" for e in outcome.run_result.trace)


# ======================================================================================
# FENCE-3 (settled): fallback stays on base run context — documented + source-locked
# ======================================================================================


def test_node_context_binding_tiers_are_documented_and_fallback_stays_unbound():
    """The three invocation tiers (universal door / declared-node door / dynamic selection)
    are a documented contract, and the evaluator's dynamic fallback deliberately uses the
    UNIVERSAL door: rerouting it through invoke_bound would silently apply the evaluator
    node's model profile/memory/injections to a foreign capability. If either side changes,
    the documented decision must be revisited — not drift."""

    import inspect
    from pathlib import Path

    from ai_workflow_engine.nodes import evaluate as evaluate_module

    arch_doc = (
        Path(__file__).resolve().parents[3] / "docs" / "workflow-engine-architecture.md"
    ).read_text(encoding="utf-8")
    assert "### Node-Context Binding Tiers" in arch_doc
    for anchor in (
        "invoke_bound",
        "base run context",
        "wait-timeout",
        "model_binding",
    ):
        assert anchor in arch_doc, f"tier contract lost its documented anchor: {anchor!r}"

    source = inspect.getsource(evaluate_module)
    fallback_branch = source.split('if action == "fallback":', 1)[1].split("if action ==", 1)[0]
    assert "services.runtime.invoke(" in fallback_branch, (
        "FENCE-3 settled semantics: the dynamic fallback must run on the base run context "
        "through the universal door"
    )
    assert "invoke_bound" not in fallback_branch, (
        "dynamic fallback must NOT be routed through the declared-node door — that would "
        "bind the evaluator's own profile/memory/injections to a different capability"
    )


# ======================================================================================
# Existing loud boundary: unknown profile on a hand-declared human node (not RED)
# ======================================================================================


def test_unknown_model_profile_on_hand_declared_human_fails_loudly_at_build():
    """The registration-time unknown-profile check already covers ALL node kinds; activating
    binding on human nodes must keep this boundary loud (confirmation, not a new fence)."""

    definition = _with_decoration(
        WorkflowBuilder("ghost_flow").human("ask", wait_policy=LocalWaitPolicy()).build(),
        "ask",
        model_profile="ghost_profile",
    )
    builder = WorkflowEngineBuilder()
    builder.register_capability("ask", lambda _c, _p: ClarificationOut(status="pending"), kind="deterministic")
    builder.register_workflow(definition)
    with pytest.raises(ValueError, match="ghost_profile"):
        builder.build()
