"""Flavor-specific subprocess argv/config assembly."""

from __future__ import annotations

import json
import math
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


def claude_control_argv(
    model: "str | None",
    cli_max_budget_usd: "float | None",
    existing_argv: "list[str] | tuple[str, ...]",
) -> "list[str]":
    """Q0.1: typed Claude control flags — the ONE mapping both the capability and console
    paths use. A typed value plus the same raw flag in caller argv is a CONFLICT (loud),
    never a silent duplicate the CLI resolves arbitrarily. Zero/negative budgets are
    rejected here too (belt for callers that bypass the pydantic schema)."""

    controls: list[str] = []
    if model:
        _reject_raw_flag_conflict(existing_argv, "--model", typed_source="model")
        controls.extend(["--model", model])
    if cli_max_budget_usd is not None:
        if not math.isfinite(cli_max_budget_usd) or cli_max_budget_usd <= 0:
            # Q0-C1: inf/NaN would emit `--max-budget-usd inf` — a fake cap, worse than none.
            raise ValueError(
                f"cli_max_budget_usd must be a finite positive number, got {cli_max_budget_usd}"
            )
        _reject_raw_flag_conflict(
            existing_argv, "--max-budget-usd", typed_source="cli_max_budget_usd"
        )
        controls.extend(["--max-budget-usd", f"{cli_max_budget_usd}"])
    return controls


def _reject_raw_flag_conflict(argv, flag: str, *, typed_source: str) -> None:
    if any(item == flag or item.startswith(f"{flag}=") for item in argv):
        raise ValueError(
            f"conflicting {flag}: set via typed {typed_source}= AND raw extra_argv — "
            "pass exactly one"
        )


def codex_structured_output_argv(existing_argv: "list[str] | tuple[str, ...]") -> list[str]:
    """Enable the one Codex JSONL protocol stream without permitting duplicate controls."""

    _reject_raw_flag_conflict(existing_argv, "--json", typed_source="structured usage")
    return ["--json"]


CODEX_STDIN_MARKER = "-"


def codex_prompt_transport(prompt: str) -> "tuple[list[str], str]":
    """Return the prompt-positional argv and the stdin bytes for one Codex invocation.

    One owner for both Codex builders (agent capability and the console classes), so the
    transport rule cannot drift between public doors.

    Codex must never carry prompt text in argv:

    * Linux rejects a single argument near ``MAX_ARG_STRLEN``. A legitimate 125,799-character
      curation prompt surfaced as ``[Errno 7] Argument list too long`` at process creation,
      before any provider call.
    * argv is world-readable through ``/proc/<pid>/cmdline``, so even a small prompt leaked.

    ``codex-cli 0.145.0`` documents the positional as: "If not provided as an argument (or if
    ``-`` is used), instructions are read from stdin." The marker replaces the prompt in the same
    position rather than joining it — supplying both makes Codex append stdin as a separate
    ``<stdin>`` block, which would duplicate the prompt. Keeping the position also preserves the
    proven argv order for the variadic ``--image`` that may follow.
    """

    return [CODEX_STDIN_MARKER], prompt


def codex_model_argv(
    model: "str | None", existing_argv: "list[str] | tuple[str, ...]"
) -> list[str]:
    if not model:
        return []
    _reject_raw_flag_conflict(existing_argv, "--model", typed_source="model")
    return ["--model", model]


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
    # Q0.1: typed model + CLI budget are REAL argv controls, not post-hoc labels.
    argv.extend(claude_control_argv(request.model, request.cli_max_budget_usd, request.extra_argv))
    argv.extend(["--output-format", "json", *request.extra_argv])
    return CliAgentInvocation(argv=argv, stdin_data=request.prompt, mcp_config_path=str(config_path))


def _build_codex_exec_invocation(flavor: CliFlavor, request: CliAgentRequest) -> CliAgentInvocation:
    resolve_effective_tools(flavor, request)  # loud rejection of non-None allowed_tools
    if request.cli_max_budget_usd is not None:
        # Q0.1: no codex budget flag exists — a silent drop would fake a cost cap.
        raise ValueError("codex_exec does not support cli_max_budget_usd (no CLI budget flag)")
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
    prompt_argv, stdin_data = codex_prompt_transport(request.prompt)
    argv.extend(
        [
            *codex_structured_output_argv(request.extra_argv),
            *request.extra_argv,
            *prompt_argv,
        ]
    )
    return CliAgentInvocation(
        argv=argv, stdin_data=stdin_data, result_file=str(result_file)
    )


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


__all__ = [
    "build_cli_agent_invocation",
    "codex_model_argv",
    "codex_structured_output_argv",
    "resolve_effective_tools",
]
