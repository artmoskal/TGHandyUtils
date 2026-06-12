import pytest
from pydantic import BaseModel

from ai_workflow_engine import (
    AgentCapability,
    LLMAgentPlanner,
    LLMRequest,
    LLMResponse,
    ReplayPlanner,
    ToolCallRequest,
    ToolSpec,
    WEAK_MODEL_CLEANER,
    build_llm_agent_capability,
)
from ai_workflow_engine.engine.capabilities import CapabilityRegistry, CapabilityRuntime
from ai_workflow_engine.models import (
    AgentRunRequest,
    AgentToolCall,
    AgentToolStep,
    CapabilityContext,
    CapabilityResult,
    CapabilitySpec,
    EvidenceRef,
    RuntimeLimits,
    WorkflowGoal,
    WorkflowRunContext,
    WorkflowUsageSummary,
)
from ai_workflow_engine.usage import WorkflowBudget, WorkflowBudgetExceeded, WorkflowUsageContext
from ai_workflow_engine.usage import workflow_usage_scope
from ai_workflow_engine.vision import ImageInput

pytestmark = pytest.mark.unit


class Caption(BaseModel):
    caption: str


class NavigateInput(BaseModel):
    url: str


class ScriptedLLM:
    def __init__(self, *responses: LLMResponse):
        self.responses = list(responses)
        self.requests: list[LLMRequest] = []

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


def _context(*, max_steps: int = 8) -> CapabilityContext:
    return CapabilityContext(
        goal=WorkflowGoal(workflow_type="agent_test", objective="exercise agent"),
        run_context=WorkflowRunContext(workflow_id="wf-agent", workflow_type="agent_test"),
        usage_summary=WorkflowUsageSummary(),
        limits=RuntimeLimits(max_steps=max_steps),
    )


def _tool_specs() -> dict[str, ToolSpec]:
    return {
        "navigate": ToolSpec(
            name="navigate",
            description="Open a URL",
            input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        ),
        "screenshot": ToolSpec(
            name="screenshot",
            description="Capture a screenshot",
            input_schema={"type": "object", "properties": {"selector": {"type": "string"}}},
        ),
    }


def _tool_runtime(*, screenshot_output=None) -> tuple[CapabilityRuntime, list[tuple[str, object]]]:
    calls: list[tuple[str, object]] = []
    registry = CapabilityRegistry()

    async def navigate(_context, payload):
        calls.append(("navigate", payload))
        return CapabilityResult(status="accepted", output={"url": payload["url"], "loaded": True})

    async def screenshot(_context, payload):
        calls.append(("screenshot", payload))
        output = screenshot_output
        if output is None:
            output = ImageInput(source="base64", data="c2NyZWVuc2hvdA==", media_type="image/png", role="screenshot")
        return CapabilityResult(status="accepted", output=output)

    registry.register(CapabilitySpec(name="navigate", kind="tool", description="Open a URL"), navigate)
    registry.register(CapabilitySpec(name="screenshot", kind="tool", description="Capture a screenshot"), screenshot)
    return CapabilityRuntime(registry), calls


def _planner(client: ScriptedLLM, **kwargs) -> LLMAgentPlanner:
    return LLMAgentPlanner(
        client,
        tool_specs=_tool_specs(),
        system_prompt="Use the available tools when needed.",
        output_model=Caption,
        node_name="agent_brain",
        **kwargs,
    )


