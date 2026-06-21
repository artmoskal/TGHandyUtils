"""Runtime observability contract: trace enrichment, usage/detail sinks, projector, HTML."""

from __future__ import annotations

import json

import pytest

from ai_workflow_engine import (
    InMemoryTraceSink,
    InMemoryUsageSink,
    ObservationDetail,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowGoal,
    WorkflowRunner,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    build_observation_graph,
    observation_graph_to_html,
    render_runtime_timeline,
)
from ai_workflow_engine.engine import InMemoryDetailSink, JsonlDetailSink
from ai_workflow_engine.usage import record_usage_event

pytestmark = pytest.mark.unit


def test_observation_detail_rejects_inline_raw_bytes():
    with pytest.raises(ValueError, match="raw bytes"):
        ObservationDetail(
            event_id="event-1",
            kind="tool_payload",
            redaction_state="none",
            content_type="application/json",
            json={"image": b"raw"},
        )


def test_detail_jsonl_sink_round_trips_alias_shape(tmp_path):
    detail = ObservationDetail(
        event_id="event-1",
        kind="rendered_prompt",
        redaction_state="digest_only",
        content_type="text/plain",
        digest="abc123",
    )
    memory = InMemoryDetailSink()
    memory.record(detail)
    path = tmp_path / "details.jsonl"
    JsonlDetailSink(path).record(detail)

    assert memory.details == [detail]
    row = json.loads(path.read_text().splitlines()[0])
    assert row["event_id"] == "event-1"
    assert row["json"] is None


async def test_engine_trace_events_are_enriched_with_distinct_run_ids():
    trace = InMemoryTraceSink()
    engine = (
        WorkflowEngineBuilder()
        .with_trace_sink(trace)
        .register_capability("step", lambda _ctx, payload: {"value": payload["value"]}, kind="deterministic")
        .register_workflow(WorkflowBuilder("run_ids").step("step").build())
        .build()
    )

    first = await engine.run("run_ids", {"value": 1})
    second = await engine.run("run_ids", {"value": 2})

    first_ids = {event.run_id for event in first.trace if event.run_id}
    second_ids = {event.run_id for event in second.trace if event.run_id}
    assert first_ids
    assert second_ids
    assert len(second_ids) == 2  # shared in-memory sink intentionally contains both runs
    assert len(first_ids | second_ids) == 2
    assert all(event.phase for event in second.trace if event.node == "step")


async def test_workflow_runner_streams_usage_to_usage_sink():
    class UsageGraph:
        async def ainvoke(self, state):
            record_usage_event(
                WorkflowUsageEvent(node="llm", operation="chat", total_tokens=11, estimated_usd=0.02)
            )
            return dict(state)

    sink = InMemoryUsageSink()
    goal = WorkflowGoal(workflow_type="usage", objective="record usage")
    result = await WorkflowRunner(usage_sink=sink).run(UsageGraph(), {}, goal=goal)

    assert len(sink.events) == 1
    assert sink.events[0].metadata["workflow_id"] == result["workflow_context"].workflow_id
    assert sink.events[0].metadata["workflow_type"] == "usage"


def test_observation_graph_projects_trace_usage_details_and_renders_html():
    definition = WorkflowBuilder("observe").step("plan").step("render").build()
    prompt_event = WorkflowTraceEvent(
        node="plan",
        decision="llm:prompt",
        phase="llm:request",
        detail_refs=["detail-1"],
        run_id="run-1",
    )
    detail = ObservationDetail(
        detail_id="detail-1",
        event_id=prompt_event.event_id,
        kind="rendered_prompt",
        redaction_state="digest_only",
        content_type="text/plain",
        digest="abc123",
    )
    graph = build_observation_graph(
        definition,
        [
            prompt_event,
            WorkflowTraceEvent(node="plan", decision="accepted", elapsed_ms=7, run_id="run-1"),
            WorkflowTraceEvent(node="render", decision="accepted", elapsed_ms=5, run_id="run-1"),
        ],
        [WorkflowUsageEvent(node="plan", operation="chat", total_tokens=42, estimated_usd=0.01)],
        [detail],
    )

    assert graph.run_id == "run-1"
    assert graph.nodes["plan"].status == "completed"
    assert graph.nodes["plan"].total_tokens == 42
    assert graph.nodes["plan"].detail_refs == ["detail-1"]
    assert "llm:request" in render_runtime_timeline(graph)
    html = observation_graph_to_html(definition, graph)
    assert "flowchart TD" in html
    assert "42 tok" in html
    assert "abc123" in html
