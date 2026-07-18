"""Durable-wait coordination — W1 public contract (models + vocabulary ONLY).

This is a dependency-light LEAF: pydantic + stdlib. No provider, viewer, or executor
imports. Wait intent is explicit machine data:

- ``LocalWaitPolicy``  — today's in-process suspension: the snapshot is exposed publicly and
  the product resumes it directly (``engine.resume``). No coordinator, no wall clock.
- ``DurableWaitPolicy`` — the wait must be REGISTERED with a product-owned coordinator
  before the engine exposes anything (W2); a finite timeout and one declared ``on_timeout``
  transition are mandatory; the public result carries a typed ``WaitHandle``, never a raw
  snapshot (C1).

Lifecycle vocabulary (C5): processing status is separate from resolution reason.
``overdue`` is a DERIVED health predicate (pending + deadline passed), never a stored
terminal status — a passed deadline proves nothing about timeout processing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional, Protocol, Union, runtime_checkable

from pydantic import model_validator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "WaitClaimOutcome",
    "WaitCoordinator",
    "InMemoryWaitCoordinator",
    "WAIT_STATUSES",
    "FAILURE_KINDS",
    "RESOLUTION_KINDS",
    "LocalWaitPolicy",
    "DurableWaitPolicy",
    "WaitPolicy",
    "WaitEvent",
    "WaitRecord",
    "WaitReceipt",
    "WaitClaim",
    "WaitHandle",
    "WaitHealth",
    "wait_policy_from",
]

from ai_workflow_engine.wait_contract import WAIT_STATUSES, WaitHandle, WaitStatus  # noqa: F401

# W3R.3/R0: closed terminal-failure vocabulary — persisted on WaitRecord and VALIDATED
# at the store boundary (an unlisted kind would make the record unloadable on reconnect).
FAILURE_KINDS = ("digest_mismatch", "snapshot_missing", "attempts_exhausted", "resume_failed", "cancelled")
# (re-exported: the contract leaf is the one shared source for the run-result surface)

RESOLUTION_KINDS = ("signal", "timeout")
ResolutionKind = Literal["signal", "timeout"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LocalWaitPolicy(BaseModel):
    """Explicitly local/in-process wait: snapshot exposed, product resumes directly."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["local"] = "local"


