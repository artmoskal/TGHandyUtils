"""Declarative workflow definition + fluent builder.

This module is the *product-facing* surface of the executable workflow engine. A product
declares a graph of nodes with :class:`WorkflowBuilder`, binds capabilities/prompts/policies
through the engine builder (see ``builder.py``), and the engine executes the whole thing.

The product never writes orchestration loops: branch/retry/retrace/fallback/fan-out/subflow
mechanics are expressed declaratively here and *executed* by the engine (``executor.py``).

Public concepts (kept product-neutral):
    WorkflowDefinition, WorkflowNode, WorkflowEdge, WorkflowBuilder, BranchDecision,
    Retry, Retrace, Fallback, SubworkflowRef, NodeKind.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from ai_workflow_engine.models import RuntimeLimits, SchedulingPolicy

# Sentinel target meaning "the workflow terminates after this edge".
END = "__end__"

# Node kinds the executor knows how to run. An authored node with any other kind
# fails validation at build time and (defensively) fails loudly at execution time —
# never silently downgraded to a simpler path.
NodeKind = Literal["step", "branch", "fanout", "evaluate", "subworkflow", "human"]
KNOWN_NODE_KINDS = frozenset({"step", "branch", "fanout", "evaluate", "subworkflow", "human"})


class WorkflowValidationError(ValueError):
    """Raised when a :class:`WorkflowDefinition` is structurally invalid."""

    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# --------------------------------------------------------------------------------------
# Control directives (first-class, reusable across products)
# --------------------------------------------------------------------------------------


class Retry(BaseModel):
    """Re-run the evaluated capability with evaluator criticism, bounded by ``max_attempts``."""

    kind: Literal["retry"] = "retry"
    max_attempts: int = 2

    def __init__(self, max_attempts: int | None = None, /, **data):
        if max_attempts is not None:
            data["max_attempts"] = max_attempts
        super().__init__(**data)


class Retrace(BaseModel):
    """Go back to an earlier node and re-run forward, bounded by ``max_retrace``."""

    kind: Literal["retrace"] = "retrace"
    target: str
    max_retrace: int = 1

    def __init__(self, target: str | None = None, /, **data):
        if target is not None:
            data["target"] = target
        super().__init__(**data)


class Fallback(BaseModel):
    """Hand off to a fallback capability when the primary output is rejected."""

    kind: Literal["fallback"] = "fallback"
    capability: str

    def __init__(self, capability: str | None = None, /, **data):
        if capability is not None:
            data["capability"] = capability
        super().__init__(**data)


ControlDirective = Union[Retry, Retrace, Fallback]


class BranchDecision(BaseModel):
    """Decision returned by a branch decider capability and recorded in trace.

    The executor routes on ``label``; ``rationale``/``confidence`` are observability only.
    """

    label: str
    rationale: str = ""
    confidence: float = 1.0


class SubworkflowRef(BaseModel):
    """Reference from a parent workflow to a child workflow run as a capability."""

    workflow_id: str
    # Narrow the child budget/limit; when omitted the child inherits the parent's.
    budget_usd: Optional[float] = None
    max_steps: Optional[int] = None

    def __init__(self, workflow_id: str | None = None, /, **data):
        if workflow_id is not None:
            data["workflow_id"] = workflow_id
        super().__init__(**data)


# --------------------------------------------------------------------------------------
# Graph model
# --------------------------------------------------------------------------------------


class WorkflowNode(BaseModel):
    """One executable node in a workflow graph.

    The default ``kind="step"`` dispatches to a registered capability — the capability's
    own ``CapabilitySpec.kind`` (deterministic/llm/tool/media/voice/external/agent) decides
    *what* runs. Control kinds (branch/fanout/evaluate/subworkflow/human) carry extra fields.
    """

    id: str
    # Free string (not Literal) on purpose: an unsupported kind must be *constructable* so the
    # builder can reject it at build() and the executor can fail loudly + trace at run time
    # (contract: "unsupported node types must fail loudly", never silently downgraded).
    kind: str = "step"
    capability: Optional[str] = None
    description: str = ""

    # input/output state mapping. Default: a step consumes the running payload and stores its
    # output under both its node id and the running payload for the next sequential node.
    input_key: Optional[str] = None
    output_key: Optional[str] = None

    # kind="branch": label -> target node id. ``capability`` (or ``decider``) returns a
    # BranchDecision/label.
    branches: Dict[str, str] = Field(default_factory=dict)
    decider: Optional[str] = None

    # kind="fanout": run ``item_capability`` once per item from ``fan_items_key`` with bounded
    # parallelism and partial-failure isolation; gather into ``output_key``.
    fan_items_key: Optional[str] = None
    item_capability: Optional[str] = None
    max_parallel: Optional[int] = None

    # kind="evaluate": run ``target_capability`` under ``evaluator`` with bounded
    # retry/retrace/fallback. ``on_reject`` selects the policy; ``fallback_capability`` is the
    # default fallback target.
    evaluator: Optional[str] = None
    target_capability: Optional[str] = None
    on_reject: Optional[ControlDirective] = None
    fallback_capability: Optional[str] = None

    # kind="subworkflow": run another registered workflow as a capability.
    subworkflow: Optional[SubworkflowRef] = None

    # policy (apply to step/evaluate nodes)
    retry: Optional[Retry] = None
    required_side_effects: List[str] = Field(default_factory=list)
    allow_raw_media_export: bool = False
    # backend scheduling/backpressure for this node's capability (e.g. a single-flight local model
    # lane). When ``scheduling.backend_key`` is set, the engine gates invocation through its
    # scheduler and holds the backend slot until the worker actually completes.
    scheduling: Optional[SchedulingPolicy] = None

    def effective_capability(self) -> Optional[str]:
        """Capability bound to this node (falls back to the node id for ergonomic builders)."""

        if self.kind == "branch":
            return self.decider or self.capability or self.id
        if self.kind == "fanout":
            return self.item_capability or self.capability or self.id
        if self.kind == "evaluate":
            return self.target_capability
        if self.kind == "subworkflow":
            return None
        return self.capability or self.id


class WorkflowEdge(BaseModel):
    """A directed edge. ``conditional`` edges carry a branch ``label`` and are taken only when
    the source branch node selects that label."""

    source: str
    target: str  # node id or END
    label: Optional[str] = None
    conditional: bool = False


class WorkflowDefinition(BaseModel):
    """A validated, executable workflow graph (product owns this; engine runs it)."""

    workflow_id: str
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    entry: str
    description: str = ""
    scheduling: Optional[SchedulingPolicy] = None
    limits: Optional[RuntimeLimits] = None

    def node(self, node_id: str) -> WorkflowNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(f"Unknown node: {node_id}")

    def node_ids(self) -> List[str]:
        return [node.id for node in self.nodes]

    def outgoing(self, node_id: str) -> List[WorkflowEdge]:
        return [edge for edge in self.edges if edge.source == node_id]

    def validate_graph(self) -> List[str]:
        """Return a list of structural errors (empty == valid)."""

        errors: List[str] = []
        if not self.nodes:
            errors.append("workflow has no nodes")
            return errors

        ids = [node.id for node in self.nodes]
        seen: set[str] = set()
        for node_id in ids:
            if node_id in seen:
                errors.append(f"duplicate node id: {node_id}")
            seen.add(node_id)
        known = set(ids)

        if self.entry not in known:
            errors.append(f"entry node not found: {self.entry}")

        for node in self.nodes:
            if node.kind not in KNOWN_NODE_KINDS:
                errors.append(f"node '{node.id}' has unsupported kind: {node.kind}")
                continue
            if node.kind == "branch":
                if not node.branches:
                    errors.append(f"branch node '{node.id}' has no branches")
                for label, target in node.branches.items():
                    if target != END and target not in known:
                        errors.append(
                            f"branch node '{node.id}' label '{label}' -> unknown node: {target}"
                        )
            elif node.kind == "fanout":
                if not node.fan_items_key:
                    errors.append(f"fanout node '{node.id}' missing fan_items_key")
                if not (node.item_capability or node.capability):
                    errors.append(f"fanout node '{node.id}' missing item_capability")
            elif node.kind == "evaluate":
                if not node.evaluator:
                    errors.append(f"evaluate node '{node.id}' missing evaluator")
                if not node.target_capability:
                    errors.append(f"evaluate node '{node.id}' missing target_capability")
                if isinstance(node.on_reject, Retrace) and node.on_reject.target not in known:
                    errors.append(
                        f"evaluate node '{node.id}' retrace target unknown: {node.on_reject.target}"
                    )
            elif node.kind == "subworkflow":
                if not node.subworkflow:
                    errors.append(f"subworkflow node '{node.id}' missing subworkflow ref")

        for edge in self.edges:
            if edge.source not in known:
                errors.append(f"edge from unknown node: {edge.source}")
            if edge.target != END and edge.target not in known:
                errors.append(f"edge to unknown node: {edge.target}")

        return errors


# --------------------------------------------------------------------------------------
# Fluent builder
# --------------------------------------------------------------------------------------


class WorkflowBuilder:
    """Fluent DSL for declaring a :class:`WorkflowDefinition`.

    Sequential edges are wired automatically between consecutive non-branch nodes; branch
    nodes route only through their declared labels. Terminal nodes (no outgoing edge) are
    connected to ``END`` at :meth:`build`.

    Example::

        workflow = (
            WorkflowBuilder("home_inventory")
            .step("select_evidence")
            .branch("evidence_quality_gate", {
                "enough": "extract_items", "ambiguous": "ask_location", "bad": "fallback_or_fail",
            })
            .step("extract_items")
            .evaluate("quality_gate", on_reject=Retrace("select_evidence"))
            .step("write_inventory")
            .build()
        )
    """

    def __init__(self, workflow_id: str, *, description: str = ""):
        self.workflow_id = workflow_id
        self.description = description
        self._nodes: List[WorkflowNode] = []
        self._edges: List[WorkflowEdge] = []
        self._entry: Optional[str] = None
        self._last: Optional[WorkflowNode] = None
        self._scheduling: Optional[SchedulingPolicy] = None
        self._limits: Optional[RuntimeLimits] = None

    # -- internal -----------------------------------------------------------------
    def _append(self, node: WorkflowNode) -> None:
        if self._entry is None:
            self._entry = node.id
        # Wire a sequential edge from the previous node, UNLESS:
        #   - the previous node was a branch (branches route only via their labels), or
        #   - this node is already a target of an existing edge (i.e. a branch "landing pad"
        #     declared later in the chain — it is reached via its label, not by fall-through).
        already_target = any(edge.target == node.id for edge in self._edges)
        if self._last is not None and self._last.kind != "branch" and not already_target:
            self._edges.append(WorkflowEdge(source=self._last.id, target=node.id))
        self._nodes.append(node)
        self._last = node

    # -- node builders ------------------------------------------------------------
    def step(
        self,
        node_id: str,
        *,
        capability: Optional[str] = None,
        input_key: Optional[str] = None,
        output_key: Optional[str] = None,
        retry: Optional[Retry] = None,
        required_side_effects: Optional[List[str]] = None,
        allow_raw_media_export: bool = False,
        scheduling: Optional[SchedulingPolicy] = None,
        description: str = "",
    ) -> "WorkflowBuilder":
        self._append(
            WorkflowNode(
                id=node_id,
                kind="step",
                capability=capability or node_id,
                input_key=input_key,
                output_key=output_key,
                retry=retry,
                required_side_effects=required_side_effects or [],
                allow_raw_media_export=allow_raw_media_export,
                scheduling=scheduling,
                description=description,
            )
        )
        return self

    def branch(
        self,
        node_id: str,
        branches: Dict[str, str],
        *,
        decider: Optional[str] = None,
        description: str = "",
    ) -> "WorkflowBuilder":
        node = WorkflowNode(
            id=node_id,
            kind="branch",
            decider=decider or node_id,
            branches=dict(branches),
            description=description,
        )
        self._append(node)
        for label, target in branches.items():
            self._edges.append(
                WorkflowEdge(source=node_id, target=target, label=label, conditional=True)
            )
        return self

    def fanout(
        self,
        node_id: str,
        *,
        capability: str,
        items_key: str,
        max_parallel: Optional[int] = None,
        output_key: Optional[str] = None,
        description: str = "",
    ) -> "WorkflowBuilder":
        self._append(
            WorkflowNode(
                id=node_id,
                kind="fanout",
                item_capability=capability,
                fan_items_key=items_key,
                max_parallel=max_parallel,
                output_key=output_key,
                description=description,
            )
        )
        return self

    def evaluate(
        self,
        node_id: str,
        *,
        target: Optional[str] = None,
        evaluator: Optional[str] = None,
        on_reject: Optional[ControlDirective] = None,
        fallback: Optional[str] = None,
        description: str = "",
    ) -> "WorkflowBuilder":
        # Default the evaluated target to the most recent step's capability.
        if target is None:
            for prior in reversed(self._nodes):
                if prior.kind == "step":
                    target = prior.capability or prior.id
                    break
        self._append(
            WorkflowNode(
                id=node_id,
                kind="evaluate",
                evaluator=evaluator or node_id,
                target_capability=target,
                on_reject=on_reject,
                fallback_capability=fallback or (on_reject.capability if isinstance(on_reject, Fallback) else None),
                description=description,
            )
        )
        return self

    def subworkflow(
        self,
        node_id: str,
        *,
        workflow: Union["WorkflowDefinition", str],
        budget_usd: Optional[float] = None,
        max_steps: Optional[int] = None,
        description: str = "",
    ) -> "WorkflowBuilder":
        workflow_id = workflow if isinstance(workflow, str) else workflow.workflow_id
        self._append(
            WorkflowNode(
                id=node_id,
                kind="subworkflow",
                subworkflow=SubworkflowRef(
                    workflow_id=workflow_id, budget_usd=budget_usd, max_steps=max_steps
                ),
                description=description,
            )
        )
        return self

    def human(
        self,
        node_id: str,
        *,
        capability: Optional[str] = None,
        description: str = "",
    ) -> "WorkflowBuilder":
        self._append(
            WorkflowNode(
                id=node_id,
                kind="human",
                capability=capability or node_id,
                description=description,
            )
        )
        return self

    # -- configuration ------------------------------------------------------------
    def with_scheduling(self, policy: SchedulingPolicy) -> "WorkflowBuilder":
        self._scheduling = policy
        return self

    def with_limits(self, limits: RuntimeLimits) -> "WorkflowBuilder":
        self._limits = limits
        return self

    # -- finalize -----------------------------------------------------------------
    def build(self) -> WorkflowDefinition:
        if self._entry is None:
            raise WorkflowValidationError(["workflow has no nodes"])

        # Connect terminal nodes (no outgoing edge, non-branch) to END.
        sources_with_edges = {edge.source for edge in self._edges}
        edges = list(self._edges)
        for node in self._nodes:
            if node.kind == "branch":
                continue
            if node.id not in sources_with_edges:
                edges.append(WorkflowEdge(source=node.id, target=END))

        definition = WorkflowDefinition(
            workflow_id=self.workflow_id,
            nodes=list(self._nodes),
            edges=edges,
            entry=self._entry,
            description=self.description,
            scheduling=self._scheduling,
            limits=self._limits,
        )
        errors = definition.validate_graph()
        if errors:
            raise WorkflowValidationError(errors)
        return definition
