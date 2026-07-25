from __future__ import annotations

import asyncio
from pathlib import Path
import time

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from ai_workflow_engine import (
    AgentRunRequest,
    CapabilityResult,
    CapabilitySpec,
    WorkflowBuilder,
    WorkflowEngine,
    build_llm_agent_capability,
)
from ai_workflow_engine.execution_window import (
    publish_invocation_window,
    reset_invocation_window,
)
from ai_workflow_engine.llm_protocol import LLMRequest, LLMResponse
from ai_workflow_tools.providers.openai_compatible import (
    ApiKeyAuth,
    NoAuth,
    OpenAICompatibleLLMClient,
    OpenAICompatibleProviderConfig,
    OpenAICompatibleProviderError,
)
from ai_workflow_tools.testing import (
    OpenAICompatibleTestServer,
    ProviderTestResponse,
    run_openai_compatible_provider_conformance,
)


pytestmark = pytest.mark.unit


def test_provider_config_is_explicit_closed_and_secret_safe():
    config = OpenAICompatibleProviderConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3:8b",
        provider="ollama",
        auth=NoAuth(),
        timeout_s=30,
    )

    assert config.base_url == "http://127.0.0.1:11434/v1"
    assert config.model == "qwen3:8b"
    assert config.provider == "ollama"
    assert "secret-value" not in repr(
        OpenAICompatibleProviderConfig(
            base_url="https://api.example.test/v1",
            model="model-1",
            provider="example",
            auth=ApiKeyAuth(api_key=SecretStr("secret-value")),
            timeout_s=30,
        )
    )

    with pytest.raises(ValidationError, match="extra_forbidden"):
        OpenAICompatibleProviderConfig(
            base_url="http://127.0.0.1:11434/v1",
            model="qwen3:8b",
            provider="ollama",
            auth=NoAuth(),
            timeout_s=30,
            invented=True,
        )


async def test_provider_client_is_constructable_without_product_configuration():
    async with OpenAICompatibleLLMClient(
        OpenAICompatibleProviderConfig(
            base_url="http://127.0.0.1:11434/v1",
            model="qwen3:8b",
            provider="ollama",
            auth=NoAuth(),
            timeout_s=30,
        )
    ) as client:
        assert client.model == "qwen3:8b"
        assert client.provider_label == "ollama"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("base_url", "https://user:pass@example.test/v1", "without credentials"),
        ("base_url", "https://example.test/v1?key=secret", "without credentials"),
        ("timeout_s", float("inf"), "finite number"),
        ("max_response_bytes", True, "valid integer"),
    ],
)
def test_provider_config_rejects_unsafe_or_ambiguous_values(field, value, message):
    values = {
        "base_url": "https://example.test/v1",
        "model": "model-1",
        "provider": "example",
        "auth": NoAuth(),
        "timeout_s": 30.0,
    }
    values[field] = value

    with pytest.raises(ValidationError, match=message):
        OpenAICompatibleProviderConfig(**values)


def test_provider_config_rejects_reserved_or_multiline_secret_headers():
    for headers in (
        {"Authorization": SecretStr("second-auth-door")},
        {"X-Tenant": SecretStr("line-one\nline-two")},
        {"X-Client-Request-Id": SecretStr("caller-controlled")},
    ):
        with pytest.raises(ValidationError):
            OpenAICompatibleProviderConfig(
                base_url="https://example.test/v1",
                model="model-1",
                provider="example",
                auth=NoAuth(),
                timeout_s=30.0,
                default_headers=headers,
            )


def _client(
    base_url: str,
    secret: str = "test-secret",
    *,
    timeout_s: float = 2.0,
    max_response_bytes: int = 8 << 20,
    no_auth: bool = False,
) -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient(
        OpenAICompatibleProviderConfig(
            base_url=base_url,
            model="test-model",
            provider="test-provider",
            auth=(
                NoAuth()
                if no_auth
                else ApiKeyAuth(api_key=SecretStr(secret))
            ),
            timeout_s=timeout_s,
            max_response_bytes=max_response_bytes,
        )
            )


