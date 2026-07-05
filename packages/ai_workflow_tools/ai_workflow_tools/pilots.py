"""Three-axis rigidity pilot — the engine-completion DoD proof (spec §11.2).

Lives in the TOOLS package on purpose: the pilot composes CliAgentCapability and
ConsoleLLMClient (L2/L1 members shipped here), and L2 may depend on L0 — never the
reverse. The engine package contains no reference to ai_workflow_tools.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_workflow_engine import (
    AgentCapability,
    AgentRunRequest,
    AgentRunResult,
    AgentToolStep,
    AsyncQueueTraceSink,
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
    WorkflowEngineBuilder,
    WorkflowRunResult,
    format_trace_events,
)
from ai_workflow_engine.models import (
    CapabilityResult,
    CapabilitySpec,
    EvidenceRef,
    SafetyPolicy,
    WorkflowTraceEvent,
    WorkflowGoal,
    WorkflowProfile,
)

from ai_workflow_tools.cli_agents import CliAgentCapability, ConsoleLLMClient, claude_p

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
                    # Fake episode needs no CLI tools; leaving this None would grant the
                    # Bash-bearing default and (honestly) fail the pre-spawn side-effect
                    # check against this pilot's narrow workspace_write-only ledger.
                    "allowed_tools": [],
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


