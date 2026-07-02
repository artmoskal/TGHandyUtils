"""Anki card generation service.

Two independent concerns, deliberately separable so the packaging half can be unit-tested
without any LLM/network:
  - extract_cards(): content -> [AnkiCard]   (LLM, config-driven ANKI_CARD_MODEL)
  - build_package(): [AnkiCard] -> .apkg file (pure genanki, no Anki running)
"""

import os
import re
import tempfile
from typing import List, Optional

import genanki
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import HumanMessage, SystemMessage

from models.anki import AnkiCard, AnkiCardSet
from core.interfaces import IConfig
from core.exceptions import ParsingError
from core.logging import get_logger
from services.llm_factory import create_anki_chat_model, llm_cost_class
from ai_workflow_engine.usage import invoke_metered_chat
from ai_workflow_engine.prompt_loader import load_prompt_template

logger = get_logger(__name__)

# Fixed IDs so re-imported packages update the same model/deck instead of duplicating it.
# (genanki recommends stable per-application IDs.)
_ANKI_MODEL_ID = 1741927384
_ANKI_CLOZE_MODEL_ID = 1741927385
_ANKI_DECK_ID = 2059400110
DEFAULT_DECK_NAME = "Telegram Cards"

_BASIC_MODEL = genanki.Model(
    _ANKI_MODEL_ID,
    "TGHandyUtils Basic",
    fields=[{"name": "Question"}, {"name": "Answer"}],
    templates=[
        {
            "name": "Card 1",
            "qfmt": "{{Question}}",
            "afmt": '{{FrontSide}}<hr id="answer">{{Answer}}',
        }
    ],
)

_CLOZE_MODEL = genanki.Model(
    _ANKI_CLOZE_MODEL_ID,
    "TGHandyUtils Cloze",
    fields=[{"name": "Text"}, {"name": "Back Extra"}],
    templates=[
        {
            "name": "Cloze",
            "qfmt": "{{cloze:Text}}",
            "afmt": "{{cloze:Text}}<br>{{Back Extra}}",
        }
    ],
    model_type=genanki.Model.CLOZE,
)


def _is_cloze(card) -> bool:
    """A card is a real cloze only if marked cloze AND it has a {{c...}} deletion."""
    return getattr(card, "type", "basic") == "cloze" and "{{c" in (card.text or "")


