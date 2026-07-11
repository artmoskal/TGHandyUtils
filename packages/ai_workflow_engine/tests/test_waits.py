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
        definition_digest="digest-conf", policy=policy,
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
        definition_digest="digest-base",
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
    assert "WaitCoordinator" in (result.error or "")
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
        definition_digest="digest-ts",
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


def test_wait_handle_is_genuinely_typed_in_runtime_schema_and_static_surface():
    """W1R.2b (codex reproducer #1): the annotation, the generated JSON schema, and static
    consumers all see the SAME WaitHandle contract — no Optional[Any]."""

    import typing

    from ai_workflow_engine.executor import WorkflowRunResult
    from ai_workflow_engine.wait_contract import WaitHandle as ContractHandle

    hints = typing.get_type_hints(WorkflowRunResult)
    assert hints["wait_handle"] == typing.Optional[ContractHandle]
    schema = _json.dumps(WorkflowRunResult.model_json_schema())
    assert "WaitHandle" in schema, "the generated schema must carry the handle contract"
    # the waits module re-exports the SAME class — one contract, no duplicate model
    assert WaitHandle is ContractHandle


def test_model_copy_updates_are_fully_validated_not_just_door_checked():
    """W1R.2b (codex reproducer #2): a copied update cannot smuggle a forged handle dict or
    an invalid status — copies reconstruct through full validation."""

    from datetime import datetime, timezone

    from ai_workflow_engine.executor import WorkflowRunResult

    handle = WaitHandle(
        wait_id="x", run_id="r", workflow_id="w", suspended_node="ask",
        deadline_at=datetime(2026, 7, 11, 13, 0, tzinfo=timezone.utc),
    )
    durable = WorkflowRunResult(workflow_id="w", status="requires_user_input", wait_handle=handle)

    with pytest.raises(ValidationError):
        durable.model_copy(update={"wait_handle": {"forged": "dict"}})
    with pytest.raises(ValidationError):
        durable.model_copy(update={"status": "definitely_not_a_status"})
    # status-neutral copies stay equivalent
    stamped = durable.model_copy(update={"observation_bundle_path": "/tmp/b"})
    assert stamped.wait_handle == handle and stamped.observation_bundle_path == "/tmp/b"


# ============================================================ W2: coordinator + registration


