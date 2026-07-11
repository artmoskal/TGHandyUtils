"""W1 — declared wait contract and public models (Phase W, durable-wait coordination)."""

import json as _json

import pytest
from pydantic import ValidationError

from ai_workflow_engine import (
    DurableWaitPolicy,
    LocalWaitPolicy,
    WaitClaim,
    WaitEvent,
    WaitHandle,
    WaitHealth,
    WaitReceipt,
    WaitRecord,
    WorkflowBuilder,
    WorkflowEngine,
    WorkflowValidationError,
)
from ai_workflow_engine.waits import WAIT_STATUSES, wait_policy_from

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ W1.1 model contract


def test_wait_models_are_strict_and_round_trip():
    policy = DurableWaitPolicy(timeout_s=3600, signal_correlation={"ticket": "T-1"})
    record = WaitRecord(
        wait_id="w1",
        run_id="r1",
        workflow_id="wf",
        suspended_node="approval",
        policy=policy,
        deadline_at="2026-07-11T13:00:00+00:00",
    )
    for model in (
        policy,
        record,
        WaitEvent(kind="signal", event_id="e1", payload={"ok": True}),
        WaitReceipt(
            registration_id="reg1", wait_id="w1", wait_version=1,
            accepted_deadline="2026-07-11T13:00:00+00:00", adapter_id="inmem",
        ),
        WaitClaim(wait_id="w1", wait_version=1, event_id="e1"),
        WaitHandle(
            wait_id="w1", run_id="r1", workflow_id="wf",
            suspended_node="approval", deadline_at="2026-07-11T13:00:00+00:00",
        ),
        WaitHealth(pending=1, claimed=0, overdue=0),
    ):
        dumped = _json.loads(model.model_dump_json())
        assert type(model).model_validate(dumped) == model, type(model).__name__
        with pytest.raises(ValidationError):
            type(model).model_validate({**dumped, "surprise": 1})


def test_wait_vocabulary_is_closed_and_overdue_is_not_a_status():
    assert WAIT_STATUSES == ("pending", "claimed", "completed", "failed", "cancelled")
    assert "overdue" not in WAIT_STATUSES, "overdue is DERIVED health, never a stored status"
    assert "expired" not in WAIT_STATUSES, "resolution reason is separate from status (C5)"
    base = dict(
        wait_id="w", run_id="r", workflow_id="wf", suspended_node="n",
        policy=DurableWaitPolicy(timeout_s=1), deadline_at="2026-07-11T13:00:00+00:00",
    )
    with pytest.raises(ValidationError):
        WaitRecord(**base, status="overdue")
    record = WaitRecord(**base, status="completed", resolution_kind="timeout")
    assert record.resolution_kind == "timeout"
    with pytest.raises(ValidationError):
        WaitRecord(**base, resolution_kind="gave_up")


def test_durable_policy_requires_finite_positive_timeout():
    for bad in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            DurableWaitPolicy(timeout_s=bad)
    with pytest.raises(ValidationError):
        DurableWaitPolicy(timeout_s=60, max_resume_attempts=0)
    assert DurableWaitPolicy(timeout_s=0.5).timeout_s == 0.5


def test_wait_policy_from_normalizes_and_rejects_loudly():
    assert wait_policy_from({"mode": "local"}).mode == "local"
    assert wait_policy_from({"mode": "durable", "timeout_s": 5}).timeout_s == 5
    with pytest.raises(ValueError, match="mode 'local' or 'durable'"):
        wait_policy_from({"mode": "eventually"})
    with pytest.raises(TypeError):
        wait_policy_from(42)


# ------------------------------------------------------- W1.2/W1.3 graph declarations


def test_human_node_requires_an_explicit_wait_policy():
    with pytest.raises(TypeError):
        WorkflowBuilder("w").human("ask")  # v0.9 W1: no silent default, ever

    definition = (
        WorkflowBuilder("w").human("ask", wait_policy=LocalWaitPolicy()).build()
    )
    assert definition.node("ask").wait_policy == {"mode": "local"}
    assert definition.validate_graph() == []


def test_durable_wait_declares_exactly_one_timeout_transition_as_machine_data():
    definition = (
        WorkflowBuilder("w")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=900), timeout_to="escalate")
        .step("finish")
        .step("escalate")
        .build()
    )
    assert definition.validate_graph() == []
    timeouts = [t for t in definition.transitions if t.policy == "on_timeout"]
    assert len(timeouts) == 1
    assert timeouts[0].source == "gate" and timeouts[0].target == "escalate"
    assert "900" in (timeouts[0].description or "")

    # serialization round-trip keeps the declared route (machine-as-data)
    from ai_workflow_engine import WorkflowDefinition

    reloaded = WorkflowDefinition.model_validate_json(definition.model_dump_json())
    assert [t.policy for t in reloaded.transitions].count("on_timeout") == 1
    assert reloaded.validate_graph() == []


