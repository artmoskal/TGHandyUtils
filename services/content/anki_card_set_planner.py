"""AI card-set planning for the Anki workflow graph."""

import json
from typing import Any, Optional

from core.exceptions import ParsingError
from core.interfaces import IConfig
from models.anki_workflow import (
    AnkiDirectiveConstraints,
    CardBuildPlan,
    ContentSource,
    ImageAssetPlan,
)
from ai_workflow_engine.engine import StructuredLLMNode, StructuredOutputError
from ai_workflow_engine.prompt_loader import load_prompt_template
from services.llm_factory import create_chat_llm


class AnkiCardSetPlanner:
    """Plan the Anki card family, count, image policy, guidance, and fallback."""

    _STATIC_PROMPT = load_prompt_template("anki/card_set.static.prompt")
    _DYNAMIC_PROMPT = load_prompt_template("anki/card_set.dynamic.prompt")

    _PROMPT = _STATIC_PROMPT + "\n" + _DYNAMIC_PROMPT

    _REPAIR_PROMPT = load_prompt_template("anki/card_set.repair.prompt")

    def __init__(self, config: IConfig, llm: Optional[Any] = None):
        self.config = config
        self._node = StructuredLLMNode(
            name="anki_card_set_planner",
            config=config,
            output_model=CardBuildPlan,
            static_prompt_template=self._STATIC_PROMPT,
            dynamic_prompt_template=self._DYNAMIC_PROMPT,
            dynamic_input_variables=[
                "uploaded_image_count",
                "can_generate_images",
                "image_role",
                "image_rationale",
                "directive_summary",
                "source_material",
            ],
            model_attr="ANKI_DECISION_MODEL",
            default_model_attr="ANKI_CARD_MODEL",
            default_model="gpt-5.4-mini",
            temperature=0.1,
            llm=llm,
            llm_factory=create_chat_llm,
            repair_prompt_template=self._REPAIR_PROMPT,
        )

    @property
    def llm(self):
        return self._node.llm

    async def plan(
        self,
        source: ContentSource,
        directives: AnkiDirectiveConstraints,
        image_asset_plan: ImageAssetPlan,
        can_generate_images: bool,
    ) -> CardBuildPlan:
        """Return a validated card build plan, retrying once on bad JSON/schema."""
        try:
            return await self._node.run(
                {
                    "uploaded_image_count": len(source.images),
                    "can_generate_images": str(can_generate_images).lower(),
                    "image_role": image_asset_plan.image_role,
                    "image_rationale": image_asset_plan.rationale,
                    "directive_summary": self._directive_summary(directives),
                    "source_material": self._source_material(source),
                },
                content_hash_input=source.content,
            )
        except StructuredOutputError as exc:
            raise ParsingError(f"Card set planner returned invalid output: {exc}") from exc

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
                "guide": AnkiCardSetPlanner._trim_guide(directives.guide),
            },
            sort_keys=True,
        )

    @staticmethod
    def _trim_guide(guide: Optional[str]) -> Optional[str]:
        if not guide:
            return None
        guide = guide.strip()
        if len(guide) > 1000:
            return guide[:1000] + "\n[TRUNCATED]"
        return guide

    @staticmethod
    def _source_material(source: ContentSource) -> str:
        text = (source.content or "").strip()
        if len(text) > 6000:
            text = text[:6000] + "\n[TRUNCATED]"
        if not text and source.images:
            text = "[image-only source; use uploaded image metadata and OCR/description if present]"
        return text
