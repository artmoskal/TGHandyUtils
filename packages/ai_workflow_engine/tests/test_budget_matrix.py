from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError

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
    budget_from_limits,
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


# --- task 1.3a: typed limits are the ONE budget source (v0.9 clean contract) -------------


def test_budget_from_limits_maps_typed_ceilings_including_new_call_counts():
    limits = RuntimeLimits(
        max_text_calls=3,
        max_image_calls=2,
        max_estimated_usd=1.5,
        max_worker_calls=7,
        max_input_tokens_per_call=100,
        max_output_tokens_per_call=200,
        max_images_per_call=4,
        max_estimated_usd_per_call=0.5,
    )

    budget = budget_from_limits(limits)

    # max_text_calls/max_image_calls could NOT come from typed limits before v0.9 —
    # they only existed as host-config duck-reads.
    assert budget.max_text_calls == 3
    assert budget.max_image_calls == 2
    assert budget.max_estimated_usd == 1.5
    assert budget.max_worker_calls == 7
    assert budget.max_input_tokens_per_call == 100
    assert budget.max_output_tokens_per_call == 200
    assert budget.max_images_per_call == 4
    assert budget.max_estimated_usd_per_call == 0.5
    assert budget_from_limits(None) == WorkflowBudget()


def test_budget_from_limits_rejects_host_config_shapes_loudly():
    host_config = SimpleNamespace(
        WORKFLOW_MAX_TEXT_CALLS_PER_RUN=16, WORKFLOW_USAGE_TRACKING_ENABLED=False
    )

    with pytest.raises(TypeError, match="no longer a budget source"):
        budget_from_limits(host_config)


def test_engine_zero_ceiling_is_a_hard_zero_cap():
    """Engine `0` means zero allowed calls — never "no cap" (that is the PRODUCT boundary's
    convention, normalized to None before the engine)."""

    budget = budget_from_limits(RuntimeLimits(max_text_calls=0))
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        budget,
    )

    with workflow_usage_scope(usage_context):
        with pytest.raises(WorkflowBudgetExceeded):
            check_budget_before_call("chat", "first_call")


def test_usage_tracking_flag_no_longer_bypasses_budget_checks():
    """Pre-v0.9 defect: WORKFLOW_USAGE_TRACKING_ENABLED=False skipped check_budget_before_call
    inside invoke_metered_chat entirely. Budgets now enforce unconditionally, pre-call."""

    llm = FakeLangChainUsageLLM()
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(max_text_calls=0),
    )
    flag_off_config = SimpleNamespace(WORKFLOW_USAGE_TRACKING_ENABLED=False)

    with workflow_usage_scope(usage_context):
        with pytest.raises(WorkflowBudgetExceeded):
            invoke_metered_chat(
                llm,
                [HumanMessage(content="hi")],
                node="flag_off_node",
                config=flag_off_config,
            )

    assert llm.messages == [], "budget must deny BEFORE the provider is invoked"


def test_limits_env_overrides_route_text_and_image_call_ceilings():
    bundle = load_workflow_config(
        defaults={"workflow": {"workflow_type": "budget_profile"}},
        env={
            "WORKFLOW_MAX_TEXT_CALLS_PER_RUN": "5",
            "WORKFLOW_MAX_IMAGE_CALLS": "2",
        },
    )

    assert bundle.profile.limits.max_text_calls == 5
    assert bundle.profile.limits.max_image_calls == 2


def test_runtime_limits_reject_unknown_keys():
    """A typo'd ceiling must never silently mean 'unlimited'."""

    with pytest.raises(ValidationError):
        RuntimeLimits(maxx_text_calls=1)


# --- task 1.3b: cumulative worker accounting + failed-attempt parity ---------------------


def test_max_worker_calls_survives_summary_serialization_roundtrip():
    """Pre-v0.9 defect: the worker counter lived only on the in-memory context, so
    suspend/resume silently regranted the whole allowance."""

    budget = WorkflowBudget(max_worker_calls=3)
    first = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        budget,
    )
    with workflow_usage_scope(first):
        check_budget_before_call("chat", "one")
        check_budget_before_call("agent", "two")

    restored_summary = WorkflowUsageSummary.model_validate(first.summary.model_dump())
    resumed = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        restored_summary,
        budget,
    )

    assert resumed.worker_call_count == 2, "resume lost the worker-call spend"
    with workflow_usage_scope(resumed):
        check_budget_before_call("external", "three")
        with pytest.raises(WorkflowBudgetExceeded, match="max_worker_calls"):
            check_budget_before_call("chat", "four")


