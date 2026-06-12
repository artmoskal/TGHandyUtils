"""CLI-agent capability support."""

from .assembly import build_cli_agent_invocation
from .capability import CliAgentCapability
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
    "CliAgentInvocation",
    "CliAgentRequest",
    "CliAgentResult",
    "CliFlavor",
    "McpServerConfig",
    "build_cli_agent_invocation",
    "claude_p",
    "codex_exec",
]
