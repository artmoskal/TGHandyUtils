"""One owner for capability invocation supervision (v0.11 iteration 2, Phase I2.0).

``CapabilityRuntime.invoke`` used to hold the execution-window resolution, the ambient
nested-window publish/reset, and the bounded-await/cancellation-containment machinery inline —
the highest-risk part of the invocation god-method. This module is that machinery's single owner.

Ownership boundaries (deliberate):
- ``execution_window.py`` REMAINS the window arithmetic owner (resolution math, clamp provenance,
  the ambient ContextVar surface). This module coordinates those primitives for one invocation —
  it never re-implements the arithmetic.
- ``process_io.py`` remains the subprocess settlement owner; the process-backed door only shows up
  here as the ``"process"`` enforcement mode's work/grace split.
- Observation is OUT: supervision raises typed, window-carrying exceptions and returns results; it
  never records a trace event or detail, and never imports observation, executor, viewer, bundle,
  or product modules.

Behavior is IDENTICAL to engine-v0.10.1 (proven by the sealed preservation oracle + the live
two-wheel differential): timeout → honest PARTIAL, suppressed cancellation → containment FAILURE,
caller cancellation propagates untranslated, unbounded calls gain no synthetic deadline, and the
ambient scope is reset on success, failure, timeout, AND caller cancellation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from ai_workflow_engine.execution_window import (
    ExecutionWindowInputs,
    invocation_window_remaining_s,
    publish_invocation_window,
    reset_invocation_window,
    resolve_execution_window,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ExecutionTimeout",
    "CancellationContainmentError",
    "resolve_invocation_window",
    "published_invocation_scope",
    "supervise_awaitable",
]


class ExecutionTimeout(Exception):
    """A capability exceeded its resolved execution window. Carries the window so the outcome
    can be recorded as a truthful PARTIAL (bounded work stopped), not an anonymous failure."""

    def __init__(self, message: str, *, window: Any = None) -> None:
        super().__init__(message)
        self.window = window


class CancellationContainmentError(Exception):
    """A cooperative handler suppressed cancellation at its execution window and finished
    anyway — the engine cannot claim it stopped. Surfaced as a FAILURE (not a clean partial),
    because unstoppable side effects may have continued past the boundary."""

    def __init__(self, message: str, *, window: Any) -> None:
        super().__init__(message)
        self.window = window


# The active invocation's complete soft/hard window lives on the context-local surface in
# execution_window.py. Nested invocations inherit the remaining soft budget; process-backed doors
# use both deadlines so cleanup fits before the hard cutoff.
def _parent_soft_remaining_s() -> Optional[float]:
    remaining = invocation_window_remaining_s()
    return remaining.soft_s if remaining is not None else None


def resolve_invocation_window(
    *, name: str, spec: Any, context: Any, is_async: bool, session: Any
) -> tuple[Any, Any, str]:
    """Resolve this invocation's execution window and refuse work that must not start.

    Determines the enforcement mode FIRST (an object with an async ``__call__`` is async too), so
    the RESOLVED, PERSISTED decision carries the truthful enforcement. Intersects the capability
    limit + run-remaining + any per-task request the planner attached + the parent's remaining
    soft budget. Returns ``(window, context_with_window, enforcement)``.

    Preflight refusals (both typed timeouts carrying the window):
    - a run whose deadline has ALREADY passed must not start new work;
    - a finite hard window on an uninterruptible capability (enforcement ``"none"``) is a
      misdeclaration — the engine never claims a stop it cannot perform.
    """

    enforcement = spec.resolved_timeout_enforcement(is_async=is_async)
    window = resolve_execution_window(
        ExecutionWindowInputs(
            request=context.execution_request,
            capability_timeout_s=spec.timeout_s,
            run_remaining_s=session.run_remaining_s() if session is not None else None,
            parent_soft_remaining_s=_parent_soft_remaining_s(),
        ),
        enforcement=enforcement,
    )
    context = context.model_copy(update={"execution_window": window})
    hard = window.hard_timeout_s

    if hard is not None and hard <= 0:
        raise ExecutionTimeout(
            f"run execution window exhausted before capability '{name}' could start",
            window=window,
        )

    if enforcement == "none" and hard is not None and hard > 0:
        raise ExecutionTimeout(
            f"capability '{name}' declares a finite execution window but cannot be "
            f"interrupted (enforcement='none'); make it cooperative async or place "
            f"work needing a hard bound behind a process-backed capability",
            window=window,
        )

    return window, context, enforcement


@contextmanager
def published_invocation_scope(window: Any) -> Iterator[None]:
    """Publish THIS invocation's soft/hard deadlines on the engine-owned ambient surface for the
    duration of the handler: nested subworkflow/child-plan calls are bounded by our remaining soft
    budget (they can only shorten it), and process-backed doors read it as their ambient work
    bound. Unbounded windows publish nothing (no synthetic deadline). The reset is guaranteed on
    success, failure, timeout, and caller cancellation alike."""

    soft = window.soft_timeout_s
    hard = window.hard_timeout_s
    if soft is not None and hard is not None:
        now = time.monotonic()
        token = publish_invocation_window(
            soft_deadline_monotonic=now + soft,
            hard_deadline_monotonic=now + hard,
        )
    else:
        token = None
    try:
        yield
    finally:
        if token is not None:
            reset_invocation_window(token)


async def supervise_awaitable(
    *, name: str, awaitable: Any, window: Any, enforcement: str
) -> Any:
    """Await a handler's result under the represented window.

    Bounded cooperative/process calls split the AMBIENT remaining time (which a parent may have
    already shortened) into work + cancellation-grace: cooperative handlers work to the soft
    deadline with the soft→hard gap as acknowledgement grace; the process owner consumes soft for
    work and hard for reap itself, so the outer runtime waits to hard only as a containment belt.
    Unbounded or uninterruptible calls are awaited as-is — no synthetic deadline."""

    hard = window.hard_timeout_s
    soft = window.soft_timeout_s
    if hard is not None and enforcement in ("cooperative", "process"):
        remaining = invocation_window_remaining_s()
        remaining_soft = remaining.soft_s if remaining is not None else soft
        remaining_hard = remaining.hard_s if remaining is not None else hard
        if enforcement == "process":
            work_timeout_s = remaining_hard
            cancellation_grace_s = 0.0
        else:
            work_timeout_s = remaining_soft
            cancellation_grace_s = max(0.0, remaining_hard - remaining_soft)
        return await _run_bounded(name, awaitable, work_timeout_s, cancellation_grace_s, window)
    return await awaitable


async def _run_bounded(
    name: str,
    awaitable: Any,
    work_timeout_s: float,
    cancellation_grace_s: float,
    window: Any,
) -> Any:
    """Run an awaitable under a represented work/cleanup window.

    Within the window: return its result. At the boundary: cancel it and give a bounded
    grace to acknowledge. If it raises CancelledError (clean stop) → PARTIAL timeout. If it
    SUPPRESSES cancellation and returns anyway → a containment FAILURE (the engine will not
    claim a stop it did not perform). If it errors while cancelling → timeout/partial."""

    inner = asyncio.ensure_future(awaitable)
    try:
        done, _pending = await asyncio.wait({inner}, timeout=work_timeout_s)
    except asyncio.CancelledError:
        # Cancellation of the caller is not this invocation's timeout. Stop the child,
        # contain it for the represented grace, then preserve the caller's cancellation.
        inner.cancel()
        await _settle_cancelled_inner(inner, cancellation_grace_s)
        raise
    if inner in done:
        # completed within the window — return its result (or re-raise its own exception,
        # which the caller maps to a normal failure, NOT a timeout).
        return inner.result()
    inner.cancel()
    try:
        if cancellation_grace_s == 0:
            await asyncio.sleep(0)
            if not inner.done():
                raise asyncio.TimeoutError
            await inner
        else:
            await asyncio.wait_for(asyncio.shield(inner), timeout=cancellation_grace_s)
    except asyncio.CancelledError:
        if asyncio.current_task() is not None and asyncio.current_task().cancelling():
            inner.cancel()
            _detach_cancelled_inner(inner)
            raise
        raise ExecutionTimeout(
            f"capability '{name}' exceeded its {work_timeout_s:g}s execution window "
            "(work deadline)",
            window=window,
        )
    except asyncio.TimeoutError:
        inner.cancel()
        _detach_cancelled_inner(inner)
        raise CancellationContainmentError(
            f"capability '{name}' did not acknowledge cancellation within "
            f"{cancellation_grace_s:g}s of its work window",
            window=window,
        )
    except Exception:
        raise ExecutionTimeout(
            f"capability '{name}' exceeded its {work_timeout_s:g}s execution window "
            "(work deadline)",
            window=window,
        )
    else:
        # cancelled but returned a value anyway → suppression → containment FAILURE.
        raise CancellationContainmentError(
            f"capability '{name}' suppressed cancellation at its execution window and "
            f"returned anyway",
            window=window,
        )


# v0.11.18: descendant settlement truth across NESTED cancellation.
#
# The caller-cancellation branch stops a child, contains it for its represented grace, then
# re-raises the caller's CancelledError so caller-owned cancellation keeps its identity. When the
# child does NOT settle inside that grace it is detached and still running — which an outer engine
# deadline owner would otherwise read as a clean acknowledged stop and report as honest PARTIAL,
# while detached work can still run side effects. The detachment is recorded here so the outer
# graph owner cannot downgrade it; caller-owned cancellation still propagates as CancelledError.
_UNSETTLED_DESCENDANTS: "ContextVar[Optional[list]]" = ContextVar(
    "ai_workflow_engine_unsettled_descendants", default=None
)


@contextmanager
def descendant_settlement_scope() -> Iterator[Any]:
    """Track descendants detached WITHOUT settling inside this scope.

    Yields a predicate reporting whether any descendant was detached unsettled. The tracker is a
    shared mutable list so tasks created inside the scope (which copy the context) report into the
    same record.
    """

    tracker: list = []
    token = _UNSETTLED_DESCENDANTS.set(tracker)
    try:
        yield lambda: bool(tracker)
    finally:
        _UNSETTLED_DESCENDANTS.reset(token)


def _note_unsettled_descendant(reason: str) -> None:
    tracker = _UNSETTLED_DESCENDANTS.get()
    if tracker is not None:
        tracker.append(reason)


async def _settle_cancelled_inner(inner: "asyncio.Task[Any]", grace_s: float) -> None:
    try:
        if grace_s > 0:
            await asyncio.wait_for(asyncio.shield(inner), timeout=grace_s)
        else:
            await asyncio.sleep(0)
    except BaseException:
        logger.debug("capability child stopped while caller cancellation was propagating")
    if not inner.done():
        inner.cancel()
        _detach_cancelled_inner(inner)


def _detach_cancelled_inner(inner: "asyncio.Task[Any]") -> None:
    # Every detachment path (own-window containment timeout, caller-cancellation settlement, or
    # cancel-while-cancelling) leaves work that did not settle inside its represented grace. Record
    # it so an outer engine-deadline owner cannot report a clean bounded stop over live work.
    if not inner.done():
        _note_unsettled_descendant("capability detached without settling in its grace")

    def _consume(task: "asyncio.Task[Any]") -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            logger.exception("detached capability task failed after cancellation")

    inner.add_done_callback(_consume)
