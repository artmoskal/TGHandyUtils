"""Named CLI tool bundles (presets) — plain tuples, deliberately not a preset subsystem.

These are claude_p-flavor tool NAMES (an L2 concern): codex_exec governs its tool surface
via ``--sandbox`` and rejects explicit allow-lists (see ``cli_agents.assembly``). Compose
bundles by tuple concatenation; a typo is an import error, which is loud for free.

Owner decision 2026-07-04: ``Bash`` stays in the generous investigation default. Honesty
comes from side-effect declaration, not from narrowing the default — any effective tool
set containing Bash requires the capability to declare ``BASH_SIDE_EFFECTS``, enforced
by a pre-spawn check in ``CliAgentCapability`` (the safety ledger must not lie).
"""

from __future__ import annotations

from typing import Iterable

READ_ONLY = ("Read", "Grep", "Glob", "LS")
WEB = ("WebFetch", "WebSearch")
INVESTIGATION = (*READ_ONLY, *WEB, "Bash")
NO_TOOLS: tuple[str, ...] = ()

# One source of truth: the default applied when CliAgentRequest.allowed_tools is None.
DEFAULT_AGENT_TOOLS = INVESTIGATION

# What Bash actually enables even when Read is scoped: shell writes + network.
BASH_SIDE_EFFECTS = ("workspace_write", "external_call")


def bash_in_tools(tools: Iterable[str]) -> bool:
    """True when the effective tool set grants Bash (bare or scoped, e.g. ``Bash(git *)``)."""

    return any(tool == "Bash" or tool.startswith("Bash(") for tool in tools)


__all__ = [
    "READ_ONLY",
    "WEB",
    "INVESTIGATION",
    "NO_TOOLS",
    "DEFAULT_AGENT_TOOLS",
    "BASH_SIDE_EFFECTS",
    "bash_in_tools",
]
