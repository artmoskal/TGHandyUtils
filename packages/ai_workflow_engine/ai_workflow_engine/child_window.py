"""Coarse child-workflow execution ceiling — the one owner for both child doors.

v0.11.18: a child workflow's configured ``RuntimeLimits.timeout_s`` was never enforced —
``WorkflowExecutor._run_inner`` called ``compiled.ainvoke`` directly, bypassing both the runner's
graph fail-safe and nested capability supervision. Both child doors (``register_workflow_capability``
and a declarative ``.subworkflow`` node) converge at ``_run_inner``, so the ceiling is owned HERE
and called once from that universal boundary — never duplicated in ``builder.py`` or
``nodes/subworkflow.py``, and never a second timeout scheduler.

Ownership rule (the fix for stealing an ancestor's deadline): a child-owned window is applied ONLY
when the child's OWN configured limit is the STRICT limiting source. If the child has no limit, or
an already-enforced ancestor (run remaining, or a parent invocation window) is tighter or equal,
this owner does nothing and the ancestor settles and attributes the outcome. The dimensions this
owner uniquely adds over the pre-existing top-level fail-safe are the child's own configured limit
and — critically — publishing that limit to nested capability supervision so synchronous
uninterruptible work is refused before it can block the event loop past the deadline.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Dict, Optional

from ai_workflow_engine.engine.invocation_supervision import published_invocation_scope
from ai_workflow_engine.engine.runner import (
    GraphFailsafeWindow,
    _GraphFailsafeExpired,
    _invoke_graph_with_failsafe,
)
from ai_workflow_engine.execution_window import (
    ExecutionWindowDecision,
    ExecutionWindowInputs,
    invocation_window_remaining_s,
    resolve_execution_window,
)
from ai_workflow_engine.models import WorkflowTraceEvent
from ai_workflow_engine.workflow import WorkflowDefinition


class ChildWorkflowWindow:
    """Resolve and enforce one shared window around a complete child-workflow invocation."""

    def __init__(self, runtime: Any) -> None:
        # Hold the runtime, not its trace_sink: the sink is a swappable collaborator that
        # consumers rebind after construction, so it is resolved at record time.
        self._runtime = runtime

    def resolve(
        self, definition: WorkflowDefinition, session: Any
    ) -> Optional[ExecutionWindowDecision]:
        """Return the FULL window decision iff the child's OWN limit is the strict limiting source.

        ``None`` means no child-owned window: either the child declares no timeout, or an
        already-enforced ancestor bound is tighter/equal and must keep ownership (adding a second
        inner timer there would re-reserve cleanup, expire before the real owner, and mislabel an
        ancestor timeout as a child partial). The full decision is returned — never collapsed —
        so publication and observation both see configured/effective/limiting-source truth.
        """

        child_limit = definition.limits.timeout_s if definition.limits is not None else None
        if child_limit is None:
            return None
        run_remaining = (
            session.run_remaining_s()
            if session is not None and hasattr(session, "run_remaining_s")
            else None
        )
        parent_remaining = invocation_window_remaining_s()
        parent_soft = parent_remaining.soft_s if parent_remaining is not None else None
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
        # Enforce only when the child's own limit is STRICTLY the tightest. A tie with an ancestor
        # (both in limiting_sources) leaves ownership with the ancestor that already enforces it.
        if set(decision.limiting_sources or []) != {"capability_limit"}:
            return None
        return decision

    async def run_bounded(
        self,
        definition: WorkflowDefinition,
        make_invocation: "Any",
        child_state: Dict[str, Any],
        decision: ExecutionWindowDecision,
    ) -> Dict[str, Any]:
        """Run the child graph under one shared, PUBLISHED window; record one terminal event.

        The window is published as the ambient invocation window for the whole child graph, so
        nested cooperative/process work narrows to the child deadline and a nested synchronous
        ``enforcement="none"`` handler is refused before it starts (the graph fail-safe alone
        cannot bound work that blocks the event loop). Retries and every internal node share this
        one window; it is never reset per attempt. Clean cooperative expiry is honest PARTIAL; a
        child that suppresses cancellation is a loud FAILED containment failure. Engine-owned
        processes are reaped by the existing per-capability cancellation contract as the cancel
        propagates in.
        """

        window = GraphFailsafeWindow(
            work_timeout_s=decision.soft_timeout_s,
            cancellation_grace_s=decision.completion_reserve_s,
        )
        started = time.monotonic()
        try:
            # Create the coroutine INSIDE the published scope so the graph task inherits the
            # ambient window; enforce the outer graph deadline with the runner's fail-safe.
            with published_invocation_scope(decision):
                final_state = await _invoke_graph_with_failsafe(
                    make_invocation(), window
                )
        except _GraphFailsafeExpired as exc:
            elapsed_s = time.monotonic() - started
            status = "failed" if exc.containment_failed else "partial"
            reason = (
                f"child workflow '{definition.workflow_id}' exhausted its execution window; "
                f"work was cancelled after {exc.window.work_timeout_s:g}s and given "
                f"{exc.window.cancellation_grace_s:g}s for containment"
            )
            if exc.containment_failed:
                reason += "; child graph code did not acknowledge cancellation within the bound"
            self._record(
                definition,
                decision,
                elapsed_s,
                status=status,
                reason=reason,
                containment_failed=exc.containment_failed,
            )
            return {
                **child_state,
                "status": status,
                "error": reason,
                "graph_failsafe": exc.window.metadata(
                    containment_failed=exc.containment_failed
                ),
            }
        elapsed_s = time.monotonic() - started
        self._record(definition, decision, elapsed_s, status="completed", reason=None)
        return final_state

    def _record(
        self,
        definition: WorkflowDefinition,
        decision: ExecutionWindowDecision,
        elapsed_s: float,
        *,
        status: str,
        reason: Optional[str],
        containment_failed: bool = False,
    ) -> None:
        """One descriptive child-window terminal event carrying the complete decision + elapsed.

        Product soft targets are never part of resolution and are not recorded here; this is the
        engine's enforced-window truth (configured breaker, effective hard/work limits, limiting
        source, cleanup reserve, actual elapsed). The trace sink is resolved now, not frozen at
        construction, so a legitimately rebound sink still receives this event.
        """

        sink = getattr(self._runtime, "trace_sink", None)
        if sink is None:
            return
        # A clean bounded stop (completed/partial) is not an error: keep the top-level ``error``
        # field for genuine containment failure only, so severity stays honest. The timeout
        # message is always observable in metadata regardless of severity.
        sink.record(
            WorkflowTraceEvent(
                node=definition.workflow_id,
                decision=f"child_window:{status}",
                phase="child:window",
                severity="error" if status == "failed" else "info",
                error=reason if status == "failed" else None,
                elapsed_ms=int(elapsed_s * 1000),
                metadata={
                    "child_workflow": definition.workflow_id,
                    "configured_timeout_s": decision.capability_timeout_s,
                    "effective_hard_s": decision.hard_timeout_s,
                    "work_timeout_s": decision.soft_timeout_s,
                    "cleanup_reserve_s": decision.completion_reserve_s,
                    "limiting_source": list(decision.limiting_sources or []),
                    "elapsed_s": elapsed_s,
                    "terminal_reason": status,
                    "message": reason,
                    "graph_failsafe_containment_failed": containment_failed,
                },
            )
        )
