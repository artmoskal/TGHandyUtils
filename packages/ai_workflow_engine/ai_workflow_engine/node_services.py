"""Node execution and scheduling services used by graph-node handlers.

Node handlers must not depend on the full ``WorkflowExecutor`` object: they receive a
``NodeExecutionServices`` implementation exposing ONLY the operations a node is allowed to
use — read its input, invoke a capability under the bound context, record its result,
route trace/observation events, consult the scheduler through a narrow facade, and (for
subworkflow nodes) resolve + run a registered child workflow. Everything else on the
executor is private by contract; the executor<->nodes coupling guard enforces this.

The concrete services own their behavior and state directly. They never retain a workflow
executor. The sole callback into lifecycle coordination is the typed child-run port used by
subworkflow nodes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, Tuple, runtime_checkable

from ai_workflow_engine._runtime_state import (
    _ACTIVE_RETRACE_PROVENANCE,
    RUNNING_PAYLOAD,
)
from ai_workflow_engine.engine.scheduler import WorkflowScheduler
from ai_workflow_engine.engine.capabilities import CapabilityCall, gather_capability_calls
from ai_workflow_engine.model_binding import model_profile_scope
from ai_workflow_engine.usage_events import capture_usage_events
from ai_workflow_engine.models import CapabilityContext, CapabilityResult, WorkflowTraceEvent
from ai_workflow_engine.planning import PlanArtifact, render_plan
from ai_workflow_engine.workflow import (
    WorkflowDefinition,
    WorkflowNode,
    render_machine_card,
)


@runtime_checkable
class NodeSchedulingServices(Protocol):
    """Narrow scheduling facade for nodes with a ``scheduling.backend_key`` lane."""

    def submit(self, *, key: str, run_id: str, payload: Any, policy: Any) -> Any: ...

    def complete(self, *, key: str, run_id: str) -> None: ...

    def active_run_id(self, key: str) -> Optional[str]: ...

    def register_task(self, key: str, run_id: str, task: "asyncio.Task[Any]") -> None: ...

    def registered_task(self, key: str) -> Optional[Tuple[str, "asyncio.Task[Any]"]]: ...

    def unregister_task(self, key: str, run_id: str) -> None: ...

    def mark_superseded(self, key: str, run_id: str, superseding_run_id: str) -> None: ...

    def pop_superseded(self, key: str, run_id: str) -> Optional[str]: ...


@runtime_checkable
class NodeExecutionServices(Protocol):
    """Everything a node handler may use. Nothing else on the executor is node-visible."""

    @property
    def runtime(self) -> Any: ...  # CapabilityRuntime: invoke/registry/trace_sink/observation

    @property
    def subworkflows(self) -> Dict[str, WorkflowDefinition]: ...

    @property
    def scheduling(self) -> NodeSchedulingServices: ...

    def node_input(self, state: Dict[str, Any], node: WorkflowNode) -> Any: ...

    def record(
        self,
        state: Dict[str, Any],
        node: WorkflowNode,
        result: CapabilityResult,
        *,
        attempts: int,
        input_payload: Any = None,
        branch_label: Optional[str] = None,
        force_status: Optional[str] = None,
        error: Optional[str] = None,
        fallback_reason: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    async def invoke_bound(
        self,
        node: WorkflowNode,
        capability: str,
        payload: Any,
        context: CapabilityContext,
        state: Dict[str, Any],
        *,
        attempt: int = 1,
        definition: Optional[WorkflowDefinition] = None,
        fanout_item_index: Optional[int] = None,
    ) -> CapabilityResult: ...

    async def gather_bound(
        self,
        node: WorkflowNode,
        calls: list[CapabilityCall],
        context: CapabilityContext,
        state: Dict[str, Any],
        *,
        max_parallel: int,
        definition: Optional[WorkflowDefinition] = None,
    ) -> list[CapabilityResult]: ...

    def context_for_node(
        self,
        node: WorkflowNode,
        context: CapabilityContext,
        state: Dict[str, Any],
        *,
        plan: Any = None,
        definition: Optional[WorkflowDefinition] = None,
    ) -> CapabilityContext: ...

    def sequential_predecessor(
        self, definition: WorkflowDefinition, node_id: str
    ) -> Optional[str]: ...

    def child_context(
        self, parent: CapabilityContext, child_def: WorkflowDefinition, ref: Any
    ) -> CapabilityContext: ...

    async def run_child(
        self, definition: WorkflowDefinition, payload: Any, context: CapabilityContext
    ) -> Any: ...  # WorkflowRunResult


@runtime_checkable
class ChildRunPort(Protocol):
    """The one allowed callback from node execution into lifecycle coordination."""

    def __call__(
        self,
        definition: WorkflowDefinition,
        payload: Any,
        context: CapabilityContext,
    ) -> Awaitable[Any]: ...


def build_child_context(
    parent: CapabilityContext,
    child_definition: WorkflowDefinition,
    reference: Any,
) -> CapabilityContext:
    """Project a parent context into one child workflow without opening a new budget."""

    plan = parent.plan
    if plan is not None and reference is not None and (
        reference.budget_usd is not None or reference.max_steps is not None
    ):
        limit_update: Dict[str, Any] = {}
        if reference.budget_usd is not None:
            limit_update["max_estimated_usd"] = reference.budget_usd
        if reference.max_steps is not None:
            limit_update["max_steps"] = reference.max_steps
        plan = plan.model_copy(
            update={"limits": plan.limits.model_copy(update=limit_update)}
        )
    goal = parent.goal.model_copy(update={"workflow_type": child_definition.workflow_id})
    return CapabilityContext(
        goal=goal,
        run_context=parent.run_context,
        plan=plan,
        usage_summary=parent.usage_summary,
        limits=plan.limits if plan is not None else parent.limits,
        metadata=dict(parent.metadata),
    )


class NodeSchedulingRuntime:
    """Own the shared scheduler and active/supersession task bookkeeping."""

    def __init__(self, scheduler: Optional[WorkflowScheduler] = None) -> None:
        self.scheduler = scheduler or WorkflowScheduler()
        self._tasks: Dict[str, tuple[str, asyncio.Task[Any]]] = {}
        self._cancellations: Dict[tuple[str, str], str] = {}

    def submit(self, *, key: str, run_id: str, payload: Any, policy: Any) -> Any:
        return self.scheduler.submit(key=key, run_id=run_id, payload=payload, policy=policy)

    def complete(self, *, key: str, run_id: str) -> None:
        self.scheduler.complete(key=key, run_id=run_id)

    def active_run_id(self, key: str) -> Optional[str]:
        return self.scheduler.active_run_id(key)

    def register_task(self, key: str, run_id: str, task: "asyncio.Task[Any]") -> None:
        self._tasks[key] = (run_id, task)

    def registered_task(self, key: str) -> Optional[Tuple[str, "asyncio.Task[Any]"]]:
        return self._tasks.get(key)

    def unregister_task(self, key: str, run_id: str) -> None:
        registered = self._tasks.get(key)
        if registered is not None and registered[0] == run_id:
            self._tasks.pop(key, None)

    def mark_superseded(self, key: str, run_id: str, superseding_run_id: str) -> None:
        self._cancellations[(key, run_id)] = superseding_run_id

    def pop_superseded(self, key: str, run_id: str) -> Optional[str]:
        return self._cancellations.pop((key, run_id), None)


class ExecutorNodeServices:
    """Concrete node behavior with explicit runtime and child-lifecycle dependencies."""

    def __init__(
        self,
        *,
        runtime: Any,
        subworkflows: Dict[str, WorkflowDefinition],
        model_profiles: Dict[str, Any],
        child_run: ChildRunPort,
        node_result_factory: Callable[..., Any],
        capability_binding_error: Callable[[str], Exception],
        scheduling: Optional[NodeSchedulingRuntime] = None,
    ) -> None:
        self._runtime = runtime
        self._subworkflows = subworkflows
        self._model_profiles = model_profiles
        self._child_run = child_run
        self._node_result_factory = node_result_factory
        self._capability_binding_error = capability_binding_error
        self.scheduling: NodeSchedulingServices = scheduling or NodeSchedulingRuntime()

    def set_model_profiles(self, profiles: Dict[str, Any]) -> None:
        self._model_profiles = profiles

    @property
    def runtime(self) -> Any:
        return self._runtime

    @property
    def subworkflows(self) -> Dict[str, WorkflowDefinition]:
        return self._subworkflows

    def node_input(self, state: Dict[str, Any], node: WorkflowNode) -> Any:
        if node.input_key:
            outputs = state.get("node_outputs", {})
            if node.input_key not in outputs:
                raise KeyError(
                    f"node '{node.id}' input_key '{node.input_key}' has no recorded output on "
                    "this path — the referenced node has not run yet; a legitimately-None output "
                    "would be present, so this is a wiring error, not empty data"
                )
            return outputs[node.input_key]
        return state.get(RUNNING_PAYLOAD)

    def record(
        self,
        state: Dict[str, Any],
        node: WorkflowNode,
        result: CapabilityResult,
        *,
        attempts: int,
        input_payload: Any = None,
        branch_label: Optional[str] = None,
        force_status: Optional[str] = None,
        error: Optional[str] = None,
        fallback_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        status = force_status or result.status
        node_outputs = {**state.get("node_outputs", {}), node.id: result.output}
        if node.output_key:
            node_outputs[node.output_key] = result.output
        node_inputs = {**state.get("node_inputs", {}), node.id: input_payload}
        node_status = {**state.get("node_status", {}), node.id: status}
        attempts_map = {**state.get("attempts", {}), node.id: attempts}
        artifacts = [*state.get("artifacts", []), *result.artifacts]
        record = self._node_result_factory(
            node_id=node.id,
            kind=node.kind,
            status=status,
            output=result.output,
            branch_label=branch_label,
            error=error or result.error,
            fallback_reason=fallback_reason,
            attempts=attempts,
            artifact_ids=[artifact.artifact_id for artifact in result.artifacts],
        )
        node_results = [*state.get("node_results", []), record]
        self._runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                attempt=attempts,
                node_status=status,
                phase="node:result",
                error=(error or result.error) if status in ("failed", "partial") else None,
                metadata={
                    "kind": node.kind,
                    **({"branch_label": branch_label} if branch_label else {}),
                },
            )
        )
        update: Dict[str, Any] = {
            RUNNING_PAYLOAD: result.output,
            "node_outputs": node_outputs,
            "node_inputs": node_inputs,
            "node_status": node_status,
            "attempts": attempts_map,
            "artifacts": artifacts,
            "node_results": node_results,
        }
        if fallback_reason:
            update["fallback_reason"] = fallback_reason
        if status == "failed":
            update["status"] = "failed"
            update["error"] = error or result.error
        return update

    async def invoke_bound(
        self,
        node: WorkflowNode,
        capability: str,
        payload: Any,
        context: CapabilityContext,
        state: Dict[str, Any],
        *,
        attempt: int = 1,
        definition: Optional[WorkflowDefinition] = None,
        fanout_item_index: Optional[int] = None,
    ) -> CapabilityResult:
        context = self.context_for_node(node, context, state, definition=definition)
        if node.memory is not None:
            context = context.model_copy(
                update={"metadata": {**context.metadata, "agent_memory": node.memory}}
            )
        if not node.model_profile:
            return await self._runtime.invoke(capability, payload, context, attempt=attempt)
        profile = self._model_profiles.get(node.model_profile)
        if profile is None:
            raise self._capability_binding_error(
                f"node '{node.id}' references unknown model profile: {node.model_profile}"
            )
        bound_context = context.model_copy(update={"model_profile": profile})
        # C2: model_used derives from the events THIS invocation emitted (task-local capture
        # at the usage-event owner) — never from an index window over the shared summary,
        # which concurrent fanout siblings interleave.
        with capture_usage_events() as captured:
            with model_profile_scope(profile):
                result = await self._runtime.invoke(
                    capability,
                    payload,
                    bound_context,
                    attempt=attempt,
                )
        model_used = next((event.model for event in reversed(captured) if event.model), None)
        binding_metadata: Dict[str, Any] = {
            "model_profile_requested": node.model_profile,
            "model_profile_model": profile.model,
            "model_used": model_used,
        }
        if fanout_item_index is not None:
            # Deterministic input-order identity for fanout items only; never fabricated
            # for ordinary sequential bindings and never leaked into payload/context.
            binding_metadata["fanout_item_index"] = fanout_item_index
        self._runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                attempt=attempt,
                decision="model_binding",
                metadata=binding_metadata,
            )
        )
        return result

    async def gather_bound(
        self,
        node: WorkflowNode,
        calls: list[CapabilityCall],
        context: CapabilityContext,
        state: Dict[str, Any],
        *,
        max_parallel: int,
        definition: Optional[WorkflowDefinition] = None,
    ) -> list[CapabilityResult]:
        """Run fanout items concurrently while every item crosses the bound node door."""

        async def invoke_call(call: CapabilityCall, index: int) -> CapabilityResult:
            return await self.invoke_bound(
                node,
                call.name,
                call.payload,
                context,
                state,
                attempt=call.attempt,
                definition=definition,
                fanout_item_index=index,
            )

        return await gather_capability_calls(
            calls,
            max_parallel=max_parallel,
            invoke_call=invoke_call,
        )

    def context_for_node(
        self,
        node: WorkflowNode,
        context: CapabilityContext,
        state: Dict[str, Any],
        *,
        plan: Any = None,
        definition: Optional[WorkflowDefinition] = None,
    ) -> CapabilityContext:
        extra: Dict[str, Any] = {}
        if node.inject_plan:
            current_plan = plan or state.get("plan_artifact")
            if current_plan is not None:
                if not isinstance(current_plan, PlanArtifact):
                    current_plan = PlanArtifact.model_validate(current_plan)
                extra["plan"] = render_plan(current_plan)
        if node.inject_machine and definition is not None:
            extra["machine"] = render_machine_card(definition, node.id, state)
        provenance = _ACTIVE_RETRACE_PROVENANCE.get()
        if provenance is not None:
            update: Dict[str, Any] = {"retrace_provenance": provenance}
            if extra:
                update["metadata"] = {**context.metadata, **extra}
            return context.model_copy(update=update)
        if not extra:
            return context
        return context.model_copy(update={"metadata": {**context.metadata, **extra}})

    def sequential_predecessor(self, definition: WorkflowDefinition, node_id: str) -> Optional[str]:
        for transition in definition.transitions:
            if transition.target == node_id and transition.policy == "always":
                return transition.source
        return None

    def child_context(
        self, parent: CapabilityContext, child_def: WorkflowDefinition, ref: Any
    ) -> CapabilityContext:
        return build_child_context(parent, child_def, ref)

    async def run_child(
        self, definition: WorkflowDefinition, payload: Any, context: CapabilityContext
    ) -> Any:
        return await self._child_run(definition, payload, context)
