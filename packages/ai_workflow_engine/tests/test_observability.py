"""Runtime observability contract: trace enrichment, usage/detail sinks, projector, HTML."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

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
)
from ai_workflow_engine.engine import InMemoryDetailSink, JsonlDetailSink
from ai_workflow_engine.observability_capture import byte_free
from ai_workflow_engine.observation_bundle import open_observation_run_bundle, prune_observation_bundles
from ai_workflow_engine.usage import invoke_metered_chat
from ai_workflow_engine.usage_events import record_usage_event
from ai_workflow_viewer import build_observation_graph, observation_graph_to_html, render_runtime_timeline

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


def test_in_memory_detail_sink_clear_drops_buffered_details():
    memory = InMemoryDetailSink()
    memory.record(ObservationDetail(event_id="event-1", kind="tool_payload"))

    memory.clear()

    assert memory.details == []


def test_observation_bundle_prune_skips_unfinalized_runs(tmp_path):
    definition = WorkflowBuilder("bundle_prune").step("node").build()
    old = open_observation_run_bundle(tmp_path, "old")
    old.finalize(definition, status="completed")
    new = open_observation_run_bundle(tmp_path, "new")
    new.finalize(definition, status="completed")
    active = open_observation_run_bundle(tmp_path, "active")

    prune_observation_bundles(tmp_path, 1)

    assert active.path.exists()
    finalized = [
        path for path in tmp_path.iterdir()
        if path.is_dir() and (path / "meta.json").exists()
    ]
    assert len(finalized) == 1


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
    assert len(first_ids) == 1
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert len({event.run_id for event in trace.events if event.run_id}) == 2
    assert all(event.phase for event in second.trace if event.node == "step")


async def test_engine_detail_sink_without_full_capture_does_not_create_pseudo_details():
    trace = InMemoryTraceSink()
    details = InMemoryDetailSink()
    engine = (
        WorkflowEngineBuilder()
        .with_trace_sink(trace)
        .with_detail_sink(details)
        .register_capability("step", lambda _ctx, payload: {"value": payload["value"]}, kind="tool")
        .register_workflow(WorkflowBuilder("tool_details").step("step").build())
        .build()
    )

    result = await engine.run("tool_details", {"value": "SECRET"})

    assert result.status == "completed"
    assert details.details == []
    tool_events = [event for event in result.trace if event.phase in {"tool:request", "tool:result"}]
    assert [event.detail_refs for event in tool_events] == [[], []]
    assert "SECRET" not in json.dumps([event.model_dump() for event in tool_events], default=str)


async def test_engine_detail_text_capture_is_explicit():
    details = InMemoryDetailSink()
    engine = (
        WorkflowEngineBuilder()
        .with_detail_sink(details)
        .with_detail_text_capture()
        .register_capability("step", lambda _ctx, payload: {"value": payload["value"]}, kind="tool")
        .register_workflow(WorkflowBuilder("tool_detail_text").step("step").build())
        .build()
    )

    result = await engine.run("tool_detail_text", {"value": "VISIBLE"})

    assert result.status == "completed"
    assert {detail.redaction_state for detail in details.details} == {"none"}
    assert any("VISIBLE" in (detail.text or "") for detail in details.details)


async def test_engine_detail_text_capture_is_runtime_wide_not_capability_opt_in():
    details = InMemoryDetailSink()
    engine = (
        WorkflowEngineBuilder()
        .with_detail_sink(details)
        .with_detail_text_capture()
        .register_capability("step", lambda _ctx, payload: {"value": payload["value"]}, kind="tool")
        .register_workflow(WorkflowBuilder("tool_detail_digest").step("step").build())
        .build()
    )

    result = await engine.run("tool_detail_digest", {"value": "HIDDEN"})

    assert result.status == "completed"
    assert details.details
    assert all(detail.redaction_state == "none" for detail in details.details)
    assert "HIDDEN" in json.dumps([detail.model_dump(by_alias=True) for detail in details.details], default=str)


async def test_engine_detail_text_capture_records_external_capabilities_when_enabled():
    details = InMemoryDetailSink()
    engine = (
        WorkflowEngineBuilder()
        .with_detail_sink(details)
        .with_detail_text_capture()
        .register_capability(
            "send_secret",
            lambda _ctx, payload: {"sent": payload["token"]},
            kind="external",
        )
        .register_workflow(WorkflowBuilder("secret_detail_digest").step("send_secret").build())
        .build()
    )

    result = await engine.run("secret_detail_digest", {"token": "SECRET_TOKEN"})

    assert result.status == "completed"
    assert details.details
    assert all(detail.redaction_state == "none" for detail in details.details)
    assert "SECRET_TOKEN" in json.dumps([detail.model_dump(by_alias=True) for detail in details.details], default=str)


def test_byte_free_redacts_data_uri_and_base64_like_strings():
    encoded = "a" * 1024
    payload = byte_free(
        {
            "image_b64": encoded,
            "uri": "data:image/png;base64," + encoded,
            "ordinary": "not base64 text",
        }
    )

    assert payload["image_b64"]["type"] == "encoded_binary_string"
    assert payload["uri"]["type"] == "data_uri"
    assert payload["ordinary"] == "not base64 text"
    assert encoded not in json.dumps(payload)


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
    assert sink.events[0].metadata["run_id"] == result["workflow_context"].workflow_id
    assert sink.events[0].metadata["workflow_id"] == result["workflow_context"].workflow_id
    assert sink.events[0].metadata["workflow_type"] == "usage"


def test_observation_graph_projects_trace_usage_details_and_renders_html():
    definition = (
        WorkflowBuilder("observe")
        .step("plan", title="Plan cards", description="Decide the output shape.")
        .step("render", title="Render cards", description="Materialize the final artifact.")
        .build()
    )
    prompt_event = WorkflowTraceEvent(
        node="plan",
        timestamp="2026-06-21T20:00:01Z",
        decision="llm:prompt",
        phase="llm:request",
        detail_refs=["detail-1"],
        run_id="run-1",
    )
    detail = ObservationDetail(
        detail_id="detail-1",
        event_id=prompt_event.event_id,
        kind="rendered_prompt",
        redaction_state="none",
        content_type="text/plain",
        text="full prompt text",
        json={"prompt": "full prompt text"},
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
    assert graph.nodes["plan"].metered_usd == 0.01
    assert graph.nodes["plan"].notional_usd is None
    assert graph.nodes["plan"].detail_refs == ["detail-1"]
    assert graph.timeline[0].timestamp == "2026-06-21T20:00:01Z"
    assert "llm:request" in render_runtime_timeline(graph)
    assert "20:00:01Z" in render_runtime_timeline(graph)
    html = observation_graph_to_html(definition, graph)
    assert "Investigation Graph" in html
    assert "Plan cards" in html
    assert "Decide the output shape." in html
    assert 'data-node-id="plan"' in html
    assert 'id="node-inspector"' in html
    assert "Overview" in html
    assert "Investigate" in html
    assert "Raw Details" in html
    assert "flowchart TD" in html
    assert "42 tok" in html
    assert "metered $0.0100" in html
    assert "abc123" in html
    assert "full prompt text" in html
    assert "20:00:01Z" in html


async def test_invoke_metered_chat_records_prompt_response_details_for_direct_calls():
    details = InMemoryDetailSink()

    class DirectLLM:
        def invoke(self, messages):
            return SimpleNamespace(
                content="rendered answer",
                usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                response_metadata={"model_name": "direct-unit"},
            )

    async def render(ctx, payload):
        output = invoke_metered_chat(
            DirectLLM(),
            [HumanMessage(content="render prompt input")],
            node="render",
            model="direct-unit",
            metadata={"producer": "unit"},
            config=SimpleNamespace(WORKFLOW_USAGE_TRACKING_ENABLED=True),
        )
        return {"text": output.content}

    engine = (
        WorkflowEngineBuilder()
        .with_detail_sink(details)
        .with_detail_text_capture()
        .register_capability("render", render, kind="llm")
        .register_workflow(WorkflowBuilder("direct_metered").step("render").build())
        .build()
    )

    result = await engine.run("direct_metered", {})

    assert result.status == "completed"
    llm_events = [
        event for event in result.trace if event.node == "render" and event.phase in {"llm:request", "llm:response"}
    ]
    assert [event.phase for event in llm_events] == ["llm:request", "llm:response"]
    assert all(event.detail_refs for event in llm_events)
    detail_text = "\n".join(detail.text or "" for detail in details.details)
    assert "render prompt input" in detail_text
    assert "rendered answer" in detail_text


def test_observation_graph_keeps_metered_and_notional_costs_separate():
    definition = WorkflowBuilder("cost").step("llm").build()
    graph = build_observation_graph(
        definition,
        [],
        [
            WorkflowUsageEvent(node="llm", operation="chat", total_tokens=10, estimated_usd=0.25),
            WorkflowUsageEvent(
                node="llm",
                operation="chat",
                cost_class="subscription_notional",
                total_tokens=20,
                estimated_usd=99.0,
                notional_usd=0.42,
                metadata={"run_id": "run-cost"},
            ),
        ],
    )

    node = graph.nodes["llm"]
    assert graph.run_id == "run-cost"
    assert node.total_tokens == 30
    assert node.metered_usd == 0.25
    assert node.notional_usd == 0.42
    html = observation_graph_to_html(definition, graph)
    assert "metered $0.2500" in html
    assert "notional $0.4200" in html
    assert "$99.0000" not in html


def test_observation_graph_filters_selected_run_and_referenced_details():
    definition = WorkflowBuilder("observe_runs").step("plan").build()
    run_1_event = WorkflowTraceEvent(
        node="plan",
        decision="llm:prompt",
        phase="llm:request",
        detail_refs=["detail-1"],
        run_id="run-1",
    )
    run_2_event = WorkflowTraceEvent(
        node="plan",
        decision="llm:prompt",
        phase="llm:request",
        detail_refs=["detail-2"],
        run_id="run-2",
    )

    graph = build_observation_graph(
        definition,
        [run_1_event, run_2_event],
        [
            WorkflowUsageEvent(node="plan", total_tokens=3, metadata={"run_id": "run-1"}),
            WorkflowUsageEvent(node="plan", total_tokens=99, metadata={"run_id": "run-2"}),
        ],
        [
            ObservationDetail(
                detail_id="detail-1",
                event_id=run_1_event.event_id,
                run_id="run-1",
                kind="rendered_prompt",
                digest="run-1-digest",
            ),
            ObservationDetail(
                detail_id="detail-2",
                event_id=run_2_event.event_id,
                run_id="run-2",
                kind="rendered_prompt",
                digest="run-2-digest",
            ),
            ObservationDetail(
                detail_id="unreferenced",
                event_id="other",
                run_id="run-1",
                kind="rendered_prompt",
                digest="should-not-render",
            ),
        ],
        run_id="run-1",
    )

    assert graph.run_id == "run-1"
    assert [entry.event_id for entry in graph.timeline] == [run_1_event.event_id]
    assert graph.nodes["plan"].total_tokens == 3
    assert list(graph.details) == ["detail-1"]


def test_detail_projection_failure_still_records_bare_trace_event(monkeypatch, caplog):
    """Task 1.7: a detail-projection bug must cost the DETAIL only — pre-fix, the whole
    trace EVENT was dropped with it, blinding the run exactly when it misbehaved."""

    import logging

    from ai_workflow_engine import observability_capture as oc

    trace_sink = InMemoryTraceSink()
    detail_sink = InMemoryDetailSink()

    def _boom(**_kwargs):
        raise RuntimeError("detail projection bug")

    monkeypatch.setattr(oc, "_build_observation_detail", _boom)

    with caplog.at_level(logging.WARNING, logger="ai_workflow_engine.observability_capture"):
        result = oc.record_observation(
            trace_sink,
            detail_sink,
            node="obs_node",
            phase="llm:response",
            kind="llm_response",
            payload={"text": "hello"},
            capture_text=True,
        )

    assert result is not None, "the bare event must still be recorded"
    event, detail = result
    assert detail is None
    assert event.detail_refs == []
    assert [e.node for e in trace_sink.events] == ["obs_node"]
    assert len(detail_sink.details) == 0
    assert any("bare trace event" in r.getMessage() for r in caplog.records)
