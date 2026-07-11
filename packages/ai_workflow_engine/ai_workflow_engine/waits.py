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

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "WaitCoordinator",
    "InMemoryWaitCoordinator",
    "WAIT_STATUSES",
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


class WaitRecord(BaseModel):
    """The engine-owned wait state a coordinator persists (snapshot itself is store-owned)."""

    model_config = ConfigDict(extra="forbid")

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


class WaitHealth(BaseModel):
    """Product-neutral coordinator health surface; ``overdue`` is DERIVED, never stored."""

    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    claimed: int = Field(ge=0)
    overdue: int = Field(ge=0)
    oldest_pending_deadline: Optional[AwareDatetime] = None


# ====================================================================== W2: coordinator


@runtime_checkable
class WaitCoordinator(Protocol):
    """Product-owned durable-wait store/transport seam (W2). The engine calls REGISTER
    before exposing any durable suspension and trusts the attestation like it trusts a
    capability handler; delivery/claim mechanics land in W3. Implementations must persist
    wait state and timeout intent atomically IN THEIR OWN transactional domain (C2/C3)."""

    async def register(self, record: WaitRecord, snapshot_json: str) -> WaitReceipt: ...

    async def get(self, wait_id: str) -> Optional[WaitRecord]: ...

    async def load_snapshot(self, wait_id: str) -> Optional[str]: ...

    async def due(self, now: Any) -> list: ...

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
        self._receipts: Dict[str, WaitReceipt] = state.setdefault("receipts", {})
        self._state = state

    async def register(self, record: WaitRecord, snapshot_json: str) -> WaitReceipt:
        async with self._lock:
            existing = self._records.get(record.wait_id)
            if existing is not None:
                if existing == record and self._snapshots.get(record.wait_id) == snapshot_json:
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

    async def due(self, now: Any) -> list:
        async with self._lock:
            return [
                record.model_copy(deep=True)
                for record in self._records.values()
                if record.status == "pending" and record.deadline_at <= now
            ]

    async def health(self) -> WaitHealth:
        async with self._lock:  # W2C.2: consistent read under the coordinator lock
            return self._health_locked()

    def _health_locked(self) -> WaitHealth:
        pending = [r for r in self._records.values() if r.status == "pending"]
        claimed = [r for r in self._records.values() if r.status == "claimed"]
        now = self._clock()
        overdue = [r for r in pending if r.deadline_at <= now]
        oldest = min((r.deadline_at for r in pending), default=None)
        return WaitHealth(
            pending=len(pending),
            claimed=len(claimed),
            overdue=len(overdue),
            oldest_pending_deadline=oldest,
        )
