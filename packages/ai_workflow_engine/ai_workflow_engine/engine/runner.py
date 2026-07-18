"""Reusable workflow runner for graph-based AI processing."""

import asyncio
from dataclasses import dataclass
import inspect
import json
import logging
import math
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from ai_workflow_engine._runtime_state import current_run_session, workflow_run_context_scope
from ai_workflow_engine.models import (
    WorkflowGoal,
    WorkflowResultStatus,
    WorkflowRunContext,
    WorkflowTraceEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.budget import WorkflowUsageContext, budget_from_limits, workflow_usage_scope
from ai_workflow_engine.usage_events import UsageSink

logger = logging.getLogger(__name__)


def derive_workflow_result_status(state: Dict[str, Any]) -> WorkflowResultStatus:
    """Derive terminal machine truth once for both the result envelope and lifecycle log."""

    explicit = state.get("status")
    current = list(state.get("node_status", {}).values())
    # A graph-level failure has no node whose later successful retry can supersede it.
    if explicit == "failed" and state.get("graph_failsafe") is not None:
        return "failed"
    if explicit == "requires_user_input" or "requires_user_input" in current:
        return "requires_user_input"
    if "failed" in current:
        return "failed"
    if "partial" in current:
        return "partial"
    if not current and explicit == "failed":
        return "failed"
    return "completed"


@dataclass(frozen=True)
class GraphFailsafeWindow:
    """Bound the graph work separately from cancellation containment."""

    work_timeout_s: float
    cancellation_grace_s: float

    def metadata(self, *, containment_failed: bool) -> Dict[str, Any]:
        return {
            "work_timeout_s": self.work_timeout_s,
            "cancellation_grace_s": self.cancellation_grace_s,
            "containment_failed": containment_failed,
        }


class _GraphFailsafeExpired(Exception):
    def __init__(self, window: GraphFailsafeWindow, *, containment_failed: bool) -> None:
        self.window = window
        self.containment_failed = containment_failed
        super().__init__("graph-level execution window expired")


def derive_graph_failsafe_window(
    run_timeout_s: Optional[float],
    session: Any,
    *,
    cancellation_grace_s: float,
) -> Optional[GraphFailsafeWindow]:
    """Resolve one graph invocation's remaining work and cleanup allowance.

    Derived from the run session's REMAINING active budget — so a resumed run that already
    consumed most of its budget does NOT receive a fresh full timeout. Cancellation grace is
    represented separately: it may contain cooperative cleanup after work expires, but it can
    never silently extend the work budget. ``None`` = unbounded run, no fail-safe."""

    if run_timeout_s is None:
        return None
    if not math.isfinite(run_timeout_s) or run_timeout_s < 0:
        raise ValueError(
            f"run timeout must be a finite non-negative number, got {run_timeout_s!r}"
        )
    if not math.isfinite(cancellation_grace_s) or cancellation_grace_s < 0:
        raise ValueError(
            "graph cancellation_grace_s must be a finite non-negative number, "
            f"got {cancellation_grace_s!r}"
        )
    if session is not None and hasattr(session, "run_hard_remaining_s"):
        work_timeout_s = session.run_remaining_s()
        hard_remaining_s = session.run_hard_remaining_s()
        effective_grace_s = min(
            cancellation_grace_s,
            session.run_completion_reserve_s(),
            hard_remaining_s,
        )
    else:
        hard_remaining_s = run_timeout_s
        effective_grace_s = min(cancellation_grace_s, hard_remaining_s / 4.0)
        work_timeout_s = hard_remaining_s - effective_grace_s
    return GraphFailsafeWindow(
        work_timeout_s=max(0.0, work_timeout_s),
        cancellation_grace_s=effective_grace_s,
    )


def _consume_background_task(task: asyncio.Task[Any]) -> None:
    """Retrieve a detached task's terminal exception after containment failed."""

    if task.cancelled():
        return
    try:
        task.exception()
    except BaseException:
        logger.exception("detached graph task failed after cancellation containment")


async def _invoke_graph_with_failsafe(
    invocation: Awaitable[Dict[str, Any]],
    window: GraphFailsafeWindow,
) -> Dict[str, Any]:
    """Run graph work, then contain cancellation for a bounded, observable grace period."""

    if window.work_timeout_s <= 0:
        # Do not schedule even one event-loop turn after an already-exhausted deadline. Runner
        # callers pass a coroutine; close it so refusal also produces no un-awaited warning.
        if inspect.iscoroutine(invocation):
            invocation.close()
        elif isinstance(invocation, asyncio.Future):
            invocation.cancel()
        raise _GraphFailsafeExpired(window, containment_failed=False)

    task = asyncio.ensure_future(invocation)
    try:
        done, _ = await asyncio.wait({task}, timeout=window.work_timeout_s)
    except asyncio.CancelledError:
        task.cancel()
        try:
            if window.cancellation_grace_s > 0:
                await asyncio.wait_for(
                    asyncio.shield(task), timeout=window.cancellation_grace_s
                )
            else:
                await asyncio.sleep(0)
        except BaseException:
            logger.debug("graph task stopped while caller cancellation was propagating")
        if not task.done():
            task.cancel()
            task.add_done_callback(_consume_background_task)
        raise
    if task in done:
        return task.result()

    task.cancel()
    try:
        if window.cancellation_grace_s == 0:
            await asyncio.sleep(0)
            if not task.done():
                raise asyncio.TimeoutError
            await task
        else:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=window.cancellation_grace_s
            )
    except asyncio.CancelledError:
        if asyncio.current_task() is not None and asyncio.current_task().cancelling():
            task.cancel()
            task.add_done_callback(_consume_background_task)
            raise
        raise _GraphFailsafeExpired(window, containment_failed=False) from None
    except asyncio.TimeoutError:
        task.cancel()
        task.add_done_callback(_consume_background_task)
        raise _GraphFailsafeExpired(window, containment_failed=True) from None
    except BaseException:
        # Work expired first. An exception while acknowledging cancellation is cleanup
        # evidence, not permission to relabel the timeout as an unrelated graph failure.
        raise _GraphFailsafeExpired(window, containment_failed=False) from None
    else:
        # Returning after cancellation means the graph swallowed the stop request. The work
        # cannot be trusted as a successful completion, even if it happened inside grace.
        raise _GraphFailsafeExpired(window, containment_failed=True) from None


