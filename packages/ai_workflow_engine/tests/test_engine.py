"""Unit tests for shared workflow engine primitives."""

import asyncio
import os
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from ai_workflow_engine.models import (
    AgentRunRequest,
    AgentStepDecision,
    CapabilitySpec,
    ClarificationOption,
    CriticismEnvelope,
    EvaluationDecision,
    EvidenceRef,
    ExternalWriteRequest,
    ExternalWriteResult,
    HumanClarificationRequest,
    RuntimeLimits,
    SafetyPolicy,
    SchedulingPolicy,
    SessionState,
    WorkflowCheckpoint,
    WorkflowArtifact,
    WorkflowDecision,
    WorkflowGoal,
    WorkflowInstrumentSpec,
    WorkflowProfile,
    WorkflowResult,
    WorkflowStepDecision,
    WorkflowUsageEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.usage_contract import NotionalPricingResult
from ai_workflow_engine.engine import (
    AgentCapability,
    CapabilityCall,
    CapabilityRegistry,
    CapabilityRuntime,
    ExternalAdapterCapability,
    EvaluationController,
    ExternalProcessCapability,
    ExternalProcessRequest,
    HumanClarificationCapability,
    InMemoryCheckpointStore,
    InMemoryHumanClarificationChannel,
    InMemoryDetailSink,
    InMemoryTraceSink,
    JsonlCheckpointStore,
    JsonlExternalWriteSink,
    JsonlTraceSink,
    RuntimePlanCompiler,
    StructuredLLMNode,
    WorkflowDecisionPlanner,
    WorkflowInstrumentRegistry,
    WorkflowLoopController,
    WorkflowRunner,
    WorkflowScheduler,
    WorkflowSupervisor,
    assert_checkpoint_payload_safe,
    artifact_result,
    capability_context_for_goal,
    cleanup_artifacts,
    gather_capabilities,
)
from ai_workflow_engine.examples import (
    CalendarBuilderInput,
    CalendarTask,
    InventoryPilotInput,
    SiteAuditInput,
    run_toy_calendar_builder,
    run_toy_inventory_pilot,
    run_toy_site_audit_pilot,
    run_toy_summary,
)
from ai_workflow_engine.budget import WorkflowBudgetExceeded
from ai_workflow_engine.pricing import estimate_cost_usd
from ai_workflow_engine.usage import invoke_metered_chat
from ai_workflow_engine.usage_rendering import format_usage_summary

pytestmark = pytest.mark.unit


class FakeGraph:
    def __init__(self):
        self.received_state = None

    def invoke(self, state):
        self.received_state = state
        return {"ok": True, **state}


class AsyncConfigGraph:
    def __init__(self):
        self.received_config = None

    async def ainvoke(self, state, config=None):
        self.received_config = config
        return {"ok": True, **state}


# Task 1.5: the runner detects recursion exhaustion by TYPE (real langgraph error) — a
# locally-faked same-named class would no longer (and must no longer) trigger the fallback.
from langgraph.errors import GraphRecursionError  # noqa: E402


class RecursingGraph:
    async def ainvoke(self, state, config=None):
        raise GraphRecursionError(
            "Recursion limit reached before END. GRAPH_RECURSION_LIMIT"
        )


async def async_handler(goal, payload):
    return {"goal": goal.objective, "payload": payload}


class FakeDecisionPlanner:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    async def plan(self, goal, registry, payload):
        self.calls.append((goal, registry, payload))
        return self.decision


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def invoke(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(content=self.responses.pop(0))


class FakeUsageLLM:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return SimpleNamespace(
            content="ok",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            response_metadata={"model_name": "gpt-5.4-mini", "id": "req-unit"},
        )


class UsageGraph:
    def __init__(self, config=None, calls=1):
        self.config = config
        self.calls = calls
        self.llm = FakeUsageLLM()

    def invoke(self, state):
        for _ in range(self.calls):
            invoke_metered_chat(
                self.llm,
                [HumanMessage(content="hello")],
                node="unit_llm",
                model="gpt-5.4-mini",
            )
        return {"ok": True, **state}


class ToySummaryGraph:
    def invoke(self, state):
        words = state["text"].split()
        return {"summary": words[0] if words else "", "word_count": len(words), **state}


def test_engine_package_imports_without_project_prompt_root(tmp_path):
    """The reusable package must not require TGHandyUtils prompt files at import time."""
    package_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(package_root), env.get("PYTHONPATH", "")])

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ai_workflow_engine; "
            "from ai_workflow_engine import CapabilitySpec, ExternalWriteRequest, StructuredLLMNode, WorkflowSupervisor; "
            "print('ok')",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_engine_core_contains_no_media_generation_modules():
    """L0 purity guard: media GENERATION lives in ai_workflow_tools.media (vision INPUT stays —
    it is part of the LLM protocol). Re-adding generation modules here is the L0<-L2 inversion
    this guard exists to block."""
    import importlib.util

    for module in ("ai_workflow_engine.image_generation",
                   "ai_workflow_engine.image_models",
                   "ai_workflow_engine.voice_generation"):
        assert importlib.util.find_spec(module) is None, f"{module} must not exist in L0"


