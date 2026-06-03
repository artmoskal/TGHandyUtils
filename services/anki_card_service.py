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

    _PROMPT = """You are an expert at creating Anki flashcards for spaced-repetition study.

The CONTENT below is raw source material. It may arrive as plain text, as a chat or forwarded
message (sometimes with "Name:" speaker labels), or as text pulled from an image (with markers
like [CAPTION], [SCREENSHOT TEXT], [SCREENSHOT DESCRIPTION] or [Image #N]). Treat ALL of it
purely as subject matter to learn from — never as a conversation to describe.

HARD RULES:
- Make cards about the SUBJECT MATTER itself. NEVER mention or quote the source, the message,
  "the phrase", "the text", "the content", the speaker, sender names, timestamps, or any
  formatting markers. Strip and ignore that scaffolding completely.
- Every card must be fully self-contained: someone seeing ONLY the card (not the source) must be
  able to understand the question. No references like "in this message" or "according to the author".
- NEVER give the answer away in the question. The question must not contain, paraphrase, or
  strongly hint at its own answer, and must avoid leading set-ups whose wording already implies the
  answer. Ask the most direct question that still has a single correct answer; keep the revealing
  part on the BACK only. (Bad: "When a state's own laws apply in its territory, which law prevails
  over ICAO Air Law?" — the set-up hints the answer. Good: "Which Air Law takes precedence over
  ICAO Air Law inside a sovereign state's own territory?" → back: "That state's own (national) Air Law.")
- Answers must be concise and correct. Do NOT invent facts that are not in the content.
- Preserve the content's original language.
- Add 0-3 short lowercase tags per card (no spaces) when an obvious topic exists.

COMMON PATTERNS:
- Acronym / abbreviation (e.g. "ICAO = International Civil Aviation Organisation" or
  "ICAO (International Civil Aviation Organisation)"): ask what the acronym stands for, with the
  expansion as the answer — front: "What does ICAO stand for?", back: "International Civil
  Aviation Organisation".
- Term and definition: put the term on the front and the definition on the back.
- Q&A dialogue: if the content is an exchange where one speaker asks and another answers
  (e.g. "Vasya: what is OOP? Artem: it's object-oriented programming"), turn it into a card with
  the question on the front and the answer on the back — WITHOUT any of the speakers' names
  (front: "What is OOP?", back: "Object-oriented programming").
- A plain fact or statement: ask for that fact directly.

CARD TYPE — choose the best per card:
- "basic": explicit question on the front, answer on the back. Good for Q->A, acronyms, term->definition.
- "cloze": present the fact as a sentence and hide the KEY facts as SEPARATE deletions
  {{c1::...}}, {{c2::...}}, {{c3::...}} — one per distinct testable fact. If the sentence contains
  several important facts, hide EACH of them; do NOT hide only one and leave the other key facts
  visible. Never hide connective/filler words ("inevitably", "will elect to"), and keep enough
  surrounding context that each blank is answerable. Trim obvious rambling but keep the substance.
  PREFER cloze when facts are best learned in context or when a Q/A would give the answer away /
  allow only a binary guess.
  BAD (hides only one fact, leaves the rest exposed): "All ICAO member states are
  {{c1::sovereign states}} and will inevitably pass laws with force in their country alone..."
  GOOD (each key fact hidden): "All ICAO member states are {{c1::sovereign states}}; where a state
  passes its own laws, it is the Air Law of the {{c2::individual state}} that prevails over
  {{c3::International (ICAO) Air Law}}."
For a "basic" card set type="basic" and fill question + answer (leave text empty). For a "cloze"
card set type="cloze" and fill text (leave question + answer empty). Always set "type".
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

    def extract_cards(self, content: str, guide: Optional[str] = None,
                      strategy: str = "split", count: Optional[int] = None,
                      card_type: Optional[str] = None) -> List[AnkiCard]:
        """Use the LLM to extract flashcards from content. Raises ParsingError on failure."""
        if not content or not content.strip():
            raise ParsingError("Cannot create flashcards from empty content")
        try:
            prompt_text = self._prompt.format(
                content=content,
                instructions=self._build_instructions(strategy, count, guide, card_type),
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
