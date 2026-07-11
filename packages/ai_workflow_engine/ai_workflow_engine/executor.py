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
import logging
from contextvars import ContextVar
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from ai_workflow_engine.engine.capabilities import (
    CapabilityCall,
    CapabilityRuntime,
    gather_capabilities,
)

logger = logging.getLogger(__name__)

# R-C2-2: private staging channel for authored-flow run-birth provenance. ONLY
# WorkflowEngine.run_authored_flow may set it; the executor consumes it exactly once per run.
# It is deliberately NOT a run() parameter — a caller-forged flow:authored event must not be
# expressible through any public signature.
_AUTHORED_PROVENANCE: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "ai_workflow_engine_authored_provenance", default=None
)
from ai_workflow_engine.engine.runner import WorkflowRunner
from ai_workflow_engine.engine.scheduler import WorkflowScheduler
from ai_workflow_engine.model_binding import model_profile_scope
from ai_workflow_engine.node_services import ExecutorNodeServices, NodeExecutionServices
from ai_workflow_engine.nodes import NODE_HANDLERS
from ai_workflow_engine.run_session import WorkflowRunSession
from ai_workflow_engine._runtime_state import (
    CONTEXT,
    RUNNING_PAYLOAD,
    observation_capture_scope,
    run_session_scope,
)
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
from ai_workflow_engine.snapshot import MachineSnapshot
from ai_workflow_engine.workflow import (
    END,
    KNOWN_NODE_KINDS,
    BranchDecision,
    WorkflowDefinition,
    WorkflowNode,
    render_machine_card,
)

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
    transition_counts: Dict[str, int]
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
    resume_suspended_node: Optional[str]
    resume_event: Any
    machine_replay_done: bool


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
    # Where this run's durable observation bundle lives (set when a bundle was attached).
    observation_bundle_path: Optional[str] = None
    # Durable suspension: set ONLY when status == "requires_user_input" — feed it back through
    # engine.resume(snapshot, event) to continue the machine without re-executing anything.
    snapshot: Optional[MachineSnapshot] = None

    def node(self, node_id: str) -> Optional[NodeResult]:
        for record in reversed(self.node_results):
            if record.node_id == node_id:
                return record
        return None


