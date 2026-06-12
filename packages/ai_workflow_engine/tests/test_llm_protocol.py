"""G4 — plain non-LangChain LLM callables as kind="llm" (AC-G4).

Deliberately contains ZERO langchain imports: the point is that a bare async callable is a
first-class LLM client.
"""

import asyncio

import pytest
from pydantic import BaseModel, ValidationError

from ai_workflow_engine import (
    ChatMessage,
    ImageInput,
    LLMRequest,
    LLMResponse,
    StructuredLLMNode,
    StructuredVisionLLMNode,
    ToolCallRequest,
    ToolResult,
    ToolSpec,
    WEAK_MODEL_CLEANER,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    is_plain_llm_callable,
)
from ai_workflow_engine.models import RuntimeLimits, WorkflowProfile

pytestmark = pytest.mark.unit


class Verdict(BaseModel):
    label: str


class FakeOllamaClient:
    """Plain async callable hitting an imaginary local HTTP endpoint."""

    def __init__(self, text='{"label": "ok"}', *, model="qwen2.5vl:3b", tokens=(11, 7), usd=None, delay=0.0):
        self.text = text
        self.model = model
        self.tokens = tokens
        self.usd = usd
        self.delay = delay
        self.requests: list[LLMRequest] = []

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        return LLMResponse(
            text=self.text,
            model=self.model,
            input_tokens=self.tokens[0],
            output_tokens=self.tokens[1],
            total_tokens=sum(self.tokens),
            estimated_usd=self.usd,
        )


def _node(client, **kwargs) -> StructuredLLMNode:
    return StructuredLLMNode(
        name="g4_node",
        config=object(),
        output_model=Verdict,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm=client,
        **kwargs,
    )


def _engine(client, *, node=None, timeout_s=None):
    the_node = node or _node(client)

    async def classify(ctx, payload):
        return await the_node.run({"item": payload})

    builder = WorkflowEngineBuilder()
    builder.register_capability("classify", classify, kind="llm", metered=True, timeout_s=timeout_s)
    builder.register_workflow(WorkflowBuilder("classifier").step("classify").build())
    return builder.build()


def test_is_plain_llm_callable_detection():
    assert is_plain_llm_callable(FakeOllamaClient())

    class LangChainShaped:
        def invoke(self, messages):
            return None

    assert not is_plain_llm_callable(LangChainShaped())
    assert not is_plain_llm_callable(None)


async def test_llm_request_tool_calling_round_trip_and_legacy_single_turn_shape():
    class EchoClient:
        def __init__(self):
            self.requests = []

        async def __call__(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            return LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        call_id="call-1",
                        name=request.tools[0].name,
                        arguments={"url": "https://example.test"},
                    )
                ],
                stop_reason="tool_use",
            )

    client = EchoClient()
    tool = ToolSpec(
        name="navigate",
        description="Open a page",
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    )
    request = LLMRequest(
        messages=[
            ChatMessage(role="system", content="You are a browser agent."),
            ChatMessage(role="user", content="Open the home page."),
        ],
        tools=[tool],
        tool_choice="required",
        metadata={"run": "wp1"},
    )

    response = await client(request)

    assert client.requests[0] == request
    assert client.requests[0].messages[1].content == "Open the home page."
    assert client.requests[0].tools[0].input_schema["required"] == ["url"]
    assert response.text == ""
    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].arguments == {"url": "https://example.test"}

    legacy = LLMRequest(system="sys", user="hello", images=[], metadata={"legacy": True})
    assert legacy.system == "sys"
    assert legacy.user == "hello"
    assert legacy.images == []
    assert legacy.messages == []
    assert legacy.tools == []
    assert legacy.tool_choice is None
    assert legacy.metadata == {"legacy": True}


def test_tool_result_images_serialize_and_logged_metadata_uses_fingerprints_only():
    image = ImageInput(source="base64", data="c2VjcmV0LWJ5dGVz", media_type="image/png", role="screenshot")
    result = ToolResult(call_id="shot-1", content="captured", images=[image])
    message = ChatMessage(role="tool", tool_results=[result])

    dumped = message.model_dump()
    assert dumped["tool_results"][0]["images"][0]["data"] == "c2VjcmV0LWJ5dGVz"

    metadata = {"image_fingerprints": result.image_fingerprints()}
    assert metadata["image_fingerprints"] == [
        {
            "sha12": image.fingerprint()["sha12"],
            "length": len("c2VjcmV0LWJ5dGVz"),
            "source": "base64",
            "media_type": "image/png",
            "role": "screenshot",
        }
    ]
    assert "c2VjcmV0LWJ5dGVz" not in str(metadata)


