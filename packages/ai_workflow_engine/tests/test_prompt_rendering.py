"""P1/P2 prompt rendering: strict file-backed prompts + StructuredLLMNode ref mode.
Plus A4: engine-owned observation-bundle lifecycle via engine.run(observation_bundle=...)."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ai_workflow_engine import (
    PromptRef,
    PromptRenderError,
    PromptRenderService,
    StructuredLLMNode,
    WorkflowBuilder,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.observation_bundle import open_observation_run_bundle

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- P1: render service
def _service(tmp_path, files: dict) -> PromptRenderService:
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return PromptRenderService(tmp_path)


def test_format_render_succeeds_with_all_variables(tmp_path):
    service = _service(tmp_path, {"greet.txt": "Hello {name}, goal: {goal}"})
    result = service.render(PromptRef(path="greet.txt"), {"name": "A", "goal": "cards"})
    assert result.text == "Hello A, goal: cards"
    assert result.variables_used == ["goal", "name"]
    assert result.template_digest and result.rendered_digest


def test_format_render_missing_variable_fails_loudly_before_any_call(tmp_path):
    service = _service(tmp_path, {"greet.txt": "Hello {name}, goal: {goal}"})
    with pytest.raises(PromptRenderError, match="missing variable.*goal"):
        service.render(PromptRef(path="greet.txt"), {"name": "A"})


def test_prompt_path_escape_is_rejected(tmp_path):
    service = _service(tmp_path, {"greet.txt": "hi"})
    with pytest.raises(ValueError, match="escapes prompt root"):
        service.render(PromptRef(path="../outside.txt"), {})


def test_declared_variables_are_validated_against_the_template(tmp_path):
    service = _service(tmp_path, {"greet.txt": "Hello {name}"})
    with pytest.raises(PromptRenderError, match="never uses.*ghost"):
        service.load_template(PromptRef(path="greet.txt", variables=["name", "ghost"]))


def test_jinja_render_strict_and_loud(tmp_path):
    pytest.importorskip("jinja2")
    service = _service(tmp_path, {"j.j2": "Hi {{ name }}{% if extra %} ({{ extra }}){% endif %}"})
    ok = service.render(PromptRef(path="j.j2", renderer="jinja"), {"name": "A", "extra": ""})
    assert ok.text == "Hi A"
    with pytest.raises(PromptRenderError, match="missing variable"):
        service.render(PromptRef(path="j.j2", renderer="jinja"), {"extra": ""})


# ---------------------------------------------------------------- P2: node ref mode
class _Decision(BaseModel):
    verdict: str


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.response, usage_metadata=None, response_metadata={})


def test_structured_llm_node_renders_from_prompt_ref(tmp_path):
    service = _service(tmp_path, {"decide.txt": "Judge {subject}.\n{format_instructions}"})
    llm = _FakeLLM(json.dumps({"verdict": "good"}))
    node = StructuredLLMNode(
        name="ref_node",
        config=object(),
        output_model=_Decision,
        prompt_ref=PromptRef(path="decide.txt"),
        prompt_renderer=service,
        llm=llm,
    )

    decision = asyncio.run(node.run({"subject": "the goblin plan"}))

    assert decision.verdict == "good"
    sent = llm.calls[0][-1].content
    assert "Judge the goblin plan." in sent
    assert "verdict" in sent  # format_instructions were injected


def test_structured_llm_node_ref_mode_missing_variable_is_loud(tmp_path):
    service = _service(tmp_path, {"decide.txt": "Judge {subject}.\n{format_instructions}"})
    llm = _FakeLLM(json.dumps({"verdict": "good"}))
    node = StructuredLLMNode(
        name="ref_node",
        config=object(),
        output_model=_Decision,
        prompt_ref=PromptRef(path="decide.txt"),
        prompt_renderer=service,
        llm=llm,
    )

    with pytest.raises(PromptRenderError, match="missing variable"):
        asyncio.run(node.run({}))
    assert llm.calls == []  # failed BEFORE any model call


def test_structured_llm_node_rejects_ref_and_raw_mix(tmp_path):
    service = _service(tmp_path, {"decide.txt": "x {format_instructions}"})
    with pytest.raises(ValueError, match="mutually exclusive"):
        StructuredLLMNode(
            name="bad_node",
            config=object(),
            output_model=_Decision,
            prompt_template="raw {format_instructions}",
            prompt_ref=PromptRef(path="decide.txt"),
            prompt_renderer=service,
            llm=_FakeLLM("{}"),
        )


def test_structured_llm_node_ref_requires_renderer(tmp_path):
    with pytest.raises(ValueError, match="prompt_renderer"):
        StructuredLLMNode(
            name="bad_node",
            config=object(),
            output_model=_Decision,
            prompt_ref=PromptRef(path="decide.txt"),
            llm=_FakeLLM("{}"),
        )


def test_engine_prompt_renderer_property_is_loud_without_root():
    engine = WorkflowEngineBuilder().build()
    with pytest.raises(ValueError, match="prompt root"):
        _ = engine.prompt_renderer


# ---------------------------------------------------------------- A4: engine-owned bundle
def _noop(context, payload):
    return {"ok": True}


def test_engine_run_finalizes_attached_bundle_with_envelope_status(tmp_path):
    builder = WorkflowEngineBuilder()
    builder.register_capability("solo", _noop, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("bundled_flow").step("solo").build())
    engine = builder.build()
    bundle = open_observation_run_bundle(tmp_path, "run-a4")

    result = asyncio.run(engine.run("bundled_flow", {"x": 1}, observation_bundle=bundle))

    assert result.status == "completed"
    meta = json.loads((bundle.path / "meta.json").read_text())
    assert meta["status"] == "completed"


def test_terminal_status_hook_overrides_the_archived_status(tmp_path):
    builder = WorkflowEngineBuilder()
    builder.register_capability("solo", _noop, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("hooked_flow").step("solo").build())
    engine = builder.build()
    bundle = open_observation_run_bundle(tmp_path, "run-hook")

    result = asyncio.run(
        engine.run(
            "hooked_flow",
            {"x": 1},
            observation_bundle=bundle,
            terminal_status=lambda envelope: "failed",  # product post-validation says no
        )
    )

    # B-post2: the returned result and the durable record must agree — both failed.
    assert result.status == "failed"
    meta = json.loads((bundle.path / "meta.json").read_text())
    assert meta["status"] == "failed"


def test_raising_terminal_status_hook_still_finalizes_the_bundle(tmp_path):
    builder = WorkflowEngineBuilder()
    builder.register_capability("solo", _noop, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("hook_boom_flow").step("solo").build())
    engine = builder.build()
    bundle = open_observation_run_bundle(tmp_path, "run-hook-boom")

    def exploding_hook(envelope):
        raise RuntimeError("post-validation crashed")

    with pytest.raises(RuntimeError, match="post-validation crashed"):
        asyncio.run(
            engine.run(
                "hook_boom_flow", {"x": 1},
                observation_bundle=bundle, terminal_status=exploding_hook,
            )
        )

    meta = json.loads((bundle.path / "meta.json").read_text())
    assert meta["status"] == "failed"  # never left unfinalized


def test_invalid_terminal_status_override_is_loud(tmp_path):
    builder = WorkflowEngineBuilder()
    builder.register_capability("solo", _noop, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("hook_bad_flow").step("solo").build())
    engine = builder.build()
    bundle = open_observation_run_bundle(tmp_path, "run-hook-bad")

    with pytest.raises(ValueError, match="invalid status"):
        asyncio.run(
            engine.run(
                "hook_bad_flow", {"x": 1},
                observation_bundle=bundle, terminal_status=lambda e: "hollow",
            )
        )
    meta = json.loads((bundle.path / "meta.json").read_text())
    assert meta["status"] == "failed"


def test_engine_run_finalizes_bundle_failed_when_the_run_raises(tmp_path):
    class _ExplodingRunner:
        config = None
        usage_sink = None

        async def run(self, *args, **kwargs):
            raise RuntimeError("boom")

    builder = WorkflowEngineBuilder()
    builder.register_capability("solo", _noop, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("raising_flow").step("solo").build())
    engine = builder.build()
    engine.executor.runner = _ExplodingRunner()
    bundle = open_observation_run_bundle(tmp_path, "run-raise")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(engine.run("raising_flow", {"x": 1}, observation_bundle=bundle))

    meta = json.loads((bundle.path / "meta.json").read_text())
    assert meta["status"] == "failed"
