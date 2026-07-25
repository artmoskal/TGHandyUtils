"""Durable-wait lifecycle ownership (W2A): ONE engine-owned collaborator behind the
executor. It owns the coordinator handle, the injected wait clock, deterministic wait
identity, receipt validation, and (from W3) claim/terminal state — the executor only
detects suspension, builds the machine snapshot, and records the returned typed outcome.

Hard boundaries: no product/provider/viewer imports, no executor import (the W3 resume
path is a NARROW injected port — see ``ClaimedResumePort``), no background task, thread,
timer, or poll. This is not a second runtime; it never executes a machine itself.
"""

from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from ai_workflow_engine.wait_contract import WaitDeliveryOutcome, WaitHandle
from ai_workflow_engine.waits import (
    DurableWaitPolicy,
    WaitReceipt,
    WaitRecord,
    WaitRegistrationAbortOutcome,
    WaitRegistrationSettlementError,
)

__all__ = [
    "ClaimedResumePort",
    "DurableWaitRuntime",
    "REGISTRATION_SETTLEMENT_TIMEOUT_S",
    "WAIT_TIMEOUT_MARKER",
    "WaitDeliveryOutcome",
    "WaitRegistrationOutcome",
    "WaitRegistrationRequest",
]

# v0.11.6 (C2): the PRIVATE engine-owned settlement bound for compensating a possibly
# committed registration after observed caller cancellation. A fixed floor, deliberately
# INDEPENDENT of the run's execution window — cancellation often arrives exactly when
# that window is exhausted, and settlement must still get a nonzero opportunity. This is
# settlement, not more workflow work, and it is not a user-facing tuning knob.
REGISTRATION_SETTLEMENT_TIMEOUT_S = 2.0

# A delivery may resume into another durable suspension. The mutable list is deliberately
# task-local by ContextVar identity but shared with child tasks copied from that context, so
# register_suspension can report that it reached and settled the next machine boundary.
_NESTED_REGISTRATION_SETTLED: ContextVar[Optional[list[str]]] = ContextVar(
    "durable_wait_nested_registration_settled", default=None
)


