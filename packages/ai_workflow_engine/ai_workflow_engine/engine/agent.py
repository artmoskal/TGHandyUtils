"""Bounded agent capability with scoped tool execution."""

from __future__ import annotations

import inspect
from typing import Awaitable, Protocol

from ai_workflow_engine.engine.capabilities import CapabilityRuntime
from ai_workflow_engine.models import (
    AgentRunRequest,
    AgentRunResult,
    AgentStepDecision,
    AgentToolCall,
    AgentToolStep,
    CapabilityContext,
    CapabilityResult,
    CapabilitySpec,
    WorkflowTraceEvent,
)


class AgentEpisodePlanner(Protocol):
    """Controller that chooses the next action for one bounded agent episode."""

    def next_step(
        self,
        context: CapabilityContext,
        request: AgentRunRequest,
        history: list[AgentToolStep],
    ) -> AgentStepDecision | Awaitable[AgentStepDecision]:
        ...


class AgentCapability:
    """Run an agent controller through registered, bounded workflow tools."""

    def __init__(
        self,
        planner: AgentEpisodePlanner,
        tool_runtime: CapabilityRuntime,
        *,
        name: str = "agent_episode",
        side_effects: tuple[str, ...] = (),
    ) -> None:
        self.planner = planner
        self.tool_runtime = tool_runtime
        self.spec = CapabilitySpec(
            name=name,
            kind="agent",
            description="Run a bounded agent episode over scoped workflow tools.",
            input_model=AgentRunRequest,
            output_model=AgentRunResult,
            side_effects=list(side_effects),
            metered=False,
        )

    async def __call__(
        self,
        context: CapabilityContext,
        request: AgentRunRequest,
    ) -> CapabilityResult:
        max_steps = self._effective_cap(request.max_steps, context.limits.max_steps)
        max_tool_calls = self._effective_cap(request.max_tool_calls, max_steps)
        allowed_tools = set(request.allowed_tools)
        history: list[AgentToolStep] = []
        tool_calls = 0

        for step_index in range(1, max_steps + 1):
            decision = self.planner.next_step(context, request, history)
            if inspect.isawaitable(decision):
                decision = await decision
            self._trace(
                step_index,
                decision.action,
                {
                    "tool_name": decision.tool_name,
                    "rationale": decision.rationale,
                    "subscription_call": request.subscription_mode,
                },
            )

            if decision.action == "finish":
                return self._result("accepted", "completed", history, request, decision.output)
            if decision.action == "fail":
                error = decision.rationale or "agent episode failed"
                return self._result("failed", "failed", history, request, decision.output, error=error)
            if decision.action != "tool":
                return self._result(
                    "failed",
                    "failed",
                    history,
                    request,
                    None,
                    error=f"unsupported agent action: {decision.action}",
                )
            if not decision.tool_name:
                return self._result(
                    "failed",
                    "failed",
                    history,
                    request,
                    None,
                    error="agent tool decision did not specify tool_name",
                )
            if decision.tool_name not in allowed_tools:
                error = f"agent tool denied by allow-list: {decision.tool_name}"
                self._trace(step_index, "rejected", {"denied_tool": decision.tool_name}, error=error)
                return self._result(
                    "rejected",
                    "failed",
                    history,
                    request,
                    None,
                    error=error,
                    metadata={"denied_tool": decision.tool_name},
                )
            if tool_calls >= max_tool_calls:
                return self._result(
                    "partial",
                    "truncated",
                    history,
                    request,
                    None,
                    error=f"agent tool-call limit exhausted: {max_tool_calls}",
                )

            tool_calls += 1
            tool_result = await self.tool_runtime.invoke(
                decision.tool_name,
                decision.payload,
                context,
                attempt=tool_calls,
            )
            history.append(
                AgentToolStep(
                    call=AgentToolCall(
                        tool_name=decision.tool_name,
                        payload=decision.payload,
                        rationale=decision.rationale,
                    ),
                    status=tool_result.status,
                    output=tool_result.output,
                    error=tool_result.error,
                )
            )
            if tool_result.status in {"failed", "rejected"} and context.plan and context.plan.fail_mode == "fail_closed":
                return self._result(
                    "failed",
                    "failed",
                    history,
                    request,
                    None,
                    error=tool_result.error or f"agent tool failed: {decision.tool_name}",
                )

        return self._result(
            "partial",
            "truncated",
            history,
            request,
            None,
            error=f"agent step limit exhausted: {max_steps}",
        )

    @staticmethod
    def _effective_cap(requested: int | None, fallback: int) -> int:
        if requested is None:
            return max(1, fallback)
        return max(1, min(requested, fallback))

    def _result(
        self,
        capability_status: str,
        run_status: str,
        history: list[AgentToolStep],
        request: AgentRunRequest,
        output: object,
        *,
        error: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> CapabilityResult:
        result = AgentRunResult(
            status=run_status,
            steps=history,
            output=output,
            subscription_call=request.subscription_mode,
            estimated_metered_usd=0.0 if request.subscription_mode else None,
            metadata={
                **request.metadata,
                "tool_call_count": len(history),
                **dict(metadata or {}),
            },
        )
        return CapabilityResult(
            status=capability_status,
            output=result,
            error=error,
            metadata={
                "agent_status": run_status,
                "tool_call_count": len(history),
                "subscription_call": request.subscription_mode,
                **dict(metadata or {}),
            },
        )

    def _trace(
        self,
        attempt: int,
        decision: str,
        metadata: dict[str, object],
        *,
        error: str | None = None,
    ) -> None:
        self.tool_runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=self.spec.name,
                attempt=attempt,
                decision=decision,
                error=error,
                metadata=metadata,
            )
        )
