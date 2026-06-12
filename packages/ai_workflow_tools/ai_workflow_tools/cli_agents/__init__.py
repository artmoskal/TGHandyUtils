"""CLI-agent capability support."""

from .assembly import build_cli_agent_invocation
from .capability import CliAgentCapability
from .console import ConsoleLLMClient
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
    "ConsoleLLMClient",
    "CliAgentInvocation",
    "CliAgentRequest",
    "CliAgentResult",
    "CliFlavor",
    "McpServerConfig",
    "build_cli_agent_invocation",
    "claude_p",
    "codex_exec",
]
