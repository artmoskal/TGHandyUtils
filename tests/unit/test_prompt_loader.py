"""Unit tests for repository prompt template loading."""

import pytest

from ai_workflow_engine.prompt_loader import PromptTemplateLoader, load_prompt_template


pytestmark = pytest.mark.unit


def test_prompt_loader_loads_required_workflow_templates():
    required = [
        "anki/card_set.static.prompt",
        "anki/visual_scenario.static.prompt",
        "anki/image_style_prefix.prompt",
        "anki/quality.static.prompt",
        "workflow/supervisor.static.prompt",
        "workflow/structured_llm.repair.prompt",
        "tasks/task_create.static.prompt",
    ]

    for relative_path in required:
        assert load_prompt_template(relative_path).strip()


def test_prompt_loader_dedents_prompt_files():
    prompt = load_prompt_template("tasks/task_create.static.prompt")

    assert prompt.startswith("You are a multilingual task creation assistant")


def test_prompt_loader_rejects_path_escape():
    with pytest.raises(ValueError):
        load_prompt_template("../config.py")


def test_prompt_template_loader_accepts_custom_root(tmp_path):
    prompt_root = tmp_path / "project_prompts"
    prompt_root.mkdir()
    (prompt_root / "toy.prompt").write_text("  Hello {name}\n", encoding="utf-8")

    loader = PromptTemplateLoader(prompt_root)

    assert loader.load("toy.prompt") == "Hello {name}"


def test_default_prompt_loader_accepts_custom_root(tmp_path):
    prompt_root = tmp_path / "project_prompts"
    prompt_root.mkdir()
    (prompt_root / "toy.prompt").write_text("Reusable prompt", encoding="utf-8")

    assert load_prompt_template("toy.prompt", root=prompt_root) == "Reusable prompt"
