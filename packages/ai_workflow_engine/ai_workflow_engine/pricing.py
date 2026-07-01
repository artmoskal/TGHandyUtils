"""Model price tables and cost estimation (cost-honesty math lives here)."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PRICE_TABLE: dict[str, dict[str, float]] = {
    # Official OpenAI pricing page, checked 2026-06-04. Keep override support because prices move.
    "gpt-5.5": {
        "input_per_1m": 5.0,
        "cached_input_per_1m": 0.5,
        "output_per_1m": 30.0,
    },
    "gpt-5.4": {
        "input_per_1m": 2.5,
        "cached_input_per_1m": 0.25,
        "output_per_1m": 15.0,
    },
    "gpt-5.4-mini": {
        "input_per_1m": 0.75,
        "cached_input_per_1m": 0.075,
        "output_per_1m": 4.5,
    },
    "gpt-5.4-nano": {
        "input_per_1m": 0.20,
        "cached_input_per_1m": 0.02,
        "output_per_1m": 1.25,
    },
    "gpt-image-2": {
        "text_input_per_1m": 5.0,
        "text_cached_input_per_1m": 1.25,
        "image_input_per_1m": 8.0,
        "image_cached_input_per_1m": 2.0,
        "image_output_per_1m": 30.0,
    },
    # Google Gemini API pricing, checked 2026-06-06. Generated images are metered as output image
    # tokens by resolution; provider adapters normalize image output into output_token_details.
    # Image-model pricing docs do not list cached-input discounts, so cached input is priced the
    # same as normal input unless WORKFLOW_MODEL_PRICE_OVERRIDES_JSON says otherwise.
    "gemini-2.5-flash-image": {
        "text_input_per_1m": 0.30,
        "text_cached_input_per_1m": 0.30,
        "image_input_per_1m": 0.30,
        "image_cached_input_per_1m": 0.30,
        "image_output_per_1m": 30.0,
    },
    "gemini-3.1-flash-image": {
        "text_input_per_1m": 0.50,
        "text_cached_input_per_1m": 0.50,
        "image_input_per_1m": 0.50,
        "image_cached_input_per_1m": 0.50,
        "image_output_per_1m": 60.0,
    },
    "gemini-3-pro-image": {
        "text_input_per_1m": 2.0,
        "text_cached_input_per_1m": 2.0,
        "image_input_per_1m": 2.0,
        "image_cached_input_per_1m": 2.0,
        "image_output_per_1m": 120.0,
    }
}


def estimate_cost_usd(
    model: str,
    operation: str,
    input_tokens: int,
    output_tokens: int,
    input_details: Optional[dict[str, int]] = None,
    output_details: Optional[dict[str, int]] = None,
    *,
    config: Any = None,
) -> Optional[float]:
    prices = _price_for_model(model, config)
    if not prices:
        return None

    input_details = input_details or {}
    output_details = output_details or {}
    if operation == "image":
        text_input = input_details.get("text_tokens", 0)
        image_input = input_details.get("image_tokens", max(input_tokens - text_input, 0))
        cached_text = input_details.get("text_cached_tokens", input_details.get("cached_text_tokens", 0))
        cached_image = input_details.get("image_cached_tokens", input_details.get("cached_image_tokens", 0))
        generic_cached = input_details.get("cache_read", input_details.get("cached_tokens", 0))
        if generic_cached and not (cached_text or cached_image):
            cached_text = min(text_input, generic_cached)
            cached_image = min(image_input, max(generic_cached - cached_text, 0))
        uncached_text = max(text_input - cached_text, 0)
        uncached_image = max(image_input - cached_image, 0)
        image_output = output_details.get("image_tokens", output_tokens)
        text_cost = uncached_text * prices.get("text_input_per_1m", 0.0) / 1_000_000
        text_cost += cached_text * prices.get("text_cached_input_per_1m", prices.get("text_input_per_1m", 0.0)) / 1_000_000
        image_in_cost = uncached_image * prices.get("image_input_per_1m", 0.0) / 1_000_000
        image_in_cost += cached_image * prices.get("image_cached_input_per_1m", prices.get("image_input_per_1m", 0.0)) / 1_000_000
        image_out_cost = image_output * prices.get("image_output_per_1m", 0.0) / 1_000_000
        return round(text_cost + image_in_cost + image_out_cost, 6)

    cached = input_details.get("cache_read", 0) or input_details.get("cached_tokens", 0)
    uncached_input = max(input_tokens - cached, 0)
    input_cost = uncached_input * prices.get("input_per_1m", 0.0) / 1_000_000
    cached_cost = cached * prices.get("cached_input_per_1m", prices.get("input_per_1m", 0.0)) / 1_000_000
    output_cost = output_tokens * prices.get("output_per_1m", 0.0) / 1_000_000
    return round(input_cost + cached_cost + output_cost, 6)


def _price_for_model(model: str, config: Any = None) -> Optional[dict[str, float]]:
    overrides = _price_overrides(config)
    if model in overrides:
        return overrides[model]
    if model in _DEFAULT_PRICE_TABLE:
        return _DEFAULT_PRICE_TABLE[model]
    price_table = {**_DEFAULT_PRICE_TABLE, **overrides}
    for key in sorted(price_table, key=len, reverse=True):
        if model.startswith(key):
            return price_table[key]
    return None


def _price_overrides(config: Any = None) -> dict[str, dict[str, float]]:
    raw = getattr(config, "WORKFLOW_MODEL_PRICE_OVERRIDES_JSON", "") if config else ""
    if not raw:
        return {}
    if not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid WORKFLOW_MODEL_PRICE_OVERRIDES_JSON")
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for model, prices in data.items():
        if isinstance(model, str) and isinstance(prices, dict):
            cleaned[model] = {str(k): float(v) for k, v in prices.items()}
    return cleaned

