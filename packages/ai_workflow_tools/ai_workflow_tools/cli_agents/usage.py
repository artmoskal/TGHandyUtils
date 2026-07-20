"""Provider-protocol usage extraction for CLI-backed calls."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

from pydantic import ValidationError

from ai_workflow_engine.usage_contract import NormalizedTokenUsage, UsageError

from .models import CliFlavor


@dataclass(frozen=True)
class ParsedCliOutput:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_output_tokens: int = 0
    num_turns: int | None = None
    duration_ms: int | None = None
    provider_reported_notional_usd: float | None = None
    normalized_usage: NormalizedTokenUsage | None = None
    usage_error: UsageError | None = None
    usage_diagnostic: str | None = None


class CliUsageAccumulator(Protocol):
    """Provider-owned bounded stdout observer used by every CLI process door."""

    def feed(self, chunk: bytes) -> None: ...

    def finish(self) -> ParsedCliOutput: ...


class _BoundedJsonLineAccumulator:
    """Incremental line framing with a hard partial-line memory bound."""

    def __init__(self, *, max_partial_line_bytes: int) -> None:
        if type(max_partial_line_bytes) is not int or max_partial_line_bytes < 1:
            raise ValueError("max_partial_line_bytes must be a positive int")
        self.max_partial_line_bytes = max_partial_line_bytes
        self._buffer = bytearray()
        self._discarding_oversized_line = False
        self.observed_bytes = 0

    def feed(self, chunk: bytes) -> None:
        if not isinstance(chunk, bytes):
            raise TypeError(f"{type(self).__name__}.feed expects bytes")
        self.observed_bytes += len(chunk)
        remaining = chunk
        while remaining:
            if self._discarding_oversized_line:
                newline = remaining.find(b"\n")
                if newline < 0:
                    return
                self._discarding_oversized_line = False
                remaining = remaining[newline + 1 :]
                continue

            newline = remaining.find(b"\n")
            if newline < 0:
                self._buffer.extend(remaining)
                if len(self._buffer) > self.max_partial_line_bytes:
                    self._buffer.clear()
                    self._discarding_oversized_line = True
                    self._record_oversized_line()
                return

            self._buffer.extend(remaining[:newline])
            if len(self._buffer) > self.max_partial_line_bytes:
                self._record_oversized_line()
            else:
                self._consume_line(bytes(self._buffer))
            self._buffer.clear()
            remaining = remaining[newline + 1 :]

    def _finish_line(self) -> None:
        if self._discarding_oversized_line:
            self._discarding_oversized_line = False
            return
        if self._buffer:
            self._consume_line(bytes(self._buffer))
            self._buffer.clear()

    def _consume_line(self, raw: bytes) -> None:
        raise NotImplementedError

    def _record_oversized_line(self) -> None:
        raise NotImplementedError


class CodexUsageAccumulator(_BoundedJsonLineAccumulator):
    """Bounded incremental parser for Codex JSONL stdout.

    ``turn.completed`` counters are cumulative snapshots, so the latest completed turn
    replaces the previous one. Non-protocol chatter is ignored and stderr is never parsed.
    """

    def __init__(self, *, max_partial_line_bytes: int = 64 * 1024) -> None:
        super().__init__(max_partial_line_bytes=max_partial_line_bytes)
        self._latest_usage: NormalizedTokenUsage | None = None
        self._latest_error: UsageError | None = "usage_event_missing"
        self._latest_diagnostic: str | None = None

    def finish(self) -> ParsedCliOutput:
        self._finish_line()
        usage = self._latest_usage
        return ParsedCliOutput(
            text="",
            input_tokens=usage.raw_input_tokens if usage else 0,
            output_tokens=usage.raw_output_tokens if usage else 0,
            cache_read_tokens=usage.cache_read_input_tokens if usage else 0,
            cache_creation_tokens=usage.cache_creation_input_tokens if usage else 0,
            reasoning_output_tokens=usage.reasoning_output_tokens if usage else 0,
            normalized_usage=usage,
            usage_error=self._latest_error,
            usage_diagnostic=self._latest_diagnostic,
        )

    def _record_oversized_line(self) -> None:
        # A protocol event cannot be trusted once its line exceeds the bounded parser.
        self._latest_usage = None
        self._latest_error = "usage_event_malformed"
        self._latest_diagnostic = (
            f"codex turn.completed line exceeded {self.max_partial_line_bytes} bytes"
        )

    def _consume_line(self, raw: bytes) -> None:
        if not raw.strip():
            return
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            return
        usage = event.get("usage")
        if not isinstance(usage, dict):
            self._latest_usage = None
            self._latest_error = "usage_event_malformed"
            self._latest_diagnostic = _bounded_usage_diagnostic(
                "codex", {"received_type": type(usage).__name__}
            )
            return
        try:
            self._latest_usage = _normalize_codex_usage(usage)
        except (TypeError, ValueError, ValidationError):
            self._latest_usage = None
            self._latest_error = "invalid_token_counters"
            self._latest_diagnostic = _bounded_usage_diagnostic("codex", usage)
        else:
            self._latest_error = None
            self._latest_diagnostic = None


class ClaudeEnvelopeAccumulator(_BoundedJsonLineAccumulator):
    """Bounded observer for Claude's one-line JSON result envelope."""

    def __init__(self, *, max_partial_line_bytes: int = 64 * 1024) -> None:
        super().__init__(max_partial_line_bytes=max_partial_line_bytes)
        self._latest = ParsedCliOutput(text="", usage_error="usage_event_missing")

    def finish(self) -> ParsedCliOutput:
        self._finish_line()
        return self._latest

    def _record_oversized_line(self) -> None:
        self._latest = ParsedCliOutput(
            text="",
            usage_error="usage_event_malformed",
            usage_diagnostic=(
                f"claude result line exceeded {self.max_partial_line_bytes} bytes"
            ),
        )

    def _consume_line(self, raw: bytes) -> None:
        if not raw.strip():
            return
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(envelope, dict):
            return
        self._latest = _parse_claude_envelope_value(envelope)


