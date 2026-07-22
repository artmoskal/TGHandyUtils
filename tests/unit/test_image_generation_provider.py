"""Unit tests for the reusable image-generation provider seam."""

import base64
from types import SimpleNamespace

import pytest

from ai_workflow_tools.media.image_models import GeneratedImage, ImageGenerationRequest
from ai_workflow_engine.models import WorkflowRunContext, WorkflowUsageSummary
from ai_workflow_tools.media.image_generation import (
    ComparisonImageGenerator,
    GeminiImageGenerator,
    OpenAIImageGenerator,
    create_image_generator,
)
from ai_workflow_engine.budget import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope


class FakeOpenAIImages:
    def __init__(self):
        self.generate_kwargs = None
        self.edit_kwargs = None

    async def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return self._response()

    async def edit(self, **kwargs):
        self.edit_kwargs = kwargs
        return self._response()

    @staticmethod
    def _response():
        data = [SimpleNamespace(b64_json=base64.b64encode(b"image").decode("ascii"))]
        return SimpleNamespace(
            data=data,
            usage={
                "input_tokens": 4,
                "input_tokens_details": {"text_tokens": 4, "image_tokens": 0},
                "output_tokens": 196,
                "output_tokens_details": {"text_tokens": 0, "image_tokens": 196},
                "total_tokens": 200,
            },
            _request_id="req-image",
        )


class FakeGeminiResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _gemini_payload(image_bytes=b"gemini-image", cached_tokens=0):
    usage = {
        "promptTokenCount": 10,
        "candidatesTokenCount": 2,
        "totalTokenCount": 12,
    }
    if cached_tokens:
        usage["cachedContentTokenCount"] = cached_tokens
    return {
        "responseId": "gemini-req",
        "usageMetadata": usage,
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        }
                    ]
                }
            }
        ],
    }


@pytest.mark.unit
async def test_openai_image_generator_uses_generate_without_references(tmp_path):
    images = FakeOpenAIImages()
    generator = OpenAIImageGenerator(SimpleNamespace(OPENAI_API_KEY="test"))
    generator._client = SimpleNamespace(images=images)

    result = await generator.generate(
        ImageGenerationRequest(
            prompt="study image",
            output_dir=str(tmp_path),
            output_basename="card.png",
            model="gpt-image-2",
        )
    )

    assert result.basename == "card.png"
    assert result.provider == "openai"
    assert (tmp_path / "card.png").read_bytes() == b"image"
    assert images.generate_kwargs["model"] == "gpt-image-2"
    assert "response_format" not in images.generate_kwargs
    assert images.edit_kwargs is None
    assert result.usage_metadata["total_tokens"] == 200
    assert result.request_id == "req-image"


@pytest.mark.unit
async def test_openai_image_generator_uses_edit_with_references(tmp_path):
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"ref")
    images = FakeOpenAIImages()
    generator = OpenAIImageGenerator(SimpleNamespace(OPENAI_API_KEY="test"))
    generator._client = SimpleNamespace(images=images)

    result = await generator.generate(
        ImageGenerationRequest(
            prompt="study image",
            output_dir=str(tmp_path),
            output_basename="card.png",
            reference_image_paths=[str(reference)],
        )
    )

    assert result.reference_image_count == 1
    assert images.edit_kwargs["model"] == "gpt-image-2"
    assert "response_format" not in images.edit_kwargs
    assert images.generate_kwargs is None


@pytest.mark.unit
async def test_openai_image_generator_records_workflow_usage(tmp_path):
    images = FakeOpenAIImages()
    generator = OpenAIImageGenerator(SimpleNamespace(OPENAI_API_KEY="test", WORKFLOW_USAGE_TRACKING_ENABLED=True))
    generator._client = SimpleNamespace(images=images)
    summary = WorkflowUsageSummary()
    context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf1", workflow_type="anki_generation"),
        summary,
        WorkflowBudget(max_image_calls=1),
    )

    with workflow_usage_scope(context):
        await generator.generate(
            ImageGenerationRequest(
                prompt="study image",
                output_dir=str(tmp_path),
                output_basename="card.png",
                model="gpt-image-2",
            )
        )

    assert summary.image_call_count == 1
    assert summary.events[0].request_id == "req-image"
    assert summary.events[0].total_tokens == 200
    assert summary.events[0].estimated_usd == 0.0059


