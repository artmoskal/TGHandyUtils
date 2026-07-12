"""Related-run identity helpers — ONE owner for every surface that touches it.

``correlation_id`` (shown to humans as *Related-run ID*) is an optional caller-supplied
grouping key shared by separate runs of one case/audit/batch/investigation. It never
controls storage, deduplication, scheduling, or execution. These two helpers are the
single enforcement points so trace, detail, and usage surfaces — and every persisted
schema — cannot drift apart:

- :func:`enrich_related_run` stamps an authoritative id into event metadata, refusing
  LOUDLY (no string coercion — ``42`` never "matches" ``"42"``) when the event already
  claims a different identity.
- :func:`validate_optional_correlation` is the shared pydantic field rule: absent stays
  absent; a blank/whitespace id would correlate unrelated work under an empty label.

Dependency-light leaf: importable by models, sinks, and usage without cycles.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["enrich_related_run", "validate_optional_correlation"]

_MISSING = object()


def enrich_related_run(
    metadata: Optional[Dict[str, Any]],
    authoritative: Optional[str],
    *,
    surface: str,
    event_id: Any = None,
) -> Optional[Dict[str, Any]]:
    """Return metadata carrying the run's related-run id, or raise on a conflict.

    No authoritative id -> metadata returned unchanged (absent stays absent). An existing
    value must be EXACTLY the authoritative string — same type, same value — otherwise
    the event is refused before any buffer/bundle/sink/summary persistence.
    """

    if not authoritative:
        return metadata
    existing = (metadata or {}).get("correlation_id", _MISSING)
    if existing is not _MISSING:
        if not isinstance(existing, str) or existing != authoritative:
            raise ValueError(
                f"{surface} event {event_id or ''} claims correlation_id {existing!r} "
                f"({type(existing).__name__}) but the run is correlated to "
                f"{authoritative!r} — conflicting related-run identity is refused before "
                "persistence"
            )
        return metadata
    return {**(metadata or {}), "correlation_id": authoritative}


def validate_optional_correlation(value: Optional[str]) -> Optional[str]:
    """Shared schema rule: None is fine; a provided id must be non-blank."""

    if value is not None and not value.strip():
        raise ValueError("correlation_id must be non-blank when provided (or omitted)")
    return value