class WorkflowRunner:
    """Execute a compiled workflow graph with consistent logging and IDs."""

    def __init__(
        self,
        config: Any = None,
        usage_sink: UsageSink | None = None,
        trace_sink: Any = None,
    ):
        self.config = config
        self.usage_sink = usage_sink
        self.trace_sink = trace_sink

    _GRAPH_CANCELLATION_GRACE_S = 2.0

    @property
    def graph_cancellation_grace_s(self) -> float:
        return self._GRAPH_CANCELLATION_GRACE_S

    async def run(
        self,
        graph: Any,
        initial_state: Dict[str, Any],
        workflow_type: Optional[str] = None,
        user_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        goal: Optional[WorkflowGoal] = None,
        graph_config: Optional[Dict[str, Any]] = None,
        recursion_fallback: Optional[
            Callable[[Dict[str, Any], Exception], Dict[str, Any] | Awaitable[Dict[str, Any]]]
        ] = None,
        usage_summary: Optional[WorkflowUsageSummary] = None,
        session: Optional[Any] = None,
    ) -> Dict[str, Any]:
        effective_type = workflow_type or (goal.workflow_type if goal else None)
        if not effective_type:
            raise ValueError("workflow_type or goal.workflow_type is required")

        engine_context = initial_state.get("engine_context")
        if session is not None:
            # H2: the run session is the single home for per-run identity + usage — no
            # re-derivation here, so a session's run can never disagree with itself.
            ctx = session.run_context
            usage_summary = session.usage_summary
        else:
            engine_run_context = getattr(engine_context, "run_context", None)
            run_id = (
                getattr(engine_run_context, "workflow_id", None)
                or (goal.metadata.get("run_id") if goal else None)
                or str(uuid.uuid4())
            )
            ctx = WorkflowRunContext(
                workflow_id=str(run_id),
                workflow_type=effective_type,
                goal_id=goal.goal_id if goal else None,
                delivery_target=goal.delivery_target if goal else None,
                user_id=user_id if user_id is not None else (goal.user_id if goal else None),
                metadata={**(goal.metadata if goal else {}), **(metadata or {})},
            )
            # A caller-provided summary seeds the scope (resume: budgets cumulative).
            usage_summary = usage_summary if usage_summary is not None else WorkflowUsageSummary()
        state = dict(initial_state)
        state["workflow_context"] = ctx
        state["usage_summary"] = usage_summary
        if goal:
            state["workflow_goal"] = goal

        start = time.monotonic()
        self._log("workflow_start", ctx, {})
        try:
            limits = getattr(engine_context, "limits", None)
            usage_context = WorkflowUsageContext(
                ctx,
                usage_summary,
                budget_from_limits(limits),
                usage_sink=self.usage_sink,
            )
            with workflow_run_context_scope(ctx), workflow_usage_scope(usage_context):
                try:
                    # v0.10 #6b graph-level fail-safe: a hang OUTSIDE a capability (a node's own
                    # async code, an uninterruptible `none` handler that runs unbounded) is not
                    # caught by per-capability enforcement. Bound the WHOLE active invocation by
                    # the session's REMAINING active budget (correct after resume — never a fresh
                    # full timeout). Cancellation/reap gets a separate represented allowance;
                    # it never extends the graph's work budget invisibly.
                    run_timeout = getattr(limits, "timeout_s", None)
                    failsafe = derive_graph_failsafe_window(
                        run_timeout,
                        current_run_session(),
                        cancellation_grace_s=self._GRAPH_CANCELLATION_GRACE_S,
                    )
                    if failsafe is not None:
                        result = await _invoke_graph_with_failsafe(
                            self._invoke_graph(graph, state, graph_config),
                            failsafe,
                        )
                    else:
                        result = await self._invoke_graph(graph, state, graph_config)
                    outcome = derive_workflow_result_status(result)
                    fallback_error: Optional[Exception] = None
                except _GraphFailsafeExpired as exc:
                    # A hang outside a capability produced nothing salvageable — return a
                    # truthful FAILED result (observable), not a raised exception.
                    failsafe_meta = exc.window.metadata(
                        containment_failed=exc.containment_failed
                    )
                    error = (
                        "run exhausted its remaining execution window; graph work was "
                        f"cancelled after {exc.window.work_timeout_s:g}s and given "
                        f"{exc.window.cancellation_grace_s:g}s for containment"
                    )
                    if exc.containment_failed:
                        error += "; graph code did not acknowledge cancellation within the bound"
                    result = {
                        **state,
                        "status": "failed",
                        "error": error,
                        "graph_failsafe": failsafe_meta,
                    }
                    if self.trace_sink is not None:
                        self.trace_sink.record(
                            WorkflowTraceEvent(
                                node=getattr(session, "workflow_id", effective_type),
                                decision="failed",
                                node_status="failed",
                                error=error,
                                phase="run:failsafe",
                                severity="error",
                                metadata={"graph_failsafe": failsafe_meta},
                            )
                        )
                    outcome = "failed"
                    fallback_error = None
                except Exception as exc:
                    if not recursion_fallback or not self._is_recursion_exhaustion(exc):
                        raise
                    fallback_update = recursion_fallback(state, exc)
                    if inspect.isawaitable(fallback_update):
                        fallback_update = await fallback_update
                    result = {**state, **(fallback_update or {})}
                    outcome = "fallback"
                    fallback_error = exc
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result["usage_summary"] = usage_summary
            payload = {
                "elapsed_ms": elapsed_ms,
                "outcome": outcome,
                "text_calls": usage_summary.text_call_count,
                "image_calls": usage_summary.image_call_count,
                "tool_calls": usage_summary.tool_call_count,
                "tool_characters": usage_summary.tool_character_count,
                "total_tokens": usage_summary.total_tokens,
                "estimated_usd": usage_summary.estimated_usd,
            }
            if fallback_error is not None:
                payload.update(
                    {
                        "fallback_error_type": fallback_error.__class__.__name__,
                        "fallback_error": str(fallback_error),
                    }
                )
            self._log(
                "workflow_end",
                ctx,
                payload,
            )
            return result
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._log(
                "workflow_error",
                ctx,
                {"elapsed_ms": elapsed_ms, "outcome": "failure", "error": str(exc)},
            )
            raise

    @staticmethod
    async def _invoke_graph(
        graph: Any,
        state: Dict[str, Any],
        graph_config: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if hasattr(graph, "ainvoke"):
            if graph_config is not None:
                return await graph.ainvoke(state, config=graph_config)
            return await graph.ainvoke(state)
        if graph_config is not None:
            return graph.invoke(state, config=graph_config)
        return graph.invoke(state)

    @staticmethod
    def _is_recursion_exhaustion(exc: Exception) -> bool:
        """LangGraph is a declared dependency — detect its recursion-limit failure by type,
        never by class-name/message heuristics (a lookalike error must not be swallowed
        into the consumer's recursion fallback)."""

        from langgraph.errors import GraphRecursionError

        return isinstance(exc, GraphRecursionError)

    @staticmethod
    def _log(event: str, ctx: WorkflowRunContext, payload: Dict[str, Any]) -> None:
        record = {
            "event": event,
            "workflow_id": ctx.workflow_id,
            "workflow_type": ctx.workflow_type,
            "goal_id": ctx.goal_id,
            "delivery_target": ctx.delivery_target,
            "user_id": ctx.user_id,
            "metadata": ctx.metadata,
            **payload,
        }
        logger.info("workflow_event %s", json.dumps(record, sort_keys=True))
