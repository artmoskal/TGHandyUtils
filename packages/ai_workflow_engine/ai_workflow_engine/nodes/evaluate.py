"""Evaluate-node handler: evaluator gates with retry/retrace/replan/fallback policies.

Mechanical split of the executor god-file (spec §2c#5). Functions take the
``WorkflowExecutor`` as ``executor`` and share its runtime/trace/record infrastructure.
"""
from __future__ import annotations

from pydantic import BaseModel
from typing import Any, Dict, Optional
from ai_workflow_engine.models import CapabilityContext, CapabilityResult, CriticismEnvelope, EvaluationDecision, WorkflowTraceEvent
from ai_workflow_engine.planning import PlanArtifact
from ai_workflow_engine.workflow import Fallback, Replan, Retrace, Retry, WorkflowDefinition, WorkflowNode
from ai_workflow_engine.executor import _CONTEXT, _RUNNING_PAYLOAD


def build_evaluate_node(executor, definition: WorkflowDefinition, node: WorkflowNode):
    evaluator = node.evaluator or node.id

    async def evaluate_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        context: CapabilityContext = state[_CONTEXT]
        evaluated_payload = executor._node_input(state, node)
        attempt = state.get("attempts", {}).get(node.id, 0) + 1
        eval_result = await executor._invoke_bound(node, evaluator, evaluated_payload, context, state, attempt=attempt)
        decision = _eval_decision(executor, node, eval_result)
        counters = dict(
            state.get("eval_counters", {}).get(
                node.id, {"retry": 0, "retrace": 0, "replan": 0}
            )
        )
        counters.setdefault("retry", 0)
        counters.setdefault("retrace", 0)
        counters.setdefault("replan", 0)
        effect = await _apply_eval(executor, node, decision, counters, context, definition, state, evaluated_payload)

        cap_status = (
            "accepted"
            if effect["status"] == "accepted"
            else ("failed" if effect["status"] == "failed" else "rejected")
        )
        combined = CapabilityResult(
            status=cap_status,
            output=effect["output"],
            artifacts=effect.get("artifacts", []),
            error=effect.get("error"),
        )
        update = executor._record(
            state,
            node,
            combined,
            attempts=attempt,
            input_payload=evaluated_payload,
            force_status=effect["status"],
            error=effect.get("error"),
            fallback_reason=effect.get("fallback_reason"),
        )
        update["routes"] = {**state.get("routes", {}), node.id: effect["route"]}
        update["eval_counters"] = {**state.get("eval_counters", {}), node.id: counters}
        update[_RUNNING_PAYLOAD] = effect["output"]
        if effect["status"] == "requires_user_input":
            update["status"] = "requires_user_input"
        executor.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                attempt=attempt,
                decision=effect["route"],
                error=effect.get("error"),
                metadata={
                    "action": decision.action,
                    "retry": counters["retry"],
                    "retrace": counters["retrace"],
                    "replan": counters["replan"],
                    "fallback_reason": effect.get("fallback_reason"),
                },
            )
        )
        return update

    return evaluate_fn

def _eval_decision(executor, node: WorkflowNode, eval_result: CapabilityResult) -> EvaluationDecision:
    from ai_workflow_engine.workflow import Fallback, Replan, Retrace, Retry

    output = eval_result.output
    if isinstance(output, EvaluationDecision):
        return output
    if eval_result.status == "accepted":
        return EvaluationDecision(action="accept")
    if eval_result.status == "failed":
        return EvaluationDecision(action="fail", rationale=eval_result.error or "evaluator failed")
    # rejected / partial -> derive the corrective action from the declared on_reject policy.
    criticism = _criticism_from(eval_result)
    on = node.on_reject
    if isinstance(on, Retry):
        return EvaluationDecision(action="retry_capability", criticism=criticism, rationale=eval_result.error or "")
    if isinstance(on, Retrace):
        return EvaluationDecision(action="retrace_to", retrace_to=on.target, criticism=criticism, rationale=eval_result.error or "")
    if isinstance(on, Replan):
        return EvaluationDecision(action="replan", retrace_to=on.target, criticism=criticism, rationale=eval_result.error or "")
    if isinstance(on, Fallback):
        return EvaluationDecision(action="fallback", target_capability=on.capability, criticism=criticism)
    if node.fallback_capability:
        return EvaluationDecision(action="fallback", target_capability=node.fallback_capability, criticism=criticism)
    return EvaluationDecision(action="fail", rationale=eval_result.error or "evaluation rejected")

