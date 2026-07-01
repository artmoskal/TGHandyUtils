"""Fanout-node handler: bounded parallel per-item execution with partial-failure isolation.

Mechanical split of the executor god-file (spec §2c#5). Handlers receive a narrow
``NodeExecutionServices`` boundary object (``services``) — never the executor itself.
"""
from __future__ import annotations
from typing import Any, Dict
from ai_workflow_engine.engine.capabilities import CapabilityCall, gather_capabilities
from ai_workflow_engine.models import CapabilityContext, CapabilityResult, WorkflowTraceEvent
from ai_workflow_engine.workflow import WorkflowDefinition, WorkflowNode
from ai_workflow_engine._runtime_state import CONTEXT, RUNNING_PAYLOAD


def build_fanout_node(services, definition: WorkflowDefinition, node: WorkflowNode):
    item_capability = node.item_capability or node.capability or node.id

    async def fanout_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        context: CapabilityContext = state[CONTEXT]
        items = _resolve_items(state, node)
        if not isinstance(items, list):
            failed = CapabilityResult(
                status="failed",
                error=f"fanout '{node.id}' items source '{node.fan_items_key}' is not a list",
            )
            return services.record(state, node, failed, attempts=1, input_payload=items)

        limit = node.max_parallel or (context.limits.max_parallel_children if context.limits else 4) or 4
        calls = [CapabilityCall(item_capability, item) for item in items]
        results = await gather_capabilities(services.runtime, calls, context, max_parallel=limit)
        outputs = [r.output for r in results if r.status in ("accepted", "partial") and r.output is not None]
        failures = [r for r in results if r.status not in ("accepted", "partial")]
        # Partial-failure isolation: parent keeps successes; a wholesale failure halts.
        if failures and not outputs:
            status = "failed"
        elif failures:
            status = "partial"
        else:
            status = "accepted"
        combined = CapabilityResult(
            status=status,
            output=outputs,
            artifacts=[artifact for r in results for artifact in r.artifacts],
            error="; ".join(f.error for f in failures if f.error) or None if failures else None,
            metadata={"total": len(items), "succeeded": len(outputs), "failed": len(failures)},
        )
        update = services.record(state, node, combined, attempts=1, input_payload=items)
        services.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                decision="fanout",
                metadata={
                    "total": len(items),
                    "succeeded": len(outputs),
                    "failed": len(failures),
                    "max_parallel": limit,
                },
            )
        )
        return update

    return fanout_fn

def _resolve_items(state: Dict[str, Any], node: WorkflowNode) -> Any:
    key = node.fan_items_key
    if not key:
        return state.get(RUNNING_PAYLOAD)
    parts = key.split(".")
    head = parts[0]
    value = state.get(RUNNING_PAYLOAD) if head == "payload" else state.get("node_outputs", {}).get(head)
    for attr in parts[1:]:
        if value is None:
            break
        value = value.get(attr) if isinstance(value, dict) else getattr(value, attr, None)
    return value
