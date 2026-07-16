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
# B2 — lifecycle state-algebra fences (per-rule, distinct sentinels, 4 segments)
# ======================================================================================


async def test_state_algebra_rules_hold_across_retrace_and_three_suspensions():
    """One lifecycle-rich run proves each declared write rule (lifecycle_state_algebra.json)
    with DISTINCT sentinels — 4 segments (3 local waits), one retrace round:

    - accumulate(key=node_id): a retraced node's key is replaced; SIBLING keys survive
    - accumulate(append): node_results keeps one entry PER VISIT; artifacts from BOTH
      rounds survive the retrace (the GAP-1 loss class, fenced at the state level)
    - replace: the running payload is the LATEST completed output
    - reset(segment) one-shot latch: a resume event reaches ONLY the suspension it answers —
      never the retraced re-ask, never a later wait in the same run
    """

    from ai_workflow_engine import WorkflowArtifact

    review_events: list[Any] = []
    signoff_events: list[Any] = []
    collect_rounds = {"n": 0}

    def collect(_context, _payload):
        collect_rounds["n"] += 1
        n = collect_rounds["n"]
        return CapabilityResult(
            status="accepted",
            output={"round": n},
            artifacts=[
                WorkflowArtifact(path=f"/evidence/round-{n}.txt", kind="reference", owner_node="collect")
            ],
        )

    def review(context, _payload):
        review_events.append(context.metadata.get("resume_event"))
        return _answer_or_pend(context)

    def signoff(context, _payload):
        signoff_events.append(context.metadata.get("resume_event"))
        return _answer_or_pend(context)

    gate_calls = {"n": 0}

    def gate(_context, _payload):
        gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if gate_calls["n"] > 1 else "rejected",
            error="deepen the evidence",
        )

    def fin(_context, payload):
        # input_key="collect": reads the ACTUAL node_outputs channel — the only public
        # observer of the accumulate(key=node_id) rule. If a wrong-rule write clobbers
        # sibling keys, this read returns None, not round-2 evidence.
        return {"shipped": True, "evidence": payload}

    def audit(_context, payload):
        # input_key="__input__": the pinned original input must survive every merge.
        return {"original": payload, "audited": True}

    engine = (
        WorkflowEngineBuilder()
        .register_capability("collect", collect, kind="deterministic")
        .register_capability("review", review, kind="deterministic")
        .register_capability("signoff", signoff, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_capability("fin", fin, kind="deterministic")
        .register_capability("audit", audit, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("algebra_flow")
            .step("collect")
            .human("review", wait_policy=LocalWaitPolicy())
            .evaluate("qa", target="collect", evaluator="gate", on_reject=Retrace("collect"))
            .human("signoff", wait_policy=LocalWaitPolicy())
            .step("fin", input_key="collect")
            .step("audit", input_key="__input__")
            .build()
        )
        .build()
    )

    seg1 = await engine.run("algebra_flow", {"goal": "ship it"})
    assert seg1.status == "requires_user_input"
    seg2 = await engine.resume(seg1.snapshot, "review-round-1")
    assert seg2.status == "requires_user_input", "retrace must re-suspend at the review gate"
    seg3 = await engine.resume(seg2.snapshot, "review-round-2")
    assert seg3.status == "requires_user_input", "the accepted round must reach the signoff gate"
    final = await engine.resume(seg3.snapshot, "signoff-final")
    assert final.status == "completed"

    # reset(segment) one-shot latch — each event reaches exactly its own suspension
    assert review_events == [None, "review-round-1", None, "review-round-2"], (
        "review must see its ask/answer pairs only: no event on the initial ask, no LEAKED "
        "event on the retraced re-ask"
    )
    assert signoff_events == [None, "signoff-final"], (
        "a later wait must never see an earlier wait's resume event"
    )

    # accumulate(append): one node_results entry per VISIT, in causal order
    visits = [(r.node_id, r.status) for r in final.node_results]
    per_node = {}
    for node_id, status in visits:
        per_node.setdefault(node_id, []).append(status)
    assert per_node["collect"] == ["accepted", "accepted"], "one entry per collect round"
    assert per_node["review"] == [
        "requires_user_input",
        "accepted",
        "requires_user_input",
        "accepted",
    ], "review history keeps all four visits, not latest-per-node"
    assert per_node["signoff"] == ["requires_user_input", "accepted"]
    assert per_node["fin"] == ["accepted"]
    assert per_node["audit"] == ["accepted"]
    order = [node_id for node_id, _ in visits]
    collect_visits = [i for i, n in enumerate(order) if n == "collect"]
    assert len(collect_visits) == 2, "exactly two collect rounds"
    assert collect_visits[0] < order.index("review"), "history preserves causal order"
    assert collect_visits[1] < order.index("signoff"), (
        "the retraced second collect round happens before signoff ever runs"
    )

    # accumulate(append): artifacts from BOTH rounds survive the retrace
    artifact_paths = sorted(a.path for a in final.artifacts)
    assert artifact_paths == ["/evidence/round-1.txt", "/evidence/round-2.txt"], (
        "evidence must accumulate across retrace rounds — losing round-1 artifacts is the "
        "GAP-1 loss class at the artifact channel"
    )

    # replace: the terminal payload is the LATEST completed output; its 'original' field
    # proves the pinned '__input__' key survived every merge, and fin's 'evidence' field
    # proves cross-node input_key reads see the retraced node's ROUND-2 value while the
    # sibling keys stayed alive (accumulate(key=node_id) through the public door)
    assert final.output == {
        "original": {"goal": "ship it"},
        "audited": True,
    }
    fin_output = [r.output for r in final.node_results if r.node_id == "fin"][-1]
    assert fin_output == {"shipped": True, "evidence": {"round": 2}}, (
        "input_key readers must see the retraced node's latest value — a wrong-rule write "
        "that clobbers sibling node_outputs keys breaks exactly this"
    )

    # accumulate(key=node_id) with sibling survival, proven through the node results the
    # engine exposes: the retraced collect key was REPLACED (its terminal record is round 2)
    # while the untouched sibling outputs survived alongside it.
    collect_final = [r.output for r in final.node_results if r.node_id == "collect"][-1]
    assert collect_final == {"round": 2}, "revisit replaces the retraced node's own value"
    review_final = [r.output for r in final.node_results if r.node_id == "review"][-1]
    review_value = (
        review_final.get("value") if isinstance(review_final, dict)
        else getattr(review_final, "value", None)
    )  # pre-suspension records cross the snapshot as wire data (dicts), later ones stay typed
    assert review_value == "review-round-2", (
        "sibling channel values from other nodes survive the retrace"
    )


async def test_stale_retrace_provenance_never_redelivers_on_loop_revisit():
    """reset(consumption) fence for pending_retrace_provenance: after the retraced
    invocation consumed it, a LATER same-segment revisit of the same node (loop-back via a
    branch, not a retrace) must see None — stale round-1 provenance re-delivered to an
    unrelated visit would misinform the capability about WHY it is running. Found by a
    surviving mutation (consumption-clear removal was unobservable in suspension flows)."""

    provenance_per_visit: list[Any] = []

    def draft(context, _payload):
        prov = getattr(context, "retrace_provenance", None)
        provenance_per_visit.append(None if prov is None else prov.round)
        return {"draft": len(provenance_per_visit)}

    gate_calls = {"n": 0}

    def gate(_context, _payload):
        gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if gate_calls["n"] > 1 else "rejected",
            error="one more retrace round",
        )

    script = ["again", "done"]

    engine_builder = WorkflowEngineBuilder()
    engine_builder.register_capability("draft", draft, kind="deterministic")
    engine_builder.register_capability("gate", gate, kind="llm")
    engine_builder.register_capability("fin", lambda _c, _p: "fin", kind="deterministic")
    engine_builder.register_guard("loop", lambda _p: script.pop(0) if script else "done")
    engine_builder.register_workflow(
        WorkflowBuilder("loop_after_retrace")
        .step("draft")
        .evaluate("qa", target="draft", evaluator="gate", on_reject=Retrace("draft"))
        .branch("loop", {"again": "draft", "done": "fin"}, bounds={"again": 1})
        .step("fin")
        .build()
    )
    engine = engine_builder.build()

    result = await engine.run("loop_after_retrace", {"seed": 1})
    assert result.status == "completed"
    assert provenance_per_visit == [None, 1, None], (
        "provenance is consumed by the retraced visit ONLY — the loop-back third visit must "
        "not receive the stale round-1 provenance"
    )