async def test_llm_agent_planner_runs_scripted_tool_loop_with_image_result_and_usage():
    client = ScriptedLLM(
        LLMResponse(
            tool_calls=[ToolCallRequest(call_id="nav-1", name="navigate", arguments={"url": "https://example.test"})],
            model="fake-agent",
            input_tokens=10,
            output_tokens=2,
            total_tokens=12,
        ),
        LLMResponse(
            tool_calls=[ToolCallRequest(call_id="shot-1", name="screenshot", arguments={"selector": "body"})],
            model="fake-agent",
            input_tokens=12,
            output_tokens=2,
            total_tokens=14,
        ),
        LLMResponse(
            text='{"caption": "page is visible"}',
            model="fake-agent",
            input_tokens=20,
            output_tokens=5,
            total_tokens=25,
        ),
    )
    tool_runtime, calls = _tool_runtime()
    capability = AgentCapability(_planner(client), tool_runtime)
    context = _context()
    request = AgentRunRequest(
        prompt="Open the page and inspect it.",
        allowed_tools=["navigate", "screenshot"],
        max_steps=5,
    )

    usage_context = WorkflowUsageContext(context.run_context, context.usage_summary, WorkflowBudget())
    with workflow_usage_scope(usage_context):
        result = await capability(context, request)

    assert result.status == "accepted"
    assert result.output.status == "completed"
    assert result.output.output.caption == "page is visible"
    assert calls == [("navigate", {"url": "https://example.test"}), ("screenshot", {"selector": "body"})]
    assert len(client.requests) == 3
    assert [event.node for event in context.usage_summary.events] == ["agent_brain", "agent_brain", "agent_brain"]

    third_request = client.requests[2]
    assert third_request.messages[0].role == "system"
    assert third_request.messages[1].content == "Open the page and inspect it."
    screenshot_turn = third_request.messages[-1]
    assert screenshot_turn.role == "tool"
    assert screenshot_turn.tool_results[0].images[0].media_type == "image/png"


async def test_llm_agent_planner_respects_agent_tool_call_cap_after_decision():
    client = ScriptedLLM(
        LLMResponse(tool_calls=[ToolCallRequest(call_id="nav-1", name="navigate", arguments={"url": "x"})]),
        LLMResponse(tool_calls=[ToolCallRequest(call_id="shot-1", name="screenshot", arguments={})]),
    )
    tool_runtime, calls = _tool_runtime()
    capability = AgentCapability(_planner(client), tool_runtime)
    context = _context()
    request = AgentRunRequest(
        prompt="Use too many tools.",
        allowed_tools=["navigate", "screenshot"],
        max_steps=5,
        max_tool_calls=1,
    )

    with workflow_usage_scope(WorkflowUsageContext(context.run_context, context.usage_summary, WorkflowBudget())):
        result = await capability(context, request)

    assert result.status == "partial"
    assert result.output.status == "truncated"
    assert len(result.output.steps) == 1
    assert calls == [("navigate", {"url": "x"})]
    assert len(client.requests) == 2


async def test_llm_agent_planner_does_not_duplicate_tool_allow_list_enforcement():
    client = ScriptedLLM(
        LLMResponse(tool_calls=[ToolCallRequest(call_id="bad-1", name="forbidden", arguments={"x": 1})])
    )
    planner = _planner(client)
    context = _context()

    with workflow_usage_scope(WorkflowUsageContext(context.run_context, context.usage_summary, WorkflowBudget())):
        decision = await planner.next_step(
            context,
            AgentRunRequest(prompt="Try a forbidden tool.", allowed_tools=["navigate"]),
            [],
        )

    assert decision.action == "tool"
    assert decision.tool_name == "forbidden"
    assert decision.payload == {"x": 1}


async def test_llm_agent_planner_budget_failure_surfaces_through_capability_runtime():
    client = ScriptedLLM(
        LLMResponse(tool_calls=[ToolCallRequest(call_id="nav-1", name="navigate", arguments={"url": "x"})]),
        LLMResponse(tool_calls=[ToolCallRequest(call_id="shot-1", name="screenshot", arguments={})]),
        LLMResponse(text='{"caption": "would exceed"}'),
    )
    tool_runtime, _calls = _tool_runtime()
    agent = AgentCapability(_planner(client), tool_runtime)
    registry = CapabilityRegistry()
    registry.register(agent.spec, agent)
    runtime = CapabilityRuntime(registry)
    context = _context()
    request = AgentRunRequest(
        prompt="Open and inspect.",
        allowed_tools=["navigate", "screenshot"],
        max_steps=5,
    )

    usage_context = WorkflowUsageContext(
        context.run_context,
        context.usage_summary,
        WorkflowBudget(max_text_calls=2),
    )
    with workflow_usage_scope(usage_context):
        result = await runtime.invoke(agent.spec.name, request, context)

    assert result.status == "failed"
    assert "max_text_calls" in (result.error or "")
    assert len(client.requests) == 2
    assert len(context.usage_summary.events) == 2


