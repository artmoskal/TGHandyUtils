"""Branch-specific Anki renderers.

Renderers are product-specific workers called by the graph. They do not decide card family,
image policy, or fallback; they convert an already-planned scenario into AnkiCard objects.
"""

from typing import Any, Optional

from core.exceptions import ParsingError
from models.anki import AnkiCard
from models.anki_workflow import (
    AnkiDirectiveConstraints,
    CardBuildPlan,
    ClozeCardScenario,
    GeneratedMedia,
    ImageAssetPlan,
    RenderedCardSet,
    TextCardScenario,
    VisualCardScenario,
)
from services.anki_card_service import AnkiCardService


def rendering_guide(scenario: Any, extra_guidance: Optional[str] = None) -> Optional[str]:
    guide_parts = []
    for attr in ("guide", "rendering_guide"):
        value = getattr(scenario, attr, None)
        if value and str(value).strip():
            guide_parts.append(str(value).strip())
    cloze_targets = getattr(scenario, "cloze_targets", None)
    if cloze_targets:
        guide_parts.append("Cloze targets to hide: " + ", ".join(str(t) for t in cloze_targets))
    max_deletions = getattr(scenario, "max_deletions", None)
    if max_deletions:
        guide_parts.append(f"Use no more than {max_deletions} meaningful cloze deletions.")
    facts = getattr(scenario, "facts_to_test", None)
    if facts:
        guide_parts.append("Facts to test: " + "; ".join(str(f) for f in facts))
    answer_text = getattr(scenario, "answer_text", None)
    if answer_text and str(answer_text).strip():
        guide_parts.append("Preferred concise answer: " + str(answer_text).strip())
    if extra_guidance and extra_guidance.strip():
        guide_parts.append("Quality repair guidance: " + extra_guidance.strip())
    return "\n".join(guide_parts) if guide_parts else None


class TextRenderer:
    def __init__(self, anki_card_service: AnkiCardService):
        self.anki_card_service = anki_card_service

    def render(
        self,
        scenario: TextCardScenario,
        image_asset_plan: ImageAssetPlan,
        strategy: str,
        repair_guidance: Optional[str] = None,
    ) -> RenderedCardSet:
        cards = self.anki_card_service.extract_cards(
            scenario.source_content,
            rendering_guide(scenario, repair_guidance),
            strategy,
            scenario.count,
            "basic",
        )
        return RenderedCardSet(cards=cards, image_asset_plan=image_asset_plan)


class ClozeRenderer:
    def __init__(self, anki_card_service: AnkiCardService):
        self.anki_card_service = anki_card_service

    def render(
        self,
        scenario: ClozeCardScenario,
        image_asset_plan: ImageAssetPlan,
        strategy: str,
        repair_guidance: Optional[str] = None,
    ) -> RenderedCardSet:
        cards = self.anki_card_service.extract_cards(
            scenario.source_content,
            rendering_guide(scenario, repair_guidance),
            strategy,
            scenario.count,
            "cloze",
        )
        return RenderedCardSet(cards=cards, image_asset_plan=image_asset_plan)


class VisualRenderer:
    def __init__(self, anki_card_service: AnkiCardService):
        self.anki_card_service = anki_card_service

    def render(
        self,
        scenario: VisualCardScenario,
        image_asset_plan: ImageAssetPlan,
        strategy: str,
        count: int | None,
        generated_media: list[GeneratedMedia],
        repair_guidance: Optional[str] = None,
    ) -> RenderedCardSet:
        question = scenario.question_text.strip()
        answer = scenario.answer_text.strip()
        if not question or not answer:
            raise ParsingError("Visual scenario must include exact question_text and answer_text")
        cards = [AnkiCard(question=question, answer=answer)]
        if generated_media:
            self._attach_generated_media(cards, generated_media, scenario.layout)
        return RenderedCardSet(
            cards=cards,
            image_asset_plan=image_asset_plan,
            generated_media=generated_media,
        )

    @staticmethod
    def _attach_generated_media(cards, generated_media: list[GeneratedMedia], layout: str) -> None:
        image_html = "".join(f'<img src="{media.basename}">' for media in generated_media)
        if not image_html:
            return
        put_on_front = layout in ("image_front_text_back", "image_front_image_back")
        for card in cards:
            if getattr(card, "type", "basic") == "cloze":
                card.text = f"{card.text}<br>{image_html}"
            elif put_on_front:
                card.question = f"{card.question}<br>{image_html}"
            else:
                card.answer = f"{card.answer}<br>{image_html}"


class FallbackTextRenderer:
    def __init__(self, anki_card_service: AnkiCardService):
        self.anki_card_service = anki_card_service

    def render(
        self,
        content: str,
        directives: AnkiDirectiveConstraints,
        plan: CardBuildPlan | None,
        strategy: str,
        count: int | None,
        repair_guidance: Optional[str] = None,
    ) -> RenderedCardSet:
        guide = directives.guide
        if repair_guidance:
            guide = f"{guide}\n{repair_guidance}" if guide else repair_guidance
        cards = self.anki_card_service.extract_cards(content, guide, strategy, count, "basic")
        return RenderedCardSet(
            cards=cards,
            image_asset_plan=ImageAssetPlan(image_role="source_content_only", rationale="Fallback to text card"),
            fallback_used=True,
            fallback_reason="text fallback",
        )
