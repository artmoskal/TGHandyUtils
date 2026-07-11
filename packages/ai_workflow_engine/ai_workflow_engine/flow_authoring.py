"""Flow-as-data: AI-authored workflows on engine rails.

An LLM (or any capability) emits a :class:`FlowArtifact` — a constrained, declarative description
of a workflow (steps / branches / evaluator gates, including bounded loops). The engine validates
EVERYTHING before
compiling: every capability must be registered, side effects must be allow-listed, retrace targets
must exist, model profiles must resolve, and planner/flow-author capabilities are forbidden inside
authored flows (AI-written things must not contain AI-writers — recursion lives ONLY in the
bounded planner-depth mechanism). An authored flow gets ZERO privileges a hand-written one lacks:
after compilation it runs through the same executor, preflight, budgets, and trace.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from ai_workflow_engine.byte_safety import assert_byte_safe
from ai_workflow_engine.models import CapabilitySpec, RuntimeLimits

# Effective-cap defaults come from the MODEL, one source of truth — never `or 4`/`or 100`
# literals that would silently rewrite a configured value (R2).
_DEFAULT_LIMITS = RuntimeLimits()
from ai_workflow_engine.workflow import (
    Fallback,
    Retrace,
    Retry,
    WorkflowBuilder,
    WorkflowDefinition,
    WorkflowValidationError,
)


# FlowArtifact nodes are a Pydantic-native DISCRIMINATED union (R6): each kind admits ONLY its
# own fields (extra="forbid"), so per-kind field discipline lives in the schema itself and an
# ordinary `model_dump()` → `model_validate()` round-trip is loss- and surprise-free — no
# caller-sensitive `model_fields_set` inspection, no hidden `exclude_unset` serialization ritual.


class StepFlowNode(BaseModel):
    """Authored step: run one registered capability (defaults to the node id)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["step"] = "step"
    id: str
    capability: Optional[str] = None
    model_profile: Optional[str] = None


class BranchFlowNode(BaseModel):
    """Authored decision state: the decider capability picks among declared labels."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["branch"]
    id: str
    capability: Optional[str] = None          # the decider; defaults to the node id
    branches: Dict[str, str] = Field(default_factory=dict)
    # Pre-set gates for authored loops: a label that closes a cycle MUST carry a bound here
    # (the engine's cycle-gate validation rejects it otherwise); optionally map a bounded label
    # to the escape label taken once the gate is exhausted.
    branch_bounds: Dict[str, int] = Field(default_factory=dict)
    branch_exhausted: Dict[str, str] = Field(default_factory=dict)
    # Self-description per label ("take when ..."): flows into Transition.description so authored
    # machines are navigable by machine card exactly like hand-written ones.
    describe: Dict[str, str] = Field(default_factory=dict)
    model_profile: Optional[str] = None


class EvaluateFlowNode(BaseModel):
    """Authored evaluator gate over an earlier capability, with bounded reject policy."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["evaluate"]
    id: str
    capability: Optional[str] = None          # the evaluator; defaults to the node id
    target: Optional[str] = None              # the evaluated capability
    on_reject: Optional[Literal["retry", "retrace", "fallback"]] = None
    retrace_to: Optional[str] = None
    fallback: Optional[str] = None
    max_attempts: int = 2
    max_retrace: int = 1
    model_profile: Optional[str] = None


