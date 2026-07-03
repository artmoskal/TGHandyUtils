"""Unit tests for Anki rendered-card quality evaluation."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from models.anki import AnkiCard
from models.anki_workflow import (
    AnkiDirectiveConstraints,
    CardBuildPlan,
    ContentSource,
    GeneratedMedia,
    ImageAssetPlan,
    RenderedCardSet,
    TextCardScenario,
)
from services.content.anki_quality_evaluator import AnkiRenderedCardEvaluator


class FakeConfig:
    OPENAI_API_KEY = "test-key"
    ANKI_CARD_MODEL = "gpt-5.4-mini"


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def invoke(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(content=self.responses.pop(0))


def _accepted_json():
    return """{
  "accepted": true,
  "issues": [],
  "severity": "none",
  "repair_strategy": "accept",
  "guidance": "",
  "visual_accepted": true
}"""


@pytest.mark.unit
async def test_quality_evaluator_accepts_valid_cards():
    llm = FakeLLM([_accepted_json()])
    evaluator = AnkiRenderedCardEvaluator(FakeConfig(), llm=llm)

    assert evaluator._node.name == "evaluate_rendered_cards"
    result = await evaluator.evaluate(
        ContentSource(content="A valve controls flow.", user_id=10),
        AnkiDirectiveConstraints(),
        CardBuildPlan(card_kind="basic", image_policy="none"),
        TextCardScenario(source_content="A valve controls flow."),
        RenderedCardSet(
            cards=[AnkiCard(question="What controls flow?", answer="A valve.")],
            image_asset_plan=ImageAssetPlan(image_role="ignore_media"),
        ),
    )

    assert result.accepted is True
    assert len(llm.messages) == 1
    assert isinstance(llm.messages[0][0], SystemMessage)
    assert isinstance(llm.messages[0][1], HumanMessage)
    system_prompt = llm.messages[0][0].content
    assert "changes source qualifiers" in system_prompt
    assert "preserve planned coverage" in system_prompt
    assert "sibling list or" in system_prompt
    assert "silently" in system_prompt
    assert "Prefer retry_card_plan" in system_prompt
    assert "target count" in system_prompt
    assert "written answer that fully answers the front question" in system_prompt
    assert "image supports" in system_prompt
    assert "must not be the only answer" in system_prompt
    assert "only repeats the tested term" in system_prompt
    assert "unconditional \"must\" statements or misleading counts" in system_prompt
    assert "introduce unexplained abbreviations" in system_prompt
    assert "Sparse explanatory text is allowed" in system_prompt
    assert "show only the abbreviation" in system_prompt
    assert "plain-English meaning, operational effect" in system_prompt
    assert "genericize" in system_prompt
    assert "catchy, witty, memorable hook" in system_prompt
    assert "Do not reject a neutral technical diagram merely because it is not funny" in system_prompt
    assert "requested funny/collage/story visual" in system_prompt
    assert "tidy static icon grid without a memory hook is not enough" in system_prompt
    assert "use retry_scenario for bad image-to-answer mapping" in system_prompt
    assert "language voice cards" in system_prompt.lower()
    assert "Do not evaluate audio" in system_prompt


@pytest.mark.unit
async def test_quality_evaluator_retries_bad_json():
    llm = FakeLLM(["not json", _accepted_json()])
    evaluator = AnkiRenderedCardEvaluator(FakeConfig(), llm=llm)

    result = await evaluator.evaluate(
        ContentSource(content="A valve controls flow.", user_id=10),
        AnkiDirectiveConstraints(),
        CardBuildPlan(card_kind="basic", image_policy="none"),
        TextCardScenario(source_content="A valve controls flow."),
        RenderedCardSet(
            cards=[AnkiCard(question="What controls flow?", answer="A valve.")],
            image_asset_plan=ImageAssetPlan(image_role="ignore_media"),
        ),
    )

    assert result.accepted is True
    assert len(llm.messages) == 2


@pytest.mark.unit
async def test_quality_evaluator_deterministically_rejects_broken_cloze():
    evaluator = AnkiRenderedCardEvaluator(FakeConfig(), llm=FakeLLM([]))

    result = await evaluator.evaluate(
        ContentSource(content="Paris is in France.", user_id=10),
        AnkiDirectiveConstraints(card_type="cloze"),
        CardBuildPlan(card_kind="cloze", image_policy="none"),
        TextCardScenario(source_content="Paris is in France."),
        RenderedCardSet(
            cards=[AnkiCard(type="cloze", text="Paris is in France.")],
            image_asset_plan=ImageAssetPlan(image_role="ignore_media"),
        ),
    )

    assert result.accepted is False
    assert result.repair_strategy == "repair_render"


@pytest.mark.unit
async def test_quality_evaluator_attaches_generated_image_for_visual_review(tmp_path):
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"fake-png")
    llm = FakeLLM([_accepted_json()])
    evaluator = AnkiRenderedCardEvaluator(FakeConfig(), llm=llm)

    await evaluator.evaluate(
        ContentSource(content="A valve controls flow.", user_id=10),
        AnkiDirectiveConstraints(card_type="visual"),
        CardBuildPlan(card_kind="visual_basic", image_policy="generate"),
        TextCardScenario(source_content="A valve controls flow."),
        RenderedCardSet(
            cards=[AnkiCard(question="What controls flow?", answer="A valve.")],
            image_asset_plan=ImageAssetPlan(image_role="generate_new_visual"),
            generated_media=[
                GeneratedMedia(
                    path=str(image_path),
                    basename="diagram.png",
                    source="generated",
                    role="back",
                )
            ],
        ),
    )

    content = llm.messages[0][1].content
    assert isinstance(content, list)
    assert content[1]["type"] == "image_url"


@pytest.mark.unit
async def test_quality_evaluator_does_not_attach_audio_as_image(tmp_path):
    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"fake-mp3")
    llm = FakeLLM([_accepted_json()])
    evaluator = AnkiRenderedCardEvaluator(FakeConfig(), llm=llm)

    await evaluator.evaluate(
        ContentSource(content="привіт", user_id=10),
        AnkiDirectiveConstraints(card_type="visual", language_voice=True, source_language="ukr", target_language="pt"),
        CardBuildPlan(card_kind="visual_basic", image_policy="generate"),
        TextCardScenario(source_content="привіт"),
        RenderedCardSet(
            cards=[AnkiCard(question="How do you say 'привіт' in Portuguese?", answer="olá<br>[sound:voice.mp3]")],
            image_asset_plan=ImageAssetPlan(image_role="generate_new_visual"),
            generated_media=[
                GeneratedMedia(
                    path=str(audio_path),
                    basename="voice.mp3",
                    source="generated",
                    role="audio",
                )
            ],
        ),
    )

    content = llm.messages[0][1].content
    assert isinstance(content, str)


@pytest.mark.unit
async def test_quality_evaluator_rejects_text_only_backend_when_inspecting_images():
    """Vision guard: a text-only routed backend for the quality role fails at construction,
    never mid-run with silently dropped images."""

    class ChatGptConfig(FakeConfig):
        ANKI_QUALITY_MODEL = "chatgpt-web"

    with pytest.raises(ValueError, match="text-only"):
        AnkiRenderedCardEvaluator(ChatGptConfig(), inspect_images=True)

    # Without image inspection the text-only backend is a legal quality choice.
    evaluator = AnkiRenderedCardEvaluator(ChatGptConfig(), inspect_images=False)
    assert evaluator.inspect_images is False

    # claude-p supports STAGED vision (workspace files + Read tool) — legal with inspection.
    class ClaudePConfig(FakeConfig):
        ANKI_QUALITY_MODEL = "claude-p"

    evaluator = AnkiRenderedCardEvaluator(ClaudePConfig(), inspect_images=True)
    assert evaluator.inspect_images is True


@pytest.mark.unit
async def test_quality_evaluator_hands_images_to_plain_callable_clients(tmp_path):
    """Staged-vision path: plain clients (claude-p) receive the generated images through
    LLMRequest.images as path-sourced ImageInputs — not silently dropped."""

    from ai_workflow_engine.llm_protocol import LLMRequest, LLMResponse

    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"fake-png")
    requests = []

    async def plain_client(request: LLMRequest) -> LLMResponse:
        requests.append(request)
        return LLMResponse(text=_accepted_json(), model="claude-p")

    evaluator = AnkiRenderedCardEvaluator(FakeConfig(), llm=plain_client)

    await evaluator.evaluate(
        ContentSource(content="A valve controls flow.", user_id=10),
        AnkiDirectiveConstraints(card_type="visual"),
        CardBuildPlan(card_kind="visual_basic", image_policy="generate"),
        TextCardScenario(source_content="A valve controls flow."),
        RenderedCardSet(
            cards=[AnkiCard(question="What controls flow?", answer="A valve.")],
            image_asset_plan=ImageAssetPlan(image_role="generate_new_visual"),
            generated_media=[
                GeneratedMedia(
                    path=str(image_path),
                    basename="diagram.png",
                    source="generated",
                    role="back",
                )
            ],
        ),
    )

    assert requests, "plain client never invoked"
    images = requests[0].images
    assert len(images) == 1
    assert images[0].source == "path"
    assert images[0].data == str(image_path)
