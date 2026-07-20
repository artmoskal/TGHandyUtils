from __future__ import annotations

import json

import pytest

from ai_workflow_tools.cli_agents.usage import (
    ClaudeEnvelopeAccumulator,
    CodexUsageAccumulator,
    parse_cli_process_output,
)
from ai_workflow_tools.cli_agents.models import CliFlavor

pytestmark = pytest.mark.unit


def _turn(**usage):
    return json.dumps({"type": "turn.completed", "usage": usage}).encode() + b"\n"


def test_codex_usage_accumulator_uses_final_cumulative_turn_across_chunks():
    first = _turn(
        input_tokens=10,
        cached_input_tokens=2,
        output_tokens=4,
        reasoning_output_tokens=1,
        total_tokens=14,
    )
    final = _turn(
        input_tokens=120,
        cached_input_tokens=20,
        output_tokens=30,
        reasoning_output_tokens=10,
        total_tokens=150,
    )
    payload = b"\xf0\x9f\x94\xa7 diagnostic\n" + first + b"not json\n" + final
    parser = CodexUsageAccumulator()
    for index in range(0, len(payload), 3):
        parser.feed(payload[index : index + 3])

    parsed = parser.finish()
    usage, error = parsed.normalized_usage, parsed.usage_error

    assert error is None
    assert usage is not None
    assert usage.uncached_input_tokens == 100
    assert usage.cache_read_input_tokens == 20
    assert usage.non_reasoning_output_tokens == 20
    assert usage.reasoning_output_tokens == 10
    assert usage.raw_total_tokens == 150


@pytest.mark.parametrize(
    "usage",
    [
        {
            "input_tokens": 2,
            "cached_input_tokens": 3,
            "output_tokens": 1,
            "reasoning_output_tokens": 0,
            "total_tokens": 3,
        },
        {
            "input_tokens": 2,
            "cached_input_tokens": 0,
            "output_tokens": 1,
            "reasoning_output_tokens": 2,
            "total_tokens": 3,
        },
        {
            "input_tokens": True,
            "cached_input_tokens": 0,
            "output_tokens": 1,
            "reasoning_output_tokens": 0,
            "total_tokens": 2,
        },
        {
            "input_tokens": 2,
            "cached_input_tokens": 0,
            "output_tokens": 1,
            "reasoning_output_tokens": 0,
            "total_tokens": 99,
        },
    ],
)
def test_codex_usage_accumulator_rejects_impossible_counters(usage):
    parser = CodexUsageAccumulator()
    parser.feed(_turn(**usage))

    parsed = parser.finish()
    normalized, error = parsed.normalized_usage, parsed.usage_error

    assert normalized is None
    assert error == "invalid_token_counters"
    assert parsed.usage_diagnostic is not None
    assert len(parsed.usage_diagnostic) <= 500
    assert "codex usage counters:" in parsed.usage_diagnostic


def test_codex_usage_diagnostic_keeps_only_counter_shape_and_never_envelope_secrets():
    parser = CodexUsageAccumulator()
    parser.feed(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": "not-an-int",
                    "output_tokens": 1,
                    "secret": "do-not-persist",
                    "nested": {"also": "secret"},
                },
                "provider_secret": "never-diagnostic",
            }
        ).encode()
        + b"\n"
    )

    parsed = parser.finish()

    assert parsed.usage_error == "invalid_token_counters"
    assert parsed.usage_diagnostic is not None
    assert '"input_tokens":"<str>"' in parsed.usage_diagnostic
    assert "do-not-persist" not in parsed.usage_diagnostic
    assert "also" not in parsed.usage_diagnostic
    assert "never-diagnostic" not in parsed.usage_diagnostic


def test_codex_usage_accumulator_bounds_unterminated_protocol_line():
    parser = CodexUsageAccumulator(max_partial_line_bytes=16)

    parser.feed(b'{"type":"turn.completed","usage":')

    parsed = parser.finish()
    normalized, error = parsed.normalized_usage, parsed.usage_error
    assert normalized is None
    assert error == "usage_event_malformed"
    assert parsed.usage_diagnostic == "codex turn.completed line exceeded 16 bytes"


