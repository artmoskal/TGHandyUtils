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
from typing import Any, Dict, Literal, Optional, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

__all__ = [
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

# C5: closed processing lifecycle — no "expired"/"overdue" terminal states.
WAIT_STATUSES = ("pending", "claimed", "completed", "failed", "cancelled")
RESOLUTION_KINDS = ("signal", "timeout")

WaitStatus = Literal["pending", "claimed", "completed", "failed", "cancelled"]
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
    status: WaitStatus = "pending"
    resolution_kind: Optional[ResolutionKind] = None  # set only when an event WINS (C5)
    created_at: AwareDatetime = Field(default_factory=_utc_now)
    deadline_at: AwareDatetime
    version: int = Field(default=1, ge=1)
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


class WaitHandle(BaseModel):
    """What a product receives for a REGISTERED wait — never the raw snapshot (C1)."""

    model_config = ConfigDict(extra="forbid")

    wait_id: str
    run_id: str
    workflow_id: str
    suspended_node: str
    deadline_at: AwareDatetime
    status: WaitStatus = "pending"


class WaitHealth(BaseModel):
    """Product-neutral coordinator health surface; ``overdue`` is DERIVED, never stored."""

    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    claimed: int = Field(ge=0)
    overdue: int = Field(ge=0)
    oldest_pending_deadline: Optional[AwareDatetime] = None
