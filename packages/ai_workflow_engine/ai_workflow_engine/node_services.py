"""Narrow service boundary between the executor and node handlers (H1).

Node handlers must not depend on the full ``WorkflowExecutor`` object: they receive a
``NodeExecutionServices`` implementation exposing ONLY the operations a node is allowed to
use — read its input, invoke a capability under the bound context, record its result,
route trace/observation events, consult the scheduler through a narrow facade, and (for
subworkflow nodes) resolve + run a registered child workflow. Everything else on the
executor is private by contract; the executor<->nodes coupling guard enforces this.

The concrete adapters (``ExecutorNodeServices`` / ``ExecutorSchedulingServices``) delegate
to the executor, which keeps owning the long-lived shared state (scheduler, compiled-graph
cache, scheduled-task registry). Tests can substitute a fake ``NodeExecutionServices``
without constructing an executor.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Protocol, Tuple, runtime_checkable

from ai_workflow_engine.models import CapabilityContext, CapabilityResult
from ai_workflow_engine.workflow import WorkflowDefinition, WorkflowNode


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
    ) -> CapabilityResult: ...

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


class ExecutorSchedulingServices:
    """Scheduling facade backed by the executor's shared scheduler + task registry."""

    def __init__(self, executor: Any) -> None:
        self._executor = executor

    def submit(self, *, key: str, run_id: str, payload: Any, policy: Any) -> Any:
        return self._executor.scheduler.submit(key=key, run_id=run_id, payload=payload, policy=policy)

    def complete(self, *, key: str, run_id: str) -> None:
        self._executor.scheduler.complete(key=key, run_id=run_id)

    def active_run_id(self, key: str) -> Optional[str]:
        return self._executor.scheduler.active_run_id(key)

    def register_task(self, key: str, run_id: str, task: "asyncio.Task[Any]") -> None:
        self._executor._scheduled_tasks[key] = (run_id, task)

    def registered_task(self, key: str) -> Optional[Tuple[str, "asyncio.Task[Any]"]]:
        return self._executor._scheduled_tasks.get(key)

    def unregister_task(self, key: str, run_id: str) -> None:
        registered = self._executor._scheduled_tasks.get(key)
        if registered is not None and registered[0] == run_id:
            self._executor._scheduled_tasks.pop(key, None)

    def mark_superseded(self, key: str, run_id: str, superseding_run_id: str) -> None:
        self._executor._scheduled_cancellations[(key, run_id)] = superseding_run_id

    def pop_superseded(self, key: str, run_id: str) -> Optional[str]:
        return self._executor._scheduled_cancellations.pop((key, run_id), None)


class ExecutorNodeServices:
    """The executor-backed ``NodeExecutionServices`` implementation."""

    def __init__(self, executor: Any) -> None:
        self._executor = executor
        self.scheduling: NodeSchedulingServices = ExecutorSchedulingServices(executor)

    @property
    def runtime(self) -> Any:
        return self._executor.runtime

    @property
    def subworkflows(self) -> Dict[str, WorkflowDefinition]:
        return self._executor.subworkflows

    def node_input(self, state: Dict[str, Any], node: WorkflowNode) -> Any:
        return self._executor._node_input(state, node)

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
        return self._executor._record(
            state,
            node,
            result,
            attempts=attempts,
            input_payload=input_payload,
            branch_label=branch_label,
            force_status=force_status,
            error=error,
            fallback_reason=fallback_reason,
        )

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
    ) -> CapabilityResult:
        return await self._executor._invoke_bound(
            node, capability, payload, context, state, attempt=attempt, definition=definition
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
        return self._executor._context_for_node(node, context, state, plan=plan, definition=definition)

    def sequential_predecessor(self, definition: WorkflowDefinition, node_id: str) -> Optional[str]:
        return self._executor._sequential_predecessor(definition, node_id)

    def child_context(
        self, parent: CapabilityContext, child_def: WorkflowDefinition, ref: Any
    ) -> CapabilityContext:
        return self._executor._child_context(parent, child_def, ref)

    async def run_child(
        self, definition: WorkflowDefinition, payload: Any, context: CapabilityContext
    ) -> Any:
        return await self._executor._run_inner(definition, payload, context)