class DurableWaitPolicy(BaseModel):
    """Registered durable wait: finite timeout + declared timeout route are MANDATORY."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["durable"] = "durable"
    timeout_s: float
    max_resume_attempts: int = Field(default=3, ge=1)
    # correlation keys the product's signal ingress must echo (values are product-owned)
    signal_correlation: Dict[str, str] = Field(default_factory=dict)

    @field_validator("timeout_s")
    @classmethod
    def _finite_positive(cls, value: float) -> float:
        import math

        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"durable wait timeout_s must be a finite positive number, got {value}")
        return value


WaitPolicy = Union[LocalWaitPolicy, DurableWaitPolicy]


def wait_policy_from(value: Any) -> "LocalWaitPolicy | DurableWaitPolicy":
    """Normalize a policy declaration (model or dict) — loud on anything else."""

    if isinstance(value, (LocalWaitPolicy, DurableWaitPolicy)):
        return value
    if isinstance(value, dict):
        mode = value.get("mode")
        if mode == "local":
            return LocalWaitPolicy.model_validate(value)
        if mode == "durable":
            return DurableWaitPolicy.model_validate(value)
        raise ValueError(f"wait policy dict requires mode 'local' or 'durable', got {mode!r}")
    raise TypeError(f"unsupported wait policy declaration: {type(value).__name__}")


class WaitEvent(BaseModel):
    """One delivered outcome for a wait — signal or timeout; event_id deduplicates."""

    model_config = ConfigDict(extra="forbid")

    kind: ResolutionKind
    event_id: str
    payload: Any = None

    @field_validator("payload")
    @classmethod
    def _payload_byte_safe(cls, value: Any) -> Any:
        # W1R.1: durable coordinators PERSIST this — raw bytes/media are unrepresentable,
        # enforced with the engine's canonical persisted-state rule.
        if value is not None:
            from ai_workflow_engine.byte_safety import assert_byte_safe  # dependency-light leaf

            assert_byte_safe(value, mode="persist", path="wait_event.payload")
        return value

    @field_validator("event_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("event_id must be non-empty — it is the deduplication key")
        return value


WAIT_RECORD_SCHEMA_VERSION = "wait-v1"


class WaitRecord(BaseModel):
    """The engine-owned wait state a coordinator persists (snapshot itself is store-owned).

    v0.11 clean contract (manifest row M12): the record is VERSIONED and closed — a coordinator
    returning a pre-v0.11 or unknown record shape fails loudly at validation; the current line
    has no importer for old persisted waits (approved policy: pending old waits are discarded).
    """

    model_config = ConfigDict(extra="forbid")

    record_schema_version: Literal["wait-v1"]
    wait_id: str
    run_id: str
    workflow_id: str
    suspended_node: str
    policy: DurableWaitPolicy
    # W2B.1/W2C.1: the machine identity is PERSISTED and MANDATORY — W3 compares this
    # against the currently registered definition before resuming; a blank digest would
    # silently disable changed-machine protection and is rejected at construction.
    definition_digest: str
    status: WaitStatus = "pending"
    resolution_kind: Optional[ResolutionKind] = None  # set only when an event WINS (C5)
    created_at: AwareDatetime = Field(default_factory=_utc_now)

    @model_validator(mode="before")
    @classmethod
    def _current_record_version_only(cls, data: Any) -> Any:
        if isinstance(data, dict):
            found = data.get("record_schema_version")
            if found != WAIT_RECORD_SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported wait-record schema: expected {WAIT_RECORD_SCHEMA_VERSION!r}, "
                    f"got {found!r} — pre-v0.11 waits are not readable by the current line "
                    f"(discard them or inspect with the matching historical tag)"
                )
        return data
    deadline_at: AwareDatetime
    version: int = Field(default=1, ge=1)

    @field_validator("definition_digest")
    @classmethod
    def _non_blank_digest(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "definition_digest must be a non-blank machine identity — blank would "
                "disable changed-machine protection"
            )
        return value
    resume_attempts: int = Field(default=0, ge=0)
    # W3R.3: terminal failure truth is PERSISTED, not just returned — a reconnected
    # product must be able to tell WHY a wait failed (closed vocabulary + bounded detail).
    failure_kind: Optional[
        Literal["digest_mismatch", "snapshot_missing", "attempts_exhausted", "resume_failed", "cancelled"]
    ] = None
    failure_detail: Optional[str] = Field(default=None, max_length=500)
    # W3R.3a/R1: immutable terminal-observation lineage, captured AT REGISTRATION. This is
    # the LOGICAL run position (segment index) of the suspending run-half — stable across
    # physical delivery attempts, so crash-retry re-registration of a chained wait stays
    # byte-identical. None = the suspension ran without observation (no on-disk group
    # exists that could misreport).
    origin_segment_index: Optional[int] = Field(default=None, ge=0)
    # Related-run id captured at REGISTRATION as immutable truth: terminal and abandoned
    # evidence writers project it without touching any live run context or registry.
    # Optional; never a storage/dedup/scheduling key.
    correlation_id: Optional[str] = None

    @field_validator("correlation_id")
    @classmethod
    def _non_blank_correlation(cls, value):
        from ai_workflow_engine.correlation import validate_optional_correlation

        return validate_optional_correlation(value)


class WaitReceipt(BaseModel):
    """Adapter ATTESTATION that wait state + timeout intent were accepted together (C2).

    This is a registration result, NOT proof — the engine validates its shape, fails loudly
    on mismatch, and otherwise trusts the configured adapter like any capability handler."""

    model_config = ConfigDict(extra="forbid")

    registration_id: str
    wait_id: str
    wait_version: int = Field(ge=1)
    accepted_deadline: AwareDatetime
    adapter_id: str


class WaitClaim(BaseModel):
    """Single-owner claim for event processing (C3): lease/version, not exactly-once magic."""

    model_config = ConfigDict(extra="forbid")

    wait_id: str
    wait_version: int = Field(ge=1)
    event_id: str
    claimed_at: AwareDatetime = Field(default_factory=_utc_now)
    lease_expires_at: Optional[AwareDatetime] = None


class WaitClaimOutcome(BaseModel):
    """W3.1: typed result of an atomic claim attempt — never an untyped error for losers.

    - ``claimed``: this caller owns processing (single active claimant; lease set).
    - ``duplicate``: the SAME event id was already accepted — idempotent, no execution.
    - ``already_processing``: a DIFFERENT event lost the race while a live claim exists.
    - ``not_accepted``: a DIFFERENT event arrived after acceptance (even with the lease
      expired) — accepted-event identity is FROZEN (W3R.2): lease recovery transfers
      execution ownership only to the SAME accepted event id; nothing ever replaces an
      accepted resolution. This is a TRANSIENT loser report (the wait is still alive) —
      deliberately distinct from the delivery-level terminal ``rejected`` (F10). Recovery
      of a lost accepted event is the product outbox's at-least-once redelivery duty.
    - ``terminal``: the wait already resolved — late events get the terminal state, never
      a re-execution.
    - ``attempts_exhausted``: bounded recovery ran out — the engine fails wait AND run.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "claimed", "duplicate", "already_processing", "not_accepted", "terminal", "attempts_exhausted"
    ]
    record: WaitRecord
    claim: Optional[WaitClaim] = None