def test_codex_usage_accumulator_rejects_completed_line_that_crosses_bound():
    parser = CodexUsageAccumulator(max_partial_line_bytes=16)
    parser.feed(b"X" * 12)
    parser.feed(b"Y" * 8 + b"\n")

    parsed = parser.finish()

    assert parsed.normalized_usage is None
    assert parsed.usage_error == "usage_event_malformed"
    assert parsed.usage_diagnostic == "codex turn.completed line exceeded 16 bytes"


def test_codex_usage_accumulator_recovers_after_oversized_completed_line():
    parser = CodexUsageAccumulator(max_partial_line_bytes=256)
    valid = _turn(
        input_tokens=2,
        cached_input_tokens=1,
        output_tokens=1,
        reasoning_output_tokens=0,
        total_tokens=3,
    )
    parser.feed(b"X" * 240)
    parser.feed(b"Y" * 32 + b"\n" + valid)

    parsed = parser.finish()

    assert parsed.usage_error is None
    assert parsed.normalized_usage is not None
    assert parsed.normalized_usage.uncached_input_tokens == 1


def test_codex_usage_accumulator_missing_completion_is_explicit_unknown():
    parser = CodexUsageAccumulator()
    parser.feed(b'{"type":"thread.started"}\nplain diagnostic\n')

    parsed = parser.finish()
    normalized, error = parsed.normalized_usage, parsed.usage_error

    assert normalized is None
    assert error == "usage_event_missing"


def test_codex_usage_protocol_never_reads_stderr_diagnostics():
    flavor = CliFlavor(
        name="codex",
        prompt_delivery="argv_last",
        result_source="result_file",
        base_argv=["codex", "exec"],
    )
    parsed = parse_cli_process_output(
        flavor,
        {
            "stdout": "",
            "stderr": _turn(
                input_tokens=999,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_output_tokens=0,
                total_tokens=1000,
            ).decode(),
            "result": "answer",
        },
    )

    assert parsed.text == "answer"
    assert parsed.normalized_usage is None
    assert parsed.usage_error == "usage_event_missing"


def test_codex_accumulator_discards_oversized_line_until_newline_then_recovers():
    parser = CodexUsageAccumulator(max_partial_line_bytes=256)
    parser.feed(b"X" * 300)
    parser.feed(b"still same oversized line\n")
    parser.feed(
        _turn(
            input_tokens=2,
            cached_input_tokens=1,
            output_tokens=1,
            reasoning_output_tokens=0,
            total_tokens=3,
        )
    )

    parsed = parser.finish()

    assert parsed.usage_error is None
    assert parsed.normalized_usage is not None
    assert parsed.normalized_usage.uncached_input_tokens == 1


def test_claude_envelope_accumulator_preserves_usage_and_provider_cost_across_chunks():
    payload = json.dumps(
        {
            "result": "done",
            "total_cost_usd": 0.123,
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 5,
            },
        }
    ).encode()
    parser = ClaudeEnvelopeAccumulator()
    for index in range(0, len(payload), 5):
        parser.feed(payload[index : index + 5])

    parsed = parser.finish()

    assert parsed.text == "done"
    assert parsed.provider_reported_notional_usd == 0.123
    assert parsed.normalized_usage is not None
    assert parsed.normalized_usage.uncached_input_tokens == 11
    assert parsed.normalized_usage.cache_read_input_tokens == 3
    assert parsed.normalized_usage.cache_creation_input_tokens == 5


def test_claude_invalid_usage_diagnostic_is_bounded_and_excludes_unrelated_envelope():
    payload = json.dumps(
        {
            "result": "done",
            "provider_secret": "never-diagnostic",
            "usage": {
                "input_tokens": {"nested": "secret"},
                "output_tokens": 7,
                "secret": "do-not-persist",
            },
        }
    ).encode()
    parser = ClaudeEnvelopeAccumulator()
    parser.feed(payload)

    parsed = parser.finish()

    assert parsed.usage_error == "invalid_token_counters"
    assert parsed.usage_diagnostic is not None
    assert len(parsed.usage_diagnostic) <= 500
    assert '"input_tokens":"<dict>"' in parsed.usage_diagnostic
    assert "never-diagnostic" not in parsed.usage_diagnostic
    assert "do-not-persist" not in parsed.usage_diagnostic
    assert "nested" not in parsed.usage_diagnostic
