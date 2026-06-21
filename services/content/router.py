"""Explicit intent -> processor wiring.

Deliberately a plain map, NOT a plugin registry. Adding a mode = one entry here.
"""

from collections.abc import Callable

from core.interfaces import Intent, IContentProcessor

ProcessorFactory = Callable[[], IContentProcessor]

_processor_factories: dict[Intent, ProcessorFactory] = {}


def configure_processors(factories: dict[Intent, ProcessorFactory]) -> None:
    """Configure app-owned processor factories from the composition root."""

    if Intent.REMINDER not in factories:
        raise ValueError("processor router requires a reminder processor factory")
    _processor_factories.clear()
    _processor_factories.update(factories)


def get_processor(intent: Intent) -> IContentProcessor:
    if not _processor_factories:
        raise RuntimeError("content processors are not configured")
    provider = _processor_factories.get(intent, _processor_factories[Intent.REMINDER])
    return provider()