def test_package_import_does_not_load_host_repo_modules(tmp_path):
    """Standalone package import must not drag TGHandyUtils product modules into sys.modules."""
    package_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(package_root), env.get("PYTHONPATH", "")])

    script = """
import sys

import ai_workflow_engine
from ai_workflow_engine import WorkflowBuilder, WorkflowEngineBuilder

forbidden_roots = {
    "config",
    "core",
    "database",
    "handlers",
    "platforms",
    "scheduler",
    "services",
    "telegram_handlers",
}
loaded = sorted(
    name for name in sys.modules
    if name in forbidden_roots or name.split(".", 1)[0] in forbidden_roots
)
assert loaded == [], loaded
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_engine_package_has_no_app_imports_or_anki_config_names():
    """The reusable package must not depend on TGHandyUtils product modules or config names."""
    package_root = Path(__file__).resolve().parents[1] / "ai_workflow_engine"
    forbidden = [
        "from core",
        "import core",
        "from services",
        "import services",
        "from config",
        "import config",
        "ANKI_",
    ]

    offenders = []
    for path in package_root.rglob("*.py"):
        text = path.read_text()
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(package_root)} contains {marker}")

    assert offenders == []


def test_engine_package_has_no_shipped_stub_markers():
    """Done engine primitives must not hide unfinished behavior behind stubs."""
    package_root = Path(__file__).resolve().parents[1] / "ai_workflow_engine"
    forbidden_substrings = [
        "NotImplementedError",
        "TODO",
        "FIXME",
        "simplified",
        "for now",
        "placeholder",
    ]

    offenders = []
    for path in package_root.rglob("*.py"):
        text = path.read_text()
        for marker in forbidden_substrings:
            if marker in text:
                offenders.append(f"{path.relative_to(package_root)} contains {marker}")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.strip() == "pass":
                offenders.append(f"{path.relative_to(package_root)}:{lineno} contains pass-only body")

    assert offenders == []


def test_engine_public_contract_has_no_product_specific_names():
    """Public engine names must pass the third-project neutrality test."""
    import ai_workflow_engine
    import ai_workflow_engine.models as models

    forbidden_terms = [
        "anki",
        "cloze",
        "telegram",
        "mageqa",
        "browser",
        "gopro",
        "calendar",
        "inventory",
        "chat_id",
        "message_id",
    ]
    public_names = list(ai_workflow_engine.__all__)
    model_field_names: list[str] = []
    for value in vars(models).values():
        fields = getattr(value, "model_fields", None)
        if fields:
            model_field_names.extend(fields)

    offenders = []
    for name in [*public_names, *model_field_names]:
        lowered = name.lower()
        for term in forbidden_terms:
            if term in lowered:
                offenders.append(name)

    assert offenders == []


async def test_workflow_runner_attaches_goal_and_context():
    goal = WorkflowGoal(
        workflow_type="anki_generation",
        objective="Generate cards",
        constraints={"default_count_policy": "prefer_fewer_cards"},
        delivery_target="telegram_anki_package",
        user_id=10,
        metadata={"telegram_chat_id": 20, "telegram_message_id": 30},
    )
    graph = FakeGraph()

    result = await WorkflowRunner().run(graph, {"source": "content"}, goal=goal)

    assert result["workflow_goal"] == goal
    assert graph.received_state["workflow_context"].goal_id == goal.goal_id
    assert graph.received_state["workflow_context"].workflow_type == "anki_generation"
    assert graph.received_state["workflow_context"].delivery_target == "telegram_anki_package"
    assert graph.received_state["workflow_context"].user_id == 10
    assert graph.received_state["workflow_context"].metadata == {
        "telegram_chat_id": 20,
        "telegram_message_id": 30,
    }


async def test_workflow_runner_forwards_optional_graph_config():
    graph = AsyncConfigGraph()
    goal = WorkflowGoal(workflow_type="bounded_graph", objective="run with explicit limits")

    result = await WorkflowRunner().run(
        graph,
        {},
        goal=goal,
        graph_config={"recursion_limit": 12},
    )

    assert result["ok"] is True
    assert graph.received_config == {"recursion_limit": 12}


async def test_workflow_runner_routes_recursion_exhaustion_to_policy_fallback():
    goal = WorkflowGoal(workflow_type="bounded_graph", objective="prove recursion fallback")
    fallback_calls = []

    def fallback(state, exc):
        fallback_calls.append((state, exc))
        return {
            "ok": True,
            "degraded": "recursion_fallback",
            "error_type": exc.__class__.__name__,
        }

    result = await WorkflowRunner().run(
        RecursingGraph(),
        {"source": "content"},
        goal=goal,
        graph_config={"recursion_limit": 2},
        recursion_fallback=fallback,
    )

    assert result["ok"] is True
    assert result["degraded"] == "recursion_fallback"
    assert result["error_type"] == "GraphRecursionError"
    assert result["workflow_context"].workflow_type == "bounded_graph"
    assert result["usage_summary"].total_tokens == 0
    assert len(fallback_calls) == 1
    assert fallback_calls[0][0]["workflow_goal"] == goal


async def test_workflow_supervisor_selects_different_instruments_for_different_goals():
    registry = WorkflowInstrumentRegistry()
    calls: list[str] = []

    registry.register(
        WorkflowInstrumentSpec(name="summarize", kind="workflow"),
        lambda _goal, payload: calls.append("summarize") or {"summary": payload["text"][:7]},
    )
    registry.register(
        WorkflowInstrumentSpec(name="inventory", kind="workflow"),
        lambda _goal, payload: calls.append("inventory") or {"items": payload["items"]},
    )

    class Planner:
        async def plan(self, goal, _registry, _payload):
            if "inventory" in goal.objective:
                return WorkflowDecision(instrument_name="inventory", rationale="inventory goal")
            return WorkflowDecision(instrument_name="summarize", rationale="summary goal")

    supervisor = WorkflowSupervisor(registry, Planner())

    summary = await supervisor.run(
        WorkflowGoal(workflow_type="auto", objective="summarize this text"),
        {"text": "portable runtime"},
    )
    inventory = await supervisor.run(
        WorkflowGoal(workflow_type="auto", objective="inventory this shelf"),
        {"items": ["glue"]},
    )

    assert summary.decision.instrument_name == "summarize"
    assert summary.result == {"summary": "portabl"}
    assert inventory.decision.instrument_name == "inventory"
    assert inventory.result == {"items": ["glue"]}
    assert calls == ["summarize", "inventory"]


async def test_capability_runtime_validates_input_output_and_records_trace():
    from pydantic import BaseModel

    class AddInput(BaseModel):
        a: int
        b: int

    class AddOutput(BaseModel):
        total: int

    def add(_context, payload: AddInput):
        return {"total": payload.a + payload.b}

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            name="add_numbers",
            kind="deterministic",
            input_model=AddInput,
            output_model=AddOutput,
        ),
        add,
    )
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    goal = WorkflowGoal(workflow_type="math", objective="add numbers")

    result = await runtime.invoke(
        "add_numbers",
        {"a": 2, "b": 3},
        capability_context_for_goal(goal),
    )

    assert result.status == "accepted"
    assert result.output == AddOutput(total=5)
    assert [event.decision for event in trace.events] == ["start", "accepted"]


async def test_capability_runtime_records_tool_payload_and_result_details_when_enabled():
    def echo(_context, payload):
        return {"echo": payload["text"]}

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="echo", kind="tool"), echo)
    trace = InMemoryTraceSink()
    details = InMemoryDetailSink()
    runtime = CapabilityRuntime(registry, trace, detail_sink=details, capture_detail_text=True)
    goal = WorkflowGoal(workflow_type="observe_tools", objective="observe tool io")

    result = await runtime.invoke(
        "echo",
        {"text": "SECRET input"},
        capability_context_for_goal(goal),
    )

    assert result.status == "accepted"
    assert result.output == {"echo": "SECRET input"}
    tool_events = [event for event in trace.events if event.phase in {"tool:request", "tool:result"}]
    assert [event.decision for event in tool_events] == ["start", "accepted"]
    assert [detail.kind for detail in details.details] == ["tool_payload", "tool_result"]
    assert tool_events[0].detail_refs == [details.details[0].detail_id]
    assert tool_events[1].detail_refs == [details.details[1].detail_id]
    assert details.details[0].event_id == tool_events[0].event_id
    assert details.details[1].event_id == tool_events[1].event_id
    assert details.details[0].redaction_state == "none"
    assert details.details[0].json_value["payload"]["text"] == "SECRET input"
    trace_blob = json.dumps([event.model_dump() for event in tool_events], default=str)
    detail_blob = json.dumps([detail.model_dump(by_alias=True) for detail in details.details], default=str)
    assert "SECRET input" not in trace_blob
    assert "SECRET input" in detail_blob


async def test_capability_runtime_records_tool_error_detail_when_enabled():
    def fail(_context, _payload):
        raise ValueError("tool exploded")

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="unstable", kind="tool"), fail)
    trace = InMemoryTraceSink()
    details = InMemoryDetailSink()
    runtime = CapabilityRuntime(registry, trace, detail_sink=details, capture_detail_text=True)
    goal = WorkflowGoal(workflow_type="observe_tools", objective="observe tool errors")

    result = await runtime.invoke("unstable", {"text": "ok"}, capability_context_for_goal(goal))

    assert result.status == "failed"
    response_event = next(event for event in trace.events if event.phase == "tool:result")
    response_detail = next(detail for detail in details.details if detail.kind == "tool_result")
    assert response_event.severity == "error"
    assert response_event.error == "tool exploded"
    assert response_event.detail_refs == [response_detail.detail_id]
    assert response_detail.redaction_state == "none"
    assert response_detail.json_value["error"] == "tool exploded"


async def test_capability_runtime_records_artifact_preview_details_when_enabled():
    artifact = WorkflowArtifact(
        path="/tmp/engine-observation-report.html",
        kind="file",
        source="unit",
        owner_node="producer",
    )

    def produce(_context, _payload):
        return artifact_result({"created": True}, [artifact])

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="producer", kind="tool"), produce)
    trace = InMemoryTraceSink()
    details = InMemoryDetailSink()
    runtime = CapabilityRuntime(registry, trace, detail_sink=details, capture_detail_text=True)
    goal = WorkflowGoal(workflow_type="observe_artifacts", objective="observe artifacts")

    result = await runtime.invoke("producer", {}, capability_context_for_goal(goal))

    assert result.status == "accepted"
    assert result.artifacts == [artifact]
    assert [detail.kind for detail in details.details] == [
        "tool_payload",
        "tool_result",
        "artifact_preview",
    ]
    artifact_event = next(event for event in trace.events if event.phase == "tool:result")
    artifact_detail = details.details[2]
    assert artifact_event.detail_refs == [details.details[1].detail_id, artifact_detail.detail_id]
    assert artifact_detail.event_id == artifact_event.event_id
    assert artifact_detail.redaction_state == "none"
    assert artifact_detail.json_value["artifact"]["path"] == "/tmp/engine-observation-report.html"


async def test_jsonl_trace_sink_records_events(tmp_path):
    from pydantic import BaseModel

    class EchoOutput(BaseModel):
        text: str

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="echo", kind="deterministic", output_model=EchoOutput),
        lambda _context, payload: {"text": payload["text"]},
    )
    trace_path = tmp_path / "traces" / "run.jsonl"
    runtime = CapabilityRuntime(registry, JsonlTraceSink(trace_path))
    goal = WorkflowGoal(workflow_type="trace", objective="record trace")

    result = await runtime.invoke("echo", {"text": "ok"}, capability_context_for_goal(goal))

    lines = trace_path.read_text().splitlines()
    assert result.status == "accepted"
    assert len(lines) == 2
    assert '"node":"echo"' in lines[0]
    assert '"decision":"accepted"' in lines[1]


async def test_capability_runtime_returns_failed_result_on_validation_error():
    from pydantic import BaseModel

    class AddInput(BaseModel):
        a: int
        b: int

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="add_numbers", kind="deterministic", input_model=AddInput),
        lambda _context, payload: payload,
    )
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    goal = WorkflowGoal(workflow_type="math", objective="add numbers")

    result = await runtime.invoke(
        "add_numbers",
        {"a": "bad", "b": 3},
        capability_context_for_goal(goal),
    )

    assert result.status == "failed"
    assert "input validation failed" in result.error


def test_runtime_plan_compiler_warns_on_unknown_constraints_and_capabilities():
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="known_tool", kind="deterministic"),
        lambda _context, payload: payload,
    )
    profile = WorkflowProfile(
        workflow_type="portable",
        constraints={"known": True, "unexpected": "x"},
        requested_capabilities=["known_tool", "missing_tool"],
    )

    plan = RuntimePlanCompiler(supported_constraint_keys={"known"}).compile(profile, registry)

    assert plan.capability_names == ["known_tool", "missing_tool"]
    assert "Unsupported constraint key: unexpected" in plan.warnings
    assert "Unknown capability requested: missing_tool" in plan.warnings


def test_runtime_plan_compiler_carries_safety_policy():
    profile = WorkflowProfile(
        workflow_type="safe_runtime",
        safety=SafetyPolicy(allowed_side_effects=["local_write"]),
    )

    plan = RuntimePlanCompiler().compile(profile)

    assert plan.safety.allowed_side_effects == ["local_write"]


async def test_gather_capabilities_isolates_partial_failures():
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="ok", kind="deterministic"), lambda _context, payload: payload)

    def fail(_context, _payload):
        raise RuntimeError("boom")

    registry.register(CapabilitySpec(name="fail", kind="deterministic"), fail)
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    goal = WorkflowGoal(workflow_type="fanout", objective="run children")

    results = await gather_capabilities(
        runtime,
        [
            CapabilityCall("ok", {"value": 1}),
            CapabilityCall("fail", {"value": 2}),
            CapabilityCall("ok", {"value": 3}),
        ],
        capability_context_for_goal(goal),
        max_parallel=2,
    )

    assert [result.status for result in results] == ["accepted", "failed", "accepted"]
    assert results[1].error == "boom"


async def test_gather_capabilities_records_timeout_as_partial_failure():
    async def slow(_context, payload):
        await asyncio.sleep(0.2)
        return payload

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="ok", kind="deterministic"), lambda _context, payload: payload)
    registry.register(CapabilitySpec(name="slow", kind="tool", timeout_s=0.01), slow)
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    goal = WorkflowGoal(workflow_type="fanout", objective="run partial fan-out")

    results = await gather_capabilities(
        runtime,
        [
            CapabilityCall("ok", {"value": 1}),
            CapabilityCall("slow", {"value": 2}),
            CapabilityCall("ok", {"value": 3}),
        ],
        capability_context_for_goal(goal),
        max_parallel=3,
    )

    # v0.10: a timed-out gather item is PARTIAL (bounded), carrying the execution-window reason.
    assert [result.status for result in results] == ["accepted", "partial", "accepted"]
    assert "execution window" in results[1].error
    assert results[1].metadata.get("timeout_reason") == "execution_window_exceeded"
    assert any(event.node == "slow" and event.decision == "partial" for event in trace.events)


async def test_capability_runtime_denies_disallowed_side_effect_before_handler_call():
    called = False

    def write_file(_context, payload):
        nonlocal called
        called = True
        return payload

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="write_file", kind="tool", side_effects=["local_write"]),
        write_file,
    )
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    profile = WorkflowProfile(
        workflow_type="safe_runtime",
        safety=SafetyPolicy(allowed_side_effects=["read_only"]),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="safe_runtime", objective="try write")

    result = await runtime.invoke("write_file", {"value": 1}, capability_context_for_goal(goal, plan=plan))

    assert result.status == "rejected"
    assert called is False
    assert result.metadata["denied_side_effects"] == ["local_write"]
    assert any(event.node == "write_file" and event.decision == "rejected" for event in trace.events)


async def test_capability_runtime_allows_declared_side_effect_from_plan():
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="write_file", kind="tool", side_effects=["local_write"]),
        lambda _context, payload: {"written": payload["value"]},
    )
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    profile = WorkflowProfile(
        workflow_type="safe_runtime",
        safety=SafetyPolicy(allowed_side_effects=["local_write"]),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="safe_runtime", objective="write")

    result = await runtime.invoke("write_file", {"value": 1}, capability_context_for_goal(goal, plan=plan))

    assert result.status == "accepted"
    assert result.output == {"written": 1}


async def test_workflow_loop_controller_runs_supervisor_style_steps_to_completion():
    from pydantic import BaseModel

    class NumberInput(BaseModel):
        value: int

    class NumberOutput(BaseModel):
        value: int

    class Planner:
        async def plan(self, _context, history, _decisions):
            if not history:
                return WorkflowStepDecision(
                    action="invoke",
                    capability_name="double",
                    payload={"value": 2},
                    rationale="First transform the input.",
                )
            if len(history) == 1:
                return WorkflowStepDecision(
                    action="invoke",
                    capability_name="increment",
                    payload=history[-1].output,
                    rationale="Then adjust the result.",
                )
            return WorkflowStepDecision(action="finish", rationale="Goal satisfied.")

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="double", kind="deterministic", input_model=NumberInput, output_model=NumberOutput),
        lambda _context, payload: NumberOutput(value=payload.value * 2),
    )
    registry.register(
        CapabilitySpec(name="increment", kind="deterministic", input_model=NumberOutput, output_model=NumberOutput),
        lambda _context, payload: NumberOutput(value=payload.value + 1),
    )
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    goal = WorkflowGoal(workflow_type="calculator", objective="double then increment")

    result = await WorkflowLoopController(runtime, Planner(), max_steps=4).run(capability_context_for_goal(goal))

    assert result.status == "completed"
    assert result.output == NumberOutput(value=5)
    assert [decision.capability_name for decision in result.decisions[:2]] == ["double", "increment"]
    assert any(event.node == "workflow_supervisor_loop" and event.decision == "finish" for event in trace.events)


async def test_workflow_loop_controller_fails_closed_on_step_limit():
    class NeverDonePlanner:
        async def plan(self, _context, _history, _decisions):
            return WorkflowStepDecision(action="invoke", capability_name="noop", payload={})

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="noop", kind="deterministic"), lambda _context, payload: payload)
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    goal = WorkflowGoal(workflow_type="loop", objective="prove step cap")

    result = await WorkflowLoopController(runtime, NeverDonePlanner(), max_steps=2).run(
        capability_context_for_goal(goal)
    )

    assert result.status == "failed"
    assert result.error == "Workflow step limit exhausted: 2"
    assert len(result.capability_results) == 2


async def test_workflow_loop_controller_writes_reloadable_checkpoints(tmp_path):
    class Planner:
        async def plan(self, _context, history, _decisions):
            if not history:
                return WorkflowStepDecision(action="invoke", capability_name="echo", payload={"value": 1})
            return WorkflowStepDecision(action="finish", payload={"done": True})

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="echo", kind="deterministic"), lambda _context, payload: payload)
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    checkpoint_path = tmp_path / "checkpoints" / "run.jsonl"
    store = JsonlCheckpointStore(checkpoint_path)
    goal = WorkflowGoal(workflow_type="checkpointed", objective="save progress")
    context = capability_context_for_goal(goal, workflow_id="wf-checkpoint")

    result = await WorkflowLoopController(runtime, Planner(), max_steps=3, checkpoint_store=store).run(context)

    reloaded = JsonlCheckpointStore(checkpoint_path)
    checkpoints = reloaded.list("wf-checkpoint")
    assert result.status == "completed"
    assert [checkpoint.status for checkpoint in checkpoints] == ["running", "running", "completed"]
    assert reloaded.latest("wf-checkpoint").data["last_output"] == {"done": True}


def test_checkpoint_stores_reject_raw_bytes_and_keep_latest():
    store = InMemoryCheckpointStore()
    first = WorkflowCheckpoint(
        workflow_id="wf-1",
        workflow_type="inventory",
        session_id=SessionState().session_id,
        step_index=1,
        data={"evidence_ref": "file:///frame.jpg"},
    )
    second = first.model_copy(update={"step_index": 2, "status": "completed"})

    store.save(first)
    store.save(second)

    assert store.latest("wf-1").step_index == 2
    assert [checkpoint.step_index for checkpoint in store.list("wf-1")] == [1, 2]
    with pytest.raises(ValueError, match="raw bytes"):
        assert_checkpoint_payload_safe({"frame": b"not-state"})


def test_checkpoint_guard_rejects_nested_transport_payload_shapes():
    from dataclasses import dataclass

    from ai_workflow_engine.transport_models import ImageInput

    @dataclass
    class DataclassState:
        raw: bytes

    class TaggedDict(dict):
        pass

    tagged = TaggedDict({"safe": True})
    tagged.raw = b"hidden"

    class TaggedInt(int):
        pass

    number = TaggedInt(7)
    number.raw = b"hidden"

    class Opaque:
        pass

    unsafe_payloads = [
        {b"raw-key": "value"},
        {"state": DataclassState(b"raw")},
        {"state": tagged},
        {"state": number},
        {"state": ImageInput(source="base64", data="aGk=", media_type="image/png")},
        {"state": Opaque()},
    ]

    for payload in unsafe_payloads:
        with pytest.raises(ValueError, match="checkpoint data"):
            assert_checkpoint_payload_safe(payload)

    assert_checkpoint_payload_safe(
        {
            "state": {
                "evidence": EvidenceRef(ref_id="frame-1", role="frame", uri="evidence://frame-1"),
                "status": "ok",
                "items": [1, 2, 3],
            }
        }
    )


async def test_human_clarification_capability_pauses_supervisor_loop_until_answer():
    clarification_id = "location-choice"

    class Planner:
        async def plan(self, _context, history, _decisions):
            if not history:
                return WorkflowStepDecision(
                    action="invoke",
                    capability_name="ask_user",
                    payload=HumanClarificationRequest(
                        clarification_id=clarification_id,
                        question="Which cabinet is this?",
                        options=[
                            ClarificationOption(label="Living room", value="living_room"),
                            ClarificationOption(label="Kitchen", value="kitchen"),
                        ],
                    ),
                    rationale="Need location before writing inventory.",
                )
            return WorkflowStepDecision(action="finish", payload=history[-1].output)

    channel = InMemoryHumanClarificationChannel()
    registry = CapabilityRegistry()
    ask_user = HumanClarificationCapability(channel)
    registry.register(ask_user.spec, ask_user)
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    profile = WorkflowProfile(
        workflow_type="inventory",
        safety=SafetyPolicy(allowed_side_effects=["notification"]),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="inventory", objective="identify object location")

    waiting = await WorkflowLoopController(runtime, Planner(), max_steps=3).run(
        capability_context_for_goal(goal, plan=plan)
    )

    assert waiting.status == "waiting"
    assert channel.pending[clarification_id].question == "Which cabinet is this?"
    assert waiting.output.status == "pending"
    assert any(event.node == "ask_user" and event.decision == "partial" for event in trace.events)

    channel.submit_answer(clarification_id, "living_room", answer_text="Living room")
    answered = await runtime.invoke(
        "ask_user",
        HumanClarificationRequest(clarification_id=clarification_id, question="Which cabinet is this?"),
        capability_context_for_goal(goal, plan=plan),
        attempt=2,
    )

    assert answered.status == "accepted"
    assert answered.output.status == "answered"
    assert answered.output.value == "living_room"


async def test_human_clarification_capability_can_continue_with_provisional_value():
    channel = InMemoryHumanClarificationChannel()
    registry = CapabilityRegistry()
    ask_user = HumanClarificationCapability(channel)
    registry.register(ask_user.spec, ask_user)
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    profile = WorkflowProfile(
        workflow_type="calendar",
        safety=SafetyPolicy(allowed_side_effects=["notification"]),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="calendar", objective="build schedule")

    result = await runtime.invoke(
        "ask_user",
        {
            "question": "What is today's energy level?",
            "clarification_id": "energy",
            "default_value": "medium",
            "continue_without_answer": True,
        },
        capability_context_for_goal(goal, plan=plan),
    )

    assert result.status == "accepted"
    assert result.output.status == "provisional"
    assert result.output.value == "medium"
    assert result.metadata["provisional"] is True
    assert channel.pending == {}


async def test_human_clarification_capability_respects_notification_safety_gate():
    channel = InMemoryHumanClarificationChannel()
    registry = CapabilityRegistry()
    ask_user = HumanClarificationCapability(channel)
    registry.register(ask_user.spec, ask_user)
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    profile = WorkflowProfile(
        workflow_type="restricted",
        safety=SafetyPolicy(allowed_side_effects=["read_only"]),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="restricted", objective="no outbound messages")

    result = await runtime.invoke(
        "ask_user",
        {"question": "Can I send a message?", "clarification_id": "policy"},
        capability_context_for_goal(goal, plan=plan),
    )

    assert result.status == "rejected"
    assert result.metadata["denied_side_effects"] == ["notification"]
    assert channel.pending == {}


async def test_agent_capability_runs_allowed_tool_and_finishes_with_structured_result():
    class Planner:
        async def next_step(self, _context, _request, history):
            if not history:
                return AgentStepDecision(
                    action="tool",
                    tool_name="inspect_page",
                    payload={"url": "https://example.test"},
                    rationale="Gather page evidence.",
                )
            return AgentStepDecision(
                action="finish",
                output={"finding_count": 1, "url": history[-1].output["url"]},
                rationale="Evidence collected.",
            )

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="inspect_page", kind="tool", side_effects=["read_only"]),
        lambda _context, payload: {"url": payload["url"], "evidence": "screenshot-ref"},
    )
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    agent = AgentCapability(Planner(), runtime, name="browser_agent")
    registry.register(agent.spec, agent)
    profile = WorkflowProfile(
        workflow_type="qa",
        limits=RuntimeLimits(max_steps=4),
        safety=SafetyPolicy(allowed_side_effects=["read_only"]),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="qa", objective="audit page")

    result = await runtime.invoke(
        "browser_agent",
        AgentRunRequest(
            prompt="Check page accessibility",
            allowed_tools=["inspect_page"],
            max_steps=3,
            subscription_mode=True,
        ),
        capability_context_for_goal(goal, plan=plan),
    )

    assert result.status == "accepted"
    assert result.output.status == "completed"
    assert result.output.subscription_call is True
    assert result.output.estimated_metered_usd == 0.0
    assert result.output.steps[0].call.tool_name == "inspect_page"
    assert result.output.output == {"finding_count": 1, "url": "https://example.test"}
    assert any(event.node == "browser_agent" and event.decision == "tool" for event in trace.events)


async def test_agent_capability_denies_unlisted_tool_before_handler_call():
    called = False

    class Planner:
        async def next_step(self, _context, _request, _history):
            return AgentStepDecision(action="tool", tool_name="delete_data", payload={})

    def destructive_tool(_context, payload):
        nonlocal called
        called = True
        return payload

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="delete_data", kind="tool", side_effects=["destructive"]),
        destructive_tool,
    )
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    agent = AgentCapability(Planner(), runtime, name="guarded_agent")
    registry.register(agent.spec, agent)
    profile = WorkflowProfile(
        workflow_type="safe_agent",
        safety=SafetyPolicy(allowed_side_effects=["destructive"]),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="safe_agent", objective="prove tool allow-list")

    result = await runtime.invoke(
        "guarded_agent",
        {"prompt": "Try a forbidden tool", "allowed_tools": ["inspect_page"]},
        capability_context_for_goal(goal, plan=plan),
    )

    assert result.status == "rejected"
    assert called is False
    assert result.metadata["denied_tool"] == "delete_data"
    assert any(event.node == "guarded_agent" and event.decision == "rejected" for event in trace.events)


async def test_agent_capability_truncates_on_step_limit_and_keeps_tool_history():
    class Planner:
        async def next_step(self, _context, _request, history):
            return AgentStepDecision(
                action="tool",
                tool_name="sample_frame",
                payload={"index": len(history)},
            )

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="sample_frame", kind="tool", side_effects=["read_only"]),
        lambda _context, payload: {"sampled": payload["index"]},
    )
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    agent = AgentCapability(Planner(), runtime, name="video_agent")
    registry.register(agent.spec, agent)
    profile = WorkflowProfile(
        workflow_type="video",
        limits=RuntimeLimits(max_steps=1),
        safety=SafetyPolicy(allowed_side_effects=["read_only"]),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="video", objective="inventory drawer")

    result = await runtime.invoke(
        "video_agent",
        {"prompt": "Sample useful frames", "allowed_tools": ["sample_frame"], "max_steps": 5},
        capability_context_for_goal(goal, plan=plan),
    )

    assert result.status == "partial"
    assert result.output.status == "truncated"
    assert result.output.steps[0].output == {"sampled": 0}
    assert "step limit exhausted" in result.error


async def test_external_process_capability_salvages_timeout_output():
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="external_worker", kind="external"),
        ExternalProcessCapability(),
    )
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    goal = WorkflowGoal(workflow_type="external", objective="run worker")

    result = await runtime.invoke(
        "external_worker",
        ExternalProcessRequest(
            command=[
                sys.executable,
                "-c",
                "import time; print('started', flush=True); time.sleep(2)",
            ],
            timeout_s=0.2,
        ),
        capability_context_for_goal(goal),
    )

    assert result.status == "partial"
    assert "timed out" in result.error
    assert "started" in result.output["stdout"]


async def test_external_process_capability_delivers_stdin_data():
    result = await ExternalProcessCapability()(
        capability_context_for_goal(WorkflowGoal(workflow_type="external", objective="pipe stdin")),
        ExternalProcessRequest(
            command=[
                sys.executable,
                "-c",
                "import sys; print(sys.stdin.read().upper(), end='')",
            ],
            stdin_data="hello stdin",
        ),
    )

    assert result.status == "accepted"
    assert result.output["stdout"] == "HELLO STDIN"
    assert result.output["result"] == "HELLO STDIN"


async def test_external_process_capability_reads_result_file_when_present(tmp_path):
    result_file = tmp_path / "last-message.txt"
    result = await ExternalProcessCapability()(
        capability_context_for_goal(WorkflowGoal(workflow_type="external", objective="read result file")),
        ExternalProcessRequest(
            command=[
                sys.executable,
                "-c",
                "from pathlib import Path; Path('last-message.txt').write_text('from file'); print('stdout fallback')",
            ],
            cwd=str(tmp_path),
            result_file="last-message.txt",
        ),
    )

    assert result.status == "accepted"
    assert result.output["stdout"].strip() == "stdout fallback"
    assert result.output["result"] == "from file"
    assert result_file.read_text() == "from file"


async def test_external_process_capability_terminates_then_grace_kills_stubborn_child(tmp_path):
    term_marker = tmp_path / "terminated.txt"
    script = (
        "import signal, sys, time\n"
        "from pathlib import Path\n"
        "marker = Path(sys.argv[1])\n"
        "def on_term(_signum, _frame):\n"
        "    marker.write_text('terminated')\n"
        "    time.sleep(5)\n"
        "signal.signal(signal.SIGTERM, on_term)\n"
        "print('ready', flush=True)\n"
        "time.sleep(10)\n"
    )

    result = await ExternalProcessCapability()(
        capability_context_for_goal(WorkflowGoal(workflow_type="external", objective="grace kill")),
        ExternalProcessRequest(
            command=[sys.executable, "-c", script, str(term_marker)],
            timeout_s=0.3,
            kill_grace_s=0.1,
        ),
    )

    assert result.status == "partial"
    assert result.metadata["killed_after_grace"] is True
    assert term_marker.read_text() == "terminated"
    assert "ready" in result.output["stdout"]


@pytest.mark.skipif(os.name != "posix", reason="process-group ownership is POSIX-specific")
async def test_external_process_timeout_stops_the_owned_descendant_tree(tmp_path):
    marker = tmp_path / "descendant-survived.txt"
    child = (
        "import sys,time; from pathlib import Path; "
        "time.sleep(0.5); Path(sys.argv[1]).write_text('survived')"
    )
    parent = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
        "print('spawned', flush=True); time.sleep(30)"
    )

    result = await ExternalProcessCapability()(
        capability_context_for_goal(
            WorkflowGoal(workflow_type="external", objective="own process tree")
        ),
        ExternalProcessRequest(
            command=[sys.executable, "-c", parent, child, str(marker)],
            timeout_s=0.1,
            kill_grace_s=0.05,
        ),
    )
    await asyncio.sleep(0.6)

    assert result.status == "partial"
    assert "spawned" in result.output["stdout"]
    assert not marker.exists(), "a descendant escaped the process-backed execution window"


async def test_external_process_timeout_also_bounds_stdin_delivery():
    """A child that never reads stdin must not strand the process owner in pipe drain."""

    operation = ExternalProcessCapability()(
        capability_context_for_goal(
            WorkflowGoal(workflow_type="external", objective="bound stdin")
        ),
        ExternalProcessRequest(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            stdin_data="x" * 2_000_000,
            timeout_s=0.1,
            kill_grace_s=0.05,
        ),
    )
    result = await asyncio.wait_for(operation, timeout=1.0)

    assert result.status == "partial"
    assert "timed out" in result.error


async def test_external_process_early_exit_during_large_stdin_preserves_child_settlement():
    """A child may reject its request before consuming a pipe-sized stdin payload.

    Closing the writer can then raise ``BrokenPipeError`` from ``wait_closed()`` even when
    ``drain()`` was already guarded. That transport detail must not replace the process's real
    exit status and bounded stderr diagnostics.
    """

    script = (
        "import sys; "
        "sys.stderr.write('request rejected before stdin\\n'); "
        "sys.stderr.flush(); "
        "raise SystemExit(2)"
    )
    result = await ExternalProcessCapability()(
        capability_context_for_goal(
            WorkflowGoal(workflow_type="external", objective="preserve early exit truth")
        ),
        ExternalProcessRequest(
            command=[sys.executable, "-c", script],
            stdin_data="x" * 2_000_000,
            timeout_s=5,
        ),
    )

    assert result.status == "failed"
    assert result.error == "external process exited 2"
    assert result.output["returncode"] == 2
    assert result.output["stderr"] == "request rejected before stdin\n"


async def test_external_process_stdin_close_suppresses_a_late_broken_pipe():
    """macOS may raise from wait_closed after drain already succeeded; Linux often does not."""

    class BrokenOnClose:
        def __init__(self) -> None:
            self.payload = b""
            self.closed = False

        def write(self, payload: bytes) -> None:
            self.payload = payload

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            raise BrokenPipeError("child exited before stdin settlement")

    stdin = BrokenOnClose()
    await ExternalProcessCapability._write_stdin(SimpleNamespace(stdin=stdin), "payload")

    assert stdin.payload == b"payload"
    assert stdin.closed is True


async def test_external_process_capability_handles_long_single_line_stdout_and_stderr():
    # Regression (G-0.1): readline() raised LimitOverrunError -> ValueError on any single
    # line beyond asyncio's 64KiB stream limit — CLI workers emit huge one-line JSON.
    payload_len = 200_000
    script = (
        "import sys\n"
        f"sys.stdout.write('o' * {payload_len})\n"
        f"sys.stderr.write('e' * {payload_len})\n"
    )

    result = await ExternalProcessCapability()(
        capability_context_for_goal(WorkflowGoal(workflow_type="external", objective="long single lines")),
        ExternalProcessRequest(command=[sys.executable, "-c", script], timeout_s=30),
    )

    assert result.status == "accepted"
    assert len(result.output["stdout"]) == payload_len
    assert set(result.output["stdout"]) == {"o"}
    assert len(result.output["stderr"]) == payload_len
    assert set(result.output["stderr"]) == {"e"}


async def test_external_process_timeout_salvage_survives_long_single_line():
    # Regression (G-0.1): the timeout-salvage path must also survive a giant no-newline
    # payload — pre-fix the reader task crashed before the timeout branch could salvage.
    payload_len = 150_000
    script = (
        "import sys, time\n"
        f"sys.stdout.write('x' * {payload_len})\n"
        "sys.stdout.flush()\n"
        "time.sleep(5)\n"
    )

    result = await ExternalProcessCapability()(
        capability_context_for_goal(WorkflowGoal(workflow_type="external", objective="salvage long line")),
        ExternalProcessRequest(command=[sys.executable, "-c", script], timeout_s=0.5),
    )

    assert result.status == "partial"
    assert "timed out" in result.error
    assert len(result.output["stdout"]) == payload_len
    assert set(result.output["stdout"]) == {"x"}


async def test_external_adapter_capability_writes_idempotently():
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            name="write_inventory",
            kind="external",
            input_model=ExternalWriteRequest,
            output_model=ExternalWriteResult,
            side_effects=["domain_write"],
        ),
        ExternalAdapterCapability(),
    )
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    goal = WorkflowGoal(workflow_type="inventory", objective="write observation")
    payload = ExternalWriteRequest(
        target="home_inventory",
        operation="upsert_item",
        idempotency_key="cabinet-1-glue",
        payload={"item": "glue", "location": "living room closet"},
        evidence_refs=[
            EvidenceRef(role="contents_frame", uri="file:///frame.jpg", media_type="image/jpeg")
        ],
        privacy_level="internal",
    )

    first = await runtime.invoke("write_inventory", payload, capability_context_for_goal(goal))
    second = await runtime.invoke("write_inventory", payload, capability_context_for_goal(goal), attempt=2)

    assert first.status == "accepted"
    assert first.output.status == "created"
    assert first.output.metadata["evidence_count"] == 1
    assert second.status == "accepted"
    assert second.output.status == "duplicate"
    assert second.output.external_id == first.output.external_id


async def test_external_adapter_capability_rejects_secret_payload_without_secure_sink():
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="write_sensitive", kind="external", input_model=ExternalWriteRequest),
        ExternalAdapterCapability(),
    )
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    goal = WorkflowGoal(workflow_type="security", objective="write secret")

    result = await runtime.invoke(
        "write_sensitive",
        {
            "target": "crm",
            "operation": "upsert",
            "idempotency_key": "secret-1",
            "payload": {"token": "should-not-enter-default-sink"},
            "privacy_level": "secret",
        },
        capability_context_for_goal(goal),
    )

    assert result.status == "rejected"
    assert "secure adapter" in result.error


async def test_jsonl_external_write_sink_records_machine_readable_audit(tmp_path):
    trace_path = tmp_path / "external" / "writes.jsonl"
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            name="write_report",
            kind="external",
            input_model=ExternalWriteRequest,
            output_model=ExternalWriteResult,
        ),
        ExternalAdapterCapability(JsonlExternalWriteSink(trace_path)),
    )
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    goal = WorkflowGoal(workflow_type="qa", objective="write report")

    result = await runtime.invoke(
        "write_report",
        {
            "target": "mageqa_report_store",
            "operation": "append_finding",
            "idempotency_key": "finding-1",
            "payload": {"severity": "high"},
            "privacy_level": "confidential",
        },
        capability_context_for_goal(goal),
    )

    line = json.loads(trace_path.read_text().splitlines()[0])
    assert result.status == "accepted"
    assert line["request"]["target"] == "mageqa_report_store"
    assert line["result"]["status"] == "created"


def test_workflow_scheduler_drop_stale_and_single_flight_cancel():
    scheduler = WorkflowScheduler(clock=lambda: 100.0)

    stale = scheduler.submit(
        key="camera-1",
        run_id="old",
        payload={},
        policy=SchedulingPolicy(mode="drop_stale", stale_after_s=10),
        created_at=80.0,
    )
    first = scheduler.submit(
        key="camera-1",
        run_id="r1",
        payload={},
        policy=SchedulingPolicy(mode="single_flight_cancel"),
    )
    second = scheduler.submit(
        key="camera-1",
        run_id="r2",
        payload={},
        policy=SchedulingPolicy(mode="single_flight_cancel"),
    )

    assert stale.action == "drop"
    assert stale.reason == "stale"
    assert first.action == "accept"
    assert second.action == "cancel_previous"
    assert "r1" in second.reason
    assert scheduler.active_run_id("camera-1") == "r1"
    promoted = scheduler.complete(key="camera-1", run_id="r1")
    assert promoted.action == "promote"
    assert promoted.run_id == "r2"


def test_workflow_scheduler_modes_priority_coalesce_and_backend_caps():
    scheduler = WorkflowScheduler(clock=lambda: 100.0)

    fanout = scheduler.submit(
        key="audit",
        run_id="child-1",
        payload={},
        policy=SchedulingPolicy(mode="fan_out_gather"),
    )
    replay_low = scheduler.submit(
        key="replay",
        run_id="low",
        payload={},
        policy=SchedulingPolicy(mode="replay_process_all", max_queue_size=3, priority=1),
    )
    replay_high = scheduler.submit(
        key="replay",
        run_id="high",
        payload={},
        policy=SchedulingPolicy(mode="replay_process_all", max_queue_size=3, priority=10),
    )
    first_coalesce = scheduler.submit(
        key="background",
        run_id="bg-1",
        payload={"version": 1},
        policy=SchedulingPolicy(mode="coalesce"),
    )
    second_coalesce = scheduler.submit(
        key="background",
        run_id="bg-2",
        payload={"version": 2},
        policy=SchedulingPolicy(mode="coalesce"),
    )
    latest = scheduler.submit(
        key="camera-live",
        run_id="latest-1",
        payload={},
        policy=SchedulingPolicy(mode="live_latest_only"),
    )
    active = scheduler.submit(
        key="same-camera",
        run_id="active",
        payload={},
        policy=SchedulingPolicy(mode="drop_not_queue"),
    )
    dropped = scheduler.submit(
        key="same-camera",
        run_id="dropped",
        payload={},
        policy=SchedulingPolicy(mode="drop_not_queue"),
    )
    backend_first = scheduler.submit(
        key="backend-a",
        run_id="b1",
        payload={},
        policy=SchedulingPolicy(
            mode="run_immediately",
            backend_key="vision",
            max_backend_concurrency=1,
        ),
    )
    backend_second = scheduler.submit(
        key="backend-b",
        run_id="b2",
        payload={},
        policy=SchedulingPolicy(
            mode="run_immediately",
            backend_key="vision",
            max_backend_concurrency=1,
        ),
    )

    assert fanout.action == "accept"
    assert replay_low.action == "queue"
    assert replay_high.action == "queue"
    assert scheduler.queued_run_ids("replay") == ["high", "low"]
    assert first_coalesce.action == "queue"
    assert second_coalesce.action == "coalesce"
    assert scheduler.queued_run_ids("background") == ["bg-2"]
    assert latest.action == "queue_latest"
    assert scheduler.queued_run_ids("camera-live") == ["latest-1"]
    assert active.action == "accept"
    assert dropped.action == "drop"
    assert backend_first.action == "accept"
    assert backend_second.action == "drop"
    assert "concurrency limit" in backend_second.reason


def test_workflow_result_exposes_all_uncertainty_states():
    statuses = [
        "unknown",
        "uncertain",
        "partial",
        "low_confidence",
        "insufficient_evidence",
        "requires_user_input",
        "external_tool_unavailable",
    ]

    results = [
        WorkflowResult(status=status, uncertainty_reason=f"{status} reason", confidence=0.4)
        for status in statuses
    ]

    assert [result.status for result in results] == statuses
    assert all(result.uncertainty_reason for result in results)


async def test_evaluation_controller_retries_one_capability_with_criticism_then_accepts():
    class Evaluator:
        async def evaluate(self, _context, latest, _history):
            if latest.output["quality"] == "good":
                return EvaluationDecision(action="accept", rationale="quality fixed")
            return EvaluationDecision(
                action="retry_capability",
                target_capability="draft_answer",
                rationale="answer omitted important facts",
                criticism=CriticismEnvelope(
                    observed="single fact",
                    expected="all facts",
                    severity="high",
                    target_capability="draft_answer",
                    user_visible_effect="would miss study coverage",
                ),
            )

    def draft_answer(_context, payload):
        return {
            "quality": "good" if "_criticism" in payload else "bad",
            "criticism_seen": "_criticism" in payload,
        }

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="draft_answer", kind="deterministic"), draft_answer)
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    profile = WorkflowProfile(
        workflow_type="quality_retry",
        limits=RuntimeLimits(max_retries=1, max_retrace=0),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="quality_retry", objective="produce complete answer")

    result = await EvaluationController(runtime, Evaluator()).run(
        "draft_answer",
        {"source": "facts"},
        capability_context_for_goal(goal, plan=plan),
    )

    assert result.status == "completed"
    assert result.output["quality"] == "good"
    assert result.output["criticism_seen"] is True
    assert [item.status for item in result.capability_results] == ["accepted", "accepted"]
    assert any(
        event.node == "workflow_evaluator"
        and event.decision == "retry_capability"
        and event.metadata["criticism"]["expected"] == "all facts"
        for event in trace.events
    )


async def test_evaluation_controller_retraces_to_earlier_capability_with_criticism():
    class Evaluator:
        async def evaluate(self, _context, latest, history):
            if latest.output["coverage"] == "complete":
                return EvaluationDecision(action="accept", rationale="coverage complete")
            return EvaluationDecision(
                action="retrace_to",
                retrace_to="plan_scope",
                rationale="scenario was too narrow",
                criticism=CriticismEnvelope(
                    observed="sampled one item",
                    expected="cover every annex",
                    severity="blocking",
                    target_capability="plan_scope",
                    user_visible_effect="would omit source material",
                ),
            )

    def plan_scope(_context, payload):
        return {
            "coverage": "complete" if "_criticism" in payload else "partial",
            "source": payload["source"],
        }

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="plan_scope", kind="deterministic"), plan_scope)
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    profile = WorkflowProfile(
        workflow_type="quality_retrace",
        limits=RuntimeLimits(max_retries=0, max_retrace=1),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="quality_retrace", objective="cover source material")

    result = await EvaluationController(runtime, Evaluator()).run(
        "plan_scope",
        {"source": "annexes"},
        capability_context_for_goal(goal, plan=plan),
        retrace_payloads={"plan_scope": {"source": "annexes"}},
    )

    assert result.status == "completed"
    assert result.output["coverage"] == "complete"
    assert len(result.capability_results) == 2
    assert any(event.node == "workflow_evaluator" and event.decision == "retrace_to" for event in trace.events)


async def test_evaluation_controller_falls_back_or_fails_when_policy_exhausts():
    class AlwaysFallback:
        async def evaluate(self, _context, _latest, _history):
            return EvaluationDecision(
                action="fallback",
                target_capability="fallback_answer",
                rationale="primary output unusable",
                criticism=CriticismEnvelope(
                    observed="broken output",
                    expected="safe fallback",
                    target_capability="fallback_answer",
                    user_visible_effect="fallback visible to user",
                ),
            )

    class AlwaysRetry:
        async def evaluate(self, _context, _latest, _history):
            return EvaluationDecision(
                action="retry_capability",
                target_capability="primary_answer",
                rationale="retry cap reached",
            )

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="primary_answer", kind="deterministic"), lambda _context, payload: {"primary": payload})
    registry.register(CapabilitySpec(name="fallback_answer", kind="deterministic"), lambda _context, payload: {"fallback": payload})
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())
    profile = WorkflowProfile(
        workflow_type="quality_fallback",
        limits=RuntimeLimits(max_retries=0, max_retrace=0),
    )
    plan = RuntimePlanCompiler().compile(profile, registry)
    goal = WorkflowGoal(workflow_type="quality_fallback", objective="fallback safely")
    context = capability_context_for_goal(goal, plan=plan)

    fallback = await EvaluationController(runtime, AlwaysFallback()).run(
        "primary_answer",
        {"source": "bad"},
        context,
    )
    failed = await EvaluationController(runtime, AlwaysRetry()).run(
        "primary_answer",
        {"source": "bad"},
        context,
    )

    assert fallback.status == "completed"
    assert "fallback" in fallback.output
    assert fallback.capability_results[-1].output["fallback"]["_criticism"]["expected"] == "safe fallback"
    assert failed.status == "failed"
    assert failed.error == "retry cap reached"


async def test_toy_summary_example_runs_through_public_capability_runtime():
    output, trace = await run_toy_summary("portable workflow engine")

    assert output.summary == "portable"
    assert output.word_count == 3
    assert any(event.node == "toy_summary" and event.decision == "accepted" for event in trace.events)


async def test_toy_calendar_builder_rejects_overbooked_plan_and_retraces():
    payload = CalendarBuilderInput(
        available_hours=4,
        energy_level="medium",
        focus_projects=["ppla", "health"],
        tasks=[
            CalendarTask(name="PPLA air law", hours=2, priority=5, focus_project="ppla"),
            CalendarTask(name="Workout", hours=1.5, priority=4, focus_project="health"),
            CalendarTask(name="Email cleanup", hours=2, priority=2),
            CalendarTask(name="Deep work", hours=2, priority=5, focus_project="ppla"),
        ],
    )

    plan, trace, retraced = await run_toy_calendar_builder(payload)

    assert retraced is True
    assert plan.total_hours <= payload.available_hours
    assert {"PPLA air law", "Deep work"}.issubset({task.name for task in plan.scheduled})
    assert "Email cleanup" in plan.deferred
    assert any(event.node == "evaluate_calendar" and event.decision == "rejected" for event in trace.events)


async def test_toy_site_audit_pilot_fans_out_adjudicates_and_writes_report():
    report, trace = await run_toy_site_audit_pilot(
        SiteAuditInput(
            url="https://example.test",
            rubric=["critical flows", "accessibility"],
            scenarios=["checkout form", "navigation smoke"],
        )
    )

    assert report.write_status == "created"
    assert {finding.title for finding in report.accepted_findings} == {
        "Primary form lacks visible error state",
        "Hero image has empty alt text",
    }
    assert all(finding.evidence_ref_id for finding in report.accepted_findings)
    assert any(event.node == "run_site_scenario" and event.decision == "accepted" for event in trace.events)
    assert any(event.node == "write_audit_report" and event.decision == "accepted" for event in trace.events)


async def test_toy_inventory_pilot_uses_evidence_refs_and_external_write():
    contents = EvidenceRef(
        role="contents",
        uri="frame://camera-1/latest",
        media_type="image/jpeg",
        summary="Open closet shelf with small supplies",
    )

    result, trace = await run_toy_inventory_pilot(
        InventoryPilotInput(location_hint=None, evidence_refs=[contents])
    )

    assert result.used_provisional_location is True
    assert result.write_status == "created"
    assert {observation.item for observation in result.observations} == {"glue", "USB-C cable"}
    assert all(observation.location == "unknown location" for observation in result.observations)
    assert all(observation.evidence_ref_ids == [contents.ref_id] for observation in result.observations)
    # Evidence is carried as refs, never raw bytes, all the way to the external write.
    assert "image_data" not in result.model_dump_json()
    assert any(event.node == "extract_inventory_items" and event.decision == "accepted" for event in trace.events)
    assert any(event.node == "write_inventory_observations" for event in trace.events)


async def test_workflow_runner_collects_usage_summary():
    goal = WorkflowGoal(workflow_type="test_workflow", objective="Track usage")

    result = await WorkflowRunner().run(UsageGraph(), {}, goal=goal)

    summary = result["usage_summary"]
    assert summary.text_call_count == 1
    assert summary.input_tokens == 10
    assert summary.output_tokens == 5
    assert summary.estimated_usd == 0.00003
    assert summary.events[0].request_id == "req-unit"


async def test_workflow_runner_blocks_text_calls_over_budget():
    # v0.9 clean contract: ceilings come from typed limits (engine_context), never from
    # host-config attributes; config remains a pricing/model source only.
    goal = WorkflowGoal(workflow_type="test_workflow", objective="Track usage")
    engine_context = SimpleNamespace(limits=RuntimeLimits(max_text_calls=1))

    with pytest.raises(WorkflowBudgetExceeded):
        await WorkflowRunner().run(
            UsageGraph(calls=2), {"engine_context": engine_context}, goal=goal
        )


async def test_workflow_runner_aborts_after_estimated_usd_cap_before_second_paid_call():
    goal = WorkflowGoal(workflow_type="test_workflow", objective="Prove live-spend cap")
    graph = UsageGraph(calls=2)
    engine_context = SimpleNamespace(
        limits=RuntimeLimits(
            max_text_calls=16,
            max_image_calls=1,
            max_estimated_usd=0.00002,
        )
    )

    with pytest.raises(
        WorkflowBudgetExceeded,
        match=r"\$0\.000030 > \$0\.000020",
    ):
        await WorkflowRunner().run(
            graph, {"engine_context": engine_context}, goal=goal
        )

    assert graph.llm.calls == 1


def test_default_price_table_covers_current_anki_text_model_snapshot():
    assert estimate_cost_usd(
        "gpt-5.4-mini-2026-03-17",
        "chat",
        input_tokens=1_000,
        output_tokens=100,
    ) == 0.0012


def test_estimate_cost_uses_cached_text_input_price():
    assert estimate_cost_usd(
        "gpt-5.4-mini",
        "chat",
        input_tokens=1_000,
        output_tokens=100,
        input_details={"cache_read": 800},
    ) == 0.00066


def test_estimate_cost_uses_cached_image_input_prices():
    assert estimate_cost_usd(
        "gpt-image-2",
        "image",
        input_tokens=1_200,
        output_tokens=100,
        input_details={
            "text_tokens": 200,
            "text_cached_tokens": 100,
            "image_tokens": 1_000,
            "image_cached_tokens": 600,
        },
        output_details={"image_tokens": 100},
    ) == 0.008025


def test_usage_summary_displays_cached_input_tokens():
    summary = WorkflowUsageSummary(
        events=[
            WorkflowUsageEvent(
                node="type_gate",
                operation="chat",
                input_tokens=1_000,
                output_tokens=100,
                total_tokens=1_100,
                input_token_details={"cache_read": 800},
                estimated_usd=0.00066,
            ),
            WorkflowUsageEvent(
                node="image_generation",
                operation="image",
                input_tokens=2_000,
                output_tokens=300,
                total_tokens=2_300,
                input_token_details={"text_cached_tokens": 100, "image_cached_tokens": 600},
                estimated_usd=0.0141,
            ),
        ]
    )

    text = format_usage_summary(summary)

    assert "AI usage:" in text
    assert "node" in text
    assert "cache" in text
    assert "type_gate" in text
    assert "image_generation" in text
    assert "txt" in text
    assert "img" in text
    assert "800" in text
    assert "700" in text
    assert "$0.0007" in text
    assert "$0.0141" in text
    assert "total: 1 text, 1 image, 3k in, 1.5k cached, 400 out" in text
    assert "billed (API): $0.0148" in text
    assert "subscription" not in text  # pure-metered run: absent is not unknown


def test_usage_summary_separates_metered_and_subscription_notional_costs():
    summary = WorkflowUsageSummary(
        events=[
            WorkflowUsageEvent(
                node="metered_call",
                operation="chat",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                estimated_usd=0.25,
            ),
            WorkflowUsageEvent(
                node="subscription_call",
                operation="chat",
                cost_class="subscription_notional",
                input_tokens=20,
                output_tokens=10,
                total_tokens=30,
                notional_usd=0.42,
                provider_reported_notional_usd=0.42,
                notional_pricing=NotionalPricingResult(
                    source="provider_reported",
                    amount_usd=0.42,
                    catalog_version="provider-reported",
                ),
            ),
        ]
    )

    text = format_usage_summary(summary)

    assert summary.metered_usd == 0.25
    assert summary.estimated_usd == 0.25
    assert summary.notional_usd == 0.42
    assert "billed (API): $0.2500" in text
    assert "subscription: ~$0.4200 plan value, no extra charge" in text


def test_usage_summary_displays_tool_character_count_when_present():
    summary = WorkflowUsageSummary(
        events=[
            WorkflowUsageEvent(
                provider="elevenlabs",
                node="generate_voice",
                operation="tool",
                model="eleven_multilingual_v2",
                input_token_details={"characters": 42},
            )
        ]
    )

    text = format_usage_summary(summary)

    assert "generate_voice" in text
    assert "tool" in text
    assert "42ch" in text
    assert "total: 0 text, 0 image, 1 tool, 0 in, 0 cached, 0 out, 42 chars" in text
    assert "billed (API): ?" in text  # metered calls happened, price unknown — honestly unknown
    assert "subscription" not in text  # no subscription events -> no plan-value segment


async def test_structured_llm_node_splits_static_prefix_and_dynamic_tail():
    llm = FakeLLM(
        [
            """{
  "instrument_name": "anki_generation",
  "rationale": "The goal asks for cards.",
  "confidence": 0.88
}"""
        ]
    )
    node = StructuredLLMNode(
        name="unit_structured_node",
        config=object(),
        output_model=WorkflowDecision,
        static_prompt_template="Stable rules.\n{format_instructions}",
        dynamic_prompt_template="Dynamic goal: {goal}",
        dynamic_input_variables=["goal"],
        model_attr="UNIT_MODEL",
        llm=llm,
    )

    decision = await node.run({"goal": "make cards"})

    assert decision.instrument_name == "anki_generation"
    assert isinstance(llm.messages[0][0], SystemMessage)
    assert isinstance(llm.messages[0][1], HumanMessage)
    assert "Stable rules" in llm.messages[0][0].content
    assert "instrument_name" in llm.messages[0][0].content
    assert "make cards" not in llm.messages[0][0].content
    assert llm.messages[0][1].content == "Dynamic goal: make cards"


def test_structured_llm_node_rejects_mixed_prompt_modes():
    with pytest.raises(ValueError, match="prompt_template cannot be combined"):
        StructuredLLMNode(
            name="unit_structured_node",
            config=object(),
            output_model=WorkflowDecision,
            prompt_template="Full prompt {goal}",
            input_variables=["goal"],
            static_prompt_template="Stable rules.\n{format_instructions}",
            dynamic_prompt_template="Dynamic goal: {goal}",
            dynamic_input_variables=["goal"],
            model_attr="UNIT_MODEL",
            llm=FakeLLM([]),
        )


def test_structured_llm_node_requires_injected_llm_or_factory():
    node = StructuredLLMNode(
        name="unit_structured_node",
        config=object(),
        output_model=WorkflowDecision,
        static_prompt_template="Stable rules.\n{format_instructions}",
        dynamic_prompt_template="Dynamic goal: {goal}",
        dynamic_input_variables=["goal"],
        model_attr="UNIT_MODEL",
    )

    with pytest.raises(ValueError, match="llm or llm_factory"):
        _ = node.llm


async def test_workflow_instrument_registry_runs_registered_async_handler():
    registry = WorkflowInstrumentRegistry()
    spec = WorkflowInstrumentSpec(
        name="anki_generation",
        kind="workflow",
        description="Generate Anki cards",
    )
    registry.register(spec, async_handler)
    goal = WorkflowGoal(workflow_type="anki_generation", objective="Generate cards")

    result = await registry.get("anki_generation").run(goal, {"count": 1})

    assert registry.list_specs() == [spec]
    assert result == {"goal": "Generate cards", "payload": {"count": 1}}


def test_workflow_instrument_registry_rejects_duplicate_names():
    registry = WorkflowInstrumentRegistry()
    spec = WorkflowInstrumentSpec(name="image_generation", kind="tool")
    registry.register(spec, lambda goal, payload: payload)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec, lambda goal, payload: payload)


def test_workflow_artifact_cleanup_respects_cleanup_flag(tmp_path):
    remove_me = tmp_path / "remove.png"
    keep_me = tmp_path / "keep.png"
    remove_me.write_bytes(b"generated")
    keep_me.write_bytes(b"owned")

    removed = cleanup_artifacts(
        [
            WorkflowArtifact(path=str(remove_me), kind="media", cleanup_on_failure=True),
            WorkflowArtifact(path=str(keep_me), kind="media", cleanup_on_failure=False),
        ]
    )

    assert removed == [str(remove_me)]
    assert not remove_me.exists()
    assert keep_me.exists()


async def test_workflow_supervisor_routes_by_workflow_type_without_planner():
    registry = WorkflowInstrumentRegistry()
    registry.register(WorkflowInstrumentSpec(name="anki_generation", kind="workflow"), async_handler)
    goal = WorkflowGoal(workflow_type="anki_generation", objective="Generate cards")

    result = await WorkflowSupervisor(registry).run(goal, {"count": 1})

    assert result.decision.instrument_name == "anki_generation"
    assert result.result == {"goal": "Generate cards", "payload": {"count": 1}}


async def test_engine_runs_toy_non_anki_workflow_through_supervisor_and_runner():
    async def toy_workflow(goal, payload):
        return await WorkflowRunner().run(
            ToySummaryGraph(),
            {"text": payload["text"]},
            goal=goal,
        )

    registry = WorkflowInstrumentRegistry()
    registry.register(
        WorkflowInstrumentSpec(
            name="toy_summary",
            kind="workflow",
            description="Summarize text without Anki-specific schemas",
        ),
        toy_workflow,
    )
    goal = WorkflowGoal(workflow_type="toy_summary", objective="summarize arbitrary text")

    result = await WorkflowSupervisor(registry).run(goal, {"text": "portable workflow engine"})

    assert result.decision.instrument_name == "toy_summary"
    assert result.result["workflow_context"].workflow_type == "toy_summary"
    assert result.result["summary"] == "portable"
    assert result.result["word_count"] == 3


async def test_workflow_supervisor_uses_decision_planner():
    registry = WorkflowInstrumentRegistry()
    registry.register(WorkflowInstrumentSpec(name="anki_generation", kind="workflow"), async_handler)
    planner = FakeDecisionPlanner(WorkflowDecision(instrument_name="anki_generation", rationale="best match"))
    goal = WorkflowGoal(workflow_type="auto", objective="Generate cards")

    result = await WorkflowSupervisor(registry, decision_planner=planner).run(goal, {"count": 1})

    assert result.decision.rationale == "best match"
    assert len(planner.calls) == 1
    assert result.result["goal"] == "Generate cards"


async def test_workflow_decision_planner_repairs_bad_json_once():
    registry = WorkflowInstrumentRegistry()
    registry.register(WorkflowInstrumentSpec(name="anki_generation", kind="workflow"), async_handler)
    llm = FakeLLM(
        [
            "not json",
            """{
  "instrument_name": "anki_generation",
  "rationale": "The goal asks for cards.",
  "confidence": 0.88
}""",
        ]
    )
    planner = WorkflowDecisionPlanner(config=object(), llm=llm)
    goal = WorkflowGoal(workflow_type="auto", objective="make Anki cards from this")

    decision = await planner.plan(goal, registry, {"content": "Paris is in France"})

    assert decision.instrument_name == "anki_generation"
    assert len(llm.messages) == 2
    assert "previous workflow-supervisor output was invalid" in llm.messages[1][0].content


# ======================================================================================
# Executable workflow engine — P1: WorkflowDefinition + WorkflowBuilder (AC-1)
# ======================================================================================

from ai_workflow_engine import (
    LocalWaitPolicy,  # noqa: E402
    END,
    Fallback,
    Replan,
    Retrace,
    Retry,
    SubworkflowRef,
    WorkflowBuilder,
    WorkflowDefinition,
    Transition,
    WorkflowNode,
    WorkflowValidationError,
)


def _home_inventory_builder() -> WorkflowBuilder:
    """The canonical builder example from the engine contract (all branch targets defined)."""

    return (
        WorkflowBuilder("home_inventory", description="GoPro inventory toy")
        .step("select_evidence")
        .branch(
            "evidence_quality_gate",
            {
                "enough": "extract_items",
                "ambiguous": "ask_location",
                "bad": "fallback_or_fail",
            },
        )
        .step("extract_items")
        .evaluate("quality_gate", on_reject=Retrace("select_evidence"))
        .step("write_inventory")
        .human("ask_location", wait_policy=LocalWaitPolicy())
        .step("fallback_or_fail")
    )


def test_control_directives_accept_positional_args():
    assert Retry(3).max_attempts == 3
    assert Retrace("select_evidence").target == "select_evidence"
    assert Fallback("fallback_cap").capability == "fallback_cap"
    assert SubworkflowRef("child_flow").workflow_id == "child_flow"


def test_workflow_builder_builds_valid_definition():
    definition = _home_inventory_builder().build()

    assert isinstance(definition, WorkflowDefinition)
    assert definition.workflow_id == "home_inventory"
    assert definition.entry == "select_evidence"
    assert set(definition.node_ids()) == {
        "select_evidence",
        "evidence_quality_gate",
        "extract_items",
        "quality_gate",
        "write_inventory",
        "ask_location",
        "fallback_or_fail",
    }
    assert definition.node("select_evidence").capability == "select_evidence"
    assert definition.node("quality_gate").target_capability == "extract_items"
    assert isinstance(definition.node("quality_gate").on_reject, Retrace)


def test_workflow_builder_wires_sequential_and_branch_edges():
    definition = _home_inventory_builder().build()
    transitions = {(t.source, t.target, t.label, t.policy) for t in definition.transitions}

    assert ("select_evidence", "evidence_quality_gate", None, "always") in transitions
    assert ("evidence_quality_gate", "extract_items", "enough", "decision") in transitions
    assert ("evidence_quality_gate", "ask_location", "ambiguous", "decision") in transitions
    assert ("evidence_quality_gate", "fallback_or_fail", "bad", "decision") in transitions
    assert not any(
        t.source == "evidence_quality_gate" and t.policy == "always" for t in definition.transitions
    )
    assert ("extract_items", "quality_gate", None, "always") in transitions
    assert ("quality_gate", "write_inventory", None, "always") in transitions
    assert ("write_inventory", END, None, "always") in transitions
    assert ("ask_location", END, None, "always") in transitions
    assert ("fallback_or_fail", END, None, "always") in transitions
    # the machine is complete data: evaluator control routes are materialized transitions
    assert ("quality_gate", "write_inventory", "accept", "on_accept") in transitions
    assert ("quality_gate", "select_evidence", "retrace", "on_reject") in transitions


def test_workflow_builder_rejects_unknown_branch_target():
    with pytest.raises(WorkflowValidationError) as exc:
        (
            WorkflowBuilder("bad_branch")
            .step("a")
            .branch("gate", {"ok": "missing_node"})
            .build()
        )
    assert any("unknown node: missing_node" in e for e in exc.value.errors)


def test_workflow_builder_rejects_duplicate_node_ids():
    with pytest.raises(WorkflowValidationError) as exc:
        WorkflowBuilder("dupe").step("a").step("a").build()
    assert any("duplicate node id: a" in e for e in exc.value.errors)


def test_workflow_builder_rejects_empty_workflow():
    with pytest.raises(WorkflowValidationError):
        WorkflowBuilder("empty").build()


def test_workflow_definition_flags_unsupported_node_kind():
    definition = WorkflowDefinition(
        workflow_id="bogus",
        nodes=[WorkflowNode(id="a", kind="teleport")],  # type: ignore[arg-type]
        transitions=[Transition(source="a", target=END)],
        entry="a",
    )
    errors = definition.validate_graph()
    assert any("unsupported kind: teleport" in e for e in errors)


def test_workflow_evaluate_retrace_target_must_exist():
    with pytest.raises(WorkflowValidationError) as exc:
        (
            WorkflowBuilder("bad_retrace")
            .step("extract")
            .evaluate("gate", on_reject=Retrace("does_not_exist"))
            .build()
        )
    assert any("retrace target unknown: does_not_exist" in e for e in exc.value.errors)


def test_workflow_definition_flags_node_shape_validation_concerns():
    definition = WorkflowDefinition(
        workflow_id="bad_node_shapes",
        nodes=[
            WorkflowNode(id="empty_branch", kind="branch"),
            WorkflowNode(id="branch", kind="branch", branches={"missing": "ghost"}),
            WorkflowNode(id="fan", kind="fanout"),
            WorkflowNode(id="eval", kind="evaluate", on_reject=Retrace("ghost")),
            WorkflowNode(id="sub", kind="subworkflow"),
            WorkflowNode(
                id="plan",
                kind="planner",
                max_tasks=0,
                max_replans=-1,
                max_plan_depth=0,
                max_total_planned_tasks=0,
            ),
            WorkflowNode(id="plan_recursive", kind="planner", max_plan_depth=2, execution="fanout"),
            WorkflowNode(id="not_planner"),
            WorkflowNode(
                id="eval_replan",
                kind="evaluate",
                evaluator="gate",
                target_capability="target",
                on_reject=Replan("not_planner"),
            ),
        ],
        transitions=[],
        entry="empty_branch",
    )

    errors = definition.validate_graph()

    assert any("branch node 'empty_branch' has no branches" in e for e in errors)
    assert any("branch node 'branch' label 'missing' -> unknown node: ghost" in e for e in errors)
    assert any("fanout node 'fan' missing fan_items_key" in e for e in errors)
    assert any("fanout node 'fan' missing item_capability" in e for e in errors)
    assert any("evaluate node 'eval' missing evaluator" in e for e in errors)
    assert any("evaluate node 'eval' missing target_capability" in e for e in errors)
    assert any("evaluate node 'eval' retrace target unknown: ghost" in e for e in errors)
    assert any("subworkflow node 'sub' missing subworkflow ref" in e for e in errors)
    assert any("planner node 'plan' max_tasks must be >= 1" in e for e in errors)
    assert any("planner node 'plan' max_replans must be >= 0" in e for e in errors)
    assert any("planner node 'plan' max_plan_depth must be >= 1" in e for e in errors)
    assert any("planner node 'plan' max_total_planned_tasks must be >= 1" in e for e in errors)
    assert any(
        "planner node 'plan_recursive' recursive planning requires execution='sequential'" in e
        for e in errors
    )
    assert any("evaluate node 'eval_replan' replan target is not a planner: not_planner" in e for e in errors)


def test_workflow_definition_flags_transition_validation_concerns():
    definition = WorkflowDefinition(
        workflow_id="bad_transitions",
        nodes=[
            WorkflowNode(id="gate", kind="branch", branches={"again": "done", "done": END}),
            WorkflowNode(id="done"),
        ],
        transitions=[
            Transition(source="ghost", target="done"),
            Transition(source="gate", target="ghost"),
            Transition(source="gate", target="done", label="again", policy="decision", max_traversals=0),
            Transition(source="gate", target="done", policy="always", max_traversals=1),
            Transition(source="gate", target="done", policy="always", on_exhausted="done"),
            Transition(source="gate", target="done", label="again", policy="decision", on_exhausted="missing"),
            Transition(source="gate", target="done", label="again", policy="decision", on_exhausted="again"),
        ],
        entry="gate",
    )

    errors = definition.validate_graph()

    assert any("transition from unknown node: ghost" in e for e in errors)
    assert any("transition to unknown node: ghost" in e for e in errors)
    assert any("transition 'gate' -> 'done' max_traversals must be >= 1" in e for e in errors)
    assert any("pre-set gates are enforced on decision transitions only" in e for e in errors)
    assert any("escape labels exist only on decision transitions" in e for e in errors)
    assert any("on_exhausted 'missing' is not a declared label of that branch" in e for e in errors)
    assert any("on_exhausted must differ from its own label" in e for e in errors)


def test_workflow_definition_flags_cycle_gate_validation_concern():
    definition = WorkflowDefinition(
        workflow_id="bad_cycle",
        nodes=[WorkflowNode(id="a"), WorkflowNode(id="b")],
        transitions=[
            Transition(source="a", target="b"),
            Transition(source="b", target="a"),
        ],
        entry="a",
    )
    errors = definition.validate_graph()
    assert any("unbounded cycle" in e for e in errors)

    bounded = WorkflowDefinition(
        workflow_id="bounded_cycle",
        nodes=[
            WorkflowNode(id="draft"),
            WorkflowNode(id="gate", kind="branch", branches={"again": "draft", "done": END}),
        ],
        transitions=[
            Transition(source="draft", target="gate"),
            Transition(source="gate", target="draft", label="again", policy="decision", max_traversals=1),
            Transition(source="gate", target=END, label="done", policy="decision"),
        ],
        entry="draft",
    )
    assert bounded.validate_graph() == []


# ======================================================================================
# Executable workflow engine — P2: WorkflowExecutor + WorkflowEngine + DI (AC-2, AC-3, AC-4)
# ======================================================================================

from pydantic import BaseModel as _BaseModel  # noqa: E402
from ai_workflow_engine import (  # noqa: E402
    BranchDecision,
    NodeResult,
    StructuredLLMNode,
    WorkflowConfigBundle,
    WorkflowEngine,
    WorkflowEngineBuilder,
    WorkflowExecutor,
    WorkflowRunResult,
)
from ai_workflow_engine.workflow import Transition as _Edge  # noqa: E402


def _shout_engine() -> WorkflowEngine:
    builder = WorkflowEngineBuilder()
    builder.register_capability("upper", lambda ctx, p: p.upper(), kind="deterministic")
    builder.register_capability("exclaim", lambda ctx, p: p + "!", kind="deterministic")
    builder.register_workflow(WorkflowBuilder("shout").step("upper").step("exclaim").build())
    return builder.build()


async def test_executor_runs_deterministic_sequential_workflow():
    engine = _shout_engine()
    result = await engine.run("shout", "hi")

    assert isinstance(result, WorkflowRunResult)
    assert result.status == "completed"
    assert result.output == "HI!"
    # Result envelope carries status, output, node records, usage summary, and trace.
    assert [r.node_id for r in result.node_results] == ["upper", "exclaim"]
    assert all(r.status == "accepted" for r in result.node_results)
    assert result.usage is not None
    assert any(e.node == "upper" for e in result.trace)


async def test_executor_runs_structured_llm_step_with_fake_llm():
    class Greeting(_BaseModel):
        greeting: str

    node = StructuredLLMNode(
        name="greet",
        config=object(),
        output_model=Greeting,
        prompt_template="Greet {name}.",
        input_variables=["name"],
        llm=FakeLLM(['{"greeting": "hello world"}']),
    )

    async def greet(ctx, payload):
        return await node.run({"name": payload["name"]})

    engine = (
        WorkflowEngineBuilder()
        .register_capability("greet", greet, kind="llm")
        .register_workflow(WorkflowBuilder("greeter").step("greet").build())
        .build()
    )
    result = await engine.run("greeter", {"name": "Ada"})

    assert result.status == "completed"
    assert isinstance(result.output, Greeting)
    assert result.output.greeting == "hello world"


async def test_executor_fails_loudly_on_unsupported_node_kind():
    engine = WorkflowEngineBuilder().build()
    bogus = WorkflowDefinition(
        workflow_id="bogus",
        nodes=[WorkflowNode(id="a", kind="teleport")],  # type: ignore[arg-type]
        transitions=[_Edge(source="a", target=END)],
        entry="a",
    )
    result = await engine.run(bogus, {})

    assert result.status == "failed"
    assert "unsupported kind: teleport" in (result.error or "")
    # Loud: a trace event records the rejection; no node ran.
    assert any(e.decision == "rejected" for e in result.trace)
    assert result.node_results == []


async def test_executor_fails_loudly_on_missing_capability():
    engine = WorkflowEngineBuilder().build()
    engine.register_workflow(WorkflowBuilder("ghosted").step("ghost").build())
    result = await engine.run("ghosted", {})

    assert result.status == "failed"
    assert "unregistered capability: ghost" in (result.error or "")


async def test_engine_builder_register_pack_runs():
    class DoublerPack:
        def register(self, builder):
            builder.register_capability("double", lambda ctx, p: p * 2, kind="deterministic")
            builder.register_workflow(WorkflowBuilder("doubler").step("double").build())

    engine = WorkflowEngineBuilder().register_pack(DoublerPack()).build()
    result = await engine.run("doubler", 21)

    assert result.status == "completed"
    assert result.output == 42


def test_engine_from_config_swaps_profile_without_touching_workflow():
    # The SAME workflow definition gets different limits purely from config/profile.
    workflow = WorkflowBuilder("cfg_demo").step("noop").build()

    def make(limits) -> WorkflowEngine:
        bundle = WorkflowConfigBundle(
            profile=WorkflowProfile(workflow_type="cfg_demo", limits=limits)
        )
        engine = WorkflowEngine.from_config(bundle)
        engine.register_capability("noop", lambda ctx, p: p, kind="deterministic")
        engine.register_workflow(workflow)
        return engine

    tight = make(RuntimeLimits(max_steps=3, max_estimated_usd=0.10))
    loose = make(RuntimeLimits(max_steps=50, max_estimated_usd=5.0))

    assert tight._plan_for(workflow).limits.max_steps == 3
    assert tight._plan_for(workflow).limits.max_estimated_usd == 0.10
    assert loose._plan_for(workflow).limits.max_steps == 50
    assert loose._plan_for(workflow).limits.max_estimated_usd == 5.0


def _router_engine() -> WorkflowEngine:
    builder = WorkflowEngineBuilder()
    builder.register_capability(
        "route",
        lambda ctx, p: BranchDecision(label="big" if p["n"] > 10 else "small"),
        kind="deterministic",
    )
    builder.register_capability("big", lambda ctx, p: f"BIG:{p['n']}", kind="deterministic")
    builder.register_capability("small", lambda ctx, p: f"SMALL:{p['n']}", kind="deterministic")
    workflow = (
        WorkflowBuilder("router")
        .branch("route", {"big": "big_step", "small": "small_step"})
        .step("big_step", capability="big")
        .step("small_step", capability="small")
        .build()
    )
    builder.register_workflow(workflow)
    return builder.build()


async def test_executor_branch_routes_two_inputs_two_ways():
    engine = _router_engine()

    big = await engine.run("router", {"n": 20})
    assert big.status == "completed"
    assert big.output == "BIG:20"
    assert big.node("route").branch_label == "big"
    # Branch decision is traced.
    assert any(e.node == "route" and e.decision == "branch" for e in big.trace)

    small = await engine.run("router", {"n": 5})
    assert small.output == "SMALL:5"
    assert small.node("route").branch_label == "small"


async def test_executor_branch_invalid_label_fails_loudly():
    engine = (
        WorkflowEngineBuilder()
        .register_capability("route", lambda ctx, p: BranchDecision(label="nonsense"), kind="deterministic")
        .register_capability("ok", lambda ctx, p: "ok", kind="deterministic")
        .register_workflow(
            WorkflowBuilder("bad_router").branch("route", {"good": "ok_step"}).step("ok_step", capability="ok").build()
        )
        .build()
    )
    result = await engine.run("bad_router", {})

    assert result.status == "failed"
    assert "invalid label" in (result.error or "")
    assert any(e.node == "route" and e.metadata.get("valid") is False for e in result.trace)


def test_recursion_exhaustion_detected_by_type_not_lookalikes():
    """Task 1.5: langgraph is a declared dependency — detect its recursion error by TYPE.
    A lookalike (same class name, same magic string, foreign module) must NOT be swallowed
    into the consumer's recursion fallback."""

    from langgraph.errors import GraphRecursionError

    from ai_workflow_engine.engine.runner import WorkflowRunner

    assert WorkflowRunner._is_recursion_exhaustion(
        GraphRecursionError("hit GRAPH_RECURSION_LIMIT")
    )

    lookalike_cls = type("GraphRecursionError", (RuntimeError,), {})
    assert not WorkflowRunner._is_recursion_exhaustion(
        lookalike_cls("GRAPH_RECURSION_LIMIT reached")
    )


