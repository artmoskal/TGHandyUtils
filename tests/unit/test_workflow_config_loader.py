"""Unit tests for reusable workflow config loading."""

from pathlib import Path

import pytest

from ai_workflow_engine.config_loader import (
    WorkflowConfigError,
    assert_no_config_secrets,
    load_workflow_config,
)
from ai_workflow_engine.engine import CapabilityRegistry, RuntimePlanCompiler
from ai_workflow_engine.models import CapabilitySpec

pytestmark = pytest.mark.unit


def _write_yaml(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_workflow_config_loader_loads_yaml_profile_models_and_settings(tmp_path):
    config_path = _write_yaml(
        tmp_path / "workflow.yaml",
        """
workflow:
  profile_id: anki.test
  workflow_type: anki_generation
  requested_capabilities:
    - source_package
    - scenario_planner
  fail_mode: fallback
  constraints:
    card_count_policy: prefer_fewer_cards
  scheduling:
    mode: run_immediately
  limits:
    max_steps: 12
    max_retries: 2
    max_estimated_usd: 5
models:
  decision:
    provider: openai
    model: gpt-5.4-mini
    temperature: 0
  scenario:
    provider: anthropic
    model: claude-sonnet-4-5
    timeout_s: 45
settings:
  image_provider: openai
  quality_evaluation_enabled: true
""",
    )

    bundle = load_workflow_config([config_path], env={})

    assert bundle.profile.profile_id == "anki.test"
    assert bundle.profile.workflow_type == "anki_generation"
    assert bundle.profile.requested_capabilities == ["source_package", "scenario_planner"]
    assert bundle.profile.limits.max_steps == 12
    assert bundle.model_for("decision").model == "gpt-5.4-mini"
    assert bundle.model_for("scenario").provider == "anthropic"
    assert bundle.settings["image_provider"] == "openai"
    assert bundle.warnings == []


def test_workflow_config_loader_merges_defaults_file_and_env_overrides(tmp_path):
    defaults = {
        "workflow": {
            "profile_id": "default",
            "workflow_type": "calendar_builder",
            "limits": {"max_steps": 4, "max_parallel_children": 2},
            "constraints": {"tone": "plain"},
        },
        "models": {
            "decision": {"provider": "openai", "model": "gpt-5.4-nano"},
        },
        "settings": {"image_provider": "none"},
    }
    config_path = _write_yaml(
        tmp_path / "profile.yaml",
        """
workflow:
  profile_id: app.profile
  limits:
    max_steps: 8
models:
  decision:
    model: gpt-5.4-mini
settings:
  image_provider: gemini
""",
    )
    env = {
        "WORKFLOW_MAX_STEPS": "16",
        "WORKFLOW_MODEL_DECISION": "gpt-5.4",
        "APP_SETTING_IMAGE_PROVIDER": "openai",
        "APP_CONSTRAINT_HUMOR": "true",
    }

    bundle = load_workflow_config(
        [config_path],
        defaults=defaults,
        env=env,
        env_prefixes=("WORKFLOW", "APP"),
    )

    assert bundle.profile.profile_id == "app.profile"
    assert bundle.profile.workflow_type == "calendar_builder"
    assert bundle.profile.limits.max_steps == 16
    assert bundle.profile.limits.max_parallel_children == 2
    assert bundle.profile.constraints["tone"] == "plain"
    assert bundle.profile.constraints["humor"] is True
    assert bundle.model_for("decision").model == "gpt-5.4"
    assert bundle.settings["image_provider"] == "openai"


def test_workflow_config_loader_supports_explicit_product_override_map(tmp_path):
    config_path = _write_yaml(
        tmp_path / "profile.yaml",
        """
workflow:
  workflow_type: qa_review
models:
  evaluator:
    provider: openai
    model: gpt-5.4-mini
settings:
  report_sink: local
""",
    )

    bundle = load_workflow_config(
        [config_path],
        env={"APP_REPORT_SINK": "github"},
        env_prefixes=("APP",),
        override_map={"REPORT_SINK": ("settings", "report_sink")},
    )

    assert bundle.settings["report_sink"] == "github"


def test_workflow_config_loader_rejects_secrets_in_files(tmp_path):
    config_path = _write_yaml(
        tmp_path / "unsafe.yaml",
        """
workflow:
  workflow_type: unsafe
settings:
  openai_api_key: sk-test-secret-value-1234567890
""",
    )

    with pytest.raises(WorkflowConfigError, match="secret-like key"):
        load_workflow_config([config_path], env={})


def test_assert_no_config_secrets_rejects_secret_like_values():
    with pytest.raises(WorkflowConfigError, match="secret-like value"):
        assert_no_config_secrets(
            {"workflow": {"workflow_type": "unsafe"}, "settings": {"raw": "sk-test-secret-value-1234567890"}},
            source="unit",
        )


def test_workflow_config_loader_warns_without_crashing_on_unknown_keys(tmp_path):
    config_path = _write_yaml(
        tmp_path / "profile.yaml",
        """
workflow:
  workflow_type: portable
  unknown_knob: true
unknown_top:
  value: 1
""",
    )

    bundle = load_workflow_config([config_path], env={})

    assert bundle.profile.workflow_type == "portable"
    assert "unknown workflow key ignored by profile model: unknown_knob" in "\n".join(bundle.warnings)
    assert "unknown top-level key ignored by engine loader: unknown_top" in "\n".join(bundle.warnings)


def test_loaded_profile_compiles_to_runtime_plan_with_registry_warnings(tmp_path):
    config_path = _write_yaml(
        tmp_path / "profile.yaml",
        """
workflow:
  profile_id: portable
  workflow_type: cross_project
  requested_capabilities:
    - qa_agent
    - missing_tool
  constraints:
    supported: true
    unsupported: true
""",
    )
    bundle = load_workflow_config([config_path], env={})
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(name="qa_agent", kind="agent"), lambda _context, payload: payload)

    plan = RuntimePlanCompiler(supported_constraint_keys={"supported"}).compile(bundle.profile, registry)

    assert plan.profile_id == "portable"
    assert plan.capability_names == ["qa_agent", "missing_tool"]
    assert "Unknown capability requested: missing_tool" in plan.warnings
    assert "Unsupported constraint key: unsupported" in plan.warnings
