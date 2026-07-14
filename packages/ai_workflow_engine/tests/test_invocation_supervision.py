"""Phase I2.0 gate: invocation supervision is the SINGLE lifecycle owner — dependency-light, and
the represented stop REALLY stops owned work.

The behavior-equality half of the gate lives in the sealed preservation oracle (all timeout/
cancellation/containment/nested/concurrent rows are differentially equal to the immutable v0.10.1
wheel). These tests attack what the oracle's canonical records cannot see directly: whether a
child coroutine OUTLIVES the engine's containment claim, whether caller cancellation settles the
child, and whether the owner grew forbidden dependencies.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from ai_workflow_engine.engine.capabilities import (
    CapabilityRegistry,
    CapabilityRuntime,
    InMemoryTraceSink,
)
from ai_workflow_engine.execution_window import TaskExecutionRequest, invocation_window_remaining_s
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilitySpec,
    RuntimePlan,
    SafetyPolicy,
    WorkflowGoal,
    WorkflowRunContext,
)

pytestmark = [pytest.mark.unit]

_MODULE = (
    Path(__file__).parents[1]
    / "ai_workflow_engine" / "engine" / "invocation_supervision.py"
)


def test_supervision_imports_nothing_forbidden():
    """Dependency direction (plan I2.0/I2.2): supervision must not reach observation, the runtime
    facade, executor, nodes, budget/usage, bundle writer, process settlement, viewer, or products.
    execution_window remains its ONLY engine collaborator (the arithmetic owner)."""

    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    forbidden = (
        "ai_workflow_engine.engine.capabilities",
        "ai_workflow_engine.engine.capability_observation",
        "ai_workflow_engine.engine.capability_contract",
        "ai_workflow_engine.observability_capture",
        "ai_workflow_engine.observation_bundle",
        "ai_workflow_engine.executor",
        "ai_workflow_engine.nodes",
        "ai_workflow_engine.budget",
        "ai_workflow_engine.usage",
        "ai_workflow_engine.engine.process_io",
        "ai_workflow_engine.engine.external",
        "ai_workflow_viewer",
        "ai_workflow_tools",
    )
    hits = [m for m in imported for f in forbidden if m == f or m.startswith(f + ".")]
    assert not hits, f"supervision reached forbidden dependencies: {hits}"
    engine_imports = [m for m in imported if m.startswith("ai_workflow_engine")]
    assert engine_imports == ["ai_workflow_engine.execution_window"], (
        f"supervision's only engine collaborator is execution_window, got {engine_imports}"
    )


def _ctx(timeout_s: float | None = None) -> CapabilityContext:
    kwargs: dict = dict(
        goal=WorkflowGoal(workflow_type="t", objective="o"),
        run_context=WorkflowRunContext(workflow_id="r", workflow_type="t"),
        plan=RuntimePlan(workflow_type="t", safety=SafetyPolicy(allowed_side_effects=[])),
    )
    if timeout_s is not None:
        kwargs["execution_request"] = TaskExecutionRequest(timeout_s=timeout_s)
    return CapabilityContext(**kwargs)


def _runtime(handler, name: str = "cap") -> CapabilityRuntime:
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name=name, kind="deterministic"), handler)
    return CapabilityRuntime(registry, InMemoryTraceSink())


@pytest.mark.asyncio
async def test_partial_timeout_really_stops_the_child():
    """The containment claim must be TRUE: after the engine reports a PARTIAL timeout, the child
    coroutine must be cancelled and emit no further heartbeats — a stop that is merely reported
    but not performed shows up here as a beat after settlement."""

    beats = {"n": 0}
    cancelled = {"seen": False}

    async def slow(_ctx, _p):
        try:
            while True:
                beats["n"] += 1
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            cancelled["seen"] = True
            raise

    runtime = _runtime(slow)
    result = await runtime.invoke("cap", {}, _ctx(timeout_s=0.3))

    assert result.status == "partial", result
    assert cancelled["seen"], "child never observed cancellation — the stop was not performed"
    settled_beats = beats["n"]
    await asyncio.sleep(0.15)
    assert beats["n"] == settled_beats, (
        f"child kept running after the containment claim ({beats['n']} > {settled_beats})"
    )


@pytest.mark.asyncio
async def test_caller_cancellation_settles_the_child():
    """Caller cancellation propagates untranslated AND the owned child is settled — cancelled,
    silent, and not leaked as a still-beating background task."""

    beats = {"n": 0}
    cancelled = {"seen": False}
    started = asyncio.Event()

    async def slow(_ctx, _p):
        started.set()
        try:
            while True:
                beats["n"] += 1
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            cancelled["seen"] = True
            raise

    runtime = _runtime(slow)
    task = asyncio.ensure_future(runtime.invoke("cap", {}, _ctx(timeout_s=5.0)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled["seen"], "child never observed the caller's cancellation"
    settled_beats = beats["n"]
    await asyncio.sleep(0.15)
    assert beats["n"] == settled_beats, "child outlived the caller's cancellation"


@pytest.mark.asyncio
async def test_caller_cancellation_second_cancel_reaches_a_stubborn_child():
    """Child-cleanup half of the settle path: a child that SWALLOWS the first CancelledError is
    re-cancelled after the settle grace (then detached with its exception consumed). Dropping
    that final cancel leaks a still-beating background task — this test observes the beats."""

    beats = {"n": 0}
    swallows = {"left": 1}
    started = asyncio.Event()

    async def stubborn(_ctx, _p):
        started.set()
        while True:
            try:
                beats["n"] += 1
                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                if swallows["left"] > 0:
                    swallows["left"] -= 1
                    continue  # swallow the first stop request and keep working
                raise

    runtime = _runtime(stubborn)
    task = asyncio.ensure_future(runtime.invoke("cap", {}, _ctx(timeout_s=0.8)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # allow the post-grace second cancel to land, then require silence
    await asyncio.sleep(0.3)
    settled_beats = beats["n"]
    await asyncio.sleep(0.15)
    assert beats["n"] == settled_beats, (
        "stubborn child was never re-cancelled after the settle grace — leaked background task"
    )
    assert swallows["left"] == 0, "the first cancellation never reached the child"


@pytest.mark.asyncio
async def test_unbounded_call_gains_no_synthetic_deadline():
    """No window request and no capability limit → the handler runs with NO ambient deadline
    published and completes normally — supervision must never invent a bound."""

    seen = {}

    async def free(_ctx, _p):
        await asyncio.sleep(0.01)
        seen["ambient"] = invocation_window_remaining_s()
        return {"ok": 1}

    runtime = _runtime(free)
    result = await runtime.invoke("cap", {}, _ctx(timeout_s=None))

    assert result.status == "accepted"
    assert seen["ambient"] is None, (
        f"unbounded call gained a synthetic ambient deadline: {seen['ambient']}"
    )
