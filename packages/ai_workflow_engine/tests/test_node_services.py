"""H1 boundary tests: node handlers run against a fake NodeExecutionServices (no executor)."""

import asyncio

import pytest

pytestmark = pytest.mark.unit

from ai_workflow_engine import WorkflowBuilder
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    WorkflowGoal,
    WorkflowRunContext,
)
from ai_workflow_engine.nodes import NODE_HANDLERS, build_branch_node, build_step_node
from ai_workflow_engine.node_services import NodeExecutionServices
from ai_workflow_engine._runtime_state import CONTEXT, RUNNING_PAYLOAD
from ai_workflow_engine.workflow import BranchDecision


class _FakeTraceSink:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


class _FakeRegistry:
    def get(self, name):
        raise KeyError(name)


class _FakeRuntime:
    def __init__(self):
        self.trace_sink = _FakeTraceSink()
        self.registry = _FakeRegistry()
        self.invocations = []

    async def invoke(self, capability, payload, context, attempt=1):
        self.invocations.append((capability, payload))
        return CapabilityResult(status="accepted", output={"echo": payload})


class FakeNodeServices:
    """Minimal in-memory NodeExecutionServices — proves handlers need no executor."""

    def __init__(self):
        self.runtime = _FakeRuntime()
        self.subworkflows = {}
        self.scheduling = None  # not exercised by these nodes
        self.recorded = []

    def node_input(self, state, node):
        if node.input_key:
            return state.get("node_outputs", {}).get(node.input_key)
        return state.get(RUNNING_PAYLOAD)

    def record(self, state, node, result, *, attempts, input_payload=None, branch_label=None,
               force_status=None, error=None, fallback_reason=None):
        self.recorded.append((node.id, force_status or result.status, result.output))
        return {
            RUNNING_PAYLOAD: result.output,
            "node_outputs": {**state.get("node_outputs", {}), node.id: result.output},
            "node_status": {**state.get("node_status", {}), node.id: force_status or result.status},
        }

    async def invoke_bound(self, node, capability, payload, context, state, *, attempt=1, definition=None):
        return await self.runtime.invoke(capability, payload, context, attempt=attempt)

    def context_for_node(self, node, context, state, *, plan=None, definition=None):
        return context

    def sequential_predecessor(self, definition, node_id):
        return None

    def child_context(self, parent, child_def, ref):
        return parent

    async def run_child(self, definition, payload, context):
        raise AssertionError("not used in these tests")


def _context() -> CapabilityContext:
    return CapabilityContext(
        goal=WorkflowGoal(workflow_type="t", objective="test"),
        run_context=WorkflowRunContext(workflow_id="run-1", workflow_type="t"),
    )


def _state(payload):
    return {
        CONTEXT: _context(),
        RUNNING_PAYLOAD: payload,
        "node_outputs": {"__input__": payload},
        "node_status": {},
        "attempts": {},
        "routes": {},
        "branch_decisions": {},
        "transition_counts": {},
    }


def test_step_handler_runs_on_fake_services_without_executor():
    definition = WorkflowBuilder("fake_flow").step("do_work").build()
    node = definition.node("do_work")
    services = FakeNodeServices()

    update = asyncio.run(build_step_node(services, definition, node)(_state({"x": 1})))

    assert services.recorded and services.recorded[0][0] == "do_work"
    assert update["node_outputs"]["do_work"] == {"echo": {"x": 1}}


def test_branch_handler_routes_on_fake_services_without_executor():
    definition = (
        WorkflowBuilder("fake_branch_flow")
        .step("prepare")
        .branch("route", {"go": "left", "stop": "right"})
        .step("left")
        .step("right")
        .build()
    )
    node = definition.node("route")
    services = FakeNodeServices()

    async def decide(capability, payload, context, attempt=1):
        return CapabilityResult(status="accepted", output=BranchDecision(label="go", rationale="test"))

    services.runtime.invoke = decide

    update = asyncio.run(build_branch_node(services, definition, node)(_state({"x": 1})))

    assert update["routes"]["route"] == "go"
    assert update["branch_decisions"]["route"] == "go"


def test_handler_table_covers_all_known_kinds():
    assert set(NODE_HANDLERS) == {
        "step", "branch", "fanout", "evaluate", "subworkflow", "human", "planner",
    }
