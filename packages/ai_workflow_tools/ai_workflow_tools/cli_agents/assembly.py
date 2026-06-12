"""Flavor-specific subprocess argv/config assembly."""

from __future__ import annotations

import json
from pathlib import Path

from .flavors import claude_p, codex_exec
from .models import CliAgentInvocation, CliAgentRequest, CliFlavor, McpServerConfig


def build_cli_agent_invocation(flavor: CliFlavor, request: CliAgentRequest) -> CliAgentInvocation:
    """Build the subprocess invocation for a supported CLI flavor."""

    if flavor.name == claude_p.name:
        return _build_claude_p_invocation(flavor, request)
    if flavor.name == codex_exec.name:
        return _build_codex_exec_invocation(flavor, request)
    raise ValueError(f"unsupported CLI flavor: {flavor.name}")


def _build_claude_p_invocation(flavor: CliFlavor, request: CliAgentRequest) -> CliAgentInvocation:
    workspace = _ensure_workspace(request.workspace_dir)
    config_path = workspace / "mcp_config.json"
    config_path.write_text(
        json.dumps({"mcpServers": _mcp_servers_for_claude(request.mcp_servers)}, sort_keys=True),
        encoding="utf-8",
    )

    argv = [
        *flavor.base_argv,
        "--mcp-config",
        str(config_path),
        "--strict-mcp-config",
    ]
    if request.allowed_tools:
        argv.extend(["--allowedTools", *request.allowed_tools])
    argv.extend(["--output-format", "json", *request.extra_argv])
    return CliAgentInvocation(argv=argv, stdin_data=request.prompt, mcp_config_path=str(config_path))


def _build_codex_exec_invocation(flavor: CliFlavor, request: CliAgentRequest) -> CliAgentInvocation:
    workspace = _ensure_workspace(request.workspace_dir)
    result_file = workspace / "codex-last-message.txt"

    argv = [*flavor.base_argv]
    for server in request.mcp_servers:
        argv.extend(_codex_mcp_config_args(server))
    argv.extend(
        [
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workspace),
            "--output-last-message",
            str(result_file),
        ]
    )
    if request.model:
        argv.extend(["--model", request.model])
    if request.reasoning_effort:
        argv.extend(["--config", f"model_reasoning_effort={json.dumps(request.reasoning_effort)}"])
    argv.extend([*request.extra_argv, request.prompt])
    return CliAgentInvocation(argv=argv, result_file=str(result_file))


def _ensure_workspace(workspace_dir: str) -> Path:
    workspace = Path(workspace_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _mcp_servers_for_claude(servers: list[McpServerConfig]) -> dict[str, dict[str, object]]:
    configured: dict[str, dict[str, object]] = {}
    for server in servers:
        payload: dict[str, object] = {"command": server.command, "args": list(server.args)}
        if server.env:
            payload["env"] = dict(server.env)
        configured[server.name] = payload
    return configured


def _codex_mcp_config_args(server: McpServerConfig) -> list[str]:
    args = [
        "--config",
        f"mcp_servers.{server.name}.command={json.dumps(server.command)}",
        "--config",
        f"mcp_servers.{server.name}.args={json.dumps(server.args)}",
    ]
    for key, value in server.env.items():
        args.extend(["--config", f"mcp_servers.{server.name}.env.{key}={json.dumps(value)}"])
    return args


__all__ = ["build_cli_agent_invocation"]