async def test_llm_agent_planner_repairs_malformed_finish_json_with_pre_parse_cleaner():
    client = ScriptedLLM(
        LLMResponse(text="not json", input_tokens=1, output_tokens=1, total_tokens=2),
        LLMResponse(
            text='<think>draft</think>\n```json\n{"caption": "fixed"}\n```',
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        ),
    )
    planner = _planner(client, pre_parse=WEAK_MODEL_CLEANER, max_repair_rounds=1)
    context = _context()

    with workflow_usage_scope(WorkflowUsageContext(context.run_context, context.usage_summary, WorkflowBudget())):
        decision = await planner.next_step(
            context,
            AgentRunRequest(prompt="Return a caption.", allowed_tools=[]),
            [],
        )

    assert decision.action == "finish"
    assert decision.output.caption == "fixed"
    assert len(client.requests) == 2
    assert "previous structured-output response was invalid" in client.requests[1].messages[-1].content
    assert len(context.usage_summary.events) == 2


async def test_llm_agent_planner_enforces_image_cap_on_tool_result_images_before_call():
    image = ImageInput(source="base64", data="c2NyZWVuc2hvdA==", media_type="image/png", role="screenshot")
    history = [
        AgentToolStep(
            call=AgentToolCall(tool_name="screenshot", payload={}, rationale="capture"),
            status="accepted",
            output=image,
        )
    ]
    client = ScriptedLLM(LLMResponse(text='{"caption": "unreached"}'))
    planner = _planner(client)
    context = _context()
    usage_context = WorkflowUsageContext(
        context.run_context,
        context.usage_summary,
        WorkflowBudget(max_images_per_call=0),
    )

    with workflow_usage_scope(usage_context):
        with pytest.raises(WorkflowBudgetExceeded, match="max_images_per_call"):
            await planner.next_step(
                context,
                AgentRunRequest(prompt="Inspect screenshot.", allowed_tools=["screenshot"]),
                history,
            )

    assert client.requests == []


async def test_llm_agent_planner_loads_image_evidence_refs_at_call_boundary():
    evidence = EvidenceRef(
        ref_id="shot-1",
        role="screenshot",
        uri="memory://shot-1",
        media_type="image/png",
    )
    history = [
        AgentToolStep(
            call=AgentToolCall(tool_name="screenshot", payload={}, rationale="capture"),
            status="accepted",
            output={"artifact": evidence},
        )
    ]
    loaded: list[EvidenceRef] = []

    def load_image(ref: EvidenceRef) -> bytes:
        loaded.append(ref)
        return b"shot-bytes"

    client = ScriptedLLM(LLMResponse(text='{"caption": "loaded"}'))
    planner = _planner(client, image_loader=load_image)
    context = _context()

    with workflow_usage_scope(WorkflowUsageContext(context.run_context, context.usage_summary, WorkflowBudget())):
        decision = await planner.next_step(
            context,
            AgentRunRequest(prompt="Inspect screenshot evidence.", allowed_tools=["screenshot"]),
            history,
        )

    assert decision.action == "finish"
    assert decision.output.caption == "loaded"
    assert loaded == [evidence]
    image = client.requests[0].messages[-1].tool_results[0].images[0]
    assert image.source == "base64"
    assert image.data == "c2hvdC1ieXRlcw=="
    assert image.media_type == "image/png"
    assert image.role == "screenshot"
    assert image.metadata == {"evidence_ref_id": "shot-1"}


