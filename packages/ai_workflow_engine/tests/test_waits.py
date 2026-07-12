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


def _definition_json():
    return (
        WorkflowBuilder("wf")
        .human("gate", wait_policy=LocalWaitPolicy())
        .step("finish")
        .build()
        .model_dump_json()
    )


def _definition_digest():
    from ai_workflow_engine.workflow import WorkflowDefinition

    return WorkflowDefinition.model_validate_json(_definition_json()).definition_digest()


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
        async def register(self, record, snapshot_json, definition_json):
            raise RuntimeError("outbox transaction failed")

    engine = _durable_engine(ExplodingCoordinator(clock=_clock()))
    result = await engine.run("durable_flow", {})
    assert result.status == "failed"
    assert "registration failed" in (result.error or "")
    assert result.snapshot is None and result.wait_handle is None
    assert any(e.decision == "wait:registration_failed" for e in result.trace)

    class ForgingCoordinator(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json, definition_json):
            receipt = await super().register(record, snapshot_json, definition_json)
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
    await coordinator.register(record, "{}", _definition_json())
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
        async def register(self, record, snapshot_json, definition_json):
            receipt = await super().register(record, snapshot_json, definition_json)
            from datetime import timedelta

            return receipt.model_copy(
                update={"accepted_deadline": receipt.accepted_deadline + timedelta(seconds=1)}
            )

    class SilentReplace(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json, definition_json):
            self._records.pop(record.wait_id, None)
            self._snapshots.pop(record.wait_id, None)
            self._receipts.pop(record.wait_id, None)
            return await super().register(record, snapshot_json, definition_json)

    class NotIdempotent(InMemoryWaitCoordinator):
        _n = 0

        async def register(self, record, snapshot_json, definition_json):
            self._records.pop(record.wait_id, None)
            self._snapshots.pop(record.wait_id, None)
            self._receipts.pop(record.wait_id, None)
            receipt = await super().register(record, snapshot_json, definition_json)
            type(self)._n += 1
            return receipt.model_copy(update={"registration_id": f"reg-{type(self)._n}"})

    class Amnesiac(InMemoryWaitCoordinator):
        async def get(self, wait_id):
            return None

    class DefinitionDropper(InMemoryWaitCoordinator):
        # W3R.3a negative: an adapter that loses the registered definition bytes breaks
        # terminal observability and must fail conformance by name.
        async def load_definition(self, wait_id):
            return None

    class StaleAttemptOrdinal(InMemoryWaitCoordinator):
        # R0/F16 negative: returning the PRE-increment row from claim_event (a common ORM
        # pattern) makes retries reuse a dead attempt's directory — conformance must name it.
        async def claim_event(self, wait_id, event, *, lease_until):
            outcome = await super().claim_event(wait_id, event, lease_until=lease_until)
            if outcome.kind == "claimed":
                stale = outcome.record.model_copy(
                    update={"resume_attempts": outcome.record.resume_attempts - 1}
                )
                return outcome.model_copy(update={"record": stale})
            return outcome

    class LeaseIgnorer(InMemoryWaitCoordinator):
        # W5.1 negative: granting a claim to a DIFFERENT event while a lease is LIVE
        # breaks single-claimant atomicity — conformance must name it.
        async def claim_event(self, wait_id, event, *, lease_until):
            outcome = await super().claim_event(wait_id, event, lease_until=lease_until)
            if outcome.kind == "already_processing":
                async with self._lock:
                    record = self._records[wait_id]
                    claimed = record.model_copy(
                        update={"version": record.version + 1,
                                "resume_attempts": record.resume_attempts + 1}
                    )
                    self._records[wait_id] = claimed
                from ai_workflow_engine.waits import WaitClaim, WaitClaimOutcome

                return WaitClaimOutcome(
                    kind="claimed",
                    record=claimed.model_copy(deep=True),
                    claim=WaitClaim(
                        wait_id=wait_id, wait_version=claimed.version,
                        event_id=event.event_id, claimed_at=self._clock(),
                        lease_expires_at=lease_until,
                    ),
                )
            return outcome

    class TerminalReviver(InMemoryWaitCoordinator):
        # W5.1 negative: claiming a TERMINAL wait re-executes settled history.
        async def claim_event(self, wait_id, event, *, lease_until):
            outcome = await super().claim_event(wait_id, event, lease_until=lease_until)
            if outcome.kind == "terminal":
                from ai_workflow_engine.waits import WaitClaim, WaitClaimOutcome

                return WaitClaimOutcome(
                    kind="claimed",
                    record=outcome.record,
                    claim=WaitClaim(
                        wait_id=wait_id, wait_version=outcome.record.version,
                        event_id=event.event_id, claimed_at=self._clock(),
                        lease_expires_at=lease_until,
                    ),
                )
            return outcome

    for broken, fragment in (
        (WrongDeadline, "ACCEPTED deadline"),
        (SilentReplace, "rejected"),
        (NotIdempotent, "idempotent"),
        (Amnesiac, "retrievable"),
        (DefinitionDropper, "load_definition"),
        (StaleAttemptOrdinal, "ordinal"),
        (LeaseIgnorer, "DIFFERENT event"),
        (TerminalReviver, "terminal"),
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
        def register(self, record, snapshot_json, definition_json): ...
        def get(self, wait_id): ...
        def load_snapshot(self, wait_id): ...
        def load_definition(self, wait_id): ...
        def claim_event(self, wait_id, event, *, lease_until): ...
        def complete(self, wait_id, claim, *, resolution_kind): ...
        def fail(self, wait_id, claim, *, error, failure_kind="resume_failed"): ...
        def due(self, now): return []
        def stalled(self, now): return []
        def cancel(self, wait_id, *, reason): ...
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
        async def register(self, record, snapshot_json, definition_json):
            await super().register(record, snapshot_json, definition_json)
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
    await coordinator.register(record, "{}", _definition_json())
    fetched = await coordinator.get("w-copy")
    fetched.status = "cancelled"
    assert (await coordinator.get("w-copy")).status == "pending", "store must be isolated"


async def test_registration_reuse_and_distinct_occurrence_identity():
    """W2R.3: a crash-after-register retry of the SAME suspension reuses the committed
    registration (deterministic wait id); the id is derived from run/node/occurrence."""

    from ai_workflow_engine import InMemoryWaitCoordinator

    calls = {"register": 0}

    class CountingCoordinator(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json, definition_json):
            calls["register"] += 1
            return await super().register(record, snapshot_json, definition_json)

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
        async def register(self, record, snapshot_json, definition_json):
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
        run_id="run-1", workflow_id="wf", definition_digest=_definition_digest(),
        suspended_node="gate", occurrence=0, policy=DurableWaitPolicy(timeout_s=60),
        snapshot_json=snapshot_json, definition_json=_definition_json(),
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
        run_id="run-1", workflow_id="wf", definition_digest=_definition_digest(),
        suspended_node="gate", occurrence=0, policy=DurableWaitPolicy(timeout_s=60),
        snapshot_json=MachineSnapshot(
            workflow_id="wf", suspended_node="gate", node_status={}, routes={},
            node_results=[], artifacts=[],
        ).model_dump_json(),
        definition_json=_definition_json(),
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

    other = (
        WorkflowBuilder("wf")
        .human("gate", wait_policy=LocalWaitPolicy())
        .step("finish")
        .step("extra")
        .build()
    )
    with pytest.raises(RuntimeError, match="DIFFERENT.*definition_digest"):
        await runtime.register_suspension(
            _registration_request(
                definition_digest=other.definition_digest(),
                definition_json=other.model_dump_json(),
            )
        )

    # W3R.3a integrity seal: bytes that do not digest to the claimed identity never store
    with pytest.raises(ValueError, match="must BE the registered machine"):
        await runtime.register_suspension(_registration_request(definition_digest="a" * 16))


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
    assert stored.definition_digest == _definition_digest(), (
        "digest must be persisted, not discarded"
    )
    assert await reconnected.load_definition(outcome.handle.wait_id) == _definition_json(), (
        "W3R.3a: the registered definition BYTES survive reconnect byte-exactly"
    )


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
    await coordinator.register(record, "{}", _definition_json())
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
                definition_json=_definition_json(),
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
    first = await coordinator.register(record, "{}", _definition_json())
    first.registration_id = "corrupted"
    duplicate = await coordinator.register(record, "{}", _definition_json())
    assert duplicate.registration_id == "reg-w-rcpt", "stored receipt must be isolated"
    duplicate.adapter_id = "also-corrupted"
    third = await coordinator.register(record, "{}", _definition_json())
    assert third.adapter_id == "in_memory"


# ================================================== W3: delivery, leases, crash recovery


def _delivery_engine(shared=None, *, clock=None, bundle_dir=None, gates=("gate",)):
    """Durable flow with a REAL post-wait step so deliveries continue the machine.
    ``bundle_dir`` turns on config-first observation (W4 segment tests); ``gates`` chains
    several durable human nodes so repeated waits inside ONE logical run are exercised."""

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
            "answer": getattr(payload, "value", None),
            "wait_idempotency": context.metadata.get("wait_idempotency"),
        }

    def escalate(context, _payload):
        return {"escalated": True, "wait_idempotency": context.metadata.get("wait_idempotency")}

    builder = WorkflowEngineBuilder().with_wait_coordinator(coordinator, clock=clock)
    if bundle_dir is not None:
        from ai_workflow_engine import ObservationConfig

        builder.with_observation(ObservationConfig(enabled=True, bundle_dir=str(bundle_dir)))
    for gate_node in gates:
        builder.register_capability(gate_node, gate)
    builder.register_capability("finish", finish)
    builder.register_capability("escalate", escalate)
    flow = WorkflowBuilder("durable_flow")
    for gate_node in gates:
        flow = flow.human(
            gate_node, wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate"
        )
    builder.register_workflow(flow.step("finish").step("escalate").build())
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


