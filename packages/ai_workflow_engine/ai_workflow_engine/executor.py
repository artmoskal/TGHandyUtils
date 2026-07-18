"""Executable workflow runtime.

``WorkflowExecutor`` is the run/resume coordinator. It composes dedicated owners for machine
compilation, node execution, suspension registration, and result assembly while keeping LangGraph
an internal backend. Products see only ``WorkflowBuilder``/``WorkflowEngine``.

Control loops that are naturally node-local (retry, evaluator retry/retrace, fan-out gather) stay
inside the relevant node. The compiled graph therefore remains a predictable DAG with explicit,
bounded back-edges only where a workflow declares graph-level retrace.

Public concepts: ``WorkflowExecutor``, ``NodeExecutionState``, ``NodeResult``,
``WorkflowRunResult``, ``UnsupportedNodeError``, ``CapabilityBindingError``.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional, TypedDict

from pydantic import field_validator, model_validator, BaseModel, ConfigDict, Field

from ai_workflow_engine.engine.capabilities import CapabilityRuntime

# R-C2-2: private staging channel for authored-flow run-birth provenance. ONLY
# WorkflowEngine.run_authored_flow may set it; the executor consumes it exactly once per run.
# It is deliberately NOT a run() parameter — a caller-forged flow:authored event must not be
# expressible through any public signature.
_AUTHORED_PROVENANCE: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "ai_workflow_engine_authored_provenance", default=None
)
from ai_workflow_engine.engine.runner import WorkflowRunner
from ai_workflow_engine.machine_compiler import WorkflowMachineCompiler
from ai_workflow_engine.node_services import (
    ExecutorNodeServices,
    NodeSchedulingRuntime,
)
from ai_workflow_engine.node_replay import NodeReplayRuntime
from ai_workflow_engine.result_assembly import RunResultAssembler
from ai_workflow_engine.nodes import NODE_HANDLERS
from ai_workflow_engine.run_session import child_trace_slice, WorkflowRunSession
from ai_workflow_engine._runtime_state import (
    CONTEXT,
    current_run_session,
    RUNNING_PAYLOAD,
    observation_capture_scope,
    retrace_provenance_scope,
    run_session_scope,
)
from ai_workflow_engine.models import (
    CapabilityContext,
    WorkflowArtifact,
    WorkflowResultStatus,
    WorkflowTraceEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.snapshot import MachineSnapshot
from ai_workflow_engine.suspension import SuspensionCoordinator
from ai_workflow_engine.wait_contract import WaitHandle
from ai_workflow_engine.workflow import WorkflowDefinition, WorkflowNode

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
    # v0.10: {"target": <node id>, "provenance": RetraceProvenance} set by the evaluate
    # retrace route; consumed exactly once when the target node re-runs.
    pending_retrace_provenance: Any


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
    # LOCAL suspension: set ONLY when status == "requires_user_input" AND the wait is a
    # LocalWaitPolicy — feed it back through engine.resume(snapshot, event). Registered
    # DURABLE waits expose wait_handle INSTEAD (C1/W1.4): exactly one of the two, never both.
    snapshot: Optional[MachineSnapshot] = None
    # W1R.2b: GENUINELY typed — runtime validation, static consumers, and the generated
    # JSON schema all see the same WaitHandle contract (tiny contract leaf; the full waits
    # module stays out of the simple tier).
    wait_handle: Optional[WaitHandle] = None

    @model_validator(mode="after")
    def _status_dependent_wait_door(self) -> "WorkflowRunResult":
        return self._assert_wait_doors()

    def _assert_wait_doors(self) -> "WorkflowRunResult":
        """W1R.2 invariant: requires_user_input carries EXACTLY ONE public door (local
        snapshot XOR durable handle); every other status carries NEITHER."""

        has_snapshot = self.snapshot is not None
        has_handle = self.wait_handle is not None
        if self.status == "requires_user_input":
            if has_snapshot == has_handle:  # both or neither
                raise ValueError(
                    "requires_user_input must carry exactly one public wait door — "
                    f"snapshot={has_snapshot}, wait_handle={has_handle}"
                )
        elif has_snapshot or has_handle:
            raise ValueError(
                f"status {self.status!r} must carry no wait door — "
                f"snapshot={has_snapshot}, wait_handle={has_handle}"
            )
        return self

    def model_copy(self, *, update: Optional[Dict[str, Any]] = None, deep: bool = False) -> "WorkflowRunResult":
        # W1R.2b: copies are FULLY validated — reconstruction through the constructor runs
        # every field validator and the status XOR on the ACTUAL objects (no serialization),
        # so no engine or public update path can smuggle a forged handle or invalid status.
        copied = super().model_copy(update=update, deep=deep)
        return WorkflowRunResult(**{name: getattr(copied, name) for name in WorkflowRunResult.model_fields})

    def node(self, node_id: str) -> Optional[NodeResult]:
        for record in reversed(self.node_results):
            if record.node_id == node_id:
                return record
        return None


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
        self.runner = WorkflowRunner(config, trace_sink=runtime.trace_sink)
        self._bound_loop: Optional[asyncio.AbstractEventLoop] = None
        # ModelProfile registry for declarative per-node model binding (set by WorkflowEngine).
        self._model_profiles: Dict[str, Any] = {}
        self._node_scheduling = NodeSchedulingRuntime()
        self._node_services: ExecutorNodeServices = ExecutorNodeServices(
            runtime=runtime,
            subworkflows=self.subworkflows,
            model_profiles=self._model_profiles,
            child_run=self._run_inner,
            node_result_factory=NodeResult,
            capability_binding_error=CapabilityBindingError,
            scheduling=self._node_scheduling,
        )
        self._suspension = SuspensionCoordinator(runtime)
        self._results = RunResultAssembler(
            runtime=runtime,
            result_factory=WorkflowRunResult,
            suspension=self._suspension,
        )
        # Node-kind handler table. Kinds present here are *implemented*; any valid-but-absent
        # kind fails loudly (never silently downgraded). Phases register more kinds.
        handlers = dict(NODE_HANDLERS)
        self._compiler = WorkflowMachineCompiler(
            runtime=runtime,
            handlers=handlers,
            node_services=self._node_services,
            replay_runtime=NodeReplayRuntime(runtime),
            state_schema=WorkflowState,
            subworkflows=self.subworkflows,
            model_profiles=self._model_profiles,
            unsupported_node_error=UnsupportedNodeError,
        )

    @property
    def model_profiles(self) -> Dict[str, Any]:
        return self._model_profiles

    @model_profiles.setter
    def model_profiles(self, profiles: Dict[str, Any]) -> None:
        self._model_profiles = profiles
        if hasattr(self, "_node_services"):
            self._node_services.set_model_profiles(profiles)
        if hasattr(self, "_compiler"):
            self._compiler.set_model_profiles(profiles)

    @property
    def scheduler(self) -> Any:
        """The shared scheduler, exposed for runtime inspection and policy tests."""

        return self._node_scheduling.scheduler

    @property
    def wait_runtime(self) -> Any:
        return self._suspension.wait_runtime

    @wait_runtime.setter
    def wait_runtime(self, wait_runtime: Any) -> None:
        self._suspension.set_wait_runtime(wait_runtime)
        if hasattr(self, "_compiler"):
            self._compiler.set_wait_runtime(wait_runtime)

    # ---------------------------------------------------------------- public API
    def supported_kinds(self) -> set[str]:
        return self._compiler.supported_kinds()

    def register_subworkflow(self, definition: WorkflowDefinition) -> None:
        self._compiler.register_subworkflow(definition)

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
            envelope = self._results.failed(definition, binding_error)
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
                session.start_execution_window(
                    run_timeout_s=context.limits.timeout_s if context.limits else None,
                    completion_reserve_s=getattr(
                        self.runner, "graph_cancellation_grace_s", 0.0
                    ),
                )
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
                            # the announcement is terminal the instant it is recorded — NEW
                            # events carry the TYPED lifecycle; string inference is legacy-only
                            node_status="completed",
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
                # W2.3: durable suspensions register BEFORE anything is exposed. A
                # registration failure converts the run to FAILED with NO public wait door
                # (the bundle finalizes failed; no orphan snapshot/handle is representable).
                durable_handle, final_state = await self._suspension.register_or_fold(
                    definition, final_state, context, session
                )
        except Exception:
            # The run raised: the bundle must still close, truthfully, as failed.
            session.close("failed")
            raise
        envelope = self._results.envelope(definition, final_state, session=session)
        if durable_handle is not None:
            # handle-only public result for registered waits (C1): validated model_copy
            envelope = envelope.model_copy(
                update={"snapshot": None, "wait_handle": durable_handle}
            )
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
        return self._finalize_result(session, envelope)

    async def _run_inner(
        self,
        definition: WorkflowDefinition,
        payload: Any,
        context: CapabilityContext,
    ) -> WorkflowRunResult:
        """Run a child workflow inside the parent's already-installed usage/budget scope.

        Used by subworkflow nodes so child metered calls aggregate into the parent's usage
        summary (shared budget) instead of opening a fresh scope.

        This is the UNIVERSAL child-workflow boundary (declared subworkflow nodes AND
        workflows registered as ordinary capabilities), so it also owns retrace isolation
        (C1, FENCE-4): a retraced PARENT's ambient provenance is shielded to ``None`` for
        the entire child preflight/compile/run/envelope path — child node invocations never
        see parent provenance, while a child-internal retrace still publishes its own inside
        the shield. The parent's value is restored on every exit path by the scope owner.
        """

        self._bind_or_validate_event_loop()
        with retrace_provenance_scope(None):
            # CXR-1: the boundary owns BOTH provenance surfaces — the ambient scope above
            # AND any explicit stale value already carried by the incoming context. The
            # built-in doors build fresh child contexts, but the universal owner must not
            # depend on caller discipline.
            if getattr(context, "retrace_provenance", None) is not None:
                context = context.model_copy(update={"retrace_provenance": None})
            binding_error = self._preflight(definition)
            if binding_error is not None:
                return self._results.failed(definition, binding_error)
            compiled = self.compile(definition)
            graph_config = {"recursion_limit": self._recursion_limit(definition, context)}
            with observation_capture_scope(self.runtime.observation):
                child_state = self._initial_state(payload, context)
                if (session := current_run_session()) is not None:
                    # Child workflows share the parent's usage/budget scope. Carry that same
                    # summary into the child envelope too; an empty fabricated summary would
                    # make nested costs look like zero and emit a false plumbing warning on
                    # every run.
                    child_state["usage_summary"] = session.usage_summary
                    child_state["workflow_context"] = session.run_context
                with child_trace_slice() as child_events:
                    final_state = await compiled.ainvoke(child_state, config=graph_config)
            return self._results.envelope(definition, final_state, child_trace=child_events)

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

        # W3.2/W3.4: durable deliveries arrive wrapped — unwrap ONCE here so the whole
        # resumed run (not just the wait node) carries the stable wait/event-derived
        # idempotency context, and the wait node sees only the inner payload.
        if isinstance(event_payload, dict) and "__wait_delivery__" in event_payload:
            delivery = event_payload["__wait_delivery__"]
            # W3R.1: the claim token is a door credential, not run context — it must not
            # leak into capability metadata or traces.
            exposed = {k: v for k, v in dict(delivery).items() if k != "claim_token"}
            context = context.model_copy(
                update={
                    "metadata": {
                        **context.metadata,
                        "wait_idempotency": f"{delivery['wait_id']}:{delivery['event_id']}",
                        "wait_delivery": exposed,
                    }
                }
            )
            event_payload = event_payload.get("payload")
        def _close_failed_bundle() -> None:
            # F8/R0: builder.resume creates the bundle directory EAGERLY, so every exit
            # path from here on must finalize it — an unfinalized directory is invisible
            # to retention forever. Mirrors run()'s preflight/raise handling.
            if observation_bundle is not None:
                WorkflowRunSession(
                    workflow_id=definition.workflow_id,
                    context=context,
                    definition=definition,
                    bundle=observation_bundle,
                ).close("failed")

        try:
            if snapshot.workflow_id != definition.workflow_id:
                raise ValueError(
                    f"snapshot is for workflow '{snapshot.workflow_id}', not '{definition.workflow_id}'"
                )
            if not snapshot.suspended_node:
                raise ValueError("snapshot has no suspended node — only suspended runs can resume")
            self._bind_or_validate_event_loop()
            binding_error = self._preflight(definition)
        except Exception:
            _close_failed_bundle()
            raise
        if binding_error is not None:
            _close_failed_bundle()
            return self._results.failed(definition, binding_error)
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
        try:
            with observation_capture_scope(self.runtime.observation), run_session_scope(session):
                session.start_execution_window(
                    run_timeout_s=context.limits.timeout_s if context.limits else None,
                    prior_active_elapsed_s=getattr(snapshot, "active_elapsed_s", 0.0) or 0.0,
                    completion_reserve_s=getattr(
                        self.runner, "graph_cancellation_grace_s", 0.0
                    ),
                )
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
                # W4.2 (closing a W3 gap): a resumed run that suspends at the NEXT durable
                # wait registers it under the SAME W2.3 contract as run() — chained/repeated
                # waits get handles; an unregistered raw snapshot is never exposed.
                durable_handle, final_state = await self._suspension.register_or_fold(
                    definition, final_state, context, session
                )
        except Exception:
            # F8/R0: a raising resume must still finalize its bundle truthfully (run() parity).
            session.close("failed")
            raise
        envelope = self._results.envelope(definition, final_state, session=session)
        if durable_handle is not None:
            envelope = envelope.model_copy(
                update={"snapshot": None, "wait_handle": durable_handle}
            )
        return self._finalize_result(session, envelope)

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

    @staticmethod
    def _finalize_result(
        session: WorkflowRunSession,
        envelope: WorkflowRunResult,
    ) -> WorkflowRunResult:
        """Close one run segment and project its bundle path exactly once."""

        session.close(envelope.status, artifacts=envelope.artifacts)
        if session.bundle is not None and getattr(session.bundle, "path", None):
            return envelope.model_copy(
                update={"observation_bundle_path": str(session.bundle.path)}
            )
        return envelope

    def compile(self, definition: WorkflowDefinition) -> Any:
        """Compile through the engine-owned machine compiler."""

        return self._compiler.compile(definition)

    def _preflight(self, definition: WorkflowDefinition) -> Optional[str]:
        return self._compiler.preflight(definition)

    # ---------------------------------------------------------------- bounds
    def _recursion_limit(self, definition: WorkflowDefinition, context: CapabilityContext) -> int:
        limits = context.limits
        loops = (limits.max_retries + limits.max_retrace + 1) if limits else 1
        return 25 + len(definition.nodes) * (2 + loops)