@pytest.mark.unit
async def test_gemini_image_generator_uses_rest_payload_with_reference_and_cache(tmp_path):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeGeminiResponse(_gemini_payload(cached_tokens=4))

    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference")
    generator = GeminiImageGenerator(
        SimpleNamespace(
            GEMINI_API_KEY="test-gemini-key",
            WORKFLOW_GEMINI_IMAGE_SIZE="1K",
            WORKFLOW_USAGE_TRACKING_ENABLED=True,
        ),
        http_post=fake_post,
    )
    summary = WorkflowUsageSummary()
    context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf1", workflow_type="anki_generation"),
        summary,
        WorkflowBudget(max_image_calls=1),
    )

    with workflow_usage_scope(context):
        result = await generator.generate(
            ImageGenerationRequest(
                prompt="study image",
                output_dir=str(tmp_path),
                output_basename="card.png",
                model="gemini-3.1-flash-image",
                size="1536x1024",
                reference_image_paths=[str(reference)],
            )
        )

    assert result.provider == "gemini"
    assert result.request_id == "gemini-req"
    assert (tmp_path / "card.png").read_bytes() == b"gemini-image"
    assert calls[0][0].endswith("/v1beta/models/gemini-3.1-flash-image:generateContent")
    payload = calls[0][1]["json"]
    assert payload["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
    assert "responseFormat" not in payload["generationConfig"]
    assert payload["contents"][0]["parts"][1]["inlineData"]["data"]
    assert summary.image_call_count == 1
    assert summary.events[0].provider == "gemini"
    assert summary.events[0].input_token_details["cached_tokens"] == 4
    assert summary.events[0].output_token_details["image_tokens"] == 1120
    assert summary.events[0].estimated_usd == 0.067205


@pytest.mark.unit
async def test_gemini_image_generator_can_opt_into_response_format(tmp_path):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeGeminiResponse(_gemini_payload())

    generator = GeminiImageGenerator(
        SimpleNamespace(
            GEMINI_API_KEY="test-gemini-key",
            WORKFLOW_GEMINI_RESPONSE_FORMAT_ENABLED=True,
            WORKFLOW_GEMINI_IMAGE_SIZE="1K",
            WORKFLOW_USAGE_TRACKING_ENABLED=False,
        ),
        http_post=fake_post,
    )

    await generator.generate(
        ImageGenerationRequest(
            prompt="study image",
            output_dir=str(tmp_path),
            output_basename="card.png",
            model="gemini-3.1-flash-image",
            size="1536x1024",
        )
    )

    payload = calls[0][1]["json"]
    assert payload["generationConfig"]["responseFormat"]["image"]["aspectRatio"] == "16:9"
    assert payload["generationConfig"]["responseFormat"]["image"]["imageSize"] == "1K"


@pytest.mark.unit
async def test_create_image_generator_switches_provider_from_config():
    openai = create_image_generator(SimpleNamespace(WORKFLOW_IMAGE_PROVIDER="openai"))
    gemini = create_image_generator(SimpleNamespace(WORKFLOW_IMAGE_PROVIDER="gemini"))
    compare = create_image_generator(
        SimpleNamespace(
            WORKFLOW_IMAGE_PROVIDER="comparison",
            WORKFLOW_IMAGE_COMPARE_PROVIDERS="openai,gemini",
            WORKFLOW_IMAGE_COMPARISON_PRIMARY_PROVIDER="gemini",
        )
    )

    assert isinstance(openai, OpenAIImageGenerator)
    assert isinstance(gemini, GeminiImageGenerator)
    assert isinstance(compare, ComparisonImageGenerator)
    assert compare.providers == ["openai", "gemini"]
    assert compare.primary_provider == "gemini"


@pytest.mark.unit
async def test_comparison_generator_runs_both_providers_and_keeps_primary_metadata(tmp_path):
    class StaticGenerator:
        def __init__(self, provider):
            self.provider = provider

        async def generate(self, request):
            path = tmp_path / f"{self.provider}.png"
            path.write_bytes(self.provider.encode("ascii"))
            return GeneratedImage(
                path=str(path),
                basename=path.name,
                provider=self.provider,
                model=request.model,
                size=request.size,
                quality=request.quality,
                output_format=request.output_format,
                reference_image_count=0,
                style_reference_version=None,
                usage_metadata={},
                estimated_usd=0.01,
                request_id=f"req-{self.provider}",
            )

    generator = ComparisonImageGenerator(
        SimpleNamespace(
            WORKFLOW_OPENAI_IMAGE_MODEL="gpt-image-2",
            WORKFLOW_GEMINI_IMAGE_MODEL="gemini-3.1-flash-image",
        ),
        providers=["openai", "gemini"],
        primary_provider="gemini",
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "ai_workflow_tools.media.image_generation._generator_for_provider",
        lambda provider, config: StaticGenerator(provider),
    )
    try:
        result = await generator.generate(
            ImageGenerationRequest(prompt="x", output_dir=str(tmp_path), output_basename="card.png")
        )
    finally:
        monkeypatch.undo()

    assert result.provider == "gemini"
    assert result.usage_metadata["comparison_primary_provider"] == "gemini"
    assert result.usage_metadata["comparison_alternatives"][0]["provider"] == "openai"


# --- ChatGPT-browser provider (subscription session over HTTP) -------------------------


class FakeChatGptResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


def _chatgpt_config(**overrides):
    values = {
        "WORKFLOW_CHATGPT_BROWSER_URL": "http://127.0.0.1:8010",
        "WORKFLOW_CHATGPT_BROWSER_TOKEN": "",
        "WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS": 340,
        "WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE": "reuse",
        "WORKFLOW_USAGE_TRACKING_ENABLED": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


_CHATGPT_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _chatgpt_image_payload(image_bytes=_CHATGPT_PNG_BYTES, **overrides):
    encoded = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "status": "completed",
        "mime": "image/png",
        "bytes": len(image_bytes),
        "source_url": "https://chatgpt.com/backend-api/x",
        "image_data_url": f"data:image/png;base64,{encoded}",
        "reference_images_used": 0,
        "conversation_mode": "reuse",
        "conversation_name": "anki-provider-contract",
        "generation": 1,
        "reused": True,
        "rotated": False,
        "freshness": {
            "schema": 4,
            "strategy": "resultFreshnessFenceV3",
            "source": "captured",
            "state": "result_observed",
            "anchor_owned": True,
            "result_owned": True,
            "result_scope_count": 1,
            "candidate_count": 1,
            "reason": None,
        },
    }
    payload.update(overrides)
    return payload


def _chatgpt_request(tmp_path, **overrides):
    values = {
        "prompt": "study image",
        "output_dir": str(tmp_path),
        "continuity_key": "anki-provider-contract",
        "idempotency_key": "provider-contract:image:0",
    }
    values.update(overrides)
    return ImageGenerationRequest(**values)


@pytest.mark.unit
async def test_chatgpt_browser_generator_writes_artifact_and_notional_usage(tmp_path):
    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator

    calls = []

    def http_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeChatGptResponse(_chatgpt_image_payload())

    generator = ChatGptBrowserImageGenerator(_chatgpt_config(), http_post=http_post)
    summary = WorkflowUsageSummary()
    context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf1", workflow_type="anki_generation"),
        summary,
        WorkflowBudget(max_image_calls=1),
    )

    with workflow_usage_scope(context):
        result = await generator.generate(
            _chatgpt_request(tmp_path, output_basename="card.png")
        )

    url, kwargs = calls[0]
    assert url == "http://127.0.0.1:8010/generate_image"
    assert kwargs["json"]["description"] == "study image"
    assert kwargs["json"]["conversation_mode"] == "reuse"
    assert kwargs["json"]["conversation_name"] == "anki-provider-contract"
    assert kwargs["json"]["idempotency_key"] == "provider-contract:image:0"
    assert "no_cache" not in kwargs["json"]
    assert "reference_images" not in kwargs["json"]
    assert kwargs["json"]["timeout"] == 340
    assert kwargs["timeout"] == 370  # read timeout = service budget + headroom

    assert result.provider == "chatgpt_browser"
    assert result.model == "chatgpt-web"
    assert result.estimated_usd is None
    with open(result.path, "rb") as fh:
        assert fh.read() == _CHATGPT_PNG_BYTES

    event = summary.events[0]
    assert event.cost_class == "subscription_notional"
    assert event.estimated_usd is None and event.notional_usd is None
    assert event.notional_pricing is not None
    assert event.notional_pricing.source == "unknown"
    assert event.notional_pricing.unknown_reason == "usage_event_missing"


