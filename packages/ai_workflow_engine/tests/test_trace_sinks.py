"""Trace sink behavior for live workflow observation."""

from __future__ import annotations

import asyncio
import logging

import pytest

from ai_workflow_engine.engine import (
    AsyncQueueTraceSink,
    CallbackTraceSink,
    CapabilityRegistry,
    CapabilityRuntime,
    InMemoryTraceSink,
    TeeTraceSink,
    capability_context_for_goal,
)
from ai_workflow_engine.models import CapabilitySpec, WorkflowGoal, WorkflowTraceEvent

pytestmark = pytest.mark.unit


def _event(decision: str) -> WorkflowTraceEvent:
    return WorkflowTraceEvent(node="trace", decision=decision)


def test_callback_trace_sink_swallows_callback_exceptions(caplog):
    seen: list[WorkflowTraceEvent] = []

    def broken_callback(event: WorkflowTraceEvent) -> None:
        seen.append(event)
        raise RuntimeError("callback exploded")

    caplog.set_level(logging.WARNING, logger="ai_workflow_engine.engine.capabilities")
    sink = CallbackTraceSink(broken_callback)

    sink.record(_event("start"))

    assert [event.decision for event in seen] == ["start"]
    assert "trace sink callback failed" in caplog.text


def test_async_queue_trace_sink_drops_oldest_when_full():
    sink = AsyncQueueTraceSink(maxsize=2)

    sink.record(_event("first"))
    sink.record(_event("second"))
    sink.record(_event("third"))

    assert sink.dropped == 1
    assert [sink.queue.get_nowait().decision, sink.queue.get_nowait().decision] == [
        "second",
        "third",
    ]


def test_tee_trace_sink_writes_to_all_sinks():
    left = InMemoryTraceSink()
    right = InMemoryTraceSink()
    sink = TeeTraceSink(left, right)

    sink.record(_event("accepted"))

    assert [event.decision for event in left.events] == ["accepted"]
    assert [event.decision for event in right.events] == ["accepted"]


async def test_callback_trace_sink_receives_event_before_run_finishes():
    seen: list[WorkflowTraceEvent] = []
    start_seen = asyncio.Event()
    release = asyncio.Event()

    def callback(event: WorkflowTraceEvent) -> None:
        seen.append(event)
        if event.decision == "start":
            start_seen.set()

    async def slow_capability(_context, _payload):
        await release.wait()
        return {"ok": True}

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="slow", kind="deterministic"), slow_capability)
    runtime = CapabilityRuntime(registry, CallbackTraceSink(callback))
    goal = WorkflowGoal(workflow_type="trace", objective="observe live events")

    task = asyncio.create_task(runtime.invoke("slow", {}, capability_context_for_goal(goal)))
    await asyncio.wait_for(start_seen.wait(), timeout=1)

    assert not task.done()
    assert [event.decision for event in seen] == ["start"]

    release.set()
    result = await task

    assert result.status == "accepted"
    assert [event.decision for event in seen] == ["start", "accepted"]
