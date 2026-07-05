"""Flavor-specific subprocess argv/config assembly."""

from __future__ import annotations

import json
from pathlib import Path

from ai_workflow_tools.toolsets import DEFAULT_AGENT_TOOLS

from .flavors import claude_p, codex_exec
from .models import CliAgentInvocation, CliAgentRequest, CliFlavor, McpServerConfig


def resolve_effective_tools(flavor: CliFlavor, request: CliAgentRequest) -> tuple[str, ...]:
    """The tool set the CLI will actually run with, after tri-state resolution.

    claude_p: ``None`` -> ``DEFAULT_AGENT_TOOLS``; ``[]`` -> no tools; list -> exact.
    codex_exec: tools are governed by ``--sandbox`` — explicit ``allowed_tools`` is a
    contract misuse and fails loudly (silently ignoring a security-relevant field is
    exactly the dishonesty the tri-state removes).
    """

    if flavor.name == codex_exec.name:
        if request.allowed_tools is not None:
            raise ValueError(
                "codex_exec governs its tool surface via --sandbox; allowed_tools cannot "
                "be mapped — leave it None (the tri-state applies to claude_p flavors)"
            )
        return ()
    if request.allowed_tools is None:
        return DEFAULT_AGENT_TOOLS
    return tuple(request.allowed_tools)


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
    if request.allowed_tools is None:
        argv.extend(["--allowedTools", *DEFAULT_AGENT_TOOLS])
    elif not request.allowed_tools:
        # Explicit no-tools: `--tools ""` is the CLI's documented disable-all switch
        # (verified on 2.1.199 container + 2.1.201 local). An omitted flag would
        # silently inherit CLI defaults — the exact ambiguity the tri-state removes.
        argv.extend(["--tools", ""])
    else:
        argv.extend(["--allowedTools", *request.allowed_tools])
    argv.extend(["--output-format", "json", *request.extra_argv])
    return CliAgentInvocation(argv=argv, stdin_data=request.prompt, mcp_config_path=str(config_path))


def _build_codex_exec_invocation(flavor: CliFlavor, request: CliAgentRequest) -> CliAgentInvocation:
    resolve_effective_tools(flavor, request)  # loud rejection of non-None allowed_tools
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


__all__ = ["build_cli_agent_invocation", "resolve_effective_tools"]
