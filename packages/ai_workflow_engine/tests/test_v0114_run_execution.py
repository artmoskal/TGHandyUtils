"""Invocation-local run deadline and recovered-attempt truth contracts."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ai_workflow_engine import (
    RunExecutionRequest,
    WorkflowBuilder,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.engine.runner import (
    derive_graph_failsafe_window,
    derive_workflow_result_status,
)
from ai_workflow_engine.executor import NodeResult
from ai_workflow_engine.models import RuntimeLimits, SafetyPolicy, WorkflowProfile, WorkflowUsageSummary
from ai_workflow_engine.result_assembly import RunResultAssembler
from ai_workflow_engine.waits import LocalWaitPolicy

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _profile(workflow_type: str, *, timeout_s: float | None = None) -> WorkflowProfile:
    return WorkflowProfile(
        workflow_type=workflow_type,
        limits=RuntimeLimits(timeout_s=timeout_s),
        safety=SafetyPolicy(allowed_side_effects=[]),
    )


async def test_run_execution_request_validates_strictly_and_is_public():
    assert RunExecutionRequest(timeout_s=3, source="mageqa").timeout_s == 3
    for invalid in (0, -1, float("inf"), float("nan"), True):
        with pytest.raises(ValidationError):
            RunExecutionRequest(timeout_s=invalid)
    with pytest.raises(ValidationError):
        RunExecutionRequest(timeout_s=1, source=" ")


async def test_run_request_narrows_profile_without_mutating_cached_plan():
    seen: list[tuple[float | None, dict]] = []

    async def observe(context, payload):
        await asyncio.sleep(0)
        seen.append((context.limits.timeout_s, context.metadata.get("run_execution", {})))
        return payload

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("narrow", timeout_s=10))
        .register_capability("observe", observe, kind="deterministic")
        .register_workflow(WorkflowBuilder("narrow").step("observe").build())
        .build()
    )

    await asyncio.gather(
        engine.run(
            "narrow",
            {"request": 3},
            execution=RunExecutionRequest(timeout_s=3, source="short"),
        ),
        engine.run(
            "narrow",
            {"request": 20},
            execution=RunExecutionRequest(timeout_s=20, source="long"),
        ),
    )
    await engine.run("narrow", {"request": None})

    assert sorted(value for value, _ in seen[:2]) == [3, 10]
    by_source = {metadata["source"]: metadata for _, metadata in seen[:2]}
    assert by_source["short"]["effective_timeout_s"] == 3
    assert by_source["long"]["effective_timeout_s"] == 10
    assert seen[2][0] == 10
    assert "run_execution" not in engine._plan_for(engine.workflows["narrow"]).constraints
    assert engine._plan_for(engine.workflows["narrow"]).limits.timeout_s == 10


async def test_run_request_bounds_unconfigured_work_and_refuses_sync_handler():
    called = {"sync": 0}

    async def slow(_context, _payload):
        await asyncio.sleep(1)
        return {"finished": True}

    def sync_after(_context, payload):
        called["sync"] += 1
        return payload

    engine = (
        WorkflowEngineBuilder()
        .with_profile(_profile("bounded_request"))
        .register_capability("slow", slow, kind="deterministic")
        .register_capability("sync_after", sync_after, kind="deterministic")
        .register_workflow(
            WorkflowBuilder("bounded_request").step("slow").step("sync_after").build()
        )
        .build()
    )

    started = time.monotonic()
    result = await engine.run(
        "bounded_request",
        {},
        execution=RunExecutionRequest(timeout_s=0.25, source="test"),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.4
    assert result.status in {"partial", "failed"}
    assert called["sync"] == 0


async def test_graph_cleanup_grace_is_inside_wall_limit():
    session = SimpleNamespace(run_remaining_s=lambda: 0.4)
    window = derive_graph_failsafe_window(0.4, session, cancellation_grace_s=2)
    assert window is not None
    assert window.work_timeout_s == pytest.approx(0.3)
    assert window.cancellation_grace_s == pytest.approx(0.1)
    assert window.work_timeout_s + window.cancellation_grace_s == pytest.approx(0.4)


async def test_effective_run_timeout_survives_snapshot_and_resume():
    seen: list[float | None] = []

    class GateResult(dict):
        @property
        def status(self):
            return self["status"]

    async def gate(context, _payload):
        seen.append(context.limits.timeout_s)
        event = context.metadata.get("resume_event")
        return GateResult(status="pending" if event is None else "answered")

    def build():
        return (
            WorkflowEngineBuilder()
            .with_profile(_profile("resume_deadline", timeout_s=30))
            .register_capability("gate", gate, kind="deterministic")
            .register_workflow(
                WorkflowBuilder("resume_deadline")
                .human("gate", wait_policy=LocalWaitPolicy())
                .build()
            )
            .build()
        )

    first = await build().run(
        "resume_deadline",
        {},
        execution=RunExecutionRequest(timeout_s=7, source="caller"),
    )
    assert first.snapshot is not None
    assert first.snapshot.run_timeout_s == 7

    await build().resume(
        first.snapshot.model_dump_json(),
        "approved",
        execution=RunExecutionRequest(timeout_s=5, source="resume_caller"),
    )
    assert seen == [7, 5]


async def test_latest_node_attempt_controls_status_and_terminal_error():
    historical = [
        NodeResult(node_id="vision", kind="step", status="partial", error="attempt timed out"),
        NodeResult(node_id="vision", kind="step", status="accepted"),
    ]
    state = {
        "status": "failed",
        "node_status": {"vision": "accepted"},
        "node_results": historical,
        "usage_summary": WorkflowUsageSummary(),
    }
    assert derive_workflow_result_status(state) == "completed"

    assembler = RunResultAssembler(
        runtime=SimpleNamespace(),
        result_factory=lambda **kwargs: kwargs,
        suspension=SimpleNamespace(),
    )
    result = assembler.envelope(
        WorkflowBuilder("recovered").step("noop").build(),
        state,
    )
    assert result["status"] == "completed"
    assert result["error"] is None
    assert result["node_results"] == historical


async def test_latest_partial_attempt_remains_terminal_truth():
    historical = [
        NodeResult(node_id="vision", kind="step", status="accepted"),
        NodeResult(node_id="vision", kind="step", status="partial", error="final timeout"),
    ]
    state = {
        "node_status": {"vision": "partial"},
        "node_results": historical,
        "usage_summary": WorkflowUsageSummary(),
    }
    assembler = RunResultAssembler(
        runtime=SimpleNamespace(),
        result_factory=lambda **kwargs: kwargs,
        suspension=SimpleNamespace(),
    )
    result = assembler.envelope(
        WorkflowBuilder("partial").step("noop").build(),
        state,
    )
    assert result["status"] == "partial"
    assert result["error"] == "final timeout"
