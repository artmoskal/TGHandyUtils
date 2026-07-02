"""Per-run execution session (H2).

One run = one ``WorkflowRunSession``: the single home for per-run state — run identity
(``run_context``), the usage summary the budget scope aggregates into, and (optionally) the
observation bundle whose lifecycle must end with the run. Long-lived application state
(registry, workflows, profiles, scheduler, compiled-graph cache) stays on
``WorkflowEngine``/``WorkflowExecutor`` and is explicitly NOT part of the session.

Internal seam: the public ``engine.run(...)``/``engine.resume(...)`` API is unchanged —
the executor builds a session per run and the runner consumes it instead of re-deriving
per-run identity/usage from loose fields. Concurrent runs therefore cannot share mutable
per-run state; isolation is proven by ``tests/test_run_session.py``.
"""

from __future__ import annotations

import uuid
from typing import Any, List, Optional

from ai_workflow_engine._runtime_state import current_run_session
from ai_workflow_engine.models import (
    CapabilityContext,
    WorkflowRunContext,
    WorkflowTraceEvent,
    WorkflowUsageSummary,
)


class SessionScopedTraceSink:
    """Tee every trace event into the ACTIVE run session's buffer, then the configured sink.

    This is the B6/B7 fix: the result envelope reads the session's run-scoped buffer instead of
    sniffing ``.events`` off whatever sink happened to be configured — so ``result.trace`` is
    complete with Jsonl/bundle sinks, and a long-lived engine no longer filters an ever-growing
    shared history per envelope. Events missing a run id are stamped from the session so the
    buffer (and downstream sinks) stay attributable. The buffer is unbounded WITHIN one run —
    runs are already bounded by recursion limits and budgets.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    @property
    def events(self):  # legacy passthrough: child-run envelopes still sniff in-memory sinks
        return getattr(self.inner, "events", None)

    def record(self, event: WorkflowTraceEvent) -> None:
        session = current_run_session()
        if session is not None:
            if event.run_id is None:
                event = event.model_copy(update={"run_id": session.run_id})
            session.trace_events.append(event)
        self.inner.record(event)


class WorkflowRunSession:
    """Everything that belongs to exactly one run.

    - ``run_context``: the run's identity (run_id/type/goal), derived ONCE here instead of
      once in the engine and again in the runner.
    - ``usage_summary``: the per-run aggregate the usage/budget scope writes into (a
      restored summary on resume keeps budgets cumulative across suspension).
    - ``bundle``: optional observation run bundle; ``close(status)`` finalizes it with the
      run's terminal status (including failures) so a bundle can never outlive its session
      silently. The engine does not create bundles — the owner that opened one attaches it.
    """

    def __init__(
        self,
        *,
        workflow_id: str,
        context: CapabilityContext,
        usage_summary: Optional[WorkflowUsageSummary] = None,
        bundle: Any = None,
        definition: Any = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.context = context
        self.usage_summary = usage_summary if usage_summary is not None else WorkflowUsageSummary()
        self.bundle = bundle
        self.definition = definition
        # Run-scoped trace buffer (B6/B7): filled by SessionScopedTraceSink while this
        # session's scope is active; the envelope reads it directly.
        self.trace_events: List[WorkflowTraceEvent] = []
        self.run_context = self._derive_run_context(workflow_id, context)
        self._closed = False

    @staticmethod
    def _derive_run_context(workflow_id: str, context: CapabilityContext) -> WorkflowRunContext:
        goal = context.goal
        engine_run_context = context.run_context
        run_id = (
            getattr(engine_run_context, "workflow_id", None)
            or (goal.metadata.get("run_id") if goal else None)
            or str(uuid.uuid4())
        )
        return WorkflowRunContext(
            workflow_id=str(run_id),
            workflow_type=goal.workflow_type if goal else workflow_id,
            goal_id=goal.goal_id if goal else None,
            delivery_target=goal.delivery_target if goal else None,
            user_id=goal.user_id if goal else None,
            metadata=dict(goal.metadata) if goal else {},
        )

    @property
    def run_id(self) -> str:
        return self.run_context.workflow_id

    def close(self, status: str) -> None:
        """Finalize session-scoped resources with the run's terminal status (idempotent).

        Today that is the optional observation bundle — failed runs finalize as failed
        instead of leaving an unfinalized directory behind. Finalizing a real
        ``ObservationRunBundle`` requires the definition (B5): attaching a bundle without a
        definition is a loud contract error, never a deep TypeError.
        """

        if self._closed:
            return
        self._closed = True
        if self.bundle is not None:
            if self.definition is None:
                raise RuntimeError(
                    "WorkflowRunSession has a bundle but no definition — attach the "
                    "definition so the bundle can be finalized (bundle.finalize(definition, ...))"
                )
            self.bundle.finalize(self.definition, status=status, usage=self.usage_summary)