# --- task 1.2: input_key validation — build-time + run-time fail-loud -------------------


def test_workflow_definition_flags_unknown_input_key():
    with pytest.raises(WorkflowValidationError, match="input_key 'tpyo'"):
        WorkflowBuilder("ik_typo").step("a").step("b", input_key="tpyo").build()


def test_input_key_accepts_node_ids_output_aliases_and_original_input():
    definition = (
        WorkflowBuilder("ik_ok")
        .step("a", output_key="alias")
        .step("b", input_key="a")
        .step("c", input_key="alias")
        .step("d", input_key="__input__")
        .build()
    )

    assert definition.validate_graph() == []


async def test_input_key_of_node_skipped_by_branch_fails_loudly():
    """Declared-but-not-run on this path: pre-1.2 this silently fed None as the payload."""

    engine = (
        WorkflowEngineBuilder()
        .register_capability(
            "route", lambda ctx, p: BranchDecision(label="fast"), kind="deterministic"
        )
        .register_capability("slow_cap", lambda ctx, p: "slow-result", kind="deterministic")
        .register_capability("fast_cap", lambda ctx, p: f"fast:{p}", kind="deterministic")
        .register_workflow(
            WorkflowBuilder("ik_skip")
            .branch("route", {"fast": "fast_step", "slow": "slow_step"})
            .step("slow_step", capability="slow_cap")
            .step("fast_step", capability="fast_cap", input_key="slow_step")
            .build()
        )
        .build()
    )

    with pytest.raises(KeyError, match="has no recorded output"):
        await engine.run("ik_skip", {"x": 1})


