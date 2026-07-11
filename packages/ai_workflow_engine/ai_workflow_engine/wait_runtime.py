"""Durable-wait lifecycle ownership (W2A): ONE engine-owned collaborator behind the
executor. It owns the coordinator handle, the injected wait clock, deterministic wait
identity, receipt validation, and (from W3) claim/terminal state — the executor only
detects suspension, builds the machine snapshot, and records the returned typed outcome.

Hard boundaries: no product/provider/viewer imports, no executor import (the W3 resume
path is a NARROW injected port — see ``ClaimedResumePort``), no background task, thread,
timer, or poll. This is not a second runtime; it never executes a machine itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_workflow_engine.wait_contract import WaitHandle
from ai_workflow_engine.waits import DurableWaitPolicy, WaitReceipt, WaitRecord

__all__ = [
    "ClaimedResumePort",
    "DurableWaitRuntime",
    "WaitRegistrationOutcome",
    "WaitRegistrationRequest",
]


@runtime_checkable
class ClaimedResumePort(Protocol):
    """W2A.3: the ONLY door back into machine execution for W3's claimed delivery — an
    injected callable, so this service never imports the executor. The engine facade
    composes it; nothing else can construct a resume path from wait state."""

    async def __call__(self, snapshot_json: str, event: Any) -> Any: ...


class WaitRegistrationRequest(BaseModel):
    """Typed input the executor hands over — everything identity needs, nothing more."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_id: str
    definition_digest: str
    suspended_node: str
    occurrence: int = Field(ge=0)
    policy: DurableWaitPolicy
    snapshot_json: str


class WaitRegistrationOutcome(BaseModel):
    """Typed result the executor records — handle for the public result, trace facts."""

    model_config = ConfigDict(extra="forbid")

    handle: WaitHandle
    reused: bool
    deadline_at: AwareDatetime
    adapter_id: Optional[str] = None
    registration_id: Optional[str] = None


class DurableWaitRuntime:
    """Registration mechanics (W2); claim/terminal lifecycle joins here in W3."""

    def __init__(
        self,
        coordinator: Any,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        resume_port: Optional[ClaimedResumePort] = None,
    ) -> None:
        if coordinator is None:
            raise ValueError("DurableWaitRuntime requires a coordinator")
        self.coordinator = coordinator
        self._clock = clock
        self._resume_port = resume_port  # consumed by W3 delivery; never called in W2

    def now(self) -> datetime:
        return self._clock() if self._clock is not None else datetime.now(timezone.utc)

    @staticmethod
    def wait_id_for(run_id: str, suspended_node: str, occurrence: int) -> str:
        # W2R.3: DETERMINISTIC identity — same logical suspension reuses one registration;
        # a later occurrence of the same node gets a distinct index.
        return f"{run_id}--{suspended_node}--{occurrence}"

    async def register_suspension(self, request: WaitRegistrationRequest) -> WaitRegistrationOutcome:
        wait_id = self.wait_id_for(request.run_id, request.suspended_node, request.occurrence)
        existing = await self.coordinator.get(wait_id)
        if existing is not None:
            # crash-after-register retry: reuse the committed registration untouched
            return WaitRegistrationOutcome(
                handle=WaitHandle(
                    wait_id=existing.wait_id,
                    run_id=existing.run_id,
                    workflow_id=existing.workflow_id,
                    suspended_node=existing.suspended_node,
                    deadline_at=existing.deadline_at,
                    status=existing.status,
                ),
                reused=True,
                deadline_at=existing.deadline_at,
            )
        now = self.now()
        record = WaitRecord(
            wait_id=wait_id,
            run_id=request.run_id,
            workflow_id=request.workflow_id,
            suspended_node=request.suspended_node,
            policy=request.policy,
            created_at=now,
            deadline_at=now + timedelta(seconds=request.policy.timeout_s),
        )
        raw_receipt = await self.coordinator.register(record, request.snapshot_json)
        # W2R.1: the attestation boundary is the CLOSED model — duck objects with matching
        # attributes are not receipts.
        receipt = (
            raw_receipt
            if isinstance(raw_receipt, WaitReceipt)
            else WaitReceipt.model_validate(raw_receipt)
        )
        if (
            receipt.wait_id != record.wait_id
            or receipt.wait_version != record.version
            or receipt.accepted_deadline != record.deadline_at
        ):
            raise RuntimeError(
                f"wait registration attestation mismatch for '{request.suspended_node}': "
                f"{receipt.registration_id!r} does not echo the registered wait"
            )
        return WaitRegistrationOutcome(
            handle=WaitHandle(
                wait_id=record.wait_id,
                run_id=record.run_id,
                workflow_id=record.workflow_id,
                suspended_node=request.suspended_node,
                deadline_at=record.deadline_at,
                status="pending",
            ),
            reused=False,
            deadline_at=record.deadline_at,
            adapter_id=receipt.adapter_id,
            registration_id=receipt.registration_id,
        )