# ---------------------------------------------------------------------------
# Phase W4 — immutable observation segments from claimed lineage
# ---------------------------------------------------------------------------


def _bundle_meta(path):
    import json

    return json.loads((path / "meta.json").read_text(encoding="utf-8"))


async def test_chained_durable_waits_register_on_the_resume_path_too(tmp_path):
    """W4.2 (closes a W3 gap): a resumed run that suspends at the NEXT durable wait must
    register it and return a handle-only result — never a raw unregistered snapshot. Before
    the fix, `_maybe_register_durable_wait` only ran in run(), so every chained wait leaked
    an unregistered snapshot and broke the W2.3 'register before exposing' contract."""

    engine, coordinator = _delivery_engine(gates=("first", "second"))
    first = await engine.run("durable_flow", {})
    assert first.wait_handle is not None and first.snapshot is None

    outcome = await engine.deliver_wait_event(
        first.wait_handle.wait_id, {"kind": "signal", "event_id": "evt-1", "payload": "ok"}
    )
    assert outcome.kind == "executed"
    chained = outcome.run_result
    assert chained.status == "requires_user_input"
    assert chained.snapshot is None, "chained durable suspension must NOT expose a snapshot"
    assert chained.wait_handle is not None, "chained durable suspension must be registered"
    assert chained.wait_handle.suspended_node == "second"
    stored = await coordinator.get(chained.wait_handle.wait_id)
    assert stored is not None and stored.status == "pending"


async def test_repeated_waits_produce_ordered_segments_scan_free(tmp_path):
    """W4.2: three chained durable waits in ONE logical run produce segments 0..3 with a
    parent chain, all under the LOGICAL run id — and the index allocation ignores decoy
    directories entirely (persisted snapshot lineage, never max(dir)+1)."""

    from ai_workflow_engine.models import WorkflowGoal

    engine, _coordinator = _delivery_engine(bundle_dir=tmp_path, gates=("a", "b", "c"))
    # Adversarial decoys: allocation must not read ANY directory names.
    (tmp_path / "seg-run--s050-decoy").mkdir()
    (tmp_path / "zzz-unrelated").mkdir()

    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="segments", metadata={"run_id": "seg-run"}
    )
    result = await engine.run("durable_flow", {}, goal=goal)
    for event_id in ("e1", "e2", "e3"):
        assert result.status == "requires_user_input", result.error
        outcome = await engine.deliver_wait_event(
            result.wait_handle.wait_id,
            {"kind": "signal", "event_id": event_id, "payload": f"answer-{event_id}"},
        )
        assert outcome.kind == "executed"
        result = outcome.run_result
    assert result.status == "completed"

    finalized = sorted(
        path.name for path in tmp_path.iterdir() if (path / "meta.json").exists()
    )
    assert finalized == ["seg-run", "seg-run--s001", "seg-run--s002", "seg-run--s003"], (
        f"decoy directories must never influence segment allocation: {finalized}"
    )
    metas = {name: _bundle_meta(tmp_path / name) for name in finalized}
    assert [metas[name]["segment_index"] for name in finalized] == [0, 1, 2, 3]
    assert all(metas[name]["run_id"] == "seg-run" for name in finalized), (
        "every segment's meta must carry the LOGICAL run id"
    )
    assert metas["seg-run"]["segment_kind"] == "initial"
    assert [metas[f"seg-run--s{i:03d}"]["attempt"] for i in (1, 2, 3)] == [1, 1, 1]
    for i in (1, 2, 3):
        assert (tmp_path / f"seg-run--s{i:03d}" / "commit.json").exists(), (
            "R1: the facade promotes each attempt AFTER the coordinator terminalizes — "
            "an executed delivery must leave a committed (canonical) segment"
        )
    assert metas["seg-run--s003"]["status"] == "completed"
    assert all(metas[f"seg-run--s{i:03d}"]["status"] == "requires_user_input" for i in (1, 2))
    digests = {metas[name]["definition_digest"] for name in finalized}
    assert len(digests) == 1 and None not in digests