async def test_input_key_with_present_none_output_stays_valid():
    """A recorded None output is legitimate data — only a MISSING key is a wiring error."""

    saw = {}

    def _reader(ctx, payload):
        saw["payload"] = payload
        return "read-none-ok"

    engine = (
        WorkflowEngineBuilder()
        .register_capability(
            "none_cap", lambda ctx, p: _CapabilityResult(output=None), kind="deterministic"
        )
        .register_capability("reader", _reader, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("ik_none")
            .step("a", capability="none_cap")
            .step("b", capability="reader", input_key="a")
            .build()
        )
        .build()
    )

    result = await engine.run("ik_none", {"x": 1})

    assert result.status == "completed"
    assert saw["payload"] is None
    assert result.output == "read-none-ok"


# ======================================================================================
# Executable workflow engine — P3: evaluate (retry/retrace/fallback) + fanout (AC-5, AC-6)
# ======================================================================================

from ai_workflow_engine.models import CapabilityResult as _CapabilityResult  # noqa: E402


def _count(result, node_id) -> int:
    return sum(1 for r in result.node_results if r.node_id == node_id)


async def test_evaluate_retry_with_criticism_then_accepts():
    # draft returns "bad" until the engine re-feeds it with evaluator criticism, then "good".
    def draft(ctx, payload):
        return {"value": "good"} if isinstance(payload, dict) and payload.get("_criticism") else {"value": "bad"}

    def gate(ctx, payload):
        if payload.get("value") == "good":
            return _CapabilityResult(status="accepted", output=payload)
        return _CapabilityResult(status="rejected", output=payload, error="bad value",
                                 metadata={"criticism": "make it good"})

    engine = (
        WorkflowEngineBuilder()
        .register_capability("draft", draft, kind="deterministic")
        .register_capability("gate", gate, kind="deterministic")
        .register_workflow(WorkflowBuilder("retry_demo").step("draft").evaluate("gate", on_reject=Retry()).build())
        .build()
    )
    result = await engine.run("retry_demo", {"value": "seed"})

    assert result.status == "completed"
    assert result.output == {"value": "good"}
    assert _count(result, "draft") == 2  # engine re-ran the upstream step (no product loop)