async def test_retrace_rounds_accumulate_until_the_cap_trips_on_a_later_rejection():
    """accumulate(counter) fence for eval_counters, owner nodes/evaluate.py: with
    max_retrace=1 and an ALWAYS-rejecting gate, round 1 must retrace and the SECOND
    rejection must exhaust (counter 1+1 > 1) — terminating with the eval-exhaustion error
    after exactly two target executions. If rounds never persist, the machine keeps
    retracing until the recursion policy stops it: more executions, different error. Found
    by a surviving mutation — existing exhaustion tests all used caps of 0, which trip on
    the FIRST rejection and never need the persisted counter."""

    draft_runs = {"n": 0}

    def draft(_context, _payload):
        draft_runs["n"] += 1
        return {"attempt": draft_runs["n"]}

    def gate(_context, _payload):
        return CapabilityResult(status="rejected", error="never good enough")

    engine = (
        WorkflowEngineBuilder()
        .register_capability("draft", draft, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_capability("fin", lambda _c, _p: "fin", kind="deterministic")
        .register_workflow(
            WorkflowBuilder("bounded_retrace")
            .step("draft")
            .evaluate(
                "qa",
                target="draft",
                evaluator="gate",
                on_reject=Retrace("draft", max_retrace=1),
            )
            .step("fin")
            .build()
        )
        .build()
    )

    result = await engine.run("bounded_retrace", {"seed": 1})
    assert result.status == "failed"
    assert "evaluation policy exhausted" in (result.error or ""), (
        "the SECOND rejection must exhaust the persisted retrace counter — not loop into "
        "the recursion backstop"
    )
    assert draft_runs["n"] == 2, (
        "exactly one original + one retraced execution: a non-persisting round counter "
        "would keep re-executing the target"
    )


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


# ======================================================================================
# IBR-1 — every node kind consumes or rejects the same context-binding fields explicitly
# ======================================================================================


async def test_fanout_applies_all_declared_context_bindings_per_item():
    """Fanout's item capability is declared by the node, so every item crosses the bound
    door independently. Concurrency/gather stays shared, while context and model-binding
    trace attribution remain per item."""

    visits: list[dict[str, Any]] = []

    def seed(_context, _payload):
        return {"items": [1, 2]}

    def make_plan(_context, _payload):
        return PlanArtifact(
            goal="inspect every frame",
            tasks=[
                PlanTask(
                    task_id="prepare",
                    description="prepare fanout inputs",
                    capability="prepare",
                    payload={"ready": True},
                )
            ],
        )

    def prepare(_context, payload):
        return payload

    def inspect_item(context, item):
        visits.append(
            {
                "item": item,
                "plan": context.metadata.get("plan"),
                "machine": context.metadata.get("machine"),
                "memory": context.metadata.get("agent_memory"),
                "profile": None if context.model_profile is None else context.model_profile.name,
            }
        )
        return {"item": item}

    definition = (
        WorkflowBuilder("fanout_context")
        .step("seed")
        .plan("plan_node", capability="make_plan")
        .fanout(
            "inspect",
            capability="inspect_item",
            items_key="seed.items",
            max_parallel=2,
            inject_plan=True,
            inject_machine=True,
            memory="structured_state",
            model_profile="fanout_probe",
            description="inspect every declared item",
        )
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("fanout_context"))
        .with_model_profile(
            ModelProfile(name="fanout_probe", provider="ollama", model="probe-1", temperature=0.0)
        )
        .register_capability("seed", seed, kind="deterministic")
        .register_capability(
            "make_plan",
            make_plan,
            spec=CapabilitySpec(name="make_plan", kind="llm", is_planner=True),
        )
        .register_capability("prepare", prepare, kind="deterministic")
        .register_capability("inspect_item", inspect_item, kind="deterministic")
        .register_workflow(definition)
        .build()
    )

    result = await engine.run("fanout_context", {})

    assert result.status == "completed"
    assert sorted(row["item"] for row in visits) == [1, 2]
    for row in visits:
        assert "inspect every frame" in row["plan"]
        assert "state: inspect (fanout)" in row["machine"]
        assert "inspect every declared item" in row["machine"]
        assert row["memory"] == "structured_state"
        assert row["profile"] == "fanout_probe"
    bindings = [
        event
        for event in result.trace
        if event.node == "inspect" and event.decision == "model_binding"
    ]
    assert len(bindings) == 2, "model binding must be attributed once per fanout item"


