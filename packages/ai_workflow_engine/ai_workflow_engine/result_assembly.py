"""State-to-result projection for workflow runs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from ai_workflow_engine._runtime_state import RUNNING_PAYLOAD
from ai_workflow_engine.engine.runner import derive_workflow_result_status
from ai_workflow_engine.models import WorkflowTraceEvent, WorkflowUsageSummary
from ai_workflow_engine.run_session import WorkflowRunSession
from ai_workflow_engine.workflow import WorkflowDefinition

logger = logging.getLogger(__name__)


class RunResultAssembler:
    """Build terminal/failed public envelopes without owning execution control flow."""

    def __init__(
        self,
        *,
        runtime: Any,
        result_factory: Callable[..., Any],
        suspension: Any,
    ) -> None:
        self._runtime = runtime
        self._result_factory = result_factory
        self._suspension = suspension

    def envelope(
        self,
        definition: WorkflowDefinition,
        final_state: Dict[str, Any],
        session: Optional[WorkflowRunSession] = None,
        *,
        child_trace: Optional[List[WorkflowTraceEvent]] = None,
    ) -> Any:
        node_results = list(final_state.get("node_results", []))
        status = derive_workflow_result_status(final_state)
        usage = final_state.get("usage_summary")
        if usage is None:
            logger.warning("run produced no usage_summary — reporting an empty one (plumbing bug?)")
            usage = WorkflowUsageSummary()
        latest_by_node = {}
        for record in node_results:
            latest_by_node[record.node_id] = record
        if status in ("partial", "failed"):
            reasons = [
                record.error
                for record in latest_by_node.values()
                if record.status in ("partial", "failed") and record.error
            ]
            if final_state.get("graph_failsafe") is not None and final_state.get("error"):
                reasons.append(final_state["error"])
            error = "; ".join(dict.fromkeys(reasons)) or None
        else:
            error = None
        snapshot = None
        if status == "requires_user_input":
            suspended = next(
                (
                    result.node_id
                    for result in reversed(node_results)
                    if result.status == "requires_user_input"
                ),
                None,
            )
            if suspended is not None:
                snapshot = self._suspension.build_snapshot(
                    definition,
                    suspended,
                    final_state,
                    session,
                )
        return self._result_factory(
            workflow_id=definition.workflow_id,
            status=status,
            output=final_state.get(RUNNING_PAYLOAD),
            error=error,
            fallback_reason=final_state.get("fallback_reason"),
            node_results=node_results,
            artifacts=list(final_state.get("artifacts", [])),
            usage=usage,
            trace=(
                list(session.trace_events)
                if session is not None
                else list(child_trace or [])
            ),
            snapshot=snapshot,
        )

    def failed(self, definition: WorkflowDefinition, error: str) -> Any:
        event = WorkflowTraceEvent(
            node=definition.workflow_id,
            decision="rejected",
            error=error,
        )
        self._runtime.trace_sink.record(event)
        return self._result_factory(
            workflow_id=definition.workflow_id,
            status="failed",
            error=error,
            trace=[event],
        )

    async def project_terminal_status(
        self,
        session: WorkflowRunSession,
        envelope: Any,
        terminal_status: Callable[[Any], Optional[str]],
    ) -> Any:
        """Apply a product projection without hiding a durable continuation."""

        settlement_attempted = False
        try:
            override = terminal_status(envelope)
            if not override:
                return envelope
            valid = {"completed", "partial", "failed", "requires_user_input"}
            if override not in valid:
                raise ValueError(
                    f"terminal_status hook returned invalid status {override!r} "
                    f"(allowed: {sorted(valid)})"
                )
            if (
                override == "requires_user_input"
                and envelope.snapshot is None
                and envelope.wait_handle is None
            ):
                raise ValueError(
                    "terminal_status hook returned 'requires_user_input' but the run has no "
                    "machine snapshot or durable handle — suspension must come from the "
                    "workflow itself"
                )
            updates: Dict[str, Any] = {"status": override}
            if override != "requires_user_input":
                if envelope.wait_handle is not None:
                    settlement_attempted = True
                    await self._suspension.settle_unexposed_handle(
                        envelope.wait_handle,
                        reason=f"terminal_status projected the run as {override}",
                    )
                updates.update({"snapshot": None, "wait_handle": None})
            return envelope.model_copy(update=updates)
        except (Exception, asyncio.CancelledError) as projection_error:
            if envelope.wait_handle is not None and not settlement_attempted:
                try:
                    await self._suspension.settle_unexposed_handle(
                        envelope.wait_handle,
                        reason=(
                            "terminal_status projection failed before the durable handle "
                            "could be exposed"
                        ),
                    )
                except Exception as settlement_error:
                    session.close("failed")
                    raise settlement_error from projection_error
            session.close("failed")
            raise
