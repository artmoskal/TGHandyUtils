from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from ai_workflow_engine import (
    ImageInput,
    LLMRequest,
    LLMResponse,
    StructuredLLMNode,
    StructuredVisionLLMNode,
    WorkflowBuilder,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.config_loader import load_workflow_config
from ai_workflow_engine.llm_protocol import record_callable_usage
from ai_workflow_engine.models import RuntimeLimits, WorkflowProfile, WorkflowRunContext, WorkflowUsageSummary
from ai_workflow_engine.usage import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
    WorkflowUsageContext,
    check_budget_before_call,
    invoke_metered_chat,
    workflow_usage_scope,
)

pytestmark = pytest.mark.unit


class Label(BaseModel):
    label: str


class FakeStructuredClient:
    def __init__(
        self,
        text: str = '{"label": "ok"}',
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_usd: float | None = None,
    ):
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.estimated_usd = estimated_usd
        self.requests: list[LLMRequest] = []

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            text=self.text,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.input_tokens + self.output_tokens,
            estimated_usd=self.estimated_usd,
        )


class FakeLangChainUsageLLM:
    def __init__(self):
        self.messages = []

    def invoke(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(
            content="ok",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            response_metadata={"model_name": "unit-model", "id": "req-unit"},
        )


def _structured_node(client: FakeStructuredClient, *, prompt_template: str = "Classify {item}.") -> StructuredLLMNode:
    return StructuredLLMNode(
        name="classify",
        config=object(),
        output_model=Label,
        prompt_template=prompt_template,
        input_variables=["item"],
        llm=client,
    )


def _engine_for_node(node: StructuredLLMNode, limits: RuntimeLimits):
    async def classify(_ctx, payload):
        return await node.run({"item": payload.get("item", "budget")})

    return (
        WorkflowEngineBuilder()
        .register_capability("classify", classify, kind="llm", metered=True)
        .register_workflow(
            WorkflowBuilder("per_call_budget").step("classify").build(),
            profile=WorkflowProfile(workflow_type="per_call_budget", limits=limits),
        )
        .build()
    )


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


def test_subscription_notional_callable_usage_does_not_debit_metered_budget():
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(max_estimated_usd=0, max_estimated_usd_per_call=0),
    )

    with workflow_usage_scope(usage_context):
        record_callable_usage(
            LLMResponse(model="unit-model", input_tokens=10, output_tokens=5, total_tokens=15, estimated_usd=99.0),
            node="subscription_worker",
            attempt=1,
            cost_class="subscription_notional",
            notional_usd=0.42,
        )

    assert usage_context.summary.metered_usd is None
    assert usage_context.summary.estimated_usd is None
    assert usage_context.summary.notional_usd == 0.42
    event = usage_context.summary.events[0]
    assert event.cost_class == "subscription_notional"
    assert event.estimated_usd is None
    assert event.notional_usd == 0.42
    assert event.metadata["cost_known"] is True
    assert event.metadata["cost_source"] == "subscription_notional"


def test_subscription_notional_metered_chat_does_not_debit_metered_budget():
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(max_estimated_usd=0, max_estimated_usd_per_call=0),
    )
    llm = FakeLangChainUsageLLM()
    config = SimpleNamespace(
        WORKFLOW_USAGE_TRACKING_ENABLED=True,
        WORKFLOW_MODEL_PRICE_OVERRIDES_JSON=(
            '{"unit-model": {"input_per_1m": 1000000, "output_per_1m": 1000000}}'
        ),
    )

    with workflow_usage_scope(usage_context):
        invoke_metered_chat(
            llm,
            [HumanMessage(content="hello")],
            node="subscription_chat",
            model="unit-model",
            config=config,
            cost_class="subscription_notional",
            notional_usd=0.42,
        )

    assert len(llm.messages) == 1
    assert usage_context.summary.metered_usd is None
    assert usage_context.summary.notional_usd == 0.42
    event = usage_context.summary.events[0]
    assert event.cost_class == "subscription_notional"
    assert event.estimated_usd is None
    assert event.notional_usd == 0.42


async def test_profile_max_worker_calls_blocks_external_after_chat_before_handler():
    external_calls = {"count": 0}
    client = FakeStructuredClient()
    node = _structured_node(client)

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


async def test_input_token_cap_denies_structured_node_before_llm_call():
    client = FakeStructuredClient()
    node = _structured_node(client, prompt_template="Classify this long item: {item}.")
    engine = _engine_for_node(node, RuntimeLimits(max_input_tokens_per_call=2))

    result = await engine.run("per_call_budget", {"item": "this prompt is intentionally too long"})

    assert result.status == "failed"
    assert len(client.requests) == 0
    assert "max_input_tokens_per_call" in (result.error or "")


async def test_output_token_cap_records_truncated_trace_after_response():
    client = FakeStructuredClient(output_tokens=5)
    node = _structured_node(client)
    engine = _engine_for_node(node, RuntimeLimits(max_output_tokens_per_call=4))

    result = await engine.run("per_call_budget", {"item": "budget"})

    assert result.status == "failed"
    assert len(client.requests) == 1
    assert result.usage.events[0].output_tokens == 5
    assert "max_output_tokens_per_call" in (result.error or "")
    assert any(event.node == "classify" and event.decision == "truncated_by_budget" for event in result.trace)


async def test_image_cap_denies_vision_node_before_llm_call():
    client = FakeStructuredClient()
    node = StructuredVisionLLMNode(
        name="inspect",
        config=object(),
        output_model=Label,
        prompt_template="Inspect {item}.",
        input_variables=["item"],
        llm=client,
    )
    images = [
        ImageInput(source="base64", data="aW1hZ2UtMQ==", media_type="image/png", role="one"),
        ImageInput(source="base64", data="aW1hZ2UtMg==", media_type="image/png", role="two"),
    ]

    async def inspect(_ctx, _payload):
        return await node.run({"item": "frame"}, images=images)

    engine = (
        WorkflowEngineBuilder()
        .register_capability("inspect", inspect, kind="llm", metered=True)
        .register_workflow(
            WorkflowBuilder("image_budget").step("inspect").build(),
            profile=WorkflowProfile(
                workflow_type="image_budget",
                limits=RuntimeLimits(max_images_per_call=1),
            ),
        )
        .build()
    )

    result = await engine.run("image_budget", {})

    assert result.status == "failed"
    assert len(client.requests) == 0
    assert "max_images_per_call" in (result.error or "")


async def test_per_call_usd_cap_denies_after_usage_event_is_recorded():
    client = FakeStructuredClient(estimated_usd=0.11)
    node = _structured_node(client)
    engine = _engine_for_node(node, RuntimeLimits(max_estimated_usd_per_call=0.10))

    result = await engine.run("per_call_budget", {"item": "budget"})

    assert result.status == "failed"
    assert len(client.requests) == 1
    assert result.usage.events[0].estimated_usd == 0.11
    assert "max_estimated_usd_per_call" in (result.error or "")
