"""LIVE tests against the ChatGPT-browser service (subscription session on the always-on box).

Run explicitly (real browser session, sequential single-browser queue, ~30–120s per image):

    ALLOW_PAID_TESTS=1 ./test.sh integration -- --cov-fail-under=0 -q \
        tests/integration/test_chatgpt_browser_live.py

Gating: the whole module SKIPS when ``CHATGPT_BROWSER_API_URL`` is not set. When the URL IS
set, an unreachable service is a FAILURE, not a skip — you explicitly opted into live tests,
so a dead browser/extension on the mini is a real finding. No metered spend: calls ride the
ChatGPT subscription (recorded as ``subscription_notional``).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ai_workflow_engine import LLMRequest, StructuredLLMNode, WEAK_MODEL_CLEANER
from ai_workflow_engine.models import WorkflowRunContext, WorkflowUsageSummary
from ai_workflow_engine.usage import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope
from ai_workflow_tools.chatgpt_browser import ChatGptBrowserLLMClient
from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator
from ai_workflow_tools.media.image_models import ImageGenerationRequest

pytestmark = pytest.mark.integration

BASE_URL = os.getenv("CHATGPT_BROWSER_API_URL", "").strip()

if not BASE_URL:
    pytest.skip(
        "CHATGPT_BROWSER_API_URL is not set — live ChatGPT-browser tests need the service "
        "endpoint (see CHATGPT_API.md in ai-llm-infra)",
        allow_module_level=True,
    )


@pytest.fixture(scope="module", autouse=True)
def _service_must_be_up():
    """URL configured ⇒ the service being down is a finding, not a silent skip."""

    import requests

    response = requests.get(f"{BASE_URL.rstrip('/')}/ping", timeout=10)
    assert response.status_code == 200, (
        f"ChatGPT-browser service unreachable at {BASE_URL} (HTTP {response.status_code}) — "
        "check the control server + Chrome extension on the mini"
    )


def _config():
    return SimpleNamespace(
        WORKFLOW_CHATGPT_BROWSER_URL=BASE_URL,
        WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS=320,
        WORKFLOW_CHATGPT_BROWSER_FORCE_FRESH=True,
        WORKFLOW_USAGE_TRACKING_ENABLED=True,
    )


class Verdict(BaseModel):
    label: str


async def test_live_ask_answers_and_reports_notional_cost():
    client = ChatGptBrowserLLMClient(BASE_URL)

    response = await client(
        LLMRequest(user="Reply with exactly one word: bridge. No punctuation, nothing else.")
    )

    assert response.text.strip(), "live /ask returned empty text"
    assert "bridge" in response.text.lower()
    assert response.cost_class == "subscription_notional"
    assert response.estimated_usd is None


async def test_live_structured_node_parses_chatgpt_reply_like_anki_would():
    """The Anki-shaped proof: browser /ask behind a StructuredLLMNode → validated model.

    This is the exact usage a text-provider swap would need — free text in, engine-side
    cleaning/parsing/repair out. ChatGPT web replies tend to wrap JSON in markdown fences;
    WEAK_MODEL_CLEANER must absorb that.
    """

    client = ChatGptBrowserLLMClient(BASE_URL)
    node = StructuredLLMNode(
        name="chatgpt_browser_live",
        config=object(),
        output_model=Verdict,
        prompt_template=(
            'Classify the word "{item}" as a part of speech. '
            'Answer with JSON only: {{"label": "<noun|verb|adjective>"}}'
        ),
        input_variables=["item"],
        llm=client,
        pre_parse=WEAK_MODEL_CLEANER,
    )
    summary = WorkflowUsageSummary()
    scope = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf-chatgpt-live", workflow_type="anki_generation"),
        summary,
        WorkflowBudget(),
    )

    with workflow_usage_scope(scope):
        result = await node.run({"item": "bridge"})

    assert result.label == "noun"


async def test_live_image_generation_writes_a_real_png(tmp_path):
    """End-to-end through OUR provider code — the same path the Anki flow will take with
    ANKI_IMAGE_PROVIDER=chatgpt (no reference images until the service ships FR-1)."""

    generator = ChatGptBrowserImageGenerator(_config())
    summary = WorkflowUsageSummary()
    scope = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf-chatgpt-live", workflow_type="anki_generation"),
        summary,
        WorkflowBudget(max_image_calls=1),
    )

    with workflow_usage_scope(scope):
        result = await generator.generate(
            ImageGenerationRequest(
                prompt=(
                    "flashcard illustration of the word bridge: a simple stone bridge over "
                    "a small river, flat vector style, clean white background"
                ),
                output_dir=str(tmp_path),
                output_basename="live-bridge.png",
            )
        )

    with open(result.path, "rb") as fh:
        header = fh.read(8)
    assert header.startswith(b"\x89PNG"), "artifact is not a PNG"
    assert result.provider == "chatgpt_browser"
    assert result.estimated_usd is None

    event = summary.events[0]
    assert event.cost_class == "subscription_notional"
    assert event.metadata["cost_known"] is False
    assert summary.image_call_count == 1


async def test_live_chat_model_renders_card_shaped_json():
    """The render path's shape: sync LangChain-style invoke → parseable card JSON."""

    import json as _json
    from types import SimpleNamespace

    from ai_workflow_engine import WEAK_MODEL_CLEANER
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserChatModel

    model = ChatGptBrowserChatModel(BASE_URL)
    output = model.invoke(
        [
            SimpleNamespace(
                type="system",
                content="You generate Anki flashcards. Reply with JSON only, no prose.",
            ),
            SimpleNamespace(
                type="human",
                content=(
                    'Make exactly one basic card about the word "bridge". Format: '
                    '[{"type": "basic", "question": "...", "answer": "..."}]'
                ),
            ),
        ]
    )

    cards = _json.loads(WEAK_MODEL_CLEANER(output.content))
    if isinstance(cards, dict):
        cards = [cards]
    assert cards and cards[0].get("question"), f"no card in reply: {output.content[:200]}"