async def test_build_llm_agent_capability_derives_tool_specs_and_runs_episode():
    client = ScriptedLLM(
        LLMResponse(tool_calls=[ToolCallRequest(call_id="nav-1", name="navigate", arguments={"url": "x"})]),
        LLMResponse(text='{"caption": "built"}'),
    )
    registry = CapabilityRegistry()
    calls: list[NavigateInput] = []

    async def navigate(_context, payload: NavigateInput):
        calls.append(payload)
        return CapabilityResult(status="accepted", output={"url": payload.url, "loaded": True})

    registry.register(
        CapabilitySpec(
            name="navigate",
            kind="tool",
            description="Open a typed URL",
            input_model=NavigateInput,
        ),
        navigate,
    )
    capability = build_llm_agent_capability(
        client,
        registry,
        allowed_tools=["navigate"],
        name="browser_agent",
        system_prompt="Use typed tools.",
        output_model=Caption,
        node_name="builder_agent",
    )
    context = _context()

    with workflow_usage_scope(WorkflowUsageContext(context.run_context, context.usage_summary, WorkflowBudget())):
        result = await capability(
            context,
            AgentRunRequest(prompt="Open x.", allowed_tools=["navigate"], max_steps=3),
        )

    assert capability.spec.name == "browser_agent"
    assert result.status == "accepted"
    assert result.output.output.caption == "built"
    assert calls == [NavigateInput(url="x")]
    tool_spec = client.requests[0].tools[0]
    assert tool_spec.name == "navigate"
    assert tool_spec.description == "Open a typed URL"
    assert tool_spec.input_schema["properties"]["url"]["type"] == "string"


def _compare_step_output(expected: AgentToolStep, live: AgentToolStep) -> str | None:
    if expected.status != live.status:
        return f"status {live.status!r} != recorded {expected.status!r}"
    if expected.call.tool_name != live.call.tool_name:
        return f"tool {live.call.tool_name!r} != recorded {expected.call.tool_name!r}"
    if expected.call.payload != live.call.payload:
        return f"payload {live.call.payload!r} != recorded {expected.call.payload!r}"
    if expected.output != live.output:
        return f"output {live.output!r} != recorded {expected.output!r}"
    return None


async def test_replay_planner_replays_steps_without_llm_usage_and_detects_divergence():
    recorded_steps = [
        AgentToolStep(
            call=AgentToolCall(tool_name="navigate", payload={"url": "https://example.test"}, rationale="open"),
            status="accepted",
            output={"url": "https://example.test", "loaded": True},
        ),
        AgentToolStep(
            call=AgentToolCall(tool_name="screenshot", payload={"selector": "body"}, rationale="capture"),
            status="accepted",
            output={"image": "same"},
        ),
    ]
    context = _context()
    planner = ReplayPlanner(recorded_steps, compare=_compare_step_output)
    capability = AgentCapability(planner, _tool_runtime(screenshot_output={"image": "same"})[0])
    request = AgentRunRequest(
        prompt="Replay.",
        allowed_tools=["navigate", "screenshot"],
        max_steps=5,
    )

    with workflow_usage_scope(WorkflowUsageContext(context.run_context, context.usage_summary, WorkflowBudget())):
        result = await capability(context, request)

    assert result.status == "accepted"
    assert result.output.output == {"replayed_steps": 2, "diverged": False}
    assert [step.call for step in result.output.steps] == [step.call for step in recorded_steps]
    assert context.usage_summary.events == []

    fail_context = _context()
    fail_capability = AgentCapability(
        ReplayPlanner(recorded_steps, compare=_compare_step_output),
        _tool_runtime(screenshot_output={"image": "different"})[0],
    )
    failed = await fail_capability(fail_context, request)
    assert failed.status == "failed"
    assert "output" in (failed.error or "")

    partial_context = _context()
    partial_capability = AgentCapability(
        ReplayPlanner(recorded_steps, on_divergence="finish_partial", compare=_compare_step_output),
        _tool_runtime(screenshot_output={"image": "different"})[0],
    )
    partial = await partial_capability(partial_context, request)
    assert partial.status == "accepted"
    assert partial.output.output["diverged"] is True
    assert "output" in partial.output.output["divergence"]


