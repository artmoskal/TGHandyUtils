"""Coarse child-workflow execution ceiling — the one owner for both child doors.

v0.11.18: a child workflow's configured ``RuntimeLimits.timeout_s`` was never enforced —
``WorkflowExecutor._run_inner`` called ``compiled.ainvoke`` directly, bypassing the runner's
graph fail-safe. Both child doors (``register_workflow_capability`` and a declarative
``.subworkflow`` node) converge at ``_run_inner``, so the ceiling is owned HERE and called once
from that universal boundary — never duplicated in ``builder.py`` or ``nodes/subworkflow.py``,
and never a second timeout scheduler.

The effective child timeout is
``min(child configured timeout, parent invocation remaining, top-level run remaining)``: a child
limit may narrow an ancestor window but can never widen it. The intersection reuses the existing
capability resolver; enforcement reuses the runner's existing graph fail-safe.
"""

from __future__ import annotations

from typing import Any, Awaitable, Dict, Optional

from ai_workflow_engine.engine.runner import (
    GraphFailsafeWindow,
    _GraphFailsafeExpired,
    _invoke_graph_with_failsafe,
)
from ai_workflow_engine.execution_window import (
    ExecutionWindowInputs,
    invocation_window_remaining_s,
    resolve_execution_window,
)
from ai_workflow_engine.models import WorkflowTraceEvent
from ai_workflow_engine.workflow import WorkflowDefinition


class ChildWorkflowWindow:
    """Resolve and enforce one shared window around a complete child-workflow invocation."""

    def __init__(self, trace_sink: Any) -> None:
        self._trace_sink = trace_sink

    def resolve(
        self, definition: WorkflowDefinition, session: Any
    ) -> Optional[GraphFailsafeWindow]:
        """Return the graph fail-safe window bounding the COMPLETE child invocation, or ``None``.

        ``None`` (no bound proposed) leaves the child bounded only by its parent/run window
        exactly as before. Note the run dimension overlaps the top-level graph fail-safe that
        already wraps the enclosing run; keeping it here is defensive and contract-faithful, but
        the dimension this owner UNIQUELY adds is the child's own configured timeout.
        """

        child_limit = definition.limits.timeout_s if definition.limits is not None else None
        run_remaining = (
            session.run_remaining_s()
            if session is not None and hasattr(session, "run_remaining_s")
            else None
        )
        parent_remaining = invocation_window_remaining_s()
        parent_soft = parent_remaining.soft_s if parent_remaining is not None else None
        if child_limit is None and run_remaining is None and parent_soft is None:
            return None
        decision = resolve_execution_window(
            ExecutionWindowInputs(
                capability_timeout_s=child_limit,
                run_remaining_s=run_remaining,
                parent_soft_remaining_s=parent_soft,
            ),
            enforcement="cooperative",
        )
        if not decision.is_bounded or decision.soft_timeout_s is None:
            return None
        return GraphFailsafeWindow(
            work_timeout_s=decision.soft_timeout_s,
            cancellation_grace_s=decision.completion_reserve_s,
        )

    async def run_bounded(
        self,
        definition: WorkflowDefinition,
        invocation: Awaitable[Dict[str, Any]],
        child_state: Dict[str, Any],
        window: GraphFailsafeWindow,
    ) -> Dict[str, Any]:
        """Run the child graph under one shared window, reusing the runner's fail-safe.

        Retries and every node inside the child consume this single window; it is never reset per
        attempt. A clean expiry (cooperative cancellation acknowledged) is honest PARTIAL; a child
        that suppresses cancellation is a loud FAILED containment failure. Engine-owned processes
        are terminated/reaped by the existing per-capability cancellation contract as the cancel
        propagates into the child graph task.
        """

        try:
            return await _invoke_graph_with_failsafe(invocation, window)
        except _GraphFailsafeExpired as exc:
            failsafe_meta = exc.window.metadata(containment_failed=exc.containment_failed)
            status = "failed" if exc.containment_failed else "partial"
            error = (
                f"child workflow '{definition.workflow_id}' exhausted its execution window; "
                f"work was cancelled after {exc.window.work_timeout_s:g}s and given "
                f"{exc.window.cancellation_grace_s:g}s for containment"
            )
            if exc.containment_failed:
                error += "; child graph code did not acknowledge cancellation within the bound"
            if self._trace_sink is not None:
                self._trace_sink.record(
                    WorkflowTraceEvent(
                        node=definition.workflow_id,
                        decision=status,
                        node_status=status,
                        error=error,
                        phase="child:window",
                        severity="error" if exc.containment_failed else "warning",
                        metadata={"graph_failsafe": failsafe_meta},
                    )
                )
            return {
                **child_state,
                "status": status,
                "error": error,
                "graph_failsafe": failsafe_meta,
            }
