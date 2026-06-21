"""Standalone viewer consumes engine observability records."""

from __future__ import annotations

import pytest

from ai_workflow_engine import (
    ObservationDetail,
    WorkflowBuilder,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
)
from ai_workflow_viewer import JsonlObservationViewer

pytestmark = pytest.mark.unit


def test_jsonl_observation_viewer_renders_html_from_public_contracts(tmp_path):
    definition = WorkflowBuilder("viewer").step("plan").step("render").build()
    trace_path = tmp_path / "trace.jsonl"
    usage_path = tmp_path / "usage.jsonl"
    detail_path = tmp_path / "detail.jsonl"
    event = WorkflowTraceEvent(
        node="plan",
        decision="llm:prompt",
        phase="llm:request",
        detail_refs=["detail-1"],
        run_id="run-1",
    )
    detail = ObservationDetail(
        detail_id="detail-1",
        event_id=event.event_id,
        kind="rendered_prompt",
        content_type="text/plain",
        digest="digest",
    )
    trace_path.write_text(
        "\n".join(
            [
                event.model_dump_json(),
                WorkflowTraceEvent(node="render", decision="accepted", run_id="run-1").model_dump_json(),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    usage_path.write_text(
        WorkflowUsageEvent(node="plan", total_tokens=9, estimated_usd=0.01).model_dump_json() + "\n",
        encoding="utf-8",
    )
    detail_path.write_text(detail.model_dump_json(by_alias=True) + "\n", encoding="utf-8")

    viewer = JsonlObservationViewer(
        definition,
        trace_path,
        usage_path=usage_path,
        detail_path=detail_path,
        title="Viewer test",
    )

    html = viewer.html()
    assert "Viewer test" in html
    assert "Investigation Graph" in html
    assert 'data-node-id="plan"' in html
    assert "flowchart TD" in html
    assert "9 tok" in html
    assert "metered $0.0100" in html
    assert "digest" in html
    assert [record["type"] for record in viewer.event_records()] == ["trace", "trace", "usage", "detail"]


def test_jsonl_observation_viewer_requires_run_id_for_multi_run_sources(tmp_path):
    definition = WorkflowBuilder("viewer").step("plan").build()
    trace_path = tmp_path / "trace.jsonl"
    detail_path = tmp_path / "detail.jsonl"
    run_1_event = WorkflowTraceEvent(
        node="plan",
        decision="accepted",
        detail_refs=["detail-1"],
        run_id="run-1",
    )
    run_2_event = WorkflowTraceEvent(
        node="plan",
        decision="accepted",
        detail_refs=["detail-2"],
        run_id="run-2",
    )
    trace_path.write_text(
        "\n".join([run_1_event.model_dump_json(), run_2_event.model_dump_json()]) + "\n",
        encoding="utf-8",
    )
    detail_path.write_text(
        "\n".join(
            [
                ObservationDetail(
                    detail_id="detail-1",
                    event_id=run_1_event.event_id,
                    run_id="run-1",
                    kind="rendered_prompt",
                    digest="run-1-digest",
                ).model_dump_json(by_alias=True),
                ObservationDetail(
                    detail_id="detail-2",
                    event_id=run_2_event.event_id,
                    run_id="run-2",
                    kind="rendered_prompt",
                    digest="run-2-digest",
                ).model_dump_json(by_alias=True),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    viewer = JsonlObservationViewer(definition, trace_path, detail_path=detail_path)
    with pytest.raises(ValueError, match="multiple run_ids"):
        viewer.html()

    selected = JsonlObservationViewer(
        definition,
        trace_path,
        detail_path=detail_path,
        run_id="run-2",
    )
    html = selected.html()

    assert "run-2-digest" in html
    assert "run-1-digest" not in html
    assert [record["record"]["run_id"] for record in selected.event_records()] == ["run-2", "run-2"]
