"""Human-node handler: clarification waits, suspension, resume-event injection.

Mechanical split of the executor god-file (spec §2c#5). Handlers receive a narrow
``NodeExecutionServices`` boundary object (``services``) — never the executor itself.
"""
from __future__ import annotations
from typing import Any, Dict
from ai_workflow_engine.models import CapabilityContext, CapabilityResult, WorkflowTraceEvent
from ai_workflow_engine.workflow import WorkflowDefinition, WorkflowNode
from ai_workflow_engine._runtime_state import CONTEXT


def build_human_node(services, definition: WorkflowDefinition, node: WorkflowNode):
    capability = node.capability or node.id

    async def human_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        context: CapabilityContext = state[CONTEXT]
        payload = services.node_input(state, node)
        resume_event = state.get("resume_event")
        if (
            resume_event is not None
            and state.get("resume_suspended_node") == node.id
            and not state.get("machine_replay_done")
        ):
            # Resuming THIS suspension: the clarification capability sees the event and
            # decides whether it answers the question (engine injects, product interprets).
            context = context.model_copy(
                update={"metadata": {**context.metadata, "resume_event": resume_event}}
            )
        result = await services.runtime.invoke(capability, payload, context)
        response = result.output
        clarification_status = getattr(response, "status", None)
        # answered -> continue with the value; provisional -> continue (partial); pending -> pause.
        if clarification_status == "pending":
            node_status, route = "requires_user_input", "halt"
        elif clarification_status == "provisional":
            node_status, route = "partial", "continue"
        elif result.status == "failed":
            node_status, route = "failed", "halt"
        else:
            node_status, route = "accepted", "continue"
        cap = CapabilityResult(
            status="accepted" if node_status in ("accepted", "partial", "requires_user_input") else "failed",
            output=response,
            error=result.error,
        )
        update = services.record(
            state, node, cap, attempts=1, input_payload=payload, force_status=node_status, error=result.error
        )
        update["routes"] = {**state.get("routes", {}), node.id: route}
        if node_status == "requires_user_input":
            update["status"] = "requires_user_input"
        services.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                decision=clarification_status or "human",
                metadata={"clarification_status": clarification_status},
            )
        )
        return update

    return human_fn
