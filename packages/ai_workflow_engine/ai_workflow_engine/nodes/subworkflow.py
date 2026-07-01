"""Subworkflow-node handler: run a registered child workflow under the shared budget scope.

Mechanical split of the executor god-file (spec §2c#5). Handlers receive a narrow
``NodeExecutionServices`` boundary object (``services``) — never the executor itself.
"""
from __future__ import annotations
from typing import Any, Dict
from ai_workflow_engine.models import CapabilityContext, CapabilityResult, WorkflowTraceEvent
from ai_workflow_engine.workflow import WorkflowDefinition, WorkflowNode
from ai_workflow_engine._runtime_state import CONTEXT


def build_subworkflow_node(services, definition: WorkflowDefinition, node: WorkflowNode):
    ref = node.subworkflow

    async def subflow_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        context: CapabilityContext = state[CONTEXT]
        child_def = services.subworkflows.get(ref.workflow_id) if ref else None
        payload = services.node_input(state, node)
        if child_def is None:
            failed = CapabilityResult(
                status="failed",
                error=f"subworkflow '{node.id}' references unregistered workflow: {ref.workflow_id if ref else None}",
            )
            return services.record(state, node, failed, attempts=1, input_payload=payload)

        child_context = services.child_context(context, child_def, ref)
        child_result = await services.run_child(child_def, payload, child_context)
        ok = child_result.status in ("completed", "partial", "requires_user_input")
        cap = CapabilityResult(
            status="accepted" if ok else "failed",
            output=child_result.output,
            artifacts=child_result.artifacts,
            error=child_result.error,
            metadata={"child_workflow": child_def.workflow_id, "child_status": child_result.status},
        )
        update = services.record(state, node, cap, attempts=1, input_payload=payload)
        services.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                decision="subworkflow",
                error=None if ok else child_result.error,
                metadata={
                    "parent_workflow": definition.workflow_id,
                    "child_workflow": child_def.workflow_id,
                    "child_status": child_result.status,
                },
            )
        )
        return update

    return subflow_fn
