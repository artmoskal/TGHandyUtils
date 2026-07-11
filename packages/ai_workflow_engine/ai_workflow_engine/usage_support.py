"""Shared coercion helpers for the usage/budget/pricing modules (leaf module)."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _model_or_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:
            # Cost-honesty: a provider payload whose dump explodes must not VANISH silently —
            # the summary degrades to no-detail, but the loss is visible in the logs.
            logger.warning(
                "provider usage payload of type %s failed model_dump(); token details dropped",
                type(value).__name__,
                exc_info=True,
            )
            return {}
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _details(value: Any) -> dict[str, int]:
    data = _model_or_dict(value)
    return {str(key): _safe_int(val) for key, val in data.items() if isinstance(val, (int, float))}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# R4: the _positive_int/_positive_float/_limit_int/_limit_float coercers died with
# budget_from_config (v0.9 typed-limits contract) — the application boundary now lives in
# the product config (config.engine_runtime_limits), not here.
