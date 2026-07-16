"""Declarative workflow definition + fluent builder.

This module is the *product-facing* surface of the executable workflow engine. A product
declares a graph of nodes with :class:`WorkflowBuilder`, binds capabilities/prompts/policies
through the engine builder (see ``builder.py``), and the engine executes the whole thing.

The product never writes orchestration loops: branch/retry/retrace/fallback/fan-out/subflow
mechanics are expressed declaratively here and *executed* by the engine (``executor.py``).

Public concepts (kept product-neutral):
    WorkflowDefinition, WorkflowNode, Transition, WorkflowBuilder, BranchDecision,
    Retry, Retrace, Replan, Fallback, SubworkflowRef, NodeKind.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from ai_workflow_engine.models import RuntimeLimits, SchedulingPolicy

# Sentinel target meaning "the workflow terminates after this edge".
END = "__end__"

# Node kinds the executor knows how to run. An authored node with any other kind
# fails validation at build time and (defensively) fails loudly at execution time —
# never silently downgraded to a simpler path.
NodeKind = Literal["step", "branch", "fanout", "evaluate", "subworkflow", "human", "planner"]
KNOWN_NODE_KINDS = frozenset(
    {"step", "branch", "fanout", "evaluate", "subworkflow", "human", "planner"}
)

# Authoritative node-kind contract for the four context decorations. Graph validation
# consumes this matrix so hand-authored definitions cannot configure a field that its node
# handler will silently ignore.
NODE_CONTEXT_BINDINGS = {
    kind: frozenset({"inject_plan", "inject_machine", "memory", "model_profile"})
    for kind in ("step", "branch", "fanout", "evaluate", "human", "planner")
}
NODE_CONTEXT_BINDINGS["subworkflow"] = frozenset({"inject_plan", "inject_machine"})


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


class Replan(BaseModel):
    """Go back to a planner node and revise only pending tasks, bounded by ``max_replans``."""

    kind: Literal["replan"] = "replan"
    target: str
    max_replans: int = 1

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


ControlDirective = Union[Retry, Retrace, Replan, Fallback]


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
    title: str = ""
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
    # parallelism and partial-failure isolation; gather into ``output_key``. ``max_items`` is a
    # declared structural bound on the item COUNT (None = unbounded, the hand-written default;
    # AI-authored flows are REQUIRED to declare it): an oversize list fails loudly, never truncates.
    fan_items_key: Optional[str] = None
    item_capability: Optional[str] = None
    # R13: structural bounds are honest — zero/negative is a build-time error, never a
    # silent rewrite to 1 downstream.
    max_parallel: Optional[int] = Field(default=None, ge=1)
    max_items: Optional[int] = Field(default=None, ge=1)
    # W1: EXPLICIT wait intent on wait-capable (human) nodes — normalized policy dict
    # ({"mode": "local"} | {"mode": "durable", ...}); REQUIRED on human nodes, forbidden
    # elsewhere; validated via call-time import so no-wait consumers never load waits.py.
    wait_policy: Optional[Dict[str, Any]] = None

    # kind="evaluate": run ``target_capability`` under ``evaluator`` with bounded
    # retry/retrace/fallback. ``on_reject`` selects the policy; ``fallback_capability`` is the
    # default fallback target.
    evaluator: Optional[str] = None
    target_capability: Optional[str] = None
    on_reject: Optional[ControlDirective] = None
    fallback_capability: Optional[str] = None

    # kind="subworkflow": run another registered workflow as a capability.
    subworkflow: Optional[SubworkflowRef] = None

    # kind="planner": run a planner capability that emits PlanArtifact, validate its declared
    # tasks, and execute those tasks under engine-owned policy/trace/replan mechanics.
    max_tasks: int = 8
    execution: Literal["sequential", "fanout"] = "sequential"
    max_replans: int = 1
    # Bounded recursive planning (pre-set OFF): a planned task may itself be a planner ONLY while
    # the current plan depth is below max_plan_depth (1 = flat plans, today's default). Each level
    # multiplies AI-authored work, so depth is opt-in per node and the cumulative task budget
    # across ALL levels is capped by max_total_planned_tasks — multiplication can never run away.
    max_plan_depth: int = 1
    max_total_planned_tasks: int = 32

    # policy (apply to step/evaluate nodes)
    retry: Optional[Retry] = None
    required_side_effects: List[str] = Field(default_factory=list)
    allow_raw_media_export: bool = False
    # backend scheduling/backpressure for this node's capability (e.g. a single-flight local model
    # lane). When ``scheduling.backend_key`` is set, the engine gates invocation through its
    # scheduler and holds the backend slot until the worker actually completes.
    scheduling: Optional[SchedulingPolicy] = None
    # declarative per-node model binding: name into the engine's ModelProfile registry, resolved at
    # run time and validated loudly at registration/preflight (unknown name never fails mid-run).
    model_profile: Optional[str] = None
    # Declarative per-NODE agent-memory selection (GoPro R4; mirrors model_profile): a
    # `resolve_agent_memory`-shaped config ("mode" string or {mode,...} mapping) applied to
    # agent capabilities invoked under this node. Validated loudly at graph validation;
    # delivered per call via context.metadata["agent_memory"]; None -> capability default.
    memory: Optional[Any] = None
    # Opt-in plan context injection. When true and a PlanArtifact exists, the executor adds a
    # rendered plan string to context.metadata["plan"] for this node's capability call only.
    inject_plan: bool = False
    # Opt-in machine self-description: inject this state's legal moves (labels, declared
    # semantics, LIVE gate budgets) as context.metadata["machine"] for this node's capability.
    inject_machine: bool = False

    def effective_capability(self) -> Optional[str]:
        """Capability bound to this node (falls back to the node id for ergonomic builders)."""

        if self.kind == "branch":
            return self.decider or self.capability or self.id
        if self.kind == "fanout":
            return self.item_capability or self.capability or self.id
        if self.kind == "evaluate":
            return self.evaluator or self.id
        if self.kind == "subworkflow":
            return None
        if self.kind == "planner":
            return self.capability or self.id
        return self.capability or self.id


class Transition(BaseModel):
    """A directed, policy-tagged transition — the machine is fully described by its transitions.

    ``policy`` says WHO selects this transition:
      - ``always``:    unconditional continuation (sequential flow)
      - ``decision``:  a decider (LLM, deterministic guard, or human answer) picked ``label``
      - ``on_accept``: the evaluator accepted (materialized from evaluate nodes)
      - ``on_reject``: the evaluator rejected (label = retry|retrace|replan); the bound mirrors
        the evaluator's own counter, which enforces it

    ``max_traversals`` is the pre-set gate for cycles: a ``decision`` transition that closes a
    loop MUST declare it (validated); the executor counts traversals per run and applies
    ``on_exhausted`` — ``"fail"`` (loud stop) or another label of the same branch node (a declared
    escape route). Halting on failure is implicit machine law and never materialized.
    """

    source: str
    target: str  # node id or END
    label: Optional[str] = None
    policy: Literal["always", "decision", "on_accept", "on_reject", "on_timeout"] = "always"
    # Self-description (the MCP analogy applied to control flow): WHEN this transition should be
    # taken, in words an AI decider can navigate by. Rendered into the machine card.
    description: str = ""
    max_traversals: Optional[int] = None
    on_exhausted: str = "fail"


class WorkflowDefinition(BaseModel):
    """A validated, executable workflow graph (product owns this; engine runs it).

    The definition is the canonical machine-as-data: serializable, visualizable, and COMPLETE —
    evaluator accept/reject routes are materialized into ``transitions`` at construction, so the
    whole machine can be read (validated, rendered, stored) from its own data alone.
    """

    workflow_id: str
    nodes: List[WorkflowNode]
    transitions: List[Transition]
    entry: str
    description: str = ""
    scheduling: Optional[SchedulingPolicy] = None
    limits: Optional[RuntimeLimits] = None

    @model_validator(mode="after")
    def _materialize_control_transitions(self) -> "WorkflowDefinition":
        """Mirror evaluate-node control flow into first-class transitions (idempotent).

        Enforcement of on_reject bounds stays in the evaluator mechanics (same counters); the
        mirrored transitions make the machine's data complete for validation/viz/storage.
        """

        existing = {(t.source, t.label, t.policy) for t in self.transitions}
        additions: List[Transition] = []
        for node in self.nodes:
            if node.kind != "evaluate":
                continue
            successor = next(
                (t.target for t in self.transitions if t.source == node.id and t.policy == "always"),
                END,
            )
            if (node.id, "accept", "on_accept") not in existing:
                additions.append(
                    Transition(source=node.id, target=successor, label="accept", policy="on_accept",
                               description="evaluator accepted — continue forward")
                )
            directive = node.on_reject
            kind = getattr(directive, "kind", None)
            if kind == "retry" and (node.id, "retry", "on_reject") not in existing:
                predecessor = next(
                    (t.source for t in self.transitions if t.target == node.id and t.policy == "always"),
                    END,
                )
                additions.append(
                    Transition(
                        source=node.id, target=predecessor, label="retry", policy="on_reject",
                        description="evaluator rejected — re-run the evaluated capability with criticism",
                        max_traversals=directive.max_attempts,
                    )
                )
            elif kind == "retrace" and (node.id, "retrace", "on_reject") not in existing:
                additions.append(
                    Transition(
                        source=node.id, target=directive.target, label="retrace", policy="on_reject",
                        description=f"evaluator rejected — go back to '{directive.target}' and re-run forward",
                        max_traversals=directive.max_retrace,
                    )
                )
            elif kind == "replan" and (node.id, "replan", "on_reject") not in existing:
                additions.append(
                    Transition(
                        source=node.id, target=directive.target, label="replan", policy="on_reject",
                        description=f"evaluator rejected — revise pending tasks at planner '{directive.target}'",
                        max_traversals=directive.max_replans,
                    )
                )
        if additions:
            self.transitions = [*self.transitions, *additions]
        return self

    def node(self, node_id: str) -> WorkflowNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(f"Unknown node: {node_id}")

    def node_ids(self) -> List[str]:
        return [node.id for node in self.nodes]

    def outgoing(self, node_id: str) -> List[Transition]:
        return [t for t in self.transitions if t.source == node_id]

    def definition_digest(self) -> str:
        """Content identity of this machine (sha256 of the canonical JSON dump).

        Machine-is-data needs data identity: caches/registries key on
        ``(workflow_id, digest)`` so a re-registered definition with the same id can never
        silently execute a stale compiled graph. Computed on demand — callers use it at
        register/compile time, not in the per-node hot path.
        """

        import hashlib

        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()[:16]

    def validate_graph(self) -> List[str]:
        """Return a list of structural errors (empty == valid)."""

        if not self.nodes:
            return ["workflow has no nodes"]

        ids = [node.id for node in self.nodes]
        known = set(ids)
        nodes_by_id = _first_nodes_by_id(self.nodes)

        errors: List[str] = []
        errors.extend(_validate_graph_identity(ids, self.entry, known))
        errors.extend(_validate_node_binding_contracts(self.nodes))
        errors.extend(_validate_node_memory_configs(self.nodes))
        errors.extend(_validate_wait_policies(self.nodes, self.transitions))
        errors.extend(_validate_node_shapes(self.nodes, known, nodes_by_id))
        errors.extend(_validate_transitions(self.transitions, nodes_by_id, known))
        errors.extend(_validate_cycle_gates(ids, self.transitions, known))
        errors.extend(_validate_injected_machine_descriptions(self.nodes, self.transitions))
        errors.extend(_validate_input_keys(self.nodes, known))
        return errors


def _validate_input_keys(nodes: List["WorkflowNode"], known: set[str]) -> List[str]:
    """An input_key must be resolvable at run time: the original input (``__input__``), a node
    id, or a declared output_key alias. A typo here used to silently feed ``None`` as the payload."""

    aliases = {node.output_key for node in nodes if node.output_key}
    allowed = known | aliases | {"__input__"}
    errors: List[str] = []
    for node in nodes:
        if node.input_key and node.input_key not in allowed:
            errors.append(
                f"node '{node.id}' input_key '{node.input_key}' references no known node id, "
                f"output_key alias, or '__input__' — it would silently read None at run time"
            )
    return errors


def _validate_node_binding_contracts(nodes: List["WorkflowNode"]) -> List[str]:
    """Reject context settings a node kind cannot consume instead of dropping them."""

    errors: List[str] = []
    for node in nodes:
        supported = NODE_CONTEXT_BINDINGS.get(node.kind)
        if supported is None:
            continue
        configured = {
            "inject_plan": node.inject_plan,
            "inject_machine": node.inject_machine,
            "memory": node.memory is not None,
            "model_profile": node.model_profile is not None,
        }
        rejected = sorted(
            name for name, enabled in configured.items() if enabled and name not in supported
        )
        if rejected:
            errors.append(
                f"node '{node.id}' ({node.kind}) does not support context binding(s): "
                f"{', '.join(rejected)}"
            )
    return errors


def _validate_injected_machine_descriptions(
    nodes: List[WorkflowNode], transitions: List["Transition"]
) -> List[str]:
    """The goblin rule made physical: a branch that injects its machine card must describe
    EVERY legal decision label — an option the navigator cannot understand is a validation
    error, not an opaque menu entry."""

    errors: List[str] = []
    for node in nodes:
        if node.kind != "branch" or not node.inject_machine:
            continue
        undescribed = sorted(
            t.label
            for t in transitions
            if t.source == node.id
            and t.policy == "decision"
            and t.label
            and not t.description.strip()
        )
        if undescribed:
            errors.append(
                f"branch '{node.id}' has inject_machine=True but no describe for label(s): "
                f"{', '.join(undescribed)} — every legal label must carry a description "
                f"for the machine card"
            )
    return errors


def _validate_wait_policies(nodes: List["WorkflowNode"], transitions: List["Transition"]) -> List[str]:
    """W1: wait intent is explicit machine data — REQUIRED on human nodes, forbidden
    elsewhere; a durable wait declares exactly ONE on_timeout transition, a local wait none;
    on_timeout transitions exist only for durable waits."""

    errors: List[str] = []
    human_nodes = [n for n in nodes if n.kind == "human"]
    for node in nodes:
        if node.wait_policy is not None and node.kind != "human":
            errors.append(f"node '{node.id}' ({node.kind}) must not declare wait_policy")
    timeout_sources: Dict[str, int] = {}
    for transition in transitions:
        if transition.policy == "on_timeout":
            timeout_sources[transition.source] = timeout_sources.get(transition.source, 0) + 1
    for node in human_nodes:
        if node.wait_policy is None:
            errors.append(
                f"wait node '{node.id}' has no wait_policy — declare LocalWaitPolicy() or "
                "DurableWaitPolicy(timeout_s=..., ...) explicitly (v0.9 W1 contract)"
            )
            continue
        from ai_workflow_engine.waits import wait_policy_from  # call-time (leaf discipline)

        try:
            policy = wait_policy_from(node.wait_policy)
        except (ValueError, TypeError) as exc:
            errors.append(f"wait node '{node.id}' has an invalid wait_policy: {exc}")
            continue
        declared = timeout_sources.get(node.id, 0)
        if policy.mode == "durable" and declared != 1:
            errors.append(
                f"durable wait '{node.id}' must declare exactly ONE on_timeout transition, found {declared}"
            )
        if policy.mode == "local" and declared:
            errors.append(f"local wait '{node.id}' must not have on_timeout transitions")
    wait_ids = {n.id for n in human_nodes}
    for source in timeout_sources:
        if source not in wait_ids:
            errors.append(f"on_timeout transition from non-wait node '{source}'")
    return errors


def _validate_node_memory_configs(nodes: List["WorkflowNode"]) -> List[str]:
    """Loud-at-build agent-memory validation (GoPro R4): an unknown mode or bad option
    must never fail mid-run."""

    if all(node.memory is None for node in nodes):
        # F1.1: memory-free graphs never import the memory stack at validation time.
        return []
    from ai_workflow_engine.memory import resolve_agent_memory

    errors: List[str] = []
    for node in nodes:
        if node.memory is None:
            continue
        try:
            resolve_agent_memory(node.memory)
        except (TypeError, ValueError) as exc:
            errors.append(f"node '{node.id}' memory config invalid: {exc}")
    return errors


def _first_nodes_by_id(nodes: List[WorkflowNode]) -> Dict[str, WorkflowNode]:
    nodes_by_id: Dict[str, WorkflowNode] = {}
    for node in nodes:
        nodes_by_id.setdefault(node.id, node)
    return nodes_by_id


def _validate_graph_identity(ids: List[str], entry: str, known: set[str]) -> List[str]:
    errors: List[str] = []
    seen: set[str] = set()
    for node_id in ids:
        if node_id in seen:
            errors.append(f"duplicate node id: {node_id}")
        seen.add(node_id)
    if entry not in known:
        errors.append(f"entry node not found: {entry}")
    return errors


def _validate_node_shapes(
    nodes: List[WorkflowNode],
    known: set[str],
    nodes_by_id: Dict[str, WorkflowNode],
) -> List[str]:
    errors: List[str] = []
    for node in nodes:
        if node.kind not in KNOWN_NODE_KINDS:
            errors.append(f"node '{node.id}' has unsupported kind: {node.kind}")
            continue
        if node.kind == "branch":
            errors.extend(_validate_branch_node(node, known))
        elif node.kind == "fanout":
            errors.extend(_validate_fanout_node(node))
        elif node.kind == "evaluate":
            errors.extend(_validate_evaluate_node(node, known, nodes_by_id))
        elif node.kind == "subworkflow":
            errors.extend(_validate_subworkflow_node(node))
        elif node.kind == "planner":
            errors.extend(_validate_planner_node(node))
    return errors


def _validate_branch_node(node: WorkflowNode, known: set[str]) -> List[str]:
    errors: List[str] = []
    if not node.branches:
        errors.append(f"branch node '{node.id}' has no branches")
    for label, target in node.branches.items():
        if target != END and target not in known:
            errors.append(f"branch node '{node.id}' label '{label}' -> unknown node: {target}")
    return errors


def _validate_fanout_node(node: WorkflowNode) -> List[str]:
    errors: List[str] = []
    if not node.fan_items_key:
        errors.append(f"fanout node '{node.id}' missing fan_items_key")
    if not (node.item_capability or node.capability):
        errors.append(f"fanout node '{node.id}' missing item_capability")
    return errors


def _validate_evaluate_node(
    node: WorkflowNode,
    known: set[str],
    nodes_by_id: Dict[str, WorkflowNode],
) -> List[str]:
    errors: List[str] = []
    if not node.evaluator:
        errors.append(f"evaluate node '{node.id}' missing evaluator")
    if not node.target_capability:
        errors.append(f"evaluate node '{node.id}' missing target_capability")
    if isinstance(node.on_reject, Retrace) and node.on_reject.target not in known:
        errors.append(f"evaluate node '{node.id}' retrace target unknown: {node.on_reject.target}")
    if isinstance(node.on_reject, Replan) and node.on_reject.target not in known:
        errors.append(f"evaluate node '{node.id}' replan target unknown: {node.on_reject.target}")
    if (
        isinstance(node.on_reject, Replan)
        and node.on_reject.target in known
        and nodes_by_id[node.on_reject.target].kind != "planner"
    ):
        errors.append(
            f"evaluate node '{node.id}' replan target is not a planner: {node.on_reject.target}"
        )
    return errors


def _validate_subworkflow_node(node: WorkflowNode) -> List[str]:
    if not node.subworkflow:
        return [f"subworkflow node '{node.id}' missing subworkflow ref"]
    return []


def _validate_planner_node(node: WorkflowNode) -> List[str]:
    errors: List[str] = []
    if not (node.capability or node.id):
        errors.append(f"planner node '{node.id}' missing capability")
    if node.max_tasks < 1:
        errors.append(f"planner node '{node.id}' max_tasks must be >= 1")
    if node.max_replans < 0:
        errors.append(f"planner node '{node.id}' max_replans must be >= 0")
    if node.max_plan_depth < 1:
        errors.append(f"planner node '{node.id}' max_plan_depth must be >= 1")
    if node.max_total_planned_tasks < 1:
        errors.append(f"planner node '{node.id}' max_total_planned_tasks must be >= 1")
    if node.max_plan_depth > 1 and node.execution == "fanout":
        errors.append(f"planner node '{node.id}' recursive planning requires execution='sequential'")
    return errors


def _validate_transitions(
    transitions: List[Transition],
    nodes_by_id: Dict[str, WorkflowNode],
    known: set[str],
) -> List[str]:
    errors: List[str] = []
    for transition in transitions:
        if transition.source not in known:
            errors.append(f"transition from unknown node: {transition.source}")
        if transition.target != END and transition.target not in known:
            errors.append(f"transition to unknown node: {transition.target}")
        errors.extend(_validate_transition_gate(transition))
        errors.extend(_validate_transition_exhaustion(transition, nodes_by_id))
    return errors


def _validate_transition_gate(transition: Transition) -> List[str]:
    if transition.max_traversals is None:
        return []
    errors: List[str] = []
    if transition.max_traversals < 1:
        errors.append(
            f"transition '{transition.source}' -> '{transition.target}' max_traversals must be >= 1"
        )
    if transition.policy in ("always", "on_accept"):
        errors.append(
            f"transition '{transition.source}' -> '{transition.target}' declares max_traversals on "
            f"policy '{transition.policy}' — pre-set gates are enforced on decision transitions only"
        )
    return errors


def _validate_transition_exhaustion(
    transition: Transition,
    nodes_by_id: Dict[str, WorkflowNode],
) -> List[str]:
    if transition.on_exhausted == "fail":
        return []
    if transition.policy != "decision":
        return [
            f"transition '{transition.source}' -> '{transition.target}' declares on_exhausted on "
            f"policy '{transition.policy}' — escape labels exist only on decision transitions"
        ]
    source_node = nodes_by_id.get(transition.source)
    if source_node is None or transition.on_exhausted not in source_node.branches:
        return [
            f"transition '{transition.source}' label '{transition.label}' on_exhausted "
            f"'{transition.on_exhausted}' is not a declared label of that branch"
        ]
    if transition.on_exhausted == transition.label:
        return [
            f"transition '{transition.source}' label '{transition.label}' on_exhausted must differ "
            f"from its own label"
        ]
    return []


def _validate_cycle_gates(
    ids: List[str],
    transitions: List[Transition],
    known: set[str],
) -> List[str]:
    # Pre-set gate law: every cycle in the {always ∪ decision} subgraph must be broken by at
    # least one BOUNDED decision transition (its counter closes the loop after N traversals).
    # on_accept/on_reject transitions carry their own evaluator-owned counters and are exempt.
    transition = _find_unbounded_cycle_transition(
        ids,
        _unbounded_cycle_adjacency(transitions, known),
        known,
    )
    if transition is None:
        return []
    return [
        f"unbounded cycle: transition '{transition.source}' -[{transition.label or 'always'}]-> "
        f"'{transition.target}' closes a loop with no pre-set gate; declare max_traversals on a "
        f"decision transition in this cycle"
    ]


def _unbounded_cycle_adjacency(
    transitions: List[Transition],
    known: set[str],
) -> Dict[str, List[Transition]]:
    adjacency: Dict[str, List[Transition]] = {}
    for transition in transitions:
        if _participates_in_unbounded_cycle_search(transition, known):
            adjacency.setdefault(transition.source, []).append(transition)
    return adjacency


def _participates_in_unbounded_cycle_search(transition: Transition, known: set[str]) -> bool:
    if transition.source not in known or transition.target == END or transition.target not in known:
        return False
    return transition.policy == "always" or (
        transition.policy == "decision" and transition.max_traversals is None
    )


def _find_unbounded_cycle_transition(
    ids: List[str],
    adjacency: Dict[str, List[Transition]],
    known: set[str],
) -> Transition | None:
    color: Dict[str, int] = dict.fromkeys(known, 0)  # 0 white, 1 grey, 2 black
    for node_id in ids:
        if color.get(node_id) != 0:
            continue
        offender = _visit_unbounded_cycle(node_id, adjacency, color)
        if offender is not None:
            return offender
    return None


def _visit_unbounded_cycle(
    node_id: str,
    adjacency: Dict[str, List[Transition]],
    color: Dict[str, int],
) -> Transition | None:
    color[node_id] = 1
    for transition in adjacency.get(node_id, []):
        if color[transition.target] == 1:
            return transition
        if color[transition.target] == 0:
            offender = _visit_unbounded_cycle(transition.target, adjacency, color)
            if offender is not None:
                return offender
    color[node_id] = 2
    return None


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
        self._transitions: List[Transition] = []
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
        already_target = any(t.target == node.id for t in self._transitions)
        if self._last is not None and self._last.kind != "branch" and not already_target:
            self._transitions.append(Transition(source=self._last.id, target=node.id))
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
        model_profile: Optional[str] = None,
        memory: Optional[Any] = None,
        inject_plan: bool = False,
        inject_machine: bool = False,
        title: str = "",
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
                model_profile=model_profile,
                memory=memory,
                inject_plan=inject_plan,
                inject_machine=inject_machine,
                title=title,
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
        bounds: Optional[Dict[str, int]] = None,
        exhausted: Optional[Dict[str, str]] = None,
        describe: Optional[Dict[str, str]] = None,
        model_profile: Optional[str] = None,
        memory: Optional[Any] = None,
        inject_plan: bool = False,
        inject_machine: bool = False,
        title: str = "",
        description: str = "",
    ) -> "WorkflowBuilder":
        """Declare a decision state. ``bounds`` sets the pre-set gate (max traversals) per label —
        REQUIRED for any label that closes a cycle; ``exhausted`` optionally maps a bounded label
        to the escape label taken once its gate is exhausted (default: loud failure)."""

        node = WorkflowNode(
            id=node_id,
            kind="branch",
            decider=decider or node_id,
            branches=dict(branches),
            model_profile=model_profile,
            memory=memory,
            inject_plan=inject_plan,
            inject_machine=inject_machine,
            title=title,
            description=description,
        )
        self._append(node)
        for label, target in branches.items():
            self._transitions.append(
                Transition(
                    source=node_id,
                    target=target,
                    label=label,
                    policy="decision",
                    description=(describe or {}).get(label, ""),
                    max_traversals=(bounds or {}).get(label),
                    on_exhausted=(exhausted or {}).get(label, "fail"),
                )
            )
        return self

    def fanout(
        self,
        node_id: str,
        *,
        capability: str,
        items_key: str,
        max_parallel: Optional[int] = None,
        max_items: Optional[int] = None,
        output_key: Optional[str] = None,
        model_profile: Optional[str] = None,
        memory: Optional[Any] = None,
        inject_plan: bool = False,
        inject_machine: bool = False,
        title: str = "",
        description: str = "",
    ) -> "WorkflowBuilder":
        self._append(
            WorkflowNode(
                id=node_id,
                kind="fanout",
                item_capability=capability,
                fan_items_key=items_key,
                max_parallel=max_parallel,
                max_items=max_items,
                output_key=output_key,
                model_profile=model_profile,
                memory=memory,
                inject_plan=inject_plan,
                inject_machine=inject_machine,
                title=title,
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
        model_profile: Optional[str] = None,
        memory: Optional[Any] = None,
        inject_plan: bool = False,
        inject_machine: bool = False,
        title: str = "",
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
                model_profile=model_profile,
                memory=memory,
                inject_plan=inject_plan,
                inject_machine=inject_machine,
                title=title,
                description=description,
            )
        )
        return self

    def plan(
        self,
        node_id: str,
        *,
        capability: Optional[str] = None,
        input_key: Optional[str] = None,
        output_key: Optional[str] = None,
        max_tasks: int = 8,
        execution: Literal["sequential", "fanout"] = "sequential",
        max_replans: int = 1,
        max_plan_depth: int = 1,
        max_total_planned_tasks: int = 32,
        model_profile: Optional[str] = None,
        memory: Optional[Any] = None,
        inject_plan: bool = False,
        inject_machine: bool = False,
        title: str = "",
        description: str = "",
    ) -> "WorkflowBuilder":
        self._append(
            WorkflowNode(
                id=node_id,
                kind="planner",
                capability=capability or node_id,
                input_key=input_key,
                output_key=output_key,
                max_tasks=max_tasks,
                execution=execution,
                max_replans=max_replans,
                max_plan_depth=max_plan_depth,
                max_total_planned_tasks=max_total_planned_tasks,
                model_profile=model_profile,
                memory=memory,
                inject_plan=inject_plan,
                inject_machine=inject_machine,
                title=title,
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
        inject_plan: bool = False,
        inject_machine: bool = False,
        title: str = "",
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
                inject_plan=inject_plan,
                inject_machine=inject_machine,
                title=title,
                description=description,
            )
        )
        return self

    def human(
        self,
        node_id: str,
        *,
        wait_policy: Any,
        timeout_to: Optional[str] = None,
        capability: Optional[str] = None,
        inject_plan: bool = False,
        inject_machine: bool = False,
        memory: Optional[Any] = None,
        model_profile: Optional[str] = None,
        title: str = "",
        description: str = "",
    ) -> "WorkflowBuilder":
        """Wait-capable node. ``wait_policy`` is REQUIRED (v0.9 breaking change, W1): pass
        ``LocalWaitPolicy()`` for today's in-process snapshot/resume, or
        ``DurableWaitPolicy(timeout_s=...)`` plus ``timeout_to=`` naming the node the
        declared timeout transition routes to. Local waits must NOT declare timeout_to.

        Human nodes are declared nodes and carry the same optional decorations as steps:
        ``memory=`` (agent-memory config delivered as ``context.metadata['agent_memory']``)
        and ``model_profile=`` (bound at invocation) — one contract whether the definition
        comes from this builder or is hand-declared."""

        from ai_workflow_engine.waits import wait_policy_from  # call-time (leaf discipline)

        policy = wait_policy_from(wait_policy)
        if policy.mode == "durable":
            if not timeout_to:
                raise WorkflowValidationError(
                    [f"durable wait '{node_id}' requires timeout_to= (the declared timeout route)"]
                )
        elif timeout_to is not None:
            raise WorkflowValidationError(
                [f"local wait '{node_id}' must not declare timeout_to (no durable timeout mechanics)"]
            )
        self._append(
            WorkflowNode(
                id=node_id,
                kind="human",
                capability=capability or node_id,
                inject_plan=inject_plan,
                inject_machine=inject_machine,
                memory=memory,
                model_profile=model_profile,
                title=title,
                description=description,
                wait_policy=policy.model_dump(),
            )
        )
        if policy.mode == "durable":
            self._transitions.append(
                Transition(
                    source=node_id,
                    target=timeout_to,
                    policy="on_timeout",
                    description=f"timeout after {policy.timeout_s:g}s",
                )
            )
        return self

    def wait(self, node_id: str, **kwargs: Any) -> "WorkflowBuilder":
        """Generic wait node (webhook/background-job shaped) — same machinery as human."""

        return self.human(node_id, **kwargs)

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

        # Connect terminal nodes (no outgoing transition, non-branch) to END.
        sources_with_outgoing = {t.source for t in self._transitions}
        transitions = list(self._transitions)
        for node in self._nodes:
            if node.kind == "branch":
                continue
            if node.id not in sources_with_outgoing:
                transitions.append(Transition(source=node.id, target=END))

        definition = WorkflowDefinition(
            workflow_id=self.workflow_id,
            nodes=list(self._nodes),
            transitions=transitions,
            entry=self._entry,
            description=self.description,
            scheduling=self._scheduling,
            limits=self._limits,
        )
        errors = definition.validate_graph()
        if errors:
            raise WorkflowValidationError(errors)
        return definition


def render_machine_card(
    definition: WorkflowDefinition,
    node_id: str,
    state: Optional[dict] = None,
) -> str:
    """Render one state's legal moves as text an AI decider can navigate by.

    The MCP analogy applied to control flow: like a tool card describes a callable tool, the
    machine card describes a state — its outgoing transitions with their declared semantics and
    the LIVE pre-set gate budgets (consumed traversals come from ``state["transition_counts"]``).
    Pure function of its inputs; deterministic, declaration-ordered.
    """

    node = definition.node(node_id)
    counts = (state or {}).get("transition_counts", {}) or {}
    lines = [f"state: {node.id} ({node.kind})"]
    if node.description:
        lines.append(f"about: {node.description}")
    outs = definition.outgoing(node_id)
    if not outs:
        lines.append("transitions: none (terminal state)")
        return "\n".join(lines)
    lines.append("transitions:")
    for t in outs:
        target = "END" if t.target == END else t.target
        if t.policy == "decision":
            entry = f"- {t.label} -> {target}"
        elif t.policy == "always":
            entry = f"- (continue) -> {target}"
        else:
            entry = f"- [{t.policy}:{t.label}] -> {target}"
        if t.description:
            entry += f" — {t.description}"
        if t.max_traversals is not None:
            if t.policy == "decision":
                used = counts.get(f"{t.source}|{t.label}", 0)
                remaining = max(t.max_traversals - used, 0)
                if remaining == 0:
                    escape = (
                        f"exhausted -> {t.on_exhausted}"
                        if t.on_exhausted != "fail"
                        else "selecting it now FAILS"
                    )
                    entry += f" [gate EXHAUSTED ({t.max_traversals}/{t.max_traversals} used); {escape}]"
                else:
                    entry += f" [gate: {remaining} of {t.max_traversals} remaining]"
            else:
                entry += f" [bounded <= {t.max_traversals}]"
        lines.append(entry)
    return "\n".join(lines)