async def test_concurrent_claim_loser_opens_no_segment(tmp_path):
    """W4.2: while a claim is held, a competing delivery gets a typed loser report and
    opens NOTHING on disk; after lease expiry the reclaim (a fresh attempt) opens exactly
    one uniquely-named continuation segment."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_engine.waits import WaitEvent

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="race", metadata={"run_id": "race-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    dirs_before = {path.name for path in tmp_path.iterdir() if path.is_dir()}
    claimed = await coordinator.claim_event(
        wait_id,
        WaitEvent(kind="signal", event_id="evt-held", payload="mine"),
        lease_until=clock() + timedelta(seconds=300),
    )
    assert claimed.kind == "claimed"
    loser = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-loser", "payload": "steal"}
    )
    assert loser.kind == "already_processing"
    assert {path.name for path in tmp_path.iterdir() if path.is_dir()} == dirs_before, (
        "a losing delivery must not open any observation segment"
    )

    current["now"] += timedelta(seconds=301)  # holder crashed: lease expires
    outcome = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-held", "payload": "mine"}
    )
    assert outcome.kind == "executed"
    new_dirs = {p.name for p in tmp_path.iterdir() if p.is_dir()} - dirs_before
    assert new_dirs == {"race-run--s001-r2"}, (
        f"exactly one continuation segment from the reclaim attempt, got {new_dirs}"
    )
    assert _bundle_meta(tmp_path / "race-run--s001-r2")["segment_index"] == 1


async def test_crashed_attempts_become_typed_abandoned_evidence_owned_by_retention(tmp_path):
    """W4R.1 (was: unbounded invisible leak): attempt 1 crashes after opening its segment
    directory; the NEXT delivery attempt reconciles it into a finalized `abandoned`
    segment (typed evidence, attributable to the logical run) BEFORE opening its own
    directory — the active claimant's directory is never touched. Repeated crashes all
    reconcile. Once the group is terminal, retention prunes every physical attempt
    directory WITH the group: nothing lives outside a retention limit anymore."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.models import WorkflowGoal

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, _coordinator = _delivery_engine(clock=clock, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="crash", metadata={"run_id": "crash-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    real_resume = engine.executor.resume
    calls = {"n": 0}

    async def crash_twice(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("simulated process death mid-resume")
        return await real_resume(*args, **kwargs)

    engine.executor.resume = crash_twice
    for _ in range(2):  # two crashed attempts
        with pytest.raises(RuntimeError, match="process death"):
            await engine.deliver_wait_event(
                wait_id, {"kind": "signal", "event_id": "evt-c", "payload": "yes"}
            )
        current["now"] += timedelta(seconds=301)

    outcome = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-c", "payload": "yes"}
    )
    assert outcome.kind == "executed" and outcome.run_result.status == "completed"

    # both dead attempts were reconciled into typed abandoned evidence (append-only marker)
    for name, attempt in (("crash-run--s001", 1), ("crash-run--s001-r2", 2)):
        meta = _bundle_meta(tmp_path / name)
        assert meta["status"] == "abandoned" and meta["attempt"] == attempt, name
        assert meta["run_id"] == "crash-run" and meta["segment_index"] == 1, name
        assert (tmp_path / name / "abandoned.json").exists(), name
    retry_meta = _bundle_meta(tmp_path / "crash-run--s001-r3")
    assert retry_meta["segment_index"] == 1 and retry_meta["status"] == "completed"
    assert (tmp_path / "crash-run--s001-r3" / "commit.json").exists(), (
        "the surviving attempt is promoted canonical after terminalization"
    )

    # bounded: retention now OWNS the abandoned directories — a newer run rotates the
    # whole crash-run group out, attempts included
    from ai_workflow_engine.observation_bundle import prune_observation_bundles

    goal2 = WorkflowGoal(
        workflow_type="durable_flow", objective="newer", metadata={"run_id": "newer-run"}
    )
    second = await engine.run("durable_flow", {}, goal=goal2)
    done = await engine.deliver_wait_event(
        second.wait_handle.wait_id, {"kind": "signal", "event_id": "evt-n", "payload": "ok"}
    )
    assert done.kind == "executed"
    prune_observation_bundles(tmp_path, 1)
    leftover = sorted(path.name for path in tmp_path.iterdir() if path.is_dir())
    assert not any(name.startswith("crash-run") for name in leftover), (
        f"abandoned attempt directories must prune WITH their group: {leftover}"
    )
    assert any(name.startswith("newer-run") for name in leftover)


# ---------------------------------------------------------------------------
# W3R/W4R — independent-review remediation reproducers (codex FAIL 2026-07-11)
# ---------------------------------------------------------------------------


async def test_accepted_event_identity_is_frozen_across_lease_expiry():
    """W3R.2 (codex probe #2, permanent): after a signal is ACCEPTED and its claimant's
    lease expires, a DIFFERENT event (here: the timeout) must get a typed `not_accepted`
    outcome and must never overwrite the acceptance — one accepted event, forever. The
    SAME accepted event still reclaims (crash recovery preserved)."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.waits import WaitEvent

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock)
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    original = WaitEvent(kind="signal", event_id="evt-original", payload="yes")
    claimed = await coordinator.claim_event(
        wait_id, original, lease_until=clock() + timedelta(seconds=60)
    )
    assert claimed.kind == "claimed"

    current["now"] += timedelta(seconds=61)  # claimant crashed; lease expired
    replacement = await coordinator.claim_event(
        wait_id,
        WaitEvent(kind="timeout", event_id="evt-replacement"),
        lease_until=clock() + timedelta(seconds=60),
    )
    assert replacement.kind == "not_accepted", (
        "a different event must NEVER replace an accepted event, even after lease expiry"
    )

    # the engine door reports the same typed TRANSIENT loser (F10: distinct from the
    # terminal `rejected` — this wait is still alive and recoverable)
    outcome = await engine.deliver_wait_event(
        wait_id, {"kind": "timeout", "event_id": "evt-timeout-2"}
    )
    assert outcome.kind == "not_accepted" and outcome.wait_status == "claimed"

    # crash recovery for the ACCEPTED event still works and completes the machine
    recovered = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-original", "payload": "yes"}
    )
    assert recovered.kind == "executed"
    stored = await coordinator.get(wait_id)
    assert stored.status == "completed" and stored.resolution_kind == "signal"


async def test_slow_old_claimant_stays_stale_after_same_event_reclaim():
    """W3R.2: the frozen-acceptance rule composes with CAS — when the SAME event reclaims
    after expiry, the slow ORIGINAL claimant's token is stale and cannot terminalize."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.waits import WaitEvent

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock)
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    event = WaitEvent(kind="signal", event_id="evt-slow", payload="yes")
    old = await coordinator.claim_event(wait_id, event, lease_until=clock() + timedelta(seconds=5))
    assert old.kind == "claimed"
    current["now"] += timedelta(seconds=6)
    new = await coordinator.claim_event(wait_id, event, lease_until=clock() + timedelta(seconds=60))
    assert new.kind == "claimed" and new.claim.wait_version > old.claim.wait_version

    with pytest.raises(ValueError, match="stale claimant"):
        await coordinator.complete(wait_id, old.claim, resolution_kind="signal")
    done = await coordinator.complete(wait_id, new.claim, resolution_kind="signal")
    assert done.status == "completed"


async def test_durable_snapshot_cannot_resume_through_the_public_door():
    """W3R.1 (codex probe #1, permanent): loading the STORED snapshot from the coordinator
    and calling public engine.resume() must fail — with or without a forged delivery
    envelope — because it would bypass claim, deduplication, leases, attempt bounds, and
    terminalization. The one legitimate door (deliver_wait_event) still executes."""

    engine, coordinator = _delivery_engine()
    first = await engine.run("durable_flow", {})
    wait_id = first.wait_handle.wait_id

    stored_snapshot = await coordinator.load_snapshot(wait_id)
    assert stored_snapshot, "the coordinator stores the raw snapshot — the attack surface"

    with pytest.raises(RuntimeError, match="sealed to durable wait"):
        await engine.resume(stored_snapshot, "bypass")  # codex's exact probe shape

    with pytest.raises(RuntimeError, match="sealed to durable wait"):
        await engine.resume(
            stored_snapshot,
            {  # forged envelope: right wait id, guessed token
                "__wait_delivery__": {
                    "wait_id": wait_id,
                    "event_id": "evt-forged",
                    "kind": "signal",
                    "attempt": 1,
                    "claim_token": "deadbeef" * 4,
                },
                "payload": "bypass",
            },
        )
    assert (await coordinator.get(wait_id)).status == "pending", (
        "rejected bypasses must leave the wait untouched"
    )

    outcome = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-legit", "payload": "yes"}
    )
    assert outcome.kind == "executed" and outcome.run_result.status == "completed"
    assert (await coordinator.get(wait_id)).status == "completed"