# A node handler builds and returns a coroutine function for one node in one workflow.
# Handlers receive the narrow NodeExecutionServices boundary, never the executor itself.
NodeHandler = Callable[[NodeExecutionServices, WorkflowDefinition, WorkflowNode], Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]]


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
        # The narrow boundary handed to node handlers (executor internals stay private).
        self._node_services: NodeExecutionServices = ExecutorNodeServices(self)
        # Node-kind handler table. Kinds present here are *implemented*; any valid-but-absent
        # kind fails loudly (never silently downgraded). Phases register more kinds.
        self._handlers: Dict[str, NodeHandler] = dict(NODE_HANDLERS)

    # ---------------------------------------------------------------- public API
    def supported_kinds(self) -> set[str]:
        return set(self._handlers)

    def register_subworkflow(self, definition: WorkflowDefinition) -> None:
        existing = self.subworkflows.get(definition.workflow_id)
        if existing is not None and existing.definition_digest() != definition.definition_digest():
            # Same id, different machine: drop every compiled graph for the id so nothing can
            # keep executing the superseded definition (memory hygiene + correctness).
            self._evict_compiled(definition.workflow_id)
        self.subworkflows[definition.workflow_id] = definition

    def _evict_compiled(self, workflow_id: str) -> None:
        for key in [k for k in self._compiled if k[0] == workflow_id]:
            self._compiled.pop(key, None)

    async def run(
        self,
        definition: WorkflowDefinition,
        payload: Any,
        context: CapabilityContext,
        *,
        recursion_fallback: Optional[Callable[..., Any]] = None,
        recursion_limit: Optional[int] = None,
        observation_bundle: Any = None,
        terminal_status: Optional[Callable[[WorkflowRunResult], Optional[str]]] = None,
    ) -> WorkflowRunResult:
        """Execute ``definition`` from ``payload`` under ``context`` and return the envelope.

        A4 (single bundle owner): pass ``observation_bundle`` and the RUN SESSION owns its
        lifecycle — finalized exactly once with the run's true terminal status, including
        when the run raises. ``terminal_status`` is the narrow product hook: it sees the
        finished envelope and may override the archived status (e.g. product post-validation
        deciding "failed") BEFORE the bundle is finalized — the durable record never says
        completed for a user-visible failure.
        """

        self._bind_or_validate_event_loop()
        # Pre-flight: bindings and node kinds must resolve, or fail loudly + trace (no run).
        binding_error = self._preflight(definition)
        if binding_error is not None:
            envelope = self._failed_envelope(definition, binding_error)
            if observation_bundle is not None:
                WorkflowRunSession(
                    workflow_id=definition.workflow_id,
                    context=context,
                    definition=definition,
                    bundle=observation_bundle,
                ).close("failed")
            return envelope

        compiled = self.compile(definition)
        limit = recursion_limit or self._recursion_limit(definition, context)
        graph_config = {"recursion_limit": limit}
        # H2: one session per run — the single home for run identity + usage aggregation
        # + the run-scoped trace buffer the envelope reads (B6/B7).
        session = WorkflowRunSession(
            workflow_id=definition.workflow_id,
            context=context,
            definition=definition,
            bundle=observation_bundle,
        )
        # Top-level run: WorkflowRunner installs the usage/budget scope + lifecycle logging.
        try:
            with observation_capture_scope(self.runtime.observation), run_session_scope(session):
                # Run-birth provenance (R7+R12+R-C2-2): engine-owned and UNFORGEABLE from the
                # public surface — no run() parameter carries it. run_authored_flow stages the
                # typed payload in a module-private context variable; the executor CONSUMES it
                # exactly once here (so nested/subsequent runs in the same context never
                # inherit it), byte-checks it, and constructs the ONE event itself (fresh
                # event id, run-id stamped, inside the session).
                _authored_provenance = _AUTHORED_PROVENANCE.get()
                if _authored_provenance is not None:
                    _AUTHORED_PROVENANCE.set(None)  # consume-once
                    from ai_workflow_engine.byte_safety import assert_byte_safe

                    assert_byte_safe(
                        dict(_authored_provenance), mode="prompt", path="authored_provenance"
                    )
                    self.runtime.trace_sink.record(
                        WorkflowTraceEvent(
                            node=definition.workflow_id,
                            decision="flow:authored",
                            run_id=str(context.run_context.workflow_id),
                            metadata=dict(_authored_provenance),
                        )
                    )
                final_state = await self.runner.run(
                    compiled,
                    self._initial_state(payload, context),
                    workflow_type=context.goal.workflow_type,
                    goal=context.goal,
                    graph_config=graph_config,
                    recursion_fallback=recursion_fallback,
                    session=session,
                )
        except Exception:
            # The run raised: the bundle must still close, truthfully, as failed.
            session.close("failed")
            raise
        envelope = self._envelope(definition, final_state, session=session)
        if terminal_status is not None:
            # B-post2: the hook must never desynchronize the durable record from the returned
            # result, and a raising hook must never leave the bundle unfinalized.
            try:
                override = terminal_status(envelope)
            except Exception:
                session.close("failed")
                raise
            if override:
                valid = {"completed", "partial", "failed", "requires_user_input"}
                if override not in valid:
                    session.close("failed")
                    raise ValueError(
                        f"terminal_status hook returned invalid status {override!r} "
                        f"(allowed: {sorted(valid)})"
                    )
                if override == "requires_user_input" and envelope.snapshot is None:
                    # A result that asks the caller to resume MUST carry a resumable snapshot;
                    # a hook cannot conjure a suspension out of a completed machine.
                    session.close("failed")
                    raise ValueError(
                        "terminal_status hook returned 'requires_user_input' but the run has no "
                        "machine snapshot — suspension must come from the workflow itself"
                    )
                envelope = envelope.model_copy(update={"status": override})
        session.close(envelope.status, artifacts=envelope.artifacts)
        if session.bundle is not None and getattr(session.bundle, "path", None):
            envelope = envelope.model_copy(
                update={"observation_bundle_path": str(session.bundle.path)}
            )
        return envelope

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
        with observation_capture_scope(self.runtime.observation):
            final_state = await compiled.ainvoke(self._initial_state(payload, context), config=graph_config)
        return self._envelope(definition, final_state)

    async def resume(
        self,
        definition: WorkflowDefinition,
        snapshot: MachineSnapshot,
        event_payload: Any,
        context: CapabilityContext,
        observation_bundle: Optional[Any] = None,
    ) -> WorkflowRunResult:
        """Continue a suspended machine: fast-forward replay (zero re-execution — recorded routes
        steer the compiled graph) to the suspended node, execute it live with the resume event,
        then run on normally. Restored usage seeds the run scope, so budgets stay cumulative."""

        if snapshot.workflow_id != definition.workflow_id:
            raise ValueError(
                f"snapshot is for workflow '{snapshot.workflow_id}', not '{definition.workflow_id}'"
            )
        if not snapshot.suspended_node:
            raise ValueError("snapshot has no suspended node — only suspended runs can resume")
        self._bind_or_validate_event_loop()
        binding_error = self._preflight(definition)
        if binding_error is not None:
            return self._failed_envelope(definition, binding_error)
        compiled = self.compile(definition)
        state = self._initial_state(snapshot.payload, context)
        node_status = dict(snapshot.node_status)
        routes = dict(snapshot.routes)
        # The suspended node re-executes live (with the event); clear its halt verdict.
        node_status.pop(snapshot.suspended_node, None)
        routes.pop(snapshot.suspended_node, None)
        state.update(
            {
                "node_outputs": {
                    **snapshot.node_outputs,
                    "__input__": snapshot.node_outputs.get("__input__", snapshot.payload),
                },
                "node_inputs": dict(snapshot.node_inputs),
                "node_status": node_status,
                "routes": routes,
                "branch_decisions": dict(snapshot.branch_decisions),
                "eval_counters": {k: dict(v) for k, v in snapshot.eval_counters.items()},
                "transition_counts": dict(snapshot.transition_counts),
                "attempts": dict(snapshot.attempts),
                "node_results": [NodeResult.model_validate(r) for r in snapshot.node_results],
                "artifacts": [
                    WorkflowArtifact.model_validate(a) if isinstance(a, dict) else a
                    for a in snapshot.artifacts
                ],
                "plan_artifact": snapshot.plan_artifact,
                "fallback_reason": snapshot.fallback_reason,
                "resume_suspended_node": snapshot.suspended_node,
                "resume_event": event_payload,
                "machine_replay_done": False,
            }
        )
        restored_usage = (
            WorkflowUsageSummary.model_validate(snapshot.usage) if snapshot.usage else None
        )
        # H2: resume rebuilds the session from the snapshot — budgets stay cumulative.
        session = WorkflowRunSession(
            workflow_id=definition.workflow_id,
            context=context,
            usage_summary=restored_usage,
            definition=definition,
            # Q-R5: the RESUMED half is a run too — it gets its own observation bundle, so
            # the suspend→resume lifecycle is inspectable end to end, not only in memory.
            bundle=observation_bundle,
        )
        with observation_capture_scope(self.runtime.observation), run_session_scope(session):
            # Recorded inside the session scope so the resumed-run envelope carries it too.
            self.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=snapshot.suspended_node,
                    decision="machine:resumed",
                    run_id=context.run_context.workflow_id,
                    metadata={
                        "workflow_id": definition.workflow_id,
                        "completed_nodes": len(snapshot.node_results),
                    },
                )
            )
            final_state = await self.runner.run(
                compiled,
                state,
                workflow_type=context.goal.workflow_type,
                goal=context.goal,
                graph_config={"recursion_limit": self._recursion_limit(definition, context)},
                session=session,
            )
        envelope = self._envelope(definition, final_state, session=session)
        session.close(envelope.status, artifacts=envelope.artifacts)
        if session.bundle is not None and getattr(session.bundle, "path", None):
            envelope = envelope.model_copy(
                update={"observation_bundle_path": str(session.bundle.path)}
            )
        return envelope

    def _with_replay(self, node: WorkflowNode, fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]):
        """Resume fast-forward: while a restored run replays, completed nodes no-op (recorded
        routes steer the graph) until the suspended node, which executes live. Normal runs pass
        straight through (the resume keys are simply absent)."""

        async def replay_aware(state: Dict[str, Any]) -> Dict[str, Any]:
            suspended = state.get("resume_suspended_node")
            if suspended and not state.get("machine_replay_done"):
                if node.id != suspended:
                    self.runtime.trace_sink.record(
                        WorkflowTraceEvent(
                            node=node.id,
                            decision="machine:fastforward",
                            metadata={"status": state.get("node_status", {}).get(node.id)},
                        )
                    )
                    return {}
                update = await fn(state)
                update["machine_replay_done"] = True
                return update
            return await fn(state)

        return replay_aware

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
            RUNNING_PAYLOAD: payload,
            CONTEXT: context,
            # "__input__" lets any node read the original workflow input via input_key.
            "node_outputs": {"__input__": payload},
            "node_inputs": {},
            "node_results": [],
            "node_status": {},
            "branch_decisions": {},
            "routes": {},
            "transition_counts": {},
            "eval_counters": {},
            "attempts": {},
            "artifacts": [],
            "status": "running",
            "error": None,
            "fallback_reason": None,
            "plan_artifact": None,
        }

    def compile(self, definition: WorkflowDefinition) -> Any:
        """Compile a definition into a runnable LangGraph (cached by id + content digest).

        The digest in the key is the B1 fix: a re-registered definition with the same
        ``workflow_id`` but different content compiles fresh instead of silently executing
        the stale graph.
        """

        cache_key = (definition.workflow_id, definition.definition_digest())
        cached = self._compiled.get(cache_key)
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
            graph.add_node(node.id, self._with_replay(node, handler(self._node_services, definition, node)))

        graph.add_edge(START, definition.entry)
        for node in definition.nodes:
            self._wire_edges(graph, definition, node, LG_END)

        compiled = graph.compile()
        self._compiled[cache_key] = compiled
        return compiled

    # ---------------------------------------------------------------- transition wiring
    def _wire_edges(self, graph: Any, definition: WorkflowDefinition, node: WorkflowNode, lg_end: Any) -> None:
        """Wire one node's outgoing transitions — ONE generic path for every node kind, reading
        the definition's first-class transitions (the machine routes itself from its own data)."""

        outs = definition.outgoing(node.id)
        if node.kind == "branch":
            path_map: Dict[str, Any] = {
                t.label: (lg_end if t.target == END else t.target)
                for t in outs
                if t.policy == "decision" and t.label
            }
            path_map["__invalid__"] = lg_end
            path_map["__halt__"] = lg_end
        elif node.kind == "evaluate":
            path_map = {"accept": lg_end, "retry": lg_end, "retrace": lg_end, "replan": lg_end, "halt": lg_end}
            for t in outs:
                if t.policy in ("on_accept", "on_reject") and t.label in path_map and t.target != END:
                    path_map[t.label] = t.target
        else:
            nxt = next((t for t in outs if t.policy == "always"), None)
            path_map = {
                "__next__": lg_end if (nxt is None or nxt.target == END) else nxt.target,
                "__halt__": lg_end,
            }
        graph.add_conditional_edges(node.id, self._route_for(node), path_map)

    @staticmethod
    def _sequential_successor(definition: WorkflowDefinition, node_id: str) -> Optional[str]:
        for t in definition.transitions:
            if t.source == node_id and t.policy == "always":
                return t.target
        return None

    @staticmethod
    def _sequential_predecessor(definition: WorkflowDefinition, node_id: str) -> Optional[str]:
        for t in definition.transitions:
            if t.target == node_id and t.policy == "always":
                return t.source
        return None

    @staticmethod
    def _route_for(node: WorkflowNode) -> Callable[[Dict[str, Any]], str]:
        """One routing convention for every node kind: handlers record the taken route under
        ``routes[node.id]``; the router only reads state (pure, no side effects)."""

        if node.kind == "branch":
            def route(state: Dict[str, Any]) -> str:
                label = state.get("routes", {}).get(node.id)
                if label == "__halt__":
                    return "__halt__"
                return label if label in node.branches else "__invalid__"
        elif node.kind == "evaluate":
            def route(state: Dict[str, Any]) -> str:
                routes = state.get("routes", {})
                if node.id not in routes:
                    # Engine invariant: the evaluate handler ALWAYS sets a route. A missing
                    # entry is a plumbing bug — defaulting to "halt" here would end the run
                    # cleanly and hide it.
                    raise KeyError(
                        f"evaluate node '{node.id}' produced no route decision — engine "
                        "invariant violated"
                    )
                return routes[node.id]
        else:
            def route(state: Dict[str, Any]) -> str:
                if state.get("routes", {}).get(node.id) == "halt":
                    return "__halt__"
                if state.get("node_status", {}).get(node.id) == "failed":
                    return "__halt__"
                return "__next__"
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
        definition: Optional[WorkflowDefinition] = None,
    ) -> CapabilityResult:
        """Invoke a capability under the node's declared model profile (if any), traced."""

        context = self._context_for_node(node, context, state, definition=definition)
        if node.memory is not None:
            # GoPro R4: node-scoped agent-memory selection rides context metadata; the
            # planner resolves it per call (validated loudly at graph validation).
            context = context.model_copy(
                update={"metadata": {**context.metadata, "agent_memory": node.memory}}
            )
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
            # The machine describes itself to its navigator: legal moves + LIVE gate budgets.
            extra["machine"] = render_machine_card(definition, node.id, state)
        if not extra:
            return context
        return context.model_copy(update={"metadata": {**context.metadata, **extra}})

    # ---------------------------------------------------------------- node handlers
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
    @staticmethod
    def _node_input(state: Dict[str, Any], node: WorkflowNode) -> Any:
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
                nested = self._nested_suspension_error(node.id, ref.workflow_id)
                if nested is not None:
                    return nested
                continue
            for cap in self._required_capabilities(node):
                if cap not in known_caps:
                    return f"node '{node.id}' binds to unregistered capability: {cap}"
            profile_error = self._model_profile_error(node)
            if profile_error:
                return profile_error
        return None

    def _nested_suspension_error(self, node_id: str, child_workflow_id: str) -> Optional[str]:
        """B2 stage-1: nested suspension is not supported yet — reject it LOUDLY at preflight.

        A child (transitively) containing ``human`` nodes could suspend, but the parent has no
        representation for a child wait (no nested snapshot / resume dispatch), so accepting the
        graph would silently break "every wait resumable". Nested snapshots are a future,
        named-consumer-gated feature.
        """

        seen: set[str] = set()
        stack = [child_workflow_id]
        while stack:
            workflow_id = stack.pop()
            if workflow_id in seen:
                continue
            seen.add(workflow_id)
            child = self.subworkflows.get(workflow_id)
            if child is None:
                continue  # unregistered child is reported by the caller's check
            for child_node in child.nodes:
                if child_node.kind == "human":
                    return (
                        f"subworkflow node '{node_id}' -> workflow '{workflow_id}' contains human "
                        f"node '{child_node.id}': nested suspension is not supported yet — move "
                        f"the human gate to the top-level workflow"
                    )
                if child_node.kind == "subworkflow" and child_node.subworkflow is not None:
                    stack.append(child_node.subworkflow.workflow_id)
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
    def _envelope(
        self,
        definition: WorkflowDefinition,
        final_state: Dict[str, Any],
        session: Optional[WorkflowRunSession] = None,
    ) -> WorkflowRunResult:
        node_results: List[NodeResult] = list(final_state.get("node_results", []))
        run_context = final_state.get("workflow_context") or getattr(
            final_state.get(CONTEXT), "run_context", None
        )
        run_id = str(run_context.workflow_id) if run_context and run_context.workflow_id else None
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
        usage = final_state.get("usage_summary")
        if usage is None:
            # Runner always seeds the summary; absence means a plumbing bug upstream. Report
            # an empty summary rather than crash the result build, but never silently — a
            # fabricated $0 would read as cost truth.
            logger.warning("run produced no usage_summary — reporting an empty one (plumbing bug?)")
            usage = WorkflowUsageSummary()
        snapshot: Optional[MachineSnapshot] = None
        if status == "requires_user_input":
            suspended = next(
                (r.node_id for r in reversed(node_results) if r.status == "requires_user_input"),
                None,
            )
            if suspended is not None:
                plan = final_state.get("plan_artifact")
                if plan is not None and hasattr(plan, "model_dump"):
                    plan = plan.model_dump()
                goal = final_state.get("workflow_goal")
                snapshot_run_context = final_state.get("workflow_context")
                snapshot = MachineSnapshot(
                    workflow_id=definition.workflow_id,
                    suspended_node=suspended,
                    payload=final_state.get(RUNNING_PAYLOAD),
                    node_outputs=dict(final_state.get("node_outputs", {})),
                    node_inputs=dict(final_state.get("node_inputs", {})),
                    node_status=dict(final_state.get("node_status", {})),
                    routes=dict(final_state.get("routes", {})),
                    branch_decisions=dict(final_state.get("branch_decisions", {})),
                    eval_counters={
                        k: dict(v) for k, v in final_state.get("eval_counters", {}).items()
                    },
                    transition_counts=dict(final_state.get("transition_counts", {})),
                    attempts=dict(final_state.get("attempts", {})),
                    node_results=[r.model_dump() for r in node_results],
                    artifacts=[
                        a.model_dump() if hasattr(a, "model_dump") else a
                        for a in final_state.get("artifacts", [])
                    ],
                    plan_artifact=plan,
                    usage=usage.model_dump() if hasattr(usage, "model_dump") else {},
                    fallback_reason=final_state.get("fallback_reason"),
                    goal=goal.model_dump() if hasattr(goal, "model_dump") else None,
                    run_context=(
                        snapshot_run_context.model_dump()
                        if hasattr(snapshot_run_context, "model_dump")
                        else None
                    ),
                )
        return WorkflowRunResult(
            workflow_id=definition.workflow_id,
            status=status,
            output=final_state.get(RUNNING_PAYLOAD),
            error=final_state.get("error"),
            fallback_reason=final_state.get("fallback_reason"),
            node_results=node_results,
            artifacts=list(final_state.get("artifacts", [])),
            usage=usage,
            # B6/B7: the session's run-scoped buffer is exact and sink-shape independent;
            # sink sniffing remains only for legacy paths without a session (child runs).
            trace=list(session.trace_events) if session is not None else self._trace_events(run_id=run_id),
            snapshot=snapshot,
        )

    def _failed_envelope(self, definition: WorkflowDefinition, error: str) -> WorkflowRunResult:
        # Loud failure: surface error + emit a trace event; never run a downgraded path.
        event = WorkflowTraceEvent(node=definition.workflow_id, decision="rejected", error=error)
        self.runtime.trace_sink.record(event)
        return WorkflowRunResult(
            workflow_id=definition.workflow_id,
            status="failed",
            error=error,
            trace=[event],
        )

    def _trace_events(self, *, run_id: str | None = None) -> List[WorkflowTraceEvent]:
        events = getattr(self.runtime.trace_sink, "events", None)
        if not isinstance(events, list):
            return []
        if run_id is None:
            return list(events)
        return [event for event in events if event.run_id == run_id]
