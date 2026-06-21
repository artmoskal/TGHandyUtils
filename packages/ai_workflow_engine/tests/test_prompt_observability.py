"""Prompt observability: static manifest (viz.render_prompt_manifest) + runtime capture
(prompt_capture.PromptCapturingLLMClient). Both are additive, read-only over existing seams."""

from types import SimpleNamespace

import pytest
from langchain_core.prompts import PromptTemplate

from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest, LLMResponse
from ai_workflow_engine.prompt_capture import PromptCapturingLLMClient
from ai_workflow_engine.viz import render_prompt_manifest
from ai_workflow_engine.vision import ImageInput
from ai_workflow_engine.workflow import WorkflowBuilder

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


async def test_prompt_capturing_client_records_byte_free_prompt_then_delegates():
    sink = _CollectSink()
    seen = {}

    async def inner(request: LLMRequest) -> LLMResponse:
        seen["request"] = request
        return LLMResponse(text="ok")

    client = PromptCapturingLLMClient(inner, sink)
    secret_image = ImageInput(source="base64", data="RAW_SECRET_BYTES", media_type="image/png")
    request = LLMRequest(
        system="be terse",
        messages=[ChatMessage(role="user", content="hello world", images=[secret_image])],
        metadata={"agent_node": "ask", "workflow_id": "wf1", "repair_round": 0},
    )

    response = await client(request)

    # delegates unchanged
    assert response.text == "ok"
    assert seen["request"] is request
    # recorded exactly one prompt event, labelled by node
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.decision == "llm:prompt"
    assert event.node == "ask"
    # prompt text captured, raw image bytes NEVER in the trace (fingerprints only)
    blob = event.model_dump_json()
    assert "hello world" in blob
    assert "be terse" in blob
    assert "RAW_SECRET_BYTES" not in blob