def test_wait_declaration_failure_matrix():
    from ai_workflow_engine import Transition, WorkflowDefinition, WorkflowNode

    with pytest.raises(WorkflowValidationError, match="requires timeout_to"):
        WorkflowBuilder("w").human("gate", wait_policy=DurableWaitPolicy(timeout_s=60))
    with pytest.raises(WorkflowValidationError, match="must not declare timeout_to"):
        WorkflowBuilder("w").human("gate", wait_policy=LocalWaitPolicy(), timeout_to="x")

    # hand-built definitions get the SAME validation loudly
    no_policy = WorkflowDefinition(
        workflow_id="w",
        nodes=[WorkflowNode(id="ask", kind="human")],
        entry="ask",
        transitions=[],
    )
    assert any("no wait_policy" in e for e in no_policy.validate_graph())

    foreign = WorkflowDefinition(
        workflow_id="w",
        nodes=[WorkflowNode(id="s", kind="step", wait_policy={"mode": "local"})],
        entry="s",
        transitions=[],
    )
    assert any("must not declare wait_policy" in e for e in foreign.validate_graph())

    stray_timeout = WorkflowDefinition(
        workflow_id="w",
        nodes=[
            WorkflowNode(id="ask", kind="human", wait_policy={"mode": "local"}),
            WorkflowNode(id="b", kind="step"),
        ],
        entry="ask",
        transitions=[Transition(source="ask", target="b", policy="on_timeout")],
    )
    assert any("must not have on_timeout" in e for e in stray_timeout.validate_graph())


def test_generic_wait_verb_is_the_same_machinery():
    definition = (
        WorkflowBuilder("w")
        .wait("webhook_gate", wait_policy=LocalWaitPolicy())
        .build()
    )
    assert definition.node("webhook_gate").kind == "human"
    assert definition.validate_graph() == []


def test_timeout_route_is_rendered_in_the_machine_diagram():
    from ai_workflow_viewer import workflow_to_mermaid

    definition = (
        WorkflowBuilder("w")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("done")
        .step("escalate")
        .build()
    )
    diagram = workflow_to_mermaid(definition)
    assert "timeout" in diagram, "the declared timeout route must be visible machine data"


# --------------------------------------------------------------- W1 execution boundary