def _clock():
    from datetime import datetime, timezone

    return lambda: datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def _durable_engine(coordinator=None):
    from ai_workflow_engine import InMemoryWaitCoordinator, WorkflowEngineBuilder

    builder = WorkflowEngineBuilder()
    if coordinator is not None:
        builder.with_wait_coordinator(coordinator)
    from pydantic import BaseModel as _BM

    class _Gate(_BM):
        status: str = "pending"

    builder.register_capability("gate", lambda ctx, p: _Gate())
    builder.register_capability("escalate", lambda ctx, p: {"escalated": True})
    builder.register_workflow(
        WorkflowBuilder("durable_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("escalate")
        .build()
    )
    return builder.build()


async def test_registered_durable_wait_returns_handle_only_and_traces_registration():
    """W2.3 happy path: register BEFORE exposing — the public result carries a matching
    typed handle, NO snapshot, and the run trace records the registration."""

    from ai_workflow_engine import InMemoryWaitCoordinator

    coordinator = InMemoryWaitCoordinator(clock=_clock())
    engine = _durable_engine(coordinator)

    result = await engine.run("durable_flow", {})

    assert result.status == "requires_user_input"
    assert result.snapshot is None, "a registered wait must NEVER expose the raw snapshot"
    handle = result.wait_handle
    assert handle is not None and handle.suspended_node == "gate"
    stored = await coordinator.get(handle.wait_id)
    assert stored is not None and stored.status == "pending"
    assert stored.deadline_at == handle.deadline_at, "handle must echo the registered deadline"
    assert await coordinator.load_snapshot(handle.wait_id), "the coordinator owns the snapshot"
    assert any(e.decision == "wait:registered" for e in result.trace), (
        "registration must be traced inside the run"
    )
    assert (await coordinator.health()).pending == 1


async def test_registration_failure_fails_the_run_with_no_public_door():
    """W2.3 failure pair: adapter raise AND attestation mismatch each convert the run to
    FAILED with neither snapshot nor handle; the bundle direction stays truthful."""

    from ai_workflow_engine import InMemoryWaitCoordinator

    class ExplodingCoordinator(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json):
            raise RuntimeError("outbox transaction failed")

    engine = _durable_engine(ExplodingCoordinator(clock=_clock()))
    result = await engine.run("durable_flow", {})
    assert result.status == "failed"
    assert "registration failed" in (result.error or "")
    assert result.snapshot is None and result.wait_handle is None
    assert any(e.decision == "wait:registration_failed" for e in result.trace)

    class ForgingCoordinator(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json):
            receipt = await super().register(record, snapshot_json)
            return receipt.model_copy(update={"wait_version": receipt.wait_version + 1})

    engine2 = _durable_engine(ForgingCoordinator(clock=_clock()))
    result2 = await engine2.run("durable_flow", {})
    assert result2.status == "failed"
    assert "attestation mismatch" in (result2.error or "")
    assert result2.snapshot is None and result2.wait_handle is None


async def test_durable_without_coordinator_still_fails_before_any_capability():
    """W2.1: the W1 hard block became coordinator-conditional — absent coordinator still
    fails preflight; local/no-wait engines need zero wait configuration."""

    engine = _durable_engine(coordinator=None)
    result = await engine.run("durable_flow", {})
    assert result.status in ("failed", "rejected")
    assert "WaitCoordinator" in (result.error or "")


def test_builder_rejects_non_conforming_coordinators_loudly():
    from ai_workflow_engine import WorkflowEngineBuilder

    with pytest.raises(TypeError, match="WaitCoordinator protocol"):
        WorkflowEngineBuilder().with_wait_coordinator(object())


async def test_in_memory_coordinator_is_deterministic_and_never_self_fires():
    """W2.2: injected clock, deterministic due(now), duplicate semantics — and a source
    guard: no task/thread/sleep/timer/poll anywhere in the waits module."""

    from datetime import timedelta
    from pathlib import Path

    import ai_workflow_engine.waits as waits_module
    from ai_workflow_engine import InMemoryWaitCoordinator
    from ai_workflow_engine.testing.wait_contract import run_wait_registration_conformance

    clock = _clock()
    await run_wait_registration_conformance(
        lambda: InMemoryWaitCoordinator(clock=clock), clock=clock
    )

    coordinator = InMemoryWaitCoordinator(clock=clock)
    from ai_workflow_engine.waits import DurableWaitPolicy as DWP, WaitRecord

    now = clock()
    record = WaitRecord(
        wait_id="w-due", run_id="r", workflow_id="wf", suspended_node="g",
        definition_digest="digest-conf", policy=DWP(timeout_s=30), created_at=now, deadline_at=now + timedelta(seconds=30),
    )
    await coordinator.register(record, "{}")
    assert await coordinator.due(now) == []
    assert [r.wait_id for r in await coordinator.due(now + timedelta(seconds=31))] == ["w-due"]

    source = Path(waits_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("create_task", "Thread(", "sleep(", "Timer(", "while True"):
        assert forbidden not in source, f"no self-firing machinery in waits.py: {forbidden}"


async def test_broken_adapters_fail_conformance_by_named_invariant():
    """W2.4: deliberately broken fakes each trip the specific invariant they violate."""

    from ai_workflow_engine import InMemoryWaitCoordinator
    from ai_workflow_engine.testing.wait_contract import run_wait_registration_conformance

    clock = _clock()

    class WrongDeadline(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json):
            receipt = await super().register(record, snapshot_json)
            from datetime import timedelta

            return receipt.model_copy(
                update={"accepted_deadline": receipt.accepted_deadline + timedelta(seconds=1)}
            )

    class SilentReplace(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json):
            self._records.pop(record.wait_id, None)
            self._snapshots.pop(record.wait_id, None)
            self._receipts.pop(record.wait_id, None)
            return await super().register(record, snapshot_json)

    class NotIdempotent(InMemoryWaitCoordinator):
        _n = 0

        async def register(self, record, snapshot_json):
            self._records.pop(record.wait_id, None)
            self._snapshots.pop(record.wait_id, None)
            self._receipts.pop(record.wait_id, None)
            receipt = await super().register(record, snapshot_json)
            type(self)._n += 1
            return receipt.model_copy(update={"registration_id": f"reg-{type(self)._n}"})

    class Amnesiac(InMemoryWaitCoordinator):
        async def get(self, wait_id):
            return None

    for broken, fragment in (
        (WrongDeadline, "ACCEPTED deadline"),
        (SilentReplace, "rejected"),
        (NotIdempotent, "idempotent"),
        (Amnesiac, "retrievable"),
    ):
        with pytest.raises(AssertionError, match=fragment.split()[0]):
            await run_wait_registration_conformance(
                lambda broken=broken: broken(clock=clock), clock=clock
            )


# ================================================== W2R: independent-recheck reproducers


def test_builder_rejects_sync_impostor_coordinators():
    """W2R.1 (codex reproducer): runtime protocols check method PRESENCE only — a
    coordinator with synchronous register/get/load_snapshot must be rejected at build."""

    from ai_workflow_engine import WorkflowEngineBuilder

    class SyncImpostor:
        def register(self, record, snapshot_json): ...
        def get(self, wait_id): ...
        def load_snapshot(self, wait_id): ...
        def claim_event(self, wait_id, event, *, lease_until): ...
        def complete(self, wait_id, claim, *, resolution_kind): ...
        def fail(self, wait_id, claim, *, error): ...
        def due(self, now): return []
        def health(self): ...

    with pytest.raises(TypeError, match="must be async"):
        WorkflowEngineBuilder().with_wait_coordinator(SyncImpostor())


async def test_duck_receipt_objects_are_not_accepted_as_attestation():
    """W2R.1 (codex reproducer): a non-WaitReceipt object with matching attributes fails
    the closed attestation boundary — the run fails with no public door."""

    from ai_workflow_engine import InMemoryWaitCoordinator

    class DuckReceipt:
        def __init__(self, record):
            self.registration_id = "duck"
            self.wait_id = record.wait_id
            self.wait_version = record.version
            self.accepted_deadline = record.deadline_at
            self.adapter_id = "duck"

    class DuckCoordinator(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json):
            await super().register(record, snapshot_json)
            return DuckReceipt(record)

    engine = _durable_engine(DuckCoordinator(clock=_clock()))
    result = await engine.run("durable_flow", {})
    assert result.status == "failed"
    assert result.snapshot is None and result.wait_handle is None


async def test_engine_and_coordinator_share_one_injected_wait_clock():
    """W2R.2 (codex reproducer): with a clock fixed in 2036, the stored record and the
    handle deadline are stamped IN 2036 — one time domain, exactly timeout_s apart."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine import InMemoryWaitCoordinator, WorkflowEngineBuilder

    frozen = datetime(2036, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    clock = lambda: frozen  # noqa: E731
    coordinator = InMemoryWaitCoordinator(clock=clock)

    builder = WorkflowEngineBuilder().with_wait_coordinator(coordinator, clock=clock)
    from pydantic import BaseModel as _BM

    class _Gate(_BM):
        status: str = "pending"

    builder.register_capability("gate", lambda ctx, p: _Gate())
    builder.register_capability("escalate", lambda ctx, p: {"escalated": True})
    builder.register_workflow(
        WorkflowBuilder("durable_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("escalate")
        .build()
    )
    engine = builder.build()

    result = await engine.run("durable_flow", {})
    assert result.status == "requires_user_input"
    stored = await coordinator.get(result.wait_handle.wait_id)
    assert stored.created_at == frozen, "record stamps must come from the INJECTED clock"
    assert stored.deadline_at == frozen + timedelta(seconds=60)
    assert result.wait_handle.deadline_at == stored.deadline_at
    assert (await coordinator.due(frozen + timedelta(seconds=61)))[0].wait_id == stored.wait_id


async def test_conformance_rejects_snapshot_loss_and_supports_reconnect():
    """W2R.3: an adapter that drops or alters the snapshot fails BY NAME; a reconnected
    adapter over shared backing sees identical state; stored records are defensively
    copied."""

    from ai_workflow_engine import InMemoryWaitCoordinator
    from ai_workflow_engine.testing.wait_contract import run_wait_registration_conformance

    clock = _clock()

    shared: dict = {}
    await run_wait_registration_conformance(
        lambda: InMemoryWaitCoordinator(clock=clock, shared_state=shared),
        clock=clock,
        reconnect=lambda: InMemoryWaitCoordinator(clock=clock, shared_state=shared),
    )

    class SnapshotDropper(InMemoryWaitCoordinator):
        async def load_snapshot(self, wait_id):
            return None

    class SnapshotMangler(InMemoryWaitCoordinator):
        async def load_snapshot(self, wait_id):
            original = await super().load_snapshot(wait_id)
            return original and original.replace("gate", "hacked")

    for broken, fragment in ((SnapshotDropper, "EXACT registered snapshot"),
                             (SnapshotMangler, "EXACT registered snapshot")):
        with pytest.raises(AssertionError, match="EXACT"):
            await run_wait_registration_conformance(
                lambda broken=broken: broken(clock=clock), clock=clock
            )

    # defensive copies: mutating a returned record does not corrupt the store
    coordinator = InMemoryWaitCoordinator(clock=clock)
    from datetime import timedelta

    from ai_workflow_engine.waits import DurableWaitPolicy as DWP, WaitRecord

    now = clock()
    record = WaitRecord(
        wait_id="w-copy", run_id="r", workflow_id="wf", suspended_node="g",
        definition_digest="digest-conf", policy=DWP(timeout_s=30), created_at=now, deadline_at=now + timedelta(seconds=30),
    )
    await coordinator.register(record, "{}")
    fetched = await coordinator.get("w-copy")
    fetched.status = "cancelled"
    assert (await coordinator.get("w-copy")).status == "pending", "store must be isolated"


async def test_registration_reuse_and_distinct_occurrence_identity():
    """W2R.3: a crash-after-register retry of the SAME suspension reuses the committed
    registration (deterministic wait id); the id is derived from run/node/occurrence."""

    from ai_workflow_engine import InMemoryWaitCoordinator

    calls = {"register": 0}

    class CountingCoordinator(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json):
            calls["register"] += 1
            return await super().register(record, snapshot_json)

    shared: dict = {}
    clock = _clock()
    coordinator = CountingCoordinator(clock=clock, shared_state=shared)
    engine = _durable_engine(coordinator)

    # a TRUE crash-retry replays the SAME inputs — identical goal + seeded run id; the
    # W2B seal rejects anything else as changed machine state (separately test-locked).
    from ai_workflow_engine import WorkflowGoal

    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="approve", metadata={"run_id": "run-fixed"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    assert first.status == "requires_user_input"
    assert calls["register"] == 1
    wait_id = first.wait_handle.wait_id
    from ai_workflow_engine.wait_runtime import DurableWaitRuntime as _DWR

    assert wait_id == _DWR.wait_id_for(first.wait_handle.run_id, "gate", 0), (
        "identity = collision-safe hash of run/node/occurrence, not a random uuid"
    )

    # crash-after-register retry: fresh engine over the same store, SAME inputs
    coordinator2 = CountingCoordinator(clock=clock, shared_state=shared)
    engine2 = _durable_engine(coordinator2)
    second = await engine2.run("durable_flow", {}, goal=goal)
    assert second.status == "requires_user_input"
    assert second.wait_handle.wait_id == wait_id, "same logical suspension reuses ONE registration"
    assert calls["register"] == 1, "no duplicate registration on retry"
    assert any(e.decision == "wait:registration_reused" for e in second.trace)


async def test_registration_failure_projects_a_failed_node_in_the_bundle(tmp_path):
    """W2R.4 (codex): the PROJECTED bundle shows the suspended node FAILED with
    failure_kind=wait_registration_failed — never a suspended node inside a failed run."""

    from ai_workflow_engine import (
        InMemoryWaitCoordinator,
        ObservationConfig,
        WorkflowEngineBuilder,
    )
    from ai_workflow_viewer import FileEventSource, build_observation_graph

    class ExplodingCoordinator(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json):
            raise RuntimeError("outbox transaction failed")

    from pydantic import BaseModel as _BM

    class _Gate(_BM):
        status: str = "pending"

    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(tmp_path))
    )
    builder.with_wait_coordinator(ExplodingCoordinator(clock=_clock()))
    builder.register_capability("gate", lambda ctx, p: _Gate())
    builder.register_capability("escalate", lambda ctx, p: {"escalated": True})
    builder.register_workflow(
        WorkflowBuilder("durable_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("escalate")
        .build()
    )
    engine = builder.build()

    result = await engine.run("durable_flow", {})
    assert result.status == "failed" and result.observation_bundle_path

    run_data = FileEventSource(result.observation_bundle_path).read()
    graph = build_observation_graph(
        run_data.definition, run_data.trace_events, run_data.usage_events,
        run_data.details, run_id=run_data.run_id,
    )
    assert graph.nodes["gate"].status == "failed", (
        f"projected node must be FAILED, not suspended: {graph.nodes['gate'].status}"
    )
    failure_events = [
        e for e in run_data.trace_events
        if e.metadata.get("failure_kind") == "wait_registration_failed"
    ]
    assert failure_events, "the typed failure kind must be in the bundle"


# ================================================== W2A: lifecycle ownership boundary


def test_wait_runtime_never_imports_the_executor():
    """W2A.3: the lifecycle service is executor-free by construction — the resume path is
    an injected port; a compile-time fake satisfies it without any executor import."""

    import ast
    from pathlib import Path

    import ai_workflow_engine.wait_runtime as wait_runtime_module
    from ai_workflow_engine.wait_runtime import ClaimedResumePort

    tree = ast.parse(Path(wait_runtime_module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "executor" in node.module:
            raise AssertionError(f"wait_runtime imports executor: {node.module}")
        if isinstance(node, ast.Import) and any("executor" in a.name for a in node.names):
            raise AssertionError("wait_runtime imports executor")

    class FakeResumePort:
        async def __call__(self, snapshot_json: str, event):
            return {"resumed": True}

    assert isinstance(FakeResumePort(), ClaimedResumePort)


async def test_registration_mechanics_live_in_the_lifecycle_service():
    """W2A.2: identity/reuse/receipt validation are the SERVICE's — callable without any
    executor; W2 behavior stays identical through the delegation (covered by the whole
    W2/W2R suite running unchanged in this same file)."""

    from datetime import timedelta

    from ai_workflow_engine import InMemoryWaitCoordinator
    from ai_workflow_engine.snapshot import MachineSnapshot
    from ai_workflow_engine.wait_runtime import DurableWaitRuntime, WaitRegistrationRequest

    clock = _clock()
    runtime = DurableWaitRuntime(InMemoryWaitCoordinator(clock=clock), clock=clock)
    snapshot_json = MachineSnapshot(
        workflow_id="wf", suspended_node="gate", node_status={}, routes={},
        node_results=[], artifacts=[],
    ).model_dump_json()
    request = WaitRegistrationRequest(
        run_id="run-1", workflow_id="wf", definition_digest="abc123", suspended_node="gate",
        occurrence=0, policy=DurableWaitPolicy(timeout_s=60), snapshot_json=snapshot_json,
    )

    first = await runtime.register_suspension(request)
    assert first.reused is False
    assert first.handle.wait_id == DurableWaitRuntime.wait_id_for("run-1", "gate", 0)
    assert first.deadline_at == clock() + timedelta(seconds=60)

    again = await runtime.register_suspension(request)
    assert again.reused is True and again.handle.wait_id == first.handle.wait_id


# ================================================== W2B: identity/reuse seal reproducers


def _registration_request(**overrides):
    from ai_workflow_engine.snapshot import MachineSnapshot
    from ai_workflow_engine.wait_runtime import WaitRegistrationRequest

    values = dict(
        run_id="run-1", workflow_id="wf", definition_digest="digest-A", suspended_node="gate",
        occurrence=0, policy=DurableWaitPolicy(timeout_s=60),
        snapshot_json=MachineSnapshot(
            workflow_id="wf", suspended_node="gate", node_status={}, routes={},
            node_results=[], artifacts=[],
        ).model_dump_json(),
    )
    values.update(overrides)
    return WaitRegistrationRequest(**values)


async def test_changed_snapshot_or_digest_is_never_silently_reused():
    """W2B.1 (codex reproducer): the crash-retry reuse contract requires EXACT identity —
    a different snapshot or definition digest is a loud integrity error."""

    from ai_workflow_engine import InMemoryWaitCoordinator
    from ai_workflow_engine.wait_runtime import DurableWaitRuntime

    clock = _clock()
    runtime = DurableWaitRuntime(InMemoryWaitCoordinator(clock=clock), clock=clock)
    await runtime.register_suspension(_registration_request())

    changed_snapshot = _registration_request(
        snapshot_json=_registration_request().snapshot_json.replace("gate", "hacked")
    )
    with pytest.raises(RuntimeError, match="DIFFERENT.*snapshot"):
        await runtime.register_suspension(changed_snapshot)

    with pytest.raises(RuntimeError, match="DIFFERENT.*definition_digest"):
        await runtime.register_suspension(_registration_request(definition_digest="digest-B"))


async def test_digest_is_persisted_and_survives_reconnect():
    """W2B.1 (codex reproducer): the definition digest is STORED on the record — W3 can
    reject resume against a changed graph, including after adapter reconnect."""

    from ai_workflow_engine import InMemoryWaitCoordinator
    from ai_workflow_engine.wait_runtime import DurableWaitRuntime

    clock = _clock()
    shared: dict = {}
    runtime = DurableWaitRuntime(
        InMemoryWaitCoordinator(clock=clock, shared_state=shared), clock=clock
    )
    outcome = await runtime.register_suspension(_registration_request())

    reconnected = InMemoryWaitCoordinator(clock=clock, shared_state=shared)
    stored = await reconnected.get(outcome.handle.wait_id)
    assert stored.definition_digest == "digest-A", "digest must be persisted, not discarded"


async def test_terminal_or_claimed_waits_are_never_revived():
    """W2B.2 (codex reproducer): a completed/claimed wait can never come back as a fresh
    requires_user_input handle."""

    from ai_workflow_engine import InMemoryWaitCoordinator
    from ai_workflow_engine.wait_runtime import DurableWaitRuntime

    clock = _clock()
    coordinator = InMemoryWaitCoordinator(clock=clock)
    runtime = DurableWaitRuntime(coordinator, clock=clock)
    outcome = await runtime.register_suspension(_registration_request())

    for status in ("completed", "claimed", "failed", "cancelled"):
        coordinator._records[outcome.handle.wait_id] = coordinator._records[
            outcome.handle.wait_id
        ].model_copy(update={"status": status})
        with pytest.raises(RuntimeError, match="never be revived"):
            await runtime.register_suspension(_registration_request())


def test_wait_ids_are_collision_safe_against_delimiter_games():
    """W2B.2 (codex reproducer): length-prefixed hashing — ("r--a","b") and ("r","a--b")
    produce DIFFERENT ids; same inputs stay deterministic."""

    from ai_workflow_engine.wait_runtime import DurableWaitRuntime as D

    assert D.wait_id_for("r--a", "b", 0) != D.wait_id_for("r", "a--b", 0)
    assert D.wait_id_for("r", "a", 10) != D.wait_id_for("r", "a1", 0)
    assert D.wait_id_for("run", "gate", 1) == D.wait_id_for("run", "gate", 1)


async def test_reference_adapter_is_async_and_alias_free():
    """W2B.3: sync due/health impostors are rejected at composition; input records and
    due() results are defensive copies — no alias can mutate stored state."""

    from datetime import timedelta

    from ai_workflow_engine import InMemoryWaitCoordinator, WorkflowEngineBuilder
    from ai_workflow_engine.waits import DurableWaitPolicy as DWP, WaitRecord

    class SyncDueImpostor(InMemoryWaitCoordinator):
        def due(self, now):  # type: ignore[override]
            return []

    with pytest.raises(TypeError, match="due must be async"):
        WorkflowEngineBuilder().with_wait_coordinator(SyncDueImpostor(clock=_clock()))

    clock = _clock()
    coordinator = InMemoryWaitCoordinator(clock=clock)
    now = clock()
    record = WaitRecord(
        wait_id="w-alias", run_id="r", workflow_id="wf", suspended_node="g",
        policy=DWP(timeout_s=1), definition_digest="d", created_at=now,
        deadline_at=now + timedelta(seconds=1),
    )
    await coordinator.register(record, "{}")
    record.status = "cancelled"  # caller mutates ITS object after registration
    assert (await coordinator.get("w-alias")).status == "pending", "input must be copied"

    due = await coordinator.due(now + timedelta(seconds=2))
    due[0].status = "cancelled"  # caller mutates a query RESULT
    assert (await coordinator.get("w-alias")).status == "pending", "due() must copy"


# ================================================== W2C: identity + receipt isolation


def test_blank_definition_digest_is_rejected_everywhere():
    """W2C.1 (codex reproducer): omitted, empty, and whitespace digests fail on BOTH the
    record and the registration request — blank identity would silently disable
    changed-machine protection."""

    from datetime import timedelta

    from ai_workflow_engine.waits import DurableWaitPolicy as DWP, WaitRecord
    from ai_workflow_engine.wait_runtime import WaitRegistrationRequest

    now = _clock()()
    base = dict(
        wait_id="w", run_id="r", workflow_id="wf", suspended_node="g",
        policy=DWP(timeout_s=1), created_at=now, deadline_at=now + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError):
        WaitRecord(**base)  # omitted
    for blank in ("", "   "):
        with pytest.raises(ValidationError):
            WaitRecord(**base, definition_digest=blank)
        with pytest.raises(ValidationError):
            WaitRegistrationRequest(
                run_id="r", workflow_id="wf", definition_digest=blank,
                suspended_node="g", occurrence=0, policy=DWP(timeout_s=1), snapshot_json="{}",
            )


async def test_duplicate_receipt_is_a_defensive_result():
    """W2C.2 (codex reproducer): mutating the receipt returned by the idempotent duplicate
    path (or the initial path) can never corrupt subsequent results."""

    from datetime import timedelta

    from ai_workflow_engine import InMemoryWaitCoordinator
    from ai_workflow_engine.waits import DurableWaitPolicy as DWP, WaitRecord

    clock = _clock()
    coordinator = InMemoryWaitCoordinator(clock=clock)
    now = clock()
    record = WaitRecord(
        wait_id="w-rcpt", run_id="r", workflow_id="wf", suspended_node="g",
        definition_digest="d", policy=DWP(timeout_s=1),
        created_at=now, deadline_at=now + timedelta(seconds=1),
    )
    first = await coordinator.register(record, "{}")
    first.registration_id = "corrupted"
    duplicate = await coordinator.register(record, "{}")
    assert duplicate.registration_id == "reg-w-rcpt", "stored receipt must be isolated"
    duplicate.adapter_id = "also-corrupted"
    third = await coordinator.register(record, "{}")
    assert third.adapter_id == "in_memory"


# ================================================== W3: delivery, leases, crash recovery


def _delivery_engine(shared=None, *, clock=None):
    """Durable flow with a REAL post-wait step so deliveries continue the machine."""

    from pydantic import BaseModel as _BM

    from ai_workflow_engine import InMemoryWaitCoordinator, WorkflowEngineBuilder

    clock = clock or _clock()
    coordinator = InMemoryWaitCoordinator(clock=clock, shared_state=shared if shared is not None else {})

    class Gate(_BM):
        status: str
        value: str = ""

    def gate(context, _payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        return Gate(status="answered", value=str(event))

    def finish(context, payload):
        return {
            "answer": payload.value,
            "wait_idempotency": context.metadata.get("wait_idempotency"),
        }

    def escalate(context, _payload):
        return {"escalated": True, "wait_idempotency": context.metadata.get("wait_idempotency")}

    builder = WorkflowEngineBuilder().with_wait_coordinator(coordinator, clock=clock)
    builder.register_capability("gate", gate)
    builder.register_capability("finish", finish)
    builder.register_capability("escalate", escalate)
    builder.register_workflow(
        WorkflowBuilder("durable_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("finish")
        .step("escalate")
        .build()
    )
    return builder.build(), coordinator


async def test_signal_delivery_resumes_the_machine_and_terminalizes_the_wait():
    """W3.2 E2E: signal → claim → private resume → suspended capability re-enters with the
    payload → run completes → wait completed with resolution_kind=signal; the stable
    wait/event idempotency context is visible to post-wait capabilities (W3.4)."""

    engine, coordinator = _delivery_engine()
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    outcome = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-1", "payload": "approved"}
    )

    assert outcome.kind == "executed"
    assert outcome.run_result.status == "completed"
    assert outcome.run_result.output["answer"] == "approved"
    assert outcome.run_result.output["wait_idempotency"] == f"{wait_id}:evt-1", (
        "post-wait capabilities must see the stable wait/event idempotency context"
    )
    stored = await coordinator.get(wait_id)
    assert stored.status == "completed" and stored.resolution_kind == "signal"


async def test_timeout_delivery_takes_the_declared_transition_without_reentering_the_gate():
    """W3.2 E2E: timeout → the DECLARED on_timeout route runs (escalate), the wait
    capability is NOT re-entered, and the wait resolves with resolution_kind=timeout."""

    engine, coordinator = _delivery_engine()
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    outcome = await engine.deliver_wait_event(
        wait_id, {"kind": "timeout", "event_id": "evt-t", "payload": None}
    )

    assert outcome.kind == "executed"
    assert outcome.run_result.status == "completed"
    assert outcome.run_result.output.get("escalated") is True, (
        f"timeout must take the declared route: {outcome.run_result.output}"
    )
    assert outcome.run_result.output["wait_idempotency"] == f"{wait_id}:evt-t"
    stored = await coordinator.get(wait_id)
    assert stored.status == "completed" and stored.resolution_kind == "timeout"
    assert any(e.decision == "wait:timeout_route" for e in outcome.run_result.trace)


async def test_duplicate_and_late_events_are_idempotent_reports_not_reexecution():
    """W3.1: the same event id is a duplicate report; a DIFFERENT late event on a resolved
    wait gets the terminal state — neither executes anything."""

    engine, _coordinator = _delivery_engine()
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    executed = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-1", "payload": "approved"}
    )
    assert executed.kind == "executed"

    duplicate = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-1", "payload": "approved"}
    )
    assert duplicate.kind == "duplicate" and duplicate.run_result is None

    late = await engine.deliver_wait_event(
        wait_id, {"kind": "timeout", "event_id": "evt-late", "payload": None}
    )
    assert late.kind == "terminal" and late.run_result is None
    assert late.wait_status == "completed" and late.resolution_kind == "signal"


async def test_signal_vs_timeout_race_yields_exactly_one_execution():
    """W3.1 (real race): a start barrier releases signal and timeout deliveries
    concurrently — exactly ONE executes; the loser gets an honest typed report."""

    import asyncio

    engine, coordinator = _delivery_engine()
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    barrier = asyncio.Barrier(2)

    async def deliver(event):
        await barrier.wait()
        return await engine.deliver_wait_event(wait_id, event)

    signal, timeout = await asyncio.gather(
        deliver({"kind": "signal", "event_id": "evt-s", "payload": "yes"}),
        deliver({"kind": "timeout", "event_id": "evt-t", "payload": None}),
    )

    kinds = sorted([signal.kind, timeout.kind])
    assert kinds.count("executed") == 1, f"exactly one winner required: {kinds}"
    loser = signal if timeout.kind == "executed" else timeout
    assert loser.kind in ("already_processing", "terminal"), loser.kind
    stored = await coordinator.get(wait_id)
    assert stored.status == "completed"
    winner = signal if signal.kind == "executed" else timeout
    assert stored.resolution_kind == winner.resolution_kind


async def test_lease_expiry_allows_reclaim_and_attempts_are_bounded():
    """W3.3: a crashed claimant's lease expires → the SAME event can reclaim and finish;
    attempts are bounded by max_resume_attempts — exhaustion fails the wait."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.waits import WaitEvent

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock)
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id
    runtime = engine.executor.wait_runtime

    # claim then simulate a crash (no complete); lease still live -> different event loses
    event = WaitEvent(kind="signal", event_id="evt-crash", payload="yes")
    claimed = await coordinator.claim_event(
        wait_id, event, lease_until=clock() + timedelta(seconds=300)
    )
    assert claimed.kind == "claimed"
    blocked = await runtime.deliver(wait_id, event, current_digest="whatever")
    assert blocked.kind == "duplicate", "same event under a LIVE lease is a duplicate report"

    # lease expires -> the same event reclaims and the delivery finishes the machine
    current["now"] += timedelta(seconds=301)
    outcome = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-crash", "payload": "yes"}
    )
    assert outcome.kind == "executed"
    stored = await coordinator.get(wait_id)
    assert stored.status == "completed"
    assert stored.resume_attempts == 2, "each claim counts toward bounded recovery"


