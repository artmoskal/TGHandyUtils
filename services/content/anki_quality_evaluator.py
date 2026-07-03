"""Rendered-card quality evaluation for the Anki workflow graph."""

import base64
import json
import mimetypes
from typing import Any, Optional

from langchain_core.messages import BaseMessage, HumanMessage

from core.exceptions import ParsingError
from core.interfaces import IConfig
from models.anki_workflow import (
    AnkiDirectiveConstraints,
    CardBuildPlan,
    ContentSource,
    GeneratedMedia,
    RenderedCardEvaluation,
    RenderedCardSet,
)
from ai_workflow_engine.engine import StructuredLLMNode, StructuredOutputError
from ai_workflow_engine.prompt_loader import load_prompt_template
from services.llm_factory import create_anki_text_llm, llm_supports_vision


class AnkiRenderedCardEvaluator:
    """Evaluate rendered cards and return a structured branch decision."""

    _STATIC_PROMPT = load_prompt_template("anki/quality.static.prompt")
    _DYNAMIC_PROMPT = load_prompt_template("anki/quality.dynamic.prompt")

    _PROMPT = _STATIC_PROMPT + "\n" + _DYNAMIC_PROMPT

    _REPAIR_PROMPT = load_prompt_template("anki/quality.repair.prompt")

    def __init__(self, config: IConfig, llm: Optional[Any] = None, inspect_images: bool = True):
        self.config = config
        self.inspect_images = inspect_images
        if llm is None and inspect_images:
            # Quality inspection attaches generated images (vision); the browser /ask
            # backend is text-only. Fail at construction, not mid-run with dropped images.
            quality_model = getattr(
                config, "ANKI_QUALITY_MODEL", getattr(config, "ANKI_CARD_MODEL", "")
            )
            if not llm_supports_vision(quality_model):
                raise ValueError(
                    f"ANKI_QUALITY_MODEL={quality_model!r} routes to a text-only backend "
                    "and cannot inspect images — use an API vision model for the quality "
                    "role or disable ANKI_QUALITY_INSPECT_IMAGES"
                )
        self._node = StructuredLLMNode(
            name="evaluate_rendered_cards",
            config=config,
            output_model=RenderedCardEvaluation,
            static_prompt_template=self._STATIC_PROMPT,
            dynamic_prompt_template=self._DYNAMIC_PROMPT,
            dynamic_input_variables=[
                "source_material",
                "directive_summary",
                "build_plan_summary",
                "scenario_summary",
                "rendered_summary",
                "media_summary",
            ],
            model_attr="ANKI_QUALITY_MODEL",
            default_model_attr="ANKI_CARD_MODEL",
            default_model="gpt-5.4-mini",
            temperature=0.0,
            llm=llm,
            llm_factory=create_anki_text_llm,
            validator=self._validate_evaluation,
            repair_prompt_template=self._REPAIR_PROMPT,
        )

    @property
    def llm(self):
        return self._node.llm

    async def evaluate(
        self,
        source: ContentSource,
        directives: AnkiDirectiveConstraints,
        build_plan: CardBuildPlan,
        scenario: Any,
        rendered: RenderedCardSet,
    ) -> RenderedCardEvaluation:
        deterministic = self._deterministic_rejection(rendered)
        if deterministic:
            return deterministic

        try:
            return await self._node.run(
                {
                    "source_material": self._source_material(source),
                    "directive_summary": self._model_json(directives),
                    "build_plan_summary": self._model_json(build_plan),
                    "scenario_summary": self._model_json(scenario),
                    "rendered_summary": self._model_json(rendered),
                    "media_summary": self._media_summary(rendered.generated_media),
                },
                content_hash_input=source.content,
                # LangChain clients get images as multimodal message parts (attempt 1):
                message_factory=lambda messages, attempt: self._messages(
                    messages,
                    rendered.generated_media if attempt == 1 else [],
                ),
                # Plain-callable clients (e.g. claude-p staged vision) get the same images
                # through the LLMRequest transport instead:
                images=self._image_inputs(rendered.generated_media) if self.inspect_images else [],
            )
        except StructuredOutputError as exc:
            raise ParsingError(f"Quality evaluator returned invalid output: {exc}") from exc

    def _messages(self, messages: list[BaseMessage], generated_media: list[GeneratedMedia]) -> list[BaseMessage]:
        image_parts = self._image_parts(generated_media) if self.inspect_images else []
        if not image_parts:
            return messages
        updated = list(messages)
        for index in range(len(updated) - 1, -1, -1):
            message = updated[index]
            if isinstance(message, HumanMessage):
                text = self._text_content(message.content)
                updated[index] = HumanMessage(content=[{"type": "text", "text": text}, *image_parts])
                return updated
        return [*updated, HumanMessage(content=image_parts)]

    @staticmethod
    def _text_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict):
                    value = item.get("text") or item.get("content")
                    if value:
                        texts.append(str(value))
                else:
                    texts.append(str(item))
            return "\n".join(texts)
        return str(content)

    @staticmethod
    def _deterministic_rejection(rendered: RenderedCardSet) -> Optional[RenderedCardEvaluation]:
        if not rendered.cards:
            return RenderedCardEvaluation(
                accepted=False,
                issues=["no rendered cards"],
                severity="high",
                repair_strategy="fallback",
                guidance="Render produced no cards; fall back to a basic text card.",
            )
        for card in rendered.cards:
            if getattr(card, "type", "basic") == "cloze":
                if "{{c" not in (card.text or ""):
                    return RenderedCardEvaluation(
                        accepted=False,
                        issues=["cloze card has no Anki deletion"],
                        severity="high",
                        repair_strategy="repair_render",
                        guidance="Render a valid cloze note with meaningful {{cN::...}} deletions.",
                    )
            elif not ((card.question or "").strip() and (card.answer or "").strip()):
                return RenderedCardEvaluation(
                    accepted=False,
                    issues=["basic card is missing front or back"],
                    severity="high",
                    repair_strategy="repair_render",
                    guidance="Render a basic card with both a self-contained question and concise answer.",
                )
        return None

    @staticmethod
    def _validate_evaluation(evaluation: RenderedCardEvaluation) -> None:
        if evaluation.accepted:
            evaluation.repair_strategy = "accept"
            evaluation.severity = "none"
            return
        if evaluation.repair_strategy == "accept":
            raise ParsingError("rejected evaluation cannot use accept repair_strategy")
        if not evaluation.issues:
            raise ParsingError("rejected evaluation must include issues")

    @staticmethod
    def _source_material(source: ContentSource) -> str:
        text = (source.content or "").strip()
        if len(text) > 6000:
            text = text[:6000] + "\n[TRUNCATED]"
        return text

    @staticmethod
    def _model_json(value: Any) -> str:
        if value is None:
            return "{}"
        if hasattr(value, "model_dump"):
            return json.dumps(value.model_dump(), sort_keys=True)
        return json.dumps(value, sort_keys=True)

    @staticmethod
    def _media_summary(media: list[GeneratedMedia]) -> str:
        return json.dumps(
            [
                {
                    "basename": item.basename,
                    "source": item.source,
                    "role": item.role,
                    "metadata": item.metadata,
                }
                for item in media
            ],
            sort_keys=True,
        )

    @staticmethod
    def _image_inputs(media: list[GeneratedMedia]) -> list:
        """Path-sourced ImageInputs for plain-callable clients (same cap/filter as
        ``_image_parts`` — first two non-audio images)."""

        from ai_workflow_engine import ImageInput

        inputs = []
        for item in media:
            if item.role == "audio":
                continue
            mime = mimetypes.guess_type(item.path)[0] or "image/png"
            if not mime.startswith("image/"):
                continue
            inputs.append(ImageInput(source="path", data=item.path, media_type=mime, role=item.role))
            if len(inputs) >= 2:
                break
        return inputs

    @staticmethod
    def _image_parts(media: list[GeneratedMedia]) -> list[dict]:
        parts = []
        for item in media:
            if item.role == "audio":
                continue
            mime = mimetypes.guess_type(item.path)[0] or "image/png"
            if not mime.startswith("image/"):
                continue
            try:
                with open(item.path, "rb") as fh:
                    data = base64.b64encode(fh.read()).decode("ascii")
            except OSError:
                continue
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
            if len(parts) >= 2:
                break
        return parts
