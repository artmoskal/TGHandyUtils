from __future__ import annotations

import json

import pytest
from ai_workflow_engine.examples import (
    ThreeAxisSiteAuditInput,
    run_toy_three_axis_site_audit_pilot,
)
from ai_workflow_engine.models import EvidenceRef

pytestmark = pytest.mark.unit


async def test_three_axis_pilot_runs_cli_console_retrace_and_replay(
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    record_path = tmp_path / "fake-cli-records.json"
    monkeypatch.setenv("FAKE_CLI_MODE", "artifacts")
    monkeypatch.setenv("FAKE_CLI_RECORD", str(record_path))
    monkeypatch.setenv("FAKE_CLI_RECORD_APPEND", "1")
    monkeypatch.setenv("FAKE_CLI_COUNTER_FILE", str(tmp_path / "fake-cli-counter.txt"))
    monkeypatch.setenv(
        "FAKE_CLI_RESULTS_JSON",
        json.dumps(
            [
                '{"axis": "flexible", "finding": "first CLI artifact"}',
                (
                    '{"summary": "3 axes accepted after retrace", '
                    '"accepted_axes": ["rigid", "semi", "flexible"], '
                    '"flexible_new_artifact_count": 1}'
                ),
            ]
        ),
    )
    seed = EvidenceRef(
        role="seed",
        uri="memory://seed/site-map",
        media_type="text/plain",
        summary="Seed site-map evidence",
    )

    outcome = await run_toy_three_axis_site_audit_pilot(
        ThreeAxisSiteAuditInput(
            url="https://example.test",
            workspace_dir=str(tmp_path / "pilot-workspace"),
            input_assets=[seed],
        ),
        fake_cli_path=str(fake_cli_path),
    )

    report = outcome.result.output
    assert outcome.result.status == "completed"
    assert report.summary == "3 axes accepted after retrace"
    assert report.accepted_axes == ["rigid", "semi", "flexible"]
    assert report.flexible_new_artifact_count == 1

    plan = outcome.result.node("scenario_plan").output
    outputs = plan.metadata["task_outputs"]
    flexible = outputs["scenario_plan.flexible_axis"]
    semirigid = outputs["scenario_plan.semi_axis"]
    assert flexible.new_artifact_count == 1
    assert flexible.input_fingerprints[0]["role"] == "seed"
    assert flexible.input_fingerprints[0]["length"] > 0
    assert semirigid.output.axis == "semi"
    assert [step.call.tool_name for step in semirigid.steps] == ["collect_semirigid_evidence"]
    assert any(
        event.node == "three_axis_coordinator"
        and event.metadata["set_composition"] == "semi_rigid"
        for event in outcome.result.usage.events
    )

    assert outcome.replay_result.status == "completed"
    assert outcome.replay_result.usage.events == []
    assert outcome.replay_result.output.output == semirigid.output
    assert [step.call.tool_name for step in outcome.replay_result.output.steps] == [
        "collect_semirigid_evidence"
    ]

    assert sum(1 for node in outcome.result.node_results if node.node_id == "scenario_plan") == 2
    assert any(event.node == "adjudicate_three_axis" and event.decision == "retrace" for event in outcome.trace_events)
    assert any(event.decision == "inputs_staged" for event in outcome.trace_events)
    assert any(
        event.decision == "artifacts_salvaged" and event.metadata["new_artifact_count"] == 1
        for event in outcome.trace_events
    )
    assert [event.model_dump() for event in outcome.live_events] == [
        event.model_dump() for event in outcome.trace_events
    ]
    assert "metered" in outcome.trace_dump
    assert "notional" in outcome.trace_dump
    assert "artifacts_salvaged" in outcome.trace_dump
    assert "memory://seed" not in outcome.trace_dump

    records = json.loads(record_path.read_text(encoding="utf-8"))
    assert len(records) == 2
    assert records[0]["stdin"].startswith("Inspect https://example.test")
    assert "Report on this three-axis audit" in records[-1]["stdin"]
