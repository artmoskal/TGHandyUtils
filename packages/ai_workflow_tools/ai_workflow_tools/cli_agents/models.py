"""Data contracts for CLI-backed agent capabilities."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from ai_workflow_engine.models import EvidenceRef
from pydantic import BaseModel, Field


class CliFlavor(BaseModel):
    """How to talk to one CLI agent runtime."""

    name: str
    prompt_delivery: Literal["stdin", "argv_last"]
    result_source: Literal["stdout_json_envelope", "result_file", "stdout_text"]
    base_argv: List[str]


class McpServerConfig(BaseModel):
    """Implementation-agnostic MCP server description."""

    name: str
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)


class CliAgentRequest(BaseModel):
    """Request for one bounded CLI-agent run."""

    prompt: str
    workspace_dir: str
    timeout_s: float = 600.0
    mcp_servers: List[McpServerConfig] = Field(default_factory=list)
    # Tri-state (claude_p): None -> DEFAULT_AGENT_TOOLS; [] -> explicit no-tools
    # (`--tools ""`); non-empty -> EXACT override, never merged with defaults.
    # codex_exec governs tools via --sandbox and rejects non-None values loudly.
    allowed_tools: Optional[List[str]] = None
    input_assets: List[EvidenceRef] = Field(default_factory=list)
    salvage_globs: List[str] = Field(
        default_factory=lambda: ["*.png", "*.jpg", "*.jpeg", "session*.md", "page-*.yml"]
    )
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    extra_argv: List[str] = Field(default_factory=list)
    subscription_mode: bool = True
    expect_json_result: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CliAgentResult(BaseModel):
    """Structured result returned by a CLI-agent capability."""

    status: Literal["completed", "truncated", "error"]
    text: str = ""
    parsed: Optional[Dict[str, Any]] = None
    artifacts: List[EvidenceRef] = Field(default_factory=list)
    new_artifact_count: int = 0
    # Provenance of staged inputs (sha12/length/role/media_type/relative path - never bytes).
    # Part of the episode RECORD so freeze-to-replay covers what went in, not just what came out.
    input_fingerprints: List[Dict[str, Any]] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    num_turns: Optional[int] = None
    duration_ms: Optional[int] = None
    notional_cost_usd: Optional[float] = None
    returncode: Optional[int] = None
    stderr_tail: str = ""


class CliAgentInvocation(BaseModel):
    """Concrete subprocess invocation assembled from a flavor and request."""

    argv: List[str]
    stdin_data: Optional[str] = None
    result_file: Optional[str] = None
    mcp_config_path: Optional[str] = None
