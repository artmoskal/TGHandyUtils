"""State-to-result projection for workflow runs."""

from __future__ import annotations

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
        error = final_state.get("error")
        if error is None and status in ("partial", "failed"):
            reasons = [record.error for record in node_results if record.error]
            error = "; ".join(dict.fromkeys(reasons)) or None
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
