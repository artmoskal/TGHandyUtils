"""G2 — pre_parse hook + stock weak-model cleaners (AC-G2)."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ai_workflow_engine import (
    WEAK_MODEL_CLEANER,
    StructuredLLMNode,
    StructuredOutputError,
    compose_cleaners,
    extract_fenced_json,
    extract_first_json_object,
    strip_think_tags,
)

pytestmark = pytest.mark.unit


class CountingFakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return SimpleNamespace(content=self.responses.pop(0))


class Answer(BaseModel):
    value: str


def _node(llm, **kwargs) -> StructuredLLMNode:
    return StructuredLLMNode(
        name="g2_node",
        config=object(),
        output_model=Answer,
        prompt_template="Answer about {topic}.",
        input_variables=["topic"],
        llm=llm,
        **kwargs,
    )


# ---------------------------------------------------------------- cleaner units

def test_strip_think_tags_removes_closed_blocks():
    assert strip_think_tags('<think>reasoning {not json}</think>{"value": "x"}') == '{"value": "x"}'
    assert strip_think_tags('<thinking>multi\nline</thinking>\n{"value": "x"}') == '{"value": "x"}'


def test_strip_think_tags_handles_unclosed_tag():
    assert strip_think_tags('{"value": "x"}<think>and then it rambles forever') == '{"value": "x"}'


def test_strip_think_tags_keeps_text_without_tags():
    assert strip_think_tags('{"value": "x"}') == '{"value": "x"}'


def test_extract_fenced_json_unwraps_json_fence():
    assert extract_fenced_json('Here you go:\n```json\n{"value": "x"}\n```\nEnjoy!') == '{"value": "x"}'
    assert extract_fenced_json('```\n{"value": "x"}\n```') == '{"value": "x"}'


def test_extract_fenced_json_passthrough_without_fence():
    assert extract_fenced_json('{"value": "x"}') == '{"value": "x"}'


def test_extract_first_json_object_is_string_aware():
    # Braces inside JSON strings must not break balancing.
    text = 'chatter {"value": "has } brace and { brace"} trailing'
    assert extract_first_json_object(text) == '{"value": "has } brace and { brace"}'


def test_extract_first_json_object_picks_first_of_multiple():
    text = '{"value": "first"} {"value": "second"}'
    assert extract_first_json_object(text) == '{"value": "first"}'


def test_extract_first_json_object_supports_arrays_and_no_json():
    assert extract_first_json_object('noise [1, 2, {"a": 1}] tail') == '[1, 2, {"a": 1}]'
    assert extract_first_json_object("no json here at all") == "no json here at all"


def test_think_tag_containing_a_fence_then_real_payload():
    text = '<think>I could write ```json\n{"value": "fake"}\n``` but…</think>\n{"value": "real"}'
    assert WEAK_MODEL_CLEANER(text) == '{"value": "real"}'


def test_compose_cleaners_applies_left_to_right():
    composed = compose_cleaners(lambda t: t.replace("a", "b"), lambda t: t.replace("b", "c"))
    assert composed("aaa") == "ccc"


# ---------------------------------------------------------------- node integration

@pytest.mark.parametrize(
    "wrapped",
    [
        '<think>let me reason about this</think>{"value": "ok"}',
        'Sure! Here is your JSON:\n```json\n{"value": "ok"}\n```',
        'Of course, happy to help. {"value": "ok"} Let me know if you need more!',
        '<think>hmm</think>\n```json\n{"value": "ok"}\n```\nhope that helps',
    ],
)
async def test_weak_model_output_parses_without_repair_call(wrapped):
    llm = CountingFakeLLM([wrapped])
    node = _node(llm, pre_parse=WEAK_MODEL_CLEANER)

    result = await node.run({"topic": "x"})

    assert result.value == "ok"
    assert llm.calls == 1  # AC-G2: no repair LLM call spent


async def test_clean_json_behaves_identically_without_pre_parse_noise(caplog):
    llm = CountingFakeLLM(['{"value": "ok"}'])
    node = _node(llm, pre_parse=WEAK_MODEL_CLEANER)

    with caplog.at_level("INFO"):
        result = await node.run({"topic": "x"})

    assert result.value == "ok"
    assert llm.calls == 1
    assert not any("structured_llm_node_pre_parse" in r.message for r in caplog.records)


async def test_pre_parse_change_is_observably_logged(caplog):
    llm = CountingFakeLLM(['```json\n{"value": "ok"}\n```'])
    node = _node(llm, pre_parse=WEAK_MODEL_CLEANER)

    with caplog.at_level("INFO"):
        await node.run({"topic": "x"})

    hits = [r.message for r in caplog.records if "structured_llm_node_pre_parse" in r.message]
    assert len(hits) == 1
    assert "sha12" in hits[0] and "length" in hits[0]


async def test_repair_attempt_output_also_passes_through_pre_parse():
    llm = CountingFakeLLM(["complete garbage, no json", '```json\n{"value": "fixed"}\n```'])
    node = _node(llm, pre_parse=WEAK_MODEL_CLEANER)

    result = await node.run({"topic": "x"})

    assert result.value == "fixed"
    assert llm.calls == 2  # repair round used, its fenced output cleaned and parsed


async def test_max_repair_rounds_two_allows_third_attempt():
    llm = CountingFakeLLM(["garbage one", "garbage two", '{"value": "third"}'])
    node = _node(llm, max_repair_rounds=2)

    result = await node.run({"topic": "x"})

    assert result.value == "third"
    assert llm.calls == 3


async def test_default_repair_budget_unchanged():
    llm = CountingFakeLLM(["garbage one", "garbage two", '{"value": "never reached"}'])
    node = _node(llm)  # default max_repair_rounds=1 -> 2 attempts total, like today

    with pytest.raises(StructuredOutputError):
        await node.run({"topic": "x"})
    assert llm.calls == 2
