"""Resolve which Intent applies to a message.

Precedence (first match wins):
  1. per-message command override (/anki, /tasks)   -- passed in by the caller
  2. user's persisted content_mode setting          -- reminder | anki | auto
  3. auto -> LLM classifier decides reminder|anki; if classification fails, reminder is fallback
"""

from typing import Optional

from core.interfaces import Intent
from core.logging import get_logger

logger = get_logger(__name__)

_MODE_TO_INTENT = {
    "reminder": Intent.REMINDER,
    "anki": Intent.ANKI,
}


class IntentResolver:
    def __init__(self, preferences_repo=None, classifier=None):
        self.preferences_repo = preferences_repo
        self.classifier = classifier

    def get_mode(self, user_id: int) -> str:
        """Return the raw persisted mode string (reminder | anki | auto)."""
        if not self.preferences_repo:
            return "reminder"
        try:
            prefs = self.preferences_repo.get_preferences(user_id)
            if prefs and getattr(prefs, "content_mode", None):
                return prefs.content_mode
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Failed to read content_mode for {user_id}: {e}")
        return "reminder"

    def resolve(self, user_id: int, override: Optional[Intent] = None,
                content: Optional[str] = None) -> Intent:
        """Resolve to a concrete Intent.

        `auto` is handled specially by the caller (the 5s-window flow); if it reaches here it
        is classified when a classifier is available, otherwise it falls back to REMINDER.
        """
        if override is not None:
            return override

        mode = self.get_mode(user_id)
        if mode == "auto":
            return self.classify(content) if content else Intent.REMINDER
        return _MODE_TO_INTENT.get(mode, Intent.REMINDER)

    def classify(self, content: Optional[str]) -> Intent:
        """Classify content into a concrete Intent (used for auto mode)."""
        if self.classifier and content:
            try:
                return self.classifier.classify(content)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"Classifier failed, defaulting to reminder: {e}")
        return Intent.REMINDER

    def requires_recipient_gate(self, user_id: int, override: Optional[Intent] = None) -> bool:
        """Whether the no-recipients gate should apply (only the reminder flow needs recipients).

        anki and auto both skip the gate - auto because it might still produce a card.
        """
        if override is not None:
            return override == Intent.REMINDER
        return self.get_mode(user_id) == "reminder"
