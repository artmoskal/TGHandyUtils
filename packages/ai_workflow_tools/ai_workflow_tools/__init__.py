"""Reusable tool-library package for ai-workflow-engine.

Discovery is one import: ``TOOL_CATALOG`` lists every shipped tool with a description,
kind, side effects, and builder; ``render_tool_catalog()`` prints it for humans/LLMs;
``register_from_catalog(engine, name, ...)`` builds + registers capability-kind entries.
Media provider SDKs stay lazy — importing this package never pulls them.
"""

from ai_workflow_tools.catalog import (
    TOOL_CATALOG,
    ToolEntry,
    register_from_catalog,
    render_tool_catalog,
    tool_entry,
)
from ai_workflow_tools.cli_agents import (
    CliAgentCapability,
    CliAgentRequest,
    ConsoleChatModel,
    ConsoleLLMClient,
    claude_p,
    codex_exec,
)
from ai_workflow_tools.toolsets import (
    BASH_SIDE_EFFECTS,
    DEFAULT_AGENT_TOOLS,
    INVESTIGATION,
    NO_TOOLS,
    READ_ONLY,
    WEB,
    bash_in_tools,
)

__version__ = "0.4.1"

__all__ = [
    "TOOL_CATALOG",
    "ToolEntry",
    "tool_entry",
    "render_tool_catalog",
    "register_from_catalog",
    "CliAgentCapability",
    "CliAgentRequest",
    "ConsoleChatModel",
    "ConsoleLLMClient",
    "claude_p",
    "codex_exec",
    "READ_ONLY",
    "WEB",
    "INVESTIGATION",
    "NO_TOOLS",
    "DEFAULT_AGENT_TOOLS",
    "BASH_SIDE_EFFECTS",
    "bash_in_tools",
]
