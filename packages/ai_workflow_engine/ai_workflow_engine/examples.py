"""Small runnable product-neutral workflows for package consumers and tests."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ai_workflow_engine.engine import (
    CapabilityCall,
    CapabilityRegistry,
    CapabilityRuntime,
    ExternalAdapterCapability,
    HumanClarificationCapability,
    InMemoryExternalWriteSink,
    InMemoryHumanClarificationChannel,
    InMemoryTraceSink,
    RuntimePlanCompiler,
    WorkflowScheduler,
    capability_context_for_goal,
    gather_capabilities,
)
from ai_workflow_engine.models import (
    ClarificationOption,
    CapabilityResult,
    CapabilitySpec,
    EvidenceRef,
    ExternalWriteRequest,
    ExternalWriteResult,
    HumanClarificationRequest,
    RuntimePlan,
    SafetyPolicy,
    SchedulingPolicy,
    WorkflowGoal,
    WorkflowProfile,
)


class ToySummaryInput(BaseModel):
    text: str


class ToySummaryOutput(BaseModel):
    summary: str
    word_count: int


class CalendarTask(BaseModel):
    name: str
    hours: float
    priority: int = Field(ge=1, le=5)
    focus_project: str = ""
    energy: str = "medium"


class CalendarBuilderInput(BaseModel):
    available_hours: float
    energy_level: str
    focus_projects: list[str] = Field(default_factory=list)
    tasks: list[CalendarTask]


class ScheduledTask(BaseModel):
    name: str
    hours: float
    priority: int
    focus_project: str = ""


class CalendarPlan(BaseModel):
    scheduled: list[ScheduledTask] = Field(default_factory=list)
    deferred: list[str] = Field(default_factory=list)
    total_hours: float = 0
    rationale: str = ""


def build_toy_summary_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()

    def summarize(_context, payload: ToySummaryInput) -> ToySummaryOutput:
        words = payload.text.split()
        return ToySummaryOutput(summary=words[0] if words else "", word_count=len(words))

    registry.register(
        CapabilitySpec(
            name="toy_summary",
            kind="deterministic",
            description="Summarize text with a deterministic first-word toy implementation.",
            input_model=ToySummaryInput,
            output_model=ToySummaryOutput,
        ),
        summarize,
    )
    return registry


async def run_toy_summary(text: str) -> tuple[ToySummaryOutput, InMemoryTraceSink]:
    registry = build_toy_summary_registry()
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    goal = WorkflowGoal(workflow_type="toy_summary", objective="summarize arbitrary text")
    result = await runtime.invoke(
        "toy_summary",
        {"text": text},
        capability_context_for_goal(goal, workflow_id="toy-summary"),
    )
    return result.output, trace


def build_toy_calendar_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()

    def draft_calendar(_context, payload: CalendarBuilderInput) -> CalendarPlan:
        # Deliberately optimistic first pass: schedules all priority work so the evaluator can reject
        # overload and exercise retrace/repair in a product-neutral workload.
        ordered = sorted(payload.tasks, key=lambda task: (-task.priority, task.name))
        scheduled = [
            ScheduledTask(
                name=task.name,
                hours=task.hours,
                priority=task.priority,
                focus_project=task.focus_project,
            )
            for task in ordered
        ]
        return CalendarPlan(
            scheduled=scheduled,
            total_hours=sum(task.hours for task in scheduled),
            rationale="First pass schedules all candidate tasks by priority.",
        )

    def evaluate_calendar(_context, payload: CalendarPlan) -> CapabilityResult:
        available_hours = float(_context.plan.constraints["available_hours"])
        if payload.total_hours <= available_hours:
            return CapabilityResult(status="accepted", output=payload)
        return CapabilityResult(
            status="rejected",
            output=payload,
            error="calendar overload",
            metadata={
                "observed_hours": payload.total_hours,
                "available_hours": available_hours,
                "criticism": "Plan is overbooked; retrace to calendar repair and preserve high priority coverage.",
            },
        )

    def repair_calendar(context, payload: CalendarBuilderInput) -> CalendarPlan:
        available_hours = float(context.plan.constraints["available_hours"])
        scheduled: list[ScheduledTask] = []
        deferred: list[str] = []
        used = 0.0
        ordered = sorted(payload.tasks, key=lambda task: (-task.priority, task.hours, task.name))
        for task in ordered:
            if used + task.hours <= available_hours:
                scheduled.append(
                    ScheduledTask(
                        name=task.name,
                        hours=task.hours,
                        priority=task.priority,
                        focus_project=task.focus_project,
                    )
                )
                used += task.hours
            else:
                deferred.append(task.name)
        return CalendarPlan(
            scheduled=scheduled,
            deferred=deferred,
            total_hours=used,
            rationale="Repaired plan preserves highest priority work within available hours.",
        )

    registry.register(
        CapabilitySpec(
            name="draft_calendar",
            kind="deterministic",
            description="Create an initial schedule from tasks and availability.",
            input_model=CalendarBuilderInput,
            output_model=CalendarPlan,
        ),
        draft_calendar,
    )
    registry.register(
        CapabilitySpec(
            name="evaluate_calendar",
            kind="deterministic",
            description="Reject schedules that exceed available time.",
            input_model=CalendarPlan,
        ),
        evaluate_calendar,
    )
    registry.register(
        CapabilitySpec(
            name="repair_calendar",
            kind="deterministic",
            description="Retrace/replan an overloaded calendar while preserving priority coverage.",
            input_model=CalendarBuilderInput,
            output_model=CalendarPlan,
        ),
        repair_calendar,
    )
    return registry


async def run_toy_calendar_builder(payload: CalendarBuilderInput) -> tuple[CalendarPlan, InMemoryTraceSink, bool]:
    registry = build_toy_calendar_registry()
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    profile = WorkflowProfile(
        workflow_type="calendar_builder",
        profile_id="toy-calendar",
        constraints={
            "available_hours": payload.available_hours,
            "energy_level": payload.energy_level,
            "focus_projects": payload.focus_projects,
        },
        requested_capabilities=["draft_calendar", "evaluate_calendar", "repair_calendar"],
    )
    plan: RuntimePlan = RuntimePlanCompiler(
        supported_constraint_keys={"available_hours", "energy_level", "focus_projects"}
    ).compile(profile, registry)
    goal = WorkflowGoal(
        workflow_type="calendar_builder",
        objective="build a realistic calendar from availability, energy, priorities, tasks, and focus projects",
    )
    context = capability_context_for_goal(goal, plan=plan, workflow_id="toy-calendar")

    draft = await runtime.invoke("draft_calendar", payload, context)
    evaluation = await runtime.invoke("evaluate_calendar", draft.output, context)
    if evaluation.status == "accepted":
        return evaluation.output, trace, False
    repaired = await runtime.invoke("repair_calendar", payload, context)
    final_evaluation = await runtime.invoke("evaluate_calendar", repaired.output, context, attempt=2)
    if final_evaluation.status != "accepted":
        raise RuntimeError("toy calendar repair failed validation")
    return final_evaluation.output, trace, True


class SiteAuditInput(BaseModel):
    url: str
    rubric: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)


class SiteAuditPlan(BaseModel):
    scenarios: list[str]
    rationale: str = ""


class SiteScenarioInput(BaseModel):
    url: str
    scenario: str


class SiteFinding(BaseModel):
    title: str
    severity: str
    evidence_ref_id: str
    confidence: float = Field(ge=0, le=1)


class SiteScenarioResult(BaseModel):
    scenario: str
    findings: list[SiteFinding] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class SiteAuditEvidence(BaseModel):
    results: list[SiteScenarioResult] = Field(default_factory=list)


class AdjudicatedFindings(BaseModel):
    accepted_findings: list[SiteFinding] = Field(default_factory=list)
    rejected_titles: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class SiteAuditReport(BaseModel):
    url: str
    accepted_findings: list[SiteFinding] = Field(default_factory=list)
    rejected_titles: list[str] = Field(default_factory=list)
    write_status: str
    summary: str = ""


def build_toy_site_audit_registry() -> CapabilityRegistry:
    """Build a fake-backed site QA pilot without product-specific engine APIs."""

    registry = CapabilityRegistry()

    def plan_site_audit(_context, payload: SiteAuditInput) -> SiteAuditPlan:
        scenarios = payload.scenarios or ["navigation smoke", "critical form"]
        return SiteAuditPlan(
            scenarios=scenarios,
            rationale="Use supplied scenarios, otherwise a small smoke + form coverage set.",
        )

    def run_site_scenario(_context, payload: SiteScenarioInput) -> SiteScenarioResult:
        evidence = EvidenceRef(
            role="dom_snapshot",
            uri=f"fake-browser://{payload.url}/{payload.scenario.replace(' ', '-')}",
            media_type="text/html",
            summary=f"Snapshot for {payload.scenario}",
        )
        findings: list[SiteFinding] = []
        if "form" in payload.scenario or "checkout" in payload.scenario:
            findings.append(
                SiteFinding(
                    title="Primary form lacks visible error state",
                    severity="high",
                    evidence_ref_id=evidence.ref_id,
                    confidence=0.91,
                )
            )
        return SiteScenarioResult(
            scenario=payload.scenario,
            findings=findings,
            evidence_refs=[evidence],
        )

    def run_catalog_checks(_context, payload: SiteAuditInput) -> SiteScenarioResult:
        evidence = EvidenceRef(
            role="accessibility_snapshot",
            uri=f"fake-a11y://{payload.url}",
            media_type="application/json",
            summary="Accessibility rule output",
        )
        return SiteScenarioResult(
            scenario="catalog checks",
            findings=[
                SiteFinding(
                    title="Hero image has empty alt text",
                    severity="medium",
                    evidence_ref_id=evidence.ref_id,
                    confidence=0.84,
                )
            ],
            evidence_refs=[evidence],
        )

    def adjudicate_findings(_context, payload: SiteAuditEvidence) -> AdjudicatedFindings:
        evidence_by_id = {
            evidence.ref_id: evidence
            for result in payload.results
            for evidence in result.evidence_refs
        }
        accepted: list[SiteFinding] = []
        rejected: list[str] = []
        for result in payload.results:
            for finding in result.findings:
                if finding.confidence >= 0.75 and finding.evidence_ref_id in evidence_by_id:
                    accepted.append(finding)
                else:
                    rejected.append(finding.title)
        return AdjudicatedFindings(
            accepted_findings=accepted,
            rejected_titles=rejected,
            evidence_refs=list(evidence_by_id.values()),
        )

    registry.register(
        CapabilitySpec(
            name="plan_site_audit",
            kind="deterministic",
            input_model=SiteAuditInput,
            output_model=SiteAuditPlan,
        ),
        plan_site_audit,
    )
    registry.register(
        CapabilitySpec(
            name="run_site_scenario",
            kind="agent",
            input_model=SiteScenarioInput,
            output_model=SiteScenarioResult,
        ),
        run_site_scenario,
    )
    registry.register(
        CapabilitySpec(
            name="run_catalog_checks",
            kind="deterministic",
            input_model=SiteAuditInput,
            output_model=SiteScenarioResult,
        ),
        run_catalog_checks,
    )
    registry.register(
        CapabilitySpec(
            name="adjudicate_findings",
            kind="deterministic",
            input_model=SiteAuditEvidence,
            output_model=AdjudicatedFindings,
        ),
        adjudicate_findings,
    )
    registry.register(
        CapabilitySpec(
            name="write_audit_report",
            kind="external",
            input_model=ExternalWriteRequest,
            output_model=ExternalWriteResult,
            side_effects=["external_write"],
        ),
        ExternalAdapterCapability(InMemoryExternalWriteSink()),
    )
    return registry


async def run_toy_site_audit_pilot(payload: SiteAuditInput) -> tuple[SiteAuditReport, InMemoryTraceSink]:
    registry = build_toy_site_audit_registry()
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    profile = WorkflowProfile(
        workflow_type="site_audit",
        profile_id="toy-site-audit",
        constraints={"rubric": payload.rubric, "max_parallel": 2},
        requested_capabilities=[
            "plan_site_audit",
            "run_site_scenario",
            "run_catalog_checks",
            "adjudicate_findings",
            "write_audit_report",
        ],
        safety=SafetyPolicy(allowed_side_effects=["external_write"]),
    )
    plan = RuntimePlanCompiler(
        supported_constraint_keys={"rubric", "max_parallel"}
    ).compile(profile, registry)
    goal = WorkflowGoal(
        workflow_type="site_audit",
        objective="QA this website against a compact rubric and grounded evidence.",
        delivery_target="report",
    )
    context = capability_context_for_goal(goal, plan=plan, workflow_id="toy-site-audit")

    audit_plan = await runtime.invoke("plan_site_audit", payload, context)
    scenario_results = await gather_capabilities(
        runtime,
        [
            CapabilityCall(
                "run_site_scenario",
                SiteScenarioInput(url=payload.url, scenario=scenario),
            )
            for scenario in audit_plan.output.scenarios
        ],
        context,
        max_parallel=2,
    )
    catalog_result = await runtime.invoke("run_catalog_checks", payload, context)
    evidence = SiteAuditEvidence(
        results=[
            result.output
            for result in [*scenario_results, catalog_result]
            if result.status == "accepted" and result.output is not None
        ]
    )
    adjudicated = await runtime.invoke("adjudicate_findings", evidence, context)
    write = await runtime.invoke(
        "write_audit_report",
        ExternalWriteRequest(
            target="site_audit_report_store",
            operation="upsert_report",
            idempotency_key=payload.url,
            payload={
                "url": payload.url,
                "finding_count": len(adjudicated.output.accepted_findings),
            },
            evidence_refs=adjudicated.output.evidence_refs,
            privacy_level="confidential",
        ),
        context,
    )
    return (
        SiteAuditReport(
            url=payload.url,
            accepted_findings=adjudicated.output.accepted_findings,
            rejected_titles=adjudicated.output.rejected_titles,
            write_status=write.output.status,
            summary=f"{len(adjudicated.output.accepted_findings)} grounded finding(s)",
        ),
        trace,
    )


class InventoryPilotInput(BaseModel):
    location_hint: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class InventoryEvidenceBundle(BaseModel):
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class InventoryExtractionInput(BaseModel):
    location: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class InventoryObservation(BaseModel):
    item: str
    location: str
    confidence: float = Field(ge=0, le=1)
    evidence_ref_ids: list[str] = Field(default_factory=list)


class InventoryObservationSet(BaseModel):
    observations: list[InventoryObservation] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class InventoryPilotResult(BaseModel):
    observations: list[InventoryObservation] = Field(default_factory=list)
    write_status: str
    scheduling_actions: list[str] = Field(default_factory=list)
    used_provisional_location: bool = False


def build_toy_inventory_registry() -> tuple[CapabilityRegistry, InMemoryHumanClarificationChannel]:
    """Build a fake-backed inventory pilot for evidence/session pressure testing."""

    registry = CapabilityRegistry()
    clarification_channel = InMemoryHumanClarificationChannel()

    def select_inventory_evidence(_context, payload: InventoryPilotInput) -> InventoryEvidenceBundle:
        return InventoryEvidenceBundle(evidence_refs=payload.evidence_refs)

    def extract_inventory_items(_context, payload: InventoryExtractionInput) -> InventoryObservationSet:
        observations: list[InventoryObservation] = []
        for evidence in payload.evidence_refs:
            if evidence.role == "contents":
                observations.append(
                    InventoryObservation(
                        item="glue",
                        location=payload.location,
                        confidence=0.88,
                        evidence_ref_ids=[evidence.ref_id],
                    )
                )
                observations.append(
                    InventoryObservation(
                        item="USB-C cable",
                        location=payload.location,
                        confidence=0.81,
                        evidence_ref_ids=[evidence.ref_id],
                    )
                )
        return InventoryObservationSet(
            observations=observations,
            evidence_refs=payload.evidence_refs,
        )

    def dedupe_inventory_observations(_context, payload: InventoryObservationSet) -> InventoryObservationSet:
        by_key: dict[tuple[str, str], InventoryObservation] = {}
        for observation in payload.observations:
            key = (observation.item.lower(), observation.location.lower())
            existing = by_key.get(key)
            if not existing or observation.confidence > existing.confidence:
                by_key[key] = observation
        return InventoryObservationSet(
            observations=list(by_key.values()),
            evidence_refs=payload.evidence_refs,
        )

    registry.register(
        CapabilitySpec(
            name="select_inventory_evidence",
            kind="deterministic",
            input_model=InventoryPilotInput,
            output_model=InventoryEvidenceBundle,
        ),
        select_inventory_evidence,
    )
    ask_location = HumanClarificationCapability(
        clarification_channel,
        name="ask_location_clarification",
    )
    registry.register(ask_location.spec, ask_location)
    registry.register(
        CapabilitySpec(
            name="extract_inventory_items",
            kind="llm",
            input_model=InventoryExtractionInput,
            output_model=InventoryObservationSet,
        ),
        extract_inventory_items,
    )
    registry.register(
        CapabilitySpec(
            name="dedupe_inventory_observations",
            kind="deterministic",
            input_model=InventoryObservationSet,
            output_model=InventoryObservationSet,
        ),
        dedupe_inventory_observations,
    )
    registry.register(
        CapabilitySpec(
            name="write_inventory_observations",
            kind="external",
            input_model=ExternalWriteRequest,
            output_model=ExternalWriteResult,
            side_effects=["external_write"],
        ),
        ExternalAdapterCapability(InMemoryExternalWriteSink()),
    )
    return registry, clarification_channel


async def run_toy_inventory_pilot(payload: InventoryPilotInput) -> tuple[InventoryPilotResult, InMemoryTraceSink]:
    registry, _clarification_channel = build_toy_inventory_registry()
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    profile = WorkflowProfile(
        workflow_type="inventory_observation",
        profile_id="toy-inventory",
        constraints={"evidence_strategy": "refs_only"},
        requested_capabilities=[
            "select_inventory_evidence",
            "ask_location_clarification",
            "extract_inventory_items",
            "dedupe_inventory_observations",
            "write_inventory_observations",
        ],
        safety=SafetyPolicy(allowed_side_effects=["notification", "external_write"]),
        scheduling=SchedulingPolicy(mode="run_latest", stale_after_s=5),
    )
    plan = RuntimePlanCompiler(
        supported_constraint_keys={"evidence_strategy"}
    ).compile(profile, registry)
    goal = WorkflowGoal(
        workflow_type="inventory_observation",
        objective="Extract compact item/location observations from frame evidence.",
        delivery_target="inventory_index",
    )
    context = capability_context_for_goal(goal, plan=plan, workflow_id="toy-inventory")

    scheduler = WorkflowScheduler(clock=lambda: 100.0)
    stale = scheduler.submit(
        key="camera-1",
        run_id="old-frame",
        payload={},
        policy=SchedulingPolicy(mode="drop_stale", stale_after_s=5),
        created_at=90.0,
    )
    accepted = scheduler.submit(
        key="camera-1",
        run_id="latest-frame",
        payload={},
        policy=SchedulingPolicy(mode="run_latest"),
    )

    evidence = await runtime.invoke("select_inventory_evidence", payload, context)
    used_provisional_location = False
    location = payload.location_hint
    if not location:
        clarification = await runtime.invoke(
            "ask_location_clarification",
            HumanClarificationRequest(
                question="Where is this cabinet or drawer?",
                options=[
                    ClarificationOption(
                        label="Living room closet",
                        value="living room closet near table",
                    )
                ],
                allow_free_text=True,
                default_value="unknown location",
                continue_without_answer=True,
                timeout_s=0,
            ),
            context,
        )
        location = clarification.output.value or "unknown location"
        used_provisional_location = clarification.output.provisional

    extracted = await runtime.invoke(
        "extract_inventory_items",
        InventoryExtractionInput(location=location, evidence_refs=evidence.output.evidence_refs),
        context,
    )
    deduped = await runtime.invoke("dedupe_inventory_observations", extracted.output, context)
    write = await runtime.invoke(
        "write_inventory_observations",
        ExternalWriteRequest(
            target="inventory_index",
            operation="upsert_observations",
            idempotency_key=f"inventory:{location}",
            payload={
                "location": location,
                "items": [observation.item for observation in deduped.output.observations],
            },
            evidence_refs=deduped.output.evidence_refs,
            privacy_level="confidential",
        ),
        context,
    )
    return (
        InventoryPilotResult(
            observations=deduped.output.observations,
            write_status=write.output.status,
            scheduling_actions=[stale.action, accepted.action],
            used_provisional_location=used_provisional_location,
        ),
        trace,
    )
