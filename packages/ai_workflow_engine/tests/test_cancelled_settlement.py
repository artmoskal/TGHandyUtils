"""Cancellation must seal settled evidence, or an immutable incomplete prefix."""
import asyncio
import hashlib
import time
import threading

import pytest
from pydantic import BaseModel

from ai_workflow_engine import (
    ObservationConfig, ObservationReader, StructuredLLMNode,
    WorkflowBuilder, WorkflowEngineBuilder, WorkflowGoal, WorkflowProfile,
)
from ai_workflow_engine._runtime_state import current_run_session
from ai_workflow_engine.engine.runner import WorkflowRunner
from ai_workflow_engine.models import ObservationDetail, RuntimeLimits, WorkflowTraceEvent, WorkflowUsageEvent
from ai_workflow_engine.models import ObservationJsonBody
from ai_workflow_engine.observation_values import body_sha256

pytestmark = pytest.mark.unit


class Answer(BaseModel):
    ok: bool


@pytest.mark.parametrize("cleanup_s, expected", [(0.01, "cancelled"), (0.2, "cancelled"), (0.5, "abandoned")])
async def test_nested_cancel_seals_after_settlement_or_stable_incomplete_prefix(tmp_path, monkeypatch, cleanup_s, expected):
    monkeypatch.setattr(WorkflowRunner, "_GRAPH_CANCELLATION_GRACE_S", 0.3)
    started = asyncio.Event()
    sessions = []

    async def client(request):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            end = time.monotonic() + cleanup_s
            while time.monotonic() < end:
                try:
                    await asyncio.sleep(end - time.monotonic())
                except asyncio.CancelledError:
                    pass  # finite cleanup acknowledgement, not fabricated observation writes
            raise

    node = StructuredLLMNode(name="review", config=object(), output_model=Answer,
                             prompt_template="Review", llm=client)

    async def review(ctx, payload):
        sessions.append(current_run_session())
        return await node.run({})

    engine = (WorkflowEngineBuilder().with_observation(ObservationConfig(
        enabled=True, bundle_dir=str(tmp_path), capture="full"))
        .register_capability("review", review, kind="llm", timeout_s=20)
        .register_workflow(WorkflowBuilder("cancel-settle").step("review").build(),
                           profile=WorkflowProfile(workflow_type="cancel-settle", limits=RuntimeLimits(timeout_s=30)))
        .build())
    task = asyncio.create_task(engine.run("cancel-settle", {}, goal=WorkflowGoal(
        workflow_type="cancel-settle", objective="settle", metadata={"run_id": "r"})))
    await asyncio.wait_for(started.wait(), 2)
    start = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert time.monotonic() - start < 0.45
    path = tmp_path / "r" / "segments" / "r"
    reader = ObservationReader(path)
    assert reader.meta.status == expected
    files = ("trace.jsonl", "details.jsonl", "usage.jsonl", "meta.json")
    sealed = {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in files}
    await asyncio.sleep(cleanup_s + 0.05)
    assert sealed == {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in files}
    if expected == "cancelled":
        assert list(reader.iter_trace_events())
        assert list(reader.iter_detail_envelopes())
        assert len(list(reader.iter_usage_events())) == 1
    else:
        assert set(reader.meta.incomplete_streams) == {"trace", "detail", "usage"}
        assert reader.meta.provider_evidence.integrity == "incomplete"
        assert list(reader.iter_trace_prefix())
        assert list(reader.iter_detail_prefix())
        assert list(reader.iter_usage_prefix()) == []  # unknown, not a zero-cost provider answer
    bundle = sessions[0].bundle
    body = ObservationJsonBody(value={"late": True})
    for sink, row in ((bundle.trace_sink, WorkflowTraceEvent(node="late", decision="late")),
                      (bundle.usage_sink, WorkflowUsageEvent(node="late")),
                      (bundle.detail_sink, ObservationDetail(event_id="late", kind="tool_payload", body=body, digest=body_sha256(body)))):
        with pytest.raises(RuntimeError, match="sealed"):
            sink.record(row)
    with pytest.raises(RuntimeError, match="sealed"):
        bundle.detail_sink.clear()
    with pytest.raises(RuntimeError, match="sealed"):
        bundle.finalize(sessions[0].definition, status="completed")
    assert sealed == {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in files}


def test_seal_waits_for_whole_inflight_write(tmp_path):
    from ai_workflow_engine import open_observation_run_bundle

    bundle = open_observation_run_bundle(tmp_path, "thread-write")
    entered, release, closing = threading.Event(), threading.Event(), threading.Event()
    inner = bundle.trace_sink.inner
    failures = []

    class DelayedSink:
        def record(self, event):
            entered.set()
            assert release.wait(2)
            inner.record(event)

    bundle.trace_sink.inner = DelayedSink()

    def write():
        try:
            bundle.trace_sink.record(WorkflowTraceEvent(node="work", decision="done"))
        except BaseException as exc:
            failures.append(exc)

    def close():
        try:
            closing.set()
            bundle.finalize(WorkflowBuilder("thread-write").step("work").build(), status="cancelled")
        except BaseException as exc:
            failures.append(exc)

    writer, finalizer = threading.Thread(target=write), threading.Thread(target=close)
    writer.start()
    assert entered.wait(2)
    finalizer.start()
    assert closing.wait(2)
    time.sleep(0.02)
    assert not (bundle.path / "meta.json").exists()
    release.set()
    writer.join(2)
    finalizer.join(2)
    assert not writer.is_alive() and not finalizer.is_alive()
    assert not failures
    assert len(list(ObservationReader(bundle.path).iter_trace_events())) == 1