def test_provider_docs_and_mageqa_handoff_name_the_public_contract():
    tools_root = Path(__file__).resolve().parents[1]
    tools_readme = (tools_root / "README.md").read_text(encoding="utf-8")
    mageqa_handoff = (
        tools_root.parent
        / "ai_workflow_engine"
        / "docs"
        / "mageqa-handoff.md"
    ).read_text(encoding="utf-8")

    for text in (tools_readme, mageqa_handoff):
        assert "OpenAICompatibleLLMClient" in text
        assert "OpenAICompatibleProviderConfig" in text
        assert "run_openai_compatible_provider_conformance" in text
        assert "missing" in text.lower() and "unknown" in text.lower()
    assert 'pip install "ai-workflow-tools[openai]"' in tools_readme
    assert "owner of the tool loop" in mageqa_handoff.lower()


async def test_real_client_passes_public_provider_conformance():
    await run_openai_compatible_provider_conformance(_client)


class _BrokenClient:
    def __init__(self, client: OpenAICompatibleLLMClient, defect: str) -> None:
        self._client = client
        self._defect = defect

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        try:
            response = await self._client(request)
        except OpenAICompatibleProviderError:
            if self._defect == "fallback_on_401":
                return LLMResponse(text="fallback", invocation_id=request.invocation_id)
            raise
        if self._defect == "usage_faker" and request.user == "conformance:simple":
            return response.model_copy(
                update={"normalized_usage": None, "usage_error": None}
            )
        if self._defect == "tool_dropper" and request.user == "conformance:inspect-image":
            return response.model_copy(update={"tool_calls": []})
        if self._defect == "identity_leaker":
            return response.model_copy(update={"invocation_id": "shared"})
        if (
            self._defect == "cross_call_bleed"
            and request.user == "conformance:concurrent-a"
        ):
            return response.model_copy(update={"text": "concurrent-b"})
        return response

    async def aclose(self) -> None:
        await self._client.aclose()


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("usage_faker", "usage must be normalized"),
        ("tool_dropper", "structured tool call"),
        ("fallback_on_401", "401 must fail loudly"),
        ("identity_leaker", "invocation identity"),
        ("cross_call_bleed", "must not share response"),
    ],
)
async def test_public_conformance_rejects_named_broken_clients(defect, message):
    def make_broken(base_url: str, secret: str) -> _BrokenClient:
        return _BrokenClient(_client(base_url, secret), defect)

    with pytest.raises(AssertionError, match=message):
        await run_openai_compatible_provider_conformance(make_broken)


async def test_no_auth_mode_sends_no_authorization_header():
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            200,
            {
                "model": "local",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )
    ) as server:
        async with _client(server.base_url, no_auth=True) as client:
            response = await client(LLMRequest(user="hello"))

    assert response.text == "ok"
    assert "authorization" not in server.requests[0].headers


async def test_response_body_is_rejected_at_streaming_byte_limit():
    oversized = "x" * 4096
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            200,
            {
                "model": "local",
                "choices": [
                    {"message": {"content": oversized}, "finish_reason": "stop"}
                ],
            },
        )
    ) as server:
        async with _client(server.base_url, max_response_bytes=1024) as client:
            with pytest.raises(
                OpenAICompatibleProviderError,
                match="exceeded configured byte limit",
            ) as raised:
                await client(LLMRequest(user="oversized"))

    assert raised.value.failure_kind == "invalid_response"


async def test_429_is_typed_bounded_secret_safe_and_never_retried():
    secret = "do-not-leak"
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            429,
            {
                "error": {
                    "code": "busy",
                    "message": f"retry token={secret}\nsoon",
                }
            },
            headers={"retry-after": "7"},
        )
    ) as server:
        async with _client(server.base_url, secret) as client:
            with pytest.raises(OpenAICompatibleProviderError) as raised:
                await client(LLMRequest(user="rate limited"))

    error = raised.value
    assert error.failure_kind == "rate_limit"
    assert error.retry_after_s == 7
    assert len(server.requests) == 1
    assert secret not in str(error)
    assert "\n" not in str(error)


