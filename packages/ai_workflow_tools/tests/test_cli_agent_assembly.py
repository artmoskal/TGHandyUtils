from __future__ import annotations

import json

import pytest
from ai_workflow_engine.models import EvidenceRef

from ai_workflow_tools.cli_agents import (
    CliAgentRequest,
    CliAgentResult,
    McpServerConfig,
    build_cli_agent_invocation,
    claude_p,
    codex_exec,
)

pytestmark = pytest.mark.unit


def test_cli_agent_models_include_spec_defaults(tmp_path):
    asset = EvidenceRef(role="screenshot", uri="file:///tmp/input.png", media_type="image/png")

    request = CliAgentRequest(prompt="Inspect this", workspace_dir=str(tmp_path), input_assets=[asset])
    result = CliAgentResult(status="completed")

    assert request.timeout_s == 600.0
    assert request.input_assets == [asset]
    assert request.salvage_globs == ["*.png", "*.jpg", "*.jpeg", "session*.md", "page-*.yml"]
    assert request.subscription_mode is True
    assert request.expect_json_result is True
    assert result.notional_cost_usd is None
    assert result.artifacts == []


def test_claude_p_assembly_writes_mcp_config_with_env_and_stdin_prompt(tmp_path):
    request = CliAgentRequest(
        prompt="Open the dashboard",
        workspace_dir=str(tmp_path),
        mcp_servers=[
            McpServerConfig(
                name="browser",
                command="npx",
                args=["@playwright/mcp"],
                env={"PLAYWRIGHT_TOKEN": "secret"},
            )
        ],
        allowed_tools=["mcp__browser__click", "mcp__browser__screenshot"],
        extra_argv=["--verbose"],
    )

    invocation = build_cli_agent_invocation(claude_p, request)

    assert invocation.argv == [
        "claude",
        "-p",
        "--mcp-config",
        str(tmp_path / "mcp_config.json"),
        "--strict-mcp-config",
        "--allowedTools",
        "mcp__browser__click",
        "mcp__browser__screenshot",
        "--output-format",
        "json",
        "--verbose",
    ]
    assert invocation.stdin_data == "Open the dashboard"
    assert invocation.result_file is None

    config = json.loads((tmp_path / "mcp_config.json").read_text(encoding="utf-8"))
    assert config == {
        "mcpServers": {
            "browser": {
                "command": "npx",
                "args": ["@playwright/mcp"],
                "env": {"PLAYWRIGHT_TOKEN": "secret"},
            }
        }
    }


def test_codex_exec_assembly_puts_mcp_env_in_config_entries_and_prompt_last(tmp_path):
    request = CliAgentRequest(
        prompt="Inspect the app",
        workspace_dir=str(tmp_path),
        mcp_servers=[
            McpServerConfig(
                name="browser",
                command="npx",
                args=["@playwright/mcp", "--isolated"],
                env={"PLAYWRIGHT_TOKEN": "secret", "TRACE": "1"},
            )
        ],
        model="gpt-5.4-codex",
        reasoning_effort="high",
        extra_argv=["--full-auto"],
    )

    invocation = build_cli_agent_invocation(codex_exec, request)

    assert invocation.argv == [
        "codex",
        "exec",
        "--config",
        'mcp_servers.browser.command="npx"',
        "--config",
        'mcp_servers.browser.args=["@playwright/mcp", "--isolated"]',
        "--config",
        'mcp_servers.browser.env.PLAYWRIGHT_TOKEN="secret"',
        "--config",
        'mcp_servers.browser.env.TRACE="1"',
        "--sandbox",
        "workspace-write",
        "--cd",
        str(tmp_path),
        "--output-last-message",
        str(tmp_path / "codex-last-message.txt"),
        "--model",
        "gpt-5.4-codex",
        "--config",
        'model_reasoning_effort="high"',
        "--full-auto",
        "Inspect the app",
    ]
    assert invocation.stdin_data is None
    assert invocation.result_file == str(tmp_path / "codex-last-message.txt")
    assert invocation.mcp_config_path is None
