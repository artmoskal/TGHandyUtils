"""Runnable, product-neutral example workflows for the executable engine.

Every example runs through ``WorkflowEngine.run``. There are NO product-owned orchestration loops
here: branching, retries, fan-out, scheduling, and fallback are all engine-owned. Each product
packages its domain capabilities + workflow shape as a :class:`WorkflowPack`; the engine owns
execution. One shared ``WorkflowExecutor`` runs all of them (see :func:`build_demo_engine`).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_workflow_engine import (
    AgentCapability,
    AgentRunRequest,
    AgentRunResult,
    AgentToolStep,
    AsyncQueueTraceSink,
    BranchDecision,
    ExternalAdapterCapability,
    Fallback,
    InMemoryExternalWriteSink,
    InMemoryTraceSink,
    LLMAgentPlanner,
    LLMRequest,
    LLMResponse,
    PlanArtifact,
    PlanTask,
    ReplayPlanner,
    Retrace,
    StructuredLLMNode,
    TeeTraceSink,
    ToolCallRequest,
    ToolSpec,
    WEAK_MODEL_CLEANER,
    WorkflowBuilder,
    WorkflowEngine,
    WorkflowEngineBuilder,
    WorkflowRunResult,
    format_trace_events,
)
from ai_workflow_engine.models import (
    CapabilityResult,
    CapabilitySpec,
    EvidenceRef,
    ExternalWriteRequest,
    ExternalWriteResult,
    SafetyPolicy,
    WorkflowTraceEvent,
    WorkflowGoal,
    WorkflowProfile,
)


# ======================================================================================
# Toy 1 — text summary (single deterministic step)
# ======================================================================================


class ToySummaryInput(BaseModel):
    text: str


class ToySummaryOutput(BaseModel):
    summary: str
    word_count: int


def _summarize(_context, payload: ToySummaryInput) -> ToySummaryOutput:
    words = payload.text.split()
    return ToySummaryOutput(summary=words[0] if words else "", word_count=len(words))


class SummaryPack:
    def register(self, builder) -> None:
        builder.register_capability(
            "toy_summary", _summarize, kind="deterministic",
            input_model=ToySummaryInput, output_model=ToySummaryOutput,
        )
        builder.register_workflow(WorkflowBuilder("toy_summary").step("toy_summary").build())


async def run_toy_summary(text: str):
    engine = WorkflowEngineBuilder().register_pack(SummaryPack()).build()
    result = await engine.run("toy_summary", ToySummaryInput(text=text))
    return result.output, engine.trace_sink


# ======================================================================================
# Toy 2 — calendar builder (step -> evaluator gate -> fallback repair)
# ======================================================================================


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


class CalendarWork(BaseModel):
    """Carries the original request alongside the plan so the engine can repair from source."""

    request: CalendarBuilderInput
    plan: CalendarPlan


def _draft_calendar(_context, request: CalendarBuilderInput) -> CalendarWork:
    ordered = sorted(request.tasks, key=lambda task: (-task.priority, task.name))
    scheduled = [
        ScheduledTask(name=t.name, hours=t.hours, priority=t.priority, focus_project=t.focus_project)
        for t in ordered
    ]
    plan = CalendarPlan(
        scheduled=scheduled,
        total_hours=sum(t.hours for t in scheduled),
        rationale="First pass schedules all candidate tasks by priority.",
    )
    return CalendarWork(request=request, plan=plan)


def _evaluate_calendar(context, work: CalendarWork) -> CapabilityResult:
    available = float(context.plan.constraints["available_hours"])
    if work.plan.total_hours <= available:
        return CapabilityResult(status="accepted", output=work)
    return CapabilityResult(
        status="rejected",
        output=work,
        error="calendar overload",
        metadata={"criticism": "Plan is overbooked; repair within available hours, keep high priority."},
    )


def _repair_calendar(context, work: CalendarWork) -> CalendarWork:
    available = float(context.plan.constraints["available_hours"])
    scheduled: list[ScheduledTask] = []
    deferred: list[str] = []
    used = 0.0
    for task in sorted(work.request.tasks, key=lambda t: (-t.priority, t.hours, t.name)):
        if used + task.hours <= available:
            scheduled.append(
                ScheduledTask(name=task.name, hours=task.hours, priority=task.priority, focus_project=task.focus_project)
            )
            used += task.hours
        else:
            deferred.append(task.name)
    plan = CalendarPlan(
        scheduled=scheduled, deferred=deferred, total_hours=used,
        rationale="Repaired plan preserves highest priority work within available hours.",
    )
    return CalendarWork(request=work.request, plan=plan)


class CalendarPack:
    def register(self, builder) -> None:
        builder.register_capability(
            "draft_calendar", _draft_calendar, kind="deterministic",
            input_model=CalendarBuilderInput, output_model=CalendarWork,
        )
        builder.register_capability("evaluate_calendar", _evaluate_calendar, kind="deterministic")
        builder.register_capability(
            "repair_calendar", _repair_calendar, kind="deterministic",
            input_model=CalendarWork, output_model=CalendarWork,
        )
        builder.register_workflow(
            WorkflowBuilder("calendar_builder")
            .step("draft_calendar")
            .evaluate("evaluate_calendar", on_reject=Fallback("repair_calendar"))
            .build()
        )


async def run_toy_calendar_builder(payload: CalendarBuilderInput):
    engine = WorkflowEngineBuilder().register_pack(CalendarPack()).build()
    result = await engine.run(
        "calendar_builder",
        payload,
        constraints={
            "available_hours": payload.available_hours,
            "energy_level": payload.energy_level,
            "focus_projects": payload.focus_projects,
        },
    )
    work: CalendarWork = result.output
    gate = result.node("evaluate_calendar")
    retraced = bool(gate and gate.fallback_reason)
    return work.plan, engine.trace_sink, retraced


# ======================================================================================
# Toy 3 — site audit (plan -> fan-out scenarios -> adjudicate + external write)
# ======================================================================================


class SiteAuditInput(BaseModel):
    url: str
    rubric: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)


class SiteScenarioInput(BaseModel):
    url: str
    scenario: str


class SiteFinding(BaseModel):
    title: str
    severity: str
    evidence_ref_id: str
    confidence: float = Field(ge=0, le=1)


class SiteScenarioResult(BaseModel):
    url: str
    scenario: str
    findings: list[SiteFinding] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class SiteAuditReport(BaseModel):
    url: str
    accepted_findings: list[SiteFinding] = Field(default_factory=list)
    rejected_titles: list[str] = Field(default_factory=list)
    write_status: str
    summary: str = ""


def _plan_site_audit(_context, payload: SiteAuditInput) -> list:
    scenarios = payload.scenarios or ["navigation smoke", "critical form"]
    inputs = [SiteScenarioInput(url=payload.url, scenario=s) for s in scenarios]
    inputs.append(SiteScenarioInput(url=payload.url, scenario="catalog checks"))
    return inputs


def _run_site_scenario(_context, payload: SiteScenarioInput) -> SiteScenarioResult:
    if payload.scenario == "catalog checks":
        evidence = EvidenceRef(
            role="accessibility_snapshot", uri=f"fake-a11y://{payload.url}",
            media_type="application/json", summary="Accessibility rule output",
        )
        return SiteScenarioResult(
            url=payload.url, scenario=payload.scenario,
            findings=[SiteFinding(title="Hero image has empty alt text", severity="medium",
                                  evidence_ref_id=evidence.ref_id, confidence=0.84)],
            evidence_refs=[evidence],
        )
    evidence = EvidenceRef(
        role="dom_snapshot", uri=f"fake-browser://{payload.url}/{payload.scenario.replace(' ', '-')}",
        media_type="text/html", summary=f"Snapshot for {payload.scenario}",
    )
    findings: list[SiteFinding] = []
    if "form" in payload.scenario or "checkout" in payload.scenario:
        findings.append(SiteFinding(title="Primary form lacks visible error state", severity="high",
                                    evidence_ref_id=evidence.ref_id, confidence=0.91))
    return SiteScenarioResult(url=payload.url, scenario=payload.scenario, findings=findings, evidence_refs=[evidence])


def _write_audit_report(_context, results: list) -> SiteAuditReport:
    # Adjudicate grounded findings, then perform the external write (engine-gated side effect).
    url = results[0].url if results else ""
    evidence_ids = {ev.ref_id for r in results for ev in r.evidence_refs}
    accepted: list[SiteFinding] = []
    rejected: list[str] = []
    for r in results:
        for f in r.findings:
            if f.confidence >= 0.75 and f.evidence_ref_id in evidence_ids:
                accepted.append(f)
            else:
                rejected.append(f.title)
    sink = InMemoryExternalWriteSink()
    write = sink.write(ExternalWriteRequest(
        target="site_audit_report_store", operation="upsert_report", idempotency_key=url,
        payload={"url": url, "finding_count": len(accepted)},
        evidence_refs=[ev for r in results for ev in r.evidence_refs], privacy_level="confidential",
    ))
    return SiteAuditReport(
        url=url, accepted_findings=accepted, rejected_titles=rejected,
        write_status=write.status, summary=f"{len(accepted)} grounded finding(s)",
    )


class SiteAuditPack:
    def register(self, builder) -> None:
        builder.register_capability("plan_site_audit", _plan_site_audit, kind="deterministic", input_model=SiteAuditInput)
        builder.register_capability("run_site_scenario", _run_site_scenario, kind="agent", input_model=SiteScenarioInput, output_model=SiteScenarioResult)
        builder.register_capability("write_audit_report", _write_audit_report, kind="external", side_effects=["external_write"], output_model=SiteAuditReport)
        builder.register_workflow(
            WorkflowBuilder("site_audit")
            .step("plan_site_audit")
            .fanout("run_scenarios", capability="run_site_scenario", items_key="plan_site_audit", max_parallel=2)
            .step("write_audit_report")
            .build(),
            profile=WorkflowProfile(workflow_type="site_audit", safety=SafetyPolicy(allowed_side_effects=["external_write"])),
        )


async def run_toy_site_audit_pilot(payload: SiteAuditInput):
    engine = WorkflowEngineBuilder().register_pack(SiteAuditPack()).build()
    result = await engine.run("site_audit", payload)
    return result.output, engine.trace_sink


# ======================================================================================
# Toy 3b — three-axis site audit proof (planner fanout + CLI agent + replay)
# ======================================================================================


class ThreeAxisSiteAuditInput(BaseModel):
    url: str
    workspace_dir: str
    input_assets: list[EvidenceRef] = Field(default_factory=list)
    coordinator_scenarios: list[str] = Field(default_factory=lambda: ["subscription visual pass"])


class ThreeAxisScenarioInput(BaseModel):
    axis: Literal["rigid", "semi"]
    url: str
    scenario: str


class ThreeAxisScenarioResult(BaseModel):
    axis: str
    scenario: str
    findings: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    new_artifact_count: int = 0


class ThreeAxisCoordinatorPlan(BaseModel):
    scenarios: list[str] = Field(default_factory=list)


class ThreeAxisPilotReport(BaseModel):
    summary: str
    accepted_axes: list[str] = Field(default_factory=list)
    flexible_new_artifact_count: int = 0


@dataclass(frozen=True)
class ThreeAxisPilotOutcome:
    result: WorkflowRunResult
    replay_result: WorkflowRunResult
    trace_events: list[WorkflowTraceEvent]
    live_events: list[WorkflowTraceEvent]
    replay_trace_events: list[WorkflowTraceEvent]
    trace_dump: str


class _ScriptedAgentLLM:
    async def __call__(self, request: LLMRequest) -> LLMResponse:
        has_tool_result = any(message.tool_results for message in request.messages)
        if not has_tool_result:
            return LLMResponse(
                text="collect scenario evidence",
                tool_calls=[
                    ToolCallRequest(
                        call_id="semi-1",
                        name="collect_semirigid_evidence",
                        arguments={"url": "https://example.test", "scenario": "guided form"},
                    )
                ],
                model="fake-semi-agent",
                input_tokens=9,
                output_tokens=3,
                total_tokens=12,
                estimated_usd=0.001,
            )
        return LLMResponse(
            text=json.dumps(
                {
                    "axis": "semi",
                    "scenario": "guided form",
                    "findings": ["Guided form state was checked by the scoped agent"],
                    "evidence_refs": [
                        {
                            "role": "agent_note",
                            "uri": "memory://semi/guided-form",
                            "media_type": "application/json",
                            "summary": "Scoped tool result from the semi-rigid episode",
                        }
                    ],
                    "new_artifact_count": 0,
                },
                sort_keys=True,
            ),
            model="fake-semi-agent",
            input_tokens=13,
            output_tokens=8,
            total_tokens=21,
            estimated_usd=0.0015,
        )


class _FakeCoordinatorLLM:
    def __init__(self, scenarios: list[str]) -> None:
        self.scenarios = scenarios

    async def __call__(self, _request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text=ThreeAxisCoordinatorPlan(scenarios=list(self.scenarios)).model_dump_json(),
            model="fake-coordinator",
            input_tokens=8,
            output_tokens=4,
            total_tokens=12,
            estimated_usd=0.0008,
        )


def _coerce_three_axis_payload(payload: Any) -> tuple[ThreeAxisSiteAuditInput, bool]:
    if isinstance(payload, ThreeAxisSiteAuditInput):
        return payload, False
    if isinstance(payload, dict):
        return ThreeAxisSiteAuditInput.model_validate(payload), "_criticism" in payload
    return ThreeAxisSiteAuditInput.model_validate(payload), False


async def _compose_three_axis_plan(context, payload: Any) -> PlanArtifact:
    request, retraced = _coerce_three_axis_payload(payload)
    base = list(context.plan.constraints.get("base_scenarios", [])) if context.plan else []
    rigid_scenario = base[0] if base else "deterministic smoke"
    semi_scenario = base[1] if len(base) > 1 else "guided form"
    coordinator = StructuredLLMNode(
        name="three_axis_coordinator",
        config=object(),
        output_model=ThreeAxisCoordinatorPlan,
        prompt_template="Choose site-audit scenarios for {url} from {requested}",
        input_variables=["url", "requested"],
        llm=_FakeCoordinatorLLM(request.coordinator_scenarios),
        max_repair_rounds=0,
    )
    coordinator_plan = await coordinator.run(
        {
            "url": request.url,
            "requested": json.dumps(request.coordinator_scenarios, sort_keys=True),
        },
        usage_metadata={"set_composition": "semi_rigid"},
    )
    coordinator_scenario = (coordinator_plan.scenarios or request.coordinator_scenarios)[0]
    flexible_workspace = Path(request.workspace_dir) / ("flexible-retrace" if retraced else "flexible-first")
    input_assets = request.input_assets or [
        EvidenceRef(
            role="seed",
            uri="memory://three-axis/seed",
            media_type="text/plain",
            summary="Seed evidence staged into the CLI workspace",
        )
    ]
    return PlanArtifact(
        goal=f"Audit {request.url} across rigid, semi-rigid, and flexible execution",
        tasks=[
            PlanTask(
                task_id="rigid_axis",
                description=rigid_scenario,
                capability="run_rigid_axis",
                payload=ThreeAxisScenarioInput(
                    axis="rigid",
                    url=request.url,
                    scenario=rigid_scenario,
                ).model_dump(),
            ),
            PlanTask(
                task_id="semi_axis",
                description=semi_scenario,
                capability="run_semirigid_axis",
                payload=_semirigid_agent_request(request.url, semi_scenario).model_dump(),
            ),
            PlanTask(
                task_id="flexible_axis",
                description=coordinator_scenario,
                capability="run_flexible_cli_axis",
                payload={
                    "prompt": f"Inspect {request.url} for {coordinator_scenario}",
                    "workspace_dir": str(flexible_workspace),
                    "input_assets": [asset.model_dump() for asset in input_assets],
                    "salvage_globs": ["*.png"],
                    "expect_json_result": True,
                    "subscription_mode": True,
                },
            ),
        ],
        metadata={
            "set_composition": "semi_rigid",
            "base_scenarios": base,
            "coordinator_source": "fake_coordinator_llm",
            "coordinator_scenarios": list(request.coordinator_scenarios),
            "retraced": retraced,
        },
    )


def _semirigid_agent_request(url: str, scenario: str) -> AgentRunRequest:
    return AgentRunRequest(
        prompt=f"Run the semi-rigid scenario '{scenario}' on {url}",
        allowed_tools=["collect_semirigid_evidence"],
        max_steps=3,
        max_tool_calls=1,
        subscription_mode=False,
        metadata={"axis": "semi", "scenario": scenario},
    )


def _run_rigid_axis(_context, payload: ThreeAxisScenarioInput) -> ThreeAxisScenarioResult:
    evidence = EvidenceRef(
        role="static_rule",
        uri=f"memory://rigid/{payload.url}",
        media_type="application/json",
        summary="Deterministic rule evidence",
    )
    return ThreeAxisScenarioResult(
        axis=payload.axis,
        scenario=payload.scenario,
        findings=["Static smoke check passed with grounded evidence"],
        evidence_refs=[evidence],
    )


def _collect_semirigid_evidence(_context, payload: dict[str, Any]) -> dict[str, Any]:
    evidence = EvidenceRef(
        role="agent_note",
        uri=f"memory://semi/{payload.get('scenario', 'guided-form')}",
        media_type="application/json",
        summary="Scoped semirigid tool output",
    )
    return {
        "finding": "Guided form state was checked by the scoped agent",
        "evidence_ref": evidence.model_dump(),
    }


def _task_outputs(plan: PlanArtifact) -> dict[str, Any]:
    outputs = plan.metadata.get("task_outputs")
    return outputs if isinstance(outputs, dict) else {}


def _flexible_output(plan: PlanArtifact) -> Any:
    return _task_outputs(plan).get("scenario_plan.flexible_axis")


def _semirigid_output(plan: PlanArtifact) -> AgentRunResult:
    output = _task_outputs(plan).get("scenario_plan.semi_axis")
    if isinstance(output, AgentRunResult):
        return output
    return AgentRunResult.model_validate(output)


def _evaluate_three_axis_plan(_context, plan: PlanArtifact) -> CapabilityResult:
    flexible = _flexible_output(plan)
    new_count = getattr(flexible, "new_artifact_count", 0)
    if not plan.metadata.get("retraced"):
        return CapabilityResult(
            status="rejected",
            output=plan,
            metadata={"criticism": "run the artifact gate once after retrace"},
        )
    if new_count < 1:
        return CapabilityResult(
            status="rejected",
            output=plan,
            metadata={"criticism": "flexible CLI scenario produced no new grounded artifact"},
        )
    return CapabilityResult(status="accepted", output=plan)


async def _write_three_axis_report(context, plan: PlanArtifact) -> ThreeAxisPilotReport:
    from ai_workflow_tools.cli_agents import ConsoleLLMClient, claude_p

    fake_cli_path = Path(context.run_context.metadata["fake_cli_path"])
    flavor = claude_p.model_copy(update={"base_argv": [sys.executable, str(fake_cli_path)]})
    node = StructuredLLMNode(
        name="three_axis_report",
        config=object(),
        output_model=ThreeAxisPilotReport,
        prompt_template="Report on this three-axis audit: {summary}",
        input_variables=["summary"],
        llm=ConsoleLLMClient(flavor),
        pre_parse=WEAK_MODEL_CLEANER,
    )
    return await node.run({"summary": _three_axis_report_summary(plan)})


def _three_axis_report_summary(plan: PlanArtifact) -> str:
    outputs = _task_outputs(plan)
    flexible = _flexible_output(plan)
    new_count = getattr(flexible, "new_artifact_count", 0)
    return json.dumps(
        {
            "tasks": list(outputs),
            "retraced": plan.metadata.get("retraced"),
            "flexible_new_artifact_count": new_count,
        },
        sort_keys=True,
    )


class ThreeAxisSiteAuditPack:
    def __init__(
        self,
        *,
        fake_cli_path: str,
        trace_sink,
        asset_loader,
    ) -> None:
        self.fake_cli_path = fake_cli_path
        self.trace_sink = trace_sink
        self.asset_loader = asset_loader

    def register(self, builder) -> None:
        from ai_workflow_engine.engine.capabilities import CapabilityRegistry, CapabilityRuntime
        from ai_workflow_tools.cli_agents import CliAgentCapability, claude_p

        tool_registry = CapabilityRegistry()
        tool_registry.register(
            CapabilitySpec(name="collect_semirigid_evidence", kind="deterministic"),
            _collect_semirigid_evidence,
        )
        semirigid_agent = AgentCapability(
            LLMAgentPlanner(
                _ScriptedAgentLLM(),
                tool_specs={
                    "collect_semirigid_evidence": ToolSpec(
                        name="collect_semirigid_evidence",
                        description="Collect one scoped scenario evidence record.",
                        input_schema={"type": "object"},
                    )
                },
                output_model=ThreeAxisScenarioResult,
                node_name="semirigid_agent_planner",
            ),
            CapabilityRuntime(tool_registry, self.trace_sink),
            name="run_semirigid_axis",
        )
        fake_flavor = claude_p.model_copy(
            update={"base_argv": [sys.executable, str(Path(self.fake_cli_path))]}
        )
        flexible_agent = CliAgentCapability(
            fake_flavor,
            name="run_flexible_cli_axis",
            side_effects=["workspace_write"],
            asset_loader=self.asset_loader,
            trace_sink=self.trace_sink,
        )
        builder.register_capability("compose_three_axis_plan", _compose_three_axis_plan, kind="deterministic")
        builder.register_capability(
            "run_rigid_axis",
            _run_rigid_axis,
            kind="deterministic",
            input_model=ThreeAxisScenarioInput,
            output_model=ThreeAxisScenarioResult,
        )
        builder.register_capability_spec(semirigid_agent.spec, semirigid_agent)
        builder.register_capability_spec(flexible_agent.spec, flexible_agent)
        builder.register_capability("evaluate_three_axis_plan", _evaluate_three_axis_plan, kind="deterministic")
        builder.register_capability(
            "write_three_axis_report",
            _write_three_axis_report,
            kind="llm",
            input_model=PlanArtifact,
            output_model=ThreeAxisPilotReport,
        )
        builder.register_workflow(
            WorkflowBuilder("three_axis_site_audit")
            .plan("scenario_plan", capability="compose_three_axis_plan", execution="fanout", max_tasks=3)
            .evaluate(
                "adjudicate_three_axis",
                target="compose_three_axis_plan",
                evaluator="evaluate_three_axis_plan",
                on_reject=Retrace("scenario_plan"),
            )
            .step("write_three_axis_report")
            .build(),
            profile=WorkflowProfile(
                workflow_type="three_axis_site_audit",
                constraints={"base_scenarios": ["deterministic smoke", "guided form"]},
                safety=SafetyPolicy(allowed_side_effects=["workspace_write"]),
            ),
        )


class ThreeAxisReplayPack:
    def __init__(self, recorded_steps: list[AgentToolStep], final_output: Any) -> None:
        self.recorded_steps = recorded_steps
        self.final_output = final_output

    def register(self, builder) -> None:
        from ai_workflow_engine.engine.capabilities import CapabilityRegistry, CapabilityRuntime

        replay_registry = CapabilityRegistry()
        replay_registry.register(
            CapabilitySpec(name="collect_semirigid_evidence", kind="deterministic"),
            _collect_semirigid_evidence,
        )
        replay_agent = AgentCapability(
            ReplayPlanner(self.recorded_steps, final_output=self.final_output),
            CapabilityRuntime(replay_registry),
            name="run_semirigid_replay",
        )
        builder.register_capability_spec(replay_agent.spec, replay_agent)
        builder.register_workflow(
            WorkflowBuilder("three_axis_agent_replay")
            .step("run_semirigid_replay")
            .build()
        )


async def run_toy_three_axis_site_audit_pilot(
    payload: ThreeAxisSiteAuditInput,
    *,
    fake_cli_path: str,
) -> ThreeAxisPilotOutcome:
    memory_sink = InMemoryTraceSink()
    live_sink = AsyncQueueTraceSink(maxsize=200)
    tee_sink = TeeTraceSink(memory_sink, live_sink)

    def asset_loader(ref: EvidenceRef) -> bytes:
        return f"{ref.role}:{ref.uri}".encode("utf-8")

    engine = (
        WorkflowEngineBuilder()
        .with_trace_sink(tee_sink)
        .register_pack(
            ThreeAxisSiteAuditPack(
                fake_cli_path=fake_cli_path,
                trace_sink=tee_sink,
                asset_loader=asset_loader,
            )
        )
        .build()
    )
    engine.runtime.trace_sink = tee_sink
    engine.executor.runtime.trace_sink = tee_sink
    result = await engine.run(
        "three_axis_site_audit",
        payload,
        goal=WorkflowGoal(
            workflow_type="three_axis_site_audit",
            objective=f"Audit {payload.url}",
            metadata={"fake_cli_path": fake_cli_path},
        ),
    )
    plan = result.node("scenario_plan").output
    semirigid = _semirigid_output(plan)
    replay_memory = InMemoryTraceSink()
    replay_engine = (
        WorkflowEngineBuilder()
        .with_trace_sink(replay_memory)
        .register_pack(ThreeAxisReplayPack(semirigid.steps, semirigid.output))
        .build()
    )
    replay_result = await replay_engine.run(
        "three_axis_agent_replay",
        _semirigid_agent_request(payload.url, "guided form"),
    )
    live_events = [live_sink.queue.get_nowait() for _ in range(live_sink.queue.qsize())]
    return ThreeAxisPilotOutcome(
        result=result,
        replay_result=replay_result,
        trace_events=list(memory_sink.events),
        live_events=live_events,
        replay_trace_events=list(replay_memory.events),
        trace_dump=format_trace_events(memory_sink.events, usage=result.usage, title="Three-axis pilot trace"),
    )


# ======================================================================================
# Toy 4 — inventory (evidence refs -> extract -> dedupe -> external write)
# ======================================================================================


class InventoryPilotInput(BaseModel):
    location_hint: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class InventoryObservation(BaseModel):
    item: str
    location: str
    confidence: float = Field(ge=0, le=1)
    evidence_ref_ids: list[str] = Field(default_factory=list)


class InventoryWork(BaseModel):
    location: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    observations: list[InventoryObservation] = Field(default_factory=list)


class InventoryPilotResult(BaseModel):
    observations: list[InventoryObservation] = Field(default_factory=list)
    write_status: str
    used_provisional_location: bool = False


def _select_inventory_evidence(_context, payload: InventoryPilotInput) -> InventoryWork:
    return InventoryWork(location=payload.location_hint or "unknown location", evidence_refs=payload.evidence_refs)


def _extract_inventory_items(_context, work: InventoryWork) -> InventoryWork:
    observations: list[InventoryObservation] = []
    for ev in work.evidence_refs:
        if ev.role == "contents":
            observations.append(InventoryObservation(item="glue", location=work.location, confidence=0.88, evidence_ref_ids=[ev.ref_id]))
            observations.append(InventoryObservation(item="USB-C cable", location=work.location, confidence=0.81, evidence_ref_ids=[ev.ref_id]))
    return work.model_copy(update={"observations": observations})


def _dedupe_inventory_observations(_context, work: InventoryWork) -> InventoryWork:
    by_key: dict[tuple[str, str], InventoryObservation] = {}
    for obs in work.observations:
        key = (obs.item.lower(), obs.location.lower())
        if key not in by_key or obs.confidence > by_key[key].confidence:
            by_key[key] = obs
    return work.model_copy(update={"observations": list(by_key.values())})


def _build_inventory_write(_context, work: InventoryWork) -> ExternalWriteRequest:
    return ExternalWriteRequest(
        target="inventory_index", operation="upsert_observations", idempotency_key=f"inventory:{work.location}",
        payload={"location": work.location, "items": [o.item for o in work.observations]},
        evidence_refs=work.evidence_refs, privacy_level="confidential",
    )


class InventoryPack:
    def register(self, builder) -> None:
        builder.register_capability("select_inventory_evidence", _select_inventory_evidence, kind="deterministic", input_model=InventoryPilotInput, output_model=InventoryWork)
        builder.register_capability("extract_inventory_items", _extract_inventory_items, kind="llm", input_model=InventoryWork, output_model=InventoryWork)
        builder.register_capability("dedupe_inventory_observations", _dedupe_inventory_observations, kind="deterministic", input_model=InventoryWork, output_model=InventoryWork)
        builder.register_capability("build_inventory_write", _build_inventory_write, kind="deterministic", input_model=InventoryWork, output_model=ExternalWriteRequest)
        builder.register_capability("write_inventory_observations", ExternalAdapterCapability(InMemoryExternalWriteSink()), spec=None, kind="external", input_model=ExternalWriteRequest, output_model=ExternalWriteResult, side_effects=["external_write"])
        builder.register_workflow(
            WorkflowBuilder("inventory_observation")
            .step("select_inventory_evidence")
            .step("extract_inventory_items")
            .step("dedupe_inventory_observations")
            .step("build_inventory_write")
            .step("write_inventory_observations")
            .build(),
            profile=WorkflowProfile(workflow_type="inventory_observation", safety=SafetyPolicy(allowed_side_effects=["external_write"])),
        )


async def run_toy_inventory_pilot(payload: InventoryPilotInput):
    engine = WorkflowEngineBuilder().register_pack(InventoryPack()).build()
    result = await engine.run("inventory_observation", payload)
    work: InventoryWork = result.node("dedupe_inventory_observations").output
    write: ExternalWriteResult = result.output
    return (
        InventoryPilotResult(
            observations=work.observations,
            write_status=write.status,
            used_provisional_location=(payload.location_hint is None),
        ),
        engine.trace_sink,
    )


# ======================================================================================
# Toy 5 — card generation (anki-shaped, product-neutral: plan -> branch -> render)
# ======================================================================================


class CardPlan(BaseModel):
    kind: str
    content: str


class GeneratedCard(BaseModel):
    front: str
    back: str
    kind: str


def _plan_card(_context, text: str) -> CardPlan:
    return CardPlan(kind="visual" if "image" in text.lower() else "text", content=text)


def _route_card_kind(_context, plan: CardPlan) -> BranchDecision:
    return BranchDecision(label=plan.kind)


def _render_text_card(_context, plan: CardPlan) -> GeneratedCard:
    return GeneratedCard(front=f"Q: {plan.content}", back="A", kind="text")


def _render_visual_card(_context, plan: CardPlan) -> GeneratedCard:
    return GeneratedCard(front="[image]", back=plan.content, kind="visual")


class CardGenerationPack:
    def register(self, builder) -> None:
        builder.register_capability("plan_card", _plan_card, kind="llm", output_model=CardPlan)
        builder.register_capability("route_card_kind", _route_card_kind, kind="deterministic", output_model=BranchDecision)
        builder.register_capability("render_text_card", _render_text_card, kind="llm", input_model=CardPlan, output_model=GeneratedCard)
        builder.register_capability("render_visual_card", _render_visual_card, kind="media", input_model=CardPlan, output_model=GeneratedCard)
        builder.register_workflow(
            WorkflowBuilder("card_generation")
            .step("plan_card")
            .branch("route_card_kind", {"text": "render_text_card", "visual": "render_visual_card"})
            .step("render_text_card")
            .step("render_visual_card")
            .build()
        )


async def run_toy_card_generation(text: str):
    engine = WorkflowEngineBuilder().register_pack(CardGenerationPack()).build()
    result = await engine.run("card_generation", text)
    return result.output, engine.trace_sink


# ======================================================================================
# Same-executor demo — one engine (one WorkflowExecutor) runs all example workflows
# ======================================================================================


def build_demo_engine() -> WorkflowEngine:
    """One DI-wired engine registering every example pack — proves a single executor runs them all."""

    return (
        WorkflowEngineBuilder()
        .register_pack(SummaryPack())
        .register_pack(CalendarPack())
        .register_pack(SiteAuditPack())
        .register_pack(InventoryPack())
        .register_pack(CardGenerationPack())
        .build()
    )