def _observe_task_result(task: "asyncio.Task[Any]") -> None:
    """Retrieve an abandoned settlement task's outcome so it never dies unobserved."""

    if not task.cancelled():
        task.exception()

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
    correlation_id: Optional[str] = None  # related-run id, immutable once registered

    @field_validator("correlation_id")
    @classmethod
    def _non_blank_correlation(cls, value):
        from ai_workflow_engine.correlation import validate_optional_correlation

        return validate_optional_correlation(value)

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

    async def settle_unexposed_handle(
        self,
        handle: WaitHandle,
        *,
        reason: str,
    ) -> None:
        """Boundedly settle one handle that the enclosing result will not expose.

        This uses registration compensation rather than operator cancellation. The adapter
        therefore refuses settlement if an exact retry joined the registration and may
        already have exposed the same handle.
        """

        validated = WaitHandle.model_validate(handle.model_dump())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REGISTRATION_SETTLEMENT_TIMEOUT_S
        interrupted_by: asyncio.CancelledError | None = None
        settle = asyncio.ensure_future(
            self._abort_unexposed_handle(validated, reason=str(reason)[:500])
        )
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                settle.cancel()
                settle.add_done_callback(_observe_task_result)
                raise WaitRegistrationSettlementError(
                    f"wait {validated.wait_id!r}: unexposed-handle settlement did not "
                    f"finish within {REGISTRATION_SETTLEMENT_TIMEOUT_S}s"
                ) from interrupted_by
            try:
                await asyncio.wait_for(asyncio.shield(settle), timeout=remaining)
            except asyncio.CancelledError as cancellation:
                if interrupted_by is None:
                    interrupted_by = cancellation
                if settle.done() and not settle.cancelled() and settle.exception() is None:
                    raise interrupted_by
                continue
            except asyncio.TimeoutError:
                continue
            except WaitRegistrationSettlementError:
                raise
            except Exception as settlement_error:
                raise WaitRegistrationSettlementError(
                    f"wait {validated.wait_id!r}: unexposed-handle settlement failed: "
                    f"{settlement_error}"
                ) from (interrupted_by or settlement_error)
            else:
                if interrupted_by is not None:
                    raise interrupted_by
                return

    async def _abort_unexposed_handle(
        self,
        handle: WaitHandle,
        *,
        reason: str,
    ) -> None:
        record = await self.coordinator.get(handle.wait_id)
        if record is None:
            return
        receipt = await self.coordinator.load_receipt(handle.wait_id)
        if (
            receipt is None
            or receipt.wait_id != handle.wait_id
            or receipt.registration_id != handle.registration_id
        ):
            raise WaitRegistrationSettlementError(
                f"wait {handle.wait_id!r}: stored registration identity does not match "
                "the unexposed handle"
            )
        raw = await self.coordinator.abort_registration(
            handle.wait_id,
            expected_registration_id=handle.registration_id,
            expected_registration_attempt_id=record.registration_attempt_id,
            expected_definition_digest=record.definition_digest,
            reason=reason,
        )
        outcome = (
            raw
            if isinstance(raw, WaitRegistrationAbortOutcome)
            else WaitRegistrationAbortOutcome.model_validate(raw)
        )
        if outcome.kind in ("absent", "cancelled"):
            return
        if (
            outcome.kind in ("not_creator", "already_terminal")
            and outcome.record is not None
            and outcome.record.status == "cancelled"
        ):
            return
        status = outcome.record.status if outcome.record is not None else "unknown"
        raise WaitRegistrationSettlementError(
            f"wait {handle.wait_id!r}: unexposed-handle settlement returned "
            f"{outcome.kind!r} with stored status {status!r}"
        )

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
        handle: WaitHandle,
        event: Any,
        *,
        current_digest: Optional[str],
        lease_seconds: float = 300.0,
    ) -> WaitDeliveryOutcome:
        """W3.2/W3.3: the ONE delivery path — atomic claim, digest check, snapshot load,
        private resume through the injected port, then terminalize. At-least-once with
        idempotent effects; a terminalized claim is never executed again.

        v0.11.6 (C1): delivery is HANDLE-BOUND — the coordinator verifies the handle's
        registration identity against its stored receipt before any state changes; a bare
        deterministic wait id cannot resume durable work."""

        from ai_workflow_engine.waits import WaitEvent, _stored_model_data

        # Pydantic does not revalidate an already-constructed model by default. Rebuild from
        # the complete raw representation so model_copy(update=...) forgeries and hidden
        # extras fail before the coordinator can claim or mutate anything.
        event = WaitEvent.model_validate(_stored_model_data(event))
        if self._resume_port is None:
            raise RuntimeError(
                "deliver_wait_event requires a composed resume port — build the engine via "
                "WorkflowEngineBuilder.with_wait_coordinator(...)"
            )
        wait_id = handle.wait_id
        lease_until = self.now() + timedelta(seconds=lease_seconds)
        outcome = await self.coordinator.claim_event(
            wait_id, event, registration_id=handle.registration_id, lease_until=lease_until
        )
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
        nested_settlements: list[str] = []
        nested_token = _NESTED_REGISTRATION_SETTLED.set(nested_settlements)
        try:
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
            except asyncio.CancelledError as cancellation:
                if nested_settlements:
                    await self._fail_after_nested_registration(
                        wait_id, claim, cancellation
                    )
                raise
        finally:
            _NESTED_REGISTRATION_SETTLED.reset(nested_token)
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
        registration_attempt_id = "attempt-" + uuid.uuid4().hex
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
        # v0.11.6 (C2): every exit shape of the exposure window is reconciled HERE — the
        # one registration owner for fresh AND resumed suspensions. Observed caller
        # cancellation settles in a shielded bounded task and re-raises the ORIGINAL
        # CancelledError; an ordinary registration failure folds only after compensation
        # proves the committed state terminal/absent; settlement failures are loud and
        # chained, never translated into a clean cancellation/failure.
        try:
            return await self._register_suspension_inner(
                request, wait_id, registration_attempt_id
            )
        except asyncio.CancelledError as cancellation:
            await self._settle_registration(
                request,
                wait_id,
                registration_attempt_id,
                cancellation,
                reason="caller cancelled before the wait handle was exposed",
            )
            nested_settlements = _NESTED_REGISTRATION_SETTLED.get()
            if nested_settlements is not None:
                nested_settlements.append(wait_id)
            raise
        except WaitRegistrationSettlementError:
            raise
        except Exception as registration_error:
            await self._settle_registration(
                request,
                wait_id,
                registration_attempt_id,
                registration_error,
                reason=f"registration failed before exposure: {registration_error}",
            )
            raise

    async def _register_suspension_inner(
        self,
        request: WaitRegistrationRequest,
        wait_id: str,
        registration_attempt_id: str,
    ) -> WaitRegistrationOutcome:
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
                    ("correlation_id", existing.correlation_id, request.correlation_id),
                )
                if stored != current
            ]
            if mismatches:
                raise RuntimeError(
                    f"wait {wait_id!r} exists with DIFFERENT {', '.join(mismatches)} — "
                    "changed machine state is rejected, never silently reused"
                )
            attempted_record = WaitRecord.model_validate(
                {
                    **existing.model_dump(),
                    "registration_attempt_id": registration_attempt_id,
                }
            )
            # C2R: the adapter sees every retry attempt atomically. This closes the race
            # where the creator's compensation could otherwise cancel a handle a concurrent
            # exact retry had already exposed.
            raw_receipt = await self.coordinator.register(
                attempted_record, request.snapshot_json, request.definition_json
            )
            stored_receipt = (
                raw_receipt
                if isinstance(raw_receipt, WaitReceipt)
                else WaitReceipt.model_validate(raw_receipt)
            )
            if stored_receipt.wait_id != wait_id:
                raise RuntimeError(
                    f"wait {wait_id!r} reuse returned a mis-keyed receipt — adapter "
                    "integrity failure"
                )
            return WaitRegistrationOutcome(
                handle=WaitHandle(
                    wait_id=existing.wait_id,
                    run_id=existing.run_id,
                    workflow_id=existing.workflow_id,
                    suspended_node=existing.suspended_node,
                    deadline_at=existing.deadline_at,
                    registration_id=stored_receipt.registration_id,
                    status=existing.status,
                ),
                reused=True,
                deadline_at=existing.deadline_at,
                adapter_id=stored_receipt.adapter_id,
                registration_id=stored_receipt.registration_id,
            )
        now = self.now()
        record = WaitRecord(
            record_schema_version="wait-v2",
            wait_id=wait_id,
            run_id=request.run_id,
            workflow_id=request.workflow_id,
            suspended_node=request.suspended_node,
            policy=request.policy,
            definition_digest=request.definition_digest,
            created_at=now,
            deadline_at=now + timedelta(seconds=request.policy.timeout_s),
            registration_attempt_id=registration_attempt_id,
            origin_segment_index=request.origin_segment_index,
            correlation_id=request.correlation_id,
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
                registration_id=receipt.registration_id,
                status="pending",
            ),
            reused=False,
            deadline_at=record.deadline_at,
            adapter_id=receipt.adapter_id,
            registration_id=receipt.registration_id,
        )

    async def _compensate_committed_registration(
        self,
        request: WaitRegistrationRequest,
        wait_id: str,
        registration_attempt_id: str,
        *,
        reason: str,
    ) -> None:
        """v0.11.6 (C2): prove the registration THIS attempt may have committed is
        terminal or absent.

        A stored registration that is not byte-identical to the attempted one was NOT
        committed by this attempt — it is left untouched and the original failure
        propagates (changed-retry truth). Anything live or contested that cannot be
        settled raises ``WaitRegistrationSettlementError`` — a caller-visible clean
        failure must never hide an executable continuation."""

        stored = await self.coordinator.get(wait_id)
        if stored is None:
            return  # absent — nothing committed
        identical = (
            stored.run_id == request.run_id
            and stored.workflow_id == request.workflow_id
            and stored.suspended_node == request.suspended_node
            and stored.policy == request.policy
            and stored.definition_digest == request.definition_digest
            and stored.origin_segment_index == request.origin_segment_index
            and stored.correlation_id == request.correlation_id
            and await self.coordinator.load_snapshot(wait_id) == request.snapshot_json
            and await self.coordinator.load_definition(wait_id) == request.definition_json
        )
        if not identical:
            return  # a DIFFERENT registration occupies this id — not ours to settle
        receipt = await self.coordinator.load_receipt(wait_id)
        if receipt is None or receipt.wait_id != wait_id:
            raise WaitRegistrationSettlementError(
                f"wait {wait_id!r}: committed registration has "
                f"{'no stored receipt' if receipt is None else 'a mis-keyed receipt'} — "
                "settlement cannot be proven; the registration may still be executable"
            )
        raw = await self.coordinator.abort_registration(
            wait_id,
            expected_registration_id=receipt.registration_id,
            expected_registration_attempt_id=registration_attempt_id,
            expected_definition_digest=request.definition_digest,
            reason=reason,
        )
        outcome = (
            raw
            if isinstance(raw, WaitRegistrationAbortOutcome)
            else WaitRegistrationAbortOutcome.model_validate(raw)
        )
        if outcome.kind in ("absent", "cancelled"):
            return
        if (
            outcome.kind == "not_creator"
            and outcome.record is not None
            and outcome.record.status == "cancelled"
        ):
            return  # another compensation already made the registration inert
        if (
            outcome.kind == "already_terminal"
            and outcome.record is not None
            and outcome.record.status == "cancelled"
        ):
            return  # a previous compensation settled it — idempotent repeat
        status = outcome.record.status if outcome.record is not None else "unknown"
        raise WaitRegistrationSettlementError(
            f"wait {wait_id!r} could not be settled ({reason}): abort_registration "
            f"returned {outcome.kind!r} with stored status {status!r} — execution truth "
            "is preserved and this caller must NOT be reported cleanly cancelled/failed"
        )

    async def _settle_registration(
        self,
        request: WaitRegistrationRequest,
        wait_id: str,
        registration_attempt_id: str,
        trigger: BaseException,
        *,
        reason: str,
    ) -> None:
        """Bounded, shielded settlement after cancellation or registration failure.

        One task owns cleanup for every exit shape. Cancellation arriving while ordinary
        failure cleanup is running cannot abandon that task; after successful settlement
        the exact cancellation propagates. Cleanup failures are always typed and explicitly
        chained from the event that made settlement necessary."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + REGISTRATION_SETTLEMENT_TIMEOUT_S
        initial_cancellation = (
            trigger if isinstance(trigger, asyncio.CancelledError) else None
        )
        interrupted_by = initial_cancellation
        settle = asyncio.ensure_future(
            self._compensate_committed_registration(
                request,
                wait_id,
                registration_attempt_id,
                reason=reason,
            )
        )
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                settle.cancel()
                settle.add_done_callback(_observe_task_result)
                raise WaitRegistrationSettlementError(
                    f"wait {wait_id!r}: registration settlement did not finish within "
                    f"{REGISTRATION_SETTLEMENT_TIMEOUT_S}s after {reason} — the registration "
                    "may still be live; this run did NOT settle cleanly"
                ) from (interrupted_by or trigger)
            try:
                await asyncio.wait_for(asyncio.shield(settle), timeout=remaining)
            except asyncio.CancelledError as cancellation:
                # repeated caller cancellation: the shield keeps settlement alive and we
                # keep waiting inside the SAME fixed budget — never abandon, never exceed
                if interrupted_by is None:
                    interrupted_by = cancellation
                if settle.done() and not settle.cancelled() and settle.exception() is None:
                    if initial_cancellation is None:
                        raise interrupted_by
                    return
                continue
            except asyncio.TimeoutError:
                continue  # the loop head converts an exhausted budget into the loud raise
            except WaitRegistrationSettlementError as settlement_error:
                raise WaitRegistrationSettlementError(str(settlement_error)) from (
                    interrupted_by or trigger
                )
            except Exception as cleanup_error:
                raise WaitRegistrationSettlementError(
                    f"wait {wait_id!r}: registration settlement raised after {reason}: "
                    f"{cleanup_error}"
                ) from (interrupted_by or trigger)
            else:
                if interrupted_by is not None and initial_cancellation is None:
                    raise interrupted_by
                return

    async def _fail_after_nested_registration(
        self,
        wait_id: str,
        claim: Any,
        cancellation: asyncio.CancelledError,
    ) -> None:
        """Fail the accepted event when cancellation prevents the next handle's exposure.

        The nested registration has already been compensated and is inert. Reporting the
        outer wait completed would claim successful resumability even though no caller
        received the next handle, so the outer lifecycle ends as a loud resume failure.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + REGISTRATION_SETTLEMENT_TIMEOUT_S
        terminalize = asyncio.ensure_future(
            self.coordinator.fail(
                wait_id,
                claim,
                error="cancelled while registering the next durable wait; run is not resumable",
                failure_kind="resume_failed",
            )
        )
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                terminalize.cancel()
                terminalize.add_done_callback(_observe_task_result)
                raise WaitRegistrationSettlementError(
                    f"wait {wait_id!r}: the resumed machine's nested registration was "
                    "cancelled, but the accepted event could not be failed within "
                    f"{REGISTRATION_SETTLEMENT_TIMEOUT_S}s"
                ) from cancellation
            try:
                await asyncio.wait_for(asyncio.shield(terminalize), timeout=remaining)
            except asyncio.CancelledError:
                if terminalize.done() and not terminalize.cancelled():
                    error = terminalize.exception()
                    if error is None:
                        return
                    raise WaitRegistrationSettlementError(
                        f"wait {wait_id!r}: failing the accepted event after nested "
                        f"registration cancellation failed: {error}"
                    ) from cancellation
                continue
            except asyncio.TimeoutError:
                continue
            except Exception as terminal_error:
                raise WaitRegistrationSettlementError(
                    f"wait {wait_id!r}: failing the accepted event after nested "
                    f"registration cancellation failed: {terminal_error}"
                ) from cancellation
            else:
                return
