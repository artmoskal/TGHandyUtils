"""Dependency-injection / IoC container for the executable workflow engine.

``WorkflowEngineBuilder`` is the wiring surface: products inject capabilities, workflows,
prompts, model profiles, trace/checkpoint sinks, and policies, then ``build()`` returns a
``WorkflowEngine`` whose single ``WorkflowExecutor`` runs every registered workflow via
``await engine.run(workflow_or_id, payload)``.

The container deliberately mirrors a DI pattern: a ``WorkflowPack`` bundles one product's
registrations (``pack.register(builder)``) so a workflow application can be assembled from
reusable parts without the product owning any orchestration mechanics.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    Union,
    runtime_checkable,
)

if TYPE_CHECKING:
    from ai_workflow_engine.prompt_rendering import PromptRenderService

from ai_workflow_engine.config_loader import ObservationConfig, WorkflowConfigBundle, load_workflow_config
from ai_workflow_engine.wait_contract import WaitDeliveryOutcome
from ai_workflow_engine.engine.capabilities import (
    CapabilityRegistry,
    CapabilityRuntime,
    DetailSink,
    InMemoryTraceSink,
    RuntimePlanCompiler,
    TraceSink,
)
import logging

from ai_workflow_engine.engine.checkpoints import CheckpointStore
from ai_workflow_engine.executor import WorkflowExecutor, WorkflowRunResult
from ai_workflow_engine.execution_window import RunExecutionRequest
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityKind,
    CapabilitySpec,
    ModelProfile,
    RuntimeLimits,
    RuntimePlan,
    SchedulingPolicy,
    WorkflowGoal,
    WorkflowProfile,
    WorkflowRunContext,
)
from ai_workflow_engine.byte_safety import assert_byte_safe
from ai_workflow_engine.run_session import (
    SessionScopedDetailSink,
    SessionScopedTraceSink,
    SessionScopedUsageSink,
)
from ai_workflow_engine.snapshot import MachineSnapshot
from ai_workflow_engine.models import CapabilityResult, WorkflowTraceEvent
from ai_workflow_engine.usage_events import UsageSink
from ai_workflow_engine.workflow import BranchDecision, WorkflowDefinition

logger = logging.getLogger(__name__)

# A capability handler is ``callable(context, payload) -> result`` (sync or async). Capability
# objects that expose a ``.spec`` attribute (e.g. HumanClarificationCapability) are also accepted.
CapabilityHandler = Callable[[CapabilityContext, Any], Any]


@runtime_checkable
class WorkflowPack(Protocol):
    """A product's bundle of registrations. Defines domain specifics, not execution mechanics."""

    def register(self, builder: "WorkflowEngineBuilder") -> None:
        ...


def _guard_handler(name: str, fn: Callable[[Any], str]) -> CapabilityHandler:
    """Wrap a plain ``fn(payload) -> label`` predicate as a deterministic branch decider."""

    def guard(_context: CapabilityContext, payload: Any) -> BranchDecision:
        return BranchDecision(label=str(fn(payload)), rationale=f"guard:{name}")

    guard.__name__ = f"guard_{name}"
    return guard


def _coerce_spec(
    name: str,
    handler: Any,
    *,
    spec: Optional[CapabilitySpec],
    kind: CapabilityKind,
    side_effects: Optional[List[str]],
    input_model: Optional[type],
    output_model: Optional[type],
    metered: bool,
    timeout_s: Optional[float],
) -> CapabilitySpec:
    overrides: list[str] = []
    if kind != "tool":
        overrides.append("kind")
    if side_effects is not None:
        overrides.append("side_effects")
    if input_model is not None:
        overrides.append("input_model")
    if output_model is not None:
        overrides.append("output_model")
    if metered:
        overrides.append("metered")
    if timeout_s is not None:
        overrides.append("timeout_s")
    if spec is not None:
        if overrides:
            raise ValueError(
                "register_capability received spec= together with policy/schema arguments that "
                f"would be ignored: {', '.join(overrides)}; put the complete contract in spec "
                "or pass registration arguments without spec"
            )
        return spec
    existing = getattr(handler, "spec", None)
    if isinstance(existing, CapabilitySpec):
        if overrides:
            raise ValueError(
                f"capability {name!r} publishes handler.spec, so registration arguments would be "
                f"ignored: {', '.join(overrides)}; configure the handler/spec itself or remove "
                "the duplicate arguments"
            )
        return existing
    return CapabilitySpec(
        name=name,
        kind=kind,
        side_effects=side_effects or [],
        input_model=input_model,
        output_model=output_model,
        metered=metered,
        timeout_s=timeout_s,
    )