async def test_evaluate_retrace_to_earlier_node_then_accepts():
    def ingest(ctx, payload):
        return {"seed": "rich"} if isinstance(payload, dict) and payload.get("_criticism") else {"seed": "poor"}

    def transform(ctx, payload):
        return {"result": f"{payload['seed']}-T"}

    def gate(ctx, payload):
        if "rich" in payload.get("result", ""):
            return _CapabilityResult(status="accepted", output=payload)
        return _CapabilityResult(status="rejected", output=payload, metadata={"criticism": "need richer seed"})

    workflow = (
        WorkflowBuilder("retrace_demo")
        .step("ingest")
        .step("transform")
        .evaluate("gate", on_reject=Retrace("ingest"))
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .register_capability("ingest", ingest, kind="deterministic")
        .register_capability("transform", transform, kind="deterministic")
        .register_capability("gate", gate, kind="deterministic")
        .register_workflow(workflow)
        .build()
    )
    result = await engine.run("retrace_demo", {})

    assert result.status == "completed"
    assert result.output == {"result": "rich-T"}
    assert _count(result, "ingest") == 2  # retraced to the earlier node and re-ran forward


async def test_evaluate_fallback_capability_on_reject():
    engine = (
        WorkflowEngineBuilder()
        .register_capability("draft", lambda ctx, p: {"value": "bad"}, kind="deterministic")
        .register_capability(
            "gate",
            lambda ctx, p: _CapabilityResult(status="rejected", output=p, metadata={"criticism": "fix"}),
            kind="deterministic",
        )
        .register_capability("repair", lambda ctx, p: {"value": "repaired"}, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("fallback_demo").step("draft").evaluate("gate", on_reject=Fallback("repair")).build()
        )
        .build()
    )
    result = await engine.run("fallback_demo", {})

    assert result.status == "completed"
    assert result.output == {"value": "repaired"}
    assert result.node("gate").fallback_reason is not None


