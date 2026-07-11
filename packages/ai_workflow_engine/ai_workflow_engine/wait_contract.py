"""Wait PUBLIC contract leaf (W1R.2b): the tiny DTO surface shared by the executor's run
result and the full wait subsystem. Deliberately minimal — the executor (simple tier) may
load THIS; the full ``waits`` module (policies, records, coordinator vocabulary) stays
excluded from no-wait consumers and re-exports these names."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict

__all__ = ["WAIT_STATUSES", "WaitStatus", "WaitHandle"]

# C5: closed processing lifecycle — no "expired"/"overdue" terminal states.
WAIT_STATUSES = ("pending", "claimed", "completed", "failed", "cancelled")
WaitStatus = Literal["pending", "claimed", "completed", "failed", "cancelled"]


class WaitHandle(BaseModel):
    """What a product receives for a REGISTERED wait — never the raw snapshot (C1)."""

    model_config = ConfigDict(extra="forbid")

    wait_id: str
    run_id: str
    workflow_id: str
    suspended_node: str
    deadline_at: AwareDatetime
    status: WaitStatus = "pending"
