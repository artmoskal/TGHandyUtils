"""Optional provider packs stay lazy and absent from the simple tools import path."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_tools_and_media_import_without_optional_provider_sdks(tmp_path):
    package_root = Path(__file__).resolve().parents[1]
    engine_root = package_root.parent / "ai_workflow_engine"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(package_root), str(engine_root), env.get("PYTHONPATH", "")]
    )

    script = """
import builtins

real_import = builtins.__import__
blocked = {"openai", "httpx"}

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] in blocked:
        raise ModuleNotFoundError(name)
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
import ai_workflow_tools
import ai_workflow_tools.media
import ai_workflow_tools.media.image_generation
import ai_workflow_tools.media.voice_generation
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
