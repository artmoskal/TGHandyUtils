"""Explicit intent -> processor wiring.

Deliberately a plain map, NOT a plugin registry. Adding a mode = one entry here.
"""

from core.interfaces import Intent, IContentProcessor
from core.container import container


def get_processor(intent: Intent) -> IContentProcessor:
    mapping = {
        Intent.REMINDER: container.reminder_processor,
        Intent.ANKI: container.anki_processor,
    }
    provider = mapping.get(intent, container.reminder_processor)
    return provider()
