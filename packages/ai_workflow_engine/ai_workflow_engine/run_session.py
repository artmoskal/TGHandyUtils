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
from typing import Any, Optional

from ai_workflow_engine.models import (
    CapabilityContext,
    WorkflowRunContext,
    WorkflowUsageSummary,
)


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
    ) -> None:
        self.workflow_id = workflow_id
        self.context = context
        self.usage_summary = usage_summary if usage_summary is not None else WorkflowUsageSummary()
        self.bundle = bundle
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
        instead of leaving an unfinalized directory behind.
        """

        if self._closed:
            return
        self._closed = True
        if self.bundle is not None:
            self.bundle.finalize(status=status)
