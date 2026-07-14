"""Prompt observability: static manifest (viz.render_prompt_manifest) + runtime capture
(prompt_capture.PromptCapturingLLMClient). Both are additive, read-only over existing seams."""

from types import SimpleNamespace

import pytest
from langchain_core.prompts import PromptTemplate

from ai_workflow_engine.engine import InMemoryDetailSink
from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest, LLMResponse, ToolCallRequest
from ai_workflow_engine.observability_capture import engine_worker_observation_scope
from ai_workflow_engine.prompt_capture import PromptCapturingLLMClient
from ai_workflow_engine.viz import render_prompt_manifest
from ai_workflow_engine.transport_models import ImageInput
from ai_workflow_engine.workflow import WorkflowBuilder
from ai_workflow_viewer import build_observation_graph, observation_graph_to_html

pytestmark = pytest.mark.unit


class _CollectSink:
    """Minimal structural TraceSink that keeps every recorded event."""

    def __init__(self) -> None:
        self.events = []

    def record(self, event) -> None:
        self.events.append(event)


def test_prompt_manifest_dumps_node_prompts_and_marks_promptless_nodes():
    workflow = WorkflowBuilder("qa").step("answer").step("review").step("deliver").build()
    handlers = {
        "answer": SimpleNamespace(prompt=PromptTemplate(template="Answer {q}", input_variables=["q"])),
        "review": SimpleNamespace(planner=SimpleNamespace(system_prompt="You are a QA agent.")),
        "deliver": SimpleNamespace(),  # registered, but carries no prompt
    }

    class _Registry:
        def get(self, name):
            if name not in handlers:
                raise KeyError(name)
            return (None, handlers[name])

    manifest = render_prompt_manifest(workflow, _Registry())

    # structure: every node appears with its kind
    assert "answer" in manifest and "review" in manifest and "deliver" in manifest
    # prompts surfaced from the two prompt-bearing shapes
    assert "[prompt]" in manifest and "Answer {q}" in manifest
    assert "[agent_system]" in manifest and "You are a QA agent." in manifest
    # promptless capability is flagged, not silently blank
    assert "no static prompt" in manifest


def test_prompt_capturing_client_requires_shared_detail_sink():
    async def inner(request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="ok")

    with pytest.raises(TypeError, match="detail_sink"):
        PromptCapturingLLMClient(inner, _CollectSink())


async def test_prompt_capturing_client_records_compact_trace_without_pseudo_detail_by_default():
    sink = _CollectSink()
    details = InMemoryDetailSink()
    seen = {}

    async def inner(request: LLMRequest) -> LLMResponse:
        seen["request"] = request
        return LLMResponse(text="ok")

    client = PromptCapturingLLMClient(inner, sink, detail_sink=details)
    secret_image = ImageInput(source="base64", data="RAW_SECRET_BYTES", media_type="image/png")
    request = LLMRequest(
        messages=[
            ChatMessage(role="system", content="be terse"),
            ChatMessage(role="user", content="hello world", images=[secret_image]),
        ],
        metadata={"agent_node": "ask", "workflow_id": "wf1", "repair_round": 0},
    )

    response = await client(request)

    # delegates unchanged
    assert response.text == "ok"
    assert seen["request"] is request
    # recorded prompt + response events, labelled by node
    assert len(sink.events) == 2
    event = sink.events[0]
    assert event.decision == "llm:prompt"
    assert event.node == "ask"
    # default is compact trace only: prompt text is not in the event, payload hashes are not computed,
    # and no SHA-only detail is created.
    blob = event.model_dump_json()
    assert "prompt_digest" not in blob
    assert "detail_digest" not in blob
    assert "hello world" not in blob
    assert "be terse" not in blob
    assert "RAW_SECRET_BYTES" not in blob
    assert event.phase == "llm:request"
    assert event.detail_refs == []
    assert details.details == []


async def test_prompt_capturing_client_skips_when_engine_worker_observation_is_active():
    sink = _CollectSink()
    details = InMemoryDetailSink()
    seen = {}

    async def inner(request: LLMRequest) -> LLMResponse:
        seen["request"] = request
        return LLMResponse(text="ok")

    client = PromptCapturingLLMClient(inner, sink, detail_sink=details, capture_text=True)
    request = LLMRequest(
        messages=[ChatMessage(role="user", content="hello")],
        metadata={"agent_node": "worker"},
    )

    with engine_worker_observation_scope():
        response = await client(request)

    assert response.text == "ok"
    assert seen["request"] is request
    assert sink.events == []
    assert details.details == []