def _criticism_from(eval_result: CapabilityResult) -> Optional[CriticismEnvelope]:
    meta = eval_result.metadata or {}
    crit = meta.get("criticism")
    if isinstance(crit, CriticismEnvelope):
        return crit
    if isinstance(crit, dict):
        return CriticismEnvelope(**crit)
    if isinstance(crit, str):
        return CriticismEnvelope(observed=crit, expected="valid output per rubric")
    if eval_result.error:
        return CriticismEnvelope(observed=eval_result.error, expected="valid output per rubric")
    return None

async def _apply_eval(
    executor,
    node: WorkflowNode,
    decision: EvaluationDecision,
    counters: Dict[str, int],
    context: CapabilityContext,
    definition: WorkflowDefinition,
    state: Dict[str, Any],
    evaluated_payload: Any,
) -> Dict[str, Any]:
    from ai_workflow_engine.workflow import Replan, Retrace, Retry

    limits = context.limits
    action = decision.action

    # Bound retry/retrace; exhaustion downgrades to fallback (if any) else fail — never loops.
    if action in ("retry_capability", "repair"):
        max_retry = node.on_reject.max_attempts if isinstance(node.on_reject, Retry) else ((limits.max_retries + 1) if limits else 2)
        if counters["retry"] + 1 > max_retry:
            action = "exhausted"
    elif action == "retrace_to":
        max_retrace = node.on_reject.max_retrace if isinstance(node.on_reject, Retrace) else (limits.max_retrace if limits else 1)
        if counters["retrace"] + 1 > max_retrace:
            action = "exhausted"
    elif action == "replan":
        max_replans = node.on_reject.max_replans if isinstance(node.on_reject, Replan) else 1
        if counters["replan"] + 1 > max_replans:
            action = "exhausted"

    if action == "exhausted":
        if node.fallback_capability:
            action = "fallback"
            decision = EvaluationDecision(
                action="fallback",
                target_capability=node.fallback_capability,
                criticism=decision.criticism,
                rationale="evaluation policy exhausted",
            )
        else:
            return {"route": "halt", "status": "failed", "output": evaluated_payload, "error": "evaluation policy exhausted; no fallback"}

    if action == "accept":
        return {"route": "accept", "status": "accepted", "output": evaluated_payload}
    if action in ("retry_capability", "repair"):
        counters["retry"] += 1
        pred = executor._sequential_predecessor(definition, node.id)
        base = state.get("node_inputs", {}).get(pred, evaluated_payload)
        return {"route": "retry", "status": "rejected", "output": _with_criticism(base, decision.criticism), "error": decision.rationale}
    if action == "retrace_to":
        counters["retrace"] += 1
        target = decision.retrace_to or (node.on_reject.target if isinstance(node.on_reject, Retrace) else None)
        base = state.get("node_inputs", {}).get(target, evaluated_payload)
        return {"route": "retrace", "status": "rejected", "output": _with_criticism(base, decision.criticism), "error": decision.rationale}
    if action == "replan":
        counters["replan"] += 1
        target = decision.retrace_to or (node.on_reject.target if isinstance(node.on_reject, Replan) else None)
        base = state.get("node_inputs", {}).get(target, evaluated_payload)
        payload = {
            "input": base,
            "plan_artifact": _dump_plan_for_payload(state.get("plan_artifact")),
        }
        return {
            "route": "replan",
            "status": "rejected",
            "output": _with_criticism(payload, decision.criticism),
            "error": decision.rationale,
        }
    if action == "fallback":
        target = decision.target_capability or node.fallback_capability
        fb = await executor.runtime.invoke(target, _with_criticism(evaluated_payload, decision.criticism), context)
        if fb.status == "failed":
            return {"route": "halt", "status": "failed", "output": evaluated_payload, "error": fb.error or "fallback failed"}
        return {
            "route": "accept",
            "status": "accepted",
            "output": fb.output,
            "fallback_reason": decision.rationale or f"fallback:{target}",
            "artifacts": fb.artifacts,
        }
    if action == "ask_user":
        return {"route": "halt", "status": "requires_user_input", "output": evaluated_payload, "error": decision.rationale}
    return {"route": "halt", "status": "failed", "output": evaluated_payload, "error": decision.rationale or "evaluation failed"}

def _with_criticism(payload: Any, criticism: Optional[CriticismEnvelope]) -> Any:
    if not criticism:
        return payload
    crit = criticism.model_dump() if isinstance(criticism, CriticismEnvelope) else criticism
    if isinstance(payload, BaseModel):
        data = payload.model_dump()
        data["_criticism"] = crit
        return data
    if isinstance(payload, dict):
        return {**payload, "_criticism": crit}
    return {"value": payload, "_criticism": crit}

def _dump_plan_for_payload(plan: Any) -> Any:
    if isinstance(plan, PlanArtifact):
        return plan.model_dump()
    if isinstance(plan, dict):
        return plan
    return None