def usage_accumulator_for(flavor: CliFlavor) -> CliUsageAccumulator | None:
    if flavor.result_source == "result_file":
        return CodexUsageAccumulator()
    if flavor.result_source == "stdout_json_envelope":
        return ClaudeEnvelopeAccumulator()
    return None


def parse_cli_process_output(flavor: CliFlavor, output: dict[str, Any]) -> ParsedCliOutput:
    """Parse result text and provider usage without mixing their authorities."""

    stdout = str(output.get("stdout") or "")
    if flavor.result_source == "stdout_json_envelope":
        return _parse_claude_envelope(stdout)
    if flavor.result_source == "result_file":
        accumulator = CodexUsageAccumulator()
        accumulator.feed(stdout.encode("utf-8"))
        return parsed_cli_output_from_accumulator(output, accumulator)
    return ParsedCliOutput(text=stdout, usage_error="usage_event_missing")


def parsed_cli_output_from_accumulator(
    output: dict[str, Any],
    accumulator: CliUsageAccumulator,
) -> ParsedCliOutput:
    stdout = str(output.get("stdout") or "")
    if getattr(accumulator, "observed_bytes", 0) == 0 and stdout:
        # Custom/test process runners may return captured stdout without driving the
        # streaming observer. The returned capture is still the same bounded process truth.
        accumulator.feed(stdout.encode("utf-8"))
    parsed = accumulator.finish()
    if (
        isinstance(accumulator, ClaudeEnvelopeAccumulator)
        and not parsed.text
        and parsed.normalized_usage is None
        and parsed.provider_reported_notional_usd is None
        and stdout
    ):
        # Preserve Claude's historical raw-text fallback when stdout is not an envelope.
        return _parse_claude_envelope(stdout)
    if not isinstance(accumulator, CodexUsageAccumulator) or "result" not in output:
        return parsed
    return ParsedCliOutput(
        **{
            **parsed.__dict__,
            "text": _stringify_text(output.get("result") or ""),
        }
    )


def _parse_claude_envelope(stdout: str) -> ParsedCliOutput:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return ParsedCliOutput(text=stdout, usage_error="usage_event_malformed")
    if not isinstance(envelope, dict):
        return ParsedCliOutput(text=stdout, usage_error="usage_event_malformed")
    return _parse_claude_envelope_value(envelope)


