"""Unit tests for application config backed by workflow profiles."""

import json
import os
import subprocess
import sys
import ast
from pathlib import Path

import pytest
import yaml

from ai_workflow_engine.config_loader import assert_no_config_secrets

pytestmark = pytest.mark.unit


def _run_config_probe(extra_env=None):
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("ANKI_", "WORKFLOW_")):
            env.pop(key)
    env.pop("OPENAI_PROMPT_CACHE_KEY_PREFIX", None)
    env.pop("OPENAI_PROMPT_CACHE_RETENTION", None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env.update(extra_env or {})
    env["PYTHONPATH"] = os.pathsep.join([str(repo_root), env.get("PYTHONPATH", "")])

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from config import Config; "
                "print(json.dumps({"
                "'card_model': Config.ANKI_CARD_MODEL, "
                "'image_provider': Config.ANKI_IMAGE_PROVIDER, "
                "'image_model': Config.ANKI_IMAGE_MODEL, "
                "'style_ref': Config.ANKI_STYLE_DESIGN_REFERENCE_IMAGE, "
                "'voice_id': Config.ANKI_ELEVENLABS_VOICE_ID, "
                "'max_text_calls': Config.WORKFLOW_MAX_TEXT_CALLS_PER_RUN"
                "}))"
            ),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_application_config_reads_non_secret_defaults_from_yaml():
    data = _run_config_probe()

    assert data["card_model"] == "gpt-5.4-mini"
    # ChatGPT-browser subscription is the profile default since FR-1 (reference images)
    # shipped; the browser service exposes no model choice — 'chatgpt-web' is the label.
    assert data["image_provider"] == "chatgpt"
    assert data["image_model"] == "chatgpt-web"
    assert data["style_ref"] == "assets/anki/ppla-design-reference-v2.png"
    assert data["voice_id"] == "bBNhdwrIjl4fcVYiRbT2"
    assert data["max_text_calls"] == 16


def test_application_config_accepts_product_transition_and_generic_overrides():
    data = _run_config_probe(
        {
            "ANKI_CARD_MODEL": "gpt-5.4",
            "ANKI_IMAGE_PROVIDER": "gemini",
            "WORKFLOW_MAX_TEXT_CALLS_PER_RUN": "3",
        }
    )

    assert data["card_model"] == "gpt-5.4"
    assert data["image_provider"] == "gemini"
    assert data["image_model"] == "gemini-3.1-flash-image"
    assert data["max_text_calls"] == 3


def test_committed_workflow_config_files_contain_no_secrets():
    repo_root = Path(__file__).resolve().parents[2]

    for path in sorted((repo_root / "config").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert_no_config_secrets(data, source=str(path))


def test_application_config_direct_env_reads_are_secrets_or_deployment_knobs():
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / "config.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    direct_env_keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "getenv"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            direct_env_keys.add(node.args[0].value)
    allowed = {
        "TELEGRAM_BOT_TOKEN",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_PROMPT_CACHE_KEY_PREFIX",
        "OPENAI_PROMPT_CACHE_RETENTION",
        "ELEVENLABS_API_KEY",
        "DATABASE_PATH",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "LOG_FILE",
        "ANKI_OBSERVATION_DIR",
        "OBSERVATION_DIR",
        "LLM_BASE_URL",
        # Deployment knob: per-deployment endpoint of the ChatGPT-browser service on the
        # always-on box (Tailscale address). No default — selecting the provider without
        # it fails loudly.
        "CHATGPT_BROWSER_API_URL",
        # Secret consumed by the secured browser transport. The application config owns
        # environment sourcing; downstream factories receive only the resolved value.
        "CHATGPT_BROWSER_API_TOKEN",
    }

    assert direct_env_keys == allowed


def _pytest_mark_names(node):
    if isinstance(node, ast.Attribute):
        value = node.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "mark"
            and isinstance(value.value, ast.Name)
            and value.value.id == "pytest"
        ):
            return {node.attr}
    if isinstance(node, ast.Call):
        return _pytest_mark_names(node.func)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        names = set()
        for item in node.elts:
            names.update(_pytest_mark_names(item))
        return names
    return set()