async def test_subworkflow_applies_plan_and_machine_cards_to_child_context():
    """A subworkflow binds machine metadata to the child run, but is not itself a
    capability and therefore has no capability-only memory/model binding."""

    seen: list[dict[str, Any]] = []

    def make_plan(_context, _payload):
        return PlanArtifact(
            goal="delegate the approved slice",
            tasks=[
                PlanTask(
                    task_id="prepare",
                    description="prepare child input",
                    capability="prepare",
                    payload={"ready": True},
                )
            ],
        )

    def prepare(_context, payload):
        return payload

    def child_step(context, payload):
        seen.append(
            {
                "plan": context.metadata.get("plan"),
                "machine": context.metadata.get("machine"),
            }
        )
        return payload

    child = WorkflowBuilder("child_context").step("child_step").build()
    parent = (
        WorkflowBuilder("parent_context")
        .plan("plan_node", capability="make_plan")
        .subworkflow(
            "delegate",
            workflow=child,
            inject_plan=True,
            inject_machine=True,
            description="delegate into the child machine",
        )
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("parent_context"))
        .register_capability(
            "make_plan",
            make_plan,
            spec=CapabilitySpec(name="make_plan", kind="llm", is_planner=True),
        )
        .register_capability("prepare", prepare, kind="deterministic")
        .register_capability("child_step", child_step, kind="deterministic")
        .register_workflow(child)
        .register_workflow(parent)
        .build()
    )

    result = await engine.run("parent_context", {"value": 1})

    assert result.status == "completed"
    assert len(seen) == 1
    assert "delegate the approved slice" in seen[0]["plan"]
    assert "state: delegate (subworkflow)" in seen[0]["machine"]
    assert "delegate into the child machine" in seen[0]["machine"]


@pytest.mark.parametrize("field,value", [("memory", "structured_state"), ("model_profile", "x")])
def test_subworkflow_rejects_capability_only_context_bindings(field, value):
    base = (
        WorkflowBuilder("bad_parent")
        .subworkflow("delegate", workflow="child")
        .build()
    )
    invalid = _with_decoration(base, "delegate", **{field: value})

    errors = invalid.validate_graph()

    assert any(
        "subworkflow" in error and field in error and "does not support" in error
        for error in errors
    ), f"{field} must fail at graph validation instead of being silently ignored: {errors}"
