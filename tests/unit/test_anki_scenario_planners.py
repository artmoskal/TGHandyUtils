"""Unit tests for per-type Anki scenario planners."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from models.anki_workflow import (
    AnkiDirectiveConstraints,
    CardBuildPlan,
    ContentSource,
    ImageAssetPlan,
)
from services.content.anki_scenario_planners import (
    ClozeScenarioPlanner,
    TextScenarioPlanner,
    VisualScenarioPlanner,
)


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


def _build_plan(card_kind="basic", image_policy="none"):
    return CardBuildPlan(
        card_kind=card_kind,
        image_policy=image_policy,
        source_facts=["A valve controls flow"],
        study_goal="Remember what a valve does",
        fallback_kind="basic",
    )


@pytest.mark.unit
async def test_text_scenario_planner_returns_rendering_guidance():
    llm = FakeLLM(
        [
            """{
  "source_content": "A valve controls flow in a pipe.",
  "guide": null,
  "rendering_guide": "Ask what a valve controls; keep the answer off the front.",
  "facts_to_test": ["A valve controls flow"],
  "answer_constraints": "Answer in one short phrase.",
  "strategy": "split",
  "count": 1
}"""
        ]
    )
    planner = TextScenarioPlanner(FakeConfig(), llm=llm)

    assert planner._node.name == "prepare_text_scenario"
    scenario = await planner.plan(
        ContentSource(content="A valve controls flow in a pipe.", user_id=10),
        AnkiDirectiveConstraints(),
        _build_plan(),
    )

    assert scenario.source_content == "A valve controls flow in a pipe."
    assert "keep the answer off the front" in scenario.rendering_guide
    assert len(llm.messages) == 1
    assert isinstance(llm.messages[0][0], SystemMessage)
    assert isinstance(llm.messages[0][1], HumanMessage)
    assert "text-card scenario planner" in llm.messages[0][0].content
    assert "Priority order for visual cards" not in llm.messages[0][0].content
    assert "grouped set-recall card" in llm.messages[0][0].content
    assert "Do not keep only a representative item" in llm.messages[0][0].content
    assert "A valve controls flow in a pipe" in llm.messages[0][1].content


@pytest.mark.unit
def test_scenario_planner_uses_scenario_model_profile(monkeypatch):
    calls = []

    def fake_create_chat_llm(config, model, temperature):
        calls.append((model, temperature))
        return FakeLLM([])

    class ProfileConfig(FakeConfig):
        ANKI_SCENARIO_MODEL = "gpt-5.4-mini"
        ANKI_CARD_MODEL = "gpt-5.4-nano"

    monkeypatch.setattr("services.content.anki_scenario_planners.create_anki_text_llm", fake_create_chat_llm)

    planner = TextScenarioPlanner(ProfileConfig())

    assert planner.llm is not None
    assert calls == [("gpt-5.4-mini", 0.1)]


@pytest.mark.unit
async def test_cloze_scenario_planner_retries_bad_json_and_requires_targets():
    llm = FakeLLM(
        [
            "not json",
            """{
  "source_content": "The pump exports three Na+ and imports two K+.",
  "guide": null,
  "rendering_guide": "Hide the ion counts and directions.",
  "cloze_targets": ["exports three Na+", "imports two K+"],
  "rewritten_sentence": "The pump exports three Na+ and imports two K+.",
  "max_deletions": 2,
  "count": 1
}""",
        ]
    )
    planner = ClozeScenarioPlanner(FakeConfig(), llm=llm)

    assert planner._node.name == "prepare_cloze_scenario"
    scenario = await planner.plan(
        ContentSource(content="The pump exports three Na+ and imports two K+.", user_id=10),
        AnkiDirectiveConstraints(card_type="cloze"),
        _build_plan(card_kind="cloze"),
    )

    assert scenario.cloze_targets == ["exports three Na+", "imports two K+"]
    assert len(llm.messages) == 2
    assert "previous scenario-planning output was invalid" in llm.messages[1][0].content


@pytest.mark.unit
async def test_cloze_scenario_planner_raises_without_targets():
    llm = FakeLLM(
        [
            """{
  "source_content": "Paris is in France.",
  "guide": null,
  "rendering_guide": "Hide the country.",
  "cloze_targets": [],
  "rewritten_sentence": "Paris is in France.",
  "max_deletions": 1,
  "count": 1
}""",
            "still invalid",
        ]
    )
    planner = ClozeScenarioPlanner(FakeConfig(), llm=llm)

    with pytest.raises(Exception, match="ClozeScenarioPlanner returned invalid output"):
        await planner.plan(
            ContentSource(content="Paris is in France.", user_id=10),
            AnkiDirectiveConstraints(card_type="cloze"),
            _build_plan(card_kind="cloze"),
        )


@pytest.mark.unit
async def test_visual_scenario_planner_returns_prompt_and_layout():
    llm = FakeLLM(
        [
            """{
  "source_content": "Air flows faster over a curved wing, lowering pressure above it.",
  "question_text": "Why does a wing generate lift?",
  "front_intent": "Ask why a wing generates lift.",
  "back_intent": "Explain pressure difference and upward force.",
  "facts_to_test": ["faster airflow above", "lower pressure above", "upward lift"],
  "visual_prompt": "Clean airfoil diagram with blue upper airflow, red lower airflow, and upward arrow; no text.",
  "reference_image_policy": "none",
  "layout": "text_front_image_back",
  "image_count": 1,
  "answer_text": "Faster upper airflow lowers pressure above the wing, so higher pressure below pushes it upward.",
  "rendering_guide": "Ask for the cause of lift; put the generated diagram on the back.",
  "fallback_kind": "basic"
}"""
        ]
    )
    planner = VisualScenarioPlanner(FakeConfig(), llm=llm)

    assert planner._node.name == "prepare_visual_scenario"
    scenario = await planner.plan(
        ContentSource(content="Air flows faster over a curved wing, lowering pressure above it.", user_id=10),
        AnkiDirectiveConstraints(),
        _build_plan(card_kind="visual_basic", image_policy="generate"),
        ImageAssetPlan(image_role="generate_new_visual", rationale="diagram helps"),
    )

    assert scenario.layout == "text_front_image_back"
    assert scenario.question_text == "Why does a wing generate lift?"
    assert "no text" in scenario.visual_prompt
    assert scenario.answer_text.startswith("Faster upper airflow")
    system_prompt = llm.messages[0][0].content
    dynamic_prompt = llm.messages[0][1].content
    assert "character reference" in system_prompt
    assert "design/typography reference" in system_prompt
    assert "Priority order for visual cards" in system_prompt
    assert "image explains the answer relationship" in system_prompt
    assert "one visual card must still cover the whole planned set" in system_prompt
    assert "Do not include the recurring aircraft mascot by default" in system_prompt
    assert "Prefer humor from the concept itself" in system_prompt
    assert "airliners" in system_prompt
    assert "typography/callout cards" in system_prompt
    assert "Preserve the user's requested visual composition" in system_prompt
    assert "question_text must be the exact Anki front text" in system_prompt
    assert "answer_text must be the exact concise Anki back answer" in system_prompt
    assert "plain-English meaning" in system_prompt
    assert "Do not invent unofficial acronym" in system_prompt
    assert "expansions" in system_prompt
    assert "not merely repeat the term being tested" in system_prompt
    assert "collage, funny scene, captions, story, diagram" in system_prompt
    assert "Do not hard-code, invent, genericize" in system_prompt
    assert "unexplained" in system_prompt
    assert "abbreviations" in system_prompt
    assert "abbreviation-only decoration" in system_prompt
    assert "Default visual tone is catchy, witty, and lightly humorous" in system_prompt
    assert "subordinate to factual accuracy" in system_prompt
    assert "neutral technical diagram" in system_prompt
    assert "A tidy icon grid or smiling mascot alone is not enough" in system_prompt
    assert "conditional items" in system_prompt
    assert "misleading counts" in system_prompt
    assert "humorous narrative collage" in system_prompt
    assert "absurd-but-grounded visual metaphor" in system_prompt
    assert "Humor must" in system_prompt
    assert "explain the answer" in system_prompt
    assert "Short abbreviation" in system_prompt
    assert "Labrador-like" in system_prompt
    assert "one centered nose propeller treatment" in system_prompt
    assert "centered translucent spinning propeller disk" in system_prompt
    assert "exactly one centered two-blade propeller" in system_prompt
    assert "crop or omit the nose/aircraft" in system_prompt
    assert "pseudo-real institutional logos" in system_prompt
    assert "shields, crests, globe marks" in system_prompt
    assert "plain covers" in system_prompt
    assert "Text/typography direction" in system_prompt
    assert "Air flows faster over a curved wing" not in system_prompt
    assert "Air flows faster over a curved wing" in dynamic_prompt
