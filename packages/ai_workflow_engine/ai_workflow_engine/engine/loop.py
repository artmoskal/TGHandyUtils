"""Bounded supervisor loop over registered workflow capabilities."""

from __future__ import annotations

from typing import Any, Protocol

from ai_workflow_engine.engine.capabilities import CapabilityRuntime
from ai_workflow_engine.engine.checkpoints import CheckpointStore, jsonable_checkpoint_data
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    SessionState,
    WorkflowCheckpoint,
    WorkflowLoopResult,
    WorkflowStepDecision,
    WorkflowTraceEvent,
)


class WorkflowStepPlanner(Protocol):
    """Planner that chooses the next capability or terminal loop action."""

    async def plan(
        self,
        context: CapabilityContext,
        history: list[CapabilityResult],
        decisions: list[WorkflowStepDecision],
    ) -> WorkflowStepDecision:
        """Return the next loop decision."""


class WorkflowLoopController:
    """Run a small goal-directed loop over registered capabilities.

    This is useful for products that need a supervisor-agent style control loop without writing
    their own runtime. Product planners decide *what* to do next; the engine owns capability
    invocation, step limits, terminal statuses, and trace.
    """

    def __init__(
        self,
        runtime: CapabilityRuntime,
        planner: WorkflowStepPlanner,
        *,
        max_steps: int | None = None,
        checkpoint_store: CheckpointStore | None = None,
        session: SessionState | None = None,
    ) -> None:
        self.runtime = runtime
        self.planner = planner
        self.max_steps = max_steps
        self.checkpoint_store = checkpoint_store
        self.session = session or SessionState()

    async def run(self, context: CapabilityContext) -> WorkflowLoopResult:
        max_steps = self.max_steps or context.limits.max_steps
        history: list[CapabilityResult] = []
        decisions: list[WorkflowStepDecision] = []

        for step_index in range(1, max_steps + 1):
            decision = await self.planner.plan(context, history, decisions)
            decisions.append(decision)
            self._trace(step_index, decision)
            self._checkpoint(context, "running", decisions, history)

            if decision.action == "finish":
                output = self._terminal_output(decision, history)
                self._checkpoint(context, "completed", decisions, history, output)
                return WorkflowLoopResult(
                    status="completed",
                    output=output,
                    decisions=decisions,
                    capability_results=history,
                )
            if decision.action == "ask_user":
                self._checkpoint(context, "waiting", decisions, history, decision.payload)
                return WorkflowLoopResult(
                    status="waiting",
                    output=decision.payload,
                    decisions=decisions,
                    capability_results=history,
                )
            if decision.action == "fail":
                self._checkpoint(context, "failed", decisions, history, decision.payload)
                return WorkflowLoopResult(
                    status="failed",
                    output=decision.payload,
                    decisions=decisions,
                    capability_results=history,
                    error=decision.rationale or "workflow supervisor failed",
                )
            if decision.action != "invoke":
                self._checkpoint(context, "failed", decisions, history)
                return WorkflowLoopResult(
                    status="failed",
                    decisions=decisions,
                    capability_results=history,
                    error=f"Unsupported workflow step action: {decision.action}",
                )
            if not decision.capability_name:
                self._checkpoint(context, "failed", decisions, history)
                return WorkflowLoopResult(
                    status="failed",
                    decisions=decisions,
                    capability_results=history,
                    error="Invoke decision did not specify capability_name",
                )

            result = await self.runtime.invoke(
                decision.capability_name,
                decision.payload,
                context,
                attempt=sum(1 for item in decisions if item.capability_name == decision.capability_name),
            )
            history.append(result)
            if result.metadata.get("waiting_for_user"):
                self._checkpoint(context, "waiting", decisions, history, result.output)
                return WorkflowLoopResult(
                    status="waiting",
                    output=result.output,
                    decisions=decisions,
                    capability_results=history,
                )
            if result.status == "failed" and context.plan and context.plan.fail_mode == "fail_closed":
                self._checkpoint(context, "failed", decisions, history, result.output)
                return WorkflowLoopResult(
                    status="failed",
                    decisions=decisions,
                    capability_results=history,
                    error=result.error or f"{decision.capability_name} failed",
                )

        self._checkpoint(context, "failed", decisions, history)
        return WorkflowLoopResult(
            status="failed",
            decisions=decisions,
            capability_results=history,
            error=f"Workflow step limit exhausted: {max_steps}",
        )

    @staticmethod
    def _terminal_output(decision: WorkflowStepDecision, history: list[CapabilityResult]) -> Any:
        if decision.payload is not None:
            return decision.payload
        if history:
            return history[-1].output
        return None

    def _trace(self, step_index: int, decision: WorkflowStepDecision) -> None:
        self.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node="workflow_supervisor_loop",
                attempt=step_index,
                decision=decision.action,
                metadata={
                    "capability_name": decision.capability_name,
                    "rationale": decision.rationale,
                    "confidence": decision.confidence,
                },
            )
        )

    def _checkpoint(
        self,
        context: CapabilityContext,
        status: str,
        decisions: list[WorkflowStepDecision],
        history: list[CapabilityResult],
        output: Any = None,
    ) -> None:
        if not self.checkpoint_store:
            return
        self.session.status = status
        self.session.step_index = len(decisions)
        self.session.data = {
            "decision_count": len(decisions),
            "capability_statuses": [result.status for result in history],
            "last_output": jsonable_checkpoint_data(output),
        }
        self.checkpoint_store.save(
            WorkflowCheckpoint(
                workflow_id=context.run_context.workflow_id,
                workflow_type=context.run_context.workflow_type,
                session_id=self.session.session_id,
                step_index=self.session.step_index,
                status=status,
                data=self.session.data,
            )
        )
