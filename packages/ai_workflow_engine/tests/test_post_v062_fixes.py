"""Post-v0.6.2 codex findings, verified + regressed: B-post1 stale plan cache,
B-post3 resume preserves the original run identity (B-post2 lives in test_prompt_rendering)."""

import asyncio
from types import SimpleNamespace

import pytest

from ai_workflow_engine import WorkflowBuilder, WorkflowEngineBuilder, WorkflowGoal

pytestmark = pytest.mark.unit


def _noop(context, payload):
    return {"ok": True}


def test_reregister_workflow_invalidates_the_cached_runtime_plan():
    """B-post1: register_workflow with a CHANGED same-id definition must drop the cached plan
    even when no profile is supplied — definition-level limits/scheduling fold into the plan."""

    builder = WorkflowEngineBuilder()
    builder.register_capability("first", _noop, kind="deterministic")
    builder.register_capability("second", _noop, kind="deterministic")
    engine = builder.build()

    flow_v1 = WorkflowBuilder("plan_cache_flow").step("first").build()
    engine.register_workflow(flow_v1)
    asyncio.run(engine.run("plan_cache_flow", {"seed": 1}))  # populates the plan cache
    assert "plan_cache_flow" in engine._plans

    flow_v2 = WorkflowBuilder("plan_cache_flow").step("second").build()
    engine.register_workflow(flow_v2)  # NO profile argument

    assert "plan_cache_flow" not in engine._plans  # stale policy cannot ride along
    result = asyncio.run(engine.run("plan_cache_flow", {"seed": 2}))
    assert result.status == "completed"
    assert result.node("second") is not None


def test_resume_preserves_original_goal_and_run_identity():
    """B-post3: resuming without re-supplying goal/user/constraints continues under the
    ORIGINAL identity — same user, delivery target, constraints, and run-id lineage."""

    seen = {}
    builder = WorkflowEngineBuilder()

    async def work(context, payload):
        return {"value": payload}

    async def ask(context, payload):
        if "resume_event" in context.metadata:
            seen["goal"] = context.goal
            seen["run_context"] = context.run_context
            return SimpleNamespace(status="answered", value="ok")
        return SimpleNamespace(status="pending")

    builder.register_capability("work", work, kind="deterministic")
    builder.register_capability("ask", ask, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("identity_wf").step("work").human("ask").build()
    )
    engine = builder.build()

    goal = WorkflowGoal(
        workflow_type="identity_wf",
        objective="prove identity survives the wait",
        constraints={"tenant": "acme"},
        delivery_target="telegram",
        user_id=77,
        metadata={"campaign": "q3"},
    )

    async def scenario():
        first = await engine.run("identity_wf", "seed", goal=goal)
        assert first.status == "requires_user_input" and first.snapshot is not None
        original_run_id = {e.run_id for e in first.trace}.pop()
        second = await engine.resume(first.snapshot, "user says go")
        return original_run_id, second

    original_run_id, second = asyncio.run(scenario())

    assert second.status == "completed"
    assert seen["goal"].user_id == 77
    assert seen["goal"].delivery_target == "telegram"
    assert seen["goal"].constraints == {"tenant": "acme"}
    assert seen["goal"].metadata.get("campaign") == "q3"
    # Run-id lineage: the resumed half is the SAME logical run.
    assert seen["run_context"].workflow_id == original_run_id
