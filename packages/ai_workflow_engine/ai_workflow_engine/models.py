"""Shared workflow execution models.

These models are intentionally product-agnostic. Product-specific graphs keep their own state,
but use these goal/trace/artifact records so workflow execution is observable and debuggable.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


CapabilityKind = Literal[
    "workflow",
    "llm",
    "tool",
    "deterministic",
    "media",
    "voice",
    "agent",
    "external",
    "human",
]
CapabilityStatus = Literal["accepted", "failed", "partial", "rejected"]
FailMode = Literal["fallback", "fail_closed", "fail_open", "ask_user"]
PrivacyLevel = Literal["public", "internal", "confidential", "secret"]
WorkflowResultStatus = Literal[
    "accepted",
    "completed",
    "failed",
    "unknown",
    "uncertain",
    "partial",
    "low_confidence",
    "insufficient_evidence",
    "requires_user_input",
    "external_tool_unavailable",
]
WorkflowTraceSeverity = Literal["debug", "info", "warning", "error"]
ObservationDetailKind = Literal[
    "rendered_prompt",
    "llm_response",
    "tool_payload",
    "tool_result",
    "artifact_preview",
    "planner_output",
    "memory_projection",
]
ObservationRedactionState = Literal["none", "redacted", "digest_only"]
EvaluationAction = Literal[
    "accept",
    "repair",
    "retry_capability",
    "retrace_to",
    "replan",
    "fallback",
    "fail",
    "ask_user",
]
WorkflowStepAction = Literal["invoke", "finish", "fail", "ask_user"]
ClarificationStatus = Literal["answered", "pending", "provisional"]
AgentStepAction = Literal["tool", "finish", "fail"]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class WorkflowGoal(BaseModel):
    """Normalized user objective passed through workflow orchestration.

    A goal is not a product-specific config object. Product workflows may read the objective and
    constraints, but they still own their own typed state, prompts, validators, and side effects.
    """

    workflow_type: str
    objective: str
    constraints: Dict[str, Any] = Field(default_factory=dict)
    delivery_target: Optional[str] = None
    user_id: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    goal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class WorkflowInstrumentSpec(BaseModel):
    """Product-agnostic description of a callable workflow capability."""

    name: str
    kind: CapabilityKind
    description: str = ""


class WorkflowDecision(BaseModel):
    """Supervisor decision for which instrument should handle a goal."""

    instrument_name: str
    rationale: str = ""
    confidence: float = 1.0


class WorkflowSupervisorResult(BaseModel):
    """Result envelope returned by a supervisor-owned instrument run."""

    decision: WorkflowDecision
    result: Any


class WorkflowStepDecision(BaseModel):
    """One supervisor-loop decision over registered capabilities."""

    action: WorkflowStepAction
    capability_name: Optional[str] = None
    payload: Any = None
    rationale: str = ""
    confidence: float = 1.0


class WorkflowLoopResult(BaseModel):
    """Result of a bounded supervisor loop."""

    status: Literal["completed", "failed", "waiting"] = "completed"
    output: Any = None
    decisions: List[WorkflowStepDecision] = Field(default_factory=list)
    capability_results: List["CapabilityResult"] = Field(default_factory=list)
    error: Optional[str] = None


class WorkflowResult(BaseModel):
    """Product-neutral workflow output envelope with explicit uncertainty states."""

    status: WorkflowResultStatus = "completed"
    output: Any = None
    confidence: Optional[float] = None
    uncertainty_reason: Optional[str] = None
    evidence_refs: List["EvidenceRef"] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowArtifact(BaseModel):
    """Product-agnostic side-effect artifact owned by a workflow run."""

    path: str
    kind: Literal["file", "media", "package", "reference", "other"] = "file"
    source: Optional[str] = None
    owner_node: Optional[str] = None
    cleanup_on_failure: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)
    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ClarificationOption(BaseModel):
    """One selectable human answer for a clarification request."""

    label: str
    value: str
    description: str = ""


class HumanClarificationRequest(BaseModel):
    """Product-neutral request to ask a human for workflow direction."""

    question: str
    clarification_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    options: List[ClarificationOption] = Field(default_factory=list)
    allow_free_text: bool = True
    default_value: Optional[str] = None
    continue_without_answer: bool = False
    timeout_s: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HumanClarificationResponse(BaseModel):
    """Answer or pause record for a human clarification request."""

    clarification_id: str
    status: ClarificationStatus
    value: Optional[str] = None
    answer_text: Optional[str] = None
    provisional: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentToolCall(BaseModel):
    """One tool/capability call requested by an agent controller."""

    tool_name: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class AgentStepDecision(BaseModel):
    """One bounded agent episode decision."""

    action: AgentStepAction
    tool_name: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    rationale: str = ""


class AgentToolStep(BaseModel):
    """Recorded result of one tool call inside an agent episode."""

    call: AgentToolCall
    status: CapabilityStatus
    output: Any = None
    error: Optional[str] = None


class AgentRunRequest(BaseModel):
    """Request to run one bounded agent episode."""

    prompt: str
    allowed_tools: List[str] = Field(default_factory=list)
    max_steps: Optional[int] = None
    max_tool_calls: Optional[int] = None
    output_schema_name: Optional[str] = None
    subscription_mode: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    """Structured output of one bounded agent episode."""

    status: Literal["completed", "failed", "truncated"] = "completed"
    steps: List[AgentToolStep] = Field(default_factory=list)
    output: Any = None
    subscription_call: bool = False
    estimated_metered_usd: Optional[float] = None
    notional_cost_usd: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowTraceEvent(BaseModel):
    node: str
    timestamp: str = Field(default_factory=_utc_timestamp)
    sequence: Optional[int] = None
    attempt: int = 1
    decision: Optional[str] = None
    error: Optional[str] = None
    artifacts: List[str] = Field(default_factory=list)
    elapsed_ms: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    detail_refs: List[str] = Field(default_factory=list)
    phase: Optional[str] = None
    severity: WorkflowTraceSeverity = "info"
    run_id: Optional[str] = None


class ObservationDetail(BaseModel):
    """Heavy/private observability payload linked from a compact trace event."""

    model_config = ConfigDict(populate_by_name=True)

    detail_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str
    run_id: Optional[str] = None
    sequence: Optional[int] = None
    kind: ObservationDetailKind
    privacy: PrivacyLevel = "internal"
    redaction_state: ObservationRedactionState = "digest_only"
    content_type: str = "text/plain"
    text: Optional[str] = None
    json_value: Optional[Dict[str, Any]] = Field(default=None, alias="json")
    artifact_id: Optional[str] = None
    digest: Optional[str] = None

    @model_validator(mode="after")
    def _reject_raw_bytes(self) -> "ObservationDetail":
        if _contains_raw_bytes(self.json_value):
            raise ValueError("ObservationDetail json payload must not contain raw bytes")
        return self


class WorkflowUsageEvent(BaseModel):
    """One metered paid-provider call inside a workflow run."""

    provider: str = "openai"
    operation: Literal["chat", "image", "tool"] = "chat"
    cost_class: Literal["metered", "subscription_notional"] = "metered"
    node: str
    run_id: Optional[str] = None
    sequence: Optional[int] = None
    model: str = ""
    attempt: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_token_details: Dict[str, Any] = Field(default_factory=dict)
    output_token_details: Dict[str, Any] = Field(default_factory=dict)
    estimated_usd: Optional[float] = None
    notional_usd: Optional[float] = None
    request_id: Optional[str] = None
    elapsed_ms: Optional[int] = None
    success: bool = True
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


def _contains_raw_bytes(value: Any) -> bool:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    if isinstance(value, dict):
        return any(_contains_raw_bytes(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_raw_bytes(item) for item in value)
    return False


class WorkflowUsageSummary(BaseModel):
    """Aggregated usage/cost view for one workflow run."""

    events: List[WorkflowUsageEvent] = Field(default_factory=list)
    # Pre-call worker-op counter (chat/agent/external under a max_worker_calls cap). Lives on
    # the SERIALIZED summary — not on the transient usage context — so suspend/resume keeps the
    # cumulative-budget promise instead of silently regranting the whole allowance.
    worker_call_count: int = 0

    def add_event(self, event: WorkflowUsageEvent) -> None:
        self.events.append(event)

    @property
    def text_call_count(self) -> int:
        return sum(1 for event in self.events if event.operation == "chat")

    @property
    def image_call_count(self) -> int:
        return sum(1 for event in self.events if event.operation == "image")

    @property
    def tool_call_count(self) -> int:
        return sum(1 for event in self.events if event.operation == "tool")

    @property
    def tool_character_count(self) -> int:
        total = 0
        for event in self.events:
            if event.operation != "tool":
                continue
            details = event.input_token_details or {}
            metadata = event.metadata or {}
            total += int(details.get("characters", 0) or metadata.get("character_count", 0) or 0)
        return total

    @property
    def input_tokens(self) -> int:
        return sum(event.input_tokens for event in self.events)

    @property
    def output_tokens(self) -> int:
        return sum(event.output_tokens for event in self.events)

    @property
    def total_tokens(self) -> int:
        return sum(event.total_tokens for event in self.events)

    @property
    def cached_input_tokens(self) -> int:
        total = 0
        for event in self.events:
            details = event.input_token_details or {}
            typed_cached = int(details.get("text_cached_tokens", 0) or details.get("cached_text_tokens", 0) or 0)
            typed_cached += int(details.get("image_cached_tokens", 0) or details.get("cached_image_tokens", 0) or 0)
            generic_cached = int(details.get("cache_read", 0) or details.get("cached_tokens", 0) or 0)
            total += typed_cached if typed_cached else generic_cached
        return total

    @property
    def metered_usd(self) -> Optional[float]:
        costs = [
            event.estimated_usd
            for event in self.events
            if event.cost_class == "metered" and event.estimated_usd is not None
        ]
        return round(sum(costs), 6) if costs else None

    @property
    def notional_usd(self) -> Optional[float]:
        costs = [event.notional_usd for event in self.events if event.notional_usd is not None]
        return round(sum(costs), 6) if costs else None

    @property
    def estimated_usd(self) -> Optional[float]:
        return self.metered_usd


class WorkflowRunContext(BaseModel):
    workflow_id: str
    workflow_type: str
    goal_id: Optional[str] = None
    delivery_target: Optional[str] = None
    user_id: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowInput(BaseModel):
    """Product-neutral input envelope for compiling or starting a workflow."""

    goal: WorkflowGoal
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence: List["EvidenceRef"] = Field(default_factory=list)


class WorkflowTrigger(BaseModel):
    """How a workflow was started without naming a product transport."""

    kind: Literal["command", "auto", "schedule", "api", "human", "system"] = "api"
    source: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    """Reference to evidence/artifacts without storing raw bytes in workflow state."""

    ref_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str
    uri: str
    media_type: Optional[str] = None
    summary: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExternalWriteRequest(BaseModel):
    """Structured handoff to a product-owned external/domain system."""

    target: str
    operation: str = "upsert"
    idempotency_key: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    privacy_level: PrivacyLevel = "internal"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExternalWriteResult(BaseModel):
    """Result of an external/domain write without storing domain state in the engine."""

    status: Literal["created", "updated", "duplicate", "rejected"] = "created"
    target: str
    operation: str = "upsert"
    idempotency_key: str
    external_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SchedulingPolicy(BaseModel):
    """Generic run scheduling behavior for live or queued workflows."""

    mode: Literal[
        "run_immediately",
        "fan_out_gather",
        "queue",
        "replay_process_all",
        "run_latest",
        "live_latest_only",
        "drop_stale",
        "drop_not_queue",
        "single_flight_cancel",
        "coalesce",
    ] = "run_immediately"
    max_queue_size: int = 1
    stale_after_s: Optional[float] = None
    priority: int = 0
    backend_key: Optional[str] = None
    max_backend_concurrency: Optional[int] = None


class RuntimeLimits(BaseModel):
    """Per-run limits that bound loops, tool calls, and paid work.

    The ONE budget source (v0.9 clean contract): ceilings configured here enforce
    unconditionally. ``0`` is an honest hard-zero cap; "no cap" = leave the field None.
    Unknown keys are rejected — a typo'd ceiling must never silently mean "unlimited".
    """

    model_config = ConfigDict(extra="forbid")

    max_steps: int = 32
    max_retries: int = 1
    max_retrace: int = 1
    max_parallel_children: int = 4
    timeout_s: Optional[float] = None
    max_text_calls: Optional[int] = None
    max_image_calls: Optional[int] = None
    max_estimated_usd: Optional[float] = None
    max_worker_calls: Optional[int] = None
    max_input_tokens_per_call: Optional[int] = None
    max_output_tokens_per_call: Optional[int] = None
    max_images_per_call: Optional[int] = None
    max_estimated_usd_per_call: Optional[float] = None


class SafetyPolicy(BaseModel):
    """Shared safety controls for capability execution."""

    fail_mode: FailMode = "fallback"
    require_registered_capabilities: bool = True
    require_trace: bool = True
    allowed_side_effects: List[str] = Field(default_factory=list)


class SessionState(BaseModel):
    """In-memory workflow session state with an explicit future checkpoint seam."""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: Literal["running", "waiting", "completed", "failed"] = "running"
    step_index: int = 0
    data: Dict[str, Any] = Field(default_factory=dict)


class WorkflowCheckpoint(BaseModel):
    """Durable snapshot of engine-owned session progress."""

    checkpoint_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str
    workflow_type: str
    session_id: str
    step_index: int = 0
    status: Literal["running", "waiting", "completed", "failed"] = "running"
    data: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowProfile(BaseModel):
    """Reusable profile/configuration compiled before a workflow runs."""

    profile_id: str = "default"
    workflow_type: str
    constraints: Dict[str, Any] = Field(default_factory=dict)
    requested_capabilities: List[str] = Field(default_factory=list)
    fail_mode: FailMode = "fallback"
    scheduling: SchedulingPolicy = Field(default_factory=SchedulingPolicy)
    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    safety: SafetyPolicy = Field(default_factory=SafetyPolicy)


class RuntimePlan(BaseModel):
    """Inspectable product-neutral plan produced from a profile and trigger."""

    workflow_type: str
    profile_id: str = "default"
    constraints: Dict[str, Any] = Field(default_factory=dict)
    capability_names: List[str] = Field(default_factory=list)
    fail_mode: FailMode = "fallback"
    scheduling: SchedulingPolicy = Field(default_factory=SchedulingPolicy)
    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    safety: SafetyPolicy = Field(default_factory=SafetyPolicy)
    warnings: List[str] = Field(default_factory=list)


class ModelProfile(BaseModel):
    """Provider/model selection for a capability or node."""

    name: str
    provider: str = "openai"
    model: str
    temperature: float = 0.0
    timeout_s: Optional[float] = None
    max_retries: int = 1
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CapabilitySpec(BaseModel):
    """Typed, traceable runtime capability available to a supervisor."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    kind: CapabilityKind
    description: str = ""
    input_model: Optional[type[BaseModel]] = Field(default=None, exclude=True)
    output_model: Optional[type[BaseModel]] = Field(default=None, exclude=True)
    input_schema_name: Optional[str] = None
    output_schema_name: Optional[str] = None
    side_effects: List[str] = Field(default_factory=list)
    metered: bool = False
    timeout_s: Optional[float] = None
    max_attempts: int = 1
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.input_model and not self.input_schema_name:
            self.input_schema_name = self.input_model.__name__
        if self.output_model and not self.output_schema_name:
            self.output_schema_name = self.output_model.__name__