def _parse_claude_envelope_value(envelope: dict[str, Any]) -> ParsedCliOutput:
    raw_usage = envelope.get("usage")
    usage: NormalizedTokenUsage | None = None
    usage_error: UsageError | None = "usage_event_missing"
    usage_diagnostic: str | None = None
    if isinstance(raw_usage, dict):
        try:
            usage = _normalize_claude_usage(raw_usage)
        except (TypeError, ValueError, ValidationError):
            usage_error = "invalid_token_counters"
            usage_diagnostic = _bounded_usage_diagnostic("claude", raw_usage)
        else:
            usage_error = None
    elif raw_usage is not None:
        usage_error = "usage_event_malformed"
        usage_diagnostic = _bounded_usage_diagnostic(
            "claude", {"received_type": type(raw_usage).__name__}
        )
    provider_cost = _strict_optional_float(envelope.get("total_cost_usd"))
    return ParsedCliOutput(
        text=_stringify_text(envelope.get("result")),
        input_tokens=usage.raw_input_tokens if usage else 0,
        output_tokens=usage.raw_output_tokens if usage else 0,
        cache_read_tokens=usage.cache_read_input_tokens if usage else 0,
        cache_creation_tokens=usage.cache_creation_input_tokens if usage else 0,
        reasoning_output_tokens=usage.reasoning_output_tokens if usage else 0,
        num_turns=_strict_optional_int(envelope.get("num_turns")),
        duration_ms=_strict_optional_int(envelope.get("duration_ms")),
        provider_reported_notional_usd=provider_cost,
        normalized_usage=usage,
        usage_error=usage_error,
        usage_diagnostic=usage_diagnostic,
    )


def _normalize_codex_usage(usage: dict[str, Any]) -> NormalizedTokenUsage:
    raw_input = _strict_counter(usage, "input_tokens")
    cached = _strict_counter(usage, "cached_input_tokens", default=0)
    raw_output = _strict_counter(usage, "output_tokens")
    reasoning = _strict_counter(usage, "reasoning_output_tokens", default=0)
    total = _strict_counter(usage, "total_tokens", default=None)
    if cached > raw_input or reasoning > raw_output:
        raise ValueError("Codex cached/reasoning counters must be subsets")
    return NormalizedTokenUsage(
        counter_schema="codex_inclusive",
        uncached_input_tokens=raw_input - cached,
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=0,
        non_reasoning_output_tokens=raw_output - reasoning,
        reasoning_output_tokens=reasoning,
        raw_input_tokens=raw_input,
        raw_output_tokens=raw_output,
        raw_total_tokens=total,
    )


def _normalize_claude_usage(usage: dict[str, Any]) -> NormalizedTokenUsage:
    raw_input = _strict_counter(usage, "input_tokens", default=0)
    cache_read = _strict_counter(usage, "cache_read_input_tokens", default=0)
    cache_creation = _strict_counter(usage, "cache_creation_input_tokens", default=0)
    raw_output = _strict_counter(usage, "output_tokens", default=0)
    return NormalizedTokenUsage(
        counter_schema="claude_disjoint_cache",
        uncached_input_tokens=raw_input,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
        non_reasoning_output_tokens=raw_output,
        reasoning_output_tokens=0,
        raw_input_tokens=raw_input,
        raw_output_tokens=raw_output,
    )


def _strict_counter(
    data: dict[str, Any],
    key: str,
    *,
    default: int | None | object = ...,
) -> int | None:
    if key not in data:
        if default is ...:
            raise ValueError(f"missing token counter {key}")
        return default  # type: ignore[return-value]
    value = data[key]
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _strict_optional_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _strict_optional_float(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(value)
    return number if number >= 0 and number < float("inf") else None


def _stringify_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _bounded_usage_diagnostic(provider: str, raw_usage: Any) -> str:
    """Retain only bounded token-counter data, never the provider's full envelope."""

    counter_keys = (
        "input_tokens",
        "cached_input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
        "received_type",
    )
    if isinstance(raw_usage, dict):
        sanitized: dict[str, Any] = {}
        for key in counter_keys:
            if key not in raw_usage:
                continue
            value = raw_usage[key]
            if type(value) in (int, float, bool) or value is None:
                sanitized[key] = value
            elif key == "received_type" and isinstance(value, str):
                sanitized[key] = value[:64]
            else:
                sanitized[key] = f"<{type(value).__name__}>"
        rendered = json.dumps(sanitized, sort_keys=True, separators=(",", ":"))
    else:
        rendered = f"<{type(raw_usage).__name__}>"
    return f"{provider} usage counters: {rendered}"[:500]


__all__ = [
    "ClaudeEnvelopeAccumulator",
    "CliUsageAccumulator",
    "CodexUsageAccumulator",
    "ParsedCliOutput",
    "parse_cli_process_output",
    "parsed_cli_output_from_accumulator",
    "usage_accumulator_for",
]