class FanoutFlowNode(BaseModel):
    """Authored bounded fan-out (v1.5a): ``max_items`` is REQUIRED by validation — AI-chosen
    parallelism is cost-bounded by construction; oversize runtime lists fail loudly."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fanout"]
    id: str
    capability: Optional[str] = None          # the per-item capability; defaults to the node id
    items_key: Optional[str] = None
    max_parallel: Optional[int] = None
    output_key: Optional[str] = None
    max_items: Optional[int] = None


FlowNode = Annotated[
    Union[StepFlowNode, BranchFlowNode, EvaluateFlowNode, FanoutFlowNode],
    Field(discriminator="kind"),
]

_FLOW_NODE_ADAPTER: TypeAdapter = TypeAdapter(FlowNode)


def FlowNodeSpec(**data: Any) -> Any:
    """Construct one authored-flow node (public compat constructor over the union).

    ``FlowNodeSpec(kind="step", id="x")`` keeps working and returns the kind-specific model;
    ``kind`` defaults to ``"step"`` as before.
    """

    data.setdefault("kind", "step")
    return _FLOW_NODE_ADAPTER.validate_python(data)


class FlowArtifact(BaseModel):
    """Checkpoint-safe, validated-before-compiled description of an authored workflow.

    Nodes are a discriminated union — a plain ``model_dump()``/``model_dump_json()`` round-trips
    through ``model_validate``/``model_validate_json`` without changing validity (R6 contract).
    """

    model_config = ConfigDict(extra="forbid")

    flow_id: str
    goal: str = ""
    nodes: List[FlowNode]
    metadata: Dict[str, Any] = Field(default_factory=dict)


def build_definition_from_artifact(
    artifact: FlowArtifact,
    *,
    registry: Any,
    model_profiles: Dict[str, Any],
    allowed_side_effects: List[str],
    max_nodes: int = 12,
    workflow_id: Optional[str] = None,
    limits: Any = None,
) -> WorkflowDefinition:
    """Validate an authored flow exhaustively, then compile it via the normal builder.

    Every violation is collected and raised in ONE loud error — no partial compilation.
    ``limits`` (RuntimeLimits of the profile the flow will EXECUTE under) bounds authored
    fanout: authoring and execution must not validate against different bounds.
    """

    errors = _validate_flow_artifact(
        artifact,
        registry=registry,
        model_profiles=model_profiles,
        allowed_side_effects=allowed_side_effects,
        max_nodes=max_nodes,
        limits=limits,
    )
    if errors:
        raise WorkflowValidationError(errors)

    return _compile_flow_artifact(artifact, workflow_id=workflow_id)


def _validate_flow_artifact(
    artifact: FlowArtifact,
    *,
    registry: Any,
    model_profiles: Dict[str, Any],
    allowed_side_effects: List[str],
    max_nodes: int,
    limits: Any = None,
) -> list[str]:
    errors: list[str] = []
    if not artifact.nodes:
        errors.append("authored flow has no nodes")
    if len(artifact.nodes) > max_nodes:
        errors.append(f"authored flow has {len(artifact.nodes)} nodes, exceeds max_nodes={max_nodes}")

    declared_ids = _collect_declared_ids(artifact.nodes, errors)
    allowed = set(allowed_side_effects)
    step_ids_so_far: set[str] = set()
    for spec in artifact.nodes:
        errors.extend(
            _validate_flow_node(
                spec,
                registry=registry,
                model_profiles=model_profiles,
                allowed_side_effects=allowed,
                declared_ids=declared_ids,
                step_ids_so_far=step_ids_so_far,
                limits=limits,
            )
        )
        if spec.kind == "step":
            step_ids_so_far.add(spec.id)
    return errors


def _collect_declared_ids(nodes: List[FlowNodeSpec], errors: list[str]) -> list[str]:
    declared_ids: list[str] = []
    for spec in nodes:
        if not spec.id:
            errors.append("authored node with empty id")
        elif spec.id in declared_ids:
            errors.append(f"duplicate authored node id: {spec.id}")
        declared_ids.append(spec.id)
    return declared_ids


def _validate_flow_node(
    spec: FlowNodeSpec,
    *,
    registry: Any,
    model_profiles: Dict[str, Any],
    allowed_side_effects: set[str],
    declared_ids: list[str],
    step_ids_so_far: set[str],
    limits: Any = None,
) -> list[str]:
    label = f"node '{spec.id}'"
    errors: list[str] = []
    # Per-kind field discipline lives in the SCHEMA now (discriminated union, extra="forbid"
    # per kind) — no runtime model_fields_set inspection (R6). Fanout has no model_profile.
    model_profile = getattr(spec, "model_profile", None)
    if model_profile and model_profile not in model_profiles:
        errors.append(f"{label}: unknown model profile '{model_profile}'")
    if spec.kind == "step":
        errors.extend(
            _validate_authored_capability(
                label, spec.capability or spec.id, registry, allowed_side_effects
            )
        )
    elif spec.kind == "branch":
        errors.extend(_validate_branch_spec(spec, registry, allowed_side_effects, declared_ids))
    elif spec.kind == "evaluate":
        errors.extend(_validate_evaluate_spec(spec, registry, allowed_side_effects, step_ids_so_far))
    elif spec.kind == "fanout":
        errors.extend(_validate_fanout_spec(spec, registry, allowed_side_effects, limits))
    return errors


def _validate_fanout_spec(
    spec: FlowNodeSpec,
    registry: Any,
    allowed_side_effects: set[str],
    limits: Any,
) -> list[str]:
    label = f"node '{spec.id}'"
    errors: list[str] = []
    errors.extend(
        _validate_authored_capability(
            label, spec.capability or spec.id, registry, allowed_side_effects
        )
    )
    if not spec.items_key:
        errors.append(f"{label}: fanout requires a non-empty items_key")

    effective = limits if isinstance(limits, RuntimeLimits) else _DEFAULT_LIMITS
    parallel_cap = effective.max_parallel_children
    if spec.max_parallel is not None and not 1 <= spec.max_parallel <= parallel_cap:
        errors.append(
            f"{label}: max_parallel={spec.max_parallel} must be between 1 and the effective "
            f"max_parallel_children={parallel_cap} (rejected, never silently clamped)"
        )

    items_cap = effective.max_authored_fanout_items
    if spec.max_items is None:
        errors.append(
            f"{label}: authored fanout REQUIRES max_items — AI-chosen parallelism must be "
            "cost-bounded by construction"
        )
    elif not 1 <= spec.max_items <= items_cap:
        errors.append(
            f"{label}: max_items={spec.max_items} must be between 1 and "
            f"limits.max_authored_fanout_items={items_cap}"
        )
    return errors


def _validate_branch_spec(
    spec: FlowNodeSpec,
    registry: Any,
    allowed_side_effects: set[str],
    declared_ids: list[str],
) -> list[str]:
    label = f"node '{spec.id}'"
    errors: list[str] = []
    if not spec.branches:
        errors.append(f"{label}: branch has no branches")
    errors.extend(
        _validate_authored_capability(
            label, spec.capability or spec.id, registry, allowed_side_effects
        )
    )
    for branch_label, target in spec.branches.items():
        if target not in declared_ids:
            errors.append(f"{label}: branch '{branch_label}' -> unknown node '{target}'")
    return errors


def _validate_evaluate_spec(
    spec: FlowNodeSpec,
    registry: Any,
    allowed_side_effects: set[str],
    step_ids_so_far: set[str],
) -> list[str]:
    label = f"node '{spec.id}'"
    errors: list[str] = []
    errors.extend(
        _validate_authored_capability(
            label, spec.capability or spec.id, registry, allowed_side_effects
        )
    )
    errors.extend(_validate_authored_capability(label, spec.target, registry, allowed_side_effects))
    if spec.on_reject == "retrace":
        if not spec.retrace_to:
            errors.append(f"{label}: on_reject=retrace requires retrace_to")
        elif spec.retrace_to not in step_ids_so_far:
            errors.append(
                f"{label}: retrace_to '{spec.retrace_to}' must be an EARLIER step "
                "(bounded back-edges only)"
            )
    if spec.on_reject == "fallback":
        if not spec.fallback:
            errors.append(f"{label}: on_reject=fallback requires fallback")
        else:
            errors.extend(
                _validate_authored_capability(label, spec.fallback, registry, allowed_side_effects)
            )
    return errors


def _validate_authored_capability(
    label: str,
    name: Optional[str],
    registry: Any,
    allowed_side_effects: set[str],
) -> list[str]:
    if not name:
        return []
    try:
        cap_spec, handler = registry.get(name)
    except KeyError:
        return [f"{label}: capability '{name}' is not registered"]

    errors: list[str] = []
    if cap_spec.is_planner:
        # R3 clean v0.9 contract: the typed CapabilitySpec.is_planner field is the ONLY
        # planner marker (metadata/handler-attribute reads removed, same as is_flow_author).
        errors.append(
            f"{label}: capability '{name}' is a planner — authored flows may not contain "
            "AI-writers (use bounded planner depth instead)"
        )
    if cap_spec.is_flow_author:
        # v0.9 clean contract: the typed CapabilitySpec.is_flow_author field is the ONLY
        # flow-author marker (metadata/handler-attribute reads removed).
        errors.append(
            f"{label}: capability '{name}' authors flows — recursion through generated "
            "structure is forbidden"
        )
    denied = sorted(effect for effect in cap_spec.side_effects if effect not in allowed_side_effects)
    if denied:
        errors.append(f"{label}: capability '{name}' side effects denied: {', '.join(denied)}")
    return errors


def _compile_flow_artifact(
    artifact: FlowArtifact,
    *,
    workflow_id: Optional[str],
) -> WorkflowDefinition:
    builder = WorkflowBuilder(
        workflow_id or f"authored:{artifact.flow_id}",
        description=artifact.goal,
    )
    for spec in artifact.nodes:
        if spec.kind == "step":
            builder.step(spec.id, capability=spec.capability, model_profile=spec.model_profile)
        elif spec.kind == "fanout":
            builder.fanout(
                spec.id,
                capability=spec.capability or spec.id,
                items_key=spec.items_key,
                max_parallel=spec.max_parallel,
                max_items=spec.max_items,
                output_key=spec.output_key,
            )
        elif spec.kind == "branch":
            builder.branch(
                spec.id,
                dict(spec.branches),
                decider=spec.capability,
                bounds=dict(spec.branch_bounds) or None,
                exhausted=dict(spec.branch_exhausted) or None,
                describe=dict(spec.describe) or None,
                model_profile=spec.model_profile,
            )
        else:
            on_reject = None
            if spec.on_reject == "retry":
                on_reject = Retry(max_attempts=spec.max_attempts)
            elif spec.on_reject == "retrace":
                on_reject = Retrace(spec.retrace_to, max_retrace=spec.max_retrace)
            elif spec.on_reject == "fallback":
                on_reject = Fallback(spec.fallback)
            builder.evaluate(
                spec.id,
                target=spec.target,
                evaluator=spec.capability,
                on_reject=on_reject,
                fallback=spec.fallback,
                model_profile=spec.model_profile,
            )
    return builder.build()


def render_capability_catalog(registry: Any, allowed_side_effects: Optional[List[str]] = None) -> str:
    """Render registered capabilities as text a flow-authoring LLM chooses from.

    MCP-style self-description for the authoring side: name, kind, declared description, side
    effects (marked DENIED when outside the allow-list), and the recursion firewall stated
    inline — planner / flow-author capabilities are marked NOT-AUTHORABLE.
    """

    allowed = set(allowed_side_effects) if allowed_side_effects is not None else None
    lines = ["capabilities:"]
    for name in sorted(registry.names()):
        spec, handler = registry.get(name)
        entry = f"- {name} ({spec.kind})"
        if spec.description:
            entry += f": {spec.description}"
        if spec.side_effects:
            marks = [
                effect + (" (DENIED)" if allowed is not None and effect not in allowed else "")
                for effect in spec.side_effects
            ]
            entry += f" [side effects: {', '.join(marks)}]"
        if spec.is_planner or spec.is_flow_author:
            entry += " [NOT-AUTHORABLE: AI-writers may not appear in authored flows]"
        lines.append(entry)
    return "\n".join(lines)


# ------------------------------------------------------------------ turnkey author (v1.5a, 2.4)


class FlowAuthorRequest(BaseModel):
    """Typed input of the turnkey flow-author capability."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    context: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("goal")
    @classmethod
    def _goal_must_be_substantial(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("flow author requires a non-empty goal")
        return value


# Model-agnostic default (settled decision 1 / D2): wholesale-overridable via prompt_template=.
# {format_instructions} is injected by the structured node's parser (the FlowArtifact schema).
DEFAULT_FLOW_AUTHOR_PROMPT = """You author ONE workflow as strict JSON (a FlowArtifact).

GOAL:
{goal}

CONTEXT:
{context}

AVAILABLE CAPABILITIES — choose ONLY from these; entries marked DENIED or NOT-AUTHORABLE are forbidden:
{catalog}

HARD BOUNDS: at most {max_nodes} nodes. Any fanout node MUST declare max_items (1..{max_items_cap})
and may declare max_parallel (1..{max_parallel_cap}). Unknown fields are rejected.

{format_instructions}

Emit ONLY the JSON object — no prose, no code fences."""


def build_flow_author_capability(
    llm: Any,
    *,
    registry: Any,
    model_profiles: Optional[Dict[str, Any]] = None,
    limits: Any = None,
    allowed_side_effects: Optional[List[str]] = None,
    max_nodes: int = 12,
    max_repair_rounds: int = 2,
    prompt_template: Optional[str] = None,
    capability_name: str = "author_flow",
) -> tuple:
    """Turnkey flow-author capability: catalog → LLM → parse → TARGET-registry validation →
    bounded repair → loud fail (settled design, 2026-07-10).

    Composes the existing :class:`StructuredLLMNode` — model binding, budget checks,
    prompt/response observation, per-attempt usage, parse/repair, and the final
    ``StructuredOutputError`` are ONE mechanic, not a re-implementation. ``registry``/
    ``model_profiles``/``limits``/``allowed_side_effects`` describe the PROCESSING engine the
    artifact will run on; validation compiles against that target and discards the result
    (``run_authored_flow`` re-validates at execution). Returns ``(CapabilitySpec, handler)`` —
    the spec carries the canonical ``is_flow_author=True`` firewall marker.
    """

    from ai_workflow_engine.engine.llm_node import StructuredLLMNode

    effective_profiles = dict(model_profiles or {})
    effective_allowed = list(allowed_side_effects or [])
    effective_limits = limits if isinstance(limits, RuntimeLimits) else _DEFAULT_LIMITS
    parallel_cap = effective_limits.max_parallel_children
    items_cap = effective_limits.max_authored_fanout_items

    def _validate_against_target(artifact: FlowArtifact) -> None:
        # Raises WorkflowValidationError with the FULL error list — the structured node feeds
        # it verbatim into the repair prompt; the compiled definition is deliberately discarded.
        build_definition_from_artifact(
            artifact,
            registry=registry,
            model_profiles=effective_profiles,
            allowed_side_effects=effective_allowed,
            max_nodes=max_nodes,
            limits=limits,
        )

    node = StructuredLLMNode(
        name=capability_name,
        config=object(),
        output_model=FlowArtifact,
        prompt_template=prompt_template or DEFAULT_FLOW_AUTHOR_PROMPT,
        input_variables=[
            "goal", "context", "catalog", "max_nodes", "max_items_cap", "max_parallel_cap",
        ],
        llm=llm,
        validator=_validate_against_target,
        max_repair_rounds=max_repair_rounds,
    )

    async def author_flow(context: Any, payload: Any) -> Dict[str, Any]:
        request = (
            payload
            if isinstance(payload, FlowAuthorRequest)
            else FlowAuthorRequest.model_validate(payload or {})
        )
        # Privacy edges (ALL prompt-bound request fields + the output): goal and context both
        # feed the rendered prompt, and the artifact dict is checkpoint-safe state — JSON text
        # can still carry a data URI, so none of them is "byte-free by construction" (R2 closed
        # the goal hole the adversarial review found).
        assert_byte_safe(request.goal, mode="prompt", path="flow_author.goal")
        assert_byte_safe(request.context, mode="prompt", path="flow_author.context")
        # R9 — one coherent target contract: the catalog is rendered AT EACH INVOCATION from
        # the SAME live registry the validator compiles against. A construction-time snapshot
        # advertised capabilities that validation no longer agreed on (and hid new ones).
        catalog = render_capability_catalog(registry, effective_allowed)
        artifact = await node.run(
            {
                "goal": request.goal,
                "context": json.dumps(request.context, sort_keys=True, default=str),
                "catalog": catalog,
                "max_nodes": max_nodes,
                "max_items_cap": items_cap,
                "max_parallel_cap": parallel_cap,
            }
        )
        # Canonical plain dump (R6): the discriminated schema round-trips ordinary
        # serialization — no caller-facing exclude_unset ritual.
        artifact_dict = artifact.model_dump()
        assert_byte_safe(artifact_dict, mode="prompt", path="flow_author.artifact")
        return artifact_dict

    spec = CapabilitySpec(
        name=capability_name,
        kind="llm",
        description=(
            "Author a FlowArtifact for the target engine (catalog-grounded, "
            "target-validated, bounded repair)."
        ),
        metered=True,
        is_flow_author=True,
    )
    return spec, author_flow
