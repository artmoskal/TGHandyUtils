"""OpenAI-specific prompt-cache request hints.

Prompt layout stays provider-neutral in product code. This helper only adds optional OpenAI routing
metadata for OpenAI-backed clients.
"""

import re
from typing import Any, Optional


_SAFE_KEY = re.compile(r"[^A-Za-z0-9_.:-]+")
_VALID_RETENTION = {"in_memory", "24h"}


def openai_prompt_cache_kwargs(config: Any, *, model: str = "", node: Optional[str] = None) -> dict[str, str]:
    """Return optional kwargs accepted by OpenAI chat completions for prompt-cache affinity."""
    prefix = getattr(config, "OPENAI_PROMPT_CACHE_KEY_PREFIX", "") if config else ""
    if not isinstance(prefix, str) or not prefix.strip():
        return {}

    parts = [prefix.strip()]
    if model:
        parts.append(model)
    if node:
        parts.append(node)
    cache_key = _SAFE_KEY.sub("-", ":".join(parts))[:128]

    kwargs = {"prompt_cache_key": cache_key}
    retention = getattr(config, "OPENAI_PROMPT_CACHE_RETENTION", "") if config else ""
    if isinstance(retention, str) and retention.strip() in _VALID_RETENTION:
        kwargs["prompt_cache_retention"] = retention.strip()
    return kwargs
