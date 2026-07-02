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
        if child_result.status == "requires_user_input":
            # B2 stage-1: a child wait has no parent-side representation (no nested snapshot,
            # no resume dispatch). Converting it to success would silently lose the wait —
            # fail LOUDLY instead. Preflight already rejects declared human nodes in children;
            # this catches runtime suspensions (e.g. an evaluator ask_user inside the child).
            error = (
                f"child workflow '{child_def.workflow_id}' suspended (requires_user_input): "
                f"nested suspension is not supported — move the wait to the top-level workflow"
            )
            failed = CapabilityResult(
                status="failed",
                output=child_result.output,
                error=error,
                metadata={"child_workflow": child_def.workflow_id, "child_status": child_result.status},
            )
            update = services.record(
                state, node, failed, attempts=1, input_payload=payload,
                force_status="failed", error=error,
            )
            services.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=node.id,
                    decision="subworkflow",
                    error=error,
                    severity="error",
                    metadata={
                        "parent_workflow": definition.workflow_id,
                        "child_workflow": child_def.workflow_id,
                        "child_status": child_result.status,
                    },
                )
            )
            return update
        ok = child_result.status in ("completed", "partial")
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
