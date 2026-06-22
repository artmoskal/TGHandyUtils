"""Per-card-family scenario planners for the Anki generation graph."""

import json
from typing import Any, Optional

from core.exceptions import ParsingError
from core.interfaces import IConfig
from models.anki_workflow import (
    AnkiDirectiveConstraints,
    CardBuildPlan,
    ClozeCardScenario,
    ContentSource,
    ImageAssetPlan,
    TextCardScenario,
    VisualCardScenario,
)
from ai_workflow_engine.engine import StructuredLLMNode, StructuredOutputError
from ai_workflow_engine.prompt_loader import load_prompt_template
from services.llm_factory import create_chat_llm


class _ScenarioPlannerBase:
    """Shared structured-output/retry wrapper for product-specific scenario planners."""

    _REPAIR_PROMPT = load_prompt_template("anki/scenario.repair.prompt")

    def __init__(
        self,
        config: IConfig,
        model_cls: type,
        node_name: str,
        static_prompt: str,
        dynamic_prompt: str,
        llm: Optional[Any] = None,
    ):
        self.config = config
        self._node = StructuredLLMNode(
            name=node_name,
            config=config,
            output_model=model_cls,
            static_prompt_template=static_prompt,
            dynamic_prompt_template=dynamic_prompt,
            dynamic_input_variables=[
                "source_material",
                "directive_summary",
                "build_plan_summary",
                "image_plan_summary",
            ],
            model_attr="ANKI_SCENARIO_MODEL",
            default_model_attr="ANKI_CARD_MODEL",
            default_model="gpt-5.4-mini",
            temperature=0.1,
            llm=llm,
            llm_factory=create_chat_llm,
            validator=self._validate_scenario,
            repair_prompt_template=self._REPAIR_PROMPT,
        )

    @property
    def llm(self):
        return self._node.llm

    async def _plan(
        self,
        source: ContentSource,
        directives: AnkiDirectiveConstraints,
        build_plan: CardBuildPlan,
        image_asset_plan: Optional[ImageAssetPlan] = None,
    ):
        try:
            return await self._node.run(
                {
                    "source_material": self._source_material(source),
                    "directive_summary": self._directive_summary(directives),
                    "build_plan_summary": self._model_json(build_plan),
                    "image_plan_summary": self._model_json(image_asset_plan) if image_asset_plan else "{}",
                },
                content_hash_input=source.content,
            )
        except StructuredOutputError as exc:
            raise ParsingError(f"{self.__class__.__name__} returned invalid output: {exc}") from exc

    def _validate_scenario(self, scenario) -> None:
        if not getattr(scenario, "source_content", "").strip():
            raise ParsingError("scenario source_content is empty")

    @staticmethod
    def _source_material(source: ContentSource) -> str:
        text = (source.content or "").strip()
        if len(text) > 6000:
            text = text[:6000] + "\n[TRUNCATED]"
        if not text and source.images:
            text = "[image-only source; use uploaded image metadata and OCR/description if present]"
        return text

    @staticmethod
    def _directive_summary(directives: AnkiDirectiveConstraints) -> str:
        return json.dumps(
            {
                "forced_card_type": directives.card_type,
                "image_policy": directives.image_policy,
                "include_image": directives.include_image,
                "image_placement": directives.image_placement,
                "strategy": directives.strategy,
                "count": directives.count,
                "language_voice": directives.language_voice,
                "source_language": directives.source_language,
                "target_language": directives.target_language,
                "guide": directives.guide,
            },
            sort_keys=True,
        )

    @staticmethod
    def _model_json(value: Any) -> str:
        if value is None:
            return "{}"
        if hasattr(value, "model_dump"):
            return json.dumps(value.model_dump(), sort_keys=True)
        return json.dumps(value, sort_keys=True)


class TextScenarioPlanner(_ScenarioPlannerBase):
    _STATIC_PROMPT = load_prompt_template("anki/text_scenario.static.prompt")
    _DYNAMIC_PROMPT = load_prompt_template("anki/text_scenario.dynamic.prompt")

    _PROMPT = _STATIC_PROMPT + "\n" + _DYNAMIC_PROMPT

    def __init__(self, config: IConfig, llm: Optional[Any] = None):
        super().__init__(
            config,
            TextCardScenario,
            "prepare_text_scenario",
            self._STATIC_PROMPT,
            self._DYNAMIC_PROMPT,
            llm=llm,
        )

    async def plan(
        self,
        source: ContentSource,
        directives: AnkiDirectiveConstraints,
        build_plan: CardBuildPlan,
    ) -> TextCardScenario:
        return await self._plan(source, directives, build_plan)


class ClozeScenarioPlanner(_ScenarioPlannerBase):
    _STATIC_PROMPT = load_prompt_template("anki/cloze_scenario.static.prompt")
    _DYNAMIC_PROMPT = load_prompt_template("anki/cloze_scenario.dynamic.prompt")

    _PROMPT = _STATIC_PROMPT + "\n" + _DYNAMIC_PROMPT

    def __init__(self, config: IConfig, llm: Optional[Any] = None):
        super().__init__(
            config,
            ClozeCardScenario,
            "prepare_cloze_scenario",
            self._STATIC_PROMPT,
            self._DYNAMIC_PROMPT,
            llm=llm,
        )

    async def plan(
        self,
        source: ContentSource,
        directives: AnkiDirectiveConstraints,
        build_plan: CardBuildPlan,
    ) -> ClozeCardScenario:
        return await self._plan(source, directives, build_plan)

    def _validate_scenario(self, scenario) -> None:
        super()._validate_scenario(scenario)
        if not scenario.cloze_targets:
            raise ParsingError("cloze scenario has no cloze_targets")


class VisualScenarioPlanner(_ScenarioPlannerBase):
    _STATIC_PROMPT = load_prompt_template("anki/visual_scenario.static.prompt")
    _DYNAMIC_PROMPT = load_prompt_template("anki/visual_scenario.dynamic.prompt")

    _PROMPT = _STATIC_PROMPT + "\n" + _DYNAMIC_PROMPT

    def __init__(self, config: IConfig, llm: Optional[Any] = None):
        super().__init__(
            config,
            VisualCardScenario,
            "prepare_visual_scenario",
            self._STATIC_PROMPT,
            self._DYNAMIC_PROMPT,
            llm=llm,
        )

    async def plan(
        self,
        source: ContentSource,
        directives: AnkiDirectiveConstraints,
        build_plan: CardBuildPlan,
        image_asset_plan: ImageAssetPlan,
    ) -> VisualCardScenario:
        return await self._plan(source, directives, build_plan, image_asset_plan)

    def _validate_scenario(self, scenario) -> None:
        super()._validate_scenario(scenario)
        if not scenario.question_text.strip():
            raise ParsingError("visual scenario question_text is empty")
        if not scenario.answer_text.strip():
            raise ParsingError("visual scenario answer_text is empty")
        if scenario.image_count not in (1, 2):
            raise ParsingError("visual scenario image_count must be 1 or 2")
        if not (scenario.visual_prompt or "").strip():
            raise ParsingError("visual scenario visual_prompt is empty")
