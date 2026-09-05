from __future__ import annotations

import json

import pytest

from ai_workflow_tools.cli_agents.usage import (
    _BoundedJsonLineAccumulator,
    ClaudeEnvelopeAccumulator,
    CodexProtocolState,
    CodexUsageAccumulator,
    ParsedCliOutput,
    parse_cli_process_output,
)
from ai_workflow_tools.cli_agents.models import CliFlavor

pytestmark = pytest.mark.unit


def _turn(**usage):
    return json.dumps({"type": "turn.completed", "usage": usage}).encode() + b"\n"


def test_json_line_framer_consumes_each_complete_line_once():
    class NonIdempotentProbe(_BoundedJsonLineAccumulator):
        def __init__(self):
            super().__init__(max_partial_line_bytes=64)
            self.lines = []

        def _consume_line(self, raw):
            self.lines.append(raw)

        def _record_oversized_line(self):
            raise AssertionError("fixture line unexpectedly oversized")

    parser = NonIdempotentProbe()
    parser.feed(b'{"sequence":1}\n')

    assert parser.lines == [b'{"sequence":1}']


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
    assert parsed.provider_failed is False
    assert parsed.codex_protocol == CodexProtocolState(subtype="turn.completed")


def test_codex_error_is_a_non_terminal_notice_until_the_process_fails():
    parser = CodexUsageAccumulator()
    encoded = json.dumps(
        {"type": "error", "message": "Reconnecting... 2/5"}
    ).encode() + b"\n"
    for index in range(0, len(encoded), 3):
        parser.feed(encoded[index : index + 3])

    parsed = parser.finish()

    assert parsed.provider_failed is False
    assert parsed.codex_protocol is not None
    assert parsed.codex_protocol.subtype == "error"
    assert parsed.failure_diagnostic(process_failed=False) is None
    diagnostic = parsed.failure_diagnostic(process_failed=True)
    assert diagnostic is not None
    assert diagnostic.subtype == "error"
    assert diagnostic.category == "connectivity"
    assert diagnostic.message == "Codex connection failed"
    assert parsed.normalized_usage is None
    assert parsed.input_tokens == 0
    assert parsed.output_tokens == 0


def test_codex_turn_failed_is_terminal_and_classified():
    parser = CodexUsageAccumulator()
    parser.feed(
        b'{"type":"turn.failed","error":{"message":"model access denied"}}\n'
    )

    parsed = parser.finish()

    assert parsed.provider_failed is True
    diagnostic = parsed.failure_diagnostic(process_failed=False)
    assert diagnostic is not None
    assert diagnostic.subtype == "turn.failed"
    assert diagnostic.category == "model_access"


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("authentication failed TOP SECRET", "authentication"),
        ("model not found TOP SECRET", "model_access"),
        ("rate limit exceeded TOP SECRET", "quota_capacity_rate_limit"),
        ("network unavailable TOP SECRET", "connectivity"),
        ("content policy violation TOP SECRET", "policy"),
        ("invalid max_capacity parameter", "unknown"),
        ("provider rejected opaque request TOP SECRET", "unknown"),
    ],
)
def test_codex_failure_categories_are_closed(message, category):
    parser = CodexUsageAccumulator()
    parser.feed(
        json.dumps({"type": "turn.failed", "error": {"message": message}}).encode()
        + b"\n"
    )

    diagnostic = parser.finish().failure_diagnostic(process_failed=False)

    assert diagnostic is not None
    assert diagnostic.category == category
    assert "TOP SECRET" not in diagnostic.message


@pytest.mark.parametrize(
    ("event", "terminal", "category"),
    [
        ({"type": "error", "message": {"secret": "value"}}, False, "malformed"),
        ({"type": "turn.failed", "error": {}}, True, "malformed"),
        ({"type": "error", "message": "x" * 501}, False, "oversized"),
        (
            {"type": "turn.failed", "error": {"message": "x" * 501}},
            True,
            "oversized",
        ),
    ],
)
def test_codex_failure_diagnostic_refuses_malformed_or_oversized_values(
    event, terminal, category
):
    parser = CodexUsageAccumulator()
    parser.feed(json.dumps(event).encode() + b"\n")

    parsed = parser.finish()
    diagnostic = parsed.failure_diagnostic(process_failed=True)

    assert parsed.provider_failed is terminal
    assert diagnostic is not None
    assert diagnostic.category == category
    assert len(diagnostic.message.encode("utf-8")) < 100
    assert "secret" not in diagnostic.message
    assert "x" * 20 not in diagnostic.message


def test_codex_failure_diagnostic_is_closed_and_excludes_all_provider_prose():
    parser = CodexUsageAccumulator()
    secret = 'TOP SECRET MARKER {"prompt":"explain the rate limit"}'
    parser.feed(
        json.dumps(
            {
                "type": "error",
                "message": secret,
                "prompt": "SIBLING PRIVATE PROMPT",
                "credentials": {"api_key": "SIBLING-SECRET"},
                "arbitrary": {"payload": ["must", "not", "persist"]},
            }
        ).encode()
        + b"\n"
    )

    diagnostic = parser.finish().failure_diagnostic(process_failed=True)

    assert diagnostic is not None
    assert diagnostic.category == "unknown"
    published = json.dumps(diagnostic.metadata(), sort_keys=True)
    assert published == (
        '{"category": "unknown", "message": "Codex provider failure was not '
        'classified", "provider": "codex", "subtype": "error"}'
    )
    assert secret not in published
    assert "SIBLING" not in published