async def test_non_json_http_failure_preserves_provider_status_and_classification():
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            502,
            None,
            raw_body=b"<html>upstream unavailable</html>",
        )
    ) as server:
        async with _client(server.base_url) as client:
            with pytest.raises(OpenAICompatibleProviderError) as raised:
                await client(LLMRequest(user="provider outage"))

    error = raised.value
    assert error.failure_kind == "provider"
    assert error.status_code == 502
    assert str(error) == "provider HTTP 502: request failed"
    assert len(server.requests) == 1


async def test_non_json_success_is_a_typed_invalid_response():
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            200,
            None,
            raw_body=b"not-json",
        )
    ) as server:
        async with _client(server.base_url) as client:
            with pytest.raises(OpenAICompatibleProviderError) as raised:
                await client(LLMRequest(user="bad success body"))

    assert raised.value.failure_kind == "invalid_response"
    assert raised.value.status_code == 200
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "usage",
    [
        {
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "total_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
        {
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "total_tokens": 3,
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
        {
            "prompt_tokens": True,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    ],
)
async def test_malformed_usage_preserves_model_result_as_typed_unknown(usage):
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            200,
            {
                "model": "local",
                "choices": [{"message": {"content": "usable"}, "finish_reason": "stop"}],
                "usage": usage,
            },
        )
    ) as server:
        async with _client(server.base_url) as client:
            response = await client(LLMRequest(user="usage edge"))

    assert response.text == "usable"
    assert response.normalized_usage is None
    assert response.usage_error == "invalid_token_counters"
    assert response.input_tokens == 0
    assert response.output_tokens == 0


async def test_blank_response_model_falls_back_to_configured_model():
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            200,
            {
                "model": "   ",
                "choices": [{"message": {"content": "usable"}, "finish_reason": "stop"}],
            },
        )
    ) as server:
        async with _client(server.base_url) as client:
            response = await client(LLMRequest(user="model identity"))

    assert response.model == "test-model"


@pytest.mark.parametrize(
    "choice",
    [
        {
            "message": {"content": None, "refusal": "policy blocked the request"},
            "finish_reason": "stop",
        },
        {
            "message": {"content": None},
            "finish_reason": "content_filter",
        },
    ],
)
async def test_provider_refusal_is_typed_and_preserves_usage(choice):
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            200,
            {
                "model": "local",
                "choices": [choice],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 1,
                    "total_tokens": 8,
                },
            },
        )
    ) as server:
        async with _client(server.base_url) as client:
            with pytest.raises(OpenAICompatibleProviderError) as raised:
                await client(LLMRequest(user="refusal truth"))

    error = raised.value
    assert error.failure_kind == "refusal"
    assert error.normalized_usage is not None
    assert error.normalized_usage.raw_total_tokens == 8
    assert error.usage_error is None
    assert error.model == "local"
    expected_fact = (
        "policy blocked the request"
        if choice["message"].get("refusal")
        else "content_filter"
    )
    assert expected_fact in str(error)
    assert len(str(error)) <= 500


async def test_engine_invocation_window_narrows_transport_timeout():
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            200,
            {
                "model": "local",
                "choices": [{"message": {"content": "late"}, "finish_reason": "stop"}],
            },
            delay_s=0.5,
        )
    ) as server:
        async with _client(server.base_url, timeout_s=2.0) as client:
            now = time.monotonic()
            token = publish_invocation_window(
                soft_deadline_monotonic=now + 0.05,
                hard_deadline_monotonic=now + 0.1,
            )
            started = time.monotonic()
            try:
                with pytest.raises(OpenAICompatibleProviderError) as raised:
                    await client(LLMRequest(user="must time out"))
                elapsed_s = time.monotonic() - started
            finally:
                reset_invocation_window(token)

    assert raised.value.failure_kind == "timeout"
    assert elapsed_s < 0.4
    assert len(server.requests) == 1


