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
from ai_workflow_engine.usage_events import record_usage_event

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
        tags = {
            "A" if '"A"' in r.model_dump_json() else (
                "B" if '"B"' in r.model_dump_json() else None
            )
            for r in records
        }
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


async def test_child_envelopes_carry_exact_slices_under_concurrency_without_sink_events():
    """v0.11 clean contract (manifest row M7): child-run envelope traces are explicit
    session-owned slices — exact under BARRIER-interleaved concurrent children, with a custom
    trace sink exposing NO ``.events`` attribute (the removed sniffing fallback would have
    returned an empty/global list here)."""

    import asyncio

    from ai_workflow_engine import WorkflowBuilder, WorkflowEngineBuilder

    class NoEventsSink:
        def __init__(self):
            self.count = 0

        def record(self, event):  # deliberately NO .events attribute
            self.count += 1

    barrier_a, barrier_b = asyncio.Event(), asyncio.Event()

    async def child_work(ctx, payload):
        tag = payload["tag"] if isinstance(payload, dict) else getattr(payload, "tag", "?")
        if tag == "a":
            barrier_a.set()
            await asyncio.wait_for(barrier_b.wait(), timeout=5.0)
        else:
            barrier_b.set()
            await asyncio.wait_for(barrier_a.wait(), timeout=5.0)
        return {"tag": tag}

    builder = WorkflowEngineBuilder().with_trace_sink(NoEventsSink())
    builder.register_capability("child_work", child_work)
    builder.register_workflow(WorkflowBuilder("child_flow").step("child_work").build())

    def seed(ctx, p):
        return {"items": [{"tag": "a"}, {"tag": "b"}]}

    builder.register_capability("seed", seed)
    builder.register_workflow(
        WorkflowBuilder("parent_flow")
        .step("seed")
        .fanout("kids", capability="run_child_flow", items_key="seed.items",
                max_parallel=2, output_key="results")
        .build()
    )
    engine = builder.build()
    engine.register_workflow_capability("run_child_flow", "child_flow")

    result = await engine.run("parent_flow", {})
    assert result.status == "completed", result.error

    kids = next(n for n in result.node_results if n.node_id == "kids")
    # fanout committed both interleaved children; the barrier proves true concurrency
    assert barrier_a.is_set() and barrier_b.is_set()
    assert kids.status == "accepted", (kids.status, kids.error)

    # the parent envelope's trace is the session buffer (exact, sink-shape independent)
    assert result.trace, "parent envelope must carry the session's run buffer"
    child_nodes = {e.node for e in result.trace}
    assert "child_work" in child_nodes, "children recorded through the session"


async def test_child_trace_slice_is_exact_per_task_under_concurrency():
    """M7 mechanism lock (mutation surface): concurrent tasks each get ONLY their own slice —
    dropping the slice-append in SessionScopedTraceSink.record leaves these lists empty and
    fails here. The slice is session-owned; no sink `.events` is consulted (the passthrough
    property is gone)."""

    import asyncio

    from ai_workflow_engine._runtime_state import run_session_scope
    from ai_workflow_engine.models import WorkflowTraceEvent
    from ai_workflow_engine.run_session import SessionScopedTraceSink, WorkflowRunSession, child_trace_slice

    class NoEventsSink:
        def __init__(self):
            self.seen = []

        def record(self, event):
            self.seen.append(event.node)

    sink = SessionScopedTraceSink(NoEventsSink())
    assert not hasattr(sink, "events"), "the legacy .events passthrough must be gone (M7)"

    barrier_a, barrier_b = asyncio.Event(), asyncio.Event()

    async def child(tag: str, mine: asyncio.Event, other: asyncio.Event) -> list[str]:
        with child_trace_slice() as events:
            sink.record(WorkflowTraceEvent(node=f"{tag}-start", decision="start"))
            mine.set()
            await asyncio.wait_for(other.wait(), timeout=5.0)  # both children mid-flight
            sink.record(WorkflowTraceEvent(node=f"{tag}-end", decision="accepted"))
            return [e.node for e in events]

    session = WorkflowRunSession(workflow_id="wf", context=_context())
    with run_session_scope(session):
        slice_a, slice_b = await asyncio.gather(
            child("a", barrier_a, barrier_b), child("b", barrier_b, barrier_a)
        )
        assert slice_a == ["a-start", "a-end"], f"sibling bleed or lost slice: {slice_a}"
        assert slice_b == ["b-start", "b-end"], f"sibling bleed or lost slice: {slice_b}"
        # the session run buffer still carries EVERYTHING (parent envelope truth)
        buffered = [e.node for e in session.trace_events]
        assert sorted(buffered) == ["a-end", "a-start", "b-end", "b-start"]

        # review finding 3: an ANCESTOR scope includes its descendants' events (stack chain),
        # while a sibling opened afterwards stays disjoint.
        with child_trace_slice() as parent_events:
            sink.record(WorkflowTraceEvent(node="p-start", decision="start"))
            with child_trace_slice() as inner_events:
                sink.record(WorkflowTraceEvent(node="c-inner", decision="accepted"))
            sink.record(WorkflowTraceEvent(node="p-end", decision="accepted"))
        assert [e.node for e in inner_events] == ["c-inner"]
        assert [e.node for e in parent_events] == ["p-start", "c-inner", "p-end"], (
            f"parent scope must include descendant events: {[e.node for e in parent_events]}"
        )