def test_codex_malformed_json_has_no_cause_until_the_process_fails():
    parser = CodexUsageAccumulator()
    parser.feed(b'{"type":"error","message":\n')

    parsed = parser.finish()

    assert parsed.provider_failed is False
    assert parsed.failure_diagnostic(process_failed=False) is None
    diagnostic = parsed.failure_diagnostic(process_failed=True)
    assert diagnostic is not None
    assert diagnostic.subtype == "process"
    assert diagnostic.category == "unknown"


def test_codex_reconnect_notices_are_cleared_by_later_completed_turn():
    parser = CodexUsageAccumulator()
    parser.feed(b'{"type":"error","message":"Reconnecting... 1/5"}\n')
    parser.feed(b'{"type":"error","message":"Reconnecting... 2/5"}\n')
    parser.feed(
        _turn(
            input_tokens=12,
            cached_input_tokens=2,
            output_tokens=3,
            reasoning_output_tokens=1,
            total_tokens=15,
        )
    )

    parsed = parser.finish()

    assert parsed.provider_failed is False
    assert parsed.codex_protocol == CodexProtocolState(subtype="turn.completed")
    assert parsed.failure_diagnostic(process_failed=False) is None
    assert parsed.normalized_usage is not None
    assert parsed.input_tokens == 12
    assert parsed.output_tokens == 3
    assert parsed.usage_error is None


def test_codex_generic_terminal_failure_retains_first_informative_notice():
    parser = CodexUsageAccumulator()
    parser.feed(b'{"type":"error","message":"quota exceeded"}\n')
    parser.feed(b'{"type":"error","message":"Reconnecting... 1/5"}\n')
    parser.feed(b'{"type":"turn.failed","error":{"message":"turn failed"}}\n')

    diagnostic = parser.finish().failure_diagnostic(process_failed=False)

    assert diagnostic is not None
    assert diagnostic.subtype == "turn.failed"
    assert diagnostic.category == "quota_capacity_rate_limit"
    assert diagnostic.message == "Codex quota, rate, or capacity limit reached"


def test_codex_informative_terminal_failure_overrides_earlier_notice():
    parser = CodexUsageAccumulator()
    parser.feed(b'{"type":"error","message":"Reconnecting... 1/5"}\n')
    parser.feed(
        b'{"type":"turn.failed","error":{"message":"authentication failed"}}\n'
    )

    diagnostic = parser.finish().failure_diagnostic(process_failed=False)

    assert diagnostic is not None
    assert diagnostic.subtype == "turn.failed"
    assert diagnostic.category == "authentication"


def test_codex_protocol_state_rejects_contradictory_direct_construction():
    notice = CodexProtocolState(subtype="error", category="connectivity")
    terminal = CodexProtocolState(subtype="turn.failed", category="unknown")
    parsed_terminal = ParsedCliOutput(text="", codex_protocol=terminal)

    assert parsed_terminal.provider_failed is True
    assert parsed_terminal.failure_diagnostic(process_failed=False) is terminal
    with pytest.raises(ValueError, match="completed"):
        CodexProtocolState(subtype="turn.completed", category=notice.category)
    with pytest.raises(ValueError, match="inconsistent"):
        CodexProtocolState(subtype="turn.failed")
    with pytest.raises(ValueError, match="inconsistent"):
        CodexProtocolState(subtype="none", category=terminal.category)
    with pytest.raises(ValueError, match="requires provider_error"):
        ParsedCliOutput(text="", provider_error_subtype="error")
    with pytest.raises(ValueError, match="Claude"):
        ParsedCliOutput(
            text="",
            provider_error=True,
            provider_error_subtype="error",
            codex_protocol=CodexProtocolState(subtype="turn.completed"),
        )


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
        prompt_delivery="stdin",
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


@pytest.mark.parametrize(
    ("is_error", "subtype"),
    [
        (True, "error_during_execution"),
        (False, "error_max_budget_usd"),
    ],
)
def test_claude_error_envelope_is_typed_even_when_process_exit_is_zero(
    is_error, subtype
):
    parsed = parse_cli_process_output(
        CliFlavor(
            name="claude",
            prompt_delivery="stdin",
            result_source="stdout_json_envelope",
            base_argv=["claude", "-p"],
        ),
        {
            "stdout": json.dumps(
                {
                    "type": "result",
                    "is_error": is_error,
                    "subtype": subtype,
                    "result": "",
                    "total_cost_usd": 0.25,
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                }
            )
        },
    )

    assert parsed.provider_error is True
    assert parsed.provider_error_subtype == subtype
    assert parsed.provider_reported_notional_usd == 0.25
    assert parsed.normalized_usage is not None


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
