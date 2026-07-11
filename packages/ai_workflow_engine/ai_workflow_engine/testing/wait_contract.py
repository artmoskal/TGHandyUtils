"""W2.4: registration conformance cases every product WaitCoordinator adapter must pass.

Plain-assert, dependency-free (no pytest): products call
``await run_wait_registration_conformance(make_coordinator, clock=...)`` from their own test
suite. Each failure names the violated invariant. The engine trusts a PASSING adapter the
way it trusts a capability handler — this kit is the honest edge of a storage-neutral
engine's verification power (C2/C3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ai_workflow_engine.waits import DurableWaitPolicy, WaitRecord

__all__ = ["run_wait_registration_conformance"]


def _record(now: datetime, wait_id: str = "w-1", *, timeout_s: float = 60.0) -> WaitRecord:
    return WaitRecord(
        wait_id=wait_id,
        run_id="run-1",
        workflow_id="wf",
        suspended_node="gate",
        policy=DurableWaitPolicy(timeout_s=timeout_s),
        created_at=now,
        deadline_at=now + timedelta(seconds=timeout_s),
    )


async def run_wait_registration_conformance(
    make_coordinator: Callable[[], Any], *, clock: Callable[[], datetime] | None = None
) -> None:
    now = (clock or (lambda: datetime.now(timezone.utc)))()

    # 1) atomic attestation: the receipt echoes EXACTLY the registered wait
    coordinator = make_coordinator()
    record = _record(now)
    receipt = await coordinator.register(record, '{"snapshot": 1}')
    assert receipt.wait_id == record.wait_id, "attestation must echo the wait_id"
    assert receipt.wait_version == record.version, "attestation must echo the version"
    assert receipt.accepted_deadline == record.deadline_at, (
        "attestation must echo the ACCEPTED deadline — a different deadline means the "
        "timeout intent was not co-committed"
    )
    assert receipt.adapter_id, "attestation must identify the adapter"

    # 2) duplicate identical registration is idempotent (crash/retry re-register)
    again = await coordinator.register(record, '{"snapshot": 1}')
    assert again == receipt, "identical duplicate registration must be idempotent"

    # 3) changed duplicate is REJECTED (never silently replaced)
    changed = record.model_copy(update={"deadline_at": record.deadline_at + timedelta(seconds=5)})
    rejected_changed = False
    try:
        await coordinator.register(changed, '{"snapshot": 1}')
    except Exception:
        rejected_changed = True
    assert rejected_changed, "a CHANGED duplicate registration must be rejected loudly"

    # 4) changed snapshot under the same wait id is also a rejected duplicate
    rejected_snapshot = False
    try:
        await coordinator.register(record, '{"snapshot": 2}')
    except Exception:
        rejected_snapshot = True
    assert rejected_snapshot, "same wait id with a DIFFERENT snapshot must be rejected"

    # 5) registered state is retrievable and pending
    stored = await coordinator.get(record.wait_id)
    assert stored is not None and stored.status == "pending", (
        "a registered wait must be retrievable in status 'pending'"
    )
    assert await coordinator.get("missing-id") is None, "unknown wait ids return None"
