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
from typing import Any, Callable, Literal, Optional, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from ai_workflow_engine.wait_contract import WaitHandle
from ai_workflow_engine.waits import DurableWaitPolicy, WaitReceipt, WaitRecord

__all__ = [
    "ClaimedResumePort",
    "DurableWaitRuntime",
    "WAIT_TIMEOUT_MARKER",
    "WaitDeliveryOutcome",
    "WaitRegistrationOutcome",
    "WaitRegistrationRequest",
]

# Internal resume-event marker for timeout deliveries: the suspended node takes its
# DECLARED on_timeout transition instead of re-entering the wait capability.
WAIT_TIMEOUT_MARKER = "__wait_timeout__"


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

    @field_validator("definition_digest", "run_id", "workflow_id", "suspended_node")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("wait identity fields must be non-blank")
        return value
    occurrence: int = Field(ge=0)
    policy: DurableWaitPolicy
    snapshot_json: str
    # W3R.3a: the canonical REGISTERED definition bytes and the suspension's LOGICAL
    # observation position — persisted with the wait so terminal evidence never depends on
    # the snapshot or the currently-registered definition surviving. Logical (index, not a
    # physical directory key): stable across delivery attempts, so chained-wait crash-retry
    # re-registration stays byte-identical (R1/F2).
    definition_json: str
    origin_segment_index: Optional[int] = Field(default=None, ge=0)

    @field_validator("definition_json")
    @classmethod
    def _non_blank_definition(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("definition_json must carry the registered definition bytes")
        return value


class WaitRegistrationOutcome(BaseModel):
    """Typed result the executor records — handle for the public result, trace facts."""

    model_config = ConfigDict(extra="forbid")

    handle: WaitHandle
    reused: bool
    deadline_at: AwareDatetime
    adapter_id: Optional[str] = None
    registration_id: Optional[str] = None


class WaitDeliveryOutcome(BaseModel):
    """W3.2: typed result of `deliver_wait_event` — losers and late events get honest
    terminal reports, never re-execution and never an untyped error."""

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
    run_result: Optional[Any] = None  # WorkflowRunResult when kind == "executed"
    # W3R.3b: when THIS delivery terminalized the wait with no continuation run, the engine
    # says whether the terminal observation segment was actually written — a failed
    # evidence write is typed, never an indistinguishable fully-observed result.
    # None = not applicable (executed / non-transitioning outcome).
    terminal_observation: Optional[Literal["recorded", "failed", "skipped"]] = None
    # R1: the claim ordinal this outcome refers to (record.resume_attempts) — the engine
    # facade keys attempt-directory commit markers and evidence repair on it.
    attempt: Optional[int] = None


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
        # W3R.1: per-delivery unforgeable claim tokens — set ONLY inside deliver() while
        # the claimed resume is in flight. The public resume door validates against this
        # registry, so a product holding raw snapshot bytes cannot re-enter execution.
        self._inflight_claims: dict[str, str] = {}

    def now(self) -> datetime:
        return self._clock() if self._clock is not None else datetime.now(timezone.utc)

    def holds_claim(self, wait_id: str, token: Optional[str]) -> bool:
        """W3R.1: True only while deliver() is executing THIS wait under THIS token."""

        return bool(token) and self._inflight_claims.get(wait_id) == token

    @staticmethod
    def wait_id_for(run_id: str, suspended_node: str, occurrence: int) -> str:
        # W2R.3+W2B.2: DETERMINISTIC and COLLISION-SAFE — the preimage length-prefixes each
        # component, so no delimiter game ("r--a","b") vs ("r","a--b") can collide; the id
        # stays stable for the same logical suspension and distinct for later occurrences.
        import hashlib

        preimage = f"{len(run_id)}:{run_id}|{len(suspended_node)}:{suspended_node}|{occurrence}"
        return "wait-" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()[:32]

    async def deliver(
        self,
        wait_id: str,
        event: Any,
        *,
        current_digest: Optional[str],
        lease_seconds: float = 300.0,
    ) -> WaitDeliveryOutcome:
        """W3.2/W3.3: the ONE delivery path — atomic claim, digest check, snapshot load,
        private resume through the injected port, then terminalize. At-least-once with
        idempotent effects; a terminalized claim is never executed again."""

        from ai_workflow_engine.waits import WaitEvent

        event = event if isinstance(event, WaitEvent) else WaitEvent.model_validate(event)
        if self._resume_port is None:
            raise RuntimeError(
                "deliver_wait_event requires a composed resume port — build the engine via "
                "WorkflowEngineBuilder.with_wait_coordinator(...)"
            )
        lease_until = self.now() + timedelta(seconds=lease_seconds)
        outcome = await self.coordinator.claim_event(wait_id, event, lease_until=lease_until)
        if outcome.kind != "claimed":
            return WaitDeliveryOutcome(
                kind=outcome.kind,
                wait_id=wait_id,
                wait_status=outcome.record.status,
                resolution_kind=outcome.record.resolution_kind,
                detail=f"claim returned {outcome.kind}",
                attempt=outcome.record.resume_attempts,
            )
        record, claim = outcome.record, outcome.claim
        # W3.2: a changed machine is REJECTED, never replayed through a new graph.
        if current_digest is None or record.definition_digest != current_digest:
            failed = await self.coordinator.fail(
                wait_id,
                claim,
                error=(
                    f"registered digest {record.definition_digest!r} does not match the "
                    f"currently registered definition {current_digest!r}"
                ),
                failure_kind="digest_mismatch",
            )
            return WaitDeliveryOutcome(
                kind="rejected",
                wait_id=wait_id,
                wait_status=failed.status,
                attempt=record.resume_attempts,
                detail=(
                    f"registered digest {record.definition_digest!r} does not match the "
                    f"currently registered definition {current_digest!r}"
                ),
            )
        snapshot_json = await self.coordinator.load_snapshot(wait_id)
        if not snapshot_json:
            failed = await self.coordinator.fail(
                wait_id, claim, error="stored snapshot missing", failure_kind="snapshot_missing"
            )
            return WaitDeliveryOutcome(
                kind="rejected", wait_id=wait_id, wait_status=failed.status, attempt=record.resume_attempts,
                detail="stored snapshot missing — adapter integrity failure",
            )
        if event.kind == "timeout":
            resume_event: Any = {WAIT_TIMEOUT_MARKER: True, "event_id": event.event_id}
        else:
            resume_event = event.payload
        # stable wait/event-derived idempotency context (W3.4) rides the resume event
        # envelope so external writes can key retries deterministically. `attempt` (W4.2)
        # is the claim ordinal from the coordinator — a crash-retry reclaim gets a HIGHER
        # attempt, so its observation segment never appends into the crashed attempt's dir.
        # `claim_token` (W3R.1) proves to the resume door that THIS delivery holds the
        # live claim; it exists only for the duration of this call.
        import uuid as _uuid

        claim_token = _uuid.uuid4().hex
        self._inflight_claims[wait_id] = claim_token
        try:
            run_result = await self._resume_port(  # type: ignore[misc]
                snapshot_json,
                {
                    "__wait_delivery__": {
                        "wait_id": wait_id,
                        "event_id": event.event_id,
                        "kind": event.kind,
                        "attempt": record.resume_attempts,
                        "claim_token": claim_token,
                    },
                    "payload": resume_event,
                },
            )
        finally:
            self._inflight_claims.pop(wait_id, None)
        if run_result.status in ("completed", "partial", "requires_user_input"):
            terminal = await self.coordinator.complete(
                wait_id, claim, resolution_kind=event.kind
            )
        else:
            terminal = await self.coordinator.fail(
                wait_id,
                claim,
                error=str(run_result.error or "resumed run failed")[:500],
                failure_kind="resume_failed",
            )
        return WaitDeliveryOutcome(
            kind="executed",
            wait_id=wait_id,
            wait_status=terminal.status,
            resolution_kind=terminal.resolution_kind,
            run_result=run_result,
            attempt=record.resume_attempts,
        )

    async def register_suspension(self, request: WaitRegistrationRequest) -> WaitRegistrationOutcome:
        wait_id = self.wait_id_for(request.run_id, request.suspended_node, request.occurrence)
        # W3R.3a integrity seal: the persisted definition bytes must BE the machine the
        # digest names — recompute and compare before anything is stored.
        from ai_workflow_engine.workflow import WorkflowDefinition

        try:
            recomputed = WorkflowDefinition.model_validate_json(
                request.definition_json
            ).definition_digest()
        except Exception as parse_error:
            raise ValueError(
                f"definition_json for wait registration is not a valid WorkflowDefinition: "
                f"{parse_error}"
            ) from parse_error
        if recomputed != request.definition_digest:
            raise ValueError(
                f"definition_json digests to {recomputed!r} but the registration claims "
                f"{request.definition_digest!r} — the persisted bytes must BE the registered "
                "machine"
            )
        existing = await self.coordinator.get(wait_id)
        if existing is not None:
            # W2B.1/W2B.2: reuse is ONLY the crash-before-handle contract — the stored
            # registration must be PENDING and match the CURRENT request exactly (identity,
            # policy, digest, AND snapshot bytes); anything else is a loud integrity error,
            # never a silent revival or a silent swap of machine state.
            if existing.status != "pending":
                raise RuntimeError(
                    f"wait {wait_id!r} is {existing.status!r} — a claimed/terminal wait can "
                    "never be revived as a new active suspension"
                )
            stored_snapshot = await self.coordinator.load_snapshot(wait_id)
            stored_definition = await self.coordinator.load_definition(wait_id)
            mismatches = [
                name
                for name, stored, current in (
                    ("run_id", existing.run_id, request.run_id),
                    ("workflow_id", existing.workflow_id, request.workflow_id),
                    ("suspended_node", existing.suspended_node, request.suspended_node),
                    ("policy", existing.policy, request.policy),
                    ("definition_digest", existing.definition_digest, request.definition_digest),
                    ("snapshot", stored_snapshot, request.snapshot_json),
                    ("definition", stored_definition, request.definition_json),
                    (
                        "origin_segment_index",
                        existing.origin_segment_index,
                        request.origin_segment_index,
                    ),
                )
                if stored != current
            ]
            if mismatches:
                raise RuntimeError(
                    f"wait {wait_id!r} exists with DIFFERENT {', '.join(mismatches)} — "
                    "changed machine state is rejected, never silently reused"
                )
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
            definition_digest=request.definition_digest,
            created_at=now,
            deadline_at=now + timedelta(seconds=request.policy.timeout_s),
            origin_segment_index=request.origin_segment_index,
        )
        raw_receipt = await self.coordinator.register(
            record, request.snapshot_json, request.definition_json
        )
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
