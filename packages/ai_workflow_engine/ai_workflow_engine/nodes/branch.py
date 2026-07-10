"""Branch-node handler: decider invocation, pre-set loop gates, transition trace.

Mechanical split of the executor god-file (spec §2c#5). Handlers receive a narrow
``NodeExecutionServices`` boundary object (``services``) — never the executor itself.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, Optional
from ai_workflow_engine.models import CapabilityContext, CapabilityResult, WorkflowTraceEvent
from ai_workflow_engine.workflow import BranchDecision, WorkflowDefinition, WorkflowNode
from ai_workflow_engine._runtime_state import CONTEXT, RUNNING_PAYLOAD

logger = logging.getLogger(__name__)


def build_branch_node(services, definition: WorkflowDefinition, node: WorkflowNode):
    decider = node.decider or node.capability or node.id
    gates = {
        t.label: t
        for t in definition.outgoing(node.id)
        if t.policy == "decision" and t.max_traversals is not None
    }

    async def branch_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        context: CapabilityContext = state[CONTEXT]
        payload = services.node_input(state, node)
        attempt = state.get("attempts", {}).get(node.id, 0) + 1
        result = await services.invoke_bound(node, decider, payload, context, state, attempt=attempt, definition=definition)
        label = _extract_label(result)
        valid = label in node.branches
        try:
            decision_policy = services.runtime.registry.get(decider)[0].kind
        except KeyError:
            # Trace metadata only (never control flow). Reachable only when the registry
            # changed between invoke and lookup (custom runtimes); anything OTHER than a
            # missing entry is an engine bug and now propagates instead of hiding.
            logger.debug("branch '%s': decider %r not in registry for trace metadata", node.id, decider)
            decision_policy = None

        # Pre-set loop gates: a bounded decision transition counts its traversals; exceeding
        # the bound either fails loudly or takes the declared escape label.
        taken = label
        error: Optional[str] = None if valid else f"branch '{node.id}' produced invalid label: {label!r}"
        counts = dict(state.get("transition_counts", {}))
        exhausted_gate = None
        if valid and label in gates:
            gate = gates[label]
            key = f"{node.id}|{label}"
            seen = counts.get(key, 0)
            if seen >= gate.max_traversals:
                exhausted_gate = gate
                if gate.on_exhausted == "fail":
                    taken = "__halt__"
                    error = (
                        f"branch '{node.id}' label '{label}' exceeded its pre-set gate "
                        f"max_traversals={gate.max_traversals} (on_exhausted=fail)"
                    )
                else:
                    taken = gate.on_exhausted
            else:
                counts[key] = seen + 1
        failed = (not valid) or taken == "__halt__"
        update = services.record(
            state,
            node,
            result,
            attempts=attempt,
            input_payload=payload,
            branch_label=taken if valid and taken != "__halt__" else label,
            force_status="failed" if failed else None,
            error=error,
        )
        update["branch_decisions"] = {
            **state.get("branch_decisions", {}),
            node.id: taken if valid and taken != "__halt__" else label,
        }
        update["routes"] = {**state.get("routes", {}), node.id: taken if valid else "__invalid__"}
        update["transition_counts"] = counts
        # A branch is a routing decision, not a transform: the running payload passes through
        # unchanged so the selected downstream node sees the real data, not the decision.
        update[RUNNING_PAYLOAD] = payload
        services.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                attempt=attempt,
                decision="branch",
                error=update.get("error") if failed else None,
                metadata={"label": label, "valid": valid, "decision_policy": decision_policy},
            )
        )
        if exhausted_gate is not None:
            services.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=node.id,
                    decision="transition:exhausted",
                    error=error if taken == "__halt__" else None,
                    metadata={
                        "label": label,
                        "max_traversals": exhausted_gate.max_traversals,
                        "rerouted_to": None if taken == "__halt__" else taken,
                    },
                )
            )
        elif valid:
            services.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=node.id,
                    decision="transition:taken",
                    metadata={
                        "label": taken,
                        "policy": "decision",
                        "decision_policy": decision_policy,
                        "traversals": counts.get(f"{node.id}|{label}"),
                    },
                )
            )
        return update

    return branch_fn

def _extract_label(result: CapabilityResult) -> Optional[str]:
    output = result.output
    if isinstance(output, BranchDecision):
        return output.label
    if isinstance(output, str):
        return output
    if isinstance(output, dict) and "label" in output:
        return output["label"]
    if hasattr(output, "label"):
        return getattr(output, "label")
    return None

# ---------------------------------------------------------------- state helpers
