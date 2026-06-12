"""Flow-as-data: AI-authored workflows on engine rails.

An LLM (or any capability) emits a :class:`FlowArtifact` — a constrained, declarative description
of a workflow (steps / branches / evaluator gates only). The engine validates EVERYTHING before
compiling: every capability must be registered, side effects must be allow-listed, retrace targets
must exist, model profiles must resolve, and planner/flow-author capabilities are forbidden inside
authored flows (AI-written things must not contain AI-writers — recursion lives ONLY in the
bounded planner-depth mechanism). An authored flow gets ZERO privileges a hand-written one lacks:
after compilation it runs through the same executor, preflight, budgets, and trace.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ai_workflow_engine.workflow import (
    Fallback,
    Retrace,
    Retry,
    WorkflowBuilder,
    WorkflowDefinition,
    WorkflowValidationError,
)


class FlowNodeSpec(BaseModel):
    """One node in an AI-authored flow. Kinds are a deliberate subset of the engine's."""

    kind: Literal["step", "branch", "evaluate"] = "step"
    id: str
    capability: Optional[str] = None          # step: defaults to id; branch: the decider;
                                              # evaluate: the evaluator capability
    target: Optional[str] = None              # evaluate: the evaluated capability
    branches: Dict[str, str] = Field(default_factory=dict)
    on_reject: Optional[Literal["retry", "retrace", "fallback"]] = None
    retrace_to: Optional[str] = None
    fallback: Optional[str] = None
    max_attempts: int = 2
    max_retrace: int = 1
    model_profile: Optional[str] = None


class FlowArtifact(BaseModel):
    """Checkpoint-safe, validated-before-compiled description of an authored workflow."""

    flow_id: str
    goal: str = ""
    nodes: List[FlowNodeSpec]
    metadata: Dict[str, Any] = Field(default_factory=dict)


def build_definition_from_artifact(
    artifact: FlowArtifact,
    *,
    registry: Any,
    model_profiles: Dict[str, Any],
    allowed_side_effects: List[str],
    max_nodes: int = 12,
    workflow_id: Optional[str] = None,
) -> WorkflowDefinition:
    """Validate an authored flow exhaustively, then compile it via the normal builder.

    Every violation is collected and raised in ONE loud error — no partial compilation.
    """

    errors: list[str] = []
    if not artifact.nodes:
        errors.append("authored flow has no nodes")
    if len(artifact.nodes) > max_nodes:
        errors.append(f"authored flow has {len(artifact.nodes)} nodes, exceeds max_nodes={max_nodes}")

    declared_ids: list[str] = []
    for spec in artifact.nodes:
        if not spec.id:
            errors.append("authored node with empty id")
        elif spec.id in declared_ids:
            errors.append(f"duplicate authored node id: {spec.id}")
        declared_ids.append(spec.id)

    allowed = set(allowed_side_effects)

    def check_capability(label: str, name: Optional[str]) -> None:
        if not name:
            return
        try:
            cap_spec, handler = registry.get(name)
        except KeyError:
            errors.append(f"{label}: capability '{name}' is not registered")
            return
        if cap_spec.metadata.get("planner") is True or getattr(handler, "is_planner", False):
            errors.append(
                f"{label}: capability '{name}' is a planner — authored flows may not contain "
                "AI-writers (use bounded planner depth instead)"
            )
        if cap_spec.metadata.get("flow_author") is True or getattr(handler, "is_flow_author", False):
            errors.append(
                f"{label}: capability '{name}' authors flows — recursion through generated "
                "structure is forbidden"
            )
        denied = sorted(effect for effect in cap_spec.side_effects if effect not in allowed)
        if denied:
            errors.append(f"{label}: capability '{name}' side effects denied: {', '.join(denied)}")

    step_ids_so_far: set[str] = set()
    for spec in artifact.nodes:
        label = f"node '{spec.id}'"
        if spec.model_profile and spec.model_profile not in model_profiles:
            errors.append(f"{label}: unknown model profile '{spec.model_profile}'")
        if spec.kind == "step":
            check_capability(label, spec.capability or spec.id)
            step_ids_so_far.add(spec.id)
        elif spec.kind == "branch":
            if not spec.branches:
                errors.append(f"{label}: branch has no branches")
            check_capability(label, spec.capability or spec.id)
            for branch_label, target in spec.branches.items():
                if target not in declared_ids:
                    errors.append(f"{label}: branch '{branch_label}' -> unknown node '{target}'")
        elif spec.kind == "evaluate":
            check_capability(label, spec.capability or spec.id)
            check_capability(label, spec.target)
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
                    check_capability(label, spec.fallback)
    if errors:
        raise WorkflowValidationError(errors)

    builder = WorkflowBuilder(workflow_id or f"authored:{artifact.flow_id}",
                              description=artifact.goal)
    for spec in artifact.nodes:
        if spec.kind == "step":
            builder.step(spec.id, capability=spec.capability, model_profile=spec.model_profile)
        elif spec.kind == "branch":
            builder.branch(spec.id, dict(spec.branches), decider=spec.capability,
                           model_profile=spec.model_profile)
        else:
            on_reject = None
            if spec.on_reject == "retry":
                on_reject = Retry(max_attempts=spec.max_attempts)
            elif spec.on_reject == "retrace":
                on_reject = Retrace(spec.retrace_to, max_retrace=spec.max_retrace)
            elif spec.on_reject == "fallback":
                on_reject = Fallback(spec.fallback)
            builder.evaluate(spec.id, target=spec.target, evaluator=spec.capability,
                             on_reject=on_reject, fallback=spec.fallback,
                             model_profile=spec.model_profile)
    return builder.build()