async def test_evaluate_exhaustion_fails_without_infinite_loop():
    calls = {"draft": 0}

    def draft(ctx, payload):
        calls["draft"] += 1
        return {"value": "bad"}  # never good -> evaluator always rejects

    engine = (
        WorkflowEngineBuilder()
        .register_capability("draft", draft, kind="deterministic")
        .register_capability(
            "gate",
            lambda ctx, p: _CapabilityResult(status="rejected", output=p, metadata={"criticism": "nope"}),
            kind="deterministic",
        )
        .register_workflow(WorkflowBuilder("exhaust_demo").step("draft").evaluate("gate", on_reject=Retry()).build())
        .build()
    )
    result = await engine.run("exhaust_demo", {})

    assert result.status == "failed"
    assert "exhausted" in (result.error or "")
    # Bounded by limits (default max_retries=1 -> initial + 2 retries), never an infinite loop.
    assert calls["draft"] == 3


async def test_fanout_gather_isolates_partial_failure():
    def plan(ctx, payload):
        return [1, 2, 3]

    async def worker(ctx, item):
        if item == 2:
            await asyncio.sleep(0.2)  # exceeds the capability timeout -> isolated failure
        return item * 10

    workflow = (
        WorkflowBuilder("fanout_demo")
        .step("plan")
        .fanout("process", capability="worker", items_key="plan")
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .register_capability("plan", plan, kind="deterministic")
        .register_capability("worker", worker, kind="tool", timeout_s=0.05)
        .register_workflow(workflow)
        .build()
    )
    result = await engine.run("fanout_demo", None)

    # Parent receives partial results; the timed-out child is isolated as PARTIAL (v0.10:
    # a capability timeout is bounded/partial, not a hard failure).
    assert result.status == "partial"
    assert sorted(result.output) == [10, 30]
    fan_event = next(e for e in result.trace if e.decision == "fanout")
    assert fan_event.metadata["succeeded"] == 2
    assert fan_event.metadata["partial"] == 1
    assert fan_event.metadata["failed"] == 0
    # Per-child trace: each worker invocation recorded a start event.
    assert sum(1 for e in result.trace if e.node == "worker" and e.decision == "start") == 3


# ======================================================================================
# Executable workflow engine — P4: subworkflow-as-capability + human clarification (AC-7, AC-8)
# ======================================================================================

from ai_workflow_engine import (  # noqa: E402
    HumanClarificationCapability,
    InMemoryHumanClarificationChannel,
)


def _enrichment_engine() -> WorkflowEngine:
    child = WorkflowBuilder("enrich").step("enrich_step").build()
    parent = (
        WorkflowBuilder("main")
        .step("prep")
        .subworkflow("call_enrich", workflow=child)
        .step("finish")
        .build()
    )
    return (
        WorkflowEngineBuilder()
        .register_capability("prep", lambda ctx, p: {"x": p["x"] + 1}, kind="deterministic")
        .register_capability("enrich_step", lambda ctx, p: {"x": p["x"] * 10}, kind="deterministic")
        .register_capability("finish", lambda ctx, p: {"x": p["x"] + 100}, kind="deterministic")
        .register_workflow(child)
        .register_workflow(parent)
        .build()
    )


async def test_subworkflow_runs_as_capability_on_same_executor():
    engine = _enrichment_engine()
    result = await engine.run("main", {"x": 1})

    assert result.status == "completed"
    assert result.output == {"x": 120}  # prep(+1) -> enrich(*10) -> finish(+100)
    assert result.node("call_enrich").output == {"x": 20}
    # Trace shows the parent/child relationship and the child's own nodes.
    sub_event = next(e for e in result.trace if e.decision == "subworkflow")
    assert sub_event.metadata["parent_workflow"] == "main"
    assert sub_event.metadata["child_workflow"] == "enrich"
    assert any(e.node == "enrich_step" for e in result.trace)


async def test_subworkflow_failure_boundary_halts_parent():
    def boom(ctx, payload):
        raise ValueError("child exploded")

    child = WorkflowBuilder("enrich").step("enrich_step", capability="boom").build()
    parent = WorkflowBuilder("main").step("prep").subworkflow("call_enrich", workflow=child).step("finish").build()
    engine = (
        WorkflowEngineBuilder()
        .register_capability("prep", lambda ctx, p: p, kind="deterministic")
        .register_capability("boom", boom, kind="deterministic")
        .register_capability("finish", lambda ctx, p: {"reached": True}, kind="deterministic")
        .register_workflow(child)
        .register_workflow(parent)
        .build()
    )
    result = await engine.run("main", {"x": 1})

    assert result.status == "failed"
    assert result.node("finish") is None  # parent halted at the failed child boundary
    sub_event = next(e for e in result.trace if e.decision == "subworkflow")
    assert sub_event.metadata["child_status"] == "failed"


async def test_subworkflow_partial_boundary_never_turns_parent_green():
    finish_calls = 0

    async def slow(_ctx, payload):
        await asyncio.sleep(0.1)
        return payload

    def finish(_ctx, payload):
        nonlocal finish_calls
        finish_calls += 1
        return payload

    child = WorkflowBuilder("bounded_child").step("slow").build()
    parent = (
        WorkflowBuilder("partial_parent")
        .subworkflow("child", workflow=child)
        .step("finish")
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .register_capability("slow", slow, kind="deterministic", timeout_s=0.01)
        .register_capability("finish", finish, kind="deterministic")
        .register_workflow(child)
        .register_workflow(parent)
        .build()
    )

    result = await engine.run("partial_parent", {})

    assert finish_calls == 1, "partial preserves salvageable flow rather than acting as failure"
    assert result.status == "partial"
    assert result.node("child").status == "partial"
    assert result.node("child").error
    sub_event = next(event for event in result.trace if event.decision == "subworkflow")
    assert sub_event.metadata["child_status"] == "partial"
    assert sub_event.error


def _clarify_engine(channel: InMemoryHumanClarificationChannel) -> WorkflowEngine:
    human_cap = HumanClarificationCapability(channel, name="ask")
    workflow = WorkflowBuilder("clarify").human("ask", wait_policy=LocalWaitPolicy()).step("use_answer").build()
    builder = WorkflowEngineBuilder()
    builder.register_capability("ask", human_cap)
    builder.register_capability(
        "use_answer", lambda ctx, p: {"answer": getattr(p, "value", None)}, kind="deterministic"
    )
    builder.register_workflow(
        workflow,
        profile=WorkflowProfile(
            workflow_type="clarify",
            safety=SafetyPolicy(allowed_side_effects=["notification"]),
        ),
    )
    return builder.build()


async def test_human_clarification_pauses_when_no_answer():
    channel = InMemoryHumanClarificationChannel()
    engine = _clarify_engine(channel)
    result = await engine.run(
        "clarify",
        HumanClarificationRequest(question="Which room?", continue_without_answer=False),
    )

    assert result.status == "requires_user_input"
    assert result.node("use_answer") is None  # paused before downstream work
    assert any(e.decision == "pending" for e in result.trace)


async def test_human_clarification_resumes_from_submitted_answer():
    channel = InMemoryHumanClarificationChannel()
    engine = _clarify_engine(channel)
    request = HumanClarificationRequest(question="Which room?", continue_without_answer=False)
    channel.submit_answer(request.clarification_id, "living room")

    result = await engine.run("clarify", request)

    assert result.status == "completed"
    assert result.output == {"answer": "living room"}
    assert any(e.decision == "answered" for e in result.trace)


async def test_human_clarification_provisional_value_continues():
    channel = InMemoryHumanClarificationChannel()
    engine = _clarify_engine(channel)
    result = await engine.run(
        "clarify",
        HumanClarificationRequest(
            question="Which room?",
            continue_without_answer=True,
            default_value="unknown room",
        ),
    )

    assert result.status in ("completed", "partial")
    assert result.output == {"answer": "unknown room"}
    assert any(e.decision == "provisional" for e in result.trace)


# ======================================================================================
# Executable workflow engine — P5: scheduling + side-effect/privacy/budget gates (AC-9, AC-10)
# ======================================================================================


async def test_forbidden_side_effect_denied_before_handler_runs():
    called = {"n": 0}

    def writer(ctx, p):
        called["n"] += 1
        return "wrote"

    engine = (
        WorkflowEngineBuilder()
        .register_capability("writer", writer, kind="external", side_effects=["external_write"])
        .register_workflow(
            WorkflowBuilder("wf").step("writer").build(),
            profile=WorkflowProfile(workflow_type="wf", safety=SafetyPolicy(allowed_side_effects=[])),
        )
        .build()
    )
    result = await engine.run("wf", {})

    assert result.status == "failed"
    assert called["n"] == 0  # handler never invoked
    assert "external_write" in (result.error or "")
    assert any(e.decision == "denied" for e in result.trace)


async def test_raw_media_export_denied_then_allowed_by_node_flag():
    called = {"n": 0}

    def export_bytes(ctx, p):
        called["n"] += 1
        return "exported"

    # Denied: profile forbids raw media (the node flag can never override the deployment ceiling).
    denied_engine = (
        WorkflowEngineBuilder()
        .register_capability("export", export_bytes, kind="media", side_effects=["raw_media_export"])
        .register_workflow(
            WorkflowBuilder("noexp").step("export", allow_raw_media_export=True).build(),
            profile=WorkflowProfile(workflow_type="noexp", safety=SafetyPolicy(allowed_side_effects=[])),
        )
        .build()
    )
    denied = await denied_engine.run("noexp", {})
    assert denied.status == "failed"
    assert called["n"] == 0

    # Denied: profile permits raw media but the node did not opt in (double consent required).
    no_optin_engine = (
        WorkflowEngineBuilder()
        .register_capability("export", export_bytes, kind="media", side_effects=["raw_media_export"])
        .register_workflow(
            WorkflowBuilder("exp").step("export").build(),
            profile=WorkflowProfile(workflow_type="exp", safety=SafetyPolicy(allowed_side_effects=["raw_media_export"])),
        )
        .build()
    )
    no_optin = await no_optin_engine.run("exp", {})
    assert no_optin.status == "failed"
    assert called["n"] == 0

    # Allowed: profile permits raw media AND the node explicitly opts in.
    allowed_engine = (
        WorkflowEngineBuilder()
        .register_capability("export", export_bytes, kind="media", side_effects=["raw_media_export"])
        .register_workflow(
            WorkflowBuilder("exp2").step("export", allow_raw_media_export=True).build(),
            profile=WorkflowProfile(workflow_type="exp2", safety=SafetyPolicy(allowed_side_effects=["raw_media_export"])),
        )
        .build()
    )
    allowed = await allowed_engine.run("exp2", {})
    assert allowed.status == "completed"
    assert allowed.output == "exported"
    assert called["n"] == 1


async def test_budget_exhaustion_denies_metered_capability():
    called = {"n": 0}

    def paid(ctx, p):
        called["n"] += 1
        return "paid-result"

    engine = (
        WorkflowEngineBuilder()
        .register_capability("paid", paid, kind="llm", metered=True)
        .register_workflow(
            WorkflowBuilder("budget").step("paid").build(),
            profile=WorkflowProfile(workflow_type="budget", limits=RuntimeLimits(max_estimated_usd=0.0)),
        )
        .build()
    )
    result = await engine.run("budget", {})

    assert result.status == "failed"
    assert called["n"] == 0  # no paid call once the budget is exhausted
    assert "budget" in (result.error or "")


# ======================================================================================
# Executable workflow engine — P6: same-executor proof + guards (AC-11, AC-12, AC-13)
# ======================================================================================

import ast as _ast  # noqa: E402
import inspect as _inspect  # noqa: E402

from ai_workflow_engine.examples import (  # noqa: E402
    ToySummaryInput,
    build_demo_engine,
    run_toy_card_generation,
)


async def test_same_executor_runs_all_example_workflows():
    # ONE engine -> ONE WorkflowExecutor runs every example workflow (no per-workflow engine).
    engine = build_demo_engine()
    executor = engine.executor

    summary = await engine.run("toy_summary", ToySummaryInput(text="one two three"))
    calendar = await engine.run(
        "calendar_builder",
        CalendarBuilderInput(
            available_hours=4,
            energy_level="medium",
            tasks=[
                CalendarTask(name="A", hours=2, priority=5),
                CalendarTask(name="B", hours=3, priority=4),
            ],
        ),
        constraints={"available_hours": 4, "energy_level": "medium", "focus_projects": []},
    )
    audit = await engine.run("site_audit", SiteAuditInput(url="https://x.test", scenarios=["checkout form"]))
    inventory = await engine.run(
        "inventory_observation",
        InventoryPilotInput(location_hint="shelf", evidence_refs=[EvidenceRef(role="contents", uri="frame://x")]),
    )
    card = await engine.run("card_generation", "make an image card")

    assert summary.status == "completed"
    assert calendar.status == "completed"
    assert audit.status == "completed"
    assert inventory.status == "completed"
    assert card.status == "completed"
    # Proof: the SAME executor compiled and ran all of them.
    assert engine.executor is executor
    assert {
        "toy_summary",
        "calendar_builder",
        "site_audit",
        "inventory_observation",
        "card_generation",
    } <= {key[0] for key in engine.executor._compiler.compiled}


_FORBIDDEN_PRODUCT_ORCHESTRATION = [
    "runtime.invoke",
    "StateGraph",
    "add_conditional_edges",
    "gather_capabilities(",
    "WorkflowScheduler(",
    "EvaluationController(",
    ".submit(",
]


def test_examples_contain_no_product_orchestration_loops():
    # AC-12: package examples must run through the engine, never hand-roll orchestration.
    import ai_workflow_engine.examples as examples_module

    source = _inspect.getsource(examples_module)
    offenders = [pattern for pattern in _FORBIDDEN_PRODUCT_ORCHESTRATION if pattern in source]
    assert not offenders, f"examples.py hand-rolls engine mechanics: {offenders}"
    # Positive: examples DO go through the engine.
    assert "engine.run(" in source
    # No manual loops around capability calls in example functions.
    tree = _ast.parse(source)
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.While,)):
            raise AssertionError("examples.py must not contain manual while-loops over capabilities")


def test_manual_loop_guard_would_flag_a_product_mini_engine():
    # A hand-written runtime.invoke retry/branch loop must trip the guard.
    bad_source = """
async def bad_flow(runtime, ctx):
    result = await runtime.invoke("draft", {}, ctx)
    while result.status == "failed":
        result = await runtime.invoke("draft", {}, ctx)
    return result
"""
    offenders = [pattern for pattern in _FORBIDDEN_PRODUCT_ORCHESTRATION if pattern in bad_source]
    assert offenders, "guard failed to detect a hand-written product mini-engine"