async def test_configured_timeout_is_total_wall_time_not_per_chunk_inactivity():
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            200,
            {
                "model": "local",
                "choices": [{"message": {"content": "too late"}, "finish_reason": "stop"}],
            },
            chunk_size=1,
            chunk_delay_s=0.02,
        )
    ) as server:
        async with _client(server.base_url, timeout_s=0.08) as client:
            started = time.monotonic()
            with pytest.raises(OpenAICompatibleProviderError) as raised:
                await client(LLMRequest(user="trickle forever"))
            elapsed_s = time.monotonic() - started

    assert raised.value.failure_kind == "timeout"
    assert elapsed_s < 0.4
    assert len(server.requests) == 1


async def test_redirect_is_a_typed_provider_failure_and_is_not_followed():
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            307,
            {"error": {"message": "moved"}},
            headers={"location": "https://credentials.example.test/collect"},
        )
    ) as server:
        async with _client(server.base_url) as client:
            with pytest.raises(OpenAICompatibleProviderError) as raised:
                await client(LLMRequest(user="do not redirect"))

    assert raised.value.failure_kind == "provider"
    assert raised.value.status_code == 307
    assert len(server.requests) == 1


async def test_blank_provider_tool_identity_is_rejected():
    with OpenAICompatibleTestServer(
        lambda _request: ProviderTestResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )
    ) as server:
        async with _client(server.base_url) as client:
            with pytest.raises(
                OpenAICompatibleProviderError,
                match="requires nonblank id",
            ):
                await client(LLMRequest(user="tool"))


class _ProbeInput(BaseModel):
    value: int


class _AgentAnswer(BaseModel):
    answer: str


async def test_public_engine_agent_runs_real_http_tool_round_trip():
    def responder(request):
        messages = request.payload["messages"]
        if any(row.get("role") == "tool" for row in messages):
            return ProviderTestResponse(
                200,
                {
                    "model": "test-model",
                    "choices": [
                        {
                            "message": {"content": '{"answer":"seen-7"}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 4,
                        "total_tokens": 15,
                    },
                },
            )
        return ProviderTestResponse(
            200,
            {
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "probe-1",
                                    "type": "function",
                                    "function": {
                                        "name": "probe",
                                        "arguments": '{"value":7}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 3,
                    "total_tokens": 11,
                },
            },
        )

    tool_calls: list[_ProbeInput] = []

    async def probe(_context, payload: _ProbeInput):
        tool_calls.append(payload)
        return CapabilityResult(status="accepted", output={"seen": payload.value})

    with OpenAICompatibleTestServer(responder) as server:
        async with _client(server.base_url) as client:
            engine = WorkflowEngine()
            engine.register_capability_spec(
                CapabilitySpec(
                    name="probe",
                    kind="tool",
                    description="Return one typed integer",
                    input_model=_ProbeInput,
                ),
                probe,
            )
            agent = build_llm_agent_capability(
                client,
                engine.registry,
                allowed_tools=["probe"],
                name="provider_agent",
                runtime=engine.runtime,
                system_prompt="Use the probe, then return JSON.",
                output_model=_AgentAnswer,
                node_name="provider_agent",
            )
            engine.register_capability_spec(agent.spec, agent)
            engine.register_workflow(
                WorkflowBuilder("provider_agent_flow").step("provider_agent").build()
            )

            result = await engine.run(
                "provider_agent_flow",
                AgentRunRequest(
                    prompt="Probe value 7.",
                    allowed_tools=["probe"],
                    max_steps=3,
                ),
            )

    assert result.status == "completed"
    assert result.output.status == "completed"
    assert result.output.output == _AgentAnswer(answer="seen-7")
    assert tool_calls == [_ProbeInput(value=7)]
    assert len(server.requests) == 2
    assert len(result.usage.events) == 2
    assert all(event.provider == "test-provider" for event in result.usage.events)
    assert all(event.cost_class == "metered" for event in result.usage.events)
