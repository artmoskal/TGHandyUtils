"""Phase I1.1 gate: the capability observation projector is the SINGLE owner of start/terminal
trace + linked details, imports nothing forbidden, and is projection-only (never control)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ai_workflow_engine.engine.capability_observation import CapabilityObservationProjector
from ai_workflow_engine.models import CapabilityResult, CapabilitySpec, WorkflowArtifact

pytestmark = [pytest.mark.unit]

_MODULE = (
    Path(__file__).parents[1]
    / "ai_workflow_engine" / "engine" / "capability_observation.py"
)


def test_projector_imports_nothing_forbidden():
    """Dependency direction: observation must not reach the runtime facade, budget, executor,
    process control, nodes, bundle writer, or viewer — it only serializes decided facts."""

    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    forbidden = (
        "ai_workflow_engine.engine.capabilities",
        "ai_workflow_engine.engine.invocation_supervision",
        "ai_workflow_engine.engine.capability_contract",
        "ai_workflow_engine.observation_bundle",
        "ai_workflow_tools",
        "ai_workflow_engine.budget",
        "ai_workflow_engine.usage",
        "ai_workflow_engine.executor",
        "ai_workflow_engine.engine.external",
        "ai_workflow_engine.engine.process_io",
        "ai_workflow_engine.engine.execution_window",
        "ai_workflow_engine.execution_window",
        "ai_workflow_engine.nodes",
        "ai_workflow_viewer",
    )
    hits = [m for m in imported for f in forbidden if m == f or m.startswith(f + ".")]
    assert not hits, f"observation projector reached forbidden dependencies: {hits}"


class _Recorder:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


class _Capture:
    """A minimal observation capture that records details in a given mode."""

    def __init__(self, mode="off"):
        self.mode = mode
        self.details = []

    def record_detail(self, *, event_id, kind, payload):
        if self.mode == "off":
            return None
        from types import SimpleNamespace

        d = SimpleNamespace(detail_id=f"d{len(self.details)}", kind=kind, event_id=event_id)
        self.details.append(d)
        return d


def _spec():
    return CapabilitySpec(name="cap", kind="deterministic")


def test_terminal_projection_emits_one_event_and_links_details_in_full_mode():
    rec, cap = _Recorder(), _Capture(mode="full")
    proj = CapabilityObservationProjector(lambda: rec.record, lambda: cap)
    out = CapabilityResult(
        status="accepted", output={"ok": 1},
        artifacts=[WorkflowArtifact(path="a.png", kind="media")],
    )
    ret = proj.terminal(name="cap", attempt=1, spec=_spec(), output=out, elapsed_ms=3, metadata={})

    assert ret is None, "projection must return no control value"
    assert len(rec.events) == 1
    ev = rec.events[0]
    assert ev.decision == "accepted" and ev.phase == "tool:result" and ev.severity == "info"
    # tool_result detail + one artifact_preview detail, both linked on the event
    assert len(ev.detail_refs) == 2 and len(cap.details) == 2


def test_capture_off_records_no_details_but_same_control_event():
    off = _Capture(mode="off")
    rec = _Recorder()
    CapabilityObservationProjector(lambda: rec.record, lambda: off).terminal(
        name="cap", attempt=1, spec=_spec(),
        output=CapabilityResult(status="accepted", output={"ok": 1}), elapsed_ms=1, metadata={},
    )
    assert off.details == []  # nothing captured in off mode
    assert rec.events[0].decision == "accepted"  # the control event still fires
    assert rec.events[0].detail_refs == []  # no detail links when capture is off


def test_projector_cannot_mutate_the_result_it_projects():
    """Observation is a projection of decided facts: handing it the terminal result must not let
    it change status/output/error/metadata."""

    result = CapabilityResult(status="partial", output={"half": 1}, error="cut", metadata={"a": 1})
    before = result.model_dump()
    _r, _c = _Recorder(), _Capture("full")
    CapabilityObservationProjector(lambda: _r.record, lambda: _c).terminal(
        name="cap", attempt=1, spec=_spec(), output=result, elapsed_ms=1, metadata={"a": 1},
    )
    assert result.model_dump() == before, "the projector mutated the result it was given"


def test_failed_and_timeout_projections_are_error_severity_with_error_detail():
    rec, cap = _Recorder(), _Capture("full")
    proj = CapabilityObservationProjector(lambda: rec.record, lambda: cap)
    proj.failed(name="cap", attempt=1, spec=_spec(), error="boom", decision="failed",
                elapsed_ms=1, metadata={})
    proj.timeout(name="cap", attempt=1, spec=_spec(), error="deadline", elapsed_ms=1,
                 metadata={"timeout_reason": "execution_window_exceeded"})
    assert [e.decision for e in rec.events] == ["failed", "partial"]
    assert all(e.severity == "error" for e in rec.events)
    assert all(len(e.detail_refs) == 1 for e in rec.events)  # one tool_error detail each


def test_projector_resolves_collaborators_per_record_not_at_construction():
    """The projector must call PROVIDERS each record so a swapped sink/capture is honored — the
    regression fixed in I1.R1. Swapping the provider targets after construction must retarget."""

    first_rec, second_rec = _Recorder(), _Recorder()
    first_cap, second_cap = _Capture("full"), _Capture("full")
    box = {"rec": first_rec, "cap": first_cap}
    proj = CapabilityObservationProjector(lambda: box["rec"].record, lambda: box["cap"])

    proj.terminal(name="cap", attempt=1, spec=_spec(),
                  output=CapabilityResult(status="accepted", output={"n": 1}), elapsed_ms=1, metadata={})
    box["rec"], box["cap"] = second_rec, second_cap  # consumer swaps collaborators mid-life
    proj.terminal(name="cap", attempt=2, spec=_spec(),
                  output=CapabilityResult(status="accepted", output={"n": 2}), elapsed_ms=1, metadata={})

    assert len(first_rec.events) == 1 and len(second_rec.events) == 1, "swap not honored per record"
    assert len(first_cap.details) == 1 and len(second_cap.details) == 1


# --------------------------------------------------------------------------------------
# Public-door rebind (I1.R1 gate): a consumer may replace runtime.trace_sink / runtime.observation
# AFTER construction (the shipped GoPro pilot tees runtime.trace_sink, pilots.py:490). v0.10.1
# resolved both at call time; the projector must not freeze the originals.
# --------------------------------------------------------------------------------------

import asyncio  # noqa: E402

from ai_workflow_engine.engine.capabilities import (  # noqa: E402
    CapabilityRegistry,
    CapabilityRuntime,
    InMemoryDetailSink,
    InMemoryTraceSink,
)
from ai_workflow_engine.models import (  # noqa: E402
    CapabilityContext,
    RuntimePlan,
    SafetyPolicy,
    WorkflowGoal,
    WorkflowRunContext,
)
from ai_workflow_engine.observability_capture import ObservationCapture  # noqa: E402


def _ctx() -> CapabilityContext:
    return CapabilityContext(
        goal=WorkflowGoal(workflow_type="t", objective="o"),
        run_context=WorkflowRunContext(workflow_id="r", workflow_type="t"),
        plan=RuntimePlan(workflow_type="t", safety=SafetyPolicy(allowed_side_effects=[])),
    )


def _runtime_with(reg=None, trace=None, **kw) -> CapabilityRuntime:
    reg = reg or CapabilityRegistry()
    reg.register(CapabilitySpec(name="cap", kind="deterministic"), lambda c, p: {"ok": 1})
    return CapabilityRuntime(reg, trace or InMemoryTraceSink(), **kw)


@pytest.mark.asyncio
async def test_runtime_honors_trace_sink_replaced_after_construction():
    original = InMemoryTraceSink()
    runtime = _runtime_with(trace=original)
    replacement = InMemoryTraceSink()
    runtime.trace_sink = replacement  # consumer swap (pilots.py pattern)

    await runtime.invoke("cap", {}, _ctx())

    assert [e.node for e in replacement.events], "replacement sink received no capability events"
    assert {e.decision for e in replacement.events} >= {"start", "accepted"}
    assert not any(e.node == "cap" for e in original.events), "events leaked to the pre-swap sink"


@pytest.mark.asyncio
async def test_runtime_honors_observation_replaced_after_construction():
    runtime = _runtime_with(detail_sink=InMemoryDetailSink(), capture_detail_text=True)
    new_details = InMemoryDetailSink()
    runtime.observation = ObservationCapture(runtime.trace_sink, detail_sink=new_details, mode="full")

    await runtime.invoke("cap", {}, _ctx())

    assert new_details.details, "replacement observation captured no details after the swap"
