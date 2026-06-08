"""Unit tests for OpenAI prompt-cache adapter hints."""

from types import SimpleNamespace

import pytest

from services.llm_factory import create_chat_llm
from services.openai_service import OpenAIService
from services.openai_cache import openai_prompt_cache_kwargs

pytestmark = pytest.mark.unit


def test_openai_prompt_cache_kwargs_builds_stable_sanitized_key():
    config = SimpleNamespace(
        OPENAI_PROMPT_CACHE_KEY_PREFIX="tg handy utils",
        OPENAI_PROMPT_CACHE_RETENTION="24h",
    )

    assert openai_prompt_cache_kwargs(config, model="gpt-5.4-mini", node="card/set") == {
        "prompt_cache_key": "tg-handy-utils:gpt-5.4-mini:card-set",
        "prompt_cache_retention": "24h",
    }


def test_openai_prompt_cache_kwargs_disabled_without_prefix():
    config = SimpleNamespace(OPENAI_PROMPT_CACHE_KEY_PREFIX="", OPENAI_PROMPT_CACHE_RETENTION="24h")

    assert openai_prompt_cache_kwargs(config, model="gpt-5.4-mini") == {}


def test_create_chat_llm_passes_openai_prompt_cache_kwargs(monkeypatch):
    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("services.llm_factory.ChatOpenAI", fake_chat_openai)

    llm = create_chat_llm(
        SimpleNamespace(
            OPENAI_API_KEY="test-key",
            OPENAI_PROMPT_CACHE_KEY_PREFIX="tghandyutils",
            OPENAI_PROMPT_CACHE_RETENTION="in_memory",
        ),
        model="gpt-5.4-mini",
        temperature=0.2,
    )

    assert llm is not None
    assert captured["model"] == "gpt-5.4-mini"
    assert captured["model_kwargs"] == {
        "prompt_cache_key": "tghandyutils:gpt-5.4-mini",
        "prompt_cache_retention": "in_memory",
    }
    assert "temperature" not in captured


async def test_openai_service_image_analysis_passes_prompt_cache_kwargs():
    captured = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="TEXT EXTRACTED:\n\nSUMMARY:\nimage"))],
                usage=None,
                model=kwargs["model"],
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    service = OpenAIService(
        api_key="test-key",
        config=SimpleNamespace(
            OPENAI_PROMPT_CACHE_KEY_PREFIX="tghandyutils",
            OPENAI_PROMPT_CACHE_RETENTION="24h",
            OPENAI_VISION_MODEL="gpt-5.4-mini",
            WORKFLOW_USAGE_TRACKING_ENABLED=False,
        ),
    )
    service.client = FakeClient()

    result = await service.analyze_image(b"fake-image")

    assert "SUMMARY" in result
    assert captured["prompt_cache_key"] == "tghandyutils:gpt-5.4-mini:image_analyzer"
    assert captured["prompt_cache_retention"] == "24h"
    assert captured["max_completion_tokens"] == 1000
