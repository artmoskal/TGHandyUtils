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

from contextlib import contextmanager
from contextvars import ContextVar

import time
import uuid
from typing import Any, Callable, Iterator, List, Optional

from ai_workflow_engine._runtime_state import current_run_session
from ai_workflow_engine.models import (
    CapabilityContext,
    WorkflowRunContext,
    WorkflowTraceEvent,
    WorkflowUsageSummary,
)


# The ANCESTOR CHAIN of active child-trace scopes (v0.11 M7, hardened per review finding 3):
# a tuple stack, one list per open scope. Every recorded event is appended to EVERY active
# ancestor, so a parent envelope includes its grandchildren; sibling tasks hold disjoint chains
# (ContextVar copy-on-task), so siblings never bleed.
_ACTIVE_CHILD_TRACES: ContextVar[tuple] = ContextVar("workflow_child_trace_scopes", default=())


@contextmanager
def child_trace_slice() -> Iterator[List[WorkflowTraceEvent]]:
    """Session-owned trace slice for ONE child run (v0.11, manifest row M7). Events recorded in
    this task context while the scope is open are appended to the yielded list AND to every
    enclosing ancestor scope — the child envelope's exact trace including descendants —
    replacing the removed sink-``.events`` sniffing fallback."""

    events: List[WorkflowTraceEvent] = []
    token = _ACTIVE_CHILD_TRACES.set(_ACTIVE_CHILD_TRACES.get() + (events,))
    try:
        yield events
    finally:
        _ACTIVE_CHILD_TRACES.reset(token)


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

    def record(self, event: WorkflowTraceEvent) -> None:
        session = current_run_session()
        if session is not None:
            from ai_workflow_engine.correlation import enrich_related_run

            updates = {}
            if event.run_id is None:
                updates["run_id"] = session.run_id
            # ONE enrichment rule for every event surface: stamp when absent, REFUSE a
            # conflicting related-run identity before buffer/bundle/sink persistence.
            enriched = enrich_related_run(
                event.metadata,
                session.run_context.correlation_id,
                surface="trace",
                event_id=event.event_id,
            )
            if enriched is not event.metadata:
                updates["metadata"] = enriched
            if updates:
                event = event.model_copy(update=updates)
            session.trace_events.append(event)
            for child_slice in _ACTIVE_CHILD_TRACES.get():
                # v0.11 (manifest row M7): a child run's envelope trace is an EXPLICIT
                # session-owned slice — captured per task (ContextVar chain), never sniffed
                # back out of a sink. Appending to EVERY active ancestor keeps parent
                # envelopes complete (grandchildren included); sibling tasks have disjoint
                # chains, so sibling events never bleed in.
                child_slice.append(event)
            if session.bundle is not None:
                session.bundle.trace_sink.record(event)
        self.inner.record(event)


class SessionScopedDetailSink:
    """Route observation details to the ACTIVE session's bundle + the configured sink."""

    def __init__(self, inner: Any = None) -> None:
        self.inner = inner

    @property
    def details(self):
        return getattr(self.inner, "details", None)

    def clear(self) -> None:
        clear = getattr(self.inner, "clear", None)
        if callable(clear):
            clear()

    def record(self, detail: Any) -> None:
        session = current_run_session()
        if session is not None:
            from ai_workflow_engine.correlation import enrich_related_run

            metadata = getattr(detail, "metadata", None)
            if metadata is not None:
                enriched = enrich_related_run(
                    metadata,
                    session.run_context.correlation_id,
                    surface="detail",
                    event_id=getattr(detail, "detail_id", None),
                )
                if enriched is not metadata:
                    detail = detail.model_copy(update={"metadata": enriched})
            if session.bundle is not None:
                session.bundle.detail_sink.record(detail)
        if self.inner is not None:
            self.inner.record(detail)


class SessionScopedUsageSink:
    """Route usage events to the ACTIVE session's bundle + the configured sink."""

    def __init__(self, inner: Any = None) -> None:
        self.inner = inner

    def record(self, event: Any) -> None:
        # Usage correlation is enriched ONCE in record_usage_event (before aggregation),
        # so every surface holds the IDENTICAL event — this tee only routes.
        session = current_run_session()
        if session is not None and session.bundle is not None:
            session.bundle.usage_sink.record(event)
        if self.inner is not None:
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
        # v0.10 run execution window: the session owns the run's monotonic deadline and the
        # cumulative ACTIVE elapsed budget. `_clock` is a monotonic source (injectable for
        # tests). We store a DEADLINE for O(1) remaining-time reads plus `_active_elapsed_s`
        # so a snapshot persists elapsed DURATION (never a monotonic timestamp) and resume
        # rebuilds the deadline from the remaining active budget — time spent suspended at a
        # human/durable gate does not consume it.
        self._clock: Callable[[], float] = time.monotonic
        self._run_timeout_s: Optional[float] = None
        self._deadline_monotonic: Optional[float] = None
        self._active_elapsed_s: float = 0.0

    def start_execution_window(
        self,
        *,
        run_timeout_s: Optional[float],
        clock: Optional[Callable[[], float]] = None,
        prior_active_elapsed_s: float = 0.0,
    ) -> None:
        """Open (or reopen on resume) the run's active execution window. ``run_timeout_s`` is
        the TOTAL active budget; ``prior_active_elapsed_s`` is what a resumed run already
        spent active (0 for a fresh run). The remaining active budget bounds the deadline."""

        if clock is not None:
            self._clock = clock
        self._run_timeout_s = run_timeout_s
        self._active_elapsed_s = prior_active_elapsed_s
        if run_timeout_s is None:
            self._deadline_monotonic = None
            return
        remaining = max(0.0, run_timeout_s - prior_active_elapsed_s)
        self._deadline_monotonic = self._clock() + remaining

    def run_remaining_s(self) -> Optional[float]:
        """Remaining ACTIVE run budget in seconds, or None if the run is unbounded."""

        if self._deadline_monotonic is None:
            return None
        return max(0.0, self._deadline_monotonic - self._clock())

    def active_elapsed_s(self) -> float:
        """Cumulative active time consumed so far (for snapshot persistence). Suspended
        wall-time is excluded because the deadline is only live while the session runs."""

        if self._deadline_monotonic is None or self._run_timeout_s is None:
            return self._active_elapsed_s
        consumed = self._run_timeout_s - max(0.0, self._deadline_monotonic - self._clock())
        return min(self._run_timeout_s, max(self._active_elapsed_s, consumed))

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
            correlation_id=getattr(goal, "correlation_id", None) if goal else None,
        )

    @property
    def run_id(self) -> str:
        return self.run_context.workflow_id

    def close(self, status: str, *, artifacts: Optional[List[Any]] = None) -> None:
        """Finalize session-scoped resources with the run's terminal status (idempotent).

        Today that is the optional observation bundle — failed runs finalize as failed
        instead of leaving an unfinalized directory behind. ``artifacts`` (the run's
        accumulated ``WorkflowArtifact``s) are archived into the bundle at finalize (G1
        evidence resolution); a run that raised before producing an envelope closes
        without them — the manifest is then honestly empty. Finalizing a real
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
            self.bundle.finalize(
                self.definition,
                status=status,
                usage=self.usage_summary,
                artifacts=artifacts,
            )
