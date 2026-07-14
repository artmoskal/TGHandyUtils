"""W2.4/R0: FULL lifecycle conformance every product WaitCoordinator adapter must pass
(registration, claim/lease/reclaim, frozen acceptance, terminalization, failure kinds,
cancel/stalled).

Plain-assert, dependency-free (no pytest): products call
``await run_wait_registration_conformance(make_coordinator, clock=...)`` from their own test
suite. Each failure names the violated invariant. The engine trusts a PASSING adapter the
way it trusts a capability handler — this kit is the honest edge of a storage-neutral
engine's verification power (C2/C3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ai_workflow_engine.waits import DurableWaitPolicy, WaitRecord

__all__ = ["run_wait_registration_conformance"]  # name kept: it now covers the FULL lifecycle


def _record(now: datetime, wait_id: str = "w-1", *, timeout_s: float = 60.0) -> WaitRecord:
    return WaitRecord(
        wait_id=wait_id,
        run_id="run-1",
        workflow_id="wf",
        suspended_node="gate",
        definition_digest="digest-conf", policy=DurableWaitPolicy(timeout_s=timeout_s),
        created_at=now,
        deadline_at=now + timedelta(seconds=timeout_s),
    )


def _snapshot_json() -> str:
    from ai_workflow_engine.snapshot import MachineSnapshot

    return MachineSnapshot(
        schema_version="v0.11",
        workflow_id="wf", suspended_node="gate", node_status={"gate": "requires_user_input"},
        routes={}, node_results=[], artifacts=[],
        goal={"workflow_type": "wf", "objective": "conformance", "goal_id": "g-conf"},
        run_context={"workflow_id": "wf-run", "workflow_type": "wf", "goal_id": "g-conf"},
    ).model_dump_json()


def _definition_json() -> str:
    from ai_workflow_engine.workflow import WorkflowBuilder
    from ai_workflow_engine.waits import LocalWaitPolicy

    return (
        WorkflowBuilder("wf")
        .human("gate", wait_policy=LocalWaitPolicy())
        .step("finish")
        .build()
        .model_dump_json()
    )


async def run_wait_registration_conformance(
    make_coordinator: Callable[[], Any],
    *,
    clock: Callable[[], datetime] | None = None,
    reconnect: Callable[[], Any] | None = None,
) -> None:
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    snapshot_json = _snapshot_json()
    definition_json = _definition_json()

    # 1) atomic attestation: the receipt echoes EXACTLY the registered wait
    coordinator = make_coordinator()
    record = _record(now)
    receipt = await coordinator.register(record, snapshot_json, definition_json)
    assert receipt.wait_id == record.wait_id, "attestation must echo the wait_id"
    assert receipt.wait_version == record.version, "attestation must echo the version"
    assert receipt.accepted_deadline == record.deadline_at, (
        "attestation must echo the ACCEPTED deadline — a different deadline means the "
        "timeout intent was not co-committed"
    )
    assert receipt.adapter_id, "attestation must identify the adapter"

    # 2) duplicate identical registration is idempotent (crash/retry re-register)
    again = await coordinator.register(record, snapshot_json, definition_json)
    assert again == receipt, "identical duplicate registration must be idempotent"

    # 3) changed duplicate is REJECTED (never silently replaced)
    changed = record.model_copy(update={"deadline_at": record.deadline_at + timedelta(seconds=5)})
    rejected_changed = False
    try:
        await coordinator.register(changed, snapshot_json, definition_json)
    except Exception:
        rejected_changed = True
    assert rejected_changed, "a CHANGED duplicate registration must be rejected loudly"

    # 4) changed snapshot under the same wait id is also a rejected duplicate
    rejected_snapshot = False
    try:
        await coordinator.register(record, snapshot_json + " ", definition_json)
    except Exception:
        rejected_snapshot = True
    assert rejected_snapshot, "same wait id with a DIFFERENT snapshot must be rejected"

    # 5) registered state is retrievable, pending, and the SNAPSHOT round-trips exactly
    stored = await coordinator.get(record.wait_id)
    assert stored is not None and stored.status == "pending", (
        "a registered wait must be retrievable in status 'pending'"
    )
    assert stored == record, "retrievable record must equal the registered record exactly"
    loaded = await coordinator.load_snapshot(record.wait_id)
    assert loaded == snapshot_json, (
        "load_snapshot must return the EXACT registered snapshot — W3 reloads the machine "
        "from this; a dropped or altered snapshot is unrecoverable state loss"
    )
    from ai_workflow_engine.snapshot import MachineSnapshot

    MachineSnapshot.model_validate_json(loaded)  # the snapshot must stay machine-loadable
    stored_definition = await coordinator.load_definition(record.wait_id)
    assert stored_definition == definition_json, (
        "load_definition must return the EXACT registered definition bytes — W3R.3 writes "
        "terminal observation evidence from them after the live registry has moved on"
    )
    from ai_workflow_engine.workflow import WorkflowDefinition

    WorkflowDefinition.model_validate_json(stored_definition)  # must stay machine-loadable
    rejected_definition = False
    try:
        await coordinator.register(record, snapshot_json, definition_json + " ")
    except Exception:
        rejected_definition = True
    assert rejected_definition, (
        "same wait id with DIFFERENT definition bytes must be rejected — the registered "
        "machine identity is immutable"
    )
    assert await coordinator.get("missing-id") is None, "unknown wait ids return None"
    assert await coordinator.load_snapshot("missing-id") is None, (
        "missing snapshots are an explicit None, never an exception or fabrication"
    )
    assert await coordinator.load_definition("missing-id") is None, (
        "missing definitions are an explicit None, never an exception or fabrication"
    )

    # 6) reconnect/restart (where supported): a NEW adapter over the same backing store
    #    sees the identical record + snapshot
    if reconnect is not None:
        fresh = reconnect()
        again_stored = await fresh.get(record.wait_id)
        assert again_stored == record, "reconnected adapter must see the identical record"
        assert await fresh.load_snapshot(record.wait_id) == snapshot_json, (
            "reconnected adapter must see the identical snapshot"
        )
        assert await fresh.load_definition(record.wait_id) == definition_json, (
            "reconnected adapter must see the identical registered definition bytes"
        )

    # ================= R0/F6+F16: claim/complete/fail lifecycle conformance =============
    # The engine's delivery path depends on ALL of this; an adapter that only passes the
    # registration section above would TypeError or corrupt state mid-delivery. The kit
    # cannot advance an adapter-owned clock, so lease expiry is exercised by claiming with
    # a ``lease_until`` already in the past.
    from ai_workflow_engine.waits import WaitEvent

    signal = WaitEvent(kind="signal", event_id="evt-conf", payload="ok")
    other = WaitEvent(kind="timeout", event_id="evt-other")
    live_lease = now + timedelta(days=365)
    expired_lease = now - timedelta(seconds=1)

    # (a) live-lease semantics: single claimant, idempotent duplicate, losing stranger
    live = make_coordinator()
    await live.register(_record(now, wait_id="w-live"), snapshot_json, definition_json)
    first_claim = await live.claim_event("w-live", signal, lease_until=live_lease)
    assert first_claim.kind == "claimed", "a pending wait must be claimable"
    assert first_claim.claim is not None and first_claim.claim.event_id == signal.event_id
    assert first_claim.record.resume_attempts >= 1, (
        "claim_event must return the POST-increment attempt ordinal (>= 1) — the engine "
        "derives the physical attempt-directory key from it; a stale pre-claim row would "
        "make retries reuse a dead attempt's directory"
    )
    duplicate = await live.claim_event("w-live", signal, lease_until=live_lease)
    assert duplicate.kind == "duplicate", "the SAME accepted event under a live lease is idempotent"
    blocked = await live.claim_event("w-live", other, lease_until=live_lease)
    assert blocked.kind == "already_processing", "a DIFFERENT event loses against a live lease"
    done = await live.complete("w-live", first_claim.claim, resolution_kind="signal")
    assert done.status == "completed" and done.resolution_kind == "signal"
    late = await live.claim_event("w-live", other, lease_until=live_lease)
    assert late.kind == "terminal", "late different events get the terminal state, never re-execution"

    # (b) expiry semantics: frozen acceptance, same-event reclaim, monotonic attempts, CAS
    expiry = make_coordinator()
    await expiry.register(_record(now, wait_id="w-expiry"), snapshot_json, definition_json)
    crashed = await expiry.claim_event("w-expiry", signal, lease_until=expired_lease)
    assert crashed.kind == "claimed"
    first_attempt = crashed.record.resume_attempts
    frozen = await expiry.claim_event("w-expiry", other, lease_until=live_lease)
    assert frozen.kind == "not_accepted", (
        "accepted-event identity is FROZEN: after lease expiry a DIFFERENT event must get "
        "'not_accepted' and never overwrite the acceptance"
    )
    reclaim = await expiry.claim_event("w-expiry", signal, lease_until=live_lease)
    assert reclaim.kind == "claimed", "the SAME accepted event reclaims after lease expiry"
    assert reclaim.record.resume_attempts > first_attempt, (
        "attempt ordinals must be STRICTLY MONOTONIC across reclaims — equal/stale "
        "ordinals collide physical attempt directories"
    )
    stale_failed = False
    try:
        await expiry.complete("w-expiry", crashed.claim, resolution_kind="signal")
    except Exception:
        stale_failed = True
    assert stale_failed, "a STALE claim token must never terminalize a reclaimed wait (CAS)"
    recovered = await expiry.complete("w-expiry", reclaim.claim, resolution_kind="signal")
    assert recovered.status == "completed"

    # (c) fail(): the failure_kind keyword is contract and must PERSIST; unlisted kinds refuse
    failing = make_coordinator()
    await failing.register(_record(now, wait_id="w-fail"), snapshot_json, definition_json)
    fail_claim = await failing.claim_event("w-fail", signal, lease_until=live_lease)
    failed = await failing.fail(
        "w-fail", fail_claim.claim, error="conformance failure detail", failure_kind="digest_mismatch"
    )
    assert failed.status == "failed" and failed.failure_kind == "digest_mismatch", (
        "fail(..., failure_kind=) must be accepted AND persisted — a reconnected product "
        "must see WHY the wait failed"
    )
    assert failed.failure_detail == "conformance failure detail"
    bogus_rejected = False
    try:
        refail = make_coordinator()
        await refail.register(_record(now, wait_id="w-bogus"), snapshot_json, definition_json)
        bogus_claim = await refail.claim_event("w-bogus", signal, lease_until=live_lease)
        await refail.fail("w-bogus", bogus_claim.claim, error="x", failure_kind="not-a-kind")
    except Exception:
        bogus_rejected = True
    assert bogus_rejected, (
        "an UNLISTED failure_kind must be rejected at the store boundary — persisting it "
        "makes the record unloadable on reconnect"
    )

    # (d) cancel + stalled: the product-driven escape hatch frozen acceptance requires
    escape = make_coordinator()
    await escape.register(_record(now, wait_id="w-guarded"), snapshot_json, definition_json)
    await escape.claim_event("w-guarded", signal, lease_until=live_lease)
    live_blocked = False
    try:
        await escape.cancel("w-guarded", reason="operator")
    except Exception:
        live_blocked = True
    assert live_blocked, "cancel must NEVER preempt an ACTIVE claimant (live lease)"
    await escape.register(_record(now, wait_id="w-stall"), snapshot_json, definition_json)
    await escape.claim_event("w-stall", signal, lease_until=expired_lease)
    stalled = await escape.stalled(now)
    assert [r.wait_id for r in stalled] == ["w-stall"], (
        "stalled(now) must surface claimed waits with expired leases — frozen acceptance "
        "makes them unrecoverable by any other event, so they MUST be visible"
    )
    cancelled = await escape.cancel("w-stall", reason="operator gave up")
    assert cancelled.status == "cancelled" and cancelled.failure_kind == "cancelled"
    assert cancelled.failure_detail == "operator gave up"
    again_cancel = await escape.cancel("w-stall", reason="twice")
    assert again_cancel.status == "cancelled", "cancel on a terminal wait is an idempotent report"
    await escape.register(_record(now, wait_id="w-pending"), snapshot_json, definition_json)
    pending_cancelled = await escape.cancel("w-pending", reason="never needed")
    assert pending_cancelled.status == "cancelled", "a pending wait is cancellable directly"

    # ================= due()/health() surface (R10.1: SlackAzz-reported gap) =============
    # The engine's product-driven timeout path reads due(now); operators read health(). An
    # adapter that only passed the sections above could TypeError or LIE here — the earlier
    # sections never exercised either method. health() derives overdue/stalled from the
    # coordinator's INJECTED clock, so the adapter under test MUST share the ``clock`` passed
    # to this kit. Assertions are membership/DELTA based so they hold whether make_coordinator
    # returns isolated stores or (for a reconnectable adapter) one shared backend.
    surface = make_coordinator()
    before = await surface.health()
    past = now - timedelta(seconds=120)
    due_record = _record(past, wait_id="w-due", timeout_s=60.0)  # deadline = now-60 (elapsed)
    not_due_record = _record(now, wait_id="w-notdue", timeout_s=3600.0)  # deadline = now+3600
    await surface.register(due_record, snapshot_json, definition_json)
    await surface.register(not_due_record, snapshot_json, definition_json)
    # a terminal wait must never appear as due; a claimed+lease-expired wait must be 'stalled'
    await surface.register(_record(past, wait_id="w-term", timeout_s=60.0), snapshot_json, definition_json)
    term_claim = await surface.claim_event("w-term", signal, lease_until=live_lease)
    await surface.complete("w-term", term_claim.claim, resolution_kind="signal")
    await surface.register(_record(now, wait_id="w-surface-stall"), snapshot_json, definition_json)
    await surface.claim_event("w-surface-stall", signal, lease_until=expired_lease)

    due_ids = {r.wait_id for r in await surface.due(now)}
    assert "w-due" in due_ids, "due(now) must surface a pending wait whose deadline has elapsed"
    assert "w-notdue" not in due_ids, "due(now) must NEVER return a not-yet-due pending wait"
    assert "w-term" not in due_ids, "due(now) must NEVER return a terminal wait"
    assert "w-surface-stall" not in due_ids, "due(now) must NEVER return a claimed wait"
    assert all(r.status == "pending" for r in await surface.due(now)), (
        "due() yields only pending records"
    )
    assert "w-due" not in {r.wait_id for r in await surface.due(past - timedelta(seconds=1))}, (
        "due(t) before a wait's deadline must not return it — the engine must never fire early"
    )

    after = await surface.health()
    # DELTA invariants: w-due(+pending), w-notdue(+pending), w-surface-stall(net +claimed),
    # w-term(register->claim->complete = net 0). Robust to any pre-existing records in the store.
    assert after.pending - before.pending == 2, (
        f"health.pending must rise by exactly the 2 new pending waits: {before.pending}->{after.pending}"
    )
    assert after.claimed - before.claimed == 1, (
        f"health.claimed must rise by the 1 stalled (claimed) wait: {before.claimed}->{after.claimed}"
    )
    assert after.overdue - before.overdue == 1, (
        "health.overdue (DERIVED, never stored) must rise by the 1 now-elapsed pending wait (w-due)"
    )
    assert after.stalled - before.stalled == 1, (
        "health.stalled must rise by the 1 claimed+lease-expired wait — invisible to due(), "
        "unrecoverable by any other event, so health() MUST surface it"
    )
    assert after.oldest_pending_deadline is not None
    assert after.oldest_pending_deadline <= due_record.deadline_at, (
        "oldest_pending_deadline must be no later than any pending wait's deadline (w-due here)"
    )

    # due() is DERIVED from persisted records, so it survives reconnect: a fresh adapter over
    # the same store computes the same due set from the same deadline, not from memory.
    if reconnect is not None:
        fresh_surface = reconnect()
        reconnected_due = {r.wait_id for r in await fresh_surface.due(record.deadline_at + timedelta(seconds=1))}
        assert record.wait_id in reconnected_due, (
            "a reconnected adapter computes due() from the persisted deadline, not memory"
        )
        assert record.wait_id not in {r.wait_id for r in await fresh_surface.due(now)}, (
            "the reconnected w-1 (deadline in the future) must not be due at now"
        )
