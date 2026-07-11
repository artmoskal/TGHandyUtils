"""CLI-agent capability support."""

from .assembly import build_cli_agent_invocation, resolve_effective_tools
from .capability import CliAgentCapability
from .console import ConsoleChatModel, ConsoleCliError, ConsoleLLMClient
from .flavors import claude_p, codex_exec
from .models import (
    CliAgentInvocation,
    CliAgentRequest,
    CliAgentResult,
    CliFlavor,
    McpServerConfig,
)

__all__ = [
    "CliAgentCapability",
    "ConsoleChatModel",
    "ConsoleCliError",
    "ConsoleLLMClient",
    "CliAgentInvocation",
    "CliAgentRequest",
    "CliAgentResult",
    "CliFlavor",
    "McpServerConfig",
    "build_cli_agent_invocation",
    "resolve_effective_tools",
    "claude_p",
    "codex_exec",
]
