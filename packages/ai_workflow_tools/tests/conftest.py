from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_workflow_engine.models import CapabilityContext, RuntimePlan, SafetyPolicy, WorkflowGoal, WorkflowRunContext


@pytest.fixture
def capability_context():
    plan = RuntimePlan(
        workflow_type="tools_test",
        safety=SafetyPolicy(allowed_side_effects=[]),
    )
    return CapabilityContext(
        goal=WorkflowGoal(workflow_type="tools_test", objective="test tool"),
        run_context=WorkflowRunContext(workflow_id="tools-test-123", workflow_type="tools_test"),
        plan=plan,
    )


@pytest.fixture
def fake_cli_path() -> Path:
    return Path(__file__).with_name("fake_cli.py")


@pytest.fixture
def run_fake_cli(fake_cli_path):
    def run(*, mode: str, tmp_path: Path, input_text: str = "", args: list[str] | None = None):
        record_path = tmp_path / "record.json"
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)
        env = {
            **os.environ,
            "FAKE_CLI_MODE": mode,
            "FAKE_CLI_RECORD": str(record_path),
            "FAKE_CLI_WORKSPACE": str(workspace),
            "FAKE_CLI_SLEEP_S": "0.01",
        }
        completed = subprocess.run(
            [sys.executable, str(fake_cli_path), *(args or [])],
            input=input_text,
            text=True,
            capture_output=True,
            env=env,
            # 20s: generous for a trivial subprocess, immune to parallel docker-build
            # load (this flaked 3x on 2026-07-12 at 2s, passing isolated every time)
            timeout=20,
            check=False,
        )
        return completed, record_path, workspace

    return run