def test_llm_request_requires_user_or_messages():
    with pytest.raises(ValidationError, match="requires either user text or messages"):
        LLMRequest()


async def test_structured_node_runs_against_plain_callable():
    client = FakeOllamaClient()
    engine = _engine(client)

    result = await engine.run("classifier", "a mug")

    assert result.status == "completed"
    assert result.output.label == "ok"
    request = client.requests[0]
    assert "Classify a mug." in request.user


async def test_usage_event_records_tokens_and_price_table_cost():
    # Model present in the engine price table -> cost estimated from tokens (cost_source=price_table).
    client = FakeOllamaClient(model="gpt-5.4-mini", tokens=(1000, 500), usd=None)
    engine = _engine(client)

    result = await engine.run("classifier", "a mug")

    events = [e for e in result.usage.events if e.node == "g4_node"]
    assert events, "callable call was not metered"
    event = events[0]
    assert event.provider == "custom"
    assert event.total_tokens == 1500
    assert event.metadata["cost_source"] == "price_table"
    assert event.metadata["cost_known"] is True
    assert event.estimated_usd and event.estimated_usd > 0


async def test_unknown_cost_is_marked_not_phantom_zero():
    # RC2: unknown local model, no cost reported -> estimated_usd stays None + cost_known=False.
    client = FakeOllamaClient(model="qwen2.5vl:3b", tokens=(0, 0), usd=None)
    engine = _engine(client)

    result = await engine.run("classifier", "a mug")

    event = [e for e in result.usage.events if e.node == "g4_node"][0]
    assert event.estimated_usd is None
    assert event.metadata["cost_known"] is False
    assert event.metadata["cost_source"] == "unknown"
    # And the run summary does not pretend the run was free-with-authority.
    assert result.usage.estimated_usd is None


async def test_callable_reported_cost_wins():
    client = FakeOllamaClient(usd=0.0123)
    engine = _engine(client)

    result = await engine.run("classifier", "a mug")

    event = [e for e in result.usage.events if e.node == "g4_node"][0]
    assert event.estimated_usd == 0.0123
    assert event.metadata["cost_source"] == "callable"


async def test_capability_timeout_enforced_on_slow_callable():
    client = FakeOllamaClient(delay=0.3)
    engine = _engine(client, timeout_s=0.05)

    result = await engine.run("classifier", "a mug")

    assert result.status == "failed"
    assert any(e.node == "classify" and e.decision == "failed" for e in result.trace)


async def test_pre_parse_cleaners_apply_to_callable_output():
    client = FakeOllamaClient(text='<think>weak model rambling</think>\n```json\n{"label": "clean"}\n```')
    node = _node(client, pre_parse=WEAK_MODEL_CLEANER)
    engine = _engine(client, node=node)

    result = await engine.run("classifier", "a mug")

    assert result.status == "completed"
    assert result.output.label == "clean"
    assert len(client.requests) == 1  # no repair call needed


async def test_repair_round_reaches_callable_with_error_context():
    class FlakyClient(FakeOllamaClient):
        async def __call__(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return LLMResponse(text="utter garbage")
            return LLMResponse(text='{"label": "repaired"}')

    client = FlakyClient()
    node = _node(client)

    result = await node.run({"item": "a mug"})

    assert result.label == "repaired"
    assert len(client.requests) == 2
    assert "previous structured-output response was invalid" in client.requests[1].user


async def test_vision_images_ride_on_the_llm_request():
    client = FakeOllamaClient(text='{"label": "seen"}')
    vision_node = StructuredVisionLLMNode(
        name="g4_vision",
        config=object(),
        output_model=Verdict,
        prompt_template="Describe frame {frame}.",
        input_variables=["frame"],
        llm=client,
    )
    images = [ImageInput(source="base64", data="aGVsbG8=", media_type="image/png", role="frame")]

    result = await vision_node.run({"frame": "f1"}, images=images)

    assert result.label == "seen"
    request = client.requests[0]
    assert len(request.images) == 1
    assert request.images[0].role == "frame"
    assert request.images[0].data == "aGVsbG8="
