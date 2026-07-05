"""G-S: the one-call façade is sugar over the real engine, never a bypass."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from ai_workflow_engine import WorkflowEngine, run_single_llm, run_single_step
from ai_workflow_engine.llm_protocol import LLMRequest, LLMResponse

pytestmark = pytest.mark.unit


class Verdict(BaseModel):
    label: str
    confidence: float


class _FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.requests: list[LLMRequest] = []

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(text=self.replies.pop(0), model="fake-model", input_tokens=3, output_tokens=2)


class _CaptureUsage:
    def __init__(self) -> None:
        self.events: list = []

    def record(self, event) -> None:
        self.events.append(event)


async def test_run_single_llm_returns_parsed_model_with_usage_and_trace():
    usage = _CaptureUsage()
    engine = WorkflowEngine(usage_sink=usage)
    llm = _FakeLLM([json.dumps({"label": "clean", "confidence": 0.9})])

    verdict = await run_single_llm(
        llm, "Classify: {text}", Verdict, {"text": "a mug"}, engine=engine
    )

    assert isinstance(verdict, Verdict)
    assert verdict.label == "clean"
    assert llm.requests, "the injected client must be the one that was called"
    # Proof this rode the REAL engine: usage metered + trace recorded, no side channel.
    assert usage.events, "single-call usage must land in the engine usage sink"
    assert engine.trace_sink.events, "single-call trace must land in the engine trace sink"


async def test_run_single_llm_ephemeral_engine_path_works_without_wiring():
    llm = _FakeLLM([json.dumps({"label": "ok", "confidence": 1.0})])

    verdict = await run_single_llm(llm, "Judge: {thing}", Verdict, {"thing": "x"})

    assert verdict.label == "ok"


async def test_run_single_llm_requires_a_client_no_default_model_ever():
    with pytest.raises(ValueError, match="no default model"):
        await run_single_llm(None, "Classify: {text}", Verdict, {"text": "x"})


async def test_run_single_llm_surfaces_unparseable_output_loudly():
    # Both the call and its single repair round return garbage -> loud failure,
    # never a silent fallback.
    llm = _FakeLLM(["not json at all", "still not json"])

    with pytest.raises(RuntimeError, match="single_llm"):
        await run_single_llm(llm, "Classify: {text}", Verdict, {"text": "x"})


async def test_run_single_llm_reuses_a_configured_engine_across_calls():
    usage = _CaptureUsage()
    engine = WorkflowEngine(usage_sink=usage)
    first = _FakeLLM([json.dumps({"label": "one", "confidence": 0.5})])
    second = _FakeLLM([json.dumps({"label": "two", "confidence": 0.6})])

    a = await run_single_llm(first, "Classify: {text}", Verdict, {"text": "a"}, engine=engine)
    b = await run_single_llm(second, "Classify: {text}", Verdict, {"text": "b"}, engine=engine)

    assert (a.label, b.label) == ("one", "two")
    assert len(usage.events) >= 2


async def test_run_single_step_accepts_plain_one_arg_callables():
    async def double(payload):
        return {"doubled": payload["n"] * 2}

    outcome = await run_single_step(double, {"n": 21})

    assert outcome == {"doubled": 42}


async def test_run_single_step_accepts_sync_callables_and_standard_capability_shape():
    def sync_upper(payload):
        return {"value": payload["value"].upper()}

    outcome = await run_single_step(sync_upper, {"value": "loud"})
    assert outcome == {"value": "LOUD"}

    async def standard(context, payload):
        assert context is not None
        return {"ctx": True}

    outcome = await run_single_step(standard, {})
    assert outcome == {"ctx": True}


async def test_run_single_step_propagates_failures_loudly():
    async def broken(_payload):
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError, match="single_step"):
        await run_single_step(broken, {})


async def test_run_single_llm_same_name_different_output_model_fails_loudly():
    class Other(BaseModel):
        value: int

    engine = WorkflowEngine()
    llm = _FakeLLM([json.dumps({"label": "one", "confidence": 0.5})])
    await run_single_llm(llm, "Classify: {text}", Verdict, {"text": "a"}, engine=engine)

    with pytest.raises(ValueError, match="distinct name="):
        await run_single_llm(
            _FakeLLM(["{}"]), "Classify: {text}", Other, {"text": "b"}, engine=engine
        )


async def test_run_single_llm_return_result_exposes_the_full_run_envelope():
    # Codex adjustment #2: usage/trace/budget proof must stay reachable on an
    # EPHEMERAL engine — return_result=True hands back the WorkflowRunResult.
    llm = _FakeLLM([json.dumps({"label": "ok", "confidence": 1.0})])

    result = await run_single_llm(
        llm, "Judge: {thing}", Verdict, {"thing": "x"}, return_result=True
    )

    assert result.status == "completed"
    assert isinstance(result.output, Verdict) and result.output.label == "ok"
    assert result.usage.events, "usage must be visible on the returned envelope"
    assert result.trace, "trace must be visible on the returned envelope"


async def test_run_single_step_return_result_exposes_the_full_run_envelope():
    async def double(payload):
        return {"doubled": payload["n"] * 2}

    result = await run_single_step(double, {"n": 4}, return_result=True)

    assert result.status == "completed"
    assert result.output == {"doubled": 8}
    assert result.trace