def test_engine_public_api_is_product_neutral():
    # AC-13: engine public names may not leak product concepts (only examples/docs/workflow ids may).
    import ai_workflow_engine as engine_pkg

    product_terms = ["anki", "gopro", "mageqa", "telegram", "cloze", "browser", "flashcard"]
    offenders = [
        name
        for name in engine_pkg.__all__
        for term in product_terms
        if term in name.lower()
    ]
    assert not offenders, f"product-specific names in engine public API: {offenders}"
    # Public workflow models also stay neutral in their field names.
    for model in (WorkflowNode, WorkflowDefinition, Transition, NodeResult, WorkflowRunResult):
        fields = getattr(model, "model_fields", {})
        leaked = [f for f in fields for term in product_terms if term in f.lower()]
        assert not leaked, f"{model.__name__} leaks product field names: {leaked}"


def test_format_trace_events_renders_flow_decisions_and_usage():
    from ai_workflow_engine import format_trace_events
    from ai_workflow_engine.models import WorkflowTraceEvent

    events = [
        WorkflowTraceEvent(
            node="plan_card_type", decision="basic", elapsed_ms=1700,
            metadata={"card_kind": "basic", "study_goal": "recall the Bernoulli relationship"},
        ),
        WorkflowTraceEvent(node="render", decision="accepted", elapsed_ms=900),
        WorkflowTraceEvent(node="evaluate", decision="rejected", attempt=2, error="overload"),
    ]
    usage = WorkflowUsageSummary(
        events=[WorkflowUsageEvent(node="x", operation="chat", total_tokens=100, estimated_usd=0.01)]
    )

    text = format_trace_events(events, usage=usage, title="Trace")

    assert "Trace" in text
    assert "plan_card_type → basic" in text
    assert "card_kind: basic" in text  # key info / LLM output surfaced from metadata
    assert "study_goal: recall the Bernoulli relationship" in text
    assert "(attempt 2)" in text
    assert "overload" in text
    assert "billed (API): $0.0100" in text  # usage/cost footer — real money only
    assert "subscription" not in text  # pure-metered run: absent is not unknown


def test_format_trace_events_preserves_usage_footer_when_trace_is_truncated():
    from ai_workflow_engine import format_trace_events
    from ai_workflow_engine.models import WorkflowTraceEvent

    events = [
        WorkflowTraceEvent(
            node=f"noisy_{index}",
            decision="accepted",
            metadata={"payload": "x" * 600},
        )
        for index in range(12)
    ]
    usage = WorkflowUsageSummary(
        events=[
            WorkflowUsageEvent(node="metered", operation="chat", total_tokens=100, estimated_usd=0.01),
            WorkflowUsageEvent(
                node="notional",
                operation="chat",
                total_tokens=50,
                cost_class="subscription_notional",
                notional_usd=0.02,
                provider_reported_notional_usd=0.02,
                notional_pricing=NotionalPricingResult(
                    source="provider_reported",
                    amount_usd=0.02,
                    catalog_version="provider-reported",
                ),
            ),
        ]
    )

    text = format_trace_events(events, usage=usage, title="Trace", max_total_len=600)

    assert "…(trace truncated)" in text
    assert "billed (API): $0.0100" in text
    assert "subscription: ~$0.0200 plan value" in text


def test_format_trace_events_omits_observation_noise_to_keep_domain_decisions_visible():
    from ai_workflow_engine import format_trace_events
    from ai_workflow_engine.models import WorkflowTraceEvent

    events = [
        WorkflowTraceEvent(
            node=f"llm_{index}",
            decision="llm:request",
            metadata={
                "detail_kind": "rendered_prompt",
                "detail_digest": "x" * 64,
                "prompt_digest": "x" * 64,
                "workflow_id": "wf-hidden",
                "output_model": "UsefulModel",
            },
        )
        for index in range(4)
    ]
    events.append(
        WorkflowTraceEvent(
            node="collect_axis",
            decision="artifacts_salvaged",
            metadata={"new_artifact_count": 1},
        )
    )

    text = format_trace_events(events, title="Trace", max_total_len=900)

    assert "artifacts_salvaged" in text
    assert "new_artifact_count: 1" in text
    assert "detail_digest" not in text
    assert "prompt_digest" not in text
    assert "wf-hidden" not in text


async def test_executor_runs_external_process_step():
    # §4 node type: external process/script/tool — a step bound to ExternalProcessCapability,
    # executed by the engine (side-effect gated by the profile).
    from ai_workflow_engine import ExternalProcessCapability
    from ai_workflow_engine.engine.external import ExternalProcessRequest

    engine = (
        WorkflowEngineBuilder()
        .register_capability(
            "run_process",
            ExternalProcessCapability(name="run_process", side_effects=["external_call"]),
        )
        .register_workflow(
            WorkflowBuilder("proc_demo").step("run_process").build(),
            profile=WorkflowProfile(
                workflow_type="proc_demo", safety=SafetyPolicy(allowed_side_effects=["external_call"])
            ),
        )
        .build()
    )
    result = await engine.run(
        "proc_demo",
        ExternalProcessRequest(command=["python", "-c", "print('engine-process-ok')"]),
    )

    assert result.status == "completed"
    assert result.output["returncode"] == 0
    assert "engine-process-ok" in result.output["stdout"]


async def test_executor_external_process_step_denied_without_side_effect_allowance():
    # Same external-process step is blocked before invocation when the profile forbids external_call.
    from ai_workflow_engine import ExternalProcessCapability
    from ai_workflow_engine.engine.external import ExternalProcessRequest

    engine = (
        WorkflowEngineBuilder()
        .register_capability(
            "run_process",
            ExternalProcessCapability(name="run_process", side_effects=["external_call"]),
        )
        .register_workflow(WorkflowBuilder("proc_denied").step("run_process").build())  # default profile forbids it
        .build()
    )
    result = await engine.run("proc_denied", ExternalProcessRequest(command=["python", "-c", "print('should-not-run')"]))

    assert result.status == "failed"
    assert "external_call" in (result.error or "")


async def test_node_memory_config_is_validated_loudly_at_build():  # GoPro R4
    with pytest.raises(ValueError, match="memory config invalid"):
        (
            WorkflowBuilder("bad_memory_flow")
            .step("probe", memory={"mode": "definitely_not_a_mode"})
            .build()
        )


async def test_node_memory_config_is_delivered_to_the_capability_context():  # GoPro R4
    engine = WorkflowEngine()
    seen: dict = {}

    async def probe(context, _payload):
        seen["memory"] = context.metadata.get("agent_memory")
        return {"ok": True}

    engine.register_capability("probe", probe)
    engine.register_workflow(
        WorkflowBuilder("memory_flow")
        .step("probe", memory={"mode": "image_evicting", "keep_last_images": 0})
        .build()
    )

    result = await engine.run("memory_flow", {})

    assert result.status == "completed"
    assert seen["memory"] == {"mode": "image_evicting", "keep_last_images": 0}


def test_workflow_node_rejects_zero_or_negative_structural_bounds():
    """R13 (recheck finding #5): hand-written graphs get the same bound honesty as authored
    ones — max_parallel=0 / max_items=0 is a MODEL error, never a silent rewrite to
    'sequential' (fanout semaphore max(1, 0)) or an accidental fall-through to defaults."""

    import pydantic

    for field, bad in (
        ("max_parallel", 0),
        ("max_parallel", -2),
        ("max_items", 0),
        ("max_items", -1),
    ):
        with pytest.raises(pydantic.ValidationError):
            WorkflowNode(id="fan", kind="fanout", **{field: bad})

    # None (unset -> engine bound applies) and >=1 remain legal
    node = WorkflowNode(id="fan", kind="fanout", max_parallel=1, max_items=1)
    assert node.max_parallel == 1 and node.max_items == 1
    assert WorkflowNode(id="fan2", kind="fanout").max_parallel is None


# --- v0.11.18 child-workflow execution windows (RED before the _run_inner boundary owner) -----
#
# Contract (docs/_discussion/2026-07-29-engine-response-child-workflow-coarse-windows.md):
#   effective child timeout = min(child configured timeout, parent remaining, run remaining)
# applied ONCE around the complete child invocation at WorkflowExecutor._run_inner, for BOTH
# child doors (register_workflow_capability and declarative .subworkflow). A child's own
# RuntimeLimits.timeout_s is unenforced in 0.11.17: _run_inner calls compiled.ainvoke directly,
# bypassing the runner's graph fail-safe.

async def _slow_child_node(context, payload):
    # Cooperatively cancellable long work: without a child window this runs to completion and
    # the parent only returns after the full sleep. Deterministic capabilities return their
    # output directly.
    await asyncio.sleep(payload.get("sleep_s", 2.0) if isinstance(payload, dict) else 2.0)
    return {"child": "finished"}


def _child_window_engine(*, door: str, child_timeout_s, parent_timeout_s=None, sleep_s=2.0):
    """Build an engine whose child workflow carries a short configured timeout while the parent
    run is large or unbounded, so only the CHILD limit can stop the slow child.

    door="capability": child reached via register_workflow_capability + a parent step.
    door="subworkflow": child reached via a declarative .subworkflow node.
    """

    child_builder = WorkflowBuilder("slow_child").step("slow_node")
    if child_timeout_s is not None:
        child_builder = child_builder.with_limits(RuntimeLimits(timeout_s=child_timeout_s))
    child = child_builder.build()

    engine = (
        WorkflowEngineBuilder()
        .register_capability("slow_node", _slow_child_node, kind="deterministic")
        .register_workflow(child)
        .build()
    )

    parent_profile = WorkflowProfile(
        workflow_type="parent",
        safety=SafetyPolicy(allowed_side_effects=[]),
        limits=RuntimeLimits(timeout_s=parent_timeout_s) if parent_timeout_s else RuntimeLimits(),
    )

    # register_workflow_capability / register_workflow live on the built WorkflowEngine.
    if door == "capability":
        engine.register_workflow_capability("run_child", "slow_child")
        parent = WorkflowBuilder("parent").step("run_child").build()
    elif door == "subworkflow":
        parent = WorkflowBuilder("parent").subworkflow("run_child", workflow=child).build()
    else:  # pragma: no cover - test wiring guard
        raise ValueError(door)

    engine.register_workflow(parent, profile=parent_profile)
    return engine


def _child_window_events(result):
    return [e for e in result.trace if (e.decision or "").startswith("child_window:")]


@pytest.mark.parametrize("door", ["capability", "subworkflow"])
async def test_child_workflow_configured_timeout_bounds_the_child(door):
    """A child with a short configured timeout must be stopped well before an unbounded parent,
    and the CHILD-window owner must be the one that acts (its own limit is the limiting source).
    In 0.11.17 the child runs to completion because _run_inner applies no child window."""

    engine = _child_window_engine(door=door, child_timeout_s=0.3, parent_timeout_s=None, sleep_s=3.0)
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await engine.run("parent", {"sleep_s": 3.0})
    elapsed = loop.time() - start

    assert elapsed < 1.5, f"[{door}] child ran ~{elapsed:.2f}s; its 0.3s limit was not enforced"
    # Clean cooperative expiry is honest PARTIAL (exact — not "partial or failed").
    assert result.status == "partial", f"[{door}] clean child timeout must be partial, got {result.status}"
    # The child-window OWNER acted, and named its own limit as the limiting source.
    events = _child_window_events(result)
    assert events, f"[{door}] no child_window terminal event was recorded"
    assert events[-1].metadata["limiting_source"] == ["capability_limit"]
    assert events[-1].metadata["configured_timeout_s"] == 0.3


@pytest.mark.parametrize("door", ["capability", "subworkflow"])
async def test_child_no_limit_leaves_ownership_with_the_ancestor(door):
    """A child with NO configured timeout stays bounded by the ancestor window, and the child
    owner does NOT act (canary #4 + Finding-2 differential): no child_window event is emitted, so
    the door's pre-change status/attribution is preserved exactly. The subworkflow door is
    unambiguous — before this change only the top-level runner owned the run deadline (failed)."""
    engine = _child_window_engine(door=door, child_timeout_s=None, parent_timeout_s=0.3)
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await engine.run("parent", {"sleep_s": 3.0})
    elapsed = loop.time() - start
    assert elapsed < 1.5, f"[{door}] no-limit child ran ~{elapsed:.2f}s; ancestor ceiling not applied"
    assert not _child_window_events(result), (
        f"[{door}] child-window owner stole ownership of an ancestor-owned timeout"
    )
    # Measured baseline (verified, not inferred): with the owner staying out, both doors report
    # the ancestor-owned outcome. The differential that proves this change did not touch the case
    # is the ABSENCE of a child_window event; the observed status is partial on both doors.
    assert result.status == "partial", f"[{door}] ancestor-owned outcome changed: {result.status}"


async def test_tighter_ancestor_wins_and_child_owner_stays_out():
    """A large child timeout can never widen a tighter run remainder, and because the ancestor is
    the limiting source the child owner does NOT add a second inner timer (Finding 2)."""
    engine = _child_window_engine(door="subworkflow", child_timeout_s=10.0, parent_timeout_s=0.3)
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await engine.run("parent", {"sleep_s": 3.0})
    elapsed = loop.time() - start
    assert elapsed < 1.5, f"child ran ~{elapsed:.2f}s; a 10s child limit widened the 0.3s ancestor"
    assert result.status == "partial", f"run-owned outcome changed: {result.status}"


@pytest.mark.parametrize("door", ["capability", "subworkflow"])
async def test_normal_child_below_breaker_completes_unchanged(door):
    """No regression: a child that finishes well under a generous window completes normally with
    its real output (canary #5). Proves the boundary window does not disturb the healthy path."""
    engine = _child_window_engine(door=door, child_timeout_s=5.0, parent_timeout_s=None, sleep_s=0.02)
    result = await engine.run("parent", {"sleep_s": 0.02})
    assert result.status == "completed", result.error
    # The child's real terminal output survives (no fabricated timeout envelope).
    assert result.output == {"child": "finished"}


async def test_child_timeout_clean_expiry_is_partial_not_failed():
    """A cooperatively cancelled child timeout is honest PARTIAL, not failed (contract)."""
    engine = _child_window_engine(door="capability", child_timeout_s=0.3, parent_timeout_s=None, sleep_s=3.0)
    result = await engine.run("parent", {"sleep_s": 3.0})
    # capability maps child partial -> capability partial -> parent partial.
    assert result.status == "partial", f"clean child timeout should be partial, got {result.status}"


async def test_fanout_partial_isolation_preserves_the_completed_sibling_artifact(tmp_path):
    """Canaries #6 + #8: two child invocations overlap behind a barrier; the slow one exceeds its
    child breaker while the fast one completes and emits a real artifact. The parent is partial, the
    completed sibling's artifact survives BY IDENTITY in the run result, and the finalized
    observation bundle on disk records it as copied.

    (An earlier attempt concluded child artifacts do not propagate; that probe was miswired —
    `CapabilityRuntime.invoke` retains completed-invocation artifacts into the run session before
    graph-state commit, and this test now pins that behaviour under a child-window timeout.)
    """

    from ai_workflow_engine import ObservationConfig

    _EVIDENCE_ARTIFACT_ID = "sibling-evidence-artifact-1"
    evidence = tmp_path / "sibling-evidence.png"
    evidence.write_bytes(b"\x89PNG\r\n\x1a\nsibling")
    bundle_root = tmp_path / "bundles"
    both_started = asyncio.Event()
    arrivals = {"n": 0}

    async def _worker(ctx, payload):
        # Barrier: force the two fanout children to be genuinely in flight together.
        arrivals["n"] += 1
        if arrivals["n"] >= 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1.0)
        if payload.get("slow"):
            await asyncio.sleep(3.0)  # exceeds the 0.4s child breaker
            return {"never": "reached"}
        return artifact_result(
            {"k": payload.get("k")},
            [
                WorkflowArtifact(
                    artifact_id=_EVIDENCE_ARTIFACT_ID,
                    path=str(evidence),
                    kind="media",
                    source="unit",
                    owner_node="work",
                )
            ],
        )

    child = WorkflowBuilder("evidence_child").step("work").with_limits(
        RuntimeLimits(timeout_s=0.4)
    ).build()
    engine = (
        WorkflowEngineBuilder()
        .with_observation(
            ObservationConfig(
                enabled=True, bundle_dir=str(bundle_root), capture="full", artifacts="copy"
            )
        )
        .register_capability("work", _worker, kind="tool", side_effects=["local_write"])
        .register_workflow(child)
        .build()
    )
    engine.register_workflow_capability("run_child", "evidence_child")
    parent = (
        WorkflowBuilder("fan_parent")
        .step("items")
        .fanout("fan", capability="run_child", items_key="items", max_parallel=2)
        .build()
    )
    engine.register_capability(
        "items",
        lambda ctx, p: [{"k": "fast", "slow": False}, {"k": "slow", "slow": True}],
        kind="deterministic",
    )
    engine.register_workflow(
        parent,
        profile=WorkflowProfile(
            workflow_type="fan_parent", safety=SafetyPolicy(allowed_side_effects=["local_write"])
        ),
    )

    loop = asyncio.get_running_loop()
    start_t = loop.time()
    result = await engine.run("fan_parent", {})
    elapsed = loop.time() - start_t

    assert elapsed < 1.5, f"fanout ran ~{elapsed:.2f}s; the stuck child was not bounded"
    assert result.status == "partial", f"one child timed out; expected partial, got {result.status}"
    assert arrivals["n"] == 2, "the two children did not both start; overlap was not forced"

    # The completed sibling's artifact survives BY EXACT IDENTITY in the run result...
    assert [a.artifact_id for a in result.artifacts] == [_EVIDENCE_ARTIFACT_ID], (
        f"the completed sibling's artifact was lost or substituted: "
        f"{[(a.artifact_id, a.path) for a in result.artifacts]}"
    )
    assert [a.path for a in result.artifacts] == [str(evidence)]
    # ...and THAT SAME id is the copied row in the finalized on-disk bundle (a substituted or
    # deduplicated row cannot satisfy this).
    manifest = json.loads((Path(result.observation_bundle_path) / "artifacts.json").read_text())
    rows = [entry for entry in manifest if entry.get("artifact_id") == _EVIDENCE_ARTIFACT_ID]
    assert len(rows) == 1, f"expected exactly one manifest row for the artifact id: {manifest}"
    assert rows[0].get("copied") is True, f"artifact not copied: {rows[0]}"