def test_test_files_do_not_mix_unit_and_integration_markers():
    """A test item selected by both tiers makes wrapper results untrustworthy."""

    repo_root = Path(__file__).resolve().parents[2]
    roots = [
        repo_root / "tests",
        repo_root / "packages" / "ai_workflow_engine" / "tests",
        repo_root / "packages" / "ai_workflow_tools" / "tests",
        repo_root / "packages" / "ai_workflow_viewer" / "tests",
    ]
    offenders = []
    for root in roots:
        for path in sorted(root.rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            module_marks = set()
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                if any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets):
                    module_marks.update(_pytest_mark_names(node.value))
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                marks = set(module_marks)
                for decorator in node.decorator_list:
                    marks.update(_pytest_mark_names(decorator))
                if {"unit", "integration"} <= marks:
                    offenders.append(f"{path.relative_to(repo_root)}::{node.name}")

    assert not offenders, "tests marked as both unit and integration:\n" + "\n".join(offenders)


def test_every_test_carries_a_tier_marker():
    """The OTHER half of the marker hazard (bit us 2026-07-03): a test with NO tier
    marker is silently deselected from BOTH tiers and never runs anywhere."""

    repo_root = Path(__file__).resolve().parents[2]
    roots = [
        repo_root / "tests",
        repo_root / "packages" / "ai_workflow_engine" / "tests",
        repo_root / "packages" / "ai_workflow_tools" / "tests",
        repo_root / "packages" / "ai_workflow_viewer" / "tests",
    ]
    tier_marks = {"unit", "integration", "api"}
    offenders = []
    for root in roots:
        for path in sorted(root.rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            module_marks = set()
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                if any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets):
                    module_marks.update(_pytest_mark_names(node.value))
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not node.name.startswith("test_"):
                    continue
                marks = set(module_marks)
                for decorator in node.decorator_list:
                    marks.update(_pytest_mark_names(decorator))
                if not (tier_marks & marks):
                    offenders.append(f"{path.relative_to(repo_root)}::{node.name}")

    assert not offenders, (
        "tests with NO tier marker (never selected by any tier):\n" + "\n".join(offenders)
    )


def test_test_wrapper_preserves_pytest_args_as_array():
    """The wrapper must pass pytest args like ``-k 'a or b'`` without word-splitting."""

    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "test.sh"
    text = script.read_text(encoding="utf-8")

    assert 'PYTEST_EXTRA_ARGS=("$@")' in text
    assert 'args+=("$@")' in text
    assert "python -m pytest $test_files" not in text
    subprocess.run(["bash", "-n", str(script)], check=True)


