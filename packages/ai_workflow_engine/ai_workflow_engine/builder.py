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
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, Union, runtime_checkable

from ai_workflow_engine.config_loader import ObservationConfig, WorkflowConfigBundle, load_workflow_config
from ai_workflow_engine.engine.capabilities import (
    CapabilityRegistry,
    CapabilityRuntime,
    DetailSink,
    InMemoryTraceSink,
    RuntimePlanCompiler,
    TraceSink,
)
from ai_workflow_engine.engine.checkpoints import CheckpointStore
from ai_workflow_engine.executor import WorkflowExecutor, WorkflowRunResult
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
from ai_workflow_engine.flow_authoring import FlowArtifact, build_definition_from_artifact
from ai_workflow_engine.prompt_rendering import PromptRenderService
from ai_workflow_engine.observation_bundle import open_observation_run_bundle
from ai_workflow_engine.run_session import (
    SessionScopedDetailSink,
    SessionScopedTraceSink,
    SessionScopedUsageSink,
)
from ai_workflow_engine.snapshot import MachineSnapshot
from ai_workflow_engine.models import CapabilityResult, WorkflowTraceEvent
from ai_workflow_engine.usage import UsageSink
from ai_workflow_engine.workflow import BranchDecision, WorkflowDefinition

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
    if spec is not None:
        return spec
    existing = getattr(handler, "spec", None)
    if isinstance(existing, CapabilitySpec):
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
        self._prompt_renderer: Optional[PromptRenderService] = None
        self.workflows: Dict[str, WorkflowDefinition] = {}
        self._profiles: Dict[str, WorkflowProfile] = {}
        self._plans: Dict[str, RuntimePlan] = {}

    @property
    def prompt_renderer(self) -> PromptRenderService:
        """Strict prompt-file renderer rooted at ``prompt_root`` (loud when unconfigured)."""

        if self.prompt_root is None:
            raise ValueError(
                "prompt_renderer requires a prompt root — build the engine with "
                "WorkflowEngineBuilder().with_prompt_root(...) or pass prompt_root="
            )
        if self._prompt_renderer is None:
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
            child_context = self.executor._child_context(context, child, None)
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

        flow = artifact if isinstance(artifact, FlowArtifact) else FlowArtifact.model_validate(artifact)
        allowed: List[str] = []
        if self.default_profile is not None:
            allowed = list(self.default_profile.safety.allowed_side_effects)
        definition = build_definition_from_artifact(
            flow,
            registry=self.registry,
            model_profiles=self.model_profiles,
            allowed_side_effects=allowed,
            max_nodes=max_nodes,
        )
        self.trace_sink.record(
            WorkflowTraceEvent(
                node=definition.workflow_id,
                decision="flow:authored",
                metadata={
                    "flow_id": flow.flow_id,
                    "goal": flow.goal,
                    "nodes": [f"{n.kind}:{n.id}" for n in flow.nodes],
                },
            )
        )
        return await self.run(definition, payload, **run_kwargs)

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
    ) -> WorkflowRunResult:
        definition = self._resolve(workflow)
        context = self._run_context_for(
            definition,
            goal=goal,
            user_id=user_id,
            constraints=constraints,
            delivery_target=delivery_target,
        )
        if (
            observation_bundle is None
            and self.observation is not None
            and self.observation.enabled
        ):
            # Config-enabled observation: the engine opens the per-run bundle itself;
            # explicit observation_bundle= remains the escape hatch and takes precedence.
            observation_bundle = open_observation_run_bundle(
                self.observation.bundle_dir,
                context.run_context.workflow_id,
                retention_limit=self.observation.retention_limit,
                artifact_policy=self.observation.artifacts,
                artifact_max_bytes=self.observation.artifact_max_bytes,
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
    ) -> WorkflowRunResult:
        """Continue a suspended workflow from its snapshot (live object or JSON string).

        The machine fast-forwards through completed nodes (zero re-execution), executes the
        suspended node with ``event_payload`` injected as ``context.metadata['resume_event']``,
        and runs on normally — budgets cumulative across both halves. The workflow must be
        registered on this engine (loud KeyError otherwise).
        """

        if isinstance(snapshot, str):
            snapshot = MachineSnapshot.model_validate_json(snapshot)
        definition = self.workflows.get(snapshot.workflow_id)
        if definition is None:
            raise KeyError(
                f"Unknown workflow: {snapshot.workflow_id} — register it before resuming"
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
        if not forked and snapshot.goal:
            goal = WorkflowGoal.model_validate(snapshot.goal)
        context = self._run_context_for(
            definition, goal=goal, user_id=user_id, constraints=constraints
        )
        if snapshot.run_context and not forked:
            context = context.model_copy(
                update={"run_context": WorkflowRunContext.model_validate(snapshot.run_context)}
            )
        return await self.executor.resume(definition, snapshot, event_payload, context)

    def _run_context_for(
        self,
        definition: WorkflowDefinition,
        *,
        goal: Optional[WorkflowGoal] = None,
        user_id: Optional[int] = None,
        constraints: Optional[Dict[str, Any]] = None,
        delivery_target: Optional[str] = None,
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
            ),
            plan=plan,
            limits=plan.limits,
            metadata={"model_profiles": self.model_profiles},
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

    def with_checkpoint_store(self, store: CheckpointStore) -> "WorkflowEngineBuilder":
        self._checkpoint_store = store
        return self

    def with_profile(self, profile: WorkflowProfile) -> "WorkflowEngineBuilder":
        self._default_profile = profile
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
        )
        for spec, handler in self._capabilities:
            engine.register_capability_spec(spec, handler)
        for definition, profile in self._workflows:
            engine.register_workflow(definition, profile=profile)
        return engine