class AnkiCardService:
    """Turn arbitrary content into Anki flashcards and package them as .apkg."""

    _STATIC_PROMPT = load_prompt_template("anki/renderer.static.prompt")
    _DYNAMIC_PROMPT = load_prompt_template("anki/renderer.dynamic.prompt")

    _PROMPT = _STATIC_PROMPT + "\n" + _DYNAMIC_PROMPT

    def __init__(self, config: IConfig):
        self.config = config
        self._llm = None  # lazy: packaging must work without an API key
        self._parser = PydanticOutputParser(pydantic_object=AnkiCardSet)
        self._static_prompt = PromptTemplate(
            template=self._STATIC_PROMPT,
            input_variables=[],
            partial_variables={"format_instructions": self._parser.get_format_instructions()},
        )
        self._dynamic_prompt = PromptTemplate(
            template=self._DYNAMIC_PROMPT,
            input_variables=["content", "instructions"],
        )
        self._prompt = PromptTemplate(
            template=self._PROMPT,
            input_variables=["content", "instructions"],
            partial_variables={"format_instructions": self._parser.get_format_instructions()},
        )

    @property
    def llm(self):
        if self._llm is None:
            self._llm = create_anki_chat_model(self.config, model=self._model_name(), temperature=0.2)
        return self._llm

    def _model_name(self) -> str:
        return getattr(
            self.config,
            "ANKI_RENDER_MODEL",
            getattr(self.config, "ANKI_CARD_MODEL", "gpt-5.4-mini"),
        )

    @staticmethod
    def _build_instructions(strategy: str, count: Optional[int], guide: Optional[str],
                            card_type: Optional[str] = None) -> str:
        lines = []
        if card_type == "cloze":
            # Cloze: ONE note by default (a single sentence with several deletions), N if asked.
            if count:
                lines.append(f"- Produce EXACTLY {count} separate cloze cards (each type 'cloze').")
            else:
                lines.append(
                    "- Produce EXACTLY ONE cloze card (type 'cloze'): a single sentence with each key "
                    "fact hidden as a separate deletion {{c1::...}}, {{c2::...}}, …. Do NOT split it "
                    "into multiple cloze notes."
                )
        else:
            if strategy == "merge":
                lines.append("- Produce EXACTLY ONE flashcard with a single broad question covering the key idea.")
            elif strategy == "count" and count:
                lines.append(f"- Produce EXACTLY {count} flashcard(s).")
            else:
                lines.append("- Each card tests ONE fact. Prefer several focused cards over one broad card.")
            if card_type == "basic":
                lines.append("- Make EVERY card a BASIC question/answer card (type 'basic').")
        if guide:
            lines.append(
                f'- FOLLOW THE USER INSTRUCTION EXACTLY: "{guide}". It overrides the default '
                f"question style. For example, if it says to expand an abbreviation, put the "
                f"abbreviation on the FRONT and its full form/definition on the BACK; if it asks "
                f"for a definition, put the term on the front and the definition on the back."
            )
        return "\n".join(lines)

    @staticmethod
    def _normalise_cloze_markup(cards: List[AnkiCard]) -> List[AnkiCard]:
        """Repair the common single-brace LLM mistake: {c1::x} -> {{c1::x}}."""
        single_cloze = re.compile(r"(?<!\{)\{(c\d+::[^{}\n]+)\}(?!\})")
        for card in cards:
            if getattr(card, "type", "basic") == "cloze" and card.text:
                card.text = single_cloze.sub(r"{{\1}}", card.text)
        return cards

    @staticmethod
    def _all_valid_requested_clozes(cards: List[AnkiCard]) -> bool:
        return bool(cards) and all(
            getattr(card, "type", "basic") == "cloze" and "{{c" in (card.text or "")
            for card in cards
        )

    @staticmethod
    def _cloze_repair_instruction() -> str:
        return (
            "\n\nREPAIR REQUIREMENT:\n"
            "- The previous output was invalid for Anki cloze cards.\n"
            "- Return ONLY cloze cards with type='cloze'.\n"
            "- The text field MUST contain literal double-brace Anki cloze deletions, for example "
            "{{c1::impact pressure}} and {{c2::static pressure}}.\n"
            "- Do not return basic question/answer cards."
        )

    def _invoke_cards(self, content: str, instructions: str) -> List[AnkiCard]:
        system_text = self._static_prompt.format()
        user_text = self._dynamic_prompt.format(content=content, instructions=instructions or "None")
        model_name = self._model_name()
        output = invoke_metered_chat(
            self.llm,
            [SystemMessage(content=system_text), HumanMessage(content=user_text)],
            node="render_text_or_cloze",
            model=model_name,
            metadata={"card_type": "render"},
            config=self.config,
            # the backend registry knows whether this model is metered or rides a
            # subscription — phantom metered $0 would break cost honesty.
            cost_class=llm_cost_class(model_name),
        )
        card_set = self._parser.parse(output.content)
        if not card_set.cards:
            raise ParsingError("LLM returned no flashcards")
        return self._normalise_cloze_markup(card_set.cards)

    def extract_cards(self, content: str, guide: Optional[str] = None,
                      strategy: str = "split", count: Optional[int] = None,
                      card_type: Optional[str] = None) -> List[AnkiCard]:
        """Use the LLM to extract flashcards from content. Raises ParsingError on failure."""
        if not content or not content.strip():
            raise ParsingError("Cannot create flashcards from empty content")
        try:
            instructions = self._build_instructions(strategy, count, guide, card_type)
            cards = self._invoke_cards(content, instructions)
            if card_type == "cloze" and not self._all_valid_requested_clozes(cards):
                logger.warning("Cloze extraction returned invalid cloze markup; retrying once")
                cards = self._invoke_cards(content, instructions + self._cloze_repair_instruction())
                if not self._all_valid_requested_clozes(cards):
                    raise ParsingError("LLM returned invalid cloze card(s)")
            logger.info(f"Extracted {len(cards)} flashcard(s) from content")
            return cards
        except ParsingError:
            raise
        except Exception as e:
            logger.error(f"Flashcard extraction failed: {e}")
            raise ParsingError(f"Flashcard extraction failed: {e}")

    def build_package(
        self,
        cards: List[AnkiCard],
        deck_name: str = DEFAULT_DECK_NAME,
        output_path: Optional[str] = None,
        media_files: Optional[List[str]] = None,
    ) -> str:
        """Build an .apkg file from cards and return its path. Pure - no network.

        Cards may contain `<img src="basename">` HTML; pass the matching file paths in
        media_files so genanki bundles them into the package.
        """
        if not cards:
            raise ValueError("Cannot build an Anki package with no cards")

        deck = genanki.Deck(_ANKI_DECK_ID, deck_name)
        for card in cards:
            tags = [t.replace(" ", "_") for t in card.tags] if card.tags else []
            if _is_cloze(card):
                note = genanki.Note(model=_CLOZE_MODEL, fields=[card.text, ""], tags=tags)
            else:
                # basic card; if mis-tagged as cloze without a deletion, fall back to text->question
                question = card.question or card.text
                note = genanki.Note(model=_BASIC_MODEL, fields=[question, card.answer], tags=tags)
            deck.add_note(note)

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".apkg", prefix="tg_anki_")
            os.close(fd)

        package = genanki.Package(deck)
        if media_files:
            package.media_files = [p for p in media_files if p and os.path.exists(p)]
        package.write_to_file(output_path)
        logger.info(f"Wrote Anki package with {len(cards)} card(s) to {output_path}")
        return output_path
