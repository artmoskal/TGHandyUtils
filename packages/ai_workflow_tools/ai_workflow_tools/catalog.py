"""The described, discoverable tool catalog (G-T): one import shows the whole knife.

Design constraints (deliberate):
- ONE description path — capability entries reuse ``CapabilitySpec.description`` at build
  time; the catalog never grows a parallel metadata schema.
- Builders lazy-import their modules so ``import ai_workflow_tools`` stays light and the
  media pack keeps its no-SDK import guarantee (install the ``[media]`` extra to generate).
- A completeness guard (tests) enumerates the package's public tool symbols and fails,
  naming the offender, when a tool ships undocumented here.
- No preset subsystem: tool bundles are plain tuples in ``ai_workflow_tools.toolsets``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ai_workflow_tools import toolsets


@dataclass(frozen=True)
class ToolEntry:
    name: str
    kind: str  # "agent" | "tool" | "llm_client" | "chat_model"
    description: str
    side_effects: tuple[str, ...]
    builder: Callable[..., Any]  # returns a capability (has .spec) or a client instance
    covers: tuple[str, ...] = field(default=())  # public symbols documented by this entry
    extra: str = ""  # install extra needed to actually run it, e.g. "[media]"


def _build_cli_agent(**kwargs: Any) -> Any:
    from ai_workflow_tools.cli_agents import CliAgentCapability, claude_p

    flavor = kwargs.pop("flavor", claude_p)
    return CliAgentCapability(flavor, **kwargs)


def _build_console_llm(**kwargs: Any) -> Any:
    from ai_workflow_tools.cli_agents import ConsoleLLMClient, claude_p

    flavor = kwargs.pop("flavor", claude_p)
    return ConsoleLLMClient(flavor, **kwargs)


def _build_console_chat(**kwargs: Any) -> Any:
    from ai_workflow_tools.cli_agents import ConsoleChatModel, claude_p

    flavor = kwargs.pop("flavor", claude_p)
    return ConsoleChatModel(flavor, **kwargs)


def _build_chatgpt_browser_llm(**kwargs: Any) -> Any:
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserLLMClient

    return ChatGptBrowserLLMClient(**kwargs)


def _build_chatgpt_browser_chat(**kwargs: Any) -> Any:
    from ai_workflow_tools.chatgpt_browser import ChatGptBrowserChatModel

    return ChatGptBrowserChatModel(**kwargs)


def _build_image_generation(**kwargs: Any) -> Any:
    from ai_workflow_tools.media.capabilities import build_image_generation_capability

    return build_image_generation_capability(**kwargs)


def _build_voice_generation(**kwargs: Any) -> Any:
    from ai_workflow_tools.media.capabilities import build_voice_generation_capability

    return build_voice_generation_capability(**kwargs)


TOOL_CATALOG: tuple[ToolEntry, ...] = (
    ToolEntry(
        name="cli_agent",
        kind="agent",
        description=(
            "Bounded CLI-agent episode (claude -p / codex exec): tri-state tool allow-list, "
            "MCP servers, staged input assets, artifact salvage, subscription-honest usage."
        ),
        side_effects=toolsets.BASH_SIDE_EFFECTS,
        builder=_build_cli_agent,
        covers=(
            "CliAgentCapability",
            "CliAgentRequest",
            "CliAgentResult",
            "CliAgentInvocation",
            "CliFlavor",
            "McpServerConfig",
            "build_cli_agent_invocation",
            "resolve_effective_tools",
            "claude_p",
            "codex_exec",
            # Presets parameterize CliAgentRequest.allowed_tools (see toolsets.py).
            *toolsets.__all__,
        ),
    ),
    ToolEntry(
        name="console_llm",
        kind="llm_client",
        description=(
            "LLMCallable over a non-interactive CLI (claude -p / codex exec) for structured "
            "text-to-JSON completions; staged-vision file inputs; ALL tools disabled "
            '(`--tools ""`) on text-only calls.'
        ),
        side_effects=(),
        builder=_build_console_llm,
        covers=("ConsoleLLMClient", "ConsoleCliError"),
    ),
    ToolEntry(
        name="console_chat",
        kind="chat_model",
        description=(
            "Sync LangChain-shaped `.invoke(messages)` adapter over a console CLI for "
            "metered-chat call sites; text-only, tools disabled."
        ),
        side_effects=(),
        builder=_build_console_chat,
        covers=("ConsoleChatModel",),
    ),
    ToolEntry(
        name="chatgpt_browser_llm",
        kind="llm_client",
        description=(
            "LLMCallable over the browser-driven ChatGPT HTTP service (subscription plan "
            "value, typed unknown pricing when facts are unavailable); rejects images and "
            "tool turns loudly."
        ),
        side_effects=("external_call",),
        builder=_build_chatgpt_browser_llm,
        covers=("ChatGptBrowserLLMClient", "ChatGptBrowserError"),
    ),
    ToolEntry(
        name="chatgpt_browser_chat",
        kind="chat_model",
        description=(
            "Sync `.invoke(messages)` adapter over the ChatGPT browser service for "
            "LangChain-shaped call sites; subscription-honest cost reporting."
        ),
        side_effects=("external_call",),
        builder=_build_chatgpt_browser_chat,
        covers=("ChatGptBrowserChatModel",),
    ),
    ToolEntry(
        name="image_generation",
        kind="tool",
        description=(
            "Generate one image artifact from a prompt (+ optional reference images) via the "
            "configured provider (OpenAI / Gemini / ChatGPT-browser / comparison)."
        ),
        side_effects=("external_call",),
        builder=_build_image_generation,
        covers=(
            "MediaGenerationCapability",
            "build_image_generation_capability",
            "ImageGenerationRequest",
            "GeneratedImage",
            "OpenAIImageGenerator",
            "create_image_generator",
        ),
        extra="[media]",
    ),
    ToolEntry(
        name="voice_generation",
        kind="tool",
        description=(
            "Synthesize one speech-audio artifact from text via the configured TTS provider "
            "(ElevenLabs today); voice identity comes from config, not the graph."
        ),
        side_effects=("external_call",),
        builder=_build_voice_generation,
        covers=(
            "build_voice_generation_capability",
            "VoiceGenerationRequest",
            "GeneratedVoiceAudio",
            "create_voice_generator",
        ),
        extra="[media]",
    ),
)


def tool_entry(name: str) -> ToolEntry:
    """The catalog entry for ``name``; unknown names fail loudly listing what exists."""

    for entry in TOOL_CATALOG:
        if entry.name == name:
            return entry
    known = ", ".join(entry.name for entry in TOOL_CATALOG)
    raise ValueError(f"unknown tool {name!r}; catalog has: {known}")


def render_tool_catalog() -> str:
    """Human/LLM-readable index — same idea as ``render_capability_catalog``."""

    lines = ["TOOL CATALOG (ai_workflow_tools)", ""]
    for entry in TOOL_CATALOG:
        effects = ", ".join(entry.side_effects) or "none"
        extra = f" [install: ai-workflow-tools{entry.extra}]" if entry.extra else ""
        lines.append(f"- {entry.name} ({entry.kind}; side effects: {effects}){extra}")
        lines.append(f"    {entry.description}")
    lines.append("")
    lines.append("TOOL PRESETS (claude_p-flavor bundles; plain tuples, compose by concatenation)")
    for preset_name in ("READ_ONLY", "WEB", "INVESTIGATION", "NO_TOOLS"):
        bundle = getattr(toolsets, preset_name)
        rendered = ", ".join(bundle) or "(no tools)"
        lines.append(f"- {preset_name}: {rendered}")
    lines.append(
        f"- DEFAULT_AGENT_TOOLS is INVESTIGATION (applied when CliAgentRequest.allowed_tools is None)"
    )
    return "\n".join(lines)


def register_from_catalog(engine: Any, name: str, **kwargs: Any) -> Any:
    """Build catalog entry ``name`` and register it on ``engine`` in one call.

    Only capability-kind entries (``agent``/``tool``) are registerable; client/chat-model
    entries are wiring inputs for LLM nodes and fail loudly with that guidance.
    """

    entry = tool_entry(name)
    built = entry.builder(**kwargs)
    spec = getattr(built, "spec", None)
    if spec is None:
        raise ValueError(
            f"catalog entry {name!r} builds a {entry.kind} (no CapabilitySpec) — pass it to "
            "your LLM node / model wiring instead of registering it as a capability"
        )
    engine.register_capability_spec(spec, built)
    return built


__all__ = [
    "ToolEntry",
    "TOOL_CATALOG",
    "tool_entry",
    "render_tool_catalog",
    "register_from_catalog",
]
