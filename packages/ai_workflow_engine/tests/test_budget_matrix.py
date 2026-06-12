import pytest
from pydantic import BaseModel

from ai_workflow_engine import LLMRequest, LLMResponse, StructuredLLMNode, WorkflowBuilder, WorkflowEngineBuilder
from ai_workflow_engine.config_loader import load_workflow_config
from ai_workflow_engine.models import RuntimeLimits, WorkflowProfile, WorkflowRunContext, WorkflowUsageSummary
from ai_workflow_engine.usage import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
    WorkflowUsageContext,
    check_budget_before_call,
    workflow_usage_scope,
)

pytestmark = pytest.mark.unit


class Label(BaseModel):
    label: str


class FakeStructuredClient:
    def __init__(self):
        self.requests: list[LLMRequest] = []

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(text='{"label": "ok"}')


def test_max_worker_calls_counts_chat_agent_external_operations_with_cap_name():
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(max_worker_calls=3),
    )

    with workflow_usage_scope(usage_context):
        check_budget_before_call("chat", "chat_node")
        check_budget_before_call("agent", "agent_node")
        check_budget_before_call("external", "external_node")
        with pytest.raises(WorkflowBudgetExceeded, match="max_worker_calls.*chat_overflow"):
            check_budget_before_call("chat", "chat_overflow")

    assert usage_context.worker_call_count == 3


def test_none_worker_call_cap_changes_nothing_for_budget_checker():
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(max_worker_calls=None),
    )

    with workflow_usage_scope(usage_context):
        for index in range(5):
            check_budget_before_call("chat", f"chat_{index}")
            check_budget_before_call("agent", f"agent_{index}")
            check_budget_before_call("external", f"external_{index}")

    assert usage_context.worker_call_count == 0


async def test_profile_max_worker_calls_blocks_external_after_chat_before_handler():
    external_calls = {"count": 0}
    client = FakeStructuredClient()
    node = StructuredLLMNode(
        name="classify",
        config=object(),
        output_model=Label,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm=client,
    )

    async def classify(_ctx, _payload):
        return await node.run({"item": "budget"})

    def external_worker(_ctx, _payload):
        external_calls["count"] += 1
        return {"external": True}

    engine = (
        WorkflowEngineBuilder()
        .register_capability("classify", classify, kind="llm", metered=True)
        .register_capability("external_worker", external_worker, kind="external")
        .register_workflow(
            WorkflowBuilder("worker_budget").step("classify").step("external_worker").build(),
            profile=WorkflowProfile(
                workflow_type="worker_budget",
                limits=RuntimeLimits(max_worker_calls=1),
            ),
        )
        .build()
    )

    result = await engine.run("worker_budget", {})

    assert result.status == "failed"
    assert len(client.requests) == 1
    assert external_calls["count"] == 0
    assert "max_worker_calls" in (result.error or "")


async def test_none_profile_worker_call_cap_does_not_block_external_workflow():
    calls: list[str] = []

    def first(_ctx, payload):
        calls.append("first")
        return payload

    def second(_ctx, payload):
        calls.append("second")
        return {"done": payload}

    engine = (
        WorkflowEngineBuilder()
        .register_capability("first", first, kind="external")
        .register_capability("second", second, kind="external")
        .register_workflow(
            WorkflowBuilder("worker_unlimited").step("first").step("second").build(),
            profile=WorkflowProfile(
                workflow_type="worker_unlimited",
                limits=RuntimeLimits(max_worker_calls=None),
            ),
        )
        .build()
    )

    result = await engine.run("worker_unlimited", {"value": 1})

    assert result.status == "completed"
    assert calls == ["first", "second"]


def test_budget_matrix_limits_load_from_yaml_profile_and_env_overrides():
    bundle = load_workflow_config(
        defaults={
            "workflow": {
                "workflow_type": "budget_profile",
                "limits": {
                    "max_worker_calls": 4,
                    "max_input_tokens_per_call": 20_000,
                    "max_output_tokens_per_call": 4_000,
                    "max_images_per_call": 8,
                    "max_estimated_usd_per_call": 0.10,
                },
            }
        },
        env={
            "WORKFLOW_MAX_WORKER_CALLS": "6",
            "WORKFLOW_MAX_IMAGES_PER_CALL": "3",
        },
    )

    assert bundle.profile.limits.max_worker_calls == 6
    assert bundle.profile.limits.max_input_tokens_per_call == 20_000
    assert bundle.profile.limits.max_output_tokens_per_call == 4_000
    assert bundle.profile.limits.max_images_per_call == 3
    assert bundle.profile.limits.max_estimated_usd_per_call == 0.10
