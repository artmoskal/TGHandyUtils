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
from ai_workflow_engine.usage import WorkflowBudgetExceeded, estimate_cost_usd, format_usage_summary, invoke_metered_chat

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


class GraphRecursionError(Exception):
    pass


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
            response_metadata={"model_name": "unit-model", "id": "req-unit"},
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
                model="unit-model",
                config=self.config,
            )
        return {"ok": True, **state}


class ToySummaryGraph:
    def invoke(self, state):
        words = state["text"].split()
        return {"summary": words[0] if words else "", "word_count": len(words), **state}


def test_engine_package_imports_without_project_prompt_root(tmp_path):
    """The reusable package must not require TGHandyUtils prompt files at import time."""
    repo_root = Path(__file__).resolve().parents[2]
    package_root = repo_root / "packages" / "ai_workflow_engine"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root), str(package_root), env.get("PYTHONPATH", "")]
    )

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


def test_media_modules_import_without_optional_provider_sdks(tmp_path):
    """The reusable package must import without optional media provider dependencies installed."""
    repo_root = Path(__file__).resolve().parents[2]
    package_root = repo_root / "packages" / "ai_workflow_engine"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root), str(package_root), env.get("PYTHONPATH", "")]
    )

    script = """
import builtins

real_import = builtins.__import__
blocked = {"openai"}

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] in blocked:
        raise ModuleNotFoundError(name)
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
import ai_workflow_engine.image_generation
import ai_workflow_engine.voice_generation
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
    repo_root = Path(__file__).resolve().parents[2]
    package_root = repo_root / "packages" / "ai_workflow_engine" / "ai_workflow_engine"
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
    repo_root = Path(__file__).resolve().parents[2]
    package_root = repo_root / "packages" / "ai_workflow_engine" / "ai_workflow_engine"
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

    assert [result.status for result in results] == ["accepted", "failed", "accepted"]
    assert results[1].error == "TimeoutError"
    assert any(event.node == "slow" and event.decision == "failed" for event in trace.events)


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


async def test_toy_inventory_pilot_uses_evidence_refs_scheduler_and_clarification():
    contents = EvidenceRef(
        role="contents",
        uri="frame://camera-1/latest",
        media_type="image/jpeg",
        summary="Open closet shelf with small supplies",
    )

    result, trace = await run_toy_inventory_pilot(
        InventoryPilotInput(location_hint=None, evidence_refs=[contents])
    )

    assert result.scheduling_actions == ["drop", "queue_latest"]
    assert result.used_provisional_location is True
    assert result.write_status == "created"
    assert {observation.item for observation in result.observations} == {"glue", "USB-C cable"}
    assert all(observation.location == "unknown location" for observation in result.observations)
    assert all(observation.evidence_ref_ids == [contents.ref_id] for observation in result.observations)
    assert "image_data" not in result.model_dump_json()
    assert any(event.node == "ask_location_clarification" and event.decision == "accepted" for event in trace.events)
    assert any(event.node == "write_inventory_observations" for event in trace.events)


async def test_workflow_runner_collects_usage_summary():
    config = SimpleNamespace(
        WORKFLOW_USAGE_TRACKING_ENABLED=True,
        WORKFLOW_MAX_TEXT_CALLS_PER_RUN=16,
        WORKFLOW_MAX_IMAGE_CALLS_PER_RUN=1,
        WORKFLOW_MAX_ESTIMATED_USD_PER_RUN=0,
        WORKFLOW_MODEL_PRICE_OVERRIDES_JSON=(
            '{"unit-model": {"input_per_1m": 100, "output_per_1m": 200}}'
        ),
    )
    goal = WorkflowGoal(workflow_type="test_workflow", objective="Track usage")

    result = await WorkflowRunner(config=config).run(UsageGraph(config=config), {}, goal=goal)

    summary = result["usage_summary"]
    assert summary.text_call_count == 1
    assert summary.input_tokens == 10
    assert summary.output_tokens == 5
    assert summary.estimated_usd == 0.002
    assert summary.events[0].request_id == "req-unit"


async def test_workflow_runner_blocks_text_calls_over_budget():
    config = SimpleNamespace(
        WORKFLOW_USAGE_TRACKING_ENABLED=True,
        WORKFLOW_MAX_TEXT_CALLS_PER_RUN=1,
        WORKFLOW_MAX_IMAGE_CALLS_PER_RUN=1,
        WORKFLOW_MAX_ESTIMATED_USD_PER_RUN=0,
        WORKFLOW_MODEL_PRICE_OVERRIDES_JSON="",
    )
    goal = WorkflowGoal(workflow_type="test_workflow", objective="Track usage")

    with pytest.raises(WorkflowBudgetExceeded):
        await WorkflowRunner(config=config).run(UsageGraph(config=config, calls=2), {}, goal=goal)


async def test_workflow_runner_aborts_after_estimated_usd_cap_before_second_paid_call():
    config = SimpleNamespace(
        WORKFLOW_USAGE_TRACKING_ENABLED=True,
        WORKFLOW_MAX_TEXT_CALLS_PER_RUN=16,
        WORKFLOW_MAX_IMAGE_CALLS_PER_RUN=1,
        WORKFLOW_MAX_ESTIMATED_USD_PER_RUN=5,
        WORKFLOW_MODEL_PRICE_OVERRIDES_JSON=(
            '{"unit-model": {"input_per_1m": 600000, "output_per_1m": 0}}'
        ),
    )
    goal = WorkflowGoal(workflow_type="test_workflow", objective="Prove live-spend cap")
    graph = UsageGraph(config=config, calls=2)

    with pytest.raises(WorkflowBudgetExceeded, match=r"\$6\.000000 > \$5\.000000"):
        await WorkflowRunner(config=config).run(graph, {}, goal=goal)

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
    assert "total: 1 text, 1 image, 3k in, 1.5k cached, 400 out, est $0.0148" in text


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
    assert "total: 0 text, 0 image, 1 tool, 0 in, 0 cached, 0 out, 42 chars, est ?" in text


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
