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

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, Union, runtime_checkable

from ai_workflow_engine.config_loader import WorkflowConfigBundle, load_workflow_config
from ai_workflow_engine.engine.capabilities import (
    CapabilityRegistry,
    CapabilityRuntime,
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
from ai_workflow_engine.workflow import WorkflowDefinition

# A capability handler is ``callable(context, payload) -> result`` (sync or async). Capability
# objects that expose a ``.spec`` attribute (e.g. HumanClarificationCapability) are also accepted.
CapabilityHandler = Callable[[CapabilityContext, Any], Any]


@runtime_checkable
class WorkflowPack(Protocol):
    """A product's bundle of registrations. Defines domain specifics, not execution mechanics."""

    def register(self, builder: "WorkflowEngineBuilder") -> None:
        ...


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
        checkpoint_store: Optional[CheckpointStore] = None,
        config: Optional[WorkflowConfigBundle] = None,
        default_profile: Optional[WorkflowProfile] = None,
        model_profiles: Optional[Dict[str, ModelProfile]] = None,
        prompt_root: Optional[Path] = None,
    ) -> None:
        self.registry = registry or CapabilityRegistry()
        self.trace_sink = trace_sink or InMemoryTraceSink()
        self.runtime = CapabilityRuntime(self.registry, self.trace_sink)
        self.executor = WorkflowExecutor(self.runtime, config=config)
        self.model_profiles = dict(model_profiles or {})
        # Executor shares the live registry so per-node model bindings resolve + validate there.
        self.executor.model_profiles = self.model_profiles
        self.checkpoint_store = checkpoint_store
        self.config = config
        self.default_profile = default_profile
        self.prompt_root = prompt_root
        self.workflows: Dict[str, WorkflowDefinition] = {}
        self._profiles: Dict[str, WorkflowProfile] = {}
        self._plans: Dict[str, RuntimePlan] = {}

    # ---------------------------------------------------------------- registration
    @classmethod
    def from_config(cls, config: Union[str, Path, WorkflowConfigBundle], **kwargs: Any) -> "WorkflowEngine":
        bundle = config if isinstance(config, WorkflowConfigBundle) else load_workflow_config([config])
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
        self.registry.register(resolved, handler)
        return self

    def register_capability_spec(self, spec: CapabilitySpec, handler: CapabilityHandler) -> "WorkflowEngine":
        self.registry.register(spec, handler)
        return self

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
        self.workflows[definition.workflow_id] = definition
        self.executor.register_subworkflow(definition)
        if profile is not None:
            self._profiles[definition.workflow_id] = profile
            self._plans.pop(definition.workflow_id, None)
        return self

    def register_pack(self, pack: WorkflowPack) -> "WorkflowEngine":
        pack.register(self)  # type: ignore[arg-type]
        return self

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
    ) -> WorkflowRunResult:
        definition = self._resolve(workflow)
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
        context = CapabilityContext(
            goal=run_goal,
            run_context=WorkflowRunContext(
                workflow_id=definition.workflow_id,
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
        return await self.executor.run(
            definition,
            payload,
            context,
            recursion_fallback=recursion_fallback,
            recursion_limit=recursion_limit,
        )

    # ---------------------------------------------------------------- internals
    def _resolve(self, workflow: Union[str, WorkflowDefinition]) -> WorkflowDefinition:
        if isinstance(workflow, WorkflowDefinition):
            # Register on first sight so subworkflow lookups + plan caching work.
            self.workflows.setdefault(workflow.workflow_id, workflow)
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
