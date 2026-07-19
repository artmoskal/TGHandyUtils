"""Wait PUBLIC contract leaf (W1R.2b): the tiny DTO surface shared by the executor's run
result and the full wait subsystem. Deliberately minimal — the executor (simple tier) may
load THIS; the full ``waits`` module (policies, records, coordinator vocabulary) stays
excluded from no-wait consumers and re-exports these names."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

__all__ = ["WAIT_STATUSES", "WaitStatus", "WaitDeliveryOutcome", "WaitHandle"]

# C5: closed processing lifecycle — no "expired"/"overdue" terminal states.
WAIT_STATUSES = ("pending", "claimed", "completed", "failed", "cancelled")
WaitStatus = Literal["pending", "claimed", "completed", "failed", "cancelled"]


class WaitHandle(BaseModel):
    """What a product receives for a REGISTERED wait — never the raw snapshot (C1).

    v0.11.6: the handle is the continuation CAPABILITY. ``registration_id`` is the
    adapter's receipt identity for the exact accepted registration incarnation — delivery
    requires the complete handle, and the coordinator refuses a registration id that does
    not match its stored receipt. Persist the whole handle; a bare deterministic wait id
    can no longer resume durable work."""

    model_config = ConfigDict(extra="forbid")

    wait_id: str
    run_id: str
    workflow_id: str
    suspended_node: str
    deadline_at: AwareDatetime
    registration_id: str
    status: WaitStatus = "pending"

    @field_validator("registration_id", "wait_id")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "wait handles require non-blank identity — a blank registration_id or "
                "wait_id cannot name an accepted registration"
            )
        return value


class WaitDeliveryOutcome(BaseModel):
    """Typed result of the public durable-wait delivery door.

    This DTO lives in the small wait contract leaf so API introspection never needs to
    import the durable runtime implementation. ``run_result`` is a WorkflowRunResult for
    executed deliveries, kept as ``Any`` to preserve the leaf dependency direction.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    kind: Literal[
        "executed",
        "duplicate",
        "already_processing",
        "not_accepted",
        "terminal",
        "rejected",
        "attempts_exhausted",
    ]
    wait_id: str
    resolution_kind: Optional[Literal["signal", "timeout"]] = None
    wait_status: str
    detail: str = ""
    run_result: Optional[Any] = None
    terminal_observation: Optional[Literal["recorded", "failed", "skipped"]] = None
    attempt: Optional[int] = None
