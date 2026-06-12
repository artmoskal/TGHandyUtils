"""Executable workflow runtime.

``WorkflowExecutor`` compiles a :class:`~ai_workflow_engine.workflow.WorkflowDefinition` into a
LangGraph ``StateGraph`` and runs it end to end. LangGraph is an *internal* execution backend:
the framework is imported only inside :meth:`WorkflowExecutor.compile` and never leaks through the
public API — products see only ``WorkflowBuilder``/``WorkflowEngine``.

The executor owns orchestration mechanics (node dispatch, input/output mapping, branch routing,
fail-closed halting, trace/result shaping). Control loops that are naturally node-local
(retry, evaluator retry/retrace, fan-out gather) are executed inside the relevant node so the
compiled graph stays a predictable DAG with explicit, bounded back-edges only where a
workflow declares a graph-level retrace.

Public concepts: ``WorkflowExecutor``, ``NodeExecutionState``, ``NodeResult``,
``WorkflowRunResult``, ``UnsupportedNodeError``, ``CapabilityBindingError``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from ai_workflow_engine.engine.capabilities import (
    CapabilityCall,
    CapabilityRuntime,
    gather_capabilities,
)
from ai_workflow_engine.engine.runner import WorkflowRunner
from ai_workflow_engine.engine.scheduler import WorkflowScheduler
from ai_workflow_engine.model_binding import model_profile_scope
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    CriticismEnvelope,
    EvaluationDecision,
    WorkflowArtifact,
    WorkflowResultStatus,
    WorkflowTraceEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.planning import PlanArtifact, PlanTask, render_plan
from ai_workflow_engine.workflow import (
    END,
    KNOWN_NODE_KINDS,
    BranchDecision,
    WorkflowDefinition,
    WorkflowNode,
)

# Internal state keys reserved by the executor (kept out of product state space).
_RUNNING_PAYLOAD = "payload"
_CONTEXT = "engine_context"


class WorkflowState(TypedDict, total=False):
    """LangGraph state schema. Every key is declared so LangGraph propagates it across nodes
    (an undeclared key on a plain ``dict`` schema would be dropped by the channel layer).
    Keys ``workflow_context``/``workflow_goal``/``usage_summary`` are injected by ``WorkflowRunner``.
    """

    payload: Any
    engine_context: Any
    node_outputs: Dict[str, Any]
    node_inputs: Dict[str, Any]
    node_results: List[Any]
    node_status: Dict[str, str]
    branch_decisions: Dict[str, str]
    routes: Dict[str, str]
    eval_counters: Dict[str, Dict[str, int]]
    attempts: Dict[str, int]
    artifacts: List[Any]
    status: str
    error: Optional[str]
    fallback_reason: Optional[str]
    workflow_context: Any
    workflow_goal: Any
    usage_summary: Any
    plan_artifact: Any


class UnsupportedNodeError(ValueError):
    """Raised/surfaced when a node kind has no executor handler."""


class CapabilityBindingError(ValueError):
    """Raised/surfaced when a node binds to a capability/subworkflow that is not registered."""


class NodeResult(BaseModel):
    """Completed execution record for one node (one entry per node visit)."""

    node_id: str
    kind: str
    status: str
    output: Any = None
    branch_label: Optional[str] = None
    error: Optional[str] = None
    fallback_reason: Optional[str] = None
    attempts: int = 1
    artifact_ids: List[str] = Field(default_factory=list)


class NodeExecutionState(BaseModel):
    """In-flight snapshot handed to a node handler (live execution state envelope)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    node: WorkflowNode
    context: CapabilityContext
    attempt: int
    input_payload: Any = None
    raw_state: Dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResult(BaseModel):
    """Terminal result envelope of a workflow run (product-neutral)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    workflow_id: str
    status: WorkflowResultStatus = "completed"
    output: Any = None
    error: Optional[str] = None
    fallback_reason: Optional[str] = None
    node_results: List[NodeResult] = Field(default_factory=list)
    artifacts: List[WorkflowArtifact] = Field(default_factory=list)
    usage: WorkflowUsageSummary = Field(default_factory=WorkflowUsageSummary)
    trace: List[WorkflowTraceEvent] = Field(default_factory=list)

    def node(self, node_id: str) -> Optional[NodeResult]:
        for record in reversed(self.node_results):
            if record.node_id == node_id:
                return record
        return None


# A node handler builds and returns a coroutine function for one node in one workflow.
NodeHandler = Callable[["WorkflowExecutor", WorkflowDefinition, WorkflowNode], Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]]


class WorkflowExecutor:
    """Compile + run workflow definitions on a single shared execution backend.

    The same executor instance runs every workflow (Anki, GoPro inventory, MageQA audit,
    calendar) — that single-executor invariant is the contract's central proof.
    """

    def __init__(
        self,
        runtime: CapabilityRuntime,
        *,
        subworkflows: Optional[Dict[str, WorkflowDefinition]] = None,
        config: Any = None,
    ) -> None:
        self.runtime = runtime
        self.subworkflows = subworkflows or {}
        self.runner = WorkflowRunner(config)
        # Shared across all runs on this executor so concurrent runs contend for the same backend
        # slots (single-flight / backpressure are engine-owned, not per-run policy state).
        self.scheduler = WorkflowScheduler()
        self._bound_loop: Optional[asyncio.AbstractEventLoop] = None
        self._scheduled_tasks: Dict[str, tuple[str, asyncio.Task[Any]]] = {}
        self._scheduled_cancellations: Dict[tuple[str, str], str] = {}
        # ModelProfile registry for declarative per-node model binding (set by WorkflowEngine).
        self.model_profiles: Dict[str, Any] = {}
        self._compiled: Dict[str, Any] = {}
        # Node-kind handler table. Kinds present here are *implemented*; any valid-but-absent
        # kind fails loudly (never silently downgraded). Phases register more kinds.
        self._handlers: Dict[str, NodeHandler] = {
            "step": WorkflowExecutor._build_step_node,
            "branch": WorkflowExecutor._build_branch_node,
            "fanout": WorkflowExecutor._build_fanout_node,
            "evaluate": WorkflowExecutor._build_evaluate_node,
            "subworkflow": WorkflowExecutor._build_subworkflow_node,
            "human": WorkflowExecutor._build_human_node,
            "planner": WorkflowExecutor._build_planner_node,
        }

    # ---------------------------------------------------------------- public API
    def supported_kinds(self) -> set[str]:
        return set(self._handlers)

    def register_subworkflow(self, definition: WorkflowDefinition) -> None:
        self.subworkflows[definition.workflow_id] = definition

    async def run(
        self,
        definition: WorkflowDefinition,
        payload: Any,
        context: CapabilityContext,
        *,
        recursion_fallback: Optional[Callable[..., Any]] = None,
        recursion_limit: Optional[int] = None,
    ) -> WorkflowRunResult:
        """Execute ``definition`` from ``payload`` under ``context`` and return the envelope."""

        self._bind_or_validate_event_loop()
        # Pre-flight: bindings and node kinds must resolve, or fail loudly + trace (no run).
        binding_error = self._preflight(definition)
        if binding_error is not None:
            return self._failed_envelope(definition, binding_error)

        compiled = self.compile(definition)
        limit = recursion_limit or self._recursion_limit(definition, context)
        graph_config = {"recursion_limit": limit}
        # Top-level run: WorkflowRunner installs the usage/budget scope + lifecycle logging.
        final_state = await self.runner.run(
            compiled,
            self._initial_state(payload, context),
            workflow_type=context.goal.workflow_type,
            goal=context.goal,
            graph_config=graph_config,
            recursion_fallback=recursion_fallback,
        )
        return self._envelope(definition, final_state)

    async def _run_inner(
        self,
        definition: WorkflowDefinition,
        payload: Any,
        context: CapabilityContext,
    ) -> WorkflowRunResult:
        """Run a child workflow inside the parent's already-installed usage/budget scope.

        Used by subworkflow nodes so child metered calls aggregate into the parent's usage
        summary (shared budget) instead of opening a fresh scope.
        """

        self._bind_or_validate_event_loop()
        binding_error = self._preflight(definition)
        if binding_error is not None:
            return self._failed_envelope(definition, binding_error)
        compiled = self.compile(definition)
        graph_config = {"recursion_limit": self._recursion_limit(definition, context)}
        final_state = await compiled.ainvoke(self._initial_state(payload, context), config=graph_config)
        return self._envelope(definition, final_state)

    def _bind_or_validate_event_loop(self) -> None:
        current = asyncio.get_running_loop()
        if self._bound_loop is None or self._bound_loop.is_closed():
            self._bound_loop = current
            return
        if self._bound_loop is not current:
            raise RuntimeError(
                "This WorkflowExecutor is bound to one event loop; from other threads/loops use "
                "asyncio.run_coroutine_threadsafe(engine.run(...), engine_loop)"
            )

    @staticmethod
    def _initial_state(payload: Any, context: CapabilityContext) -> Dict[str, Any]:
        return {
            _RUNNING_PAYLOAD: payload,
            _CONTEXT: context,
            # "__input__" lets any node read the original workflow input via input_key.
            "node_outputs": {"__input__": payload},
            "node_inputs": {},
            "node_results": [],
            "node_status": {},
            "branch_decisions": {},
            "routes": {},
            "eval_counters": {},
            "attempts": {},
            "artifacts": [],
            "status": "running",
            "error": None,
            "fallback_reason": None,
            "plan_artifact": None,
        }

    def compile(self, definition: WorkflowDefinition) -> Any:
        """Compile a definition into a runnable LangGraph (cached by workflow id)."""

        cached = self._compiled.get(definition.workflow_id)
        if cached is not None:
            return cached

        from langgraph.graph import END as LG_END, START, StateGraph

        graph = StateGraph(WorkflowState)
        for node in definition.nodes:
            handler = self._handlers.get(node.kind)
            if handler is None:
                # Defensive: pre-flight already catches this; raise so direct compile is loud.
                raise UnsupportedNodeError(
                    f"node '{node.id}' kind '{node.kind}' is not supported by this executor"
                )
            graph.add_node(node.id, handler(self, definition, node))

        graph.add_edge(START, definition.entry)
        for node in definition.nodes:
            self._wire_edges(graph, definition, node, LG_END)

        compiled = graph.compile()
        self._compiled[definition.workflow_id] = compiled
        return compiled

    # ---------------------------------------------------------------- edge wiring
    def _wire_edges(self, graph: Any, definition: WorkflowDefinition, node: WorkflowNode, lg_end: Any) -> None:
        if node.kind == "branch":
            path_map: Dict[str, Any] = {}
            for label, target in node.branches.items():
                path_map[label] = lg_end if target == END else target
            path_map["__invalid__"] = lg_end
            graph.add_conditional_edges(node.id, self._branch_router(node), path_map)
            return

        if node.kind == "evaluate":
            from ai_workflow_engine.workflow import Replan, Retrace

            successor = self._sequential_successor(definition, node.id)
            predecessor = self._sequential_predecessor(definition, node.id)
            retrace_target = node.on_reject.target if isinstance(node.on_reject, Retrace) else None
            replan_target = node.on_reject.target if isinstance(node.on_reject, Replan) else None
            graph.add_conditional_edges(
                node.id,
                self._evaluate_router(node),
                {
                    "accept": lg_end if (successor is None or successor == END) else successor,
                    "retry": predecessor or lg_end,
                    "retrace": retrace_target or lg_end,
                    "replan": replan_target or lg_end,
                    "halt": lg_end,
                },
            )
            return

        # step + fanout: sequential continuation, fail-closed halt on failure.
        successor = self._sequential_successor(definition, node.id)
        target = lg_end if (successor is None or successor == END) else successor
        graph.add_conditional_edges(
            node.id,
            self._step_router(node),
            {"__next__": target, "__halt__": lg_end},
        )

    @staticmethod
    def _sequential_successor(definition: WorkflowDefinition, node_id: str) -> Optional[str]:
        for edge in definition.edges:
            if edge.source == node_id and not edge.conditional:
                return edge.target
        return None

    @staticmethod
    def _sequential_predecessor(definition: WorkflowDefinition, node_id: str) -> Optional[str]:
        for edge in definition.edges:
            if edge.target == node_id and not edge.conditional:
                return edge.source
        return None

    @staticmethod
    def _evaluate_router(node: WorkflowNode) -> Callable[[Dict[str, Any]], str]:
        def route(state: Dict[str, Any]) -> str:
            return state.get("routes", {}).get(node.id, "halt")

        return route

    @staticmethod
    def _step_router(node: WorkflowNode) -> Callable[[Dict[str, Any]], str]:
        def route(state: Dict[str, Any]) -> str:
            if state.get("routes", {}).get(node.id) == "halt":
                return "__halt__"
            if state.get("node_status", {}).get(node.id) == "failed":
                return "__halt__"
            return "__next__"

        return route

    @staticmethod
    def _branch_router(node: WorkflowNode) -> Callable[[Dict[str, Any]], str]:
        def route(state: Dict[str, Any]) -> str:
            label = state.get("branch_decisions", {}).get(node.id)
            if label in node.branches:
                return label
            return "__invalid__"

        return route

    # ---------------------------------------------------------------- model binding
    async def _invoke_bound(
        self,
        node: WorkflowNode,
        capability: str,
        payload: Any,
        context: CapabilityContext,
        state: Dict[str, Any],
        *,
        attempt: int = 1,
    ) -> CapabilityResult:
        """Invoke a capability under the node's declared model profile (if any), traced."""

        context = self._context_for_node(node, context, state)
        if not node.model_profile:
            return await self.runtime.invoke(capability, payload, context, attempt=attempt)
        profile = self.model_profiles.get(node.model_profile)
        if profile is None:  # defensive: preflight already rejects this
            raise CapabilityBindingError(
                f"node '{node.id}' references unknown model profile: {node.model_profile}"
            )
        usage = state.get("usage_summary")
        events_before = len(usage.events) if usage is not None else 0
        bound_context = context.model_copy(update={"model_profile": profile})
        with model_profile_scope(profile):
            result = await self.runtime.invoke(capability, payload, bound_context, attempt=attempt)
        new_events = usage.events[events_before:] if usage is not None else []
        model_used = next((event.model for event in reversed(new_events) if event.model), None)
        self.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                attempt=attempt,
                decision="model_binding",
                metadata={
                    "model_profile_requested": node.model_profile,
                    "model_profile_model": profile.model,
                    "model_used": model_used,
                },
            )
        )
        return result

    @staticmethod
    def _context_for_node(
        node: WorkflowNode,
        context: CapabilityContext,
        state: Dict[str, Any],
        *,
        plan: Optional[PlanArtifact] = None,
    ) -> CapabilityContext:
        if not node.inject_plan:
            return context
        current_plan = plan or state.get("plan_artifact")
        if current_plan is None:
            return context
        if not isinstance(current_plan, PlanArtifact):
            current_plan = PlanArtifact.model_validate(current_plan)
        metadata = {**context.metadata, "plan": render_plan(current_plan)}
        return context.model_copy(update={"metadata": metadata})

    # ---------------------------------------------------------------- node handlers
    def _build_step_node(self, definition: WorkflowDefinition, node: WorkflowNode):
        capability = node.capability or node.id

        async def step_fn(state: Dict[str, Any]) -> Dict[str, Any]:
            context: CapabilityContext = state[_CONTEXT]
            payload = self._node_input(state, node)

            # Engine-owned pre-invocation gate: forbidden side effect / raw-media export /
            # budget exhaustion are blocked BEFORE the handler runs (fail-closed, traced).
            denial = self._policy_denial(node, capability, state, context)
            if denial:
                rejected = CapabilityResult(
                    status="rejected",
                    error=f"denied by policy: {', '.join(denial)}",
                    metadata={"denied": denial},
                )
                self.runtime.trace_sink.record(
                    WorkflowTraceEvent(node=node.id, decision="denied", error=rejected.error, metadata={"denied": denial})
                )
                return self._record(
                    state, node, rejected, attempts=1, input_payload=payload, force_status="failed", error=rejected.error
                )

            # Backend scheduling/backpressure: gate this node through the shared scheduler. The
            # backend slot is held until the worker actually completes (released in `finally`),
            # so a concurrent second call is dropped/queued and cancellation cannot free it early.
            sched = node.scheduling
            if sched is not None and sched.backend_key:
                lane = sched.backend_key
                run_id = uuid.uuid4().hex
                decision = self.scheduler.submit(key=lane, run_id=run_id, payload=payload, policy=sched)
                schedule_metadata = {
                    "lane": lane,
                    "reason": decision.reason,
                    "run_id": run_id,
                    "previous_run_id": decision.previous_run_id,
                }
                self.runtime.trace_sink.record(
                    WorkflowTraceEvent(
                        node=node.id,
                        decision=f"schedule:{decision.action}",
                        metadata={k: v for k, v in schedule_metadata.items() if v is not None},
                    )
                )
                if decision.action in ("drop", "coalesce"):
                    denied = CapabilityResult(
                        status="rejected",
                        error=f"scheduling {decision.action}: {decision.reason}",
                        metadata={"scheduling": decision.action, "lane": lane},
                    )
                    return self._record(
                        state, node, denied, attempts=1, input_payload=payload,
                        force_status="failed", error=denied.error,
                    )
                if decision.action == "cancel_previous":
                    cancel_error = await self._cancel_previous_scheduled_run(
                        lane=lane,
                        previous_run_id=decision.previous_run_id,
                        superseding_run_id=run_id,
                        node=node,
                    )
                    if cancel_error is not None:
                        denied = CapabilityResult(
                            status="failed",
                            error=cancel_error,
                            metadata={"scheduling": "cancel_previous", "lane": lane},
                        )
                        return self._record(
                            state, node, denied, attempts=1, input_payload=payload,
                            force_status="failed", error=cancel_error,
                        )
                    if self.scheduler.active_run_id(lane) != run_id:
                        superseded = CapabilityResult(
                            status="failed",
                            error=f"scheduling superseded: run {run_id} was not promoted",
                            metadata={"scheduling": "superseded", "lane": lane, "run_id": run_id},
                        )
                        return self._record(
                            state, node, superseded, attempts=1, input_payload=payload,
                            force_status="failed", error=superseded.error,
                        )
                    self.runtime.trace_sink.record(
                        WorkflowTraceEvent(
                            node=node.id,
                            decision="schedule:promote",
                            metadata={"lane": lane, "run_id": run_id, "after_run_id": decision.previous_run_id},
                        )
                    )
                result = await self._invoke_scheduled_bound(
                    node=node,
                    capability=capability,
                    payload=payload,
                    context=context,
                    state=state,
                    lane=lane,
                    run_id=run_id,
                    attempt=1,
                )
                if result.status == "rejected":
                    return self._record(
                        state, node, result, attempts=1, input_payload=payload,
                        force_status="failed", error=result.error or "rejected by policy",
                    )
                return self._record(state, node, result, attempts=1, input_payload=payload)

            attempt_base = state.get("attempts", {}).get(node.id, 0)
            max_attempts = node.retry.max_attempts if node.retry else 1
            result: CapabilityResult = CapabilityResult(status="failed", error="not run")
            attempts = 0
            for offset in range(max_attempts):
                attempts = attempt_base + offset + 1
                result = await self._invoke_bound(node, capability, payload, context, state, attempt=attempts)
                if result.status != "failed":
                    break
            if result.status == "rejected":
                # Runtime-level denial (defense in depth) is also fail-closed.
                return self._record(
                    state, node, result, attempts=attempts, input_payload=payload,
                    force_status="failed", error=result.error or "rejected by policy",
                )
            return self._record(state, node, result, attempts=attempts, input_payload=payload)

        return step_fn

    async def _invoke_scheduled_bound(
        self,
        *,
        node: WorkflowNode,
        capability: str,
        payload: Any,
        context: CapabilityContext,
        state: Dict[str, Any],
        lane: str,
        run_id: str,
        attempt: int,
    ) -> CapabilityResult:
        current_task = asyncio.current_task()
        if current_task is not None:
            self._scheduled_tasks[lane] = (run_id, current_task)
        try:
            result = await self._invoke_bound(node, capability, payload, context, state, attempt=attempt)
            superseded_by = self._scheduled_cancellations.pop((lane, run_id), None)
            if superseded_by:
                return self._cancelled_scheduled_result(
                    node=node, lane=lane, run_id=run_id, superseded_by=superseded_by
                )
            return result
        except asyncio.CancelledError:
            superseded_by = self._scheduled_cancellations.pop((lane, run_id), None)
            if not superseded_by:
                raise
            return self._cancelled_scheduled_result(
                node=node, lane=lane, run_id=run_id, superseded_by=superseded_by
            )
        finally:
            registered = self._scheduled_tasks.get(lane)
            if registered is not None and registered[0] == run_id:
                self._scheduled_tasks.pop(lane, None)
            self.scheduler.complete(key=lane, run_id=run_id)

    async def _cancel_previous_scheduled_run(
        self,
        *,
        lane: str,
        previous_run_id: Optional[str],
        superseding_run_id: str,
        node: WorkflowNode,
    ) -> Optional[str]:
        if not previous_run_id:
            return f"scheduling cancel_previous had no previous run for lane {lane}"
        registered = self._scheduled_tasks.get(lane)
        if registered is None or registered[0] != previous_run_id:
            # The previous worker already exited (its finally freed the slot and may have promoted
            # us) — there is nothing left to cancel. This is success for our purposes; the
            # promotion check downstream decides whether this run proceeds or is superseded.
            # Treating it as an error here would fail a possibly-already-promoted run without
            # ever releasing its slot, leaving the lane permanently stuck.
            self.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=node.id,
                    decision="schedule:cancel_skipped",
                    metadata={
                        "lane": lane,
                        "previous_run_id": previous_run_id,
                        "reason": "previous run already exited",
                    },
                )
            )
            return None
        _run_id, task = registered
        self._scheduled_cancellations[(lane, previous_run_id)] = superseding_run_id
        self.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                decision="schedule:cancel_request",
                metadata={
                    "lane": lane,
                    "previous_run_id": previous_run_id,
                    "superseding_run_id": superseding_run_id,
                },
            )
        )
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected terminal state: the previous task ended cancelled. Scheduled nodes convert
            # engine-owned cancellation into a failed node record themselves; what matters here is
            # only that the worker has truly exited before promotion.
            self.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=node.id,
                    decision="schedule:cancel_confirmed",
                    metadata={"lane": lane, "previous_run_id": previous_run_id, "exit": "cancelled"},
                )
            )
        finally:
            # The cancelled run normally consumes its marker; if it exited through a path that
            # could not (already past its pop points), clean up so the dict cannot accumulate.
            self._scheduled_cancellations.pop((lane, previous_run_id), None)
        return None

    def _cancelled_scheduled_result(
        self,
        *,
        node: WorkflowNode,
        lane: str,
        run_id: str,
        superseded_by: str,
    ) -> CapabilityResult:
        error = f"cancelled: superseded by {superseded_by}"
        self.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                decision="schedule:cancelled",
                error=error,
                metadata={"lane": lane, "run_id": run_id, "superseded_by": superseded_by},
            )
        )
        return CapabilityResult(
            status="failed",
            error=error,
            metadata={
                "scheduling": "cancelled",
                "lane": lane,
                "run_id": run_id,
                "superseded_by": superseded_by,
            },
        )

    def _policy_denial(
        self,
        node: WorkflowNode,
        capability: str,
        state: Dict[str, Any],
        context: CapabilityContext,
    ) -> List[str]:
        """Return the policy reasons this node may not be invoked (empty == allowed)."""

        plan = context.plan
        if plan is None:
            return []
        try:
            spec, _handler = self.runtime.registry.get(capability)
        except KeyError:
            return []  # missing capability is surfaced by pre-flight, not here
        # The profile's allowed_side_effects is the deployment ceiling. A node may never exceed it.
        allowed = set(plan.safety.allowed_side_effects)
        needed = set(spec.side_effects) | set(node.required_side_effects)
        denied = sorted(effect for effect in needed if effect not in allowed)
        # Raw-media export is privacy-sensitive: it additionally requires an explicit per-node
        # opt-in even when the profile permits it (double consent), and the node opt-in can never
        # grant it when the profile forbids it.
        if "raw_media_export" in spec.side_effects and not node.allow_raw_media_export:
            if "raw_media_export" not in denied:
                denied.append("raw_media_export:node-opt-in-required")
        if denied:
            return denied
        # Budget: a metered capability cannot run once the run's spend has reached the cap.
        if spec.metered and plan.limits and plan.limits.max_estimated_usd is not None:
            usage = state.get("usage_summary")
            spent = (getattr(usage, "estimated_usd", None) or 0.0) if usage is not None else 0.0
            if spent >= plan.limits.max_estimated_usd:
                return ["budget_exhausted"]
        return []

    def _build_branch_node(self, definition: WorkflowDefinition, node: WorkflowNode):
        decider = node.decider or node.capability or node.id

        async def branch_fn(state: Dict[str, Any]) -> Dict[str, Any]:
            context: CapabilityContext = state[_CONTEXT]
            payload = self._node_input(state, node)
            attempt = state.get("attempts", {}).get(node.id, 0) + 1
            result = await self._invoke_bound(node, decider, payload, context, state, attempt=attempt)
            label = self._extract_label(result)
            valid = label in node.branches
            update = self._record(
                state,
                node,
                result,
                attempts=attempt,
                input_payload=payload,
                branch_label=label,
                force_status=None if valid else "failed",
                error=None if valid else f"branch '{node.id}' produced invalid label: {label!r}",
            )
            branch_decisions = {**state.get("branch_decisions", {}), node.id: label}
            update["branch_decisions"] = branch_decisions
            # A branch is a routing decision, not a transform: the running payload passes through
            # unchanged so the selected downstream node sees the real data, not the decision.
            update[_RUNNING_PAYLOAD] = payload
            self.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=node.id,
                    attempt=attempt,
                    decision="branch",
                    error=None if valid else update.get("error"),
                    metadata={"label": label, "valid": valid},
                )
            )
            return update

        return branch_fn

    def _build_fanout_node(self, definition: WorkflowDefinition, node: WorkflowNode):
        item_capability = node.item_capability or node.capability or node.id

        async def fanout_fn(state: Dict[str, Any]) -> Dict[str, Any]:
            context: CapabilityContext = state[_CONTEXT]
            items = self._resolve_items(state, node)
            if not isinstance(items, list):
                failed = CapabilityResult(
                    status="failed",
                    error=f"fanout '{node.id}' items source '{node.fan_items_key}' is not a list",
                )
                return self._record(state, node, failed, attempts=1, input_payload=items)

            limit = node.max_parallel or (context.limits.max_parallel_children if context.limits else 4) or 4
            calls = [CapabilityCall(item_capability, item) for item in items]
            results = await gather_capabilities(self.runtime, calls, context, max_parallel=limit)
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
            update = self._record(state, node, combined, attempts=1, input_payload=items)
            self.runtime.trace_sink.record(
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

    def _build_planner_node(self, definition: WorkflowDefinition, node: WorkflowNode):
        planner_capability = node.capability or node.id

        async def planner_fn(state: Dict[str, Any]) -> Dict[str, Any]:
            context: CapabilityContext = state[_CONTEXT]
            payload = self._node_input(state, node)
            attempt = state.get("attempts", {}).get(node.id, 0) + 1

            resume_plan = self._coerce_plan_artifact(payload)
            if resume_plan is None:
                planner_result = await self._invoke_bound(
                    node, planner_capability, payload, context, state, attempt=attempt
                )
                if planner_result.status in ("failed", "rejected"):
                    return self._record(
                        state,
                        node,
                        planner_result,
                        attempts=attempt,
                        input_payload=payload,
                        force_status="failed",
                        error=planner_result.error or "planner failed",
                    )
                plan = self._coerce_plan_artifact(planner_result.output)
                if plan is None:
                    failed = CapabilityResult(
                        status="failed",
                        error=f"planner '{node.id}' did not emit a PlanArtifact-compatible output",
                    )
                    return self._record(
                        state, node, failed, attempts=attempt, input_payload=payload
                    )
                prior_plan = self._coerce_plan_artifact(state.get("plan_artifact"))
                if prior_plan is not None:
                    plan = self._merge_replanned_plan(prior_plan, plan)
            else:
                plan = resume_plan
                planner_result = CapabilityResult(status="accepted", output=plan)

            validation_errors = self._validate_plan(node, plan, context)
            if validation_errors:
                error = "planner validation failed: " + "; ".join(validation_errors)
                self.runtime.trace_sink.record(
                    WorkflowTraceEvent(
                        node=node.id,
                        attempt=attempt,
                        decision="plan:validation_failed",
                        error=error,
                        metadata={"errors": validation_errors},
                    )
                )
                failed = CapabilityResult(status="failed", output=plan, error=error)
                update = self._record(
                    state, node, failed, attempts=attempt, input_payload=payload
                )
                update["plan_artifact"] = plan
                return update

            executed_plan, task_outputs, task_artifacts, task_failures = await self._execute_plan(
                node=node,
                plan=plan,
                context=context,
                state=state,
            )
            if task_outputs:
                executed_plan = executed_plan.model_copy(
                    update={"metadata": {**executed_plan.metadata, "task_outputs": task_outputs}}
                )
            status = "partial" if task_failures else "accepted"
            if executed_plan.tasks and all(task.status in ("failed", "skipped") for task in executed_plan.tasks):
                status = "failed"
            result = CapabilityResult(
                status=status,
                output=executed_plan,
                artifacts=task_artifacts,
                error="; ".join(task_failures) or planner_result.error,
                metadata={"tasks": len(executed_plan.tasks), "failed": len(task_failures)},
            )
            update = self._record(
                state,
                node,
                result,
                attempts=attempt,
                input_payload=payload,
                force_status="failed" if status == "failed" else status,
                error=result.error if status == "failed" else None,
            )
            node_outputs = {**update.get("node_outputs", {})}
            node_outputs.update(task_outputs)
            update["node_outputs"] = node_outputs
            update["plan_artifact"] = executed_plan
            update[_RUNNING_PAYLOAD] = executed_plan
            return update

        return planner_fn

    @staticmethod
    def _coerce_plan_artifact(value: Any) -> Optional[PlanArtifact]:
        if value is None:
            return None
        if isinstance(value, PlanArtifact):
            return value
        if isinstance(value, dict):
            try:
                return PlanArtifact.model_validate(value)
            except Exception:
                return None
        return None

    @staticmethod
    def _merge_replanned_plan(prior: PlanArtifact, proposed: PlanArtifact) -> PlanArtifact:
        immutable = {
            task.task_id: task
            for task in prior.tasks
            if task.status in ("done", "failed", "skipped")
        }
        merged: list[PlanTask] = []
        seen: set[str] = set()
        for old_task in prior.tasks:
            if old_task.task_id in immutable:
                merged.append(old_task)
                seen.add(old_task.task_id)
        for task in proposed.tasks:
            if task.task_id in immutable:
                continue
            status = task.status if task.status in ("pending", "in_progress") else "pending"
            merged.append(task.model_copy(update={"status": status, "error": None, "output_ref": None}))
            seen.add(task.task_id)
        metadata = {**proposed.metadata}
        if "task_outputs" in prior.metadata and "task_outputs" not in metadata:
            metadata["task_outputs"] = prior.metadata["task_outputs"]
        return proposed.model_copy(update={"tasks": merged, "revision": prior.revision + 1, "metadata": metadata})

    def _validate_plan(
        self,
        node: WorkflowNode,
        plan: PlanArtifact,
        context: CapabilityContext,
    ) -> list[str]:
        errors: list[str] = []
        if len(plan.tasks) > node.max_tasks:
            errors.append(f"plan has {len(plan.tasks)} tasks, exceeds max_tasks={node.max_tasks}")
        seen: set[str] = set()
        allowed = set(context.plan.safety.allowed_side_effects) if context.plan is not None else set()
        for index, task in enumerate(plan.tasks):
            label = task.task_id or f"#{index + 1}"
            if not task.task_id:
                errors.append(f"task {index + 1} has empty task_id")
            elif task.task_id in seen:
                errors.append(f"task '{task.task_id}' has duplicate task_id")
            seen.add(task.task_id)
            try:
                spec, handler = self.runtime.registry.get(task.capability)
            except KeyError:
                errors.append(f"task '{label}' capability '{task.capability}' is not registered")
                continue
            if task.capability == (node.capability or node.id):
                errors.append(f"task '{label}' cannot call its planner capability '{task.capability}'")
            if spec.metadata.get("planner") is True or getattr(handler, "is_planner", False):
                errors.append(f"task '{label}' cannot call planner capability '{task.capability}'")
            denied = sorted(effect for effect in spec.side_effects if effect not in allowed)
            if denied:
                errors.append(
                    f"task '{label}' capability '{task.capability}' side effects denied: "
                    f"{', '.join(denied)}"
                )
        return errors

    async def _execute_plan(
        self,
        *,
        node: WorkflowNode,
        plan: PlanArtifact,
        context: CapabilityContext,
        state: Dict[str, Any],
    ) -> tuple[PlanArtifact, Dict[str, Any], list[WorkflowArtifact], list[str]]:
        task_outputs: Dict[str, Any] = {}
        artifacts: list[WorkflowArtifact] = []
        failures: list[str] = []
        tasks = list(plan.tasks)
        if node.execution == "fanout":
            return await self._execute_plan_fanout(node, plan, context, state)

        current_plan = plan
        for index, task in enumerate(tasks):
            if task.status in ("done", "failed", "skipped"):
                continue
            started = task.model_copy(update={"status": "in_progress", "error": None})
            tasks[index] = started
            current_plan = current_plan.model_copy(update={"tasks": list(tasks)})
            self._trace_plan_task(node, started, "plan:task_started")
            result = await self._invoke_plan_task(node, started, context, state, current_plan)
            finished, output_key = self._finish_plan_task(node, started, result)
            tasks[index] = finished
            current_plan = current_plan.model_copy(update={"tasks": list(tasks)})
            if output_key and result.output is not None:
                task_outputs[output_key] = result.output
            artifacts.extend(result.artifacts)
            if finished.status == "failed":
                failures.append(f"{finished.task_id}: {finished.error or 'failed'}")
                self._trace_plan_task(node, finished, "plan:task_failed", error=finished.error)
            else:
                self._trace_plan_task(node, finished, "plan:task_done")
        return current_plan, task_outputs, artifacts, failures

    async def _execute_plan_fanout(
        self,
        node: WorkflowNode,
        plan: PlanArtifact,
        context: CapabilityContext,
        state: Dict[str, Any],
    ) -> tuple[PlanArtifact, Dict[str, Any], list[WorkflowArtifact], list[str]]:
        pending_indexes = [
            index for index, task in enumerate(plan.tasks)
            if task.status not in ("done", "failed", "skipped")
        ]
        tasks = list(plan.tasks)
        for index in pending_indexes:
            tasks[index] = tasks[index].model_copy(update={"status": "in_progress", "error": None})
            self._trace_plan_task(node, tasks[index], "plan:task_started")
        started_plan = plan.model_copy(update={"tasks": list(tasks)})
        limit = node.max_parallel or (context.limits.max_parallel_children if context.limits else 4) or 4
        semaphore = asyncio.Semaphore(max(1, limit))

        async def invoke(index: int) -> tuple[int, CapabilityResult]:
            async with semaphore:
                result = await self._invoke_plan_task(node, tasks[index], context, state, started_plan)
                return index, result

        results = await asyncio.gather(*(invoke(index) for index in pending_indexes))
        task_outputs: Dict[str, Any] = {}
        artifacts: list[WorkflowArtifact] = []
        failures: list[str] = []
        for index, result in results:
            finished, output_key = self._finish_plan_task(node, tasks[index], result)
            tasks[index] = finished
            if output_key and result.output is not None:
                task_outputs[output_key] = result.output
            artifacts.extend(result.artifacts)
            if finished.status == "failed":
                failures.append(f"{finished.task_id}: {finished.error or 'failed'}")
                self._trace_plan_task(node, finished, "plan:task_failed", error=finished.error)
            else:
                self._trace_plan_task(node, finished, "plan:task_done")
        return plan.model_copy(update={"tasks": tasks}), task_outputs, artifacts, failures

    async def _invoke_plan_task(
        self,
        node: WorkflowNode,
        task: PlanTask,
        context: CapabilityContext,
        state: Dict[str, Any],
        plan: PlanArtifact,
    ) -> CapabilityResult:
        denial = self._planned_task_denial(task, state, context)
        if denial:
            return CapabilityResult(
                status="rejected",
                error=f"denied by policy: {', '.join(denial)}",
                metadata={"denied": denial},
            )
        task_context = self._context_for_node(node, context, state, plan=plan)
        return await self.runtime.invoke(task.capability, task.payload, task_context, attempt=1)

    def _planned_task_denial(
        self,
        task: PlanTask,
        state: Dict[str, Any],
        context: CapabilityContext,
    ) -> list[str]:
        plan = context.plan
        if plan is None:
            return []
        try:
            spec, _handler = self.runtime.registry.get(task.capability)
        except KeyError:
            return []
        if spec.metered and plan.limits and plan.limits.max_estimated_usd is not None:
            usage = state.get("usage_summary")
            spent = (getattr(usage, "estimated_usd", None) or 0.0) if usage is not None else 0.0
            if spent >= plan.limits.max_estimated_usd:
                return ["budget_exhausted"]
        return []

    @staticmethod
    def _finish_plan_task(
        node: WorkflowNode,
        task: PlanTask,
        result: CapabilityResult,
    ) -> tuple[PlanTask, Optional[str]]:
        if result.status in ("accepted", "partial"):
            output_key = f"{node.id}.{task.task_id}"
            return (
                task.model_copy(update={"status": "done", "error": None, "output_ref": output_key}),
                output_key,
            )
        return (
            task.model_copy(
                update={
                    "status": "failed",
                    "error": result.error or f"task capability returned {result.status}",
                    "output_ref": None,
                }
            ),
            None,
        )

    def _trace_plan_task(
        self,
        node: WorkflowNode,
        task: PlanTask,
        decision: str,
        *,
        error: Optional[str] = None,
    ) -> None:
        self.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                decision=decision,
                error=error,
                metadata={
                    "task_id": task.task_id,
                    "capability": task.capability,
                    "status": task.status,
                    "output_ref": task.output_ref,
                },
            )
        )

    def _build_evaluate_node(self, definition: WorkflowDefinition, node: WorkflowNode):
        evaluator = node.evaluator or node.id

        async def evaluate_fn(state: Dict[str, Any]) -> Dict[str, Any]:
            context: CapabilityContext = state[_CONTEXT]
            evaluated_payload = self._node_input(state, node)
            attempt = state.get("attempts", {}).get(node.id, 0) + 1
            eval_result = await self._invoke_bound(node, evaluator, evaluated_payload, context, state, attempt=attempt)
            decision = self._eval_decision(node, eval_result)
            counters = dict(
                state.get("eval_counters", {}).get(
                    node.id, {"retry": 0, "retrace": 0, "replan": 0}
                )
            )
            counters.setdefault("retry", 0)
            counters.setdefault("retrace", 0)
            counters.setdefault("replan", 0)
            effect = await self._apply_eval(node, decision, counters, context, definition, state, evaluated_payload)

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
            update = self._record(
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
            self.runtime.trace_sink.record(
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

    def _build_subworkflow_node(self, definition: WorkflowDefinition, node: WorkflowNode):
        ref = node.subworkflow

        async def subflow_fn(state: Dict[str, Any]) -> Dict[str, Any]:
            context: CapabilityContext = state[_CONTEXT]
            child_def = self.subworkflows.get(ref.workflow_id) if ref else None
            payload = self._node_input(state, node)
            if child_def is None:
                failed = CapabilityResult(
                    status="failed",
                    error=f"subworkflow '{node.id}' references unregistered workflow: {ref.workflow_id if ref else None}",
                )
                return self._record(state, node, failed, attempts=1, input_payload=payload)

            child_context = self._child_context(context, child_def, ref)
            child_result = await self._run_inner(child_def, payload, child_context)
            ok = child_result.status in ("completed", "partial", "requires_user_input")
            cap = CapabilityResult(
                status="accepted" if ok else "failed",
                output=child_result.output,
                artifacts=child_result.artifacts,
                error=child_result.error,
                metadata={"child_workflow": child_def.workflow_id, "child_status": child_result.status},
            )
            update = self._record(state, node, cap, attempts=1, input_payload=payload)
            self.runtime.trace_sink.record(
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

    def _build_human_node(self, definition: WorkflowDefinition, node: WorkflowNode):
        capability = node.capability or node.id

        async def human_fn(state: Dict[str, Any]) -> Dict[str, Any]:
            context: CapabilityContext = state[_CONTEXT]
            payload = self._node_input(state, node)
            result = await self.runtime.invoke(capability, payload, context)
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
            update = self._record(
                state, node, cap, attempts=1, input_payload=payload, force_status=node_status, error=result.error
            )
            update["routes"] = {**state.get("routes", {}), node.id: route}
            if node_status == "requires_user_input":
                update["status"] = "requires_user_input"
            self.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=node.id,
                    decision=clarification_status or "human",
                    metadata={"clarification_status": clarification_status},
                )
            )
            return update

        return human_fn

    def _child_context(
        self,
        parent: CapabilityContext,
        child_def: WorkflowDefinition,
        ref: Any,
    ) -> CapabilityContext:
        plan = parent.plan
        if plan is not None and ref is not None and (ref.budget_usd is not None or ref.max_steps is not None):
            limit_update: Dict[str, Any] = {}
            if ref.budget_usd is not None:
                limit_update["max_estimated_usd"] = ref.budget_usd
            if ref.max_steps is not None:
                limit_update["max_steps"] = ref.max_steps
            plan = plan.model_copy(update={"limits": plan.limits.model_copy(update=limit_update)})
        goal = parent.goal.model_copy(update={"workflow_type": child_def.workflow_id})
        return CapabilityContext(
            goal=goal,
            run_context=parent.run_context,
            plan=plan,
            usage_summary=parent.usage_summary,  # shared budget across parent + child
            limits=plan.limits if plan is not None else parent.limits,
            metadata=dict(parent.metadata),
        )

    # -- evaluate helpers ---------------------------------------------------------
    def _eval_decision(self, node: WorkflowNode, eval_result: CapabilityResult) -> EvaluationDecision:
        from ai_workflow_engine.workflow import Fallback, Replan, Retrace, Retry

        output = eval_result.output
        if isinstance(output, EvaluationDecision):
            return output
        if eval_result.status == "accepted":
            return EvaluationDecision(action="accept")
        if eval_result.status == "failed":
            return EvaluationDecision(action="fail", rationale=eval_result.error or "evaluator failed")
        # rejected / partial -> derive the corrective action from the declared on_reject policy.
        criticism = self._criticism_from(eval_result)
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

    @staticmethod
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
        self,
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
            pred = self._sequential_predecessor(definition, node.id)
            base = state.get("node_inputs", {}).get(pred, evaluated_payload)
            return {"route": "retry", "status": "rejected", "output": self._with_criticism(base, decision.criticism), "error": decision.rationale}
        if action == "retrace_to":
            counters["retrace"] += 1
            target = decision.retrace_to or (node.on_reject.target if isinstance(node.on_reject, Retrace) else None)
            base = state.get("node_inputs", {}).get(target, evaluated_payload)
            return {"route": "retrace", "status": "rejected", "output": self._with_criticism(base, decision.criticism), "error": decision.rationale}
        if action == "replan":
            counters["replan"] += 1
            target = decision.retrace_to or (node.on_reject.target if isinstance(node.on_reject, Replan) else None)
            base = state.get("node_inputs", {}).get(target, evaluated_payload)
            payload = {
                "input": base,
                "plan_artifact": self._dump_plan_for_payload(state.get("plan_artifact")),
            }
            return {
                "route": "replan",
                "status": "rejected",
                "output": self._with_criticism(payload, decision.criticism),
                "error": decision.rationale,
            }
        if action == "fallback":
            target = decision.target_capability or node.fallback_capability
            fb = await self.runtime.invoke(target, self._with_criticism(evaluated_payload, decision.criticism), context)
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

    @staticmethod
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

    @staticmethod
    def _dump_plan_for_payload(plan: Any) -> Any:
        if isinstance(plan, PlanArtifact):
            return plan.model_dump()
        if isinstance(plan, dict):
            return plan
        return None

    @staticmethod
    def _resolve_items(state: Dict[str, Any], node: WorkflowNode) -> Any:
        key = node.fan_items_key
        if not key:
            return state.get(_RUNNING_PAYLOAD)
        parts = key.split(".")
        head = parts[0]
        value = state.get(_RUNNING_PAYLOAD) if head == "payload" else state.get("node_outputs", {}).get(head)
        for attr in parts[1:]:
            if value is None:
                break
            value = value.get(attr) if isinstance(value, dict) else getattr(value, attr, None)
        return value

    @staticmethod
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
    @staticmethod
    def _node_input(state: Dict[str, Any], node: WorkflowNode) -> Any:
        if node.input_key:
            return state.get("node_outputs", {}).get(node.input_key)
        return state.get(_RUNNING_PAYLOAD)

    @staticmethod
    def _record(
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
        record = NodeResult(
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
        update: Dict[str, Any] = {
            _RUNNING_PAYLOAD: result.output,
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

    # ---------------------------------------------------------------- pre-flight + bounds
    def _preflight(self, definition: WorkflowDefinition) -> Optional[str]:
        structural = definition.validate_graph()
        if structural:
            return "; ".join(structural)
        known_caps = set(self.runtime.registry.names())
        for node in definition.nodes:
            if node.kind not in KNOWN_NODE_KINDS:
                return f"node '{node.id}' has unsupported kind: {node.kind}"
            if node.kind not in self._handlers:
                return f"node '{node.id}' kind '{node.kind}' is not implemented by this executor"
            if node.kind == "subworkflow":
                ref = node.subworkflow
                if ref is None or ref.workflow_id not in self.subworkflows:
                    return f"subworkflow node '{node.id}' references unregistered workflow"
                continue
            for cap in self._required_capabilities(node):
                if cap not in known_caps:
                    return f"node '{node.id}' binds to unregistered capability: {cap}"
            profile_error = self._model_profile_error(node)
            if profile_error:
                return profile_error
        return None

    def _model_profile_error(self, node: WorkflowNode) -> Optional[str]:
        """Validate declarative model binding before any execution (loud, never mid-run)."""

        if not node.model_profile:
            return None
        if node.model_profile not in self.model_profiles:
            return (
                f"node '{node.id}' references unknown model profile: {node.model_profile} "
                f"(registered: {sorted(self.model_profiles) or 'none'})"
            )
        # RC1 (where statically visible): a capability whose handler advertises a fixed LLM client
        # cannot honor a model profile — declaring both is a configuration conflict, not a
        # preference. Handlers that don't expose the attribute are checked loudly at call time.
        capability = node.effective_capability()
        if capability:
            try:
                _spec, handler = self.runtime.registry.get(capability)
            except KeyError:
                return None
            if getattr(handler, "accepts_model_profile", None) is False:
                return (
                    f"node '{node.id}' declares model_profile={node.model_profile!r} but capability "
                    f"'{capability}' has a fixed llm client; use llm_factory or drop the binding"
                )
        return None

    @staticmethod
    def _required_capabilities(node: WorkflowNode) -> List[str]:
        caps: List[str] = []
        if node.kind in ("step", "human", "planner"):
            caps.append(node.capability or node.id)
        elif node.kind == "branch":
            caps.append(node.decider or node.capability or node.id)
        elif node.kind == "fanout":
            caps.append(node.item_capability or node.capability or node.id)
        elif node.kind == "evaluate":
            if node.evaluator:
                caps.append(node.evaluator)
            if node.fallback_capability:
                caps.append(node.fallback_capability)
        return [cap for cap in caps if cap]

    def _recursion_limit(self, definition: WorkflowDefinition, context: CapabilityContext) -> int:
        limits = context.limits
        loops = (limits.max_retries + limits.max_retrace + 1) if limits else 1
        return 25 + len(definition.nodes) * (2 + loops)

    # ---------------------------------------------------------------- envelopes
    def _envelope(self, definition: WorkflowDefinition, final_state: Dict[str, Any]) -> WorkflowRunResult:
        node_results: List[NodeResult] = list(final_state.get("node_results", []))
        explicit = final_state.get("status")
        status: WorkflowResultStatus
        if explicit == "failed":
            status = "failed"
        elif explicit == "requires_user_input":
            status = "requires_user_input"
        elif any(record.status == "partial" for record in node_results):
            status = "partial"
        else:
            status = "completed"
        usage = final_state.get("usage_summary") or WorkflowUsageSummary()
        return WorkflowRunResult(
            workflow_id=definition.workflow_id,
            status=status,
            output=final_state.get(_RUNNING_PAYLOAD),
            error=final_state.get("error"),
            fallback_reason=final_state.get("fallback_reason"),
            node_results=node_results,
            artifacts=list(final_state.get("artifacts", [])),
            usage=usage,
            trace=self._trace_events(),
        )

    def _failed_envelope(self, definition: WorkflowDefinition, error: str) -> WorkflowRunResult:
        # Loud failure: surface error + emit a trace event; never run a downgraded path.
        self.runtime.trace_sink.record(
            WorkflowTraceEvent(node=definition.workflow_id, decision="rejected", error=error)
        )
        return WorkflowRunResult(
            workflow_id=definition.workflow_id,
            status="failed",
            error=error,
            trace=self._trace_events(),
        )

    def _trace_events(self) -> List[WorkflowTraceEvent]:
        events = getattr(self.runtime.trace_sink, "events", None)
        return list(events) if isinstance(events, list) else []