class WaitHealth(BaseModel):
    """Product-neutral coordinator health surface; ``overdue`` is DERIVED, never stored."""

    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    claimed: int = Field(ge=0)
    overdue: int = Field(ge=0)
    # F9: claimed waits whose lease expired — invisible to due() (they are not pending),
    # unrecoverable by any other event (frozen acceptance), so they MUST be surfaced for
    # the product to redeliver the accepted event or cancel().
    stalled: int = Field(default=0, ge=0)
    # v0.11.5 (U1, SlackAzz AC-11): REQUIRED, deliberately no defaults — an adapter that has
    # not implemented the current health contract must fail loudly constructing this model,
    # never present a healthy-looking zero (latest-only line).
    # ``failed``: current count of records with status=="failed" (any failure_kind;
    # cancellations are a distinct status and never counted here).
    failed: int = Field(ge=0)
    # ``integrity_errors``: current count of persisted entries the adapter can enumerate but
    # cannot decode/validate as the current WaitRecord contract. Such entries are EXCLUDED
    # from every other count and counted here exactly once; the count clears when the entry
    # is repaired or removed. If the backend cannot be enumerated at all, ``health()`` must
    # RAISE — an outage must never be reported as integrity_errors=0.
    integrity_errors: int = Field(ge=0)
    oldest_pending_deadline: Optional[AwareDatetime] = None


# ====================================================================== W2: coordinator


@runtime_checkable
class WaitCoordinator(Protocol):
    """Product-owned durable-wait store/transport seam (W2). The engine calls REGISTER
    before exposing any durable suspension and trusts the attestation like it trusts a
    capability handler; delivery/claim mechanics land in W3. Implementations must persist
    wait state and timeout intent atomically IN THEIR OWN transactional domain (C2/C3)."""

    async def register(
        self, record: WaitRecord, snapshot_json: str, definition_json: str
    ) -> WaitReceipt: ...

    async def get(self, wait_id: str) -> Optional[WaitRecord]: ...

    async def load_snapshot(self, wait_id: str) -> Optional[str]: ...

    async def load_definition(self, wait_id: str) -> Optional[str]: ...

    async def claim_event(self, wait_id: str, event: WaitEvent, *, lease_until: Any) -> WaitClaimOutcome: ...

    async def complete(self, wait_id: str, claim: WaitClaim, *, resolution_kind: str) -> WaitRecord: ...

    async def fail(
        self, wait_id: str, claim: WaitClaim, *, error: str, failure_kind: str = "resume_failed"
    ) -> WaitRecord: ...


    async def due(self, now: Any) -> list: ...

    async def stalled(self, now: Any) -> list: ...

    async def cancel(self, wait_id: str, *, reason: str) -> WaitRecord: ...

    async def health(self) -> WaitHealth: ...


