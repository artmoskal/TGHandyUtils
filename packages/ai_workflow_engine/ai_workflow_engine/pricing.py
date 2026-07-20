"""Model price tables and cost estimation (cost-honesty math lives here)."""

from __future__ import annotations

from typing import Optional

from ai_workflow_engine.usage_contract import default_chat_rate_table

_DEFAULT_PRICE_TABLE: dict[str, dict[str, float]] = {
    **default_chat_rate_table(),
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
    # same as normal input.
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
) -> Optional[float]:
    prices = _price_for_model(model)
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


def _price_for_model(model: str) -> Optional[dict[str, float]]:
    if model in _DEFAULT_PRICE_TABLE:
        return _DEFAULT_PRICE_TABLE[model]
    for key in sorted(_DEFAULT_PRICE_TABLE, key=len, reverse=True):
        if model.startswith(key):
            return _DEFAULT_PRICE_TABLE[key]
    return None