@pytest.mark.unit
async def test_chatgpt_browser_generator_fresh_mode_keeps_prompt_and_omits_continuity(tmp_path):
    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator

    calls = []

    def http_post(url, **kwargs):
        calls.append((url, kwargs))
        payload = _chatgpt_image_payload(conversation_mode="fresh")
        for name in ("conversation_name", "generation", "reused", "rotated", "freshness"):
            payload.pop(name)
        return FakeChatGptResponse(payload)

    generator = ChatGptBrowserImageGenerator(
        _chatgpt_config(
            WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE="fresh",
            WORKFLOW_USAGE_TRACKING_ENABLED=False,
        ),
        http_post=http_post,
    )
    await generator.generate(
        _chatgpt_request(tmp_path, continuity_key=None)
    )

    assert calls[0][1]["json"]["description"] == "study image"
    assert calls[0][1]["json"]["conversation_mode"] == "fresh"
    assert calls[0][1]["json"]["no_cache"] is True
    assert "conversation_name" not in calls[0][1]["json"]


@pytest.mark.unit
async def test_chatgpt_browser_generator_requires_explicit_url(tmp_path):
    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator, ImageGenerationError

    generator = ChatGptBrowserImageGenerator(_chatgpt_config(WORKFLOW_CHATGPT_BROWSER_URL=""))
    with pytest.raises(ImageGenerationError, match="CHATGPT_BROWSER_API_URL"):
        await generator.generate(_chatgpt_request(tmp_path, prompt="x"))


