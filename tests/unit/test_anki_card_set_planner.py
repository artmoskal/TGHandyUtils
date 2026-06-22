"""Unit tests for the AI Anki card-set planner."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from models.anki_workflow import AnkiDirectiveConstraints, ContentSource, ImageAssetPlan
from services.content.anki_card_set_planner import AnkiCardSetPlanner


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


def _valid_plan_json(card_kind="visual_basic", image_policy="generate"):
    return f"""{{
  "card_kind": "{card_kind}",
  "image_policy": "{image_policy}",
  "count": null,
  "source_facts": ["A valve controls flow"],
  "study_goal": "Remember what a valve does",
  "visual_rationale": "A simple valve illustration can support recall",
  "fallback_kind": "basic",
  "user_constraints_applied": []
}}"""


@pytest.mark.unit
async def test_card_set_planner_returns_validated_plan():
    llm = FakeLLM([_valid_plan_json()])
    planner = AnkiCardSetPlanner(FakeConfig(), llm=llm)

    assert planner._node.name == "plan_card_type"
    plan = await planner.plan(
        ContentSource(content="A valve controls flow", user_id=10),
        AnkiDirectiveConstraints(),
        ImageAssetPlan(image_role="ignore_media", rationale="No uploaded image"),
        can_generate_images=True,
    )

    assert plan.card_kind == "visual_basic"
    assert plan.image_policy == "generate"
    assert len(llm.messages) == 1
    assert isinstance(llm.messages[0][0], SystemMessage)
    assert isinstance(llm.messages[0][1], HumanMessage)
    assert "Prefer fewer cards by default" in llm.messages[0][0].content
    assert "default visual tone should be catchy, witty, and lightly humorous" in llm.messages[0][0].content
    assert "sterile icon grid" in llm.messages[0][0].content
    assert "Do not reject visual_basic merely" in llm.messages[0][0].content
    assert "because the source is an abbreviation or definition" in llm.messages[0][0].content
    assert "operational meaning, effect" in llm.messages[0][0].content
    assert "Count is based on independent study objectives" in llm.messages[0][0].content
    assert "sibling list/set under one category" in llm.messages[0][0].content
    assert "do not select one representative item and drop the rest" in llm.messages[0][0].content
    assert "A valve controls flow" in llm.messages[0][1].content


@pytest.mark.unit
async def test_card_set_planner_passes_instruction_guidance_to_type_gate():
    llm = FakeLLM([_valid_plan_json()])
    planner = AnkiCardSetPlanner(FakeConfig(), llm=llm)

    await planner.plan(
        ContentSource(content="A valve controls flow", user_id=10),
        AnkiDirectiveConstraints(guide="make it a funny visual mnemonic"),
        ImageAssetPlan(image_role="ignore_media", rationale="No uploaded image"),
        can_generate_images=True,
    )

    assert '"guide": "make it a funny visual mnemonic"' in llm.messages[0][1].content
    assert '"guide": "make it a funny visual mnemonic"' not in llm.messages[0][0].content


@pytest.mark.unit
def test_card_set_planner_uses_decision_model_profile(monkeypatch):
    calls = []

    def fake_create_chat_llm(config, model, temperature):
        calls.append((model, temperature))
        return FakeLLM([_valid_plan_json()])

    class ProfileConfig(FakeConfig):
        ANKI_DECISION_MODEL = "gpt-5.4-nano"
        ANKI_CARD_MODEL = "gpt-5.4-mini"

    monkeypatch.setattr("services.content.anki_card_set_planner.create_chat_llm", fake_create_chat_llm)

    planner = AnkiCardSetPlanner(ProfileConfig())

    assert planner.llm is not None
    assert calls == [("gpt-5.4-nano", 0.1)]


@pytest.mark.unit
async def test_card_set_planner_retries_once_on_bad_json():
    llm = FakeLLM(["not json", _valid_plan_json(card_kind="cloze", image_policy="none")])
    planner = AnkiCardSetPlanner(FakeConfig(), llm=llm)

    plan = await planner.plan(
        ContentSource(content="Paris is in France", user_id=10),
        AnkiDirectiveConstraints(),
        ImageAssetPlan(image_role="ignore_media", rationale="No uploaded image"),
        can_generate_images=False,
    )

    assert plan.card_kind == "cloze"
    assert len(llm.messages) == 2
    assert "previous card-set planning output was invalid" in llm.messages[1][0].content


@pytest.mark.unit
async def test_card_set_planner_raises_after_repair_failure():
    llm = FakeLLM(["not json", "still not json"])
    planner = AnkiCardSetPlanner(FakeConfig(), llm=llm)

    with pytest.raises(Exception, match="Card set planner returned invalid output"):
        await planner.plan(
            ContentSource(content="Paris is in France", user_id=10),
            AnkiDirectiveConstraints(),
            ImageAssetPlan(image_role="ignore_media", rationale="No uploaded image"),
            can_generate_images=False,
        )

    assert len(llm.messages) == 2
