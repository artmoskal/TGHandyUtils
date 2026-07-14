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
from typing import Any, Callable, Dict, List, Optional, TypedDict

from pydantic import field_validator, model_validator, BaseModel, ConfigDict, Field

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
from ai_workflow_engine.engine.runner import WorkflowRunner, derive_workflow_result_status
from ai_workflow_engine.machine_compiler import WorkflowMachineCompiler
from ai_workflow_engine.node_services import (
    ExecutorNodeServices,
    NodeSchedulingRuntime,
)
from ai_workflow_engine.node_replay import NodeReplayRuntime
from ai_workflow_engine.nodes import NODE_HANDLERS
from ai_workflow_engine.run_session import child_trace_slice, WorkflowRunSession
from ai_workflow_engine._runtime_state import (
    CONTEXT,
    current_run_session,
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
from ai_workflow_engine.snapshot import MachineSnapshot, SNAPSHOT_SCHEMA_VERSION
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
        self._wait_runtime: Any = None
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
        return self._wait_runtime

    @wait_runtime.setter
    def wait_runtime(self, wait_runtime: Any) -> None:
        self._wait_runtime = wait_runtime
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
                session.start_execution_window(
                    run_timeout_s=context.limits.timeout_s if context.limits else None
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
                durable_handle, final_state = await self._register_durable_or_fold(
                    definition, final_state, context, session
                )
        except Exception:
            # The run raised: the bundle must still close, truthfully, as failed.
            session.close("failed")
            raise
        envelope = self._envelope(definition, final_state, session=session)
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
            child_state = self._initial_state(payload, context)
            if (session := current_run_session()) is not None:
                # Child workflows share the parent's usage/budget scope. Carry that same
                # summary into the child envelope too; an empty fabricated summary would make
                # nested costs look like zero and emitted a false plumbing warning on every run.
                child_state["usage_summary"] = session.usage_summary
                child_state["workflow_context"] = session.run_context
            with child_trace_slice() as child_events:
                final_state = await compiled.ainvoke(child_state, config=graph_config)
        return self._envelope(definition, final_state, child_trace=child_events)

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
        try:
            with observation_capture_scope(self.runtime.observation), run_session_scope(session):
                session.start_execution_window(
                    run_timeout_s=context.limits.timeout_s if context.limits else None,
                    prior_active_elapsed_s=getattr(snapshot, "active_elapsed_s", 0.0) or 0.0,
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
                durable_handle, final_state = await self._register_durable_or_fold(
                    definition, final_state, context, session
                )
        except Exception:
            # F8/R0: a raising resume must still finalize its bundle truthfully (run() parity).
            session.close("failed")
            raise
        envelope = self._envelope(definition, final_state, session=session)
        if durable_handle is not None:
            envelope = envelope.model_copy(
                update={"snapshot": None, "wait_handle": durable_handle}
            )
        session.close(envelope.status, artifacts=envelope.artifacts)
        if session.bundle is not None and getattr(session.bundle, "path", None):
            envelope = envelope.model_copy(
                update={"observation_bundle_path": str(session.bundle.path)}
            )
        return envelope

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
        """Compile through the engine-owned machine compiler."""

        return self._compiler.compile(definition)

    def _preflight(self, definition: WorkflowDefinition) -> Optional[str]:
        return self._compiler.preflight(definition)

    # ---------------------------------------------------------------- bounds
    def _recursion_limit(self, definition: WorkflowDefinition, context: CapabilityContext) -> int:
        limits = context.limits
        loops = (limits.max_retries + limits.max_retrace + 1) if limits else 1
        return 25 + len(definition.nodes) * (2 + loops)

    # ---------------------------------------------------------------- envelopes
    @staticmethod
    def _suspension_occurrence(final_state: Dict[str, Any], suspended: str) -> int:
        """0-based ordinal of the CURRENT suspension of ``suspended`` (already recorded
        in node_results). One shared source for snapshot sealing and wait registration."""

        return max(
            0,
            sum(
                1
                for r in final_state.get("node_results", [])
                if r.node_id == suspended and r.status == "requires_user_input"
            )
            - 1,
        )

    def _build_snapshot(
        self,
        definition: WorkflowDefinition,
        suspended: str,
        final_state: Dict[str, Any],
        session: Optional[WorkflowRunSession] = None,
    ) -> MachineSnapshot:
        # W4: record WHICH observation segment captured this suspension, so any resume
        # (local or claimed) derives the continuation segment from persisted lineage —
        # never from a directory scan. No bundle/segment on the session -> defaults.
        segment = getattr(getattr(session, "bundle", None), "segment", None)
        # W3R.1: a durable suspension's snapshot carries its deterministic wait id, sealing
        # it to the claimed delivery path (public resume rejects it). Same inputs as
        # registration -> same id by construction; registration double-checks.
        durable_wait_id: Optional[str] = None
        node = definition.node(suspended) if suspended in {n.id for n in definition.nodes} else None
        if node is not None and (node.wait_policy or {}).get("mode") == "durable":
            from ai_workflow_engine.wait_runtime import DurableWaitRuntime

            run_context_obj = final_state.get("workflow_context")
            run_id_for_wait = getattr(run_context_obj, "workflow_id", None)
            if run_id_for_wait:
                durable_wait_id = DurableWaitRuntime.wait_id_for(
                    str(run_id_for_wait),
                    suspended,
                    self._suspension_occurrence(final_state, suspended),
                )
        node_results: List[NodeResult] = list(final_state.get("node_results", []))
        plan = final_state.get("plan_artifact")
        if plan is not None and hasattr(plan, "model_dump"):
            plan = plan.model_dump()
        usage = final_state.get("usage_summary") or WorkflowUsageSummary()
        # v0.11 (M6): the capturing run's identity is REQUIRED. Top-level/resume runs carry it in
        # state; a CHILD run's identity lives on its CapabilityContext (goal) while its lineage
        # keeps the parent's run_context (shared usage/budget scope, B-post3).
        state_context = final_state.get(CONTEXT)
        goal = final_state.get("workflow_goal") or getattr(state_context, "goal", None)
        snapshot_run_context = final_state.get("workflow_context") or getattr(
            state_context, "run_context", None
        )
        if goal is None or snapshot_run_context is None:
            raise RuntimeError(
                "suspension without run identity: neither state nor context carries "
                "workflow_goal/run_context — a v0.11 snapshot requires the capturing run's identity"
            )
        # Recheck RR1 normalization at the OWNER: a CHILD capture pairs the child's goal with
        # the parent's run context (B-post3 lineage). The public MachineSnapshot model seals
        # identity consistency unconditionally, so build a coherent SNAPSHOT-ONLY run context:
        # workflow_type/goal_id follow the capturing goal while the parent run id and
        # correlation lineage are preserved. (The nested-suspension envelope is then converted
        # to a loud failure by the subworkflow node — unchanged.)
        goal_id = getattr(goal, "goal_id", None) or (goal.get("goal_id") if isinstance(goal, dict) else None)
        goal_wt = getattr(goal, "workflow_type", None) or (goal.get("workflow_type") if isinstance(goal, dict) else None)
        rc_goal_id = getattr(snapshot_run_context, "goal_id", None)
        rc_wt = getattr(snapshot_run_context, "workflow_type", None)
        if hasattr(snapshot_run_context, "model_copy") and (rc_goal_id != goal_id or rc_wt != goal_wt):
            snapshot_run_context = snapshot_run_context.model_copy(
                update={"goal_id": goal_id, "workflow_type": goal_wt}
            )
        return MachineSnapshot(
            schema_version=SNAPSHOT_SCHEMA_VERSION,
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
            goal=goal,
            run_context=snapshot_run_context,
            segment_index=getattr(segment, "segment_index", None),
            active_elapsed_s=(
                _sess.active_elapsed_s()
                if (_sess := current_run_session()) is not None
                else 0.0
            ),
            durable_wait_id=durable_wait_id,
        )

    async def _register_durable_or_fold(
        self,
        definition: WorkflowDefinition,
        final_state: Dict[str, Any],
        context: CapabilityContext,
        session: Optional[WorkflowRunSession] = None,
    ) -> tuple[Optional[WaitHandle], Dict[str, Any]]:
        """W2.3 for EVERY execution path: register a durable suspension or fold the run
        to failed with the typed failure traces. Shared by run() and resume() — a resumed
        run that suspends at the NEXT durable wait must register it exactly like a fresh
        run would (chained/repeated waits), never expose a raw unregistered snapshot."""

        try:
            return (
                await self._maybe_register_durable_wait(definition, final_state, context, session),
                final_state,
            )
        except Exception as registration_error:
            failed_node = next(
                (
                    r.node_id
                    for r in reversed(final_state.get("node_results", []))
                    if r.status == "requires_user_input"
                ),
                definition.workflow_id,
            )
            self.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=definition.workflow_id,
                    decision="wait:registration_failed",
                    error=str(registration_error)[:500],
                    run_id=str(context.run_context.workflow_id),
                )
            )
            # W2R.4: the SUSPENDED node's typed lifecycle flips to failed — the projected
            # bundle/viewer must never show a suspended node in a failed run.
            self.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=failed_node,
                    node_status="failed",
                    phase="node:result",
                    error=str(registration_error)[:500],
                    run_id=str(context.run_context.workflow_id),
                    metadata={"failure_kind": "wait_registration_failed"},
                )
            )
            return None, {
                **final_state,
                "status": "failed",
                "error": f"durable wait registration failed: {registration_error}",
            }

    async def _maybe_register_durable_wait(
        self,
        definition: WorkflowDefinition,
        final_state: Dict[str, Any],
        context: CapabilityContext,
        session: Optional[WorkflowRunSession] = None,
    ) -> Optional[WaitHandle]:
        """W2.3: a DURABLE suspension is registered with its coordinator BEFORE the engine
        exposes anything. Returns the typed handle on success; raises on any registration
        failure (the run then fails with NO public door); returns None for non-durable."""

        # Only a run that ACTUALLY halted suspended registers (W4.2): restored
        # node_results keep earlier halves' requires_user_input entries forever as
        # evidence, so entry presence alone would spuriously re-register a finished
        # resume — the explicit machine status is the truth.
        if final_state.get("status") != "requires_user_input":
            return None
        node_results = list(final_state.get("node_results", []))
        suspended = next(
            (r.node_id for r in reversed(node_results) if r.status == "requires_user_input"),
            None,
        )
        if suspended is None:
            return None
        node = definition.node(suspended)
        policy_dict = node.wait_policy or {}
        if policy_dict.get("mode") != "durable":
            return None
        wait_runtime = getattr(self, "wait_runtime", None)
        if wait_runtime is None:
            raise RuntimeError(
                f"durable wait '{suspended}' suspended without a configured WaitCoordinator"
            )
        from ai_workflow_engine.byte_safety import assert_byte_safe
        from ai_workflow_engine.wait_runtime import WaitRegistrationRequest
        from ai_workflow_engine.waits import DurableWaitPolicy

        policy = DurableWaitPolicy.model_validate(policy_dict)
        snapshot = self._build_snapshot(definition, suspended, final_state, session)
        assert_byte_safe(snapshot.model_dump(), mode="persist", path="durable_wait.snapshot")
        run_id = str(context.run_context.workflow_id)
        occurrence = self._suspension_occurrence(final_state, suspended)
        # W2A.2: identity, reuse, receipt validation, and clock live in ONE lifecycle
        # owner; the executor detects, builds the snapshot, delegates, and records.
        outcome = await wait_runtime.register_suspension(
            WaitRegistrationRequest(
                run_id=run_id,
                workflow_id=definition.workflow_id,
                definition_digest=definition.definition_digest(),
                suspended_node=suspended,
                occurrence=occurrence,
                policy=policy,
                snapshot_json=snapshot.model_dump_json(),
                # W3R.3a: the immutable facts terminal evidence is built from later —
                # persisted NOW, while the definition and lineage still exist.
                definition_json=definition.model_dump_json(),
                origin_segment_index=snapshot.segment_index,
                correlation_id=context.run_context.correlation_id,
            )
        )
        if snapshot.durable_wait_id != outcome.handle.wait_id:
            raise RuntimeError(
                f"wait identity integrity failure: snapshot sealed to "
                f"{snapshot.durable_wait_id!r} but registration produced "
                f"{outcome.handle.wait_id!r}"
            )
        self.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=suspended,
                decision="wait:registration_reused" if outcome.reused else "wait:registered",
                run_id=run_id,
                metadata={
                    "wait_id": outcome.handle.wait_id,
                    "deadline_at": outcome.deadline_at.isoformat(),
                    **(
                        {
                            "adapter_id": outcome.adapter_id,
                            "registration_id": outcome.registration_id,
                        }
                        if not outcome.reused
                        else {}
                    ),
                },
            )
        )
        return outcome.handle


    def _envelope(
        self,
        definition: WorkflowDefinition,
        final_state: Dict[str, Any],
        session: Optional[WorkflowRunSession] = None,
        *,
        child_trace: Optional[List[WorkflowTraceEvent]] = None,
    ) -> WorkflowRunResult:
        node_results: List[NodeResult] = list(final_state.get("node_results", []))
        run_context = final_state.get("workflow_context") or getattr(
            final_state.get(CONTEXT), "run_context", None
        )
        run_id = str(run_context.workflow_id) if run_context and run_context.workflow_id else None
        status: WorkflowResultStatus = derive_workflow_result_status(final_state)
        usage = final_state.get("usage_summary")
        if usage is None:
            # Runner always seeds the summary; absence means a plumbing bug upstream. Report
            # an empty summary rather than crash the result build, but never silently — a
            # fabricated $0 would read as cost truth.
            logger.warning("run produced no usage_summary — reporting an empty one (plumbing bug?)")
            usage = WorkflowUsageSummary()
        error = final_state.get("error")
        if error is None and status in ("partial", "failed"):
            # A child workflow crosses its parent boundary through WorkflowRunResult. Preserve
            # terminal node reasons there so partial/failure provenance cannot disappear even
            # though the child state itself has no single global error field.
            reasons = [record.error for record in node_results if record.error]
            error = "; ".join(dict.fromkeys(reasons)) or None
        snapshot: Optional[MachineSnapshot] = None
        if status == "requires_user_input":
            suspended = next(
                (r.node_id for r in reversed(node_results) if r.status == "requires_user_input"),
                None,
            )
            if suspended is not None:
                snapshot = self._build_snapshot(definition, suspended, final_state, session)
        return WorkflowRunResult(
            workflow_id=definition.workflow_id,
            status=status,
            output=final_state.get(RUNNING_PAYLOAD),
            error=error,
            fallback_reason=final_state.get("fallback_reason"),
            node_results=node_results,
            artifacts=list(final_state.get("artifacts", [])),
            usage=usage,
            # B6/B7 + v0.11 M7: top-level/resume envelopes carry the session's exact run
            # buffer; CHILD envelopes carry their explicit session-owned slice. The sink-
            # sniffing fallback is gone — custom sinks need no ``.events``.
            trace=list(session.trace_events) if session is not None else list(child_trace or []),
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
