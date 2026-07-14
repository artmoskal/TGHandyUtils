"""Phase I2.2 gate: four hermetic consumer qualification shapes through the PUBLIC doors.

Each scenario reuses a real adopter's usage shape — Anki (content pipeline), MageQA (browser QA),
GoPro (goal compiler), SlackAzz (durable triage) — on the SAME WorkflowEngineBuilder /
WorkflowExecutor / CapabilityRuntime with no product branch and no test-only runtime. Together
they cover the plan's checklist: accepted and partial results, authored flow, fanout, process
timeout, artifact preview, suspend/resume, side-effect denial, grouped bundles, and viewer
rendering — all hermetic (no network, no paid calls, subprocesses are local ``python -c``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ai_workflow_engine import (
    DurableWaitPolicy,
    InMemoryWaitCoordinator,
    ObservationConfig,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowGoal,
)
from ai_workflow_engine.engine.external import ExternalProcessCapability, ExternalProcessRequest
from ai_workflow_engine.models import (
    CapabilityResult,
    CapabilitySpec,
    ObservationDetail,
    SafetyPolicy,
    WorkflowArtifact,
    WorkflowProfile,
    WorkflowTraceEvent,
)
from ai_workflow_viewer import build_observation_graph, observation_graph_to_html

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# ---------------------------------------------------------------- Anki shape (content pipeline)

async def test_anki_shape_accepted_artifact_bundle_and_viewer(tmp_path):
    """Accepted multi-step run with a media artifact: full capture links an artifact preview,
    the auto-opened bundle carries trace/details/usage + the artifact manifest, and the viewer
    renders the SAME bundle records — bundle and viewer truth stay coherent."""

    bundle_root = tmp_path / "bundles"
    media = tmp_path / "card.png"
    media.write_bytes(b"\x89PNG-not-really")

    builder = (
        WorkflowEngineBuilder()
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(bundle_root)))
        .with_detail_text_capture(True)
    )
    builder.register_capability("draft", lambda ctx, p: {"text": f"card for {p.get('topic')}"})

    def package(_ctx, payload):
        return CapabilityResult(
            status="accepted",
            output={"text": payload.text if hasattr(payload, "text") else payload["text"]},
            artifacts=[WorkflowArtifact(path=str(media), kind="media", owner_node="package")],
        )

    builder.register_capability("package", package)
    definition = WorkflowBuilder("anki_cards").step("draft").step("package").build()
    builder.register_workflow(definition)
    engine = builder.build()

    result = await engine.run("anki_cards", {"topic": "mitosis"})

    assert result.status == "completed", result.error
    assert [a.kind for a in result.artifacts] == ["media"]
    assert result.observation_bundle_path, "config-first observation must auto-open a bundle"
    bundle = Path(result.observation_bundle_path)

    trace_events = [
        WorkflowTraceEvent.model_validate_json(line)
        for line in (bundle / "trace.jsonl").read_text().splitlines()
    ]
    details = [
        ObservationDetail.model_validate_json(line)
        for line in (bundle / "details.jsonl").read_text().splitlines()
    ]
    detail_kinds = {d.kind for d in details}
    assert "artifact_preview" in detail_kinds, f"artifact preview missing: {detail_kinds}"
    assert "tool_result" in detail_kinds

    manifest = json.loads((bundle / "artifacts.json").read_text())
    assert manifest, "artifact manifest must list the produced media artifact"

    graph = build_observation_graph(definition, trace_events, (), details)
    html = observation_graph_to_html(definition, graph)
    assert "draft" in html and "package" in html, "viewer must render both pipeline nodes"


# ---------------------------------------------------------------- MageQA shape (browser QA)

async def test_mageqa_shape_process_timeout_partial_and_side_effect_denial(tmp_path):
    """One engine, two QA workflows: a process-backed probe that overruns its window settles as
    an honest PARTIAL with process_io truth, and a network capability under a deny-all profile is
    rejected — no product branch, same runtime for both."""

    probe = ExternalProcessCapability(name="probe")
    builder = WorkflowEngineBuilder()
    builder.register_capability("probe", probe, spec=probe.spec)
    builder.register_capability(
        "crawl", lambda ctx, p: {"crawled": True},
        spec=CapabilitySpec(name="crawl", kind="tool", side_effects=["network"]),
    )
    builder.register_workflow(
        WorkflowBuilder("qa_probe").step("probe").build(),
        profile=WorkflowProfile(
            workflow_type="qa_probe",
            safety=SafetyPolicy(allowed_side_effects=["external_call"]),
        ),
    )
    builder.register_workflow(
        WorkflowBuilder("qa_guard").step("crawl").build(),
        profile=WorkflowProfile(
            workflow_type="qa_guard",
            safety=SafetyPolicy(allowed_side_effects=[]),
        ),
    )
    engine = builder.build()

    overrun = await engine.run(
        "qa_probe",
        ExternalProcessRequest(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_s=0.4,
            kill_grace_s=0.5,
        ),
    )
    assert overrun.status == "partial", (overrun.status, overrun.error)
    probe_node = next(n for n in overrun.node_results if n.node_id == "probe")
    assert probe_node.status == "partial"
    assert "timed out" in (probe_node.error or ""), probe_node.error

    # The claimed process truth, actually asserted (codex I2 review): the capability-door partial
    # event carries the bounded process_io settlement (byte totals/truncation per stream + the
    # result-file state) AND the resolved execution window.
    probe_partial = next(
        e for e in overrun.trace
        if e.node == "probe" and e.decision == "partial" and e.phase == "tool:result"
    )
    process_io = probe_partial.metadata.get("process_io")
    assert isinstance(process_io, dict), f"partial trace lacks process_io truth: {probe_partial.metadata}"
    for stream in ("stdout", "stderr"):
        stream_meta = process_io.get(stream)
        assert isinstance(stream_meta, dict) and stream_meta.get("truncated") is False, (
            f"{stream} settlement truth missing/wrong: {process_io}"
        )
        assert stream_meta.get("total_bytes") == 0, f"sleeping probe wrote no {stream}: {stream_meta}"
    assert isinstance(process_io.get("result_file"), dict), process_io
    # Bound truth for an explicit per-request process window (the engine run itself is
    # unbounded here, so the window rides as process_execution_bound, not execution_window).
    bound_meta = probe_partial.metadata.get("process_execution_bound")
    assert isinstance(bound_meta, dict), f"partial trace lacks the process bound truth: {probe_partial.metadata}"
    assert bound_meta.get("work_timeout_s") == 0.4 and bound_meta.get("kill_grace_s") == 0.5, bound_meta
    assert bound_meta.get("source") == "explicit", bound_meta

    denied = await engine.run("qa_guard", {})
    assert denied.status == "failed", (denied.status, denied.error)
    crawl_node = next(n for n in denied.node_results if n.node_id == "crawl")
    assert crawl_node.status == "failed", "safety denial is committed as a failed node"
    assert "denied by policy" in (crawl_node.error or ""), crawl_node.error
    assert any(e.decision == "denied" for e in engine.trace_sink.events if e.node == "crawl"), (
        "the denial decision must be visible on the trace"
    )


# ---------------------------------------------------------------- GoPro shape (goal compiler)

async def test_gopro_shape_authored_flow_with_fanout(tmp_path):
    """The S1 goal-compiler shape: an AI-authored FlowArtifact (step + bounded fanout) is
    validated, compiled, and run by the SAME executor as hand-written workflows, with authored
    provenance on the trace and per-child outcomes preserved."""

    builder = WorkflowEngineBuilder()
    builder.register_capability("collect", lambda ctx, p: {"items": ["alpha", "beta", "gamma"]})
    builder.register_capability("label", lambda ctx, item: {"label": str(item).upper()})
    engine = builder.build()

    artifact = {
        "flow_id": "inventory_labels",
        "goal": "collect items then label each",
        "nodes": [
            {"kind": "step", "id": "collect"},
            {"kind": "fanout", "id": "label", "items_key": "collect.items",
             "max_parallel": 2, "max_items": 8, "output_key": "labels"},
        ],
    }
    result = await engine.run_authored_flow(artifact, {"goal": "label the shelf"})

    assert result.status == "completed", result.error
    assert isinstance(result.output, list), result.output
    labels = sorted(entry["label"] for entry in result.output if isinstance(entry, dict))
    assert labels == ["ALPHA", "BETA", "GAMMA"], result.output
    assert any(e.decision == "flow:authored" for e in engine.trace_sink.events), (
        "authored-flow provenance event missing"
    )
    fanout_node = next(n for n in result.node_results if n.node_id == "label")
    assert fanout_node.status == "accepted"


# ---------------------------------------------------------------- SlackAzz shape (durable triage)

async def test_slackazz_shape_durable_suspend_resume_grouped_bundle(tmp_path):
    """Durable triage: the run suspends on a human gate (wait_handle door), a claimed signal
    resumes it to completion, and the grouped observation bundle records BOTH segments of the one
    logical run (initial suspended segment + resume segment) under the logical run id."""

    bundle_root = tmp_path / "bundles"

    from datetime import datetime, timezone

    def clock():
        return datetime.now(timezone.utc)

    coordinator = InMemoryWaitCoordinator(clock=clock)
    builder = (
        WorkflowEngineBuilder()
        .with_wait_coordinator(coordinator, clock=clock)
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(bundle_root)))
    )

    from pydantic import BaseModel

    class Gate(BaseModel):
        status: str
        value: str = ""

    def gate(ctx, _p):
        event = ctx.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        return Gate(status="answered", value=str(event))

    builder.register_capability("gate", gate)
    builder.register_capability("reply", lambda ctx, p: {"reply": getattr(p, "value", "")})
    builder.register_capability("escalate", lambda ctx, p: {"escalated": True})
    builder.register_workflow(
        WorkflowBuilder("triage")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("reply")
        .step("escalate")
        .build()
    )
    engine = builder.build()

    goal = WorkflowGoal(workflow_type="triage", objective="triage", metadata={"run_id": "qual-slack"})
    first = await engine.run("triage", {}, goal=goal)
    assert first.status == "requires_user_input"
    assert first.wait_handle is not None and first.snapshot is None

    outcome = await engine.deliver_wait_event(
        first.wait_handle.wait_id, {"kind": "signal", "event_id": "evt-q", "payload": "approved"}
    )
    assert outcome.kind == "executed"
    assert outcome.run_result.status == "completed"

    segments = sorted(p.name for p in bundle_root.iterdir() if (p / "meta.json").exists())
    assert segments == ["qual-slack", "qual-slack--s001"], segments
    metas = {
        name: json.loads((bundle_root / name / "meta.json").read_text()) for name in segments
    }
    assert metas["qual-slack"]["segment_kind"] == "initial"
    assert [metas[name]["segment_index"] for name in segments] == [0, 1]
    assert all(m["run_id"] == "qual-slack" for m in metas.values()), (
        "both segments must carry the LOGICAL run id"
    )

    # The claimed grouped lifecycle, actually crossed end to end (codex I2 review): read the
    # LOGICAL run through the grouped reader and render the grouped viewer page — the resumed
    # story must merge into ONE truthful lifecycle, never a false still-suspended state.
    from ai_workflow_viewer import FileEventSource, observation_group_to_html

    group = FileEventSource(bundle_root).read_group("qual-slack")
    assert group.run_id == "qual-slack"
    assert [s.segment_index for s in group.segments] == [0, 1]
    assert [s.status for s in group.segments] == ["requires_user_input", "completed"], (
        f"grouped reader must show suspended->completed, got {[s.status for s in group.segments]}"
    )
    assert group.records, "the merged group must carry the segment-ordered records"

    html = observation_group_to_html(group)
    assert "qual-slack" in html
    gate_sections = [seg for seg in html.split("segment") if "gate" in seg]
    assert gate_sections, "group page must render the gate node"
    assert "completed" in html, "the resumed lifecycle must render as completed"
    # the terminal story is completed — the page must not present the run as still waiting
    assert html.count("requires_user_input") <= html.count("completed"), (
        "grouped page reads as still-suspended despite the completed resume"
    )
