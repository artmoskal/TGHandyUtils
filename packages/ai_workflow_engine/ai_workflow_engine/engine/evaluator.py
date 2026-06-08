"""Reusable evaluator-driven retry/retrace controller."""

from __future__ import annotations

import inspect
from typing import Any, Protocol

from pydantic import BaseModel

from ai_workflow_engine.engine.capabilities import CapabilityRuntime
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    CriticismEnvelope,
    EvaluationDecision,
    WorkflowLoopResult,
    WorkflowStepDecision,
    WorkflowTraceEvent,
)


class EvaluationPlanner(Protocol):
    """Product-owned quality evaluator used by the generic evaluation controller."""

    async def evaluate(
        self,
        context: CapabilityContext,
        latest: CapabilityResult,
        history: list[CapabilityResult],
    ) -> EvaluationDecision:
        """Return the next quality-control action."""


class EvaluationController:
    """Run a capability under bounded evaluator retry/retrace/fallback policy."""

    def __init__(
        self,
        runtime: CapabilityRuntime,
        evaluator: EvaluationPlanner,
        *,
        fallback_capability: str | None = None,
        max_rounds: int | None = None,
    ) -> None:
        self.runtime = runtime
        self.evaluator = evaluator
        self.fallback_capability = fallback_capability
        self.max_rounds = max_rounds

    async def run(
        self,
        capability_name: str,
        payload: Any,
        context: CapabilityContext,
        *,
        retrace_payloads: dict[str, Any] | None = None,
    ) -> WorkflowLoopResult:
        max_rounds = self.max_rounds or max(1, context.limits.max_retries + context.limits.max_retrace + 1)
        history: list[CapabilityResult] = []
        decisions: list[WorkflowStepDecision] = []
        attempts_by_capability: dict[str, int] = {}
        current_name = capability_name
        current_payload = payload
        retrace_payloads = retrace_payloads or {}

        for round_index in range(1, max_rounds + 1):
            attempts_by_capability[current_name] = attempts_by_capability.get(current_name, 0) + 1
            latest = await self.runtime.invoke(
                current_name,
                current_payload,
                context,
                attempt=attempts_by_capability[current_name],
            )
            history.append(latest)
            decision = await self._evaluate(context, latest, history)
            self._trace(round_index, decision)

            if decision.action == "accept":
                return WorkflowLoopResult(status="completed", output=latest.output, decisions=decisions, capability_results=history)
            if decision.action == "ask_user":
                return WorkflowLoopResult(status="waiting", output=decision, decisions=decisions, capability_results=history)
            if decision.action == "fail":
                return WorkflowLoopResult(
                    status="failed",
                    output=latest.output,
                    decisions=decisions,
                    capability_results=history,
                    error=decision.rationale or latest.error or "evaluation failed",
                )
            if decision.action in {"fallback", "repair"}:
                target = decision.target_capability or self.fallback_capability
                if not target:
                    return WorkflowLoopResult(
                        status="failed",
                        output=latest.output,
                        decisions=decisions,
                        capability_results=history,
                        error="evaluation requested fallback without target capability",
                    )
                fallback_payload = self._payload_with_criticism(payload, decision.criticism)
                fallback = await self.runtime.invoke(target, fallback_payload, context)
                history.append(fallback)
                return WorkflowLoopResult(status="completed", output=fallback.output, decisions=decisions, capability_results=history)
            if decision.action == "retry_capability":
                target = decision.target_capability or current_name
                if attempts_by_capability.get(target, 0) >= context.limits.max_retries + 1:
                    return self._exhausted(history, decisions, decision)
                current_name = target
                current_payload = self._payload_with_criticism(current_payload, decision.criticism)
                decisions.append(WorkflowStepDecision(action="invoke", capability_name=current_name, payload=current_payload, rationale=decision.rationale))
                continue
            if decision.action == "retrace_to":
                target = decision.retrace_to or decision.target_capability
                if not target:
                    return self._exhausted(history, decisions, decision)
                retrace_count = sum(1 for item in decisions if item.capability_name == target)
                if retrace_count >= context.limits.max_retrace:
                    return self._exhausted(history, decisions, decision)
                current_name = target
                current_payload = self._payload_with_criticism(retrace_payloads.get(target, payload), decision.criticism)
                decisions.append(WorkflowStepDecision(action="invoke", capability_name=current_name, payload=current_payload, rationale=decision.rationale))
                continue

            return WorkflowLoopResult(
                status="failed",
                output=latest.output,
                decisions=decisions,
                capability_results=history,
                error=f"unsupported evaluation action: {decision.action}",
            )

        return WorkflowLoopResult(
            status="failed",
            decisions=decisions,
            capability_results=history,
            error=f"evaluation round limit exhausted: {max_rounds}",
        )

    async def _evaluate(
        self,
        context: CapabilityContext,
        latest: CapabilityResult,
        history: list[CapabilityResult],
    ) -> EvaluationDecision:
        decision = self.evaluator.evaluate(context, latest, history)
        if inspect.isawaitable(decision):
            decision = await decision
        return decision

    def _trace(self, round_index: int, decision: EvaluationDecision) -> None:
        self.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node="workflow_evaluator",
                attempt=round_index,
                decision=decision.action,
                metadata={
                    "target_capability": decision.target_capability,
                    "retrace_to": decision.retrace_to,
                    "rationale": decision.rationale,
                    "criticism": decision.criticism.model_dump() if decision.criticism else None,
                },
            )
        )

    @staticmethod
    def _payload_with_criticism(payload: Any, criticism: CriticismEnvelope | None) -> Any:
        if not criticism:
            return payload
        criticism_payload = criticism.model_dump()
        if isinstance(payload, BaseModel):
            data = payload.model_dump()
            data["_criticism"] = criticism_payload
            return data
        if isinstance(payload, dict):
            return {**payload, "_criticism": criticism_payload}
        return {"value": payload, "_criticism": criticism_payload}

    @staticmethod
    def _exhausted(
        history: list[CapabilityResult],
        decisions: list[WorkflowStepDecision],
        decision: EvaluationDecision,
    ) -> WorkflowLoopResult:
        return WorkflowLoopResult(
            status="failed",
            output=history[-1].output if history else None,
            decisions=decisions,
            capability_results=history,
            error=decision.rationale or "evaluation policy exhausted",
        )
