"""Subworkflow-node handler: run a registered child workflow under the shared budget scope.

Mechanical split of the executor god-file (spec §2c#5). Functions take the
``WorkflowExecutor`` as ``executor`` and share its runtime/trace/record infrastructure.
"""
from __future__ import annotations
from typing import Any, Dict
from ai_workflow_engine.models import CapabilityContext, CapabilityResult, WorkflowTraceEvent
from ai_workflow_engine.workflow import WorkflowDefinition, WorkflowNode
from ai_workflow_engine._runtime_state import CONTEXT


def build_subworkflow_node(executor, definition: WorkflowDefinition, node: WorkflowNode):
    ref = node.subworkflow

    async def subflow_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        context: CapabilityContext = state[CONTEXT]
        child_def = executor.subworkflows.get(ref.workflow_id) if ref else None
        payload = executor._node_input(state, node)
        if child_def is None:
            failed = CapabilityResult(
                status="failed",
                error=f"subworkflow '{node.id}' references unregistered workflow: {ref.workflow_id if ref else None}",
            )
            return executor._record(state, node, failed, attempts=1, input_payload=payload)

        child_context = executor._child_context(context, child_def, ref)
        child_result = await executor._run_inner(child_def, payload, child_context)
        ok = child_result.status in ("completed", "partial", "requires_user_input")
        cap = CapabilityResult(
            status="accepted" if ok else "failed",
            output=child_result.output,
            artifacts=child_result.artifacts,
            error=child_result.error,
            metadata={"child_workflow": child_def.workflow_id, "child_status": child_result.status},
        )
        update = executor._record(state, node, cap, attempts=1, input_payload=payload)
        executor.runtime.trace_sink.record(
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