async def test_attempt_exhaustion_fails_the_wait():
    """W3.3: max_resume_attempts crossings end the wait failed — no infinite reclaim."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.waits import WaitEvent

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock)
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    event = WaitEvent(kind="signal", event_id="evt-x", payload="y")
    for _ in range(3):  # default max_resume_attempts=3: claim, crash, lease-expire
        claimed = await coordinator.claim_event(
            wait_id, event, lease_until=clock() + timedelta(seconds=1)
        )
        assert claimed.kind == "claimed"
        current["now"] += timedelta(seconds=2)

    exhausted = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-x", "payload": "y"}
    )
    assert exhausted.kind == "attempts_exhausted"
    assert (await coordinator.get(wait_id)).status == "failed"


async def test_changed_machine_is_rejected_at_delivery():
    """W3.2: the stored definition digest must match the CURRENTLY registered definition —
    a changed graph fails the wait instead of replaying old state through new structure."""

    shared: dict = {}
    engine, _coordinator = _delivery_engine(shared)
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    changed, coordinator2 = _delivery_engine(shared)
    changed.register_workflow(
        WorkflowBuilder("durable_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("finish")
        .step("extra_step", capability="finish")
        .step("escalate")
        .build()
    )

    outcome = await changed.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-1", "payload": "yes"}
    )
    assert outcome.kind == "rejected"
    assert "digest" in outcome.detail
    assert (await coordinator2.get(wait_id)).status == "failed"


async def test_durable_handle_cannot_be_used_as_a_raw_snapshot():
    """W3.2 direct-bypass probe: no API accepts the handle as a snapshot."""

    engine, _coordinator = _delivery_engine()
    first = await engine.run("durable_flow", {})

    with pytest.raises(Exception):
        await engine.resume(first.wait_handle, "yes")  # typed handle is not a snapshot


async def test_stale_claimant_cannot_terminalize_after_reclaim():
    """W3.1 CAS: a claimant whose lease expired and whose wait was RECLAIMED holds a stale
    token — its complete/fail must raise, never overwrite the new claimant's state."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.waits import WaitEvent

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock)
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    event = WaitEvent(kind="signal", event_id="evt-s", payload="y")
    stale = await coordinator.claim_event(wait_id, event, lease_until=clock() + timedelta(seconds=1))
    assert stale.kind == "claimed"
    current["now"] += timedelta(seconds=2)  # stale claimant's lease expires
    fresh = await coordinator.claim_event(wait_id, event, lease_until=clock() + timedelta(seconds=300))
    assert fresh.kind == "claimed" and fresh.claim.wait_version > stale.claim.wait_version

    with pytest.raises(ValueError, match="stale claimant"):
        await coordinator.complete(wait_id, stale.claim, resolution_kind="signal")
    completed = await coordinator.complete(wait_id, fresh.claim, resolution_kind="signal")
    assert completed.status == "completed"


async def test_different_event_cannot_steal_a_live_lease():
    """W3.1: while a claim's lease is LIVE, a different event id gets already_processing —
    it can never steal the claim or double-execute."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.waits import WaitEvent

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock)
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    held = await coordinator.claim_event(
        wait_id, WaitEvent(kind="signal", event_id="evt-a", payload="y"),
        lease_until=clock() + timedelta(seconds=300),
    )
    assert held.kind == "claimed"
    thief = await coordinator.claim_event(
        wait_id, WaitEvent(kind="timeout", event_id="evt-b", payload=None),
        lease_until=clock() + timedelta(seconds=300),
    )
    assert thief.kind == "already_processing" and thief.claim is None
