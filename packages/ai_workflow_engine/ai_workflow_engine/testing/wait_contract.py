"""W2.4/R0: FULL lifecycle conformance every product WaitCoordinator adapter must pass
(registration, claim/lease/reclaim, frozen acceptance, terminalization, failure kinds,
cancel/stalled).

Plain-assert, dependency-free (no pytest): products call
``await run_wait_registration_conformance(make_coordinator, reconnect=..., clock=...)``
from their own test suite. ``reconnect`` must construct a second adapter over the same
backing store. Each failure names the violated invariant. The engine trusts a PASSING
adapter the way it trusts a capability handler — this kit is the honest edge of a
storage-neutral engine's verification power (C2/C3).

This in-process kit proves behavior across distinct adapter instances; it cannot prove
that an implementation persists transaction state across OS processes. Production
adapters must additionally run process-isolated tests against their real Redis/DB store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ai_workflow_engine.waits import DurableWaitPolicy, WaitRecord

__all__ = [
    "run_wait_registration_conformance",  # name kept: it covers the FULL lifecycle
    "run_wait_integrity_conformance",  # v0.11.5: adapter-specific corruption reporting
]


def _record(now: datetime, wait_id: str = "w-1", *, timeout_s: float = 60.0) -> WaitRecord:
    return WaitRecord(
        record_schema_version="wait-v2",
        wait_id=wait_id,
        run_id="run-1",
        workflow_id="wf",
        suspended_node="gate",
        definition_digest="digest-conf",
        policy=DurableWaitPolicy(timeout_s=timeout_s),
        registration_attempt_id=f"attempt-{wait_id}",
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
    reconnect: Callable[[], Any],
    clock: Callable[[], datetime] | None = None,
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
    # v0.11.6 (C1): registration identity is REQUIRED, non-blank, and reloadable — the
    # engine builds the exposed handle from it and compensation reloads it after
    # acknowledgement loss.
    assert receipt.registration_id and receipt.registration_id.strip(), (
        "the receipt must carry a non-blank registration_id — it is the handle-bound "
        "delivery identity for this accepted registration incarnation"
    )
    reloaded = await coordinator.load_receipt(record.wait_id)
    assert reloaded == receipt, (
        "load_receipt must return the EXACT stored attestation — crash-retry reuse and "
        "acknowledgement-loss compensation both depend on it"
    )
    assert await coordinator.load_receipt("missing-id") is None, (
        "missing receipts are an explicit None, never an exception or fabrication"
    )

    # 2) duplicate identical registration is idempotent (crash/retry re-register) and
    #    returns the SAME registration identity — one accepted attempt, one incarnation
    again = await coordinator.register(record, snapshot_json, definition_json)
    assert again == receipt, "identical duplicate registration must be idempotent"
    assert again.registration_id == receipt.registration_id, (
        "exact idempotent retry must reuse the stored registration_id, never mint a "
        "second incarnation identity for the same accepted attempt"
    )

    # 2b) reconnect is explicitly a second adapter over this backing store. It must see
    # and reuse the same accepted incarnation, not mint process-local identity.
    other_store = reconnect()
    assert other_store is not coordinator, (
        "reconnect must return a NEW coordinator instance over the same backing store"
    )
    shared_record = await other_store.get(record.wait_id)
    assert shared_record == record, (
        "a reconnected adapter must see the accepted record before retry registration"
    )
    shared_receipt = await other_store.register(record, snapshot_json, definition_json)
    assert shared_receipt.registration_id == receipt.registration_id, (
        "a reconnected adapter re-registering the identical record is the same accepted "
        "incarnation and must return the same registration_id"
    )

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

    # 6) reconnect/restart: a NEW adapter over the same backing store sees every
    # lifecycle fact. This is mandatory because registration compensation races other
    # processes; a same-process-only conformance pass is not a durable contract.
    fresh = reconnect()
    assert fresh is not coordinator, (
        "reconnect must return a NEW coordinator instance over the same backing store"
    )
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
    live_receipt = await live.register(_record(now, wait_id="w-live"), snapshot_json, definition_json)
    # v0.11.6 (C1): a registration id that does not name the accepted incarnation is
    # refused LOUDLY and BEFORE any mutation — the first legitimate claim below still
    # sees attempt ordinal 1, proving the refusal changed nothing.
    stale_refused = False
    try:
        await live.claim_event(
            "w-live", signal, registration_id="reg-never-exposed", lease_until=live_lease
        )
    except Exception:
        stale_refused = True
    assert stale_refused, (
        "claim_event must refuse a registration_id that does not match the stored "
        "receipt — a never-exposed or retired incarnation's handle cannot claim"
    )
    unchanged = await live.get("w-live")
    assert unchanged is not None and unchanged.status == "pending" and unchanged.resume_attempts == 0, (
        "a refused registration-identity claim must leave the wait record untouched"
    )
    first_claim = await live.claim_event(
        "w-live", signal, registration_id=live_receipt.registration_id, lease_until=live_lease
    )
    assert first_claim.kind == "claimed", "a pending wait must be claimable"
    assert first_claim.claim is not None and first_claim.claim.event_id == signal.event_id
    assert first_claim.record.resume_attempts >= 1, (
        "claim_event must return the POST-increment attempt ordinal (>= 1) — the engine "
        "derives the physical attempt-directory key from it; a stale pre-claim row would "
        "make retries reuse a dead attempt's directory"
    )
    duplicate = await live.claim_event(
        "w-live", signal, registration_id=live_receipt.registration_id, lease_until=live_lease
    )
    assert duplicate.kind == "duplicate", "the SAME accepted event under a live lease is idempotent"
    blocked = await live.claim_event(
        "w-live", other, registration_id=live_receipt.registration_id, lease_until=live_lease
    )
    assert blocked.kind == "already_processing", "a DIFFERENT event loses against a live lease"
    done = await live.complete("w-live", first_claim.claim, resolution_kind="signal")
    assert done.status == "completed" and done.resolution_kind == "signal"
    late = await live.claim_event(
        "w-live", other, registration_id=live_receipt.registration_id, lease_until=live_lease
    )
    assert late.kind == "terminal", "late different events get the terminal state, never re-execution"
    # v0.11.6 (C1): a terminal record is never revived by a fresh same-id registration —
    # normal operation has no second incarnation inside one backing store.
    revival_refused = False
    try:
        await live.register(_record(now, wait_id="w-live"), snapshot_json, definition_json)
    except Exception:
        revival_refused = True
    assert revival_refused, (
        "registering a fresh record under a TERMINAL wait's id must be refused — terminal "
        "records are never revived as new active suspensions"
    )

    # (b) expiry semantics: frozen acceptance, same-event reclaim, monotonic attempts, CAS
    expiry = make_coordinator()
    expiry_receipt = await expiry.register(
        _record(now, wait_id="w-expiry"), snapshot_json, definition_json
    )
    crashed = await expiry.claim_event(
        "w-expiry", signal, registration_id=expiry_receipt.registration_id, lease_until=expired_lease
    )
    assert crashed.kind == "claimed"
    first_attempt = crashed.record.resume_attempts
    frozen = await expiry.claim_event(
        "w-expiry", other, registration_id=expiry_receipt.registration_id, lease_until=live_lease
    )
    assert frozen.kind == "not_accepted", (
        "accepted-event identity is FROZEN: after lease expiry a DIFFERENT event must get "
        "'not_accepted' and never overwrite the acceptance"
    )
    reclaim = await expiry.claim_event(
        "w-expiry", signal, registration_id=expiry_receipt.registration_id, lease_until=live_lease
    )
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
    failing_receipt = await failing.register(
        _record(now, wait_id="w-fail"), snapshot_json, definition_json
    )
    fail_claim = await failing.claim_event(
        "w-fail", signal, registration_id=failing_receipt.registration_id, lease_until=live_lease
    )
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
        refail_receipt = await refail.register(
            _record(now, wait_id="w-bogus"), snapshot_json, definition_json
        )
        bogus_claim = await refail.claim_event(
            "w-bogus", signal, registration_id=refail_receipt.registration_id, lease_until=live_lease
        )
        await refail.fail("w-bogus", bogus_claim.claim, error="x", failure_kind="not-a-kind")
    except Exception:
        bogus_rejected = True
    assert bogus_rejected, (
        "an UNLISTED failure_kind must be rejected at the store boundary — persisting it "
        "makes the record unloadable on reconnect"
    )

    # (d) cancel + stalled: the product-driven escape hatch frozen acceptance requires
    escape = make_coordinator()
    guarded_receipt = await escape.register(
        _record(now, wait_id="w-guarded"), snapshot_json, definition_json
    )
    await escape.claim_event(
        "w-guarded", signal, registration_id=guarded_receipt.registration_id, lease_until=live_lease
    )
    live_blocked = False
    try:
        await escape.cancel("w-guarded", reason="operator")
    except Exception:
        live_blocked = True
    assert live_blocked, "cancel must NEVER preempt an ACTIVE claimant (live lease)"
    stall_receipt = await escape.register(
        _record(now, wait_id="w-stall"), snapshot_json, definition_json
    )
    await escape.claim_event(
        "w-stall", signal, registration_id=stall_receipt.registration_id, lease_until=expired_lease
    )
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

    # ============== v0.11.6 (C2): atomic registration-abort conformance ==============
    # The engine settles a possibly-committed, never-exposed registration through this
    # operation. Outcomes are closed and TRUTHFUL; validation precedes every mutation.
    aborter = make_coordinator()
    absent = await aborter.abort_registration(
        "w-abort-never", expected_registration_id="reg-x",
        expected_registration_attempt_id="attempt-x",
        expected_definition_digest="digest-conf", reason="settle",
    )
    assert absent.kind == "absent", "aborting an uncommitted wait id must report 'absent'"
    abort_record = _record(now, wait_id="w-abort")
    abort_receipt = await aborter.register(
        abort_record, snapshot_json, definition_json
    )
    wrong_id = await aborter.abort_registration(
        "w-abort", expected_registration_id="reg-not-it",
        expected_registration_attempt_id=abort_record.registration_attempt_id,
        expected_definition_digest="digest-conf", reason="settle",
    )
    assert wrong_id.kind == "refused_mismatch", (
        "a registration-id mismatch must be refused — never cancel another incarnation"
    )
    wrong_digest = await aborter.abort_registration(
        "w-abort", expected_registration_id=abort_receipt.registration_id,
        expected_registration_attempt_id=abort_record.registration_attempt_id,
        expected_definition_digest="digest-other", reason="settle",
    )
    assert wrong_digest.kind == "refused_mismatch", (
        "a definition-digest mismatch must be refused — never cancel a different machine"
    )
    after_refusals = await aborter.get("w-abort")
    assert after_refusals is not None and after_refusals.status == "pending" and (
        after_refusals.resume_attempts == 0
    ), "refused aborts must MUTATE NOTHING — validation precedes mutation"
    reused_record = _record(now, wait_id="w-abort-reused")
    reused_receipt = await aborter.register(
        reused_record, snapshot_json, definition_json
    )
    retry_record = reused_record.model_copy(
        update={"registration_attempt_id": "attempt-conformance-retry"}
    )
    await aborter.register(retry_record, snapshot_json, definition_json)
    retry_abort = await aborter.abort_registration(
        "w-abort-reused",
        expected_registration_id=reused_receipt.registration_id,
        expected_registration_attempt_id=retry_record.registration_attempt_id,
        expected_definition_digest="digest-conf",
        reason="retry cancelled",
    )
    assert retry_abort.kind == "not_creator", (
        "a retry did not create the registration and must never revoke its creator"
    )
    creator_after_reuse = await aborter.abort_registration(
        "w-abort-reused",
        expected_registration_id=reused_receipt.registration_id,
        expected_registration_attempt_id=reused_record.registration_attempt_id,
        expected_definition_digest="digest-conf",
        reason="creator cancelled late",
    )
    assert creator_after_reuse.kind == "refused_reused", (
        "once another invocation reused a registration, creator compensation must lose "
        "the CAS rather than revoke a potentially exposed handle"
    )
    assert (await aborter.get("w-abort-reused")).status == "pending"

    # Distinct-instance CAS is load-bearing. The creator and exact retry use different
    # coordinator instances over one backing store. Product suites must repeat this race
    # in separate OS processes against the real store; an in-process kit cannot detect a
    # class-level cache shared by every instance in this interpreter.
    cross_creator = make_coordinator()
    cross_record = _record(now, wait_id="w-abort-distinct-instance")
    cross_receipt = await cross_creator.register(
        cross_record, snapshot_json, definition_json
    )
    cross_reuser = reconnect()
    assert cross_reuser is not cross_creator, (
        "distinct-instance compensation requires separate coordinator objects"
    )
    cross_retry = cross_record.model_copy(
        update={"registration_attempt_id": "attempt-distinct-instance-retry"}
    )
    cross_retry_receipt = await cross_reuser.register(
        cross_retry, snapshot_json, definition_json
    )
    assert cross_retry_receipt == cross_receipt, (
        "a reconnected exact retry must reuse the accepted receipt"
    )
    cross_retry_abort = await cross_reuser.abort_registration(
        cross_record.wait_id,
        expected_registration_id=cross_receipt.registration_id,
        expected_registration_attempt_id=cross_retry.registration_attempt_id,
        expected_definition_digest=cross_record.definition_digest,
        reason="reuser cancelled",
    )
    assert cross_retry_abort.kind == "not_creator", (
        "the reconnected retry must not revoke a registration created elsewhere"
    )
    cross_creator_abort = await cross_creator.abort_registration(
        cross_record.wait_id,
        expected_registration_id=cross_receipt.registration_id,
        expected_registration_attempt_id=cross_record.registration_attempt_id,
        expected_definition_digest=cross_record.definition_digest,
        reason="creator cancelled after distinct-instance reuse",
    )
    assert cross_creator_abort.kind == "refused_reused", (
        "retry participation must survive reconnect — creator compensation must not "
        "revoke a handle another process may have exposed"
    )
    assert (await reconnect().get(cross_record.wait_id)).status == "pending"

    # Creator-wins ordering: merely reconnecting does not invent participation. If no
    # retry has registered yet, the creator may still cancel and every process sees it.
    creator_wins = make_coordinator()
    creator_wins_record = _record(now, wait_id="w-abort-creator-wins")
    creator_wins_receipt = await creator_wins.register(
        creator_wins_record, snapshot_json, definition_json
    )
    creator_wins_observer = reconnect()
    assert (await creator_wins_observer.get(creator_wins_record.wait_id)).status == "pending"
    creator_wins_abort = await creator_wins.abort_registration(
        creator_wins_record.wait_id,
        expected_registration_id=creator_wins_receipt.registration_id,
        expected_registration_attempt_id=creator_wins_record.registration_attempt_id,
        expected_definition_digest=creator_wins_record.definition_digest,
        reason="creator cancelled before reuse",
    )
    assert creator_wins_abort.kind == "cancelled"
    assert (await creator_wins_observer.get(creator_wins_record.wait_id)).status == "cancelled", (
        "creator compensation must be visible after reconnect"
    )
    retry_rejected = False
    try:
        await creator_wins_observer.register(
            creator_wins_record, snapshot_json, definition_json
        )
    except Exception:
        retry_rejected = True
    assert retry_rejected, (
        "an exact retry arriving AFTER creator compensation must fail loudly, never "
        "revive the cancelled registration or expose a handle"
    )
    assert (await creator_wins_observer.get(creator_wins_record.wait_id)).status == "cancelled", (
        "a rejected post-compensation retry must leave terminal state unchanged"
    )
    settled = await aborter.abort_registration(
        "w-abort", expected_registration_id=abort_receipt.registration_id,
        expected_registration_attempt_id=abort_record.registration_attempt_id,
        expected_definition_digest="digest-conf", reason="never exposed",
    )
    assert settled.kind == "cancelled" and settled.record is not None and (
        settled.record.status == "cancelled"
    ), "an exact pending registration must be atomically cancelled"
    stored_settled = await aborter.get("w-abort")
    assert stored_settled is not None and stored_settled.status == "cancelled", (
        "the 'cancelled' outcome must be TRUTHFUL — the stored record moved with it"
    )
    repeat_settle = await aborter.abort_registration(
        "w-abort", expected_registration_id=abort_receipt.registration_id,
        expected_registration_attempt_id=abort_record.registration_attempt_id,
        expected_definition_digest="digest-conf", reason="again",
    )
    assert repeat_settle.kind == "already_terminal" and (
        repeat_settle.record is not None and repeat_settle.record.status == "cancelled"
    ), "repeated compensation is an idempotent terminal report"
    claim_guard_record = _record(now, wait_id="w-abort-claimed")
    claim_guard_receipt = await aborter.register(
        claim_guard_record, snapshot_json, definition_json
    )
    abort_claim = await aborter.claim_event(
        "w-abort-claimed", signal,
        registration_id=claim_guard_receipt.registration_id, lease_until=live_lease,
    )
    assert abort_claim.kind == "claimed"
    claimed_refused = await aborter.abort_registration(
        "w-abort-claimed", expected_registration_id=claim_guard_receipt.registration_id,
        expected_registration_attempt_id=claim_guard_record.registration_attempt_id,
        expected_definition_digest="digest-conf", reason="settle",
    )
    assert claimed_refused.kind == "refused_active_claim", (
        "compensation must never preempt a claimant — delivery owns a claimed wait"
    )
    assert (await aborter.get("w-abort-claimed")).status == "claimed", (
        "the active-claim refusal must change nothing"
    )
    await aborter.complete("w-abort-claimed", abort_claim.claim, resolution_kind="signal")
    delivery_won = await aborter.abort_registration(
        "w-abort-claimed", expected_registration_id=claim_guard_receipt.registration_id,
        expected_registration_attempt_id=claim_guard_record.registration_attempt_id,
        expected_definition_digest="digest-conf", reason="settle",
    )
    assert delivery_won.kind == "already_terminal" and (
        delivery_won.record is not None and delivery_won.record.status == "completed"
    ), "a delivery-won registration reports terminal truth — execution is never rewritten"

    # ================= due()/health() surface (R10.1: SlackAzz-reported gap) =============
    # The engine's product-driven timeout path reads due(now); operators read health(). An
    # adapter that only passed the sections above could TypeError or LIE here — the earlier
    # sections never exercised either method. health() derives overdue/stalled from the
    # coordinator's INJECTED clock, so the adapter under test MUST share the ``clock`` passed
    # to this kit. Assertions are membership/DELTA based so they hold whether make_coordinator
    # returns isolated stores or (for a reconnectable adapter) one shared backend.
    surface = make_coordinator()
    before = await surface.health()
    # H7: ordinary lifecycle conformance BEGINS at zero integrity — a constant or invented
    # nonzero count is a lying adapter, not a healthy store.
    assert before.integrity_errors == 0, (
        f"lifecycle conformance requires integrity_errors == 0 at start, got {before.integrity_errors}"
    )
    past = now - timedelta(seconds=120)
    due_record = _record(past, wait_id="w-due", timeout_s=60.0)  # deadline = now-60 (elapsed)
    not_due_record = _record(now, wait_id="w-notdue", timeout_s=3600.0)  # deadline = now+3600
    await surface.register(due_record, snapshot_json, definition_json)
    await surface.register(not_due_record, snapshot_json, definition_json)
    # a terminal wait must never appear as due; a claimed+lease-expired wait must be 'stalled'
    term_receipt = await surface.register(
        _record(past, wait_id="w-term", timeout_s=60.0), snapshot_json, definition_json
    )
    term_claim = await surface.claim_event(
        "w-term", signal, registration_id=term_receipt.registration_id, lease_until=live_lease
    )
    await surface.complete("w-term", term_claim.claim, resolution_kind="signal")
    surface_stall_receipt = await surface.register(
        _record(now, wait_id="w-surface-stall"), snapshot_json, definition_json
    )
    await surface.claim_event(
        "w-surface-stall",
        signal,
        registration_id=surface_stall_receipt.registration_id,
        lease_until=expired_lease,
    )

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

    # v0.11.5 (U1): the failed count must cover BOTH producers — the public fail() door and
    # bounded-recovery attempts exhaustion — and the store must report a zero-integrity shape.
    fail_door = _record(now, wait_id="w-surface-fail")
    fail_door_receipt = await surface.register(fail_door, snapshot_json, definition_json)
    surface_fail_claim = await surface.claim_event(
        "w-surface-fail",
        signal,
        registration_id=fail_door_receipt.registration_id,
        lease_until=live_lease,
    )
    await surface.fail(
        "w-surface-fail", surface_fail_claim.claim, error="surface failure", failure_kind="resume_failed"
    )
    exhaust = _record(now, wait_id="w-surface-exhaust")
    exhaust_receipt = await surface.register(exhaust, snapshot_json, definition_json)
    exhausted_outcome = None
    for _ in range(exhaust.policy.max_resume_attempts + 1):
        exhausted_outcome = await surface.claim_event(
            "w-surface-exhaust",
            signal,
            registration_id=exhaust_receipt.registration_id,
            lease_until=expired_lease,
        )
    assert exhausted_outcome is not None and exhausted_outcome.kind == "attempts_exhausted", (
        "bounded recovery must terminalize as attempts_exhausted after max_resume_attempts"
    )
    assert exhausted_outcome.record.status == "failed"

    after = await surface.health()
    # DELTA invariants: w-due(+pending), w-notdue(+pending), w-surface-stall(net +claimed),
    # w-term(register->claim->complete = net 0), w-surface-fail + w-surface-exhaust (+2 failed).
    # Robust to any pre-existing records in the store.
    assert after.pending - before.pending == 2, (
        f"health.pending must rise by exactly the 2 new pending waits: {before.pending}->{after.pending}"
    )
    assert after.failed - before.failed == 2, (
        "health.failed must count every status=='failed' record from BOTH producers — the "
        f"public fail() door and attempts exhaustion: {before.failed}->{after.failed}"
    )
    # H7: ...and ENDS at zero — the full lifecycle above created no corruption.
    assert after.integrity_errors == 0, (
        f"lifecycle conformance must end at integrity_errors == 0, got {after.integrity_errors} — "
        "an uncorrupted store must never invent integrity errors"
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


async def run_wait_integrity_conformance(
    make_coordinator: Callable[[], Any],
    *,
    inject_corruption: Callable[[], Any],
    repair_corruption: Callable[[], Any],
    clock: Callable[[], datetime] | None = None,
    reconnect: Callable[[], Any] | None = None,
) -> None:
    """v0.11.5 (U1): adapter-specific corruption-reporting conformance — TESTING ONLY.

    Corruption injection cannot be generic: only the adapter's own tests know how to write a
    malformed entry into their backend. Products supply ``inject_corruption`` (write one
    persisted entry that can be enumerated but not decoded/validated as the current
    ``WaitRecord`` contract) and ``repair_corruption`` (remove or fix that entry). Both may
    be sync or async. The runtime ``WaitCoordinator`` protocol gains NO corruption controls.

    Contract proven here: baseline zero; one malformed entry reports EXACTLY one
    ``integrity_errors`` while every other count stays truthful and valid records stay
    retrievable; a reconnected adapter observes the same condition; repair/removal returns
    the count to zero. Separately (documented on ``WaitHealth``): if the backend cannot be
    enumerated at all, ``health()`` must RAISE — an outage is never ``integrity_errors=0``.
    """

    import inspect

    async def _call(hook: Callable[[], Any]) -> None:
        result = hook()
        if inspect.isawaitable(result):
            await result

    now = (clock or (lambda: datetime.now(timezone.utc)))()
    snapshot_json = _snapshot_json()
    definition_json = _definition_json()

    def _assert_full_health(actual, expected, moment: str) -> None:
        # H4-H6: COMPLETE-snapshot equality, field by field so a lying adapter fails by the
        # exact drifted field — one field standing still never proves the others did.
        for field in (
            "pending",
            "claimed",
            "overdue",
            "stalled",
            "failed",
            "integrity_errors",
            "oldest_pending_deadline",
        ):
            got, want = getattr(actual, field), getattr(expected, field)
            assert got == want, (
                f"{moment}: health.{field} drifted — expected {want!r}, got {got!r}; "
                "integrity conformance compares the COMPLETE health model"
            )

    coordinator = make_coordinator()
    baseline = await coordinator.health()
    assert baseline.integrity_errors == 0, (
        "an uncorrupted backend must report integrity_errors == 0 at baseline"
    )

    valid = _record(now, wait_id="w-intact")
    await coordinator.register(valid, snapshot_json, definition_json)
    healthy = await coordinator.health()
    assert healthy.integrity_errors == 0, "registration alone must not create integrity errors"

    await _call(inject_corruption)
    corrupted = await coordinator.health()
    expected_corrupted = healthy.model_copy(
        update={"integrity_errors": healthy.integrity_errors + 1}
    )
    _assert_full_health(corrupted, expected_corrupted, "while corrupted")
    still = await coordinator.get(valid.wait_id)
    assert still is not None and still == valid, (
        "valid records must remain retrievable while a sibling entry is corrupt"
    )

    if reconnect is not None:
        fresh = reconnect()
        observed = await fresh.health()
        _assert_full_health(observed, expected_corrupted, "on reconnect while corrupted")

    await _call(repair_corruption)
    repaired = await coordinator.health()
    _assert_full_health(repaired, healthy, "after repair")
