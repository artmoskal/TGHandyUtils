"""LLM classifier for `auto` mode: decide reminder vs anki for a piece of content."""

from langchain_core.messages import HumanMessage, SystemMessage

from core.interfaces import Intent, IConfig
from core.logging import get_logger
from services.llm_factory import create_chat_llm
from ai_workflow_engine.usage import invoke_metered_chat

logger = get_logger(__name__)


class IntentClassifier:
    """Classify content as a reminder/task or anki flashcard material."""

    _SYSTEM_PROMPT = """Decide what the user most likely wants done with this message.
Answer with exactly ONE word:
- "reminder" if it is a task, todo, appointment, or something to be reminded about (often time-related).
- "anki" if it is factual/study material the user likely wants to memorise as flashcards.
"""

    _USER_PROMPT = """Message:
{content}

Answer (one word - reminder or anki):"""

    _PROMPT = _SYSTEM_PROMPT + "\n" + _USER_PROMPT

    def __init__(self, config: IConfig):
        self.config = config
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = create_chat_llm(self.config, model=self._model_name(), temperature=0.0)
        return self._llm

    def _model_name(self) -> str:
        model = getattr(self.config, "ANKI_CARD_MODEL", None)
        if isinstance(model, str) and model.strip():
            return model
        return "gpt-5.4-mini"

    def classify(self, content: str) -> Intent:
        """Return the guessed Intent. Defaults to REMINDER on any ambiguity/failure."""
        try:
            resp = invoke_metered_chat(
                self.llm,
                [
                    SystemMessage(content=self._SYSTEM_PROMPT),
                    HumanMessage(content=self._USER_PROMPT.format(content=content)),
                ],
                node="intent_classifier",
                model=self._model_name(),
            )
            answer = (resp.content or "").strip().lower()
            if "anki" in answer or "flash" in answer or "card" in answer:
                return Intent.ANKI
            return Intent.REMINDER
        except Exception as e:
            logger.warning(f"Intent classification failed, defaulting to reminder: {e}")
            return Intent.REMINDER