async def test_planner_preserves_client_reported_notional_cost():
    """A console-style client reports subscription cost via notional_usd (estimated_usd=None);
    the planner must not lose it (cost-honesty: never an unknown when the envelope told us)."""
    from ai_workflow_engine import LLMRequest, LLMResponse
    from ai_workflow_engine.engine.agent_planner import LLMAgentPlanner
    from ai_workflow_engine.models import (
        AgentRunRequest,
        CapabilityContext,
        WorkflowGoal,
        WorkflowRunContext,
        WorkflowUsageSummary,
    )
    from ai_workflow_engine.usage import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope

    async def console_style_client(request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text='{"label": "done"}',
            model="claude_p",
            estimated_usd=None,
            cost_class="subscription_notional",
            notional_usd=0.0734,
        )

    planner = LLMAgentPlanner(console_style_client, tool_specs={}, node_name="console_planner")
    context = CapabilityContext(
        goal=WorkflowGoal(workflow_type="pilot", objective="x"),
        run_context=WorkflowRunContext(workflow_id="wf-n", workflow_type="pilot"),
    )
    summary = WorkflowUsageSummary()
    usage_context = WorkflowUsageContext(context.run_context, summary, WorkflowBudget())
    with workflow_usage_scope(usage_context):
        decision = await planner.next_step(
            context, AgentRunRequest(prompt="go", subscription_mode=True), history=[]
        )

    assert decision.action == "finish"
    event = summary.events[0]
    assert event.cost_class == "subscription_notional"
    assert event.notional_usd == 0.0734
    assert event.metadata["cost_known"] is True


async def test_build_llm_agent_capability_joins_the_shared_trace_sink():
    """Episode tool-step traces must land in the SAME sink as the rest of the workflow when the
    engine runtime/sink is passed — no private trace islands."""
    from ai_workflow_engine import InMemoryTraceSink, LLMRequest, LLMResponse, ToolCallRequest
    from ai_workflow_engine.engine.agent_planner import build_llm_agent_capability
    from ai_workflow_engine.engine.capabilities import CapabilityRegistry
    from ai_workflow_engine.models import (
        AgentRunRequest,
        CapabilityContext,
        CapabilitySpec,
        WorkflowGoal,
        WorkflowRunContext,
    )

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(name="probe_tool", kind="tool", description="probe"),
        lambda ctx, payload: {"ok": True},
    )

    calls = {"n": 0}

    async def scripted_llm(request: LLMRequest) -> LLMResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(
                tool_calls=[ToolCallRequest(call_id="c1", name="probe_tool", arguments={})]
            )
        return LLMResponse(text='{"done": true}')

    shared_sink = InMemoryTraceSink()
    capability = build_llm_agent_capability(
        scripted_llm,
        registry,
        allowed_tools=["probe_tool"],
        trace_sink=shared_sink,
    )
    context = CapabilityContext(
        goal=WorkflowGoal(workflow_type="episode", objective="probe"),
        run_context=WorkflowRunContext(workflow_id="wf-sink", workflow_type="episode"),
    )

    result = await capability(context, AgentRunRequest(prompt="go", allowed_tools=["probe_tool"]))

    assert result.status in ("accepted", "partial")
    assert any(e.node == "probe_tool" and e.decision == "start" for e in shared_sink.events)
    assert any(e.node == "probe_tool" and e.decision == "accepted" for e in shared_sink.events)
