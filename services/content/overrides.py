"""Per-message intent overrides from the /anki and /tasks commands.

Two ways to override the user's saved content_mode for a single message:
  - inline:    "/anki <content>" -> processed immediately as a card (handled by the command)
  - armed:     "/anki" alone     -> the NEXT message is processed as a card (stored here)

The store is a simple in-memory dict (same pattern as the threading buffers). A one-shot
override is consumed by the next incoming message at the entry handlers.
"""

from typing import Dict, Optional, Tuple

from core.interfaces import Intent

_armed: Dict[int, Intent] = {}


def arm(user_id: int, intent: Intent) -> None:
    """Arm a one-shot override for the user's next message."""
    _armed[user_id] = intent


def peek(user_id: int) -> Optional[Intent]:
    """Return any armed override WITHOUT clearing it (used for the recipient gate)."""
    return _armed.get(user_id)


def consume(user_id: int) -> Optional[Intent]:
    """Return and clear any armed override for this user."""
    return _armed.pop(user_id, None)


def parse_caption_override(text: Optional[str]) -> Tuple[Optional[Intent], Optional[str]]:
    """Detect a leading /anki or /tasks command in caption/text.

    Returns (intent_or_None, remaining_text_with_command_stripped).
    """
    if not text:
        return None, text
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered.startswith("/anki"):
        return Intent.ANKI, stripped[len("/anki"):].strip()
    if lowered.startswith("/tasks"):
        return Intent.REMINDER, stripped[len("/tasks"):].strip()
    return None, text
