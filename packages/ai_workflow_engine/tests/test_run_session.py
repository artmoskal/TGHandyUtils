"""H2 run-session tests: per-run isolation, resume without re-execution, bundle lifecycle."""

import asyncio
from types import SimpleNamespace

import pytest

from ai_workflow_engine import (
    LocalWaitPolicy,
    InMemoryDetailSink,
    InMemoryTraceSink,
    WorkflowBuilder,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.models import (
    CapabilityContext,
    WorkflowGoal,
    WorkflowRunContext,
    WorkflowUsageEvent,
)
from ai_workflow_engine.run_session import WorkflowRunSession
from ai_workflow_engine.usage import record_usage_event

pytestmark = pytest.mark.unit


def _two_step_engine():
    """One SHARED engine (shared trace/detail sinks) — the hostile setup for isolation."""

    trace_sink = InMemoryTraceSink()
    detail_sink = InMemoryDetailSink()
    builder = (
        WorkflowEngineBuilder()
        .with_trace_sink(trace_sink)
        .with_detail_sink(detail_sink)
        .with_detail_text_capture(True)
    )

    async def work(context, payload):
        # Interleave the two runs so shared mutable state would be exposed.
        await asyncio.sleep(0.01)
        record_usage_event(
            WorkflowUsageEvent(node="work", model="fake", metadata={"tag": payload["tag"]})
        )
        return {"tag": payload["tag"], "stage": "worked"}

    async def finish(context, payload):
        await asyncio.sleep(0.01)
        record_usage_event(
            WorkflowUsageEvent(node="finish", model="fake", metadata={"tag": payload["tag"]})
        )
        return {"tag": payload["tag"], "stage": "finished"}

    builder.register_capability("work", work, kind="deterministic")
    builder.register_capability("finish", finish, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("iso_wf").step("work").step("finish").build()
    )
    return builder.build(), trace_sink, detail_sink


def test_concurrent_runs_share_no_per_run_state():
    engine, _trace, detail_sink = _two_step_engine()

    async def scenario():
        return await asyncio.gather(
            engine.run("iso_wf", {"tag": "A"}),
            engine.run("iso_wf", {"tag": "B"}),
        )

    result_a, result_b = asyncio.run(scenario())

    # Outputs: each run kept its own payload end to end.
    assert result_a.output == {"tag": "A", "stage": "finished"}
    assert result_b.output == {"tag": "B", "stage": "finished"}

    # Trace: each envelope carries exactly its own run's events (run_id partition).
    run_id_a = {e.run_id for e in result_a.trace}
    run_id_b = {e.run_id for e in result_b.trace}
    assert len(run_id_a) == 1 and len(run_id_b) == 1
    assert run_id_a != run_id_b

    # Usage: each session aggregated ONLY its own events (2 per run), stamped with its run id.
    for result, tag in ((result_a, "A"), (result_b, "B")):
        events = result.usage.events
        assert len(events) == 2, f"run {tag} usage events leaked/merged: {len(events)}"
        assert {e.metadata["tag"] for e in events} == {tag}
        assert {e.run_id for e in events} == (run_id_a if tag == "A" else run_id_b)

    # Details: the shared sink got both runs, but every record is stamped with a run id and
    # no record mixes content across runs.
    by_run = {}
    for record in detail_sink.details:
        assert record.run_id, "observation detail without run_id"
        by_run.setdefault(record.run_id, []).append(record)
    assert len(by_run) == 2
    for rid, records in by_run.items():
        tags = {("A" if '"A"' in (r.text or "") else ("B" if '"B"' in (r.text or "") else None)) for r in records}
        tags.discard(None)
        assert len(tags) <= 1, f"detail records for run {rid} mix payload tags: {tags}"


def test_resume_rebuilds_session_without_reexecuting_completed_nodes():
    calls = {"work": 0, "finish": 0}
    builder = WorkflowEngineBuilder()

    async def work(context, payload):
        calls["work"] += 1
        return {"value": payload}

    async def ask(context, payload):
        if "resume_event" in context.metadata:
            return SimpleNamespace(status="answered", value=context.metadata["resume_event"])
        return SimpleNamespace(status="pending")

    async def finish(context, payload):
        calls["finish"] += 1
        return {"done": True}

    builder.register_capability("work", work, kind="deterministic")
    builder.register_capability("ask", ask, kind="deterministic")
    builder.register_capability("finish", finish, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("resume_wf").step("work").human("ask", wait_policy=LocalWaitPolicy()).step("finish").build()
    )
    engine = builder.build()

    async def scenario():
        first = await engine.run("resume_wf", "seed")
        assert first.status == "requires_user_input" and first.snapshot is not None
        assert calls == {"work": 1, "finish": 0}
        return await engine.resume(first.snapshot, "user says go")

    second = asyncio.run(scenario())

    assert second.status == "completed"
    # The session was rebuilt from the snapshot: completed nodes did NOT re-execute.
    assert calls == {"work": 1, "finish": 1}


class _RecordingBundle:
    """Fake mirroring ObservationRunBundle.finalize's REAL signature (B5 regression)."""

    def __init__(self):
        self.finalized_with = []

    def finalize(self, definition, *, status, usage=None, artifacts=None):
        assert definition is not None
        self.finalized_with.append(status)


def _context() -> CapabilityContext:
    return CapabilityContext(
        goal=WorkflowGoal(workflow_type="t", objective="test"),
        run_context=WorkflowRunContext(workflow_id="run-42", workflow_type="t"),
    )


def test_session_close_finalizes_bundle_once_with_terminal_status():
    bundle = _RecordingBundle()
    definition = WorkflowBuilder("wf_close").step("s").build()
    session = WorkflowRunSession(
        workflow_id="wf", context=_context(), bundle=bundle, definition=definition
    )

    session.close("failed")
    session.close("completed")  # idempotent: the first terminal status wins

    assert bundle.finalized_with == ["failed"]
    assert session.run_id == "run-42"


def test_session_close_with_bundle_but_no_definition_fails_loudly():
    session = WorkflowRunSession(workflow_id="wf", context=_context(), bundle=_RecordingBundle())
    with pytest.raises(RuntimeError, match="no definition"):
        session.close("completed")


def test_failed_run_closes_its_session_with_failed_status(monkeypatch):
    import ai_workflow_engine.executor as executor_module

    closes = []

    class RecordingSession(WorkflowRunSession):
        def close(self, status, *, artifacts=None):
            closes.append(status)
            super().close(status, artifacts=artifacts)

    monkeypatch.setattr(executor_module, "WorkflowRunSession", RecordingSession)

    builder = WorkflowEngineBuilder()

    async def explode(context, payload):
        raise RuntimeError("boom")

    builder.register_capability("explode", explode, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("fail_wf").step("explode").build())
    engine = builder.build()

    result = asyncio.run(engine.run("fail_wf", "seed"))

    assert result.status == "failed"
    assert closes == ["failed"]