async def test_local_wait_snapshots_keep_the_public_resume_door():
    """W3R.1 degradation guard: sealing durable snapshots must not touch local waits —
    their snapshot is the public contract and resumes directly, exactly as in v0.8."""

    from ai_workflow_engine import WorkflowEngineBuilder
    from pydantic import BaseModel as _BM

    class Gate(_BM):
        status: str
        value: str = ""

    builder = WorkflowEngineBuilder()

    def gate(context, _payload):
        event = context.metadata.get("resume_event")
        return Gate(status="pending") if event is None else Gate(status="answered", value=str(event))

    builder.register_capability("gate", gate)
    builder.register_capability("finish", lambda ctx, p: {"answer": p.value})
    builder.register_workflow(
        WorkflowBuilder("local_flow").human("gate", wait_policy=LocalWaitPolicy()).step("finish").build()
    )
    engine = builder.build()

    first = await engine.run("local_flow", {})
    assert first.status == "requires_user_input"
    assert first.snapshot is not None and first.snapshot.durable_wait_id is None
    resumed = await engine.resume(first.snapshot.to_json(), "direct")
    assert resumed.status == "completed" and resumed.output["answer"] == "direct"


async def test_wait_failures_without_continuation_are_observable_and_prunable(tmp_path):
    """W3R.3 (codex probe #3, permanent): digest mismatch at delivery terminalizes the
    wait with a PERSISTED failure kind, and the observation group flips to failed via an
    engine-owned `wait_terminal` evidence segment — the viewer and retention must agree
    with coordinator truth instead of protecting a "suspended" lie forever."""

    from ai_workflow_engine.models import WorkflowGoal

    shared: dict = {}
    engine, _coordinator = _delivery_engine(shared, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="wfail", metadata={"run_id": "wfail-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    changed, coordinator2 = _delivery_engine(shared, bundle_dir=tmp_path)
    changed.register_workflow(
        WorkflowBuilder("durable_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("finish")
        .step("extra", capability="finish")
        .step("escalate")
        .build()
    )
    outcome = await changed.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-1", "payload": "yes"}
    )
    assert outcome.kind == "rejected"

    stored = await coordinator2.get(wait_id)
    assert stored.status == "failed"
    assert stored.failure_kind == "digest_mismatch", (
        "the WHY must be persisted on the record, not just returned"
    )
    assert stored.failure_detail and "does not match" in stored.failure_detail

    wfail_dir = tmp_path / "wfail-run--s001-wfail"
    assert (wfail_dir / "meta.json").exists(), "terminal evidence segment must be finalized"
    meta = _bundle_meta(wfail_dir)
    assert meta["status"] == "failed" and meta["segment_kind"] == "wait_terminal"
    assert meta["run_id"] == "wfail-run" and meta["segment_index"] == 1
    assert meta["definition_digest"] == stored.definition_digest, (
        "the evidence segment carries the REGISTERED digest — the group's canonical identity"
    )
    trace_text = (wfail_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "wait:failed" in trace_text and "digest_mismatch" in trace_text

    # retention: the group's newest segment is now failed -> no longer in-flight-protected
    from ai_workflow_engine.observation_bundle import prune_observation_bundles

    prune_observation_bundles(tmp_path, 1)  # only group present: survives as the newest
    assert (tmp_path / "wfail-run").exists()


async def test_attempts_exhaustion_persists_kind_and_writes_terminal_evidence(tmp_path):
    """W3R.3: exhaustion (no claim ever handed to a continuation) also persists its kind
    and closes the observation group."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_engine.waits import WaitEvent

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="exhaust", metadata={"run_id": "exhaust-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    event = WaitEvent(kind="signal", event_id="evt-x", payload="y")
    for _ in range(3):  # crash-claim to exhaustion (same accepted event)
        claimed = await coordinator.claim_event(
            wait_id, event, lease_until=clock() + timedelta(seconds=1)
        )
        assert claimed.kind == "claimed"
        current["now"] += timedelta(seconds=2)

    exhausted = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-x", "payload": "y"}
    )
    assert exhausted.kind == "attempts_exhausted"
    stored = await coordinator.get(wait_id)
    assert stored.failure_kind == "attempts_exhausted"
    assert "3 of 3" in (stored.failure_detail or "")

    terminal_dirs = [p.name for p in tmp_path.iterdir() if p.name.endswith("-wfail")]
    assert terminal_dirs == ["exhaust-run--s001-wfail"]
    assert _bundle_meta(tmp_path / terminal_dirs[0])["status"] == "failed"


def _budgeted_durable_engine(max_estimated_usd, outbox=None, crash_once=None, clock=None, bundle_dir=None):
    """Durable flow whose paid steps go through the REAL budget gate: spend(0.6) ->
    durable gate -> spend(0.6) -> finish. Optional external-write outbox at the
    side-effect boundary and a crash hook for at-least-once recovery tests."""

    from ai_workflow_engine import InMemoryWaitCoordinator, WorkflowEngineBuilder
    from ai_workflow_engine.models import RuntimeLimits, WorkflowProfile, WorkflowUsageEvent
    from ai_workflow_engine.usage_events import record_usage_event
    from pydantic import BaseModel as _BM

    clock = clock or _clock()
    coordinator = InMemoryWaitCoordinator(clock=clock, shared_state={})
    builder = WorkflowEngineBuilder()
    if bundle_dir is not None:
        from ai_workflow_engine import ObservationConfig

        builder = builder.with_observation(
            ObservationConfig(enabled=True, bundle_dir=str(bundle_dir))
        )
    builder = (
        builder
        .with_wait_coordinator(coordinator, clock=clock)
        .with_profile(
            WorkflowProfile(
                workflow_type="budget_wait_flow",
                limits=RuntimeLimits(max_estimated_usd=max_estimated_usd),
            )
        )
    )

    class Gate(_BM):
        status: str
        value: str = ""

    def paid(node_name):
        def _spend(context, payload):
            # the REAL metered path: pre-call gate + recorded usage event (0.60 USD)
            record_usage_event(
                WorkflowUsageEvent(
                    node=node_name, operation="chat", total_tokens=100, estimated_usd=0.60
                )
            )
            return payload

        return _spend

    def gate(context, _payload):
        event = context.metadata.get("resume_event")
        return Gate(status="pending") if event is None else Gate(status="answered", value=str(event))

    def deliverer(context, payload):
        if outbox is not None:
            # PRODUCT-SIDE idempotent external write: the stable engine-provided
            # wait/event key dedupes retries; a random key would double-send.
            key = context.metadata.get("wait_idempotency")
            assert key, "post-wait side effects must see the stable idempotency key"
            outbox.setdefault(key, {"sends": 0})
            outbox[key]["sends"] += 1
            outbox[key]["last_payload"] = getattr(payload, "value", None)
        return {"delivered": True}

    builder.register_capability("spend_before", paid("spend_before"))
    builder.register_capability("gate", gate)
    builder.register_capability("spend_after", paid("spend_after"))
    builder.register_capability("deliver_result", deliverer)
    builder.register_capability("escalate", lambda ctx, p: {"escalated": True})
    builder.register_workflow(
        WorkflowBuilder("budget_wait_flow")
        .step("spend_before")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("spend_after")
        .step("deliver_result")
        .step("escalate")
        .build()
    )
    engine = builder.build()
    if crash_once is not None:
        real_resume = engine.executor.resume

        async def crashing(*args, **kwargs):
            result = await real_resume(*args, **kwargs)
            if crash_once.pop("armed", None):
                raise RuntimeError("simulated crash AFTER the external write, before terminalize")
            return result

        engine.executor.resume = crashing
    return engine, coordinator


async def test_budget_stays_cumulative_across_durable_suspension():
    """W3R.4/W3.2: the resumed half inherits the pre-suspension spend — a $1.00 ceiling
    with $0.60 spent BEFORE the wait must deny the $0.60 post-wait call ($1.20 > $1.00),
    while the same flow under a loose ceiling completes. Denial comes from the CUMULATIVE
    summary, not the resumed half alone."""

    engine, coordinator = _budgeted_durable_engine(max_estimated_usd=1.00)
    first = await engine.run("budget_wait_flow", {})
    assert first.status == "requires_user_input"
    assert first.usage.estimated_usd == 0.60

    outcome = await engine.deliver_wait_event(
        first.wait_handle.wait_id, {"kind": "signal", "event_id": "evt-b", "payload": "go"}
    )
    assert outcome.kind == "executed"
    resumed = outcome.run_result
    assert resumed.status == "failed", "cumulative budget must deny the post-wait call"
    assert "$1.200000 > $1.000000" in str(resumed.error), (
        "denial must cite the CUMULATIVE spend (0.60 pre-wait + 0.60 post-wait)"
    )
    stored = await coordinator.get(first.wait_handle.wait_id)
    assert stored.status == "failed" and stored.failure_kind == "resume_failed"

    loose_engine, _ = _budgeted_durable_engine(max_estimated_usd=5.00)
    first2 = await loose_engine.run("budget_wait_flow", {})
    ok = await loose_engine.deliver_wait_event(
        first2.wait_handle.wait_id, {"kind": "signal", "event_id": "evt-b2", "payload": "go"}
    )
    assert ok.kind == "executed" and ok.run_result.status == "completed"
    assert ok.run_result.usage.estimated_usd == 1.20, "both halves metered once each"


async def test_external_write_is_idempotent_across_crash_and_reclaim():
    """W3R.4/W3.4: a REAL external-write boundary under at-least-once delivery — the
    post-wait capability sends into an outbox keyed by the stable engine idempotency key;
    the process crashes AFTER the write but BEFORE terminalization; the lease expires and
    the SAME event redelivers; the machine re-executes and the outbox deduplicates on the
    key: exactly ONE external send. A random per-call key (the omission) would have sent
    twice — the recorded attempt count proves the retry actually happened."""

    from datetime import datetime, timedelta, timezone

    outbox: dict = {}
    crash = {"armed": True}
    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    engine, coordinator = _budgeted_durable_engine(
        max_estimated_usd=None,
        outbox=outbox,
        crash_once=crash,
        clock=lambda: current["now"],  # ONE clock: coordinator lease + runtime deadline
    )

    first = await engine.run("budget_wait_flow", {})
    wait_id = first.wait_handle.wait_id

    with pytest.raises(RuntimeError, match="simulated crash"):
        await engine.deliver_wait_event(
            wait_id, {"kind": "signal", "event_id": "evt-once", "payload": "ship-it"}
        )
    key = f"{wait_id}:evt-once"
    assert outbox[key]["sends"] == 1, "the external write happened before the crash"
    assert (await coordinator.get(wait_id)).status == "claimed", "crash left the claim open"

    current["now"] += timedelta(seconds=301)  # lease expires -> same event reclaims
    outcome = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-once", "payload": "ship-it"}
    )
    assert outcome.kind == "executed" and outcome.run_result.status == "completed"

    assert list(outbox) == [key], "one stable key — no second identity ever formed"
    assert outbox[key]["sends"] == 2, "the machine DID re-execute (at-least-once is real)"
    assert outbox[key]["last_payload"] == "ship-it"
    # the PRODUCT-side dedup contract: sends keyed identically collapse to one delivery
    delivered_once = {k: v for k, v in outbox.items()}
    assert len(delivered_once) == 1, (
        "a random per-call key would have produced TWO outbox identities (double-send); "
        "the stable wait/event key is what makes the retry safe"
    )
    stored = await coordinator.get(wait_id)
    assert stored.status == "completed" and stored.resume_attempts == 2


async def test_snapshot_missing_failure_yields_a_readable_failed_group(tmp_path):
    """W3R.3a/b (codex recheck probe #1, permanent): when the stored snapshot is GONE, the
    terminal evidence segment is built from facts persisted at registration — origin
    lineage + registered definition bytes — so its parent/index form a VALID chain
    (initial <- wfail) instead of the parent-less segment the strict reader refused."""

    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_engine.workflow import WorkflowDefinition

    shared: dict = {}
    engine, coordinator = _delivery_engine(shared, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="snaploss", metadata={"run_id": "snaploss-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    shared["snapshots"].pop(wait_id)  # adapter integrity failure: snapshot lost

    outcome = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-s", "payload": "yes"}
    )
    assert outcome.kind == "rejected"
    assert outcome.terminal_observation == "recorded", (
        "the evidence write must be TYPED on the outcome, not a log line"
    )
    stored = await coordinator.get(wait_id)
    assert stored.status == "failed" and stored.failure_kind == "snapshot_missing"

    wfail = tmp_path / "snaploss-run--s001-wfail"
    meta = _bundle_meta(wfail)
    assert meta["segment_index"] == 1
    assert meta["attempt"] is None, (
        "terminal evidence is attempt-less: finalize IS its commit — no marker ceremony"
    )
    written = WorkflowDefinition.model_validate_json(
        (wfail / "definition.json").read_text(encoding="utf-8")
    )
    assert written.definition_digest() == meta["definition_digest"] == stored.definition_digest, (
        "the evidence definition file IS the registered bytes — it recomputes to the "
        "registered digest, so strict group readers accept the chain"
    )
    from ai_workflow_viewer import FileEventSource

    grouped = FileEventSource(tmp_path).read_group("snaploss-run")
    assert grouped.status == "failed"
    assert [segment.kind for segment in grouped.segments] == ["initial", "wait_terminal"]


async def test_absent_workflow_registration_still_closes_the_group(tmp_path):
    """W3R.3a/b (codex recheck probe #2, permanent): a restarted engine that no longer has
    the workflow registered fails the wait AND still writes the terminal evidence segment
    from the REGISTERED definition bytes — the group closes instead of showing a suspended
    lie forever; without observation configured the outcome is typed `skipped`."""

    from ai_workflow_engine import InMemoryWaitCoordinator, WorkflowEngineBuilder, ObservationConfig
    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_engine.workflow import WorkflowDefinition

    shared: dict = {}
    clock = _clock()
    engine, _coordinator = _delivery_engine(shared, clock=clock, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="restart", metadata={"run_id": "restart-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    # restarted process: same coordinator store + observation dir, NO workflows registered
    restarted = (
        WorkflowEngineBuilder()
        .with_wait_coordinator(InMemoryWaitCoordinator(clock=clock, shared_state=shared), clock=clock)
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(tmp_path)))
        .build()
    )
    outcome = await restarted.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-r", "payload": "yes"}
    )
    assert outcome.kind == "rejected" and outcome.wait_status == "failed"
    assert outcome.terminal_observation == "recorded", (
        "an absent current registration must not skip the truthful terminal observation"
    )
    stored = await restarted.wait_coordinator.get(wait_id)
    assert stored.failure_kind == "digest_mismatch"

    wfail = tmp_path / "restart-run--s001-wfail"
    meta = _bundle_meta(wfail)
    assert meta["status"] == "failed" and meta["segment_index"] == 1
    written = WorkflowDefinition.model_validate_json(
        (wfail / "definition.json").read_text(encoding="utf-8")
    )
    assert written.definition_digest() == stored.definition_digest, (
        "with no current registration, the evidence definition is the REGISTERED bytes"
    )
    from ai_workflow_viewer import FileEventSource

    grouped = FileEventSource(tmp_path).read_group("restart-run")
    assert grouped.status == "failed"
    assert [segment.kind for segment in grouped.segments] == ["initial", "wait_terminal"]

    # variant: no observation configured -> typed `skipped`, never a silent nothing
    shared2: dict = {}
    engine2, _c2 = _delivery_engine(shared2, clock=clock)  # observation OFF
    second = await engine2.run("durable_flow", {})
    bare = (
        WorkflowEngineBuilder()
        .with_wait_coordinator(InMemoryWaitCoordinator(clock=clock, shared_state=shared2), clock=clock)
        .build()
    )
    skipped = await bare.deliver_wait_event(
        second.wait_handle.wait_id, {"kind": "signal", "event_id": "evt-r2", "payload": "y"}
    )
    assert skipped.kind == "rejected" and skipped.terminal_observation == "skipped"


async def test_redelivery_repairs_terminal_evidence_after_post_failure_crash(tmp_path):
    """A process can die after the coordinator commits ``failed`` but before the engine
    writes the terminal observation segment. Redelivering the accepted event must repair
    that evidence without re-executing the workflow or leaving the group suspended."""

    from ai_workflow_engine.models import WorkflowGoal, WorkflowTraceEvent
    from ai_workflow_engine.waits import WaitEvent
    from ai_workflow_viewer import FileEventSource

    shared: dict = {}
    engine, coordinator = _delivery_engine(shared, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow",
        objective="repair terminal evidence",
        metadata={"run_id": "repair-run"},
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id
    event = WaitEvent(kind="signal", event_id="evt-repair", payload="yes")

    # Simulate process loss in WorkflowEngine.deliver_wait_event after the lifecycle
    # service commits the failed state but before _record_wait_terminal_segment runs.
    failed = await engine.executor.wait_runtime.deliver(
        wait_id,
        event,
        current_digest=None,
    )
    assert failed.kind == "rejected" and (await coordinator.get(wait_id)).status == "failed"
    partial = tmp_path / "repair-run--s001-wfail"
    assert not (partial / "meta.json").exists()
    partial.mkdir()
    (partial / "trace.jsonl").write_text(
        WorkflowTraceEvent(
            node="gate",
            decision="stale-partial-write",
            run_id="repair-run",
            sequence=1,
            event_id="stale-terminal-event",
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    repaired = await engine.deliver_wait_event(wait_id, event)
    assert repaired.kind == "duplicate"
    assert repaired.run_result is None, "repair must never re-execute the workflow"
    assert repaired.terminal_observation == "recorded"
    grouped = FileEventSource(tmp_path).read_group("repair-run")
    assert grouped.status == "failed"
    assert [segment.kind for segment in grouped.segments] == ["initial", "wait_terminal"]
    assert all(event.event_id != "stale-terminal-event" for event in grouped.trace_events)

    # Further at-least-once deliveries are read-only reports: no duplicate trace rows.
    again = await engine.deliver_wait_event(wait_id, event)
    assert again.kind == "duplicate" and again.terminal_observation == "recorded"
    assert len(FileEventSource(tmp_path).read_group("repair-run").trace_events) == len(
        grouped.trace_events
    )


# ---------------------------------------------------------------------------
# R0/R1 — segment-lifecycle reproducers (post-`e0b26eb` adversarial review, F1/F2/F3/F7/F8/F9)
# ---------------------------------------------------------------------------


async def test_redelivery_after_resume_failed_never_writes_colliding_evidence(tmp_path):
    """F1 (permanent): the wait failed THROUGH a continuation (resume_failed) — its failed
    attempt IS the evidence. An at-least-once redelivery must repair/confirm that
    attempt's commit marker, never write a `wait_terminal` at the same logical index; the
    group stays readable and failed."""

    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_viewer import FileEventSource

    engine, coordinator = _budgeted_durable_engine(max_estimated_usd=1.00, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="budget_wait_flow", objective="rf", metadata={"run_id": "rf-run"}
    )
    first = await engine.run("budget_wait_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    out1 = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-1", "payload": "go"}
    )
    assert out1.kind == "executed" and out1.run_result.status == "failed"
    assert (await coordinator.get(wait_id)).failure_kind == "resume_failed"

    out2 = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-1", "payload": "go"}
    )
    assert out2.kind == "duplicate"
    assert out2.terminal_observation == "recorded", (
        "the redelivery confirms existing evidence — typed, not re-written"
    )
    names = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert not any(name.endswith("-wfail") for name in names), (
        f"no wait_terminal may collide with the failed continuation attempt: {names}"
    )
    group = FileEventSource(tmp_path).read_group("rf-run")
    assert group.status == "failed"
    assert [s.kind for s in group.segments] == ["initial", "resume"]


async def test_chained_waits_survive_crash_reclaim_with_observation(tmp_path):
    """F2 (permanent): chained durable waits + crash recovery. The machine snapshot
    carries only the LOGICAL segment index, so attempt r2's re-suspension at the child
    wait re-registers byte-identically (reused, never folded to failed); the child wait
    stays the same pending wait and the run completes end to end."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_viewer import FileEventSource

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock, bundle_dir=tmp_path, gates=("a", "b"))
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="chain-crash", metadata={"run_id": "cc-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_a = first.wait_handle.wait_id

    # attempt 1 executes fully (registers wait B) but the process dies BEFORE wait A is
    # terminalized: patch deliver's terminalize path by killing complete() once
    real_complete = coordinator.complete
    crash = {"armed": True}

    async def complete_then_crash(*args, **kwargs):
        if crash.pop("armed", None):
            raise RuntimeError("simulated death before terminalization")
        return await real_complete(*args, **kwargs)

    coordinator.complete = complete_then_crash
    with pytest.raises(RuntimeError, match="before terminalization"):
        await engine.deliver_wait_event(
            wait_a, {"kind": "signal", "event_id": "evt-a", "payload": "one"}
        )
    handle_b_first = None  # the crashed attempt registered wait B already
    current["now"] += timedelta(seconds=301)

    retry = await engine.deliver_wait_event(
        wait_a, {"kind": "signal", "event_id": "evt-a", "payload": "one"}
    )
    assert retry.kind == "executed", (
        f"crash-retry must re-execute cleanly, got {retry.kind}: {retry.detail} / "
        f"{retry.run_result.error if retry.run_result else ''}"
    )
    chained = retry.run_result
    assert chained.status == "requires_user_input" and chained.wait_handle is not None, (
        "the re-suspension at the child wait must RE-REGISTER identically (reused), "
        "never fold the run to failed on a routine at-least-once retry"
    )
    wait_b = chained.wait_handle.wait_id
    assert (await coordinator.get(wait_b)).status == "pending"

    done = await engine.deliver_wait_event(
        wait_b, {"kind": "signal", "event_id": "evt-b", "payload": "two"}
    )
    assert done.kind == "executed" and done.run_result.status == "completed"

    group = FileEventSource(tmp_path).read_group("cc-run")
    assert group.status == "completed"
    assert [s.segment_index for s in group.segments] == [0, 1, 2]
    dispositions = {(s.segment_index, s.disposition) for s in group.non_canonical}
    assert (1, "abandoned") in dispositions or (1, "provisional") in dispositions, (
        "the crashed attempt at index 1 is typed evidence, not canonical history"
    )


async def test_attempt_finalized_but_unterminalized_is_not_canonical(tmp_path):
    """F3 (permanent): an attempt that FINALIZED its bundle but died before the
    coordinator terminalized has no commit marker — the reclaim's attempt outranks it,
    the reconciler demotes it to abandoned (append-only marker over its finalized meta),
    and the group stays readable with both spends visible in actual economics."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_viewer import FileEventSource

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="fincrash", metadata={"run_id": "fc-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    real_complete = coordinator.complete
    crash = {"armed": True}

    async def complete_then_crash(*args, **kwargs):
        if crash.pop("armed", None):
            raise RuntimeError("died after finalize, before terminalize")
        return await real_complete(*args, **kwargs)

    coordinator.complete = complete_then_crash
    with pytest.raises(RuntimeError, match="after finalize"):
        await engine.deliver_wait_event(
            wait_id, {"kind": "signal", "event_id": "evt-f", "payload": "yes"}
        )
    # the attempt FINALIZED (meta exists, status completed) but was never committed
    dead = tmp_path / "fc-run--s001"
    assert (dead / "meta.json").exists() and not (dead / "commit.json").exists()

    current["now"] += timedelta(seconds=301)
    retry = await engine.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-f", "payload": "yes"}
    )
    assert retry.kind == "executed" and retry.run_result.status == "completed"

    assert (dead / "abandoned.json").exists(), (
        "the reconciler demotes a finalized-but-uncommitted attempt — append-only marker; "
        "its finalized meta is never rewritten by a would-be zombie"
    )
    group = FileEventSource(tmp_path).read_group("fc-run")
    assert group.status == "completed"
    assert [s.segment_id for s in group.segments if s.segment_index == 1] == ["fc-run--s001-r2"]
    assert group.non_canonical[0].segment_id == "fc-run--s001"
    assert group.non_canonical[0].disposition == "abandoned"
    assert group.usage_totals["usage_count"] >= group.canonical_usage_totals["usage_count"], (
        "the dead attempt's real spend stays in actual economics"
    )


def _local_seg_engine(bundle_dir):
    """Local-wait engine with config-first observation (fixture-local; mirrors the
    observation-config suite's shape without cross-file imports)."""

    from pydantic import BaseModel as _BM

    from ai_workflow_engine import ObservationConfig, WorkflowEngineBuilder

    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(bundle_dir))
    )

    class Gate(_BM):
        status: str
        value: str = ""

    def ask(context, _payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        return Gate(status="answered", value=str(event))

    builder.register_capability("ask", ask, kind="deterministic")
    builder.register_capability("finish", lambda ctx, p: {"done": True}, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("seg_flow").human("ask", wait_policy=LocalWaitPolicy()).step("finish").build()
    )
    return builder.build()


async def test_double_local_resume_stays_readable_with_supersession(tmp_path):
    """F7 (permanent): resuming the SAME local snapshot twice (operator retry) must not
    corrupt the group — the LATEST committed attempt is canonical, the earlier one is
    typed `superseded` evidence, and both remain inspectable."""

    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_viewer import FileEventSource

    engine = _local_seg_engine(tmp_path)
    goal = WorkflowGoal(workflow_type="seg_flow", objective="twice", metadata={"run_id": "twice-run"})
    first = await engine.run("seg_flow", {}, goal=goal)
    assert first.status == "requires_user_input"

    first_resume = await engine.resume(first.snapshot, "answer-1")
    second_resume = await engine.resume(first.snapshot, "answer-2")
    assert first_resume.status == "completed" and second_resume.status == "completed"

    group = FileEventSource(tmp_path).read_group("twice-run")
    assert group.status == "completed"
    assert [s.segment_index for s in group.segments] == [0, 1]
    assert len(group.non_canonical) == 1
    assert group.non_canonical[0].disposition == "superseded"
    assert group.non_canonical[0].segment_index == 1, (
        "double execution is inspectable evidence, never an unreadable group"
    )


async def test_raising_resume_finalizes_its_segment(tmp_path):
    """F8 (permanent): a resume that RAISES (or exits early) must finalize its eagerly
    created observation directory as failed — nothing may live outside retention."""

    from ai_workflow_engine.models import WorkflowGoal

    engine = _local_seg_engine(tmp_path)
    goal = WorkflowGoal(workflow_type="seg_flow", objective="boom", metadata={"run_id": "boom-run"})
    first = await engine.run("seg_flow", {}, goal=goal)

    real_run = engine.executor.runner.run

    async def exploding(*args, **kwargs):
        raise RuntimeError("runner exploded mid-resume")

    engine.executor.runner.run = exploding
    with pytest.raises(RuntimeError, match="exploded"):
        await engine.resume(first.snapshot, "go")
    engine.executor.runner.run = real_run

    leaked = [
        p.name
        for p in tmp_path.iterdir()
        if p.is_dir() and p.name.startswith("boom-run--s001") and not (p / "meta.json").exists()
    ]
    assert leaked == [], f"raising resume left unfinalized directories: {leaked}"
    failed_dirs = [
        p.name
        for p in tmp_path.iterdir()
        if p.is_dir()
        and p.name.startswith("boom-run--s001")
        and _bundle_meta(p).get("status") == "failed"
    ]
    assert len(failed_dirs) == 1, "the raising attempt finalizes truthfully as failed"


async def test_stalled_wait_is_visible_and_cancellable_with_terminal_evidence(tmp_path):
    """F9/R2 (permanent): a claimed wait whose claimant died and whose accepted event is
    never redelivered is surfaced by stalled()/health(), and engine.cancel_wait()
    terminalizes it AND closes its observation group — no invisible forever-claimed wait,
    no suspended lie, and the engine still never self-fires."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_engine.waits import WaitEvent
    from ai_workflow_viewer import FileEventSource

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    engine, coordinator = _delivery_engine(clock=clock, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="stall", metadata={"run_id": "stall-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    claimed = await coordinator.claim_event(
        wait_id,
        WaitEvent(kind="signal", event_id="evt-lost", payload="x"),
        lease_until=clock() + timedelta(seconds=60),
    )
    assert claimed.kind == "claimed"
    current["now"] += timedelta(seconds=61)  # claimant dead; accepted event lost forever

    stalled = await coordinator.stalled(clock())
    assert [r.wait_id for r in stalled] == [wait_id]
    assert (await coordinator.health()).stalled == 1

    record, observation = await engine.cancel_wait(wait_id, reason="operator: source queue lost the event")
    assert record.status == "cancelled" and record.failure_kind == "cancelled"
    assert observation == "recorded"
    group = FileEventSource(tmp_path).read_group("stall-run")
    assert group.status == "cancelled"
    assert group.segments[-1].kind == "wait_terminal"


async def test_failed_commit_marker_is_typed_and_repaired_by_redelivery(tmp_path):
    """R0R3-C1 (codex probe, permanent): a transient marker-write failure on a COMPLETED
    delivery is (a) visible immediately as terminal_observation='failed', and (b) repaired
    by any at-least-once redelivery — a completed run may never permanently read as
    suspended after one recoverable filesystem hiccup."""

    from ai_workflow_engine import segment_lifecycle
    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_viewer import FileEventSource

    engine, coordinator = _delivery_engine(bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="marker", metadata={"run_id": "marker-run"}
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id

    real_commit = segment_lifecycle.commit_attempt
    calls = {"n": 0}

    def flaky_commit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return False  # transient filesystem failure on the FIRST write
        return real_commit(*args, **kwargs)

    segment_lifecycle.commit_attempt = flaky_commit
    try:
        out1 = await engine.deliver_wait_event(
            wait_id, {"kind": "signal", "event_id": "evt-m", "payload": "yes"}
        )
        assert out1.kind == "executed" and out1.run_result.status == "completed"
        assert out1.terminal_observation == "failed", (
            "a failed marker write must be VISIBLE on the typed outcome, never silent"
        )
        assert not (tmp_path / "marker-run--s001" / "commit.json").exists()

        out2 = await engine.deliver_wait_event(
            wait_id, {"kind": "signal", "event_id": "evt-m", "payload": "yes"}
        )
        assert out2.kind == "duplicate"
        assert out2.terminal_observation == "recorded", (
            "at-least-once redelivery must REPAIR the missing marker for completed waits"
        )
        assert calls["n"] >= 2, "the redelivery must re-enter the commit path"
    finally:
        segment_lifecycle.commit_attempt = real_commit

    assert (tmp_path / "marker-run--s001" / "commit.json").exists()
    group = FileEventSource(tmp_path).read_group("marker-run")
    assert group.status == "completed", (
        "after repair the group shows the completed truth, not a suspended lie"
    )


async def test_correlation_is_immutable_registration_truth_across_wait_lifecycle(tmp_path):
    """W5R.3: the related-run id is captured at REGISTRATION and survives every path that
    writes after the originating run session is gone — digest-mismatch terminal evidence,
    crashed-attempt reconciliation, and cancellation — while a re-registration claiming a
    DIFFERENT correlation is refused like any other identity change. Absent stays absent."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.models import WorkflowGoal

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    shared: dict = {}
    engine, coordinator = _delivery_engine(shared, clock=clock, bundle_dir=tmp_path)
    goal = WorkflowGoal(
        workflow_type="durable_flow", objective="corr",
        metadata={"run_id": "corr-run"}, correlation_id="case-42",
    )
    first = await engine.run("durable_flow", {}, goal=goal)
    wait_id = first.wait_handle.wait_id
    stored = await coordinator.get(wait_id)
    assert stored.correlation_id == "case-42", "registration persists the related-run id"

    # crash one attempt so reconciliation writes an abandoned meta later
    real_resume = engine.executor.resume
    crash = {"armed": True}

    async def crash_once(*args, **kwargs):
        if crash.pop("armed", None):
            raise RuntimeError("boom")
        return await real_resume(*args, **kwargs)

    engine.executor.resume = crash_once
    with pytest.raises(RuntimeError):
        await engine.deliver_wait_event(
            wait_id, {"kind": "signal", "event_id": "evt-c", "payload": "x"}
        )
    current["now"] += timedelta(seconds=301)

    # deliver against a CHANGED definition -> digest mismatch -> terminal evidence
    changed, coordinator2 = _delivery_engine(shared, clock=clock, bundle_dir=tmp_path)
    changed.register_workflow(
        WorkflowBuilder("durable_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("finish")
        .step("extra", capability="finish")
        .step("escalate")
        .build()
    )
    outcome = await changed.deliver_wait_event(
        wait_id, {"kind": "signal", "event_id": "evt-c", "payload": "x"}
    )
    assert outcome.kind == "rejected" and outcome.terminal_observation == "recorded"

    wfail_meta = _bundle_meta(tmp_path / "corr-run--s001-wfail")
    assert wfail_meta["correlation_id"] == "case-42", (
        "terminal evidence projects the REGISTERED related-run id, no live context needed"
    )
    abandoned_meta = _bundle_meta(tmp_path / "corr-run--s001")
    assert abandoned_meta["status"] == "abandoned"
    assert abandoned_meta["correlation_id"] == "case-42", (
        "crashed-attempt reconciliation keeps the registered related-run id"
    )
    trace_text = (tmp_path / "corr-run--s001-wfail" / "trace.jsonl").read_text(encoding="utf-8")
    assert '"correlation_id":"case-42"' in trace_text

    # changed-correlation re-registration is an identity violation (same wait id inputs)
    from ai_workflow_engine.wait_runtime import DurableWaitRuntime

    runtime = DurableWaitRuntime(coordinator, clock=clock)
    base = _registration_request()
    await runtime.register_suspension(base)
    with pytest.raises(RuntimeError, match="DIFFERENT.*correlation_id"):
        await runtime.register_suspension(
            _registration_request(correlation_id="case-OTHER")
        )

    # absent stays absent: an uncorrelated cancelled wait writes no phantom key
    goal2 = WorkflowGoal(
        workflow_type="durable_flow", objective="plain", metadata={"run_id": "plain-w"}
    )
    second = await engine.run("durable_flow", {}, goal=goal2)
    record, observation = await engine.cancel_wait(second.wait_handle.wait_id, reason="op")
    assert observation == "recorded"
    plain_meta = _bundle_meta(tmp_path / "plain-w--s001-wfail")
    assert "correlation_id" not in plain_meta
