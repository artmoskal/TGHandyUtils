"""Data contracts for CLI-backed agent capabilities."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from ai_workflow_engine.models import EvidenceRef
from ai_workflow_engine.usage_contract import (
    NormalizedTokenUsage,
    ProviderInvocationId,
    UsageError,
)
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
    # v0.10 (defect 5): NO hidden default. The subprocess bound is the engine's execution
    # window (soft work deadline); an explicit ``timeout_s`` may only NARROW it, never enlarge
    # it. When the engine supplies no window, an explicit positive timeout is REQUIRED — a
    # missing window must never silently become the old 600s ten-minute default nobody declared.
    timeout_s: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    # Optional: when omitted the capability mints a private temp workspace for this episode
    # (artifacts still salvage from it). Callers that need a known location pass one.
    workspace_dir: Optional[str] = None
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
    # Q0.1: typed pre-call CLI budget cap (claude --max-budget-usd). Positive-only at the
    # schema; None = no CLI cap. codex_exec has no such flag and rejects a set value loudly.
    cli_max_budget_usd: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    extra_argv: List[str] = Field(default_factory=list)
    subscription_mode: bool = True
    expect_json_result: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)
    invocation_id: Optional[ProviderInvocationId] = None


class CliAgentResult(BaseModel):
    """Structured result returned by a CLI-agent capability."""

    status: Literal["completed", "truncated", "error"]
    invocation_id: ProviderInvocationId
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
    reasoning_output_tokens: int = 0
    normalized_usage: Optional[NormalizedTokenUsage] = None
    usage_error: Optional[UsageError] = None
    usage_diagnostic: Optional[str] = Field(default=None, max_length=500)
    provider_reported_notional_usd: Optional[float] = None
    num_turns: Optional[int] = None
    duration_ms: Optional[int] = None
    elapsed_ms: Optional[int] = Field(default=None, ge=0)
    notional_cost_usd: Optional[float] = None
    returncode: Optional[int] = None
    stderr_tail: str = ""


class CliAgentInvocation(BaseModel):
    """Concrete subprocess invocation assembled from a flavor and request."""

    argv: List[str]
    stdin_data: Optional[str] = None
    result_file: Optional[str] = None
    mcp_config_path: Optional[str] = None
