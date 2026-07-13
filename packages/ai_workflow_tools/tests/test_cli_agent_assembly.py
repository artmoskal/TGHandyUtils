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

    # v0.10 (defect 5): NO hidden default — the subprocess bound comes from the engine window
    # or an explicit positive timeout, never a silent 600s ten-minute default.
    assert request.timeout_s is None
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


def test_claude_p_tri_state_none_applies_default_agent_tools(tmp_path):
    from ai_workflow_tools.toolsets import DEFAULT_AGENT_TOOLS

    invocation = build_cli_agent_invocation(
        claude_p,
        CliAgentRequest(prompt="investigate", workspace_dir=str(tmp_path)),
    )

    marker = invocation.argv.index("--allowedTools")
    granted = invocation.argv[marker + 1 : marker + 1 + len(DEFAULT_AGENT_TOOLS)]
    assert granted == list(DEFAULT_AGENT_TOOLS)
    assert "--tools" not in invocation.argv


def test_claude_p_tri_state_empty_list_means_explicit_no_tools(tmp_path):
    invocation = build_cli_agent_invocation(
        claude_p,
        CliAgentRequest(prompt="pure completion", workspace_dir=str(tmp_path), allowed_tools=[]),
    )

    marker = invocation.argv.index("--tools")
    assert invocation.argv[marker + 1] == ""
    assert "--allowedTools" not in invocation.argv


def test_claude_p_tri_state_explicit_list_is_exact_override_no_merge(tmp_path):
    invocation = build_cli_agent_invocation(
        claude_p,
        CliAgentRequest(
            prompt="narrow read",
            workspace_dir=str(tmp_path),
            allowed_tools=["Read", "Grep"],
        ),
    )

    marker = invocation.argv.index("--allowedTools")
    tail = invocation.argv[marker + 1 :]
    assert tail[:2] == ["Read", "Grep"]
    assert "Bash" not in invocation.argv
    assert "WebFetch" not in invocation.argv
    assert "--tools" not in invocation.argv


def test_codex_exec_rejects_explicit_allowed_tools_loudly(tmp_path):
    request = CliAgentRequest(
        prompt="inspect", workspace_dir=str(tmp_path), allowed_tools=["Read"]
    )

    with pytest.raises(ValueError, match="--sandbox"):
        build_cli_agent_invocation(codex_exec, request)


def test_preset_constants_round_trip_and_share_one_source_of_truth(tmp_path):
    from ai_workflow_tools.toolsets import (
        DEFAULT_AGENT_TOOLS,
        INVESTIGATION,
        NO_TOOLS,
        READ_ONLY,
        WEB,
    )

    assert DEFAULT_AGENT_TOOLS is INVESTIGATION
    assert INVESTIGATION == (*READ_ONLY, *WEB, "Bash")

    invocation = build_cli_agent_invocation(
        claude_p,
        CliAgentRequest(
            prompt="preset run", workspace_dir=str(tmp_path), allowed_tools=list(INVESTIGATION)
        ),
    )
    marker = invocation.argv.index("--allowedTools")
    assert invocation.argv[marker + 1 : marker + 1 + len(INVESTIGATION)] == list(INVESTIGATION)

    no_tools = build_cli_agent_invocation(
        claude_p,
        CliAgentRequest(
            prompt="no tools", workspace_dir=str(tmp_path), allowed_tools=list(NO_TOOLS)
        ),
    )
    assert no_tools.argv[no_tools.argv.index("--tools") + 1] == ""


# ------------------------------ Q0.1: typed Claude model + CLI budget argv controls


def test_claude_p_maps_typed_model_and_budget_to_real_argv(tmp_path):
    """Q0.1 (codex Q-C1): the typed request fields become REAL argv controls — before this,
    Claude ran its default model while the response was merely labeled with the routed one."""

    invocation = build_cli_agent_invocation(
        claude_p,
        CliAgentRequest(
            prompt="classify",
            workspace_dir=str(tmp_path),
            model="sonnet",
            cli_max_budget_usd=0.10,
        ),
    )

    model_at = invocation.argv.index("--model")
    assert invocation.argv[model_at + 1] == "sonnet"
    budget_at = invocation.argv.index("--max-budget-usd")
    assert invocation.argv[budget_at + 1] == "0.1"
    # controls precede the caller extra_argv tail and appear exactly once
    assert invocation.argv.count("--model") == 1
    assert invocation.argv.count("--max-budget-usd") == 1


def test_claude_p_omits_control_flags_when_fields_unset(tmp_path):
    """Degradation pair: unset typed fields leave argv EXACTLY as before Q0.1."""

    invocation = build_cli_agent_invocation(
        claude_p, CliAgentRequest(prompt="classify", workspace_dir=str(tmp_path))
    )
    assert "--model" not in invocation.argv
    assert "--max-budget-usd" not in invocation.argv


def test_claude_p_rejects_conflicting_raw_flags_pre_spawn(tmp_path):
    with pytest.raises(ValueError, match="conflicting --model"):
        build_cli_agent_invocation(
            claude_p,
            CliAgentRequest(
                prompt="x",
                workspace_dir=str(tmp_path),
                model="sonnet",
                extra_argv=["--model", "haiku"],
            ),
        )
    with pytest.raises(ValueError, match="conflicting --max-budget-usd"):
        build_cli_agent_invocation(
            claude_p,
            CliAgentRequest(
                prompt="x",
                workspace_dir=str(tmp_path),
                cli_max_budget_usd=0.10,
                extra_argv=["--max-budget-usd=0.5"],
            ),
        )


def test_cli_budget_is_positive_only_and_codex_rejects_it(tmp_path):
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        CliAgentRequest(prompt="x", workspace_dir=str(tmp_path), cli_max_budget_usd=0)
    with pytest.raises(pydantic.ValidationError):
        CliAgentRequest(prompt="x", workspace_dir=str(tmp_path), cli_max_budget_usd=-1.0)

    with pytest.raises(ValueError, match="codex_exec does not support cli_max_budget_usd"):
        build_cli_agent_invocation(
            codex_exec,
            CliAgentRequest(
                prompt="x", workspace_dir=str(tmp_path), cli_max_budget_usd=0.10
            ),
        )


def test_codex_exec_argv_unchanged_when_budget_unset(tmp_path):
    """Q0.1 AC: codex behavior is unchanged — same argv as before the field existed."""

    invocation = build_cli_agent_invocation(
        codex_exec,
        CliAgentRequest(prompt="investigate", workspace_dir=str(tmp_path), model="o4-mini"),
    )
    assert "--max-budget-usd" not in invocation.argv
    model_at = invocation.argv.index("--model")
    assert invocation.argv[model_at + 1] == "o4-mini"


def test_cli_budget_rejects_non_finite_values_at_every_entry(tmp_path):
    """Q0-C1 (codex probe): inf/NaN must never reach argv — `--max-budget-usd inf` is a
    fake cap, worse than none. All three entries reject: typed model, shared helper."""

    import pydantic

    from ai_workflow_tools.cli_agents.assembly import claude_control_argv

    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(pydantic.ValidationError):
            CliAgentRequest(prompt="x", workspace_dir=str(tmp_path), cli_max_budget_usd=bad)
        with pytest.raises(ValueError, match="finite"):
            claude_control_argv(None, bad, [])
