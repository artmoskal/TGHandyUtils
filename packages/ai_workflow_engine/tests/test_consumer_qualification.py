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
        assert isinstance(stream_meta, dict), f"{stream} settlement truth missing: {process_io}"
        assert stream_meta.get("total_bytes") == 0, f"sleeping probe wrote no {stream}: {stream_meta}"
        assert stream_meta.get("retained_bytes") == 0, stream_meta
        assert isinstance(stream_meta.get("limit_bytes"), int) and stream_meta["limit_bytes"] > 0, stream_meta
        assert stream_meta.get("truncated") is False, stream_meta
    result_file = process_io.get("result_file")
    assert isinstance(result_file, dict), process_io
    assert result_file.get("status") == "missing", (
        f"a sleeping probe writes no result file — settlement must say so: {result_file}"
    )
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

    # Merged truth at the DATA level: the projection over the group's MERGED events must show
    # the gate node completed. This is the permanent lock for the pre-resume-only regression
    # (rendering from group.segments[0] instead of the merged events leaves the gate
    # requires_user_input and fails here).
    merged_graph = build_observation_graph(
        group.definition, group.trace_events, group.usage_events, group.details
    )
    assert merged_graph.nodes["gate"].status == "completed", (
        f"merged gate node must be completed, got {merged_graph.nodes['gate'].status}"
    )

    # Merged truth at the RENDERED level: the gate node card itself carries the completed
    # status class (the segment strip legitimately shows one historical requires_user_input
    # card, so substring counting proves nothing — the node card class does).
    import re

    html = observation_group_to_html(group)
    assert "qual-slack" in html
    gate_card = re.search(
        r'class="graph-node run-status-(\w+)[^"]*"[^>]*data-node-id="gate"', html
    )
    assert gate_card is not None, "group page must render the gate node card"
    assert gate_card.group(1) == "completed", (
        f"rendered gate card must be run-status-completed, got run-status-{gate_card.group(1)} — "
        "the grouped page presents the resumed run as still suspended"
    )


# ------------------------------------------- MageQA shape (approval gate + coverage retrace, B0)

async def test_mageqa_shape_human_approval_retrace_with_injected_plan(tmp_path):
    """MageQA's canonical deepening loop through the PUBLIC doors, post-B0: a planner plans the
    audit, an approval gate (human node with inject_plan) suspends, and the coverage evaluator
    retraces to the GATE once — the retraced re-ask must receive typed provenance AND the live
    plan card, the criticism payload, and no leaked resume event; evidence from round 1 survives;
    the grouped bundle shows the whole three-segment lifecycle as ONE logical run."""

    from ai_workflow_engine import LocalWaitPolicy, PlanArtifact, PlanTask
    from ai_workflow_engine.models import RuntimeLimits
    from ai_workflow_engine.workflow import Retrace
    from ai_workflow_viewer import FileEventSource
    from pydantic import BaseModel

    bundle_root = tmp_path / "bundles"

    class Approval(BaseModel):
        status: str
        value: object = None

    ask_visits: list[dict] = []

    def make_plan(_ctx, _p):
        return PlanArtifact(
            goal="audit the storefront",
            tasks=[
                PlanTask(
                    task_id="observe",
                    description="collect storefront facts",
                    capability="observe",
                    payload={"page": "home"},
                )
            ],
        )

    def observe(_ctx, p):
        return {"facts": ["banner present"], "page": (p or {}).get("page")}

    def approve(ctx, payload):
        prov = getattr(ctx, "retrace_provenance", None)
        ask_visits.append(
            {
                "round": None if prov is None else prov.round,
                "plan_card": ctx.metadata.get("plan"),
                "criticism": isinstance(payload, dict) and "_criticism" in payload,
                "resume_event": ctx.metadata.get("resume_event"),
            }
        )
        event = ctx.metadata.get("resume_event")
        if event is None:
            return Approval(status="pending")
        return Approval(status="answered", value=event)

    gate_rounds = {"n": 0}

    def coverage(_ctx, _p):
        gate_rounds["n"] += 1
        return CapabilityResult(
            status="accepted" if gate_rounds["n"] > 1 else "rejected",
            error="coverage too shallow — one more approval round",
        )

    def report(_ctx, p):
        return {"report": "final", "approved_with": getattr(p, "value", None)}

    engine = (
        WorkflowEngineBuilder()
        .with_profile(
            WorkflowProfile(
                workflow_type="audit_approval",
                limits=RuntimeLimits(timeout_s=None),
                safety=SafetyPolicy(allowed_side_effects=[]),
            )
        )
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(bundle_root)))
        .register_capability(
            "make_plan",
            make_plan,
            spec=CapabilitySpec(name="make_plan", kind="llm", is_planner=True),
        )
        .register_capability("observe", observe)
        .register_capability("approve", approve)
        .register_capability("coverage", coverage, kind="llm")
        .register_capability("report", report)
        .register_workflow(
            WorkflowBuilder("audit_approval")
            .plan("plan_node", capability="make_plan")
            .human(
                "approve",
                wait_policy=LocalWaitPolicy(),
                inject_plan=True,
                description="human approval of the audit plan",
            )
            .evaluate("qa", target="approve", evaluator="coverage", on_reject=Retrace("approve"))
            .step("report")
            .build()
        )
        .build()
    )

    goal = WorkflowGoal(
        workflow_type="audit_approval", objective="audit", metadata={"run_id": "qual-mageqa-b0"}
    )
    first = await engine.run("audit_approval", {"target": "storefront"}, goal=goal)
    assert first.status == "requires_user_input"
    second = await engine.resume(first.snapshot, {"approve": "round-1"})
    assert second.status == "requires_user_input", "the coverage retrace must re-ask the human"
    final = await engine.resume(second.snapshot, {"approve": "round-2"})
    assert final.status == "completed"

    assert [v["round"] for v in ask_visits] == [None, None, 1, None], (
        "typed provenance belongs to the retraced re-ask exactly once"
    )
    assert all(
        isinstance(v["plan_card"], str) and "audit the storefront" in v["plan_card"]
        for v in ask_visits
    ), "inject_plan must deliver the LIVE plan card on every approval visit"
    assert [v["criticism"] for v in ask_visits] == [False, False, True, False]
    assert [v["resume_event"] for v in ask_visits] == [
        None,
        {"approve": "round-1"},
        None,
        {"approve": "round-2"},
    ]

    # round-1 evidence survives the deepening round (the GAP-1 promise, consumer-shaped)
    plan_records = [r for r in final.node_results if r.node_id == "plan_node"]
    assert plan_records, "planner truth must be in the final envelope"
    outputs = [r.output for r in final.node_results if r.node_id == "report"]
    assert outputs and outputs[-1]["approved_with"] == {"approve": "round-2"}

    # one logical run across all three segments in the persisted bundle
    group = FileEventSource(bundle_root).read_group("qual-mageqa-b0")
    assert [s.segment_index for s in group.segments] == [0, 1, 2]
    assert [s.status for s in group.segments] == [
        "requires_user_input",
        "requires_user_input",
        "completed",
    ]
