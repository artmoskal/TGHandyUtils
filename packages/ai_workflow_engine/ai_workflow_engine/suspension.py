"""Snapshot construction and durable-wait registration for workflow runs."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ai_workflow_engine._runtime_state import CONTEXT, RUNNING_PAYLOAD, current_run_session
from ai_workflow_engine.models import (
    CapabilityContext,
    WorkflowTraceEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.run_session import WorkflowRunSession
from ai_workflow_engine.snapshot import MachineSnapshot, SNAPSHOT_SCHEMA_VERSION
from ai_workflow_engine.wait_contract import WaitHandle
from ai_workflow_engine.workflow import WorkflowDefinition

logger = logging.getLogger(__name__)


class SuspensionCoordinator:
    """Own local/durable snapshot truth and register-before-expose semantics."""

    def __init__(self, runtime: Any, *, wait_runtime: Any = None) -> None:
        self._runtime = runtime
        self._wait_runtime = wait_runtime

    @property
    def wait_runtime(self) -> Any:
        return self._wait_runtime

    def set_wait_runtime(self, wait_runtime: Any) -> None:
        self._wait_runtime = wait_runtime

    async def settle_unexposed_handle(self, handle: WaitHandle, *, reason: str) -> None:
        """Make a registered wait inert when the enclosing result will not expose its handle.

        The durable runtime owns the participant-aware, bounded settlement. A concurrent
        exact retry may already have exposed the same registration; that case must fail
        loudly instead of revoking the retry's valid handle.
        """

        if self._wait_runtime is None:
            raise RuntimeError("cannot settle a durable wait without its runtime")
        validated = WaitHandle.model_validate(handle.model_dump())
        await self._wait_runtime.settle_unexposed_handle(validated, reason=reason)

    @staticmethod
    def suspension_occurrence(final_state: Dict[str, Any], suspended: str) -> int:
        return max(
            0,
            sum(
                1
                for result in final_state.get("node_results", [])
                if result.node_id == suspended and result.status == "requires_user_input"
            )
            - 1,
        )

    def build_snapshot(
        self,
        definition: WorkflowDefinition,
        suspended: str,
        final_state: Dict[str, Any],
        session: Optional[WorkflowRunSession] = None,
    ) -> MachineSnapshot:
        segment = getattr(getattr(session, "bundle", None), "segment", None)
        durable_wait_id: Optional[str] = None
        node = definition.node(suspended) if suspended in {n.id for n in definition.nodes} else None
        if node is not None and (node.wait_policy or {}).get("mode") == "durable":
            from ai_workflow_engine.wait_runtime import DurableWaitRuntime

            run_context_obj = final_state.get("workflow_context")
            run_id_for_wait = getattr(run_context_obj, "workflow_id", None)
            if run_id_for_wait:
                durable_wait_id = DurableWaitRuntime.wait_id_for(
                    str(run_id_for_wait),
                    suspended,
                    self.suspension_occurrence(final_state, suspended),
                )
        node_results: List[Any] = list(final_state.get("node_results", []))
        plan = final_state.get("plan_artifact")
        if plan is not None and hasattr(plan, "model_dump"):
            plan = plan.model_dump()
        usage = final_state.get("usage_summary") or WorkflowUsageSummary()
        state_context = final_state.get(CONTEXT)
        goal = final_state.get("workflow_goal") or getattr(state_context, "goal", None)
        snapshot_run_context = final_state.get("workflow_context") or getattr(
            state_context,
            "run_context",
            None,
        )
        if goal is None or snapshot_run_context is None:
            raise RuntimeError(
                "suspension without run identity: neither state nor context carries "
                "workflow_goal/run_context — a v0.11 snapshot requires the capturing run's identity"
            )
        goal_id = getattr(goal, "goal_id", None) or (
            goal.get("goal_id") if isinstance(goal, dict) else None
        )
        goal_workflow_type = getattr(goal, "workflow_type", None) or (
            goal.get("workflow_type") if isinstance(goal, dict) else None
        )
        context_goal_id = getattr(snapshot_run_context, "goal_id", None)
        context_workflow_type = getattr(snapshot_run_context, "workflow_type", None)
        if hasattr(snapshot_run_context, "model_copy") and (
            context_goal_id != goal_id or context_workflow_type != goal_workflow_type
        ):
            snapshot_run_context = snapshot_run_context.model_copy(
                update={"goal_id": goal_id, "workflow_type": goal_workflow_type}
            )
        return MachineSnapshot(
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            workflow_id=definition.workflow_id,
            suspended_node=suspended,
            payload=final_state.get(RUNNING_PAYLOAD),
            node_outputs=dict(final_state.get("node_outputs", {})),
            node_inputs=dict(final_state.get("node_inputs", {})),
            node_status=dict(final_state.get("node_status", {})),
            routes=dict(final_state.get("routes", {})),
            branch_decisions=dict(final_state.get("branch_decisions", {})),
            eval_counters={
                key: dict(value) for key, value in final_state.get("eval_counters", {}).items()
            },
            transition_counts=dict(final_state.get("transition_counts", {})),
            attempts=dict(final_state.get("attempts", {})),
            node_results=[result.model_dump() for result in node_results],
            artifacts=[
                artifact.model_dump() if hasattr(artifact, "model_dump") else artifact
                for artifact in final_state.get("artifacts", [])
            ],
            plan_artifact=plan,
            usage=usage.model_dump() if hasattr(usage, "model_dump") else {},
            fallback_reason=final_state.get("fallback_reason"),
            goal=goal,
            run_context=snapshot_run_context,
            segment_index=getattr(segment, "segment_index", None),
            active_elapsed_s=(
                active_session.active_elapsed_s()
                if (active_session := current_run_session()) is not None
                else 0.0
            ),
            run_timeout_s=(
                state_context.limits.timeout_s
                if state_context is not None and state_context.limits is not None
                else None
            ),
            durable_wait_id=durable_wait_id,
        )

    async def register_or_fold(
        self,
        definition: WorkflowDefinition,
        final_state: Dict[str, Any],
        context: CapabilityContext,
        session: Optional[WorkflowRunSession] = None,
    ) -> tuple[Optional[WaitHandle], Dict[str, Any]]:
        try:
            return (
                await self._register_durable_wait(definition, final_state, context, session),
                final_state,
            )
        except Exception as registration_error:
            # v0.11.6 (C2): a settlement failure means an unexposed registration may
            # still be executable — folding it into a clean failed result would be the
            # exact lie this contract forbids. Propagate loudly instead.
            from ai_workflow_engine.waits import WaitRegistrationSettlementError

            if isinstance(registration_error, WaitRegistrationSettlementError):
                raise
            suspended_record = next(
                (
                    result
                    for result in reversed(final_state.get("node_results", []))
                    if result.status == "requires_user_input"
                ),
                None,
            )
            failed_node = (
                suspended_record.node_id
                if suspended_record is not None
                else definition.workflow_id
            )
            failure_error = f"durable wait registration failed: {registration_error}"
            self._runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=definition.workflow_id,
                    decision="wait:registration_failed",
                    error=str(registration_error)[:500],
                    run_id=str(context.run_context.workflow_id),
                )
            )
            self._runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=failed_node,
                    node_status="failed",
                    phase="node:result",
                    error=str(registration_error)[:500],
                    run_id=str(context.run_context.workflow_id),
                    metadata={"failure_kind": "wait_registration_failed"},
                )
            )
            return None, {
                **final_state,
                "status": "failed",
                "error": failure_error,
                "node_status": {
                    **final_state.get("node_status", {}),
                    failed_node: "failed",
                },
                "node_results": [
                    *final_state.get("node_results", []),
                    *(
                        [
                            suspended_record.model_copy(
                                update={
                                    "status": "failed",
                                    "error": failure_error,
                                }
                            )
                        ]
                        if suspended_record is not None
                        else []
                    ),
                ],
            }

    async def _register_durable_wait(
        self,
        definition: WorkflowDefinition,
        final_state: Dict[str, Any],
        context: CapabilityContext,
        session: Optional[WorkflowRunSession] = None,
    ) -> Optional[WaitHandle]:
        if final_state.get("status") != "requires_user_input":
            return None
        node_results = list(final_state.get("node_results", []))
        suspended = next(
            (
                result.node_id
                for result in reversed(node_results)
                if result.status == "requires_user_input"
            ),
            None,
        )
        if suspended is None:
            return None
        node = definition.node(suspended)
        policy_dict = node.wait_policy or {}
        if policy_dict.get("mode") != "durable":
            return None
        if self._wait_runtime is None:
            raise RuntimeError(
                f"durable wait '{suspended}' suspended without a configured WaitCoordinator"
            )
        from ai_workflow_engine.byte_safety import assert_byte_safe
        from ai_workflow_engine.wait_runtime import WaitRegistrationRequest
        from ai_workflow_engine.waits import DurableWaitPolicy

        policy = DurableWaitPolicy.model_validate(policy_dict)
        snapshot = self.build_snapshot(definition, suspended, final_state, session)
        assert_byte_safe(snapshot.model_dump(), mode="persist", path="durable_wait.snapshot")
        run_id = str(context.run_context.workflow_id)
        occurrence = self.suspension_occurrence(final_state, suspended)
        expected_wait_id = self._wait_runtime.wait_id_for(run_id, suspended, occurrence)
        if snapshot.durable_wait_id != expected_wait_id:
            raise RuntimeError(
                "wait identity integrity failure before registration: snapshot sealed to "
                f"{snapshot.durable_wait_id!r} but the deterministic registration identity "
                f"is {expected_wait_id!r}"
            )
        outcome = await self._wait_runtime.register_suspension(
            WaitRegistrationRequest(
                run_id=run_id,
                workflow_id=definition.workflow_id,
                definition_digest=definition.definition_digest(),
                suspended_node=suspended,
                occurrence=occurrence,
                policy=policy,
                snapshot_json=snapshot.model_dump_json(),
                definition_json=definition.model_dump_json(),
                origin_segment_index=snapshot.segment_index,
                correlation_id=context.run_context.correlation_id,
            )
        )
        try:
            self._runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=suspended,
                    decision=(
                        "wait:registration_reused" if outcome.reused else "wait:registered"
                    ),
                    run_id=run_id,
                    metadata={
                        "wait_id": outcome.handle.wait_id,
                        "deadline_at": outcome.deadline_at.isoformat(),
                        **(
                            {
                                "adapter_id": outcome.adapter_id,
                                "registration_id": outcome.registration_id,
                            }
                            if not outcome.reused
                            else {}
                        ),
                    },
                )
            )
        except Exception as trace_error:
            # Registration is already committed and its handle is valid. Auxiliary
            # observation cannot turn that fact into a clean failed result with a hidden,
            # executable continuation.
            logger.warning(
                "durable wait %s registered but registration trace failed: %s",
                outcome.handle.wait_id,
                trace_error,
            )
        return outcome.handle
