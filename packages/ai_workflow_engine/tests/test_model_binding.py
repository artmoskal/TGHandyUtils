"""G3 — declarative per-node model binding (AC-G3) incl. RC1 fixed-client conflict."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ai_workflow_engine import (
    LLMRequest,
    LLMResponse,
    StructuredLLMNode,
    WorkflowBuilder,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.models import ModelProfile

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