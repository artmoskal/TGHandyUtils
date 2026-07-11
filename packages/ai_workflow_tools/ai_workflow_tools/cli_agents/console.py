"""Console LLM client backed by a non-interactive CLI flavor."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Sequence

import logging
import math
from typing import Any

from ai_workflow_engine.engine.external import ExternalProcessCapability, ExternalProcessRequest
from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest, LLMResponse

from .capability import parse_cli_process_output
from .flavors import claude_p, codex_exec
from .assembly import claude_control_argv
from .models import CliAgentInvocation, CliFlavor

logger = logging.getLogger(__name__)

# Every spelling the claude CLI accepts for tool-surface flags: camel/kebab aliases,
# bare ("--tools", "value") and joined ("--tools=value") forms. Exact-token checks are
# NOT enough — missing a joined/kebab form would stack a conflicting `--tools ""` on top
# of the caller's explicit tool policy.
_TOOL_FLAGS = (
    "--tools",
    "--allowedTools",
    "--allowed-tools",
    "--disallowedTools",
    "--disallowed-tools",
)


def _has_explicit_tool_flag(argv: Sequence[str]) -> bool:
    return any(
        item == flag or item.startswith(f"{flag}=") for item in argv for flag in _TOOL_FLAGS
    )


class ConsoleLLMClient:
    """LLMCallable over a CLI runtime for plain text-in, JSON/text-out calls."""

    def __init__(
        self,
        flavor: CliFlavor,
        *,
        timeout_s: float = 120.0,
        subscription_mode: bool = True,
        extra_argv: Sequence[str] = (),
        external_runner: ExternalProcessCapability | None = None,
        cli_max_budget_usd: float | None = None,
    ) -> None:
        if cli_max_budget_usd is not None and (
            not math.isfinite(cli_max_budget_usd) or cli_max_budget_usd <= 0
        ):
            raise ValueError(
                f"cli_max_budget_usd must be a finite positive number, got {cli_max_budget_usd}"
            )
        self.flavor = flavor
        self.timeout_s = timeout_s
        self.subscription_mode = subscription_mode
        self.extra_argv = list(extra_argv)
        self.cli_max_budget_usd = cli_max_budget_usd
        self.external_runner = external_runner or ExternalProcessCapability()
        # Usage-event attribution for the engine's plain-callable metering path.
        self.provider_label = flavor.name

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)
        prompt = _flatten_request(request)
        images = [*request.images, *(img for m in request.messages for img in m.images)]
        with tempfile.TemporaryDirectory(prefix="ai-workflow-console-") as workspace:
            extra_argv = list(self.extra_argv)
            if images:
                # Staged vision: images become FILES in the CLI's working directory and the
                # agent reads them itself (claude -p needs Read allowed; codex reads its
                # sandbox natively). Same design as CliAgentCapability input_assets.
                staged = _stage_request_images(Path(workspace), images)
                listing = ", ".join(staged)
                prompt = (
                    f"First read and inspect these image file(s) in your working directory: "
                    f"{listing}. They are required context for the task below.\n\n{prompt}"
                )
                if self.flavor.name == claude_p.name and not _has_explicit_tool_flag(extra_argv):
                    # Path-SCOPED Read: only the staged inputs are readable. An unscoped
                    # Read would let hostile prompt content steer the agent into reading
                    # anything visible to the process (e.g. /app/.env in the bot container).
                    extra_argv = [*extra_argv, "--allowedTools", "Read(./inputs/**)"]
            if self.flavor.name == claude_p.name:
                # Q0.1: the routed profile model becomes a REAL --model argv control (the
                # response label below reads the same source, so label == request).
                extra_argv = [
                    *claude_control_argv(
                        _model_from_request(request), self.cli_max_budget_usd, extra_argv
                    ),
                    *extra_argv,
                ]
            elif self.cli_max_budget_usd is not None:
                raise ValueError(
                    f"{self.flavor.name} does not support cli_max_budget_usd (no CLI budget flag)"
                )
            invocation = _build_console_invocation(self.flavor, prompt, Path(workspace), extra_argv)
            external = await self.external_runner(
                None,
                ExternalProcessRequest(
                    command=invocation.argv,
                    cwd=workspace,
                    timeout_s=self.timeout_s,
                    stdin_data=invocation.stdin_data,
                    result_file=invocation.result_file,
                    kill_grace_s=10.0,
                    metadata={"flavor": self.flavor.name, "console_llm": True},
                ),
            )
            if external.status != "accepted":
                raise RuntimeError(external.error or f"console CLI exited with status {external.status}")
            output = external.output if isinstance(external.output, dict) else {}
            if output.get("returncode") != 0:
                stderr = str(output.get("stderr") or "")
                # Cost honesty on ABORTED calls: claude still emits a result envelope with
                # the consumed notional (e.g. error_max_budget_usd) — surface it instead of
                # discarding it, so the operator sees what the failed attempt actually burnt.
                aborted = parse_cli_process_output(self.flavor, output)
                subtype = ""
                try:
                    import json as _json

                    envelope = _json.loads(str(output.get("stdout") or ""))
                    subtype = str(envelope.get("subtype") or "") if isinstance(envelope, dict) else ""
                except (ValueError, TypeError):
                    pass
                consumed = (
                    f"; consumed notional ~${aborted.notional_cost_usd:.4f}"
                    if aborted.notional_cost_usd is not None
                    else ""
                )
                raise RuntimeError(
                    f"console CLI exited {output.get('returncode')}"
                    f"{f' ({subtype})' if subtype else ''}{consumed}: {stderr[-800:]}"
                )
            parsed = parse_cli_process_output(self.flavor, output)
            logger.info(
                "console_llm_call flavor=%s tokens_in=%s tokens_out=%s cost_usd=%s duration_ms=%s",
                self.flavor.name,
                parsed.input_tokens,
                parsed.output_tokens,
                parsed.notional_cost_usd,
                parsed.duration_ms,
            )
            notional_usd = parsed.notional_cost_usd if self.subscription_mode else None
            estimated_usd = None if self.subscription_mode else parsed.notional_cost_usd
            return LLMResponse(
                text=parsed.text,
                model=_model_from_request(request) or self.flavor.name,
                input_tokens=parsed.input_tokens,
                output_tokens=parsed.output_tokens,
                total_tokens=parsed.input_tokens + parsed.output_tokens,
                estimated_usd=estimated_usd,
                cost_class="subscription_notional" if self.subscription_mode else "metered",
                notional_usd=notional_usd,
                raw=output,
            )

    @staticmethod
    def _validate_request(request: LLMRequest) -> None:
        # Images ARE supported: they are staged as workspace files the agent reads itself
        # (see _stage_request_images) — never inlined into the text prompt.
        for message in request.messages:
            if any(result.images for result in message.tool_results):
                raise ValueError(
                    "ConsoleLLMClient cannot receive tool-result images; use CliAgentCapability"
                )
        if request.tools or request.tool_choice:
            raise ValueError("ConsoleLLMClient cannot host tool-calling turns")


def _stage_request_images(workspace: Path, images: Any) -> list[str]:
    """Write request images into ``<workspace>/inputs/`` and return relative paths.

    ``source="path"`` copies the file; ``source="base64"`` decodes; ``url`` is rejected
    loudly (the console runs offline relative to the caller — fetch before calling).
    """

    import base64
    import mimetypes
    import shutil

    input_dir = workspace / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for index, image in enumerate(images, start=1):
        suffix = mimetypes.guess_extension(image.media_type or "") or ".png"
        target = input_dir / f"img-{index}{suffix}"
        if image.source == "path":
            try:
                shutil.copyfile(image.data, target)
            except OSError as exc:
                raise ValueError(f"console image file unreadable: {image.data}: {exc}") from exc
        elif image.source == "base64":
            target.write_bytes(base64.b64decode(image.data))
        else:
            raise ValueError(
                f"console clients cannot fetch source={image.source!r} images — "
                "load them first (path or base64)"
            )
        staged.append(str(target.relative_to(workspace)))
    return staged


class ConsoleChatModel:
    """LangChain-shaped SYNC adapter over a console CLI for ``llm.invoke(messages)`` sites.

    Mirrors ``ChatGptBrowserChatModel``: product paths that meter LangChain chat calls
    (``invoke_metered_chat``) only need ``.invoke(messages) -> something-with-.content``.
    Runs the CLI with ``subprocess.run`` (no event loop required — safe from sync call
    sites). Text-only: multimodal message parts are rejected loudly. The caller passes
    ``cost_class="subscription_notional"`` to its metering wrapper (via the backend
    registry) — CLI subscriptions report notional cost, never phantom metered USD.
    """

    def __init__(
        self,
        flavor: CliFlavor,
        *,
        timeout_s: float = 240.0,
        extra_argv: Sequence[str] = (),
    ) -> None:
        self.flavor = flavor
        self.timeout_s = float(timeout_s)
        self.extra_argv = list(extra_argv)

    def invoke(self, messages: Any) -> Any:
        import subprocess

        prompt = _flatten_langchain_messages(messages)
        with tempfile.TemporaryDirectory(prefix="ai-workflow-console-") as workspace:
            invocation = _build_console_invocation(
                self.flavor, prompt, Path(workspace), self.extra_argv
            )
            try:
                completed = subprocess.run(
                    invocation.argv,
                    input=invocation.stdin_data,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    cwd=workspace,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"console CLI binary not found for flavor {self.flavor.name!r} "
                    f"({invocation.argv[0]!r}) — install/authenticate it in this runtime "
                    "or route the role back to an API model"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"console CLI timed out after {self.timeout_s:.0f}s ({self.flavor.name})"
                ) from exc
            if completed.returncode != 0:
                raise RuntimeError(
                    f"console CLI exited {completed.returncode}: {completed.stderr[-800:]}"
                )
            output: dict[str, Any] = {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }
            if invocation.result_file:
                result_path = Path(invocation.result_file)
                if result_path.exists():
                    output["result"] = result_path.read_text(encoding="utf-8")
            parsed = parse_cli_process_output(self.flavor, output)
            logger.info(
                "console_chat_call flavor=%s tokens_in=%s tokens_out=%s cost_usd=%s duration_ms=%s",
                self.flavor.name,
                parsed.input_tokens,
                parsed.output_tokens,
                parsed.notional_cost_usd,
                parsed.duration_ms,
            )
            if not parsed.text.strip():
                raise RuntimeError(f"console CLI returned no text ({self.flavor.name})")
            return _ConsoleChatReply(content=parsed.text, raw=output)


class _ConsoleChatReply:
    """Minimal chat-response shape: ``.content`` plus empty usage metadata attributes."""

    def __init__(self, content: str, raw: dict[str, Any]):
        self.content = content
        self.raw = raw
        self.usage_metadata = None
        self.response_metadata: dict[str, Any] = {}


def _flatten_langchain_messages(messages: Any) -> str:
    parts: list[str] = []
    for message in messages:
        role = getattr(message, "type", None) or getattr(message, "role", "user")
        content = getattr(message, "content", message)
        if isinstance(content, list):
            texts: list[str] = []
            for part in content:
                kind = part.get("type") if isinstance(part, dict) else None
                if kind == "text":
                    texts.append(str(part.get("text") or ""))
                elif kind:
                    raise ValueError(
                        f"ConsoleChatModel cannot send {kind!r} message parts (text-only CLI); "
                        "keep vision roles on an API client"
                    )
            content = "\n".join(texts)
        if str(content).strip():
            parts.append(f"{role}: {content}")
    return "\n\n".join(parts)


def _build_console_invocation(
    flavor: CliFlavor,
    prompt: str,
    workspace: Path,
    extra_argv: list[str],
) -> CliAgentInvocation:
    workspace.mkdir(parents=True, exist_ok=True)
    if flavor.name == claude_p.name:
        argv = [*flavor.base_argv, "--output-format", "json", *extra_argv]
        if not _has_explicit_tool_flag(extra_argv):
            # Console calls are completions, not agents: a tool call would break the
            # JSON parse/repair contract and add an injection surface. `--tools ""` is
            # the CLI's documented disable-all switch (verified 2.1.199/2.1.201); the
            # staged-vision path appends its scoped --allowedTools BEFORE this point,
            # and explicit caller tool flags win.
            argv.extend(["--tools", ""])
        return CliAgentInvocation(argv=argv, stdin_data=prompt)
    if flavor.name == codex_exec.name:
        result_file = workspace / "codex-last-message.txt"
        return CliAgentInvocation(
            argv=[
                *flavor.base_argv,
                "--sandbox",
                "workspace-write",
                "--cd",
                str(workspace),
                "--output-last-message",
                str(result_file),
                *extra_argv,
                prompt,
            ],
            result_file=str(result_file),
        )
    raise ValueError(f"unsupported console CLI flavor: {flavor.name}")


def _flatten_request(request: LLMRequest) -> str:
    parts: list[str] = []
    if request.system:
        parts.append(f"system: {request.system}")
    if request.user:
        parts.append(f"user: {request.user}")
    for message in request.messages:
        parts.extend(_flatten_message(message))
    return "\n\n".join(parts)


def _flatten_message(message: ChatMessage) -> list[str]:
    parts: list[str] = []
    if message.content:
        parts.append(f"{message.role}: {message.content}")
    for call in message.tool_calls:
        parts.append(f"assistant tool_call {call.name}: {call.arguments}")
    for result in message.tool_results:
        prefix = "tool error" if result.is_error else "tool"
        parts.append(f"{prefix} {result.call_id}: {result.content}")
    return parts


def _model_from_request(request: LLMRequest) -> str:
    profile = request.metadata.get("model_profile")
    if isinstance(profile, dict):
        model = profile.get("model")
        if isinstance(model, str):
            return model
    return ""


__all__ = ["ConsoleLLMClient"]