class WorkflowEngine:
    """Assembled engine: registry + single executor + registered workflows/profiles.

    Supports both construction styles from the contract:
        engine = WorkflowEngineBuilder()...build()
        engine = WorkflowEngine.from_config("gopro.inventory.yaml"); engine.register_capability(...)
    and both run styles:
        await engine.run("workflow_id", payload)
        await engine.run(workflow_definition, payload)
    """

    def __init__(
        self,
        *,
        registry: Optional[CapabilityRegistry] = None,
        trace_sink: Optional[TraceSink] = None,
        detail_sink: Optional[DetailSink] = None,
        capture_detail_text: bool = False,
        usage_sink: Optional[UsageSink] = None,
        checkpoint_store: Optional[CheckpointStore] = None,
        config: Optional[WorkflowConfigBundle] = None,
        default_profile: Optional[WorkflowProfile] = None,
        model_profiles: Optional[Dict[str, ModelProfile]] = None,
        prompt_root: Optional[Path] = None,
        observation: Optional[ObservationConfig] = None,
    ) -> None:
        self.registry = registry or CapabilityRegistry()
        self.trace_sink = trace_sink or InMemoryTraceSink()
        self.detail_sink = detail_sink
        self.observation = observation
        # Engine-owned observation (config-first): when enabled with full capture, detail
        # text capture turns on and the session-routing sinks deliver every record into the
        # auto-opened per-run bundle. Products configure policy once; the engine owns mechanics.
        observation_on = observation is not None and observation.enabled
        if observation_on and observation.capture == "full":
            capture_detail_text = True
        runtime_detail_sink = self.detail_sink
        if self.detail_sink is not None or observation_on:
            runtime_detail_sink = SessionScopedDetailSink(self.detail_sink)
        self.runtime = CapabilityRuntime(
            self.registry,
            # Session-aware tee (B6/B7): every runtime trace event lands in the ACTIVE run
            # session's buffer (exact per-run envelope trace), the session's bundle when one
            # is attached, and then the configured sink.
            SessionScopedTraceSink(self.trace_sink),
            detail_sink=runtime_detail_sink,
            capture_detail_text=capture_detail_text,
        )
        self.executor = WorkflowExecutor(self.runtime, config=config)
        self.executor.runner.usage_sink = SessionScopedUsageSink(usage_sink)
        self.usage_sink = usage_sink
        self.model_profiles = dict(model_profiles or {})
        # Executor shares the live registry so per-node model bindings resolve + validate there.
        self.executor.model_profiles = self.model_profiles
        self.checkpoint_store = checkpoint_store
        self.config = config
        self.default_profile = default_profile
        self.prompt_root = prompt_root
        self._prompt_renderer: Optional["PromptRenderService"] = None
        self.workflows: Dict[str, WorkflowDefinition] = {}
        self._profiles: Dict[str, WorkflowProfile] = {}
        self._plans: Dict[str, RuntimePlan] = {}

    async def deliver_wait_event(self, handle: Any, event: Any) -> WaitDeliveryOutcome:
        """W3.2: the ONE public door for durable continuation (signal or timeout).

        v0.11.6 (C1): delivery is HANDLE-BOUND. The caller presents the complete
        ``WaitHandle`` the engine exposed (model or its dict dump) — a bare wait id is
        refused, and the handle's registration identity must name the accepted
        registration incarnation before any coordinator state can change.

        Returns a typed ``WaitDeliveryOutcome``: executed runs carry the resumed
        ``WorkflowRunResult``; duplicate/late/racing deliveries get honest terminal
        reports without re-execution. The registered definition digest must match the
        CURRENTLY registered definition — a changed machine is rejected, never replayed."""

        from ai_workflow_engine.wait_contract import WaitHandle as _WaitHandle
        from ai_workflow_engine.waits import _stored_model_data

        if isinstance(handle, _WaitHandle):
            # Boundary revalidation from the COMPLETE raw representation: a
            # model_copy(update=...) forgery (hidden extras included) dies here, before
            # the coordinator sees anything.
            handle = _WaitHandle.model_validate(_stored_model_data(handle))
        elif isinstance(handle, dict):
            handle = _WaitHandle.model_validate(handle)
        else:
            raise TypeError(
                "deliver_wait_event requires the exposed WaitHandle (model or dict) — a "
                "bare wait id cannot resume durable work; persist the complete handle"
            )
        wait_id = handle.wait_id
        runtime = getattr(self.executor, "wait_runtime", None)
        if runtime is None:
            raise RuntimeError(
                "deliver_wait_event requires a configured WaitCoordinator — compose one "
                "via WorkflowEngineBuilder.with_wait_coordinator(...)"
            )
        record = await runtime.coordinator.get(wait_id)
        if record is None:
            raise KeyError(f"unknown wait id {wait_id!r}")
        # C1: the handle must NAME the registered suspension — identity fields are
        # immutable registration facts (status is lifecycle-mutable and deliberately not
        # compared: a persisted pending-status handle legitimately delivers late events).
        doctored = [
            name
            for name, presented, stored in (
                ("run_id", handle.run_id, record.run_id),
                ("workflow_id", handle.workflow_id, record.workflow_id),
                ("suspended_node", handle.suspended_node, record.suspended_node),
                ("deadline_at", handle.deadline_at, record.deadline_at),
            )
            if presented != stored
        ]
        if doctored:
            raise ValueError(
                f"wait handle does not match the registered wait {wait_id!r} "
                f"(differs in {', '.join(doctored)}) — delivery requires the handle the "
                "engine exposed, unmodified"
            )
        current = self.workflows.get(record.workflow_id)
        current_digest = current.definition_digest() if current is not None else None
        outcome = await runtime.deliver(handle, event, current_digest=current_digest)
        from ai_workflow_engine import segment_lifecycle  # call-time (F1.1)

        observation_on = self.observation is not None and self.observation.enabled
        if outcome.kind == "executed":
            if not observation_on or record.origin_segment_index is None:
                outcome = outcome.model_copy(update={"terminal_observation": "skipped"})
            else:
                # R1: the coordinator terminalized THIS attempt — promote its segment to
                # canonical. R0R3-C1: the write's SUCCESS is part of the typed result — a
                # provisional-looking completed run must be visible immediately, and any
                # at-least-once redelivery repairs it (branch below).
                committed = segment_lifecycle.commit_attempt(
                    self.observation.bundle_dir,
                    segment_lifecycle.attempt_segment_id(
                        str(record.run_id),
                        int(record.origin_segment_index) + 1,
                        int(outcome.attempt or 1),
                    ),
                    wait_id=wait_id,
                    event_id=getattr(event, "event_id", None) or (
                        event.get("event_id") if isinstance(event, dict) else None
                    ),
                    attempt=outcome.attempt,
                    resolution=outcome.run_result.status if outcome.run_result else None,
                )
                outcome = outcome.model_copy(
                    update={"terminal_observation": "recorded" if committed else "failed"}
                )
        elif outcome.run_result is None and outcome.wait_status in (
            "failed",
            "cancelled",
            "completed",
        ):
            # W3R.3/R1/R0R3-C1: THIS call terminalized the wait without a machine
            # continuation, OR it redelivered after a process died (or a marker write
            # failed) between the terminal commit and evidence convergence — for
            # COMPLETED waits too, not only failures. The lifecycle owner routes by
            # persisted wait facts: executed-through evidence gets its commit marker
            # ensured; no-continuation failures get the wait_terminal segment.
            observation = await segment_lifecycle.ensure_terminal_evidence(
                self.observation, runtime, wait_id
            )
            outcome = outcome.model_copy(update={"terminal_observation": observation})
        return outcome

    async def cancel_wait(self, wait_id: str, *, reason: str) -> Any:
        """R2/F9: product-driven terminal escape hatch for a pending or STALLED wait
        (claimed with an expired lease — the state frozen acceptance makes unrecoverable
        when the accepted event is lost). Cancels at the coordinator, then converges the
        observation group so the cancelled run does not read as suspended forever. The
        engine still never self-fires: discovering stalled waits (``stalled()``/
        ``health()``) and deciding to cancel remain the product's loop."""

        runtime = getattr(self.executor, "wait_runtime", None)
        if runtime is None:
            raise RuntimeError(
                "cancel_wait requires a configured WaitCoordinator — compose one via "
                "WorkflowEngineBuilder.with_wait_coordinator(...)"
            )
        record = await runtime.coordinator.cancel(wait_id, reason=reason)
        observation = None
        if self.observation is not None and self.observation.enabled:
            from ai_workflow_engine import segment_lifecycle  # call-time (F1.1)

            observation = await segment_lifecycle.ensure_terminal_evidence(
                self.observation, runtime, wait_id
            )
        return record, observation

    @property
    def wait_coordinator(self) -> Any:
        """Read-only view of the composed durable-wait coordinator (owner: the executor's
        DurableWaitRuntime lifecycle service)."""

        runtime = getattr(self.executor, "wait_runtime", None)
        return runtime.coordinator if runtime is not None else None

    @property
    def prompt_renderer(self) -> "PromptRenderService":
        """Strict prompt-file renderer rooted at ``prompt_root`` (loud when unconfigured)."""

        if self.prompt_root is None:
            raise ValueError(
                "prompt_renderer requires a prompt root — build the engine with "
                "WorkflowEngineBuilder().with_prompt_root(...) or pass prompt_root="
            )
        if self._prompt_renderer is None:
            from ai_workflow_engine.prompt_rendering import PromptRenderService  # call-time (F1.1)

            self._prompt_renderer = PromptRenderService(self.prompt_root)
        return self._prompt_renderer

    # ---------------------------------------------------------------- registration
    @classmethod
    def from_config(cls, config: Union[str, Path, WorkflowConfigBundle], **kwargs: Any) -> "WorkflowEngine":
        bundle = config if isinstance(config, WorkflowConfigBundle) else load_workflow_config([config])
        kwargs.setdefault("observation", bundle.observation)
        return cls(
            config=bundle,
            default_profile=bundle.profile,
            model_profiles=dict(bundle.models),
            **kwargs,
        )

    def register_capability(
        self,
        name: str,
        handler: Any,
        *,
        spec: Optional[CapabilitySpec] = None,
        kind: CapabilityKind = "tool",
        side_effects: Optional[List[str]] = None,
        input_model: Optional[type] = None,
        output_model: Optional[type] = None,
        metered: bool = False,
        timeout_s: Optional[float] = None,
    ) -> "WorkflowEngine":
        resolved = _coerce_spec(
            name,
            handler,
            spec=spec,
            kind=kind,
            side_effects=side_effects,
            input_model=input_model,
            output_model=output_model,
            metered=metered,
            timeout_s=timeout_s,
        )
        if resolved.name != name:
            resolved = resolved.model_copy(update={"name": name})
        self._assert_observability_sink_alignment(handler)
        self.registry.register(resolved, handler)
        return self

    def register_capability_spec(self, spec: CapabilitySpec, handler: CapabilityHandler) -> "WorkflowEngine":
        self._assert_observability_sink_alignment(handler)
        self.registry.register(spec, handler)
        return self

    def _assert_observability_sink_alignment(self, handler: Any) -> None:
        engine_detail_sink = getattr(self.runtime, "detail_sink", None)
        if engine_detail_sink is None:
            return
        runtime = getattr(handler, "tool_runtime", None)
        if runtime is None:
            return
        runtime_detail_sink = getattr(runtime, "detail_sink", None)
        if runtime_detail_sink is not engine_detail_sink:
            raise ValueError(
                "Agent capability observability sink mismatch: build/register it with "
                "runtime=engine.runtime so trace and detail refs resolve in the same run view"
            )
        planner = getattr(handler, "planner", None)
        planner_detail_sink = getattr(planner, "detail_sink", engine_detail_sink)
        if planner_detail_sink is not engine_detail_sink:
            raise ValueError(
                "Agent planner observability sink mismatch: use the engine runtime/detail sink"
            )

    def register_guard(self, name: str, fn: Callable[[Any], str]) -> "WorkflowEngine":
        """Register a deterministic routing guard: ``fn(payload) -> label``.

        The cheap navigation path of the state machine — a branch decider with zero LLM calls and
        zero ceremony; the branch trace records ``decision_policy="deterministic"``. The returned
        label must be one the branch declares; anything else fails loudly like any decider.
        """

        return self.register_capability(name, _guard_handler(name, fn), kind="deterministic")

    def register_workflow(
        self,
        definition: WorkflowDefinition,
        *,
        profile: Optional[WorkflowProfile] = None,
    ) -> "WorkflowEngine":
        # Declarative model bindings fail loudly at registration, never mid-run (AC-G3).
        unknown_profiles = [
            f"node '{node.id}' -> {node.model_profile!r}"
            for node in definition.nodes
            if node.model_profile and node.model_profile not in self.model_profiles
        ]
        if unknown_profiles:
            raise ValueError(
                f"workflow '{definition.workflow_id}' references unknown model profile(s): "
                f"{', '.join(unknown_profiles)} (registered: {sorted(self.model_profiles) or 'none'})"
            )
        existing = self.workflows.get(definition.workflow_id)
        if existing is not None and existing.definition_digest() != definition.definition_digest():
            # B-post1: a changed same-id definition invalidates the cached runtime plan too —
            # definition-level limits/scheduling are folded into the plan, so a stale plan would
            # run the new machine under the OLD policy. Mirrors the _resolve re-register path.
            self.trace_sink.record(
                WorkflowTraceEvent(
                    node=definition.workflow_id,
                    decision="machine:re-registered",
                    metadata={
                        "old_digest": existing.definition_digest(),
                        "new_digest": definition.definition_digest(),
                    },
                )
            )
            self._plans.pop(definition.workflow_id, None)
        self.workflows[definition.workflow_id] = definition
        self.executor.register_subworkflow(definition)
        if profile is not None:
            self._profiles[definition.workflow_id] = profile
            self._plans.pop(definition.workflow_id, None)
        return self

    def register_pack(self, pack: WorkflowPack) -> "WorkflowEngine":
        pack.register(self)  # type: ignore[arg-type]
        return self

    def register_workflow_capability(
        self,
        name: str,
        workflow_id: str,
        *,
        side_effects: List[str] | tuple = (),
        description: str = "",
    ) -> "WorkflowEngine":
        """Expose a registered workflow as an ordinary capability (kind="workflow").

        This is what makes PARALLEL sub-workflows possible: fan out over the capability with
        ``.fanout(..., capability=name)`` — each item runs the child workflow in the parent's
        usage/budget scope with partial-failure isolation, like any other fanout child.
        """

        if workflow_id not in self.workflows:
            raise ValueError(
                f"register_workflow_capability: unknown workflow '{workflow_id}' "
                f"(registered: {sorted(self.workflows) or 'none'})"
            )

        async def run_child(context: CapabilityContext, payload: Any) -> CapabilityResult:
            child = self.workflows[workflow_id]
            from ai_workflow_engine.node_services import build_child_context

            child_context = build_child_context(context, child, None)
            run_result = await self.executor._run_inner(child, payload, child_context)
            if run_result.status == "completed":
                status = "accepted"
            elif run_result.status in ("partial", "requires_user_input"):
                status = "partial"
            else:
                status = "failed"
            return CapabilityResult(
                status=status,
                output=run_result.output,
                error=run_result.error,
                artifacts=run_result.artifacts,
                metadata={"child_workflow": workflow_id, "child_status": run_result.status},
            )

        self.registry.register(
            CapabilitySpec(
                name=name,
                kind="workflow",
                description=description or f"Run workflow '{workflow_id}' as a capability",
                side_effects=list(side_effects),
            ),
            run_child,
        )
        return self

    async def run_authored_flow(
        self,
        artifact: Union[FlowArtifact, Dict[str, Any]],
        payload: Any,
        *,
        max_nodes: int = 12,
        **run_kwargs: Any,
    ) -> WorkflowRunResult:
        """Validate-then-run an AI-authored flow (flow-as-data, engine spec §2e).

        The artifact is validated exhaustively BEFORE compilation (registered capabilities,
        allow-listed side effects, bounded back-edges, resolvable model profiles, no AI-writers
        inside) and then executed by the same executor as any hand-written workflow — identical
        budgets, preflight, trace, and policy. Loud ValueError on any violation; no partial build.
        """

        # Call-time import (F1.1): only authored-flow users pay for the authoring stack.
        from ai_workflow_engine.flow_authoring import FlowArtifact, build_definition_from_artifact

        flow = artifact if isinstance(artifact, FlowArtifact) else FlowArtifact.model_validate(artifact)
        # R7: the PUBLIC execution entry validates the COMPLETE artifact — goal, node fields,
        # metadata — before any compile, trace, or bundle write. The author factory's output
        # guard protects only factory-authored artifacts; this one protects everything.
        assert_byte_safe(flow.model_dump(), mode="prompt", path="authored_flow")
        allowed: List[str] = []
        if self.default_profile is not None:
            allowed = list(self.default_profile.safety.allowed_side_effects)
        definition = build_definition_from_artifact(
            flow,
            registry=self.registry,
            model_profiles=self.model_profiles,
            allowed_side_effects=allowed,
            max_nodes=max_nodes,
            # Authoring and execution must not validate against different fanout bounds:
            # these are the limits the flow will actually run under.
            limits=self.default_profile.limits if self.default_profile is not None else None,
        )
        # R7+R12+R-C2-2: provenance belongs to the RUN and the mechanism is engine-owned —
        # the typed payload is STAGED in the executor's module-private context variable (no
        # public signature carries it, so a hand-written run cannot forge flow:authored); the
        # EXECUTOR consumes it exactly once and constructs the trace event (fresh event id,
        # byte-checked, run-id stamped, inside the session).
        from ai_workflow_engine.executor import _AUTHORED_PROVENANCE

        provenance = {
            "flow_id": flow.flow_id,
            "goal": flow.goal,
            "nodes": [f"{n.kind}:{n.id}" for n in flow.nodes],
        }
        staged = _AUTHORED_PROVENANCE.set(provenance)
        try:
            return await self.run(definition, payload, **run_kwargs)
        finally:
            _AUTHORED_PROVENANCE.reset(staged)

    # ---------------------------------------------------------------- execution
    async def run(
        self,
        workflow: Union[str, WorkflowDefinition],
        payload: Any,
        *,
        goal: Optional[WorkflowGoal] = None,
        user_id: Optional[int] = None,
        constraints: Optional[Dict[str, Any]] = None,
        delivery_target: Optional[str] = None,
        recursion_fallback: Optional[Any] = None,
        recursion_limit: Optional[int] = None,
        observation_bundle: Any = None,
        terminal_status: Optional[Any] = None,
        execution: Optional[RunExecutionRequest] = None,
    ) -> WorkflowRunResult:
        definition = self._resolve(workflow)
        if observation_bundle is not None:
            # C2-4 (F-2G-1): the escape hatch is public API — a string/path or partial duck
            # object must fail HERE, at the door, not deep inside the run session. Call-time
            # import keeps the simple tier lazy (F1.1).
            from ai_workflow_engine.observation_bundle import ObservationRunBundle

            if not isinstance(observation_bundle, ObservationRunBundle):
                raise TypeError(
                    "observation_bundle= expects the canonical ObservationRunBundle "
                    f"(got {type(observation_bundle).__name__!r}). Open one with "
                    "open_observation_run_bundle(...), or configure engine-owned bundles "
                    "via with_observation(ObservationConfig(enabled=True, bundle_dir=...))."
                )
        context = self._run_context_for(
            definition,
            goal=goal,
            user_id=user_id,
            constraints=constraints,
            delivery_target=delivery_target,
            execution=execution,
        )
        if (
            observation_bundle is None
            and self.observation is not None
            and self.observation.enabled
        ):
            # Config-enabled observation: the engine opens the per-run bundle itself;
            # explicit observation_bundle= remains the escape hatch and takes precedence.
            from ai_workflow_engine.observation_bundle import (  # call-time (F1.1)
                ObservationSegment,
                open_observation_run_bundle,
            )

            run_id = str(context.run_context.workflow_id)
            observation_bundle = open_observation_run_bundle(
                self.observation.bundle_dir,
                run_id,
                retention_limit=self.observation.retention_limit,
                artifact_policy=self.observation.artifacts,
                artifact_max_bytes=self.observation.artifact_max_bytes,
                evict_suspended_after_s=self.observation.evict_suspended_after_s,
                correlation_id=context.run_context.correlation_id,
                # W4.1: the initial segment keeps the pre-W4 directory key (= run id), so
                # ordinary single-run bundle paths are byte-stable; segment meta is additive.
                segment=ObservationSegment(
                    segment_id=run_id,
                    segment_index=0,
                    kind="initial",
                    definition_digest=definition.definition_digest(),
                ),
            )
        return await self.executor.run(
            definition,
            payload,
            context,
            recursion_fallback=recursion_fallback,
            recursion_limit=recursion_limit,
            observation_bundle=observation_bundle,
            terminal_status=terminal_status,
        )

    async def resume(
        self,
        snapshot: Union[MachineSnapshot, str],
        event_payload: Any = None,
        *,
        goal: Optional[WorkflowGoal] = None,
        user_id: Optional[int] = None,
        constraints: Optional[Dict[str, Any]] = None,
        execution: Optional[RunExecutionRequest] = None,
    ) -> WorkflowRunResult:
        """Continue a suspended workflow from its snapshot (live object or JSON string).

        The machine fast-forwards through completed nodes (zero re-execution), executes the
        suspended node with ``event_payload`` injected as ``context.metadata['resume_event']``,
        and runs on normally — budgets cumulative across both halves. The workflow must be
        registered on this engine (loud KeyError otherwise).
        """

        if isinstance(snapshot, str):
            snapshot = MachineSnapshot.model_validate_json(snapshot)
        else:
            # Recheck RR1: an already-built object re-passes the FULL seal (wire checks
            # included) — model_copy or other validator-skipping constructions cannot smuggle
            # a conflicting identity through the public resume door.
            snapshot = MachineSnapshot.model_validate(snapshot.model_dump())
        definition = self.workflows.get(snapshot.workflow_id)
        if definition is None:
            raise KeyError(
                f"Unknown workflow: {snapshot.workflow_id} — register it before resuming"
            )
        if snapshot.durable_wait_id:
            # W3R.1: a durable suspension has ONE door — deliver_wait_event. Raw snapshot
            # bytes (from the coordinator or product storage) must not re-enter execution
            # here: that would skip claim/dedup/lease/attempt-bounds/terminalization. The
            # claimed delivery path proves itself with the in-flight claim token minted by
            # DurableWaitRuntime.deliver(); nothing else can present it.
            delivery = (
                event_payload.get("__wait_delivery__") if isinstance(event_payload, dict) else None
            )
            runtime = getattr(self.executor, "wait_runtime", None)
            if (
                runtime is None
                or not isinstance(delivery, dict)
                or delivery.get("wait_id") != snapshot.durable_wait_id
                or not runtime.holds_claim(
                    snapshot.durable_wait_id, delivery.get("claim_token")
                )
            ):
                raise RuntimeError(
                    f"snapshot is sealed to durable wait {snapshot.durable_wait_id!r} — "
                    "resume it via engine.deliver_wait_event(handle, event) using the complete "
                    "persisted WaitHandle; direct "
                    "resume would bypass claim, deduplication, leases, and attempt bounds"
                )
        # B-post3 (strict override rule): resume continues the ORIGINAL run identity — goal,
        # constraints, user, delivery target, metadata, and run-id lineage all come from the
        # snapshot. Forking the context is a DELIBERATE act: pass a full replacement ``goal=``.
        # Partial overrides (user_id/constraints without a goal) are ambiguous and rejected
        # loudly instead of being silently half-applied.
        if goal is None and (user_id is not None or constraints is not None):
            raise ValueError(
                "resume(...) does not support partial context overrides — pass a full "
                "replacement goal= to deliberately fork the resume context, or pass nothing "
                "to continue under the snapshot's original identity"
            )
        forked = goal is not None
        if not forked:
            # v0.11 (review finding 1): the snapshot's identity is TYPED and REQUIRED — consume
            # it directly, no truthiness test, no reparse. A snapshot cannot reach here without
            # a valid goal/run_context, so resume can never mint a replacement identity.
            goal = snapshot.goal
        context = self._run_context_for(
            definition,
            goal=goal,
            user_id=user_id,
            constraints=constraints,
            execution=execution,
        )
        if not forked:
            persisted_timeout_s = snapshot.run_timeout_s
            if persisted_timeout_s is not None:
                current_timeout_s = context.limits.timeout_s
                effective_timeout_s = (
                    persisted_timeout_s
                    if current_timeout_s is None
                    else min(persisted_timeout_s, current_timeout_s)
                )
                limits = context.limits.model_copy(update={"timeout_s": effective_timeout_s})
                plan = (
                    context.plan.model_copy(update={"limits": limits})
                    if context.plan is not None
                    else None
                )
                context = context.model_copy(update={"limits": limits, "plan": plan})
            context = context.model_copy(update={"run_context": snapshot.run_context})
        observation_bundle = None
        if self.observation is not None and self.observation.enabled:
            # Q-R5/R1: the resumed half is one physical ATTEMPT of the next LOGICAL
            # segment of the same run — meta keeps the logical run id; the physical key
            # comes from the segment-lifecycle owner. The logical index comes from the
            # snapshot's persisted position, never a directory scan.
            from ai_workflow_engine import segment_lifecycle  # call-time (F1.1)
            from ai_workflow_engine.observation_bundle import (  # call-time (F1.1)
                ObservationSegment,
                open_observation_run_bundle,
            )

            logical_run_id = str(context.run_context.workflow_id)
            child_index = int(snapshot.segment_index or 0) + 1
            delivery = (
                event_payload.get("__wait_delivery__") if isinstance(event_payload, dict) else None
            )
            attempt: Optional[int] = None
            if isinstance(delivery, dict):
                # Claimed delivery: deterministic key — only the single claim winner for
                # this (index, attempt) exists (CAS), and a crash-retry reclaim carries a
                # higher attempt, so it can never append into a dead attempt's directory.
                attempt = int(delivery.get("attempt") or 1)
                if attempt > 1:
                    # R1: engine-owned reconciliation — every PRIOR uncommitted attempt
                    # (finalized or not) becomes typed abandoned evidence owned by group
                    # retention; the active attempt's directory is never touched.
                    segment_lifecycle.reconcile_prior_attempts(
                        self.observation.bundle_dir,
                        run_id=logical_run_id,
                        segment_index=child_index,
                        definition=definition,
                        definition_digest=definition.definition_digest(),
                        upto_attempt=attempt,
                        correlation_id=context.run_context.correlation_id,
                    )
                segment_id = segment_lifecycle.attempt_segment_id(
                    logical_run_id, child_index, attempt
                )
            else:
                # Local in-process resume: unique physical key; the commit marker below
                # (written only on success) makes the LATEST committed attempt canonical,
                # so re-running a snapshot stays inspectable without corrupting the group.
                segment_id = segment_lifecycle.local_segment_id(logical_run_id, child_index)
            observation_bundle = open_observation_run_bundle(
                self.observation.bundle_dir,
                logical_run_id,
                retention_limit=self.observation.retention_limit,
                artifact_policy=self.observation.artifacts,
                artifact_max_bytes=self.observation.artifact_max_bytes,
                evict_suspended_after_s=self.observation.evict_suspended_after_s,
                correlation_id=context.run_context.correlation_id,
                segment=ObservationSegment(
                    segment_id=segment_id,
                    segment_index=child_index,
                    kind="resume",
                    definition_digest=definition.definition_digest(),
                    attempt=attempt,
                ),
            )
        # LOCAL resumes need no promotion step: with no coordinator there is no second
        # commit fact for the disk to disagree with — finalize IS the commit (readers
        # treat attempt-less segments as committed). Only DURABLE attempts carry a
        # commit marker, written by deliver_wait_event AFTER terminalization.
        return await self.executor.resume(
            definition, snapshot, event_payload, context, observation_bundle=observation_bundle
        )

    def _run_context_for(
        self,
        definition: WorkflowDefinition,
        *,
        goal: Optional[WorkflowGoal] = None,
        user_id: Optional[int] = None,
        constraints: Optional[Dict[str, Any]] = None,
        delivery_target: Optional[str] = None,
        execution: Optional[RunExecutionRequest] = None,
    ) -> CapabilityContext:
        plan = self._plan_for(definition)
        run_goal = goal or WorkflowGoal(
            workflow_type=definition.workflow_id,
            objective=definition.description or definition.workflow_id,
            user_id=user_id,
            delivery_target=delivery_target,
        )
        merged_constraints = {**plan.constraints, **run_goal.constraints, **(constraints or {})}
        if merged_constraints != plan.constraints:
            plan = plan.model_copy(update={"constraints": merged_constraints})
        limits = plan.limits
        run_execution_metadata: Dict[str, Any] = {}
        if execution is not None:
            configured_timeout_s = limits.timeout_s
            effective_timeout_s = (
                execution.timeout_s
                if configured_timeout_s is None
                else min(configured_timeout_s, execution.timeout_s)
            )
            limits = limits.model_copy(update={"timeout_s": effective_timeout_s})
            plan = plan.model_copy(update={"limits": limits})
            run_execution_metadata = {
                "run_execution": {
                    "requested_timeout_s": execution.timeout_s,
                    "configured_timeout_s": configured_timeout_s,
                    "effective_timeout_s": effective_timeout_s,
                    "source": execution.source,
                }
            }
        run_id = str(run_goal.metadata.get("run_id") or uuid.uuid4())
        return CapabilityContext(
            goal=run_goal,
            run_context=WorkflowRunContext(
                workflow_id=run_id,
                workflow_type=run_goal.workflow_type,
                goal_id=run_goal.goal_id,
                delivery_target=run_goal.delivery_target,
                user_id=run_goal.user_id,
                metadata=dict(run_goal.metadata),
                correlation_id=run_goal.correlation_id,
            ),
            plan=plan,
            limits=limits,
            metadata={"model_profiles": self.model_profiles, **run_execution_metadata},
        )

    # ---------------------------------------------------------------- internals
    def _resolve(self, workflow: Union[str, WorkflowDefinition]) -> WorkflowDefinition:
        if isinstance(workflow, WorkflowDefinition):
            # Register on first sight; on re-registration with the SAME id but DIFFERENT
            # content, REPLACE everywhere (workflows, plan cache, subworkflow registry +
            # compiled-graph eviction inside register_subworkflow) — the registries must
            # never diverge about which machine an id names (B1).
            existing = self.workflows.get(workflow.workflow_id)
            if existing is not None and existing.definition_digest() != workflow.definition_digest():
                self.trace_sink.record(
                    WorkflowTraceEvent(
                        node=workflow.workflow_id,
                        decision="machine:re-registered",
                        metadata={
                            "old_digest": existing.definition_digest(),
                            "new_digest": workflow.definition_digest(),
                        },
                    )
                )
                self._plans.pop(workflow.workflow_id, None)
            self.workflows[workflow.workflow_id] = workflow
            self.executor.register_subworkflow(workflow)
            return workflow
        try:
            return self.workflows[workflow]
        except KeyError as exc:
            raise KeyError(f"Unknown workflow: {workflow}") from exc

    def _profile_for(self, definition: WorkflowDefinition) -> Optional[WorkflowProfile]:
        return self._profiles.get(definition.workflow_id) or self.default_profile

    def _plan_for(self, definition: WorkflowDefinition) -> RuntimePlan:
        cached = self._plans.get(definition.workflow_id)
        if cached is not None:
            return cached
        profile = self._profile_for(definition)
        if profile is None:
            profile = WorkflowProfile(
                workflow_type=definition.workflow_id,
                profile_id=f"{definition.workflow_id}-default",
                scheduling=definition.scheduling or SchedulingPolicy(),
                limits=definition.limits or RuntimeLimits(),
            )
        compiler = RuntimePlanCompiler(supported_constraint_keys=set(profile.constraints))
        plan = compiler.compile(profile, self.registry)
        # Definition-level scheduling/limits override the profile when explicitly set.
        if definition.scheduling is not None:
            plan = plan.model_copy(update={"scheduling": definition.scheduling})
        if definition.limits is not None:
            plan = plan.model_copy(update={"limits": definition.limits})
        self._plans[definition.workflow_id] = plan
        return plan


