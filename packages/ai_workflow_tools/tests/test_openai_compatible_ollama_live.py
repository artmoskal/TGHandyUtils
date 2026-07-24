"""Real local Ollama qualification for the generic provider adapter.

This is no-spend but intentionally lives in the integration tier: it requires a running
host Ollama service and an installed multimodal/tool-capable model.
"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
import zlib

import pytest
from pydantic import BaseModel

from ai_workflow_engine import (
    AgentRunRequest,
    CapabilityResult,
    CapabilitySpec,
    WorkflowBuilder,
    WorkflowEngine,
    build_llm_agent_capability,
)
from ai_workflow_engine.llm_protocol import LLMRequest, ToolSpec
from ai_workflow_engine.transport_models import ImageInput
from ai_workflow_tools.providers.openai_compatible import (
    NoAuth,
    OpenAICompatibleLLMClient,
    OpenAICompatibleProviderConfig,
    OpenAICompatibleProviderError,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_OLLAMA_PROVIDER_QUALIFICATION") != "1",
        reason="set RUN_OLLAMA_PROVIDER_QUALIFICATION=1 for real local Ollama proof",
    ),
]

BASE_URL = os.getenv("OLLAMA_BASE_URL") or "http://host.docker.internal:11434/v1"
MODEL = (
    os.getenv("OLLAMA_MODEL")
    or "ministral-3:8b-instruct-2512-q4_K_M"
)
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL") or "qwen2.5vl:3b"


def _client(
    *,
    timeout_s: float = 120.0,
    model: str = MODEL,
) -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient(
        OpenAICompatibleProviderConfig(
            base_url=BASE_URL,
            model=model,
            provider="ollama",
            auth=NoAuth(),
            timeout_s=timeout_s,
            temperature=0.0,
            max_completion_tokens=80,
        )
    )


def _assert_usage_is_honest(response) -> None:
    if response.normalized_usage is None:
        assert response.usage_error == "usage_event_missing"
        assert response.input_tokens == 0
        assert response.output_tokens == 0
    else:
        assert response.usage_error is None
        assert response.normalized_usage.raw_input_tokens >= 0
        assert response.normalized_usage.raw_output_tokens > 0
        assert response.total_tokens == (
            response.input_tokens + response.output_tokens
        )


async def test_real_ollama_text_and_usage_contract():
    async with _client() as client:
        response = await client(
            LLMRequest(
                system="Answer concisely.",
                user="Reply with exactly OLLAMA_TEXT_OK.",
                invocation_id="ollama-live-text",
            )
        )

    assert "OLLAMA_TEXT_OK" in response.text
    assert response.model
    assert response.invocation_id == "ollama-live-text"
    assert response.metadata["provider"] == "ollama"
    _assert_usage_is_honest(response)


async def test_real_ollama_structured_tool_call():
    async with _client() as client:
        response = await client(
            LLMRequest(
                user=(
                    "Use the add_numbers tool with a=2 and b=3. "
                    "Do not answer without calling the tool."
                ),
                tools=[
                    ToolSpec(
                        name="add_numbers",
                        description="Add two integers",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "a": {"type": "integer"},
                                "b": {"type": "integer"},
                            },
                            "required": ["a", "b"],
                            "additionalProperties": False,
                        },
                    )
                ],
                tool_choice="required",
                invocation_id="ollama-live-tool",
            )
        )

    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.name == "add_numbers"
    assert call.arguments == {"a": 2, "b": 3}
    _assert_usage_is_honest(response)


class _LiveProbeInput(BaseModel):
    value: int


class _LiveAgentAnswer(BaseModel):
    answer: str


async def test_real_ollama_drives_engine_agent_tool_round_trip():
    calls: list[_LiveProbeInput] = []

    async def probe(_context, payload: _LiveProbeInput):
        calls.append(payload)
        return CapabilityResult(
            status="accepted",
            output={"answer": f"seen-{payload.value}"},
        )

    async with _client() as client:
        engine = WorkflowEngine()
        engine.register_capability_spec(
            CapabilitySpec(
                name="probe",
                kind="tool",
                description="Return one typed integer as an answer token",
                input_model=_LiveProbeInput,
            ),
            probe,
        )
        agent = build_llm_agent_capability(
            client,
            engine.registry,
            allowed_tools=["probe"],
            name="ollama_agent",
            runtime=engine.runtime,
            system_prompt=(
                "You must call the probe tool once with value 7. After its result, "
                'return only JSON in the exact shape {"answer":"seen-7"}.'
            ),
            output_model=_LiveAgentAnswer,
            node_name="ollama_agent",
        )
        engine.register_capability_spec(agent.spec, agent)
        engine.register_workflow(
            WorkflowBuilder("ollama_agent_flow").step("ollama_agent").build()
        )

        result = await engine.run(
            "ollama_agent_flow",
            AgentRunRequest(
                prompt="Run the required probe now.",
                allowed_tools=["probe"],
                max_steps=3,
            ),
        )

    assert result.status == "completed"
    assert result.output.output == _LiveAgentAnswer(answer="seen-7")
    assert calls == [_LiveProbeInput(value=7)]
    assert len(result.usage.events) == 2
    assert all(event.provider == "ollama" for event in result.usage.events)
    assert all(event.success for event in result.usage.events)


async def test_real_ollama_multimodal_image():
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    width = height = 64
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanline = b"\x00" + bytes((220, 30, 30)) * width
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanline * height))
        + chunk(b"IEND", b"")
    )
    image = ImageInput(
        source="base64",
        data=base64.b64encode(png).decode("ascii"),
        media_type="image/png",
        role="qualification",
    )
    async with _client(model=VISION_MODEL) as client:
        response = await client(
            LLMRequest(
                user=(
                    "Inspect the attached image. Reply with the exact token IMAGE_OK, "
                    "then one short visual description."
                ),
                images=[image],
                invocation_id="ollama-live-image",
            )
        )

    assert "IMAGE_OK" in response.text
    assert len(response.text) < 1000
    _assert_usage_is_honest(response)


async def test_real_ollama_timeout_is_typed():
    async with _client(timeout_s=0.001) as client:
        with pytest.raises(OpenAICompatibleProviderError) as raised:
            await client(
                LLMRequest(
                    user="Write a long essay about timeout testing.",
                    invocation_id="ollama-live-timeout",
                )
            )

    assert raised.value.failure_kind == "timeout"
    assert raised.value.invocation_id == "ollama-live-timeout"


async def test_real_ollama_concurrent_calls_are_isolated():
    # Use the smaller vision model for this host-capacity proof. Ollama may serialize or
    # evict models under memory pressure; concurrency here proves the adapter keeps two
    # in-flight calls isolated, not that every model/server can execute them in parallel.
    async with _client(model=VISION_MODEL) as client:
        left, right = await asyncio.gather(
            client(
                LLMRequest(
                    user="Reply with exactly CONCURRENT_LEFT.",
                    invocation_id="ollama-live-left",
                )
            ),
            client(
                LLMRequest(
                    user="Reply with exactly CONCURRENT_RIGHT.",
                    invocation_id="ollama-live-right",
                )
            ),
        )

    assert "CONCURRENT_LEFT" in left.text
    assert "CONCURRENT_RIGHT" in right.text
    assert left.invocation_id == "ollama-live-left"
    assert right.invocation_id == "ollama-live-right"
    assert left.invocation_id != right.invocation_id
