"""Anki card generation service.

Two independent concerns, deliberately separable so the packaging half can be unit-tested
without any LLM/network:
  - extract_cards(): content -> [AnkiCard]   (LLM, gpt-4o-mini)
  - build_package(): [AnkiCard] -> .apkg file (pure genanki, no Anki running)
"""

import os
import tempfile
from typing import List, Optional

import genanki
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import HumanMessage

from models.anki import AnkiCard, AnkiCardSet
from core.interfaces import IConfig
from core.exceptions import ParsingError
from core.logging import get_logger
from services.llm_factory import create_chat_llm

logger = get_logger(__name__)

# Fixed IDs so re-imported packages update the same model/deck instead of duplicating it.
# (genanki recommends stable per-application IDs.)
_ANKI_MODEL_ID = 1741927384
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


class AnkiCardService:
    """Turn arbitrary content into Anki flashcards and package them as .apkg."""

    _PROMPT = """You are a flashcard creation assistant. Turn the content below into high-quality
Anki flashcards (question/answer pairs) suitable for spaced-repetition study.

RULES:
- Questions must be self-contained (understandable without seeing the original content).
- Answers must be concise and correct. Do not invent facts not present in the content.
- Preserve the content's original language.
- Add 0-3 short lowercase tags per card (no spaces) when an obvious topic exists.
{instructions}

CONTENT:
{content}

{format_instructions}
"""

    def __init__(self, config: IConfig):
        self.config = config
        self._llm = None  # lazy: packaging must work without an API key
        self._parser = PydanticOutputParser(pydantic_object=AnkiCardSet)
        self._prompt = PromptTemplate(
            template=self._PROMPT,
            input_variables=["content", "instructions"],
            partial_variables={"format_instructions": self._parser.get_format_instructions()},
        )

    @property
    def llm(self):
        if self._llm is None:
            model = getattr(self.config, "ANKI_CARD_MODEL", "gpt-5.4-mini")
            self._llm = create_chat_llm(self.config, model=model, temperature=0.2)
        return self._llm

    @staticmethod
    def _build_instructions(strategy: str, count: Optional[int], guide: Optional[str]) -> str:
        lines = []
        if strategy == "merge":
            lines.append("- Produce EXACTLY ONE flashcard with a single broad question covering the key idea.")
        elif strategy == "count" and count:
            lines.append(f"- Produce EXACTLY {count} flashcard(s).")
        else:
            lines.append("- Each card tests ONE fact. Prefer several focused cards over one broad card.")
        if guide:
            lines.append(
                f'- FOLLOW THE USER INSTRUCTION EXACTLY: "{guide}". It overrides the default '
                f"question style. For example, if it says to expand an abbreviation, put the "
                f"abbreviation on the FRONT and its full form/definition on the BACK; if it asks "
                f"for a definition, put the term on the front and the definition on the back."
            )
        return "\n".join(lines)

    def extract_cards(self, content: str, guide: Optional[str] = None,
                      strategy: str = "split", count: Optional[int] = None) -> List[AnkiCard]:
        """Use the LLM to extract flashcards from content. Raises ParsingError on failure."""
        if not content or not content.strip():
            raise ParsingError("Cannot create flashcards from empty content")
        try:
            prompt_text = self._prompt.format(
                content=content,
                instructions=self._build_instructions(strategy, count, guide),
            )
            output = self.llm.invoke([HumanMessage(content=prompt_text)])
            card_set = self._parser.parse(output.content)
            if not card_set.cards:
                raise ParsingError("LLM returned no flashcards")
            logger.info(f"Extracted {len(card_set.cards)} flashcard(s) from content")
            return card_set.cards
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
            note = genanki.Note(
                model=_BASIC_MODEL,
                fields=[card.question, card.answer],
                tags=[t.replace(" ", "_") for t in card.tags] if card.tags else [],
            )
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