def _run_copied_test_wrapper(tmp_path: Path, *, dotenv: str | None) -> tuple[Path, str]:
    repo_root = tmp_path / "repo"
    infra = repo_root / "infra"
    bin_dir = tmp_path / "bin"
    trace = tmp_path / "compose-env-paths.txt"
    infra.mkdir(parents=True)
    bin_dir.mkdir()
    source_root = Path(__file__).resolve().parents[2]
    (repo_root / "test.sh").write_bytes((source_root / "test.sh").read_bytes())
    (infra / "docker-compose.test.yml").write_bytes(
        (source_root / "infra" / "docker-compose.test.yml").read_bytes()
    )
    if dotenv is not None:
        (repo_root / ".env").write_text(dotenv, encoding="utf-8")

    fake_compose = bin_dir / "docker-compose"
    fake_compose.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                'test -f "$TG_TEST_ENV_FILE"',
                'printf "%s\\n" "$TG_TEST_ENV_FILE" >> "$TEST_ENV_TRACE"',
                "exit 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake_compose.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "TEST_ENV_TRACE": str(trace),
    }
    completed = subprocess.run(
        ["bash", str(repo_root / "test.sh"), "unit"],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    paths = {Path(value) for value in trace.read_text(encoding="utf-8").splitlines()}
    assert len(paths) == 1
    return paths.pop(), completed.stdout


def test_test_wrapper_uses_and_removes_temporary_env_outside_clean_checkout(tmp_path: Path):
    selected, stdout = _run_copied_test_wrapper(tmp_path, dotenv=None)
    copied_repo = tmp_path / "repo"
    repo_root = Path(__file__).resolve().parents[2]
    operations = (
        repo_root / "packages" / "ai_workflow_engine" / "docs" / "operations.md"
    ).read_text(encoding="utf-8")

    assert selected.is_absolute()
    assert copied_repo not in selected.parents
    assert not selected.exists()
    assert not (copied_repo / ".env").exists()
    assert "temporary empty test environment" in stdout
    assert "creates a permission-restricted" in operations
    assert "no operator-created `.env`" in " ".join(operations.split())


def test_test_wrapper_preserves_real_dotenv_without_deleting_it(tmp_path: Path):
    dotenv = "CHATGPT_BROWSER_API_TOKEN=not-a-real-secret\n"
    selected, stdout = _run_copied_test_wrapper(tmp_path, dotenv=dotenv)
    expected = tmp_path / "repo" / ".env"

    assert selected == expected
    assert expected.read_text(encoding="utf-8") == dotenv
    assert "temporary empty test environment" not in stdout


@pytest.mark.unit
def test_engine_runtime_limits_is_the_one_app_budget_boundary():
    """R1: the application normalizes its ceilings ONCE into typed RuntimeLimits.
    Real Config defaults must produce the historical ceilings (text=16, image=1, USD
    unlimited via the 0-means-no-cap convention)."""
    from config import Config, engine_runtime_limits

    limits = engine_runtime_limits(Config)

    assert limits.max_text_calls == 16
    assert limits.max_image_calls == 1
    assert limits.max_estimated_usd is None  # config default 0 == "no cap" -> typed None
    # absent app fields stay unbounded rather than inventing ceilings
    assert limits.max_worker_calls is None
    assert limits.max_input_tokens_per_call is None


@pytest.mark.unit
def test_engine_runtime_limits_is_mock_safe_and_never_invents_caps():
    from unittest.mock import Mock

    from config import engine_runtime_limits

    limits = engine_runtime_limits(Mock())

    assert limits.max_text_calls is None
    assert limits.max_estimated_usd is None
    assert engine_runtime_limits(None).max_text_calls is None


@pytest.mark.unit
def test_reminder_profile_restores_run_ceilings_from_the_app_boundary():
    """R1 regression (the review's #1 finding): the reminder graph lost its per-run
    text/image ceilings when the host-config duck-read was deleted. The profile must now
    carry them from the typed app boundary, and its structural limits stay reminder-shaped."""
    from types import SimpleNamespace
    from unittest.mock import Mock

    from config import Config
    from services.content.reminder_generation_graph import ReminderGenerationGraph

    graph = ReminderGenerationGraph(
        parsing_service=SimpleNamespace(config=Config), task_service=Mock()
    )
    profile = graph._workflow_profile()

    assert profile.limits.max_text_calls == 16
    assert profile.limits.max_image_calls == 1
    assert profile.limits.max_estimated_usd is None
    assert profile.limits.max_steps == 8
    assert profile.limits.max_retries == 0
    assert profile.limits.max_parallel_children == 1

    # behavior probe: the registered engine profile is what the runner executes under
    engine = graph._engine()
    registered = engine._profiles["reminder_generation"]
    assert registered.limits.max_text_calls == 16

    from ai_workflow_engine.budget import budget_from_limits

    assert budget_from_limits(registered.limits).max_text_calls == 16


@pytest.mark.unit
def test_engine_runtime_limits_rejects_malformed_budget_settings_loudly():
    """R13 (recheck finding #5): a typo'd ceiling must never silently mean 'no cap'.
    Negative, non-finite, boolean, and unparseable values raise ValueError naming the
    setting; only genuinely-absent shapes (None, empty string, exact 0) mean no cap."""

    from types import SimpleNamespace

    from config import engine_runtime_limits

    def cfg(**overrides):
        return SimpleNamespace(**overrides)

    for bad in (-5, "-5", "abc", "1.5", True, False):
        with pytest.raises(ValueError, match="WORKFLOW_MAX_TEXT_CALLS_PER_RUN"):
            engine_runtime_limits(cfg(WORKFLOW_MAX_TEXT_CALLS_PER_RUN=bad))

    for bad in (-0.5, "nan", "inf", float("nan"), float("inf"), "abc", True):
        with pytest.raises(ValueError, match="WORKFLOW_MAX_ESTIMATED_USD_PER_RUN"):
            engine_runtime_limits(cfg(WORKFLOW_MAX_ESTIMATED_USD_PER_RUN=bad))


@pytest.mark.unit
def test_engine_runtime_limits_keeps_legacy_no_cap_shapes_lenient():
    """R13 pair: the legacy 'not configured' shapes still mean no cap — exact 0 (the app's
    historical convention), None, and empty string; valid positives pass through typed."""

    from types import SimpleNamespace

    from config import engine_runtime_limits

    def cfg(**overrides):
        return SimpleNamespace(**overrides)

    for none_like in (0, "0", None, "", "  "):
        limits = engine_runtime_limits(cfg(WORKFLOW_MAX_TEXT_CALLS_PER_RUN=none_like))
        assert limits.max_text_calls is None, f"{none_like!r} must mean 'no cap'"

    assert (
        engine_runtime_limits(cfg(WORKFLOW_MAX_TEXT_CALLS_PER_RUN="12")).max_text_calls == 12
    )
    assert (
        engine_runtime_limits(
            cfg(WORKFLOW_MAX_ESTIMATED_USD_PER_RUN="0.25")
        ).max_estimated_usd
        == 0.25
    )
