"""Composable output cleaners for weak/quirky LLMs.

Small pure functions that sanitize raw model text before structured parsing. Weak local models
(e.g. qwen-family on Ollama) routinely wrap JSON in reasoning tags, Markdown fences, or chatter;
these cleaners recover the parseable payload without spending a repair LLM call.

Use via ``StructuredLLMNode(..., pre_parse=WEAK_MODEL_CLEANER)`` or compose your own with
:func:`compose_cleaners`. Every cleaner is conservative: when its pattern is absent it returns the
input unchanged, and when extraction fails it falls back to the original text so the normal
parse/repair path still applies.
"""

from __future__ import annotations

import re
from typing import Callable

TextCleaner = Callable[[str], str]

_THINK_TAG_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
# Unclosed think-tag (weak models sometimes never close it): drop from the opening tag onward
# only if a JSON-ish payload still remains before it — otherwise keep the text intact.
_OPEN_THINK_RE = re.compile(r"<think(?:ing)?>", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)


def strip_think_tags(text: str) -> str:
    """Remove ``<think>…</think>`` / ``<thinking>…</thinking>`` reasoning blocks."""

    cleaned = _THINK_TAG_RE.sub("", text)
    # Handle a dangling, never-closed think tag: keep whatever precedes it.
    match = _OPEN_THINK_RE.search(cleaned)
    if match and "</think" not in cleaned[match.end():].lower():
        prefix = cleaned[: match.start()]
        if prefix.strip():
            cleaned = prefix
    return cleaned.strip() if cleaned.strip() else text


def extract_fenced_json(text: str) -> str:
    """Return the body of the first \\```json …\\``` (or plain \\```) fence, if any."""

    match = _FENCE_RE.search(text)
    if not match:
        return text
    body = match.group(1).strip()
    return body if body else text


def extract_first_json_object(text: str) -> str:
    """Return the first balanced top-level JSON object/array found in the text.

    Brace counting is string-aware (braces inside JSON strings don't count). Returns the input
    unchanged when no balanced payload exists.
    """

    closers = {"{": "}", "[": "]"}
    start = min(
        (pos for pos in (text.find("{"), text.find("[")) if pos != -1),
        default=-1,
    )
    while start != -1:
        opener = text[start]
        closer = closers[opener]
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        nexts = [pos for pos in (text.find("{", start + 1), text.find("[", start + 1)) if pos != -1]
        start = min(nexts) if nexts else -1
    return text


def compose_cleaners(*cleaners: TextCleaner) -> TextCleaner:
    """Chain cleaners left-to-right into a single callable."""

    def cleaned(text: str) -> str:
        for cleaner in cleaners:
            text = cleaner(text)
        return text

    return cleaned


# The recommended default for weak local models: strip reasoning, unwrap fences, isolate JSON.
WEAK_MODEL_CLEANER: TextCleaner = compose_cleaners(
    strip_think_tags, extract_fenced_json, extract_first_json_object
)


# The one canonical repair prompt for structured-output retries (shared by StructuredLLMNode
# and LLMAgentPlanner; one voice for the same contract).
STRUCTURED_REPAIR_PROMPT = """The previous structured-output response was invalid.

Validation/parsing error:
{error}

Return a corrected JSON object only. Do not include prose or Markdown fences.

Original request:
{original_prompt}"""
