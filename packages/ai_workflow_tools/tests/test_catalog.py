"""G-T: the tool catalog is complete, described, renderable, and registerable."""

from __future__ import annotations

import pytest
from ai_workflow_engine import WorkflowBuilder, WorkflowEngine
from ai_workflow_tools import (
    TOOL_CATALOG,
    register_from_catalog,
    render_tool_catalog,
    tool_entry,
)
from ai_workflow_tools.media.image_models import GeneratedImage, ImageGenerationRequest

pytestmark = pytest.mark.unit


class _FakeImageGenerator:
    def __init__(self) -> None:
        self.requests: list[ImageGenerationRequest] = []

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        self.requests.append(request)
        return GeneratedImage(
            path=f"{request.output_dir}/fake.png",
            basename="fake.png",
            provider="fake",
            model=request.model,
            size=request.size,
            quality=request.quality,
            output_format="png",
        )


def test_every_catalog_entry_has_a_real_description_and_side_effect_declaration():
    assert TOOL_CATALOG, "catalog must not be empty"
    for entry in TOOL_CATALOG:
        assert entry.description.strip(), f"catalog entry {entry.name} has no description"
        assert entry.kind in {"agent", "tool", "llm_client", "chat_model"}, entry.name
        assert isinstance(entry.side_effects, tuple), entry.name


def test_capability_entries_build_specs_with_matching_descriptions(tmp_path):
    cli = tool_entry("cli_agent").builder()
    assert cli.spec.description.strip()
    assert cli.spec.description == tool_entry("cli_agent").description

    image = tool_entry("image_generation").builder(generator=_FakeImageGenerator())
    assert image.spec.description == tool_entry("image_generation").description
    assert image.spec.side_effects == ["external_call"]


def test_render_tool_catalog_shows_every_tool_and_the_presets():
    rendered = render_tool_catalog()
    for entry in TOOL_CATALOG:
        assert entry.name in rendered
        assert entry.description.splitlines()[0][:40] in rendered
    for preset in ("READ_ONLY", "WEB", "INVESTIGATION", "NO_TOOLS", "DEFAULT_AGENT_TOOLS"):
        assert preset in rendered
    assert "Bash" in rendered  # the generous default is visible, not hidden


def test_catalog_completeness_guard_every_public_tool_symbol_is_covered():
    # A new public tool that ships without a catalog entry must FAIL here by name.
    import ai_workflow_tools.chatgpt_browser as chatgpt_browser
    import ai_workflow_tools.cli_agents as cli_agents
    import ai_workflow_tools.media as media
    import ai_workflow_tools.media.capabilities as media_capabilities
    import ai_workflow_tools.providers.openai_compatible as openai_compatible
    import ai_workflow_tools.toolsets as toolsets

    public_symbols = (
        set(cli_agents.__all__)
        | set(media.__all__)
        | set(media_capabilities.__all__)
        | set(chatgpt_browser.__all__)
        | set(openai_compatible.__all__)
        | set(toolsets.__all__)
    )
    covered = set()
    for entry in TOOL_CATALOG:
        covered.update(entry.covers)

    missing = sorted(public_symbols - covered)
    assert not missing, f"public tool symbols missing from TOOL_CATALOG covers: {missing}"


async def test_register_from_catalog_then_engine_run_works(tmp_path):
    from ai_workflow_engine.models import SafetyPolicy, WorkflowProfile

    engine = WorkflowEngine()
    generator = _FakeImageGenerator()
    built = register_from_catalog(engine, "image_generation", generator=generator)
    assert built.spec.name == "image_generation"

    # The entry declares external_call honestly, so the product must ALLOW it —
    # the engine's side-effect gate refuses otherwise (that refusal is by design).
    engine.register_workflow(
        WorkflowBuilder("media_smoke").step("image_generation").build(),
        profile=WorkflowProfile(
            workflow_type="media_smoke",
            safety=SafetyPolicy(allowed_side_effects=["external_call"]),
        ),
    )
    result = await engine.run(
        "media_smoke",
        {"prompt": "a red circle", "output_dir": str(tmp_path)},
    )

    assert result.status == "completed"
    assert generator.requests and generator.requests[0].prompt == "a red circle"


def test_register_from_catalog_rejects_client_entries_with_guidance():
    engine = WorkflowEngine()
    with pytest.raises(ValueError, match="LLM node / model wiring"):
        register_from_catalog(engine, "console_llm")


def test_unknown_tool_name_fails_loudly_listing_the_catalog():
    with pytest.raises(ValueError, match="cli_agent"):
        tool_entry("definitely_not_a_tool")