class InMemoryWaitCoordinator:
    """Deterministic reference implementation (W2.2): dict store + asyncio lock + INJECTED
    clock. It never fires anything itself — no task, thread, sleep, timer, or poll; timeouts
    are delivered explicitly by the caller/tests (`due(now)` merely reports)."""

    adapter_id = "in_memory"

    def __init__(self, *, clock: Any, shared_state: Optional[Dict[str, Dict]] = None) -> None:
        import asyncio

        if clock is None or not callable(clock):
            raise ValueError("InMemoryWaitCoordinator requires an injected clock callable")
        self._clock = clock
        self._lock = asyncio.Lock()
        # W2R.3: `shared_state` lets tests reconnect a SECOND coordinator over the same
        # backing store — the restart/reconnect story a durable adapter must support.
        state = shared_state if shared_state is not None else {}
        self._records: Dict[str, WaitRecord] = state.setdefault("records", {})
        self._snapshots: Dict[str, str] = state.setdefault("snapshots", {})
        self._definitions: Dict[str, str] = state.setdefault("definitions", {})
        self._receipts: Dict[str, WaitReceipt] = state.setdefault("receipts", {})
        self._accepted_events: Dict[str, str] = state.setdefault("accepted_events", {})
        self._leases: Dict[str, Any] = state.setdefault("leases", {})
        self._claims: Dict[str, WaitClaim] = state.setdefault("claims", {})
        self._state = state

    async def register(
        self, record: WaitRecord, snapshot_json: str, definition_json: str
    ) -> WaitReceipt:
        async with self._lock:
            existing = self._records.get(record.wait_id)
            if existing is not None:
                if (
                    existing == record
                    and self._snapshots.get(record.wait_id) == snapshot_json
                    and self._definitions.get(record.wait_id) == definition_json
                ):
                    # idempotent crash/retry re-register — DEFENSIVE result (W2C.2): the
                    # caller can never alias the stored receipt.
                    return self._receipts[record.wait_id].model_copy(deep=True)
                raise ValueError(
                    f"wait {record.wait_id!r} is already registered with DIFFERENT content — "
                    "duplicate registrations must be identical"
                )
            receipt = WaitReceipt(
                registration_id=f"reg-{record.wait_id}",
                wait_id=record.wait_id,
                wait_version=record.version,
                accepted_deadline=record.deadline_at,
                adapter_id=self.adapter_id,
            )
            # W2B.3: defensive INPUT copies — a caller mutating its record/receipt objects
            # after registration cannot alias into the store.
            self._records[record.wait_id] = record.model_copy(deep=True)
            self._snapshots[record.wait_id] = snapshot_json
            self._definitions[record.wait_id] = definition_json
            self._receipts[record.wait_id] = receipt.model_copy(deep=True)
            return receipt.model_copy(deep=True)

    async def get(self, wait_id: str) -> Optional[WaitRecord]:
        async with self._lock:
            record = self._records.get(wait_id)
            # defensive copy: callers cannot mutate stored state outside future claim ops
            return record.model_copy(deep=True) if record is not None else None

    async def load_snapshot(self, wait_id: str) -> Optional[str]:
        async with self._lock:
            return self._snapshots.get(wait_id)

    async def load_definition(self, wait_id: str) -> Optional[str]:
        async with self._lock:
            return self._definitions.get(wait_id)

    async def claim_event(self, wait_id: str, event: WaitEvent, *, lease_until: Any) -> WaitClaimOutcome:
        async with self._lock:
            record = self._records.get(wait_id)
            if record is None:
                raise KeyError(f"unknown wait id {wait_id!r}")
            accepted = self._accepted_events.get(wait_id)
            if record.status in ("completed", "failed", "cancelled"):
                kind = "duplicate" if accepted == event.event_id else "terminal"
                return WaitClaimOutcome(kind=kind, record=record.model_copy(deep=True))
            if record.status == "claimed":
                lease = self._leases.get(wait_id)
                now = self._clock()
                if accepted == event.event_id and (lease is None or lease > now):
                    return WaitClaimOutcome(kind="duplicate", record=record.model_copy(deep=True))
                if lease is not None and lease > now:
                    return WaitClaimOutcome(
                        kind="already_processing", record=record.model_copy(deep=True)
                    )
                # W3R.2: accepted-event identity is FROZEN. An expired lease permits
                # crash recovery ONLY for the same accepted event; a different event
                # (e.g. a timeout racing a slow accepted signal) is rejected and never
                # overwrites the acceptance.
                if accepted is not None and accepted != event.event_id:
                    return WaitClaimOutcome(kind="not_accepted", record=record.model_copy(deep=True))
                # lease expired, same accepted event: reclaim (crash recovery) — bounded below
            if record.resume_attempts >= record.policy.max_resume_attempts:
                failed = record.model_copy(
                    update={
                        "status": "failed",
                        "version": record.version + 1,
                        "failure_kind": "attempts_exhausted",
                        "failure_detail": (
                            f"bounded recovery exhausted after {record.resume_attempts} "
                            f"of {record.policy.max_resume_attempts} attempts"
                        ),
                    }
                )
                self._records[wait_id] = failed
                return WaitClaimOutcome(kind="attempts_exhausted", record=failed.model_copy(deep=True))
            claimed = record.model_copy(
                update={
                    "status": "claimed",
                    "version": record.version + 1,
                    "resume_attempts": record.resume_attempts + 1,
                }
            )
            self._records[wait_id] = claimed
            self._accepted_events[wait_id] = event.event_id
            self._leases[wait_id] = lease_until
            claim = WaitClaim(
                wait_id=wait_id,
                wait_version=claimed.version,
                event_id=event.event_id,
                claimed_at=self._clock(),
                lease_expires_at=lease_until,
            )
            self._claims[wait_id] = claim.model_copy(deep=True)
            return WaitClaimOutcome(
                kind="claimed", record=claimed.model_copy(deep=True), claim=claim.model_copy(deep=True)
            )

    async def complete(self, wait_id: str, claim: WaitClaim, *, resolution_kind: str) -> WaitRecord:
        return await self._terminalize(wait_id, claim, status="completed", resolution_kind=resolution_kind)

    async def fail(
        self, wait_id: str, claim: WaitClaim, *, error: str, failure_kind: str = "resume_failed"
    ) -> WaitRecord:
        return await self._terminalize(
            wait_id,
            claim,
            status="failed",
            resolution_kind=None,
            failure_kind=failure_kind,
            failure_detail=str(error)[:500] if error else None,
        )

    async def _terminalize(
        self,
        wait_id: str,
        claim: WaitClaim,
        *,
        status: str,
        resolution_kind: Optional[str],
        failure_kind: Optional[str] = None,
        failure_detail: Optional[str] = None,
    ) -> WaitRecord:
        # F11: model_copy(update=...) skips pydantic validation — enforce the closed
        # vocabulary HERE so no adapter/subclass can persist an unloadable record.
        if failure_kind is not None and failure_kind not in FAILURE_KINDS:
            raise ValueError(
                f"failure_kind {failure_kind!r} is not in the closed vocabulary {FAILURE_KINDS}"
            )
        if failure_detail is not None:
            failure_detail = str(failure_detail)[:500]
        async with self._lock:
            record = self._records.get(wait_id)
            if record is None:
                raise KeyError(f"unknown wait id {wait_id!r}")
            stored_claim = self._claims.get(wait_id)
            if (
                record.status != "claimed"
                or stored_claim is None
                or stored_claim.event_id != claim.event_id
                or stored_claim.wait_version != claim.wait_version
            ):
                raise ValueError(
                    f"stale claimant: claim token does not own wait {wait_id!r} (CAS "
                    "mismatch) — a terminalized or reclaimed wait is never overwritten"
                )
            terminal = record.model_copy(
                update={
                    "status": status,
                    "version": record.version + 1,
                    "resolution_kind": resolution_kind,
                    "failure_kind": failure_kind,
                    "failure_detail": failure_detail,
                }
            )
            self._records[wait_id] = terminal
            self._leases.pop(wait_id, None)
            return terminal.model_copy(deep=True)

    async def due(self, now: Any) -> list:
        async with self._lock:
            return [
                record.model_copy(deep=True)
                for record in self._records.values()
                if record.status == "pending" and record.deadline_at <= now
            ]

    async def stalled(self, now: Any) -> list:
        """F9/R2: claimed waits whose lease expired — the ONLY records frozen acceptance
        makes unrecoverable by any other event. Products redeliver the accepted event
        (their outbox knows it) or cancel(); the engine still never self-fires."""

        async with self._lock:
            return [
                record.model_copy(deep=True)
                for wait_id, record in self._records.items()
                if record.status == "claimed"
                and (lease := self._leases.get(wait_id)) is not None
                and lease <= now
            ]

    async def cancel(self, wait_id: str, *, reason: str) -> WaitRecord:
        """F9/R2: product-driven terminal escape hatch. Legal ONLY for a pending wait or
        a claimed wait whose lease has expired — an ACTIVE claimant owns its wait, and a
        terminal wait is already settled (idempotent report)."""

        async with self._lock:
            record = self._records.get(wait_id)
            if record is None:
                raise KeyError(f"unknown wait id {wait_id!r}")
            if record.status in ("completed", "failed", "cancelled"):
                return record.model_copy(deep=True)  # idempotent terminal report
            if record.status == "claimed":
                lease = self._leases.get(wait_id)
                if lease is not None and lease > self._clock():
                    raise ValueError(
                        f"wait {wait_id!r} has an ACTIVE claimant (lease live) — cancel is "
                        "an escape hatch for stalled/pending waits, never a preemption"
                    )
            cancelled = record.model_copy(
                update={
                    "status": "cancelled",
                    "version": record.version + 1,
                    "failure_kind": "cancelled",
                    "failure_detail": str(reason)[:500] if reason else None,
                }
            )
            self._records[wait_id] = cancelled
            self._leases.pop(wait_id, None)
            return cancelled.model_copy(deep=True)

    async def health(self) -> WaitHealth:
        async with self._lock:  # W2C.2: consistent read under the coordinator lock
            return self._health_locked()

    def _health_locked(self) -> WaitHealth:
        # v0.11.5: integrity-aware enumeration (reference behavior for the adapter contract):
        # entries that cannot be validated as the CURRENT WaitRecord contract are excluded
        # from every status count and reported once as integrity_errors. The dict itself is
        # the backend; if it were unreadable this method would raise, never report zeros.
        valid: Dict[str, WaitRecord] = {}
        integrity_errors = 0
        for wait_id, entry in self._records.items():
            if isinstance(entry, WaitRecord):
                valid[wait_id] = entry
                continue
            try:
                valid[wait_id] = WaitRecord.model_validate(entry)
            except Exception:
                integrity_errors += 1
        pending = [r for r in valid.values() if r.status == "pending"]
        claimed = [r for r in valid.values() if r.status == "claimed"]
        failed = [r for r in valid.values() if r.status == "failed"]
        now = self._clock()
        overdue = [r for r in pending if r.deadline_at <= now]
        stalled = [
            wait_id
            for wait_id, record in valid.items()
            if record.status == "claimed"
            and (lease := self._leases.get(wait_id)) is not None
            and lease <= now
        ]
        oldest = min((r.deadline_at for r in pending), default=None)
        return WaitHealth(
            pending=len(pending),
            claimed=len(claimed),
            overdue=len(overdue),
            stalled=len(stalled),
            failed=len(failed),
            integrity_errors=integrity_errors,
            oldest_pending_deadline=oldest,
        )