async def test_durable_wait_execution_is_blocked_until_w2_registration():
    """W1 boundary: a durable wait DECLARES fine but cannot EXECUTE before coordinator
    registration exists — the run fails before any capability executes, and no raw
    snapshot can ever leak for a durable wait."""

    engine = WorkflowEngine()
    calls = {"gate": 0}

    def gate(_context, _payload):
        calls["gate"] += 1
        return {"status": "pending"}

    engine.register_capability("gate", gate)
    engine.register_capability("escalate", lambda ctx, p: {"escalated": True})
    engine.register_workflow(
        WorkflowBuilder("durable_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("escalate")
        .build()
    )

    result = await engine.run("durable_flow", {})

    assert result.status in ("failed", "rejected")
    assert "coordinator registration" in (result.error or "")
    assert calls["gate"] == 0, "preflight must reject BEFORE any capability executes"
    assert result.snapshot is None and result.wait_handle is None


async def test_local_wait_keeps_v08_suspend_resume_semantics():
    """Degradation gate: LocalWaitPolicy is byte-equivalent to the old .human behavior."""

    from pydantic import BaseModel

    class Gate(BaseModel):
        status: str
        value: str = ""

    engine = WorkflowEngine()

    def ask(context, _payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        return Gate(status="answered", value=str(event))

    engine.register_capability("ask", ask)
    engine.register_capability("finish", lambda ctx, p: {"answer": p.value})
    engine.register_workflow(
        WorkflowBuilder("local_flow")
        .human("ask", wait_policy=LocalWaitPolicy())
        .step("finish")
        .build()
    )

    first = await engine.run("local_flow", {})
    assert first.status == "requires_user_input"
    assert first.snapshot is not None and first.wait_handle is None

    resumed = await engine.resume(first.snapshot, "blue")
    assert resumed.status == "completed"
    assert resumed.output == {"answer": "blue"}


def test_run_result_never_carries_both_public_wait_doors():
    from ai_workflow_engine.executor import WorkflowRunResult
    from ai_workflow_engine.snapshot import MachineSnapshot

    snapshot = MachineSnapshot(
        workflow_id="w", suspended_node="ask", node_status={}, routes={},
        node_results=[], artifacts=[],
    )
    with pytest.raises(ValidationError, match="exactly one public wait door"):
        WorkflowRunResult(
            workflow_id="w",
            status="requires_user_input",
            snapshot=snapshot,
            wait_handle=WaitHandle(
                wait_id="x", run_id="r", workflow_id="w",
                suspended_node="ask", deadline_at="2026-07-11T13:00:00+00:00",
            ),
        )


# ------------------------------------------------- W1R: independent-gate reproducers


def test_wait_event_payload_rejects_raw_bytes_and_media():
    """W1R.1 (codex reproducer): durable coordinators persist the payload — raw bytes and
    transport media are unrepresentable at model construction."""

    from ai_workflow_engine import ImageInput

    with pytest.raises(ValidationError):
        WaitEvent(kind="signal", event_id="e1", payload=b"raw-bytes")
    with pytest.raises(ValidationError):
        WaitEvent(kind="signal", event_id="e1", payload={"nested": [b"raw"]})
    with pytest.raises(ValidationError):
        WaitEvent(
            kind="signal",
            event_id="e1",
            payload={"img": ImageInput(source="base64", data="QUFB", media_type="image/png")},
        )
    ok = WaitEvent(kind="signal", event_id="e1", payload={"approved": True, "note": "fine"})
    assert ok.payload["approved"] is True


def test_wait_timestamps_must_be_timezone_aware_datetimes():
    """W1R.1 (codex reproducer): 'not-a-date' and naive datetimes fail; aware ISO strings
    parse and JSON round-trip losslessly."""

    from datetime import datetime, timezone

    base = dict(
        wait_id="w", run_id="r", workflow_id="wf", suspended_node="n",
        policy=DurableWaitPolicy(timeout_s=1),
    )
    with pytest.raises(ValidationError):
        WaitRecord(**base, deadline_at="not-a-date")
    with pytest.raises(ValidationError):
        WaitRecord(**base, deadline_at=datetime(2026, 7, 11, 13, 0, 0))  # naive
    record = WaitRecord(**base, deadline_at="2026-07-11T13:00:00+00:00")
    assert record.deadline_at.tzinfo is not None
    reloaded = WaitRecord.model_validate_json(record.model_dump_json())
    assert reloaded == record
    with pytest.raises(ValidationError):
        WaitReceipt(
            registration_id="x", wait_id="w", wait_version=1,
            accepted_deadline="tomorrow-ish", adapter_id="a",
        )
    assert WaitHealth(pending=0, claimed=0, overdue=0, oldest_pending_deadline=None)


def test_run_result_wait_door_matrix_is_status_dependent_and_typed():
    """W1R.2 (codex reproducer): exactly-one door on requires_user_input; NO door on any
    other status; forged dict handles rejected; model_copy cannot bypass."""

    from datetime import datetime, timezone

    from ai_workflow_engine.executor import WorkflowRunResult
    from ai_workflow_engine.snapshot import MachineSnapshot

    snapshot = MachineSnapshot(
        workflow_id="w", suspended_node="ask", node_status={}, routes={},
        node_results=[], artifacts=[],
    )
    handle = WaitHandle(
        wait_id="x", run_id="r", workflow_id="w", suspended_node="ask",
        deadline_at=datetime(2026, 7, 11, 13, 0, tzinfo=timezone.utc),
    )

    # suspended with NEITHER door
    with pytest.raises(ValidationError, match="exactly one public wait door"):
        WorkflowRunResult(workflow_id="w", status="requires_user_input")
    # suspended with BOTH doors
    with pytest.raises(ValidationError):
        WorkflowRunResult(
            workflow_id="w", status="requires_user_input", snapshot=snapshot, wait_handle=handle
        )
    # completed carrying a snapshot / failed carrying a handle
    with pytest.raises(ValidationError, match="must carry no wait door"):
        WorkflowRunResult(workflow_id="w", status="completed", snapshot=snapshot)
    with pytest.raises(ValidationError, match="must carry no wait door"):
        WorkflowRunResult(workflow_id="w", status="failed", wait_handle=handle)
    # forged dict pretending to be a handle
    with pytest.raises(ValidationError):
        WorkflowRunResult(
            workflow_id="w", status="requires_user_input", wait_handle={"anything": "goes"}
        )

    # valid local and durable envelopes serialize
    local = WorkflowRunResult(workflow_id="w", status="requires_user_input", snapshot=snapshot)
    durable = WorkflowRunResult(workflow_id="w", status="requires_user_input", wait_handle=handle)
    assert local.snapshot is not None and durable.wait_handle.wait_id == "x"

    # model_copy bypass is closed: flipping status away with a door attached fails loudly
    with pytest.raises(ValueError, match="must carry no wait door"):
        local.model_copy(update={"status": "completed"})
    completed = local.model_copy(update={"status": "completed", "snapshot": None})
    assert completed.status == "completed"