_OBSERVATION_UNSET: Any = object()


class WorkflowEngineBuilder:
    """Fluent IoC builder that assembles a :class:`WorkflowEngine`."""

    def __init__(self) -> None:
        self._config: Optional[WorkflowConfigBundle] = None
        self._trace_sink: Optional[TraceSink] = None
        self._detail_sink: Optional[DetailSink] = None
        self._capture_detail_text = False
        self._usage_sink: Optional[UsageSink] = None
        self._checkpoint_store: Optional[CheckpointStore] = None
        self._prompt_root: Optional[Path] = None
        self._default_profile: Optional[WorkflowProfile] = None
        self._model_profiles: Dict[str, ModelProfile] = {}
        self._capabilities: List[Tuple[CapabilitySpec, CapabilityHandler]] = []
        self._workflows: List[Tuple[WorkflowDefinition, Optional[WorkflowProfile]]] = []
        # Sentinel: distinguishes "no override" (config's observation applies) from an explicit
        # with_observation(None) opt-out. Resolved only at build() time so call order never matters.
        self._observation: Any = _OBSERVATION_UNSET

    # -- configuration ------------------------------------------------------------
    def with_config(self, config: Union[str, Path, WorkflowConfigBundle]) -> "WorkflowEngineBuilder":
        bundle = config if isinstance(config, WorkflowConfigBundle) else load_workflow_config([config])
        self._config = bundle
        if self._default_profile is None:
            self._default_profile = bundle.profile
        for name, profile in bundle.models.items():
            self._model_profiles.setdefault(name, profile)
        return self

    def with_prompt_root(self, root: Union[str, Path]) -> "WorkflowEngineBuilder":
        self._prompt_root = Path(root)
        return self

    def with_trace_sink(self, sink: TraceSink) -> "WorkflowEngineBuilder":
        self._trace_sink = sink
        return self

    def with_detail_sink(self, sink: DetailSink) -> "WorkflowEngineBuilder":
        self._detail_sink = sink
        return self

    def with_detail_text_capture(self, enabled: bool = True) -> "WorkflowEngineBuilder":
        self._capture_detail_text = enabled
        return self

    def with_usage_sink(self, sink: UsageSink) -> "WorkflowEngineBuilder":
        self._usage_sink = sink
        return self

    def with_wait_coordinator(self, coordinator: Any, *, clock: Any = None) -> "WorkflowEngineBuilder":
        """W2.1/W2R: the ONE composition-root seam for durable waits. Rejects objects that
        do not satisfy the WaitCoordinator protocol AND (W2R.1) sync impostors — runtime
        protocols only check method PRESENCE, so asyncness is verified explicitly.
        ``clock`` (W2R.2) is the single durable-wait time source shared by engine record
        stamps and coordinator due/health in tests; default is UTC now."""

        import inspect

        from ai_workflow_engine.waits import WaitCoordinator  # call-time (leaf discipline)

        _PROTOCOL_METHODS = (
            "register",
            "get",
            "load_receipt",
            "load_snapshot",
            "load_definition",
            "claim_event",
            "abort_registration",
            "complete",
            "fail",
            "due",
            "stalled",
            "cancel",
            "health",
        )
        if not isinstance(coordinator, WaitCoordinator):
            raise TypeError(
                f"{type(coordinator).__name__} does not satisfy the WaitCoordinator protocol "
                f"(async {'/'.join(_PROTOCOL_METHODS)})"
            )
        # F6/R0: EVERY protocol member is async-checked — runtime_checkable verifies
        # presence only, and a sync claim_event/complete/fail would pass composition and
        # then TypeError mid-delivery AFTER a claim was taken.
        for name in _PROTOCOL_METHODS:
            method = inspect.unwrap(getattr(coordinator, name))
            if not inspect.iscoroutinefunction(method):
                raise TypeError(
                    f"WaitCoordinator.{name} must be async — {type(coordinator).__name__}.{name} "
                    "is a synchronous function (I/O-backed adapter operations are awaited)"
                )
        if clock is not None and not callable(clock):
            raise TypeError("wait clock must be a callable returning an aware datetime")
        self._wait_coordinator = coordinator
        self._wait_clock = clock
        return self

    def with_checkpoint_store(self, store: CheckpointStore) -> "WorkflowEngineBuilder":
        self._checkpoint_store = store
        return self

    def with_profile(self, profile: WorkflowProfile) -> "WorkflowEngineBuilder":
        self._default_profile = profile
        return self

    def with_observation(self, observation: Optional[ObservationConfig]) -> "WorkflowEngineBuilder":
        """Explicit observation override; ``None`` disables even when config enables it."""

        self._observation = observation
        return self

    def with_model_profile(self, profile: ModelProfile) -> "WorkflowEngineBuilder":
        self._model_profiles[profile.name] = profile
        return self

    # -- registration -------------------------------------------------------------
    def register_capability(
        self,
        name: str,
        handler: Any,
        *,
        spec: Optional[CapabilitySpec] = None,
        kind: CapabilityKind = "tool",
        side_effects: Optional[List[str]] = None,
        input_model: Optional[type] = None,
        output_model: Optional[type] = None,
        metered: bool = False,
        timeout_s: Optional[float] = None,
    ) -> "WorkflowEngineBuilder":
        resolved = _coerce_spec(
            name,
            handler,
            spec=spec,
            kind=kind,
            side_effects=side_effects,
            input_model=input_model,
            output_model=output_model,
            metered=metered,
            timeout_s=timeout_s,
        )
        if resolved.name != name:
            resolved = resolved.model_copy(update={"name": name})
        self._capabilities.append((resolved, handler))
        return self

    def register_capability_spec(self, spec: CapabilitySpec, handler: CapabilityHandler) -> "WorkflowEngineBuilder":
        self._capabilities.append((spec, handler))
        return self

    def register_guard(self, name: str, fn: Callable[[Any], str]) -> "WorkflowEngineBuilder":
        """Deterministic routing guard (see :meth:`WorkflowEngine.register_guard`)."""

        return self.register_capability(name, _guard_handler(name, fn), kind="deterministic")

    def register_workflow(
        self,
        definition: WorkflowDefinition,
        *,
        profile: Optional[WorkflowProfile] = None,
    ) -> "WorkflowEngineBuilder":
        self._workflows.append((definition, profile))
        return self

    def register_pack(self, pack: WorkflowPack) -> "WorkflowEngineBuilder":
        pack.register(self)
        return self

    # -- finalize -----------------------------------------------------------------
    def build(self) -> WorkflowEngine:
        if self._observation is not _OBSERVATION_UNSET:
            observation = self._observation
        else:
            observation = self._config.observation if self._config is not None else None
        engine = WorkflowEngine(
            trace_sink=self._trace_sink,
            detail_sink=self._detail_sink,
            capture_detail_text=self._capture_detail_text,
            usage_sink=self._usage_sink,
            checkpoint_store=self._checkpoint_store,
            config=self._config,
            default_profile=self._default_profile,
            model_profiles=self._model_profiles,
            prompt_root=self._prompt_root,
            observation=observation,
        )
        for spec, handler in self._capabilities:
            engine.register_capability_spec(spec, handler)
        for definition, profile in self._workflows:
            engine.register_workflow(definition, profile=profile)
        coordinator = getattr(self, "_wait_coordinator", None)
        if coordinator is not None:
            # W2A: ONE lifecycle owner — DurableWaitRuntime on the executor; the engine
            # exposes a read-only delegate. Only wait-selecting products pay this import.
            from ai_workflow_engine.wait_runtime import DurableWaitRuntime  # call-time

            engine.executor.wait_runtime = DurableWaitRuntime(
                coordinator,
                clock=getattr(self, "_wait_clock", None),
                # W3.2: the ONLY door back into machine execution — an injected port,
                # never an import (engine.resume accepts snapshot JSON + event).
                resume_port=engine.resume,
            )
        return engine