async def test_failed_plain_callable_attempt_records_event_like_langchain_path():
    """Failed-attempt accounting parity between the two LLM transports: a raising
    plain-callable client must leave the same honest trail as a raising LangChain client —
    a usage event with success=False and NO invented token/cost values."""

    class ExplodingCallable:
        async def __call__(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("plain transport down")

    node = StructuredLLMNode(
        name="exploding",
        config=object(),
        output_model=Label,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm=ExplodingCallable(),
    )
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(),
    )

    with workflow_usage_scope(usage_context):
        with pytest.raises(Exception, match="plain transport down"):
            await node.run({"item": "q"})

    failed = [event for event in usage_context.summary.events if event.success is False]
    assert failed, "plain-callable failure left no usage event (LangChain path records one)"
    event = failed[0]
    assert event.total_tokens == 0 and event.input_tokens == 0 and event.output_tokens == 0
    assert event.estimated_usd is None, "failed attempt must not invent cost"


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

    # Spec semantics: the response is already produced and paid for — the run continues with the
    # output, and the truncation is recorded loudly on the usage event (never a retroactive fail).
    assert result.status == "completed"
    assert len(client.requests) == 1
    event = result.usage.events[0]
    assert event.output_tokens == 5
    assert event.metadata["truncated_by_budget"] is True
    assert event.metadata["output_tokens_over_cap"] == "5/4"


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


class _CountingLangChainClient:
    """LangChain-shaped (has .invoke) — used to pin per-call caps on the thread path."""

    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return SimpleNamespace(content='{"label": "ok"}')


async def test_input_token_cap_applies_to_langchain_path_too():
    client = _CountingLangChainClient()
    node = StructuredLLMNode(
        name="lc_capped_node",
        config=object(),
        output_model=Label,
        prompt_template="Summarize: {text}",
        input_variables=["text"],
        llm=client,
    )
    context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf-cap", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(max_input_tokens_per_call=5),
    )
    with workflow_usage_scope(context):
        with pytest.raises(WorkflowBudgetExceeded, match="max_input_tokens_per_call"):
            await node.run({"text": "long prompt body " * 50})
    assert client.calls == 0  # denied BEFORE the LangChain client was invoked


def test_estimate_text_tokens_counts_chat_message_content_not_repr():
    from ai_workflow_engine import ChatMessage, ToolCallRequest, ToolResult
    from ai_workflow_engine.usage import estimate_text_tokens

    message = ChatMessage(role="user", content="abcd")
    assert estimate_text_tokens([message]) == 1  # 4 chars -> 1 token, no field-name inflation

    tooled = ChatMessage(
        role="tool",
        tool_results=[ToolResult(call_id="c1", content="x" * 40)],
        tool_calls=[ToolCallRequest(call_id="c1", name="t", arguments={"q": "y" * 40})],
    )
    estimate = estimate_text_tokens([tooled])
    assert 15 <= estimate <= 40  # content+arguments based, not str(repr) of the whole model


# ------------------------------ Phase R8: failed routed-call attribution parity


async def test_failed_routed_plain_callable_reports_routed_model_and_profile():
    """R8 (review finding #20): when a ModelProfile routes the client, the FAILED-attempt
    usage event must carry the routed model/profile identity — not the node's default model."""

    from ai_workflow_engine import ModelProfile
    from ai_workflow_engine.model_binding import model_profile_scope

    class ExplodingRoutedClient:
        provider_label = "chatgpt_browser"

        async def __call__(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("routed transport down")

    node = StructuredLLMNode(
        name="routed_exploder",
        config=object(),
        output_model=Label,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm_factory=lambda config, model, temperature: ExplodingRoutedClient(),
    )
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(),
    )
    routed = ModelProfile(name="browser_route", provider="chatgpt_browser", model="routed-model")

    with workflow_usage_scope(usage_context):
        with model_profile_scope(routed):
            with pytest.raises(Exception, match="routed transport down"):
                await node.run({"item": "q"})

    failed = [e for e in usage_context.summary.events if e.success is False]
    assert failed, "routed failure left no usage event"
    event = failed[0]
    assert event.model == "routed-model", "failure must carry the ROUTED model, not the default"
    assert event.provider == "chatgpt_browser"
    assert event.metadata["model_profile"] == "browser_route"
    assert event.total_tokens == 0 and event.estimated_usd is None


