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
    assert "flowchart TD" in html
    assert "9 tok" in html
    assert "metered $0.0100" in html
    assert "digest" in html
    assert [record["type"] for record in viewer.event_records()] == ["trace", "trace", "usage", "detail"]
