"""Reusable workflow runner for graph-based AI processing."""

import inspect
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from ai_workflow_engine._runtime_state import workflow_run_context_scope
from ai_workflow_engine.models import WorkflowGoal, WorkflowRunContext, WorkflowUsageSummary
from ai_workflow_engine.usage import (
    UsageSink,
    WorkflowUsageContext,
    budget_from_limits,
    workflow_usage_scope,
)

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """Execute a compiled workflow graph with consistent logging and IDs."""

    def __init__(self, config: Any = None, usage_sink: UsageSink | None = None):
        self.config = config
        self.usage_sink = usage_sink

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
                    result = await self._invoke_graph(graph, state, graph_config)
                    outcome = "success"
                    fallback_error: Optional[Exception] = None
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