async def test_failed_anonymous_callable_keeps_fallback_attribution():
    """Regression pair: non-profile callables retain the current fallback identity."""

    class ExplodingCallable:
        async def __call__(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("plain transport down")

    node = StructuredLLMNode(
        name="anon_exploder",
        config=object(),
        output_model=Label,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm=ExplodingCallable(),
    )
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(),
    )

    with workflow_usage_scope(usage_context):
        with pytest.raises(Exception, match="plain transport down"):
            await node.run({"item": "q"})

    event = [e for e in usage_context.summary.events if e.success is False][0]
    assert event.provider == "custom"
    assert "model_profile" not in event.metadata


async def test_failed_subscription_client_is_never_booked_as_metered():
    """R14 (recheck finding #6): a raising client that marks itself ``subscription_mode=True``
    (the claude -p console client shape — it exposes NO cost_class attribute) must record its
    failed attempt as subscription_notional; booking it as metered would poison the money
    ledger the run summary displays."""

    class ExplodingSubscriptionClient:
        provider_label = "claude_console"
        subscription_mode = True

        async def __call__(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("console transport down")

    node = StructuredLLMNode(
        name="subscription_exploder",
        config=object(),
        output_model=Label,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm=ExplodingSubscriptionClient(),
    )
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(),
    )

    with workflow_usage_scope(usage_context):
        with pytest.raises(Exception, match="console transport down"):
            await node.run({"item": "q"})

    failed = [e for e in usage_context.summary.events if e.success is False]
    assert failed, "subscription failure left no usage event"
    assert failed[0].cost_class == "subscription_notional", (
        "subscription_mode client failures must never be booked as metered spend"
    )
    assert failed[0].estimated_usd is None


async def test_failed_client_with_explicit_cost_class_keeps_it():
    """R14 regression pair: an explicit cost_class attribute still wins unchanged."""

    class ExplodingMeteredClient:
        provider_label = "openai"
        cost_class = "metered"
        subscription_mode = True  # explicit cost_class must take precedence

        async def __call__(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("metered transport down")

    node = StructuredLLMNode(
        name="metered_exploder",
        config=object(),
        output_model=Label,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm=ExplodingMeteredClient(),
    )
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(),
    )

    with workflow_usage_scope(usage_context):
        with pytest.raises(Exception, match="metered transport down"):
            await node.run({"item": "q"})

    failed = [e for e in usage_context.summary.events if e.success is False]
    assert failed and failed[0].cost_class == "metered"


async def test_failed_call_records_the_burn_a_typed_transport_error_carries():
    """Q-R2 (engine half): when the transport error carries what the aborted call actually
    consumed (claude budget aborts burn turns first), the FAILED usage event records that
    notional and the CLI subtype — money honesty covers failures."""

    class BudgetAbortError(RuntimeError):
        cli_subtype = "error_max_budget_usd"
        notional_usd = 0.112174

    class AbortingSubscriptionClient:
        provider_label = "claude_p"
        subscription_mode = True

        async def __call__(self, request: LLMRequest) -> LLMResponse:
            raise BudgetAbortError("console CLI exited 1 (error_max_budget_usd)")

    node = StructuredLLMNode(
        name="budget_abort",
        config=object(),
        output_model=Label,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm=AbortingSubscriptionClient(),
    )
    usage_context = WorkflowUsageContext(
        WorkflowRunContext(workflow_id="wf", workflow_type="budget"),
        WorkflowUsageSummary(),
        WorkflowBudget(),
    )

    with workflow_usage_scope(usage_context):
        with pytest.raises(Exception, match="error_max_budget_usd"):
            await node.run({"item": "q"})

    failed = [e for e in usage_context.summary.events if e.success is False]
    assert len(failed) == 2, "initial + retry attempts each leave an honest failed event"
    for event in failed:
        assert event.notional_usd == pytest.approx(0.112174), (
            "each aborted attempt's REAL burn must be recorded, not dropped"
        )
        assert event.metadata.get("cli_subtype") == "error_max_budget_usd"
        assert event.cost_class == "subscription_notional"
    assert usage_context.summary.notional_usd == pytest.approx(2 * 0.112174), (
        "every aborted attempt's burn aggregates into the run's notional total"
    )
