"""Worker-integrated scheduling tests for the reusable workflow executor."""

import asyncio

import pytest

from ai_workflow_engine import WorkflowBuilder, WorkflowEngineBuilder
from ai_workflow_engine.models import SchedulingPolicy

pytestmark = pytest.mark.unit


def _two_flow_engine(lane: SchedulingPolicy, long_worker, quick_worker):
    return (
        WorkflowEngineBuilder()
        .register_capability("long_worker", long_worker, kind="llm")
        .register_capability("quick_worker", quick_worker, kind="llm")
        .register_workflow(
            WorkflowBuilder("flowA").step("slow", capability="long_worker", scheduling=lane).build()
        )
        .register_workflow(
            WorkflowBuilder("flowB").step("fast", capability="quick_worker", scheduling=lane).build()
        )
        .build()
    )


async def test_drop_not_queue_backend_slot_held_until_worker_completes_blocks_concurrent_call():
    started = asyncio.Event()
    release = asyncio.Event()

    async def long_worker(ctx, p):
        started.set()
        await release.wait()
        return "A-done"

    async def quick_worker(ctx, p):
        return "B-done"

    lane = SchedulingPolicy(mode="drop_not_queue", backend_key="local_model", max_backend_concurrency=1)
    engine = _two_flow_engine(lane, long_worker, quick_worker)

    task_a = asyncio.create_task(engine.run("flowA", {}))
    await started.wait()  # A is inside the worker, holding the single backend slot.

    # Second backend call cannot start concurrently — it is dropped while the slot is locked.
    res_b = await engine.run("flowB", {})
    assert res_b.status == "failed"
    drop = next(e for e in res_b.trace if e.decision == "schedule:drop")
    assert drop.metadata["lane"] == "local_model"
    assert drop.metadata["reason"]

    # The slot is released only when the worker actually completes.
    release.set()
    res_a = await asyncio.wait_for(task_a, timeout=2)
    assert res_a.status == "completed"
    assert res_a.output == "A-done"

    res_b2 = await engine.run("flowB", {})
    assert res_b2.status == "completed"
    assert res_b2.output == "B-done"


async def test_single_flight_cancel_cancels_previous_waits_for_exit_then_promotes_latest():
    started = asyncio.Event()
    cancel_seen = asyncio.Event()
    allow_cancel_exit = asyncio.Event()
    quick_started = asyncio.Event()

    async def long_worker(ctx, p):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_seen.set()
            await allow_cancel_exit.wait()
            raise

    async def quick_worker(ctx, p):
        quick_started.set()
        return "B-done"

    lane = SchedulingPolicy(mode="single_flight_cancel", backend_key="local_model", max_backend_concurrency=1)
    engine = _two_flow_engine(lane, long_worker, quick_worker)

    task_a = asyncio.create_task(engine.run("flowA", {}))
    await started.wait()

    task_b = asyncio.create_task(engine.run("flowB", {}))
    await cancel_seen.wait()
    await asyncio.sleep(0)
    assert not quick_started.is_set()

    allow_cancel_exit.set()
    res_a, res_b = await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2)

    assert res_a.status == "failed"
    assert res_a.node("slow").status == "failed"
    assert "cancelled: superseded by" in (res_a.error or "")
    assert res_b.status == "completed"
    assert res_b.output == "B-done"

    cancel_previous = next(e for e in res_b.trace if e.decision == "schedule:cancel_previous")
    promoted_run_id = cancel_previous.metadata["run_id"]
    previous_run_id = cancel_previous.metadata["previous_run_id"]
    assert promoted_run_id != previous_run_id
    assert any(
        e.decision == "schedule:cancel_request"
        and e.metadata["previous_run_id"] == previous_run_id
        and e.metadata["superseding_run_id"] == promoted_run_id
        for e in res_b.trace
    )
    assert any(
        e.decision == "schedule:cancelled"
        and e.metadata["run_id"] == previous_run_id
        and e.metadata["superseded_by"] == promoted_run_id
        for e in res_b.trace
    )
    assert any(
        e.decision == "schedule:promote" and e.metadata["run_id"] == promoted_run_id
        for e in res_b.trace
    )


async def test_single_flight_cancel_marks_suppressed_cancel_worker_failed_after_real_exit():
    started = asyncio.Event()
    cancel_seen = asyncio.Event()
    allow_cancel_exit = asyncio.Event()
    quick_started = asyncio.Event()

    async def stubborn_worker(ctx, p):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_seen.set()
            await allow_cancel_exit.wait()
            return "A-ignored-cancel"

    async def quick_worker(ctx, p):
        quick_started.set()
        return "B-done"

    lane = SchedulingPolicy(mode="single_flight_cancel", backend_key="local_model", max_backend_concurrency=1)
    engine = _two_flow_engine(lane, stubborn_worker, quick_worker)

    task_a = asyncio.create_task(engine.run("flowA", {}))
    await started.wait()

    task_b = asyncio.create_task(engine.run("flowB", {}))
    await cancel_seen.wait()
    await asyncio.sleep(0)
    assert not quick_started.is_set()

    allow_cancel_exit.set()
    res_a, res_b = await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2)

    assert res_a.status == "failed"
    assert res_a.output is None
    assert "cancelled: superseded by" in (res_a.error or "")
    assert res_b.status == "completed"
    assert res_b.output == "B-done"


async def test_cancel_previous_race_previous_exits_before_lookup_does_not_wedge_lane():
    """If the previous worker fully exits between submit() and the task-registry lookup
    (multi-loop/threaded-driver interleaving), the superseding run must NOT hard-fail while
    holding the promoted slot — that would wedge the lane forever. It proceeds instead."""

    started = asyncio.Event()
    release_a = asyncio.Event()

    async def long_worker(ctx, p):
        started.set()
        await release_a.wait()
        return "A-done"

    async def quick_worker(ctx, p):
        return "B-done"

    lane = SchedulingPolicy(mode="single_flight_cancel", backend_key="local_model", max_backend_concurrency=1)
    engine = _two_flow_engine(lane, long_worker, quick_worker)
    scheduler = engine.executor.scheduler
    real_submit = scheduler.submit

    def racing_submit(**kwargs):
        decision = real_submit(**kwargs)
        if decision.action == "cancel_previous":
            # Simulate the previous worker exiting inside the submit→lookup window:
            # registry entry gone, slot freed, the new run already promoted to active.
            engine.executor._scheduled_tasks.pop(kwargs["key"], None)
            scheduler.complete(key=kwargs["key"], run_id=decision.previous_run_id)
        return decision

    scheduler.submit = racing_submit
    task_a = asyncio.create_task(engine.run("flowA", {}))
    await started.wait()

    res_b = await asyncio.wait_for(engine.run("flowB", {}), timeout=2)
    # Not a false "could not find active task" failure — the run proceeds on its promoted slot.
    assert res_b.status == "completed"
    assert res_b.output == "B-done"
    assert any(e.decision == "schedule:cancel_skipped" for e in res_b.trace)

    release_a.set()
    res_a = await asyncio.wait_for(task_a, timeout=2)
    assert res_a.status == "completed"

    # The lane stays usable afterwards (no phantom active run wedging it).
    scheduler.submit = real_submit
    res_c = await asyncio.wait_for(engine.run("flowB", {}), timeout=2)
    assert res_c.status == "completed"
