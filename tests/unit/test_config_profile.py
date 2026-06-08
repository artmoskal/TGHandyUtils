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
    assert data["image_provider"] == "openai"
    assert data["image_model"] == "gpt-image-2"
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
    }

    assert direct_env_keys == allowed
