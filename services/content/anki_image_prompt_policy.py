"""Provider-aware image prompt policy for Anki visual cards."""

from dataclasses import dataclass
import re
from typing import Any

from ai_workflow_engine.prompt_loader import load_prompt_template
from models.anki_workflow import AnkiDirectiveConstraints, VisualCardScenario


@dataclass(frozen=True)
class ImagePromptContext:
    provider: str
    reference_image_policy: str
    reference_image_count: int
    layout: str
    image_role: str


class AnkiImagePromptPolicy:
    """Build final image prompts while keeping provider mechanics outside the graph.

    The visual scenario planner decides what the card should teach. This policy turns that scenario
    into provider-specific image instructions without changing graph topology.
    """

    _STYLE_PREFIX = load_prompt_template("anki/image_style_prefix.prompt")
    _BASE_GENERATION = load_prompt_template("anki/image_generation/base.prompt")
    _OPENAI_GENERATION = load_prompt_template("anki/image_generation/openai.prompt")
    _GEMINI_GENERATION = load_prompt_template("anki/image_generation/gemini.prompt")
    _COMPARISON_GENERATION = load_prompt_template("anki/image_generation/comparison.prompt")

    def __init__(self, config: Any = None, image_provider: str = ""):
        self.config = config
        self.image_provider = _canonical_provider(
            image_provider or getattr(config, "ANKI_IMAGE_PROVIDER", "openai")
        )

    def build_visual_scenario_prompt(
        self,
        source_content: str,
        directives: AnkiDirectiveConstraints | None = None,
    ) -> str:
        guide = getattr(directives, "guide", None)
        guide_block = f"\n\nUser guidance:\n{guide.strip()}" if guide and guide.strip() else ""
        return (
            f"{self._STYLE_PREFIX}\n\n"
            f"Learning material:\n{source_content.strip()}"
            f"{guide_block}"
        )

    def build_generation_prompt(
        self,
        scenario: VisualCardScenario,
        context: ImagePromptContext,
    ) -> str:
        brief = (scenario.visual_prompt or scenario.source_content or "").strip()
        if not brief:
            brief = "Create a focused aviation study-card visual from the scenario."

        provider_policy = self._provider_prompt(context.provider)
        if "PPLA deck visual style:" in brief:
            prompt = brief
        else:
            prompt = (
                f"{self._STYLE_PREFIX}\n\n"
                f"{self._BASE_GENERATION}\n\n"
                f"{provider_policy}\n\n"
                f"Card-specific visual brief:\n{brief}"
            )
        return (
            f"{prompt}\n\n"
            f"Generated card role: {context.image_role} side.\n"
            f"Layout plan: {context.layout}.\n"
            f"Reference image policy: {context.reference_image_policy} "
            f"({context.reference_image_count} attached reference image(s)).\n"
            f"Question to support: {scenario.question_text.strip()}\n"
            f"Back answer to explain visually: {scenario.answer_text.strip()}\n"
            f"{self._numeric_guard(scenario)}"
        )

    def _provider_prompt(self, provider: str) -> str:
        if provider == "openai":
            return self._OPENAI_GENERATION
        if provider == "gemini":
            return self._GEMINI_GENERATION
        if provider == "comparison":
            return self._COMPARISON_GENERATION
        return self._COMPARISON_GENERATION

    @staticmethod
    def _numeric_guard(scenario: VisualCardScenario) -> str:
        combined = "\n".join(
            value
            for value in [
                scenario.source_content,
                scenario.question_text,
                scenario.answer_text,
                scenario.visual_prompt,
                scenario.rendering_guide or "",
            ]
            if value
        )
        numeric_strings = sorted(set(re.findall(r"\b\d+(?:[.,]\d+)?\b", combined)))
        if not numeric_strings:
            return (
                "Numeric/value guard: the source/scenario supplied no numeric values. Do not show "
                "any numbers, numeric readouts, pressure values, altitude values, frequencies, "
                "limits, or unit-bearing quantities in the image. For instruments, use blank or "
                "abstract tick marks and word labels only."
            )
        return (
            "Numeric/value guard: only these source-provided numeric strings may appear, if needed: "
            + ", ".join(numeric_strings)
            + ". Do not invent any other numbers, dial readings, units, limits, frequencies, "
            "pressures, or altitudes."
        )


def _canonical_provider(provider: str) -> str:
    if not isinstance(provider, str):
        return "openai"
    value = str(provider or "").strip().lower()
    aliases = {
        "gpt": "openai",
        "gpt-image": "openai",
        "google": "gemini",
        "nano-banana": "gemini",
        "nanobanana": "gemini",
        "compare": "comparison",
    }
    return aliases.get(value, value or "openai")
