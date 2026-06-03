"""LLM classifier for `auto` mode: decide reminder vs anki for a piece of content."""

from langchain_core.messages import HumanMessage

from core.interfaces import Intent, IConfig
from core.logging import get_logger
from services.llm_factory import create_chat_llm

logger = get_logger(__name__)


class IntentClassifier:
    """Classify content as a reminder/task or anki flashcard material (gpt-4o-mini)."""

    _PROMPT = """Decide what the user most likely wants done with this message.
Answer with exactly ONE word:
- "reminder" if it is a task, todo, appointment, or something to be reminded about (often time-related).
- "anki" if it is factual/study material the user likely wants to memorise as flashcards.

Message:
{content}

Answer (one word - reminder or anki):"""

    def __init__(self, config: IConfig):
        self.config = config
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = create_chat_llm(self.config, temperature=0.0)
        return self._llm

    def classify(self, content: str) -> Intent:
        """Return the guessed Intent. Defaults to REMINDER on any ambiguity/failure."""
        try:
            resp = self.llm.invoke([HumanMessage(content=self._PROMPT.format(content=content))])
            answer = (resp.content or "").strip().lower()
            if "anki" in answer or "flash" in answer or "card" in answer:
                return Intent.ANKI
            return Intent.REMINDER
        except Exception as e:
            logger.warning(f"Intent classification failed, defaulting to reminder: {e}")
            return Intent.REMINDER