async def test_child_window_refuses_synchronous_uninterruptible_work_before_it_runs():
    """Finding 1: the child window is PUBLISHED to nested supervision, so a synchronous
    (enforcement='none') child handler under a finite child limit is refused before it starts —
    the graph fail-safe alone cannot bound work that blocks the event loop. Without publication a
    0.1s child around a 0.6s blocking handler returned only after ~0.78s."""

    def _blocking(ctx, payload):
        import time as _t
        _t.sleep(0.6)  # synchronous, uninterruptible
        return {"child": "finished"}

    child = WorkflowBuilder("sync_child").step("blocking").with_limits(
        RuntimeLimits(timeout_s=0.1)
    ).build()
    engine = (
        WorkflowEngineBuilder()
        .register_capability("blocking", _blocking, kind="deterministic")
        .register_workflow(child)
        .build()
    )
    parent = WorkflowBuilder("parent").subworkflow("run_child", workflow=child).build()
    engine.register_workflow(
        parent, profile=WorkflowProfile(workflow_type="parent", safety=SafetyPolicy(allowed_side_effects=[]))
    )
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await engine.run("parent", {})
    elapsed = loop.time() - start

    assert elapsed < 0.4, f"sync child ran ~{elapsed:.2f}s; it was not refused before execution"
    assert result.status in ("partial", "failed")


async def test_completed_bounded_child_records_full_window_decision_and_elapsed():
    """Finding 3: a normal bounded child (child limit is the limiting source) records a
    child_window:completed event carrying the configured breaker, effective hard/work limits,
    limiting source, and actual elapsed — not only on timeout."""
    engine = _child_window_engine(door="capability", child_timeout_s=5.0, parent_timeout_s=None, sleep_s=0.02)
    result = await engine.run("parent", {"sleep_s": 0.02})
    assert result.status == "completed", result.error
    events = _child_window_events(result)
    assert events, "a completed bounded child recorded no child_window event"
    ev = events[-1]
    assert ev.decision == "child_window:completed"
    assert ev.metadata["configured_timeout_s"] == 5.0
    assert ev.metadata["effective_hard_s"] == 5.0
    assert ev.metadata["limiting_source"] == ["capability_limit"]
    assert ev.metadata["elapsed_s"] >= 0.0 and ev.elapsed_ms is not None


async def test_stubborn_child_that_ignores_cancellation_is_loud_containment_failed():
    """Blocking contract: a child capability that ignores cancellation and keeps working must be
    a LOUD containment failure, never a clean partial.

    Before the fix, the outer child deadline cancelled the graph, nested supervision detached the
    still-running handler and re-raised ordinary CancelledError, and the graph owner read that as
    an acknowledged stop -> partial in ~0.16s while the work continued. The engine must not claim a
    bounded stop it did not perform.
    """

    finished = {"value": False}

    async def _stubborn(context, payload):
        deadline = asyncio.get_running_loop().time() + 0.6
        while asyncio.get_running_loop().time() < deadline:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue  # ignore every cancellation and keep working
        finished["value"] = True
        return {"child": "finished"}

    child = WorkflowBuilder("stubborn_child").step("stubborn").with_limits(
        RuntimeLimits(timeout_s=0.2)
    ).build()
    engine = (
        WorkflowEngineBuilder()
        .register_capability("stubborn", _stubborn, kind="deterministic")
        .register_workflow(child)
        .build()
    )
    parent = WorkflowBuilder("parent").subworkflow("run_child", workflow=child).build()
    engine.register_workflow(
        parent, profile=WorkflowProfile(workflow_type="parent", safety=SafetyPolicy(allowed_side_effects=[]))
    )
    result = await engine.run("parent", {})

    assert result.status == "failed", (
        f"a child that ignored cancellation must be loud containment failed, got {result.status}"
    )
    # The point of the contract: the engine reported a LOUD failure precisely because the work had
    # NOT finished. A clean partial here would be a claim the engine never performed.
    assert finished["value"] is False, "setup invalid: the handler completed before the breaker"
    events = _child_window_events(result)
    assert events and events[-1].decision == "child_window:failed"
    assert events[-1].severity == "error"
    assert events[-1].metadata["graph_failsafe_containment_failed"] is True
    # The detached handler must actually finish; a silent poll timeout would hide a leak.
    for _ in range(200):
        if finished["value"]:
            break
        await asyncio.sleep(0.02)
    assert finished["value"] is True, (
        "the detached stubborn handler never settled; it leaked past the test"
    )


async def test_child_window_spans_retries_and_is_not_reset_per_attempt():
    """Canary #5: retries consume ONE shared child window, never a fresh window per attempt.

    Step retries are node-level (``step(retry=Retry(...))``) — an earlier version wrongly used
    RuntimeLimits.max_retries and executed exactly ONE attempt, so it proved nothing. Here the node
    retries a failing handler whose CUMULATIVE attempts exceed the child breaker; the test asserts
    at least two real attempts occurred and that the outer child window (not retry exhaustion) is
    what stopped the run.
    """

    from ai_workflow_engine.workflow import Retry

    calls = {"n": 0}

    async def _slow_flaky(ctx, payload):
        calls["n"] += 1
        await asyncio.sleep(0.3)
        raise RuntimeError(f"attempt {calls['n']} rejected")

    # 20 x 0.3s = 6s of retry work under a 1s breaker: exhaustion CANNOT be what stops this
    # run. The wider timing geometry also leaves meaningful separation between the nested
    # shrinking-window timeout and the outer child deadline under loaded CI.
    child = (
        WorkflowBuilder("retry_child")
        .step("flaky", retry=Retry(max_attempts=20))
        .with_limits(RuntimeLimits(timeout_s=1.0))
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .register_capability("flaky", _slow_flaky, kind="deterministic")
        .register_workflow(child)
        .build()
    )
    parent = WorkflowBuilder("parent").subworkflow("run_child", workflow=child).build()
    engine.register_workflow(
        parent, profile=WorkflowProfile(workflow_type="parent", safety=SafetyPolicy(allowed_side_effects=[]))
    )
    loop = asyncio.get_running_loop()
    start_t = loop.time()
    result = await engine.run("parent", {})
    elapsed = loop.time() - start_t

    # Real retries happened (a per-attempt reset regression is only meaningful if >1 attempt ran).
    assert calls["n"] >= 2, f"retry canary executed only {calls['n']} attempt(s); it proves nothing"
    assert calls["n"] < 20, "retries exhausted; the breaker never fired, so nothing is proven"
    # The WINDOW stopped it, not retry exhaustion. Note the mechanism: because the child window is
    # PUBLISHED, each nested attempt is bounded by the SHRINKING remainder, so attempts consume one
    # shared budget and stop early — the child graph then finishes just inside the window (a
    # `child_window:completed` event with elapsed ~= the work budget). That is the shared-deadline
    # contract; a per-attempt RESET would instead allow ~20 x 0.12s of work.
    assert result.status == "partial", f"the shared window must stop this run, got {result.status}"
    # TWO-LAYER observation truth, locked exactly:
    #  (inner) the nested capability records the real timeout as a PARTIAL node outcome, because
    #          the published window shrinks with each attempt;
    #  (outer) the child graph then settles INSIDE its own breaker, so the owner records
    #          child_window:completed — forcing the owner to "partial" would misstate which timer
    #          fired.
    inner = [
        e
        for e in result.trace
        if e.node == "flaky"
        and e.decision == "partial"
        and e.metadata.get("timeout_reason") == "execution_window_exceeded"
    ]
    assert inner, "the nested capability recorded no execution-window timeout"
    inner_window = inner[-1].metadata["execution_window"]
    assert inner_window["limiting_sources"] == ["parent_window"], inner_window
    assert "parent_soft_remaining" in inner_window["clamps"], inner_window
    events = _child_window_events(result)
    assert events, "the child window did not own this run"
    owner_event = events[-1]
    assert owner_event.decision == "child_window:completed", (
        "expected the child graph to settle inside its own breaker while nested work recorded the "
        f"timeout; got {owner_event.decision}. (Observed once under heavy instrumentation load as "
        "child_window:partial — if this fails on loaded CI it means the outer timer won the race, "
        "not that the shared-window contract broke.)"
    )
    assert owner_event.metadata["limiting_source"] == ["capability_limit"]
    assert owner_event.metadata["elapsed_s"] <= owner_event.metadata["effective_hard_s"] + 0.25, (
        f"child ran past its own window: {owner_event.metadata}"
    )
    assert elapsed < 2.0, f"child ran ~{elapsed:.2f}s; retries appear to reset the window per attempt"


async def test_child_window_reaps_process_backed_descendant(tmp_path):
    """Canary #9: a real engine-owned process under a bounded child is terminated and reaped, and
    its descendant does not escape.

    An earlier version asserted only that a delayed marker was absent — it passed in ~20ms with a
    nonexistent executable, proving neither spawn nor reap. This requires POSITIVE start evidence
    (the parent writes its own pid, the descendant writes its pid) before asserting cleanup, so a
    spawn failure can no longer masquerade as successful containment.
    """

    from ai_workflow_engine import ExternalProcessCapability
    from ai_workflow_engine.engine.external import ExternalProcessRequest

    started = tmp_path / "parent-started.pid"
    descendant_pid_file = tmp_path / "descendant.pid"
    escaped = tmp_path / "descendant-escaped.txt"

    child_script = (
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(1.5); Path(sys.argv[2]).write_text('escaped')"
    )
    parent_script = (
        "import os,subprocess,sys,time; from pathlib import Path; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]]); "
        "Path(sys.argv[4]).write_text(str(os.getpid())); "
        "print('spawned', flush=True); time.sleep(30)"
    )

    child = WorkflowBuilder("proc_child").step("run_process").with_limits(
        RuntimeLimits(timeout_s=0.5)
    ).build()
    engine = (
        WorkflowEngineBuilder()
        .register_capability(
            "run_process",
            ExternalProcessCapability(name="run_process", side_effects=["external_call"]),
        )
        .register_workflow(child)
        .build()
    )
    parent = WorkflowBuilder("parent").subworkflow("run_child", workflow=child).build()
    engine.register_workflow(
        parent,
        profile=WorkflowProfile(
            workflow_type="parent", safety=SafetyPolicy(allowed_side_effects=["external_call"])
        ),
    )
    result = await engine.run(
        "parent",
        ExternalProcessRequest(
            command=[
                sys.executable,
                "-c",
                parent_script,
                child_script,
                str(descendant_pid_file),
                str(escaped),
                str(started),
            ],
        ),
    )

    # POSITIVE start evidence first: a spawn failure must not pass this test.
    assert started.exists(), "the process never started; this test would prove nothing about reaping"
    assert descendant_pid_file.exists(), "the descendant never started; cleanup is unproven"
    parent_pid = int(started.read_text())
    descendant_pid = int(descendant_pid_file.read_text())

    # The CHILD WINDOW (not an unrelated process failure) bounded it: exact status + owner
    # attribution. The published window bounds the process AT THE CAPABILITY DOOR, so the child
    # graph settles inside its own window and the owner event records the enforced decision.
    assert result.status == "partial", f"expected the child window to bound this, got {result.status}"
    events = _child_window_events(result)
    assert events, "process cleanup was not attributed to the child window"
    assert events[-1].metadata["limiting_source"] == ["capability_limit"]
    assert events[-1].metadata["configured_timeout_s"] == 0.5

    # Both the owned process and its descendant are gone, and the descendant never completed.
    await asyncio.sleep(1.8)
    for label, pid in (("owned process", parent_pid), ("descendant", descendant_pid)):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise AssertionError(f"{label} pid {pid} survived the child execution window")
    assert not escaped.exists(), "a descendant escaped the child execution window"


async def test_child_window_preserves_per_child_usage_attribution_in_concurrent_fanout():
    """Canary #8 (full contract): two child invocations forced to overlap, one performing a REAL
    node-level retry, each attempt emitting usage through the real usage door.

    Asserts the consumer's scenario correlation, the attempt sequence, per-attempt invocation
    identity, the exact event count (no duplication), and exact aggregate totals — an earlier
    version hand-authored two ids with no retry and proved only that two events aggregate.
    """

    from ai_workflow_engine.usage_events import record_usage_event
    from ai_workflow_engine.workflow import Retry

    both_started = asyncio.Event()
    arrivals = {"n": 0}
    attempts = {"fast": 0, "flaky": 0}

    async def _overlap(context, payload):
        """Non-retried overlap fence: serialization must fail before metered work starts."""

        arrivals["n"] += 1
        if arrivals["n"] >= 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2.0)
        return payload

    async def _metered(context, payload):
        scenario = payload.get("scenario")
        attempts[scenario] += 1
        attempt_no = attempts[scenario]
        # Real usage door, one event per ATTEMPT, carrying consumer scenario correlation.
        record_usage_event(
            WorkflowUsageEvent(
                node="metered",
                operation="chat",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                attempt=attempt_no,
                invocation_id=f"inv-{scenario}-{attempt_no}",
                metadata={"scenario": scenario},
            )
        )
        if scenario == "flaky" and attempt_no == 1:
            raise RuntimeError("first attempt rejected; retry")
        return {"scenario": scenario, "attempt": attempt_no}

    child = (
        WorkflowBuilder("metered_child")
        .step("overlap")
        .step("metered", retry=Retry(max_attempts=3))
        .with_limits(RuntimeLimits(timeout_s=5.0))
        .build()
    )
    engine = (
        WorkflowEngineBuilder()
        .register_capability("overlap", _overlap, kind="deterministic")
        .register_capability("metered", _metered, kind="llm")
        .register_workflow(child)
        .build()
    )
    engine.register_workflow_capability("run_child", "metered_child")
    parent = (
        WorkflowBuilder("fan_parent")
        .step("items")
        .fanout("fan", capability="run_child", items_key="items", max_parallel=2)
        .build()
    )
    engine.register_capability(
        "items",
        lambda ctx, p: [{"scenario": "fast"}, {"scenario": "flaky"}],
        kind="deterministic",
    )
    engine.register_workflow(
        parent, profile=WorkflowProfile(workflow_type="fan_parent", safety=SafetyPolicy(allowed_side_effects=[]))
    )

    result = await engine.run("fan_parent", {})

    assert result.status == "completed", result.error
    # Overlap is proven by the loud barrier above (serialised execution never reaches this line).
    assert arrivals["n"] >= 2, "the children did not overlap; concurrency was not forced"
    assert attempts["flaky"] == 2, f"the flaky child did not really retry: {attempts}"

    metered = [e for e in result.usage.events if e.node == "metered"]
    # Exact count: fast(1) + flaky(2 attempts) = 3 events, no duplication, none dropped.
    assert len(metered) == 3, f"expected 3 usage events (1 fast + 2 flaky attempts), got {len(metered)}"
    # Per-attempt invocation identity is preserved and distinct.
    assert {e.invocation_id for e in metered} == {"inv-fast-1", "inv-flaky-1", "inv-flaky-2"}
    # Consumer scenario correlation survives concurrent execution (no cross-child attribution).
    by_scenario = {}
    for event in metered:
        by_scenario.setdefault(event.metadata["scenario"], []).append(event.attempt)
    assert sorted(by_scenario["flaky"]) == [1, 2], f"attempt sequence collapsed: {by_scenario}"
    assert by_scenario["fast"] == [1], f"fast child attribution drifted: {by_scenario}"
    # Exact aggregate over all three attempts.
    assert result.usage.input_tokens == 30 and result.usage.output_tokens == 15, (
        f"aggregate drifted: in={result.usage.input_tokens} out={result.usage.output_tokens}"
    )


def test_child_window_uses_the_current_trace_sink_after_a_consumer_rebind():
    """The owner must resolve runtime.trace_sink at RECORD time. Consumers rebind the sink after
    engine construction (the pilots.py pattern locked by test_capability_observation.py); freezing
    it in the constructor would send child-window events to a stale sink."""

    from types import SimpleNamespace

    from ai_workflow_engine.child_window import ChildWorkflowWindow
    from ai_workflow_engine.execution_window import (
        ExecutionWindowInputs,
        resolve_execution_window,
    )

    class _Sink:
        def __init__(self):
            self.events = []

        def record(self, event):
            self.events.append(event)

    original, replacement = _Sink(), _Sink()
    runtime = SimpleNamespace(trace_sink=original)
    owner = ChildWorkflowWindow(runtime)

    runtime.trace_sink = replacement  # consumer swap AFTER construction

    decision = resolve_execution_window(
        ExecutionWindowInputs(capability_timeout_s=1.0), enforcement="cooperative"
    )
    definition = WorkflowBuilder("rebind_child").step("node").build()
    owner._record(definition, decision, 0.01, status="completed", reason=None)

    assert not original.events, "child-window event went to the stale (frozen) sink"
    assert len(replacement.events) == 1, "child-window event did not reach the rebound sink"
    assert replacement.events[0].decision == "child_window:completed"
