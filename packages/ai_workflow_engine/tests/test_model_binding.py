"""G3 — declarative per-node model binding (AC-G3) incl. RC1 fixed-client conflict."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ai_workflow_engine import (
    InMemoryDetailSink,
    InMemoryTraceSink,
    LLMRequest,
    LLMResponse,
    StructuredLLMNode,
    WorkflowBuilder,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.models import ModelProfile
from ai_workflow_engine.prompt_capture import PromptCapturingLLMClient

pytestmark = pytest.mark.unit

LOCAL = ModelProfile(name="local_vlm", provider="ollama", model="qwen2.5vl:3b", temperature=0.0)
STRONG = ModelProfile(name="strong_planner", provider="openai", model="gpt-5.5", temperature=0.1)


class Step(BaseModel):
    note: str


class RecordingFactoryLLM:
    """LangChain-shaped fake produced by a factory; remembers which model it was built for."""

    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        return SimpleNamespace(
            content='{"note": "done"}',
            usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            response_metadata={"model_name": self.model},
        )


def _factory_node(name: str) -> StructuredLLMNode:
    built = []

    def factory(config, model, temperature):
        built.append(model)
        return RecordingFactoryLLM(model)

    node = StructuredLLMNode(
        name=name,
        config=SimpleNamespace(WORKFLOW_DEFAULT_MODEL="default-model"),
        output_model=Step,
        prompt_template="Do {what}.",
        input_variables=["what"],
        llm_factory=factory,
    )
    node._built_models = built  # test hook
    return node


def _two_step_engine():
    node_a = _factory_node("weak_step_node")
    node_b = _factory_node("strong_step_node")

    async def weak_step(ctx, payload):
        return await node_a.run({"what": "cheap work"})

    async def strong_step(ctx, payload):
        return await node_b.run({"what": "planner work"})

    builder = WorkflowEngineBuilder()
    builder.with_model_profile(LOCAL).with_model_profile(STRONG)
    builder.register_capability("weak_step", weak_step, kind="llm")
    builder.register_capability("strong_step", strong_step, kind="llm")
    builder.register_workflow(
        WorkflowBuilder("two_models")
        .step("weak_step", model_profile="local_vlm")
        .step("strong_step", model_profile="strong_planner")
        .build()
    )
    return builder.build(), node_a, node_b


async def test_two_steps_use_their_declared_profiles_and_trace_proves_it():
    engine, node_a, node_b = _two_step_engine()

    result = await engine.run("two_models", {})

    assert result.status == "completed"
    # Each factory was invoked with its profile's model.
    assert node_a._built_models == ["qwen2.5vl:3b"]
    assert node_b._built_models == ["gpt-5.5"]
    # Usage events record the per-step model.
    models_by_node = {e.node: e.model for e in result.usage.events}
    assert models_by_node["weak_step_node"] == "qwen2.5vl:3b"
    assert models_by_node["strong_step_node"] == "gpt-5.5"
    # Engine trace records the binding decision per step, requested + used in one place.
    bindings = {e.node: e.metadata for e in result.trace if e.decision == "model_binding"}
    assert bindings["weak_step"]["model_profile_requested"] == "local_vlm"
    assert bindings["weak_step"]["model_used"] == "qwen2.5vl:3b"
    assert bindings["strong_step"]["model_profile_requested"] == "strong_planner"
    assert bindings["strong_step"]["model_used"] == "gpt-5.5"


async def test_factory_structured_llm_node_emits_observation_details_without_wrapper():
    details = InMemoryDetailSink()
    node = _factory_node("observed_factory_node")

    async def observed(ctx, payload):
        return await node.run({"what": "observable work"})

    engine = (
        WorkflowEngineBuilder()
        .with_detail_sink(details)
        .with_detail_text_capture()
        .register_capability("observed", observed, kind="llm")
        .register_workflow(WorkflowBuilder("observed_factory").step("observed").build())
        .build()
    )

    result = await engine.run("observed_factory", {})

    assert result.status == "completed"
    prompt_detail = next(detail for detail in details.details if detail.kind == "rendered_prompt")
    response_detail = next(detail for detail in details.details if detail.kind == "llm_response")
    assert prompt_detail.redaction_state == "none"
    assert response_detail.redaction_state == "none"
    assert "observable work" in (prompt_detail.text or "")
    assert '"note": "done"' in (response_detail.text or "")


async def test_factory_structured_llm_node_emits_compact_trace_when_detail_capture_off():
    node = _factory_node("compact_factory_node")

    async def observed(ctx, payload):
        return await node.run({"what": "compact work"})

    engine = (
        WorkflowEngineBuilder()
        .register_capability("observed", observed, kind="llm")
        .register_workflow(WorkflowBuilder("compact_factory").step("observed").build())
        .build()
    )

    result = await engine.run("compact_factory", {})

    assert result.status == "completed"
    llm_events = [event for event in result.trace if event.phase in {"llm:request", "llm:response"}]
    assert [event.phase for event in llm_events] == ["llm:request", "llm:response"]
    assert [event.detail_refs for event in llm_events] == [[], []]
    blob = "\n".join(event.model_dump_json() for event in llm_events)
    assert "compact work" not in blob
    assert "prompt_digest" not in blob
    assert "response_digest" not in blob
    assert "detail_digest" not in blob


async def test_structured_llm_node_with_prompt_capture_wrapper_does_not_double_emit():
    trace = InMemoryTraceSink()
    details = InMemoryDetailSink()
    seen_metadata = []

    async def inner(request: LLMRequest) -> LLMResponse:
        seen_metadata.append(dict(request.metadata))
        return LLMResponse(text='{"note": "done"}', model="wrapped-unit", total_tokens=3)

    wrapped = PromptCapturingLLMClient(inner, trace, detail_sink=details, capture_text=True)
    node = StructuredLLMNode(
        name="wrapped_worker_node",
        config=SimpleNamespace(WORKFLOW_DEFAULT_MODEL="default-model"),
        output_model=Step,
        prompt_template="Do {what}.",
        input_variables=["what"],
        llm=wrapped,
    )

    async def observed(ctx, payload):
        return await node.run({"what": "wrapped work"})

    engine = (
        WorkflowEngineBuilder()
        .with_trace_sink(trace)
        .with_detail_sink(details)
        .with_detail_text_capture()
        .register_capability("observed", observed, kind="llm")
        .register_workflow(WorkflowBuilder("wrapped_worker").step("observed").build())
        .build()
    )

    result = await engine.run("wrapped_worker", {})

    assert result.status == "completed"
    assert all(not key.startswith("_ai_workflow_engine_") for key in seen_metadata[0])
    llm_events = [event for event in result.trace if event.phase in {"llm:request", "llm:response"}]
    assert [event.phase for event in llm_events] == ["llm:request", "llm:response"]
    llm_details = [detail for detail in details.details if detail.kind in {"rendered_prompt", "llm_response"}]
    assert [detail.kind for detail in llm_details] == ["rendered_prompt", "llm_response"]


async def test_absent_model_profile_keeps_default_behavior():
    node = _factory_node("plain_node")

    async def plain(ctx, payload):
        return await node.run({"what": "anything"})

    engine = (
        WorkflowEngineBuilder()
        .register_capability("plain", plain, kind="llm")
        .register_workflow(WorkflowBuilder("no_binding").step("plain").build())
        .build()
    )
    result = await engine.run("no_binding", {})

    assert result.status == "completed"
    assert node._built_models == ["default-model"]  # WORKFLOW_DEFAULT_MODEL fallback as today
    assert not any(e.decision == "model_binding" for e in result.trace)  # no trace noise


def test_unknown_profile_name_fails_loudly_at_registration():
    builder = WorkflowEngineBuilder()
    builder.register_capability("step_x", lambda ctx, p: p, kind="llm")
    builder.register_workflow(
        WorkflowBuilder("bad_binding").step("step_x", model_profile="does_not_exist").build()
    )
    with pytest.raises(ValueError, match="does_not_exist"):
        builder.build()


async def test_unknown_profile_name_fails_loudly_at_preflight_for_adhoc_definitions():
    engine = WorkflowEngineBuilder().build()
    engine.register_capability("step_x", lambda ctx, p: p, kind="llm")
    # Bypass register_workflow validation by running an ad-hoc definition object.
    definition = WorkflowBuilder("adhoc").step("step_x", model_profile="ghost_profile").build()
    engine.workflows[definition.workflow_id] = definition

    result = await engine.run("adhoc", {})

    assert result.status == "failed"
    assert "ghost_profile" in (result.error or "")
    assert result.node_results == []  # rejected before any execution


async def test_rc1_fixed_llm_client_with_model_profile_fails_loudly_at_preflight():
    fixed_node = StructuredLLMNode(
        name="fixed_node",
        config=object(),
        output_model=Step,
        prompt_template="Do {what}.",
        input_variables=["what"],
        llm=RecordingFactoryLLM("hardwired-model"),  # fixed client, no factory
    )

    async def fixed_step(ctx, payload):
        return await fixed_node.run({"what": "x"})

    # Expose the conflict statically, as a capability handler attribute.
    fixed_step.accepts_model_profile = fixed_node.accepts_model_profile
    assert fixed_step.accepts_model_profile is False

    builder = WorkflowEngineBuilder()
    builder.with_model_profile(LOCAL)
    builder.register_capability("fixed_step", fixed_step, kind="llm")
    builder.register_workflow(
        WorkflowBuilder("conflict").step("fixed_step", model_profile="local_vlm").build()
    )
    result = await builder.build().run("conflict", {})

    assert result.status == "failed"
    assert "fixed llm client" in (result.error or "")
    assert result.node_results == []  # preflight rejection, handler never ran


async def test_rc1_call_time_backstop_when_conflict_is_not_statically_visible():
    fixed_node = StructuredLLMNode(
        name="hidden_fixed_node",
        config=object(),
        output_model=Step,
        prompt_template="Do {what}.",
        input_variables=["what"],
        llm=RecordingFactoryLLM("hardwired-model"),
    )

    async def hidden_fixed_step(ctx, payload):  # plain wrapper: conflict invisible to preflight
        return await fixed_node.run({"what": "x"})

    builder = WorkflowEngineBuilder()
    builder.with_model_profile(LOCAL)
    builder.register_capability("hidden_fixed_step", hidden_fixed_step, kind="llm")
    builder.register_workflow(
        WorkflowBuilder("hidden_conflict").step("hidden_fixed_step", model_profile="local_vlm").build()
    )
    result = await builder.build().run("hidden_conflict", {})

    # Never a silent "client wins": the run fails loudly on first invocation.
    assert result.status == "failed"
    assert "fixed llm client" in (result.error or "")


async def test_plain_callable_receives_resolved_profile_on_request_metadata():
    seen_requests: list[LLMRequest] = []

    async def callable_client(request: LLMRequest) -> LLMResponse:
        seen_requests.append(request)
        return LLMResponse(text='{"note": "done"}', model="qwen2.5vl:3b")

    node = StructuredLLMNode(
        name="callable_node",
        config=object(),
        output_model=Step,
        prompt_template="Do {what}.",
        input_variables=["what"],
        llm=callable_client,
    )
    assert node.accepts_model_profile is True

    async def step(ctx, payload):
        return await node.run({"what": "x"})

    builder = WorkflowEngineBuilder()
    builder.with_model_profile(LOCAL)
    builder.register_capability("step_c", step, kind="llm")
    builder.register_workflow(WorkflowBuilder("callable_bound").step("step_c", model_profile="local_vlm").build())
    result = await builder.build().run("callable_bound", {})

    assert result.status == "completed"
    assert seen_requests[0].metadata["model_profile"]["model"] == "qwen2.5vl:3b"


# ======================================================================================
# C2 reproducer: model_binding must attribute the invocation's OWN usage events
# ======================================================================================


async def test_concurrent_fanout_items_attribute_their_own_models():
    """C0 reproducer (attribution): two concurrent fanout items under ONE bound profile
    emit usage events for DIFFERENT actual models (barrier-forced order: B emits first,
    A second, B returns last). Each item's model_binding trace must claim ITS OWN model.
    The shared-summary index-window inference cannot represent this interleaving: the
    late-returning B item reads the shared tail and claims model-a."""

    import asyncio

    from ai_workflow_engine.models import WorkflowUsageEvent
    from ai_workflow_engine.usage_events import record_usage_event

    a_done = asyncio.Event()
    b_emitted = asyncio.Event()

    async def worker(_context, item):
        role = item["role"]
        if role == "b":
            record_usage_event(
                WorkflowUsageEvent(node="fan", operation="chat", model="model-b", total_tokens=1)
            )
            b_emitted.set()
            await asyncio.wait_for(a_done.wait(), timeout=10)
            return {"ran": "b"}
        await asyncio.wait_for(b_emitted.wait(), timeout=10)
        record_usage_event(
            WorkflowUsageEvent(node="fan", operation="chat", model="model-a", total_tokens=1)
        )
        a_done.set()
        return {"ran": "a"}

    def seed(_context, _payload):
        return [{"role": "a"}, {"role": "b"}]

    builder = WorkflowEngineBuilder()
    builder.with_model_profile(
        ModelProfile(name="shared_router", provider="custom", model="router-default", temperature=0.0)
    )
    builder.register_capability("seed", seed, kind="deterministic")
    builder.register_capability("worker", worker, kind="llm")
    builder.register_workflow(
        WorkflowBuilder("attr_flow")
        .step("seed")
        .fanout(
            "fan",
            capability="worker",
            items_key="seed",
            max_parallel=2,
            model_profile="shared_router",
        )
        .build()
    )
    engine = builder.build()

    result = await engine.run("attr_flow", {})
    assert result.status == "completed"

    bindings = [
        event
        for event in result.trace
        if event.decision == "model_binding" and event.node == "fan"
    ]
    assert len(bindings) == 2, "one binding decision per fanout item"
    by_index = {
        event.metadata["fanout_item_index"]: event.metadata.get("model_used")
        for event in bindings
    }
    assert by_index == {0: "model-a", 1: "model-b"}, (
        "each item's binding must attribute the model IT invoked, keyed by deterministic "
        f"input-order identity — got {by_index}"
    )
    # reversed completion order (b returns last) must not disturb input-order results
    fan_output = [r.output for r in result.node_results if r.node_id == "fan"][-1]
    assert [entry["ran"] for entry in fan_output] == ["a", "b"], (
        "fanout outputs stay input-ordered regardless of completion order"
    )
    # aggregate money/usage truth is capture-independent: both events counted exactly once
    assert len(result.usage.events) == 2 and result.usage.total_tokens == 2
    assert sorted(e.model for e in result.usage.events) == ["model-a", "model-b"]


async def test_nested_bound_invocation_contributes_to_inner_and_outer_captures_once():
    """Nesting is INCLUSIVE for observation and single-count for accounting: an outer bound
    step whose capability internally runs a nested bound invocation sees the nested model in
    ITS OWN binding truth, the nested binding names itself, and the global summary counts
    the event exactly once."""

    from ai_workflow_engine.models import WorkflowUsageEvent
    from ai_workflow_engine.usage_events import record_usage_event

    engine_holder = {}

    async def inner(_context, _payload):
        record_usage_event(
            WorkflowUsageEvent(node="inner", operation="chat", model="nested-model", total_tokens=3)
        )
        return {"inner": True}

    async def outer(context, _payload):
        # a real nested BOUND invocation through the same public runtime door
        result = await engine_holder["engine"].runtime.invoke("inner", {}, context)
        return {"outer": True, "nested": result.output}

    builder = WorkflowEngineBuilder()
    builder.with_model_profile(
        ModelProfile(name="outer_profile", provider="custom", model="outer-model", temperature=0.0)
    )
    builder.register_capability("inner", inner, kind="llm")
    builder.register_capability("outer", outer, kind="llm")
    builder.register_workflow(
        WorkflowBuilder("nested_capture").step("outer", model_profile="outer_profile").build()
    )
    engine = builder.build()
    engine_holder["engine"] = engine

    result = await engine.run("nested_capture", {})
    assert result.status == "completed"

    bindings = {e.node: e.metadata for e in result.trace if e.decision == "model_binding"}
    assert bindings["outer"]["model_used"] == "nested-model", (
        "the outer capture must include nested provider work (inclusive nesting)"
    )
    assert "fanout_item_index" not in bindings["outer"], (
        "ordinary sequential bindings must not fabricate an item index"
    )
    nested_events = [e for e in result.usage.events if e.model == "nested-model"]
    assert len(nested_events) == 1, "capture observes; it must never double-account"
    assert result.usage.total_tokens == 3


async def test_concurrent_workflows_do_not_cross_attribute_models():
    """Two whole engine runs interleaving in one loop keep attribution run-local."""

    import asyncio

    from ai_workflow_engine.models import WorkflowUsageEvent
    from ai_workflow_engine.usage_events import record_usage_event

    gate_one = asyncio.Event()
    gate_two = asyncio.Event()

    def build(model_name: str, wait_for: asyncio.Event, then_set: asyncio.Event):
        async def worker(_context, _payload):
            record_usage_event(
                WorkflowUsageEvent(node="w", operation="chat", model=model_name, total_tokens=1)
            )
            then_set.set()
            await asyncio.wait_for(wait_for.wait(), timeout=10)
            return {"m": model_name}

        builder = WorkflowEngineBuilder()
        builder.with_model_profile(
            ModelProfile(name="p", provider="custom", model="p-default", temperature=0.0)
        )
        builder.register_capability("w", worker, kind="llm")
        builder.register_workflow(WorkflowBuilder("conc").step("w", model_profile="p").build())
        return builder.build()

    engine_one = build("model-one", gate_one, gate_two)   # emits, then waits for run 2
    engine_two = build("model-two", gate_two, gate_one)   # emits after run 1, releases it

    result_one, result_two = await asyncio.gather(
        engine_one.run("conc", {}), engine_two.run("conc", {})
    )
    assert result_one.status == "completed" and result_two.status == "completed"

    used_one = [e.metadata["model_used"] for e in result_one.trace if e.decision == "model_binding"]
    used_two = [e.metadata["model_used"] for e in result_two.trace if e.decision == "model_binding"]
    assert used_one == ["model-one"] and used_two == ["model-two"], (
        f"cross-run attribution leak: {used_one} / {used_two}"
    )


async def test_bound_invocation_with_no_usage_events_reports_model_used_none():
    """No event means model_used=None — profile.model is never substituted as observed
    truth, and failed capability results keep today's binding behavior."""

    def silent(_context, _payload):
        return {"quiet": True}

    def failing(_context, _payload):
        return CapabilityResult(status="failed", error="worker down", output=None)

    builder = WorkflowEngineBuilder()
    builder.with_model_profile(
        ModelProfile(name="p", provider="custom", model="p-default", temperature=0.0)
    )
    builder.register_capability("silent", silent, kind="llm")
    builder.register_capability("failing", failing, kind="llm")
    builder.register_workflow(WorkflowBuilder("quiet_flow").step("silent", model_profile="p").build())
    builder.register_workflow(
        WorkflowBuilder("fail_flow").step("failing", model_profile="p").build()
    )
    engine = builder.build()

    quiet = await engine.run("quiet_flow", {})
    assert quiet.status == "completed"
    quiet_binding = next(e for e in quiet.trace if e.decision == "model_binding")
    assert quiet_binding.metadata["model_used"] is None
    assert quiet_binding.metadata["model_profile_model"] == "p-default"

    failed = await engine.run("fail_flow", {})
    assert failed.status == "failed"
    failed_binding = next(e for e in failed.trace if e.decision == "model_binding")
    assert failed_binding.metadata["model_profile_requested"] == "p"