class CapabilityContext(BaseModel):
    """Context passed into a capability invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    goal: WorkflowGoal
    run_context: WorkflowRunContext
    plan: Optional[RuntimePlan] = None
    usage_summary: WorkflowUsageSummary = Field(default_factory=WorkflowUsageSummary)
    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    # Per-node declarative model binding, resolved by the executor for this invocation.
    model_profile: Optional[ModelProfile] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CapabilityResult(BaseModel):
    """Result envelope for one capability invocation."""

    status: CapabilityStatus = "accepted"
    output: Any = None
    error: Optional[str] = None
    artifacts: List[WorkflowArtifact] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CriticismEnvelope(BaseModel):
    """Evaluator criticism used for repair/retry/retrace prompts."""

    observed: str
    expected: str
    severity: Literal["low", "medium", "high", "blocking"] = "medium"
    target_capability: Optional[str] = None
    user_visible_effect: str = ""


class EvaluationDecision(BaseModel):
    """Generic evaluator decision that controls retry/retrace/fallback."""

    action: EvaluationAction
    rationale: str = ""
    target_capability: Optional[str] = None
    retrace_to: Optional[str] = None
    criticism: Optional[CriticismEnvelope] = None
    user_visible_effect: str = ""
    confidence: float = 1.0


class RetryPolicy(BaseModel):
    max_attempts: int = 1
    retry_on_status: List[CapabilityStatus] = Field(default_factory=lambda: ["failed"])


class RetracePolicy(BaseModel):
    max_retrace: int = 1
    allowed_targets: List[str] = Field(default_factory=list)


WorkflowInput.model_rebuild()
WorkflowLoopResult.model_rebuild()
WorkflowResult.model_rebuild()