@pytest.mark.unit
async def test_chatgpt_browser_generator_sends_reference_images_and_asserts_honored(tmp_path):
    """FR-1: style refs go up as data URLs; the echoed reference_images_used is enforced."""

    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator

    ref = tmp_path / "style.png"
    ref.write_bytes(b"style-bytes")
    calls = []

    def http_post(url, **kwargs):
        calls.append((url, kwargs))
        payload = _chatgpt_image_payload()
        payload["reference_images_used"] = 1
        return FakeChatGptResponse(payload)

    generator = ChatGptBrowserImageGenerator(
        _chatgpt_config(WORKFLOW_USAGE_TRACKING_ENABLED=False), http_post=http_post
    )
    result = await generator.generate(
        _chatgpt_request(
            tmp_path / "out", prompt="styled card", reference_image_paths=[str(ref)]
        )
    )

    sent = calls[0][1]["json"]["reference_images"]
    assert len(sent) == 1 and sent[0]["role"] == "style"
    assert sent[0]["data_url"] == "data:image/png;base64," + base64.b64encode(b"style-bytes").decode()
    assert result.reference_image_count == 1
    assert result.generation_evidence.reference_images_used == 1


@pytest.mark.unit
async def test_chatgpt_browser_generator_refuses_style_dropped_result(tmp_path):
    """A response that did not honor the refs is refused — silent style drop is undetectable."""

    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator, ImageGenerationError

    ref = tmp_path / "style.png"
    ref.write_bytes(b"style-bytes")

    def http_post(url, **kwargs):
        payload = _chatgpt_image_payload()
        payload["reference_images_used"] = 0
        return FakeChatGptResponse(payload)

    generator = ChatGptBrowserImageGenerator(
        _chatgpt_config(WORKFLOW_USAGE_TRACKING_ENABLED=False), http_post=http_post
    )
    with pytest.raises(ImageGenerationError, match="reference image count mismatch"):
        await generator.generate(
            _chatgpt_request(
                tmp_path / "out", prompt="styled card", reference_image_paths=[str(ref)]
            )
        )
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


@pytest.mark.unit
async def test_chatgpt_browser_generator_unreadable_reference_is_loud(tmp_path):
    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator, ImageGenerationError

    generator = ChatGptBrowserImageGenerator(_chatgpt_config(WORKFLOW_USAGE_TRACKING_ENABLED=False))
    with pytest.raises(ImageGenerationError, match="unreadable"):
        await generator.generate(
            _chatgpt_request(
                tmp_path,
                prompt="x",
                reference_image_paths=[str(tmp_path / "missing.png")],
            )
        )


@pytest.mark.unit
async def test_chatgpt_browser_generator_surfaces_service_errors_loudly(tmp_path):
    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator, ImageGenerationError

    def http_post(url, **kwargs):
        return FakeChatGptResponse({"detail": "task was not picked up"}, status_code=502)

    generator = ChatGptBrowserImageGenerator(
        _chatgpt_config(WORKFLOW_USAGE_TRACKING_ENABLED=False), http_post=http_post
    )
    with pytest.raises(ImageGenerationError, match="HTTP 502"):
        await generator.generate(_chatgpt_request(tmp_path, prompt="x"))
    assert not list(tmp_path.iterdir()), "failed generation left a partial artifact behind"


@pytest.mark.unit
async def test_create_image_generator_dispatches_chatgpt_provider():
    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator

    generator = create_image_generator(SimpleNamespace(WORKFLOW_IMAGE_PROVIDER="chatgpt"))
    assert isinstance(generator, ChatGptBrowserImageGenerator)


@pytest.mark.unit
async def test_chatgpt_browser_generator_honours_429_retry_after(tmp_path):
    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator

    calls = {"n": 0}

    def http_post(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeChatGptResponse(
                {"status": "rate_limited", "error": "self-throttle", "retry_after": 6},
                status_code=429,
            )
        return FakeChatGptResponse(_chatgpt_image_payload())

    sleeps = []

    async def sleeper(delay):
        sleeps.append(delay)

    generator = ChatGptBrowserImageGenerator(
        _chatgpt_config(WORKFLOW_USAGE_TRACKING_ENABLED=False),
        http_post=http_post,
        sleeper=sleeper,
    )
    result = await generator.generate(
        _chatgpt_request(tmp_path)
    )

    assert sleeps == [6]
    assert calls["n"] == 2
    with open(result.path, "rb") as fh:
        assert fh.read() == _CHATGPT_PNG_BYTES
