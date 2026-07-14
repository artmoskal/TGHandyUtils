"""Resume fast-forward and typed retrace delivery for compiled node callables."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from ai_workflow_engine._runtime_state import _ACTIVE_RETRACE_PROVENANCE
from ai_workflow_engine.models import WorkflowTraceEvent
from ai_workflow_engine.workflow import WorkflowNode


NodeCallable = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class NodeReplayRuntime:
    """Wrap node callables with resume replay and one-shot retrace context."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def wrap(self, node: WorkflowNode, fn: NodeCallable) -> NodeCallable:
        async def replay_aware(state: Dict[str, Any]) -> Dict[str, Any]:
            suspended = state.get("resume_suspended_node")
            if suspended and not state.get("machine_replay_done"):
                if node.id != suspended:
                    self._runtime.trace_sink.record(
                        WorkflowTraceEvent(
                            node=node.id,
                            decision="machine:fastforward",
                            metadata={"status": state.get("node_status", {}).get(node.id)},
                        )
                    )
                    return {}
                update = await self._run_with_retrace_provenance(node, fn, state)
                update["machine_replay_done"] = True
                return update
            return await self._run_with_retrace_provenance(node, fn, state)

        return replay_aware

    @staticmethod
    async def _run_with_retrace_provenance(
        node: WorkflowNode,
        fn: NodeCallable,
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        pending = state.get("pending_retrace_provenance")
        if not (isinstance(pending, dict) and pending.get("target") == node.id):
            return await fn(state)
        token = _ACTIVE_RETRACE_PROVENANCE.set(pending.get("provenance"))
        try:
            update = await fn(state)
        finally:
            _ACTIVE_RETRACE_PROVENANCE.reset(token)
        update["pending_retrace_provenance"] = None
        return update
