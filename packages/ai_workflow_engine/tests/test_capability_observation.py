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
    proj = CapabilityObservationProjector(rec.record, cap)
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
    CapabilityObservationProjector(rec.record, off).terminal(
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
    CapabilityObservationProjector(_Recorder().record, _Capture("full")).terminal(
        name="cap", attempt=1, spec=_spec(), output=result, elapsed_ms=1, metadata={"a": 1},
    )
    assert result.model_dump() == before, "the projector mutated the result it was given"


def test_failed_and_timeout_projections_are_error_severity_with_error_detail():
    rec, cap = _Recorder(), _Capture("full")
    proj = CapabilityObservationProjector(rec.record, cap)
    proj.failed(name="cap", attempt=1, spec=_spec(), error="boom", decision="failed",
                elapsed_ms=1, metadata={})
    proj.timeout(name="cap", attempt=1, spec=_spec(), error="deadline", elapsed_ms=1,
                 metadata={"timeout_reason": "execution_window_exceeded"})
    assert [e.decision for e in rec.events] == ["failed", "partial"]
    assert all(e.severity == "error" for e in rec.events)
    assert all(len(e.detail_refs) == 1 for e in rec.events)  # one tool_error detail each