async def test_nested_and_sibling_child_envelopes_are_exact_through_real_wiring():
    """Review finding 3, real executor wiring: a parent->child->grandchild chain of ACTUAL
    _run_inner envelopes — the child envelope includes its grandchild's capability events
    (descendants), and two barrier-interleaved sibling child runs capture disjoint envelopes.
    A custom sink WITHOUT .events proves no sniffing anywhere."""

    import asyncio

    from ai_workflow_engine import WorkflowBuilder, WorkflowEngineBuilder
    from ai_workflow_engine.models import WorkflowGoal

    class NoEventsSink:
        def record(self, event):
            pass

    builder = WorkflowEngineBuilder().with_trace_sink(NoEventsSink())
    builder.register_capability("leaf_work", lambda ctx, p: {"leaf": True})
    builder.register_workflow(WorkflowBuilder("grandchild").step("leaf_work").build())
    builder.register_workflow(
        WorkflowBuilder("child").subworkflow("run_grandchild", workflow="grandchild").build()
    )
    builder.register_workflow(
        WorkflowBuilder("parent").subworkflow("run_child", workflow="child").build()
    )

    barrier_x, barrier_y = asyncio.Event(), asyncio.Event()

    async def waiter(ctx, payload):
        tag = payload["tag"] if isinstance(payload, dict) else "?"
        if tag == "x":
            barrier_x.set()
            await asyncio.wait_for(barrier_y.wait(), timeout=5.0)
        else:
            barrier_y.set()
            await asyncio.wait_for(barrier_x.wait(), timeout=5.0)
        return {"tag": tag}

    builder.register_capability("waiter", waiter)
    builder.register_workflow(WorkflowBuilder("sib").step("waiter").build())
    engine = builder.build()

    # ---- nested: run the top workflow; capture the CHILD envelope through real _run_inner
    # wiring by invoking the child definition the same way the subworkflow node does.
    result = await engine.run("parent", {})
    assert result.status == "completed", result.error
    parent_nodes = [e.node for e in result.trace]
    assert "leaf_work" in parent_nodes, "top envelope must include grandchild capability events"

    # child envelope, captured via the executor door subworkflow nodes use (_run_inner) inside
    # a live session: its trace must include the grandchild's leaf_work (descendants), proving
    # the ancestor-chain fix end to end.
    from ai_workflow_engine._runtime_state import run_session_scope
    from ai_workflow_engine.run_session import WorkflowRunSession
    from ai_workflow_engine.models import CapabilityContext, WorkflowRunContext

    goal = WorkflowGoal(workflow_type="child", objective="nested-trace")
    ctx = CapabilityContext(
        goal=goal, run_context=WorkflowRunContext(workflow_id="nest-run", workflow_type="child")
    )
    session = WorkflowRunSession(workflow_id="child", context=ctx)
    with run_session_scope(session):
        child_env = await engine.executor._run_inner(
            engine.workflows["child"], {}, ctx
        )
    assert child_env.status == "completed", child_env.error
    child_nodes = [e.node for e in child_env.trace]
    assert "leaf_work" in child_nodes, (
        f"child envelope lost its grandchild's events (single-scope regression): {child_nodes}"
    )

    # ---- siblings: two interleaved child runs in ONE session — envelopes disjoint and exact
    sib_goal = WorkflowGoal(workflow_type="sib", objective="sibling-trace")
    sib_ctx = CapabilityContext(
        goal=sib_goal, run_context=WorkflowRunContext(workflow_id="sib-run", workflow_type="sib")
    )
    sib_session = WorkflowRunSession(workflow_id="sib", context=sib_ctx)
    with run_session_scope(sib_session):
        env_x, env_y = await asyncio.gather(
            engine.executor._run_inner(engine.workflows["sib"], {"tag": "x"}, sib_ctx),
            engine.executor._run_inner(engine.workflows["sib"], {"tag": "y"}, sib_ctx),
        )
    assert barrier_x.is_set() and barrier_y.is_set()
    for env in (env_x, env_y):
        assert env.status == "completed", env.error
        waiter_events = [e for e in env.trace if e.node == "waiter"]
        assert waiter_events, "sibling envelope must carry its own capability events"
        # exactly ONE start + terminal pair per sibling — the other sibling's pair must NOT bleed
        starts = [e for e in waiter_events if e.decision == "start"]
        terms = [e for e in waiter_events if e.decision == "accepted"]
        assert len(starts) == 1 and len(terms) == 1, (
            f"sibling envelope not exact: {[ (e.node, e.decision) for e in waiter_events ]}"
        )