@pytest.mark.unit
async def test_prompt_capturing_client_can_capture_full_text_in_detail_only():
    sink = _CollectSink()
    details = InMemoryDetailSink()

    async def inner(request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="ok")

    client = PromptCapturingLLMClient(inner, sink, detail_sink=details, capture_text=True)
    request = LLMRequest(
        messages=[
            ChatMessage(role="system", content="be terse"),
            ChatMessage(role="user", content="hello world"),
        ],
        metadata={"agent_node": "ask"},
    )

    await client(request)

    event = sink.events[0]
    detail = details.details[0]
    assert "hello world" not in event.model_dump_json()
    assert "be terse" not in event.model_dump_json()
    assert "hello world" in detail.text
    assert "be terse" in detail.text
    assert detail.redaction_state == "none"
    definition = WorkflowBuilder("prompt_run").step("ask").build()
    graph = build_observation_graph(definition, sink.events, details=details.details)
    assert event.detail_refs[0] in graph.details
    html = observation_graph_to_html(definition, graph)
    assert "hello world" in html


async def test_prompt_capturing_client_records_llm_response_compact_trace_by_default():
    sink = _CollectSink()
    details = InMemoryDetailSink()
    expected = LLMResponse(
        text="model answer with SECRET",
        model="unit-model",
        output_tokens=7,
        total_tokens=11,
        tool_calls=[ToolCallRequest(call_id="call-1", name="lookup", arguments={"q": "SECRET"})],
    )

    async def inner(_request: LLMRequest) -> LLMResponse:
        return expected

    client = PromptCapturingLLMClient(inner, sink, detail_sink=details)
    response = await client(
        LLMRequest(messages=[ChatMessage(role="user", content="hello")], metadata={"agent_node": "ask"})
    )

    assert response is expected
    response_event = sink.events[1]
    assert response_event.decision == "llm:response"
    assert response_event.phase == "llm:response"
    assert response_event.detail_refs == []
    assert details.details == []
    blob = response_event.model_dump_json()
    assert "model answer" not in blob
    assert "SECRET" not in blob


async def test_prompt_capturing_client_captures_response_text_in_detail_only():
    sink = _CollectSink()
    details = InMemoryDetailSink()

    async def inner(_request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="visible answer")

    client = PromptCapturingLLMClient(inner, sink, detail_sink=details, capture_text=True)
    await client(LLMRequest(messages=[ChatMessage(role="user", content="hello")]))

    response_event = sink.events[1]
    response_detail = details.details[1]
    assert "visible answer" not in response_event.model_dump_json()
    assert "visible answer" in response_detail.text
    assert response_detail.json_value["text"] == "visible answer"
    assert response_detail.redaction_state == "none"


async def test_prompt_capturing_client_records_error_response_then_reraises():
    sink = _CollectSink()
    details = InMemoryDetailSink()

    class ModelDown(RuntimeError):
        pass

    expected = ModelDown("model unavailable")

    async def inner(_request: LLMRequest) -> LLMResponse:
        raise expected

    client = PromptCapturingLLMClient(inner, sink, detail_sink=details, capture_text=True)

    with pytest.raises(ModelDown) as raised:
        await client(LLMRequest(messages=[ChatMessage(role="user", content="hello")]))

    assert raised.value is expected
    response_event = sink.events[1]
    response_detail = details.details[1]
    assert response_event.decision == "llm:response"
    assert response_event.phase == "llm:response"
    assert response_event.severity == "error"
    assert response_event.error == "model unavailable"
    assert response_event.detail_refs == [response_detail.detail_id]
    assert response_detail.kind == "llm_response"
    assert response_detail.redaction_state == "none"
    assert response_detail.json_value["error"] == "model unavailable"



def test_llm_request_modes_are_exclusive_and_deliberate():
    """v0.11 clean contract (manifest row M5): simple mode (system?/user/+images) and multi-turn
    mode (messages only) are both first-class; mixing them fails loudly — no silent precedence."""

    import pytest as _pytest

    simple = LLMRequest(system="be terse", user="hi", images=[])
    assert simple.user == "hi" and not simple.messages

    multi = LLMRequest(messages=[
        ChatMessage(role="system", content="be terse"),
        ChatMessage(role="user", content="hi"),
    ])
    assert multi.messages and not multi.user and multi.system is None

    with _pytest.raises(ValueError, match="modes are exclusive"):
        LLMRequest(user="hi", messages=[ChatMessage(role="user", content="x")])
    with _pytest.raises(ValueError, match="modes are exclusive"):
        LLMRequest(system="s", messages=[ChatMessage(role="user", content="x")])
    with _pytest.raises(ValueError, match="modes are exclusive"):
        LLMRequest(
            images=[ImageInput(source="base64", data="QUFB", media_type="image/png")],
            messages=[ChatMessage(role="user", content="x")],
        )
    with _pytest.raises(ValueError, match="either user text or messages"):
        LLMRequest()
