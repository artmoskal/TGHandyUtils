"""Standalone viewer consumes engine observability records."""

from __future__ import annotations

import json

import pytest

from ai_workflow_engine import (
    ObservationDetail,
    WorkflowBuilder,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
)
from ai_workflow_viewer import JsonlObservationViewer

pytestmark = pytest.mark.unit


def _write_bundle(
    base,
    run_id: str,
    definition,
    *,
    trace_events=None,
    details=None,
    usage_events=None,
):
    run_path = base / run_id
    run_path.mkdir(parents=True)
    (run_path / "definition.json").write_text(definition.model_dump_json(), encoding="utf-8")
    (run_path / "trace.jsonl").write_text(
        "\n".join(event.model_dump_json() for event in (trace_events or [])) + "\n",
        encoding="utf-8",
    )
    (run_path / "details.jsonl").write_text(
        "\n".join(detail.model_dump_json(by_alias=True) for detail in (details or [])) + "\n",
        encoding="utf-8",
    )
    (run_path / "usage.jsonl").write_text(
        "\n".join(event.model_dump_json() for event in (usage_events or [])) + "\n",
        encoding="utf-8",
    )
    (run_path / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "workflow_id": definition.workflow_id,
                "timestamp": "2026-06-21T20:00:00Z",
                "definition_path": "definition.json",
                "trace_path": "trace.jsonl",
                "detail_path": "details.jsonl",
                "usage_path": "usage.jsonl",
            }
        ),
        encoding="utf-8",
    )
    return run_path


def test_jsonl_observation_viewer_renders_html_from_public_contracts(tmp_path):
    definition = WorkflowBuilder("viewer").step("plan").step("render").build()
    event = WorkflowTraceEvent(
        node="plan",
        decision="llm:prompt",
        phase="llm:request",
        detail_refs=["detail-1"],
        run_id="run-1",
        sequence=1,
    )
    detail = ObservationDetail(
        detail_id="detail-1",
        event_id=event.event_id,
        kind="rendered_prompt",
        content_type="text/plain",
        digest="digest",
        run_id="run-1",
        sequence=2,
    )
    run_path = _write_bundle(
        tmp_path,
        "run-1",
        definition,
        trace_events=[
            event,
            WorkflowTraceEvent(node="render", decision="accepted", run_id="run-1", sequence=3),
        ],
        usage_events=[WorkflowUsageEvent(node="plan", total_tokens=9, estimated_usd=0.01, run_id="run-1", sequence=4)],
        details=[detail],
    )

    viewer = JsonlObservationViewer.from_run_bundle(run_path, title="Viewer test")

    html = viewer.html()
    assert "Viewer test" in html
    assert "Investigation Graph" in html
    assert 'data-node-id="plan"' in html
    assert "flowchart TD" in html
    assert "9 tok" in html
    assert "metered $0.0100" in html
    assert "digest" in html
    assert "<dialog" in html
    assert "Copy raw" in html
    assert [record["type"] for record in viewer.event_records()] == ["trace", "detail", "trace", "usage"]


def test_jsonl_observation_viewer_lists_runs_for_multi_run_bundle_sources(tmp_path):
    definition = WorkflowBuilder("viewer").step("plan").build()
    run_1_event = WorkflowTraceEvent(
        node="plan",
        decision="accepted",
        detail_refs=["detail-1"],
        run_id="run-1",
        sequence=1,
    )
    run_2_event = WorkflowTraceEvent(
        node="plan",
        decision="accepted",
        detail_refs=["detail-2"],
        run_id="run-2",
        sequence=1,
    )
    _write_bundle(
        tmp_path,
        "run-1",
        definition,
        trace_events=[run_1_event],
        details=[
            ObservationDetail(
                detail_id="detail-1",
                event_id=run_1_event.event_id,
                run_id="run-1",
                kind="rendered_prompt",
                digest="run-1-digest",
                sequence=2,
            ),
        ],
    )
    _write_bundle(
        tmp_path,
        "run-2",
        definition,
        trace_events=[run_2_event],
        details=[
            ObservationDetail(
                detail_id="detail-2",
                event_id=run_2_event.event_id,
                run_id="run-2",
                kind="rendered_prompt",
                digest="run-2-digest",
                sequence=2,
            ),
        ],
    )

    viewer = JsonlObservationViewer.from_run_bundle(tmp_path)
    index = viewer.html()
    assert "Workflow observations" in index
    assert "run-1" in index
    assert "run-2" in index
    assert "?run_id=run-2" in index

    selected = JsonlObservationViewer.from_run_bundle(tmp_path, run_id="run-2")
    html = selected.html()

    assert "run-2-digest" in html
    assert "run-1-digest" not in html
    assert [record["record"]["run_id"] for record in selected.event_records()] == ["run-2", "run-2"]
