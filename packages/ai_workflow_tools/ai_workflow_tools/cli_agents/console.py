"""Console LLM client backed by a non-interactive CLI flavor."""

from __future__ import annotations

import asyncio
import contextvars
import tempfile
import threading
import time
from pathlib import Path
from typing import Sequence

import logging
import math
from typing import Any

from ai_workflow_engine.engine.external import ExternalProcessCapability, ExternalProcessRequest
from ai_workflow_engine.execution_window import (
    InvocationBound,
    invocation_window_remaining_s,
    resolve_invocation_bound,
)
from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest, LLMResponse
from ai_workflow_engine.llm_protocol import (
    current_blocking_call_cancellation,
    current_llm_usage_identity,
)
from ai_workflow_engine.usage_contract import new_provider_invocation_id

from .usage import (
    CliUsageAccumulator,
    parse_cli_process_output,
    parsed_cli_output_from_accumulator,
    usage_accumulator_for,
)
from .flavors import claude_p, codex_exec
from .assembly import (
    claude_control_argv,
    codex_model_argv,
    codex_prompt_transport,
    codex_structured_output_argv,
)
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


def _console_effective_bound(explicit_timeout_s: float, *, owner: str) -> InvocationBound:
    """One console bound through the engine-owned invocation-window surface: the ambient
    engine soft-remaining (when this call runs inside a bounded engine invocation) narrowed
    by the client's explicit timeout — narrowing-only, the explicit value can never widen an
    engine bound. Standalone calls (no ambient window) keep the explicit bound."""

    ambient = invocation_window_remaining_s()
    bound = resolve_invocation_bound(
        engine_soft_s=ambient.soft_s if ambient is not None else None,
        engine_hard_s=ambient.hard_s if ambient is not None else None,
        explicit_timeout_s=explicit_timeout_s,
        owner=owner,
    )
    if bound.error is not None or bound.timeout_s is None:
        raise ValueError(bound.error or f"{owner}: no console timeout resolved")
    return bound


def _run_external_process_sync(
    runner: ExternalProcessCapability,
    request: ExternalProcessRequest,
) -> Any:
    """Use the engine's process owner from a synchronous chat-model interface.

    LangChain's sync ``invoke`` may run on a plain worker thread (the normal engine path) or
    directly on an event-loop thread. ``asyncio.run`` handles the former; the latter needs a
    short bridge thread so we do not create a nested event loop. The current ContextVar state is
    copied so the process owner still sees the engine's invocation window.
    """

    async def invoke() -> Any:
        cancellation = current_blocking_call_cancellation()
        task = asyncio.current_task()
        if cancellation is not None and task is not None:
            cancellation.bind(asyncio.get_running_loop(), task)
        try:
            return await runner(None, request)
        finally:
            if cancellation is not None and task is not None:
                cancellation.unbind(task)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(invoke())

    context = contextvars.copy_context()
    result: list[Any] = []
    error: list[BaseException] = []

    def run() -> None:
        try:
            result.append(context.run(asyncio.run, invoke()))
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=run, name="ai-workflow-console-process", daemon=False)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


class ConsoleCliError(RuntimeError):
    """Typed console-CLI failure (Q-R2): carries what the aborted call actually consumed so
    the engine's failed-usage event and any ledger see STRUCTURED burn data, not prose."""

    def __init__(
        self,
        message: str,
        *,
        cli_subtype: str = "",
        notional_usd: "float | None" = None,
        returncode: "int | None" = None,
        num_turns: "int | None" = None,
        failure_kind: str = "",
        worker_calls: int = 1,
        process_execution_bound: dict[str, Any] | None = None,
        model: str = "",
        normalized_usage: Any = None,
        usage_error: Any = None,
        usage_diagnostic: str | None = None,
        invocation_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        # QRF.4: the classification vocabulary is CLOSED — a typo ("timeuot") is a loud
        # construction error, never a silent fall-through to heuristics.
        if failure_kind not in ("", "cap", "timeout", "provider"):
            raise ValueError(
                f"failure_kind must be one of '', 'cap', 'timeout', 'provider' — got {failure_kind!r}"
            )
        # QRF.2: one raised error represents AT LEAST one spawned paid attempt — zero or
        # negative counts would undercount spend and are rejected loudly.
        if type(worker_calls) is not int or worker_calls < 1:  # bool is an int subclass — rejected
            raise ValueError(f"worker_calls must be a positive int, got {worker_calls!r}")
        self.cli_subtype = cli_subtype
        self.notional_usd = notional_usd
        self.returncode = returncode
        self.num_turns = num_turns
        self.failure_kind = failure_kind
        self.worker_calls = worker_calls
        self.process_execution_bound = dict(process_execution_bound or {})
        self.model = model
        self.normalized_usage = normalized_usage
        self.usage_error = usage_error
        self.usage_diagnostic = usage_diagnostic
        self.invocation_id = invocation_id
        self.elapsed_ms = elapsed_ms


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
        model: str | None = None,
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
        self.model = model
        self.external_runner = external_runner or ExternalProcessCapability()
        # Usage-event attribution for the engine's plain-callable metering path.
        self.provider_label = flavor.name

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)
        invocation_id = request.invocation_id or new_provider_invocation_id()
        request = request.model_copy(update={"invocation_id": invocation_id})
        prompt = _flatten_request(request)
        images = [*request.images, *(img for m in request.messages for img in m.images)]
        with tempfile.TemporaryDirectory(prefix="ai-workflow-console-") as workspace:
            staged: list[str] = []
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
                        _model_from_request(request) or self.model,
                        self.cli_max_budget_usd,
                        extra_argv,
                    ),
                    *extra_argv,
                ]
            elif self.cli_max_budget_usd is not None:
                raise ValueError(
                    f"{self.flavor.name} does not support cli_max_budget_usd (no CLI budget flag)"
                )
            elif self.flavor.name == codex_exec.name:
                extra_argv = [
                    *codex_model_argv(
                        _model_from_request(request) or self.model, extra_argv
                    ),
                    *extra_argv,
                ]
            invocation = _build_console_invocation(
                self.flavor,
                prompt,
                Path(workspace),
                extra_argv,
                image_paths=staged if self.flavor.name == codex_exec.name else (),
            )
            # 5R: inside an engine run the ambient invocation window bounds this call — the
            # client's own timeout_s may only NARROW it; standalone calls keep it as-is.
            process_bound = _console_effective_bound(
                self.timeout_s, owner=f"console_llm[{self.flavor.name}]"
            )
            usage_accumulator = usage_accumulator_for(self.flavor)
            started = time.monotonic()
            try:
                external = await self.external_runner(
                    None,
                    ExternalProcessRequest(
                        command=invocation.argv,
                        cwd=workspace,
                        timeout_s=process_bound.timeout_s,
                        stdin_data=invocation.stdin_data,
                        result_file=invocation.result_file,
                        kill_grace_s=process_bound.kill_grace_s,
                        stdout_observer=(
                            usage_accumulator.feed
                            if usage_accumulator is not None
                            else None
                        ),
                        metadata={
                            "flavor": self.flavor.name,
                            "console_llm": True,
                            "invocation_id": invocation_id,
                            "process_execution_bound": process_bound.metadata(),
                        },
                    ),
                )
            except asyncio.CancelledError:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if usage_accumulator is not None:
                    parsed_cancelled = parsed_cli_output_from_accumulator(
                        {}, usage_accumulator
                    )
                    from ai_workflow_engine.models import WorkflowUsageEvent
                    from ai_workflow_engine.usage_events import record_usage_event

                    record_usage_event(
                        WorkflowUsageEvent(
                            provider=self.flavor.name,
                            operation="chat",
                            cost_class=(
                                "subscription_notional"
                                if self.subscription_mode
                                else "metered"
                            ),
                            node=str(
                                request.metadata.get("engine_usage_node")
                                or "console_llm"
                            ),
                            model=(
                                _model_from_request(request)
                                or self.model
                                or self.flavor.name
                            ),
                            attempt=(
                                request.metadata.get("engine_usage_attempt")
                                if type(request.metadata.get("engine_usage_attempt")) is int
                                else 1
                            ),
                            invocation_id=invocation_id,
                            input_tokens=parsed_cancelled.input_tokens,
                            output_tokens=parsed_cancelled.output_tokens,
                            total_tokens=(
                                parsed_cancelled.input_tokens
                                + parsed_cancelled.output_tokens
                            ),
                            input_token_details={
                                "cache_read": parsed_cancelled.cache_read_tokens,
                                "cache_creation": parsed_cancelled.cache_creation_tokens,
                            },
                            output_token_details={
                                "reasoning": parsed_cancelled.reasoning_output_tokens,
                            },
                            normalized_usage=parsed_cancelled.normalized_usage,
                            usage_error=parsed_cancelled.usage_error,
                            usage_diagnostic=parsed_cancelled.usage_diagnostic,
                            provider_reported_notional_usd=(
                                parsed_cancelled.provider_reported_notional_usd
                                if self.subscription_mode
                                else None
                            ),
                            estimated_usd=(
                                parsed_cancelled.provider_reported_notional_usd
                                if not self.subscription_mode
                                else None
                            ),
                            success=False,
                            error="caller cancellation",
                            elapsed_ms=elapsed_ms,
                            metadata={"cancelled": True},
                        )
                    )
                raise
            elapsed_ms = int((time.monotonic() - started) * 1000)
            output = external.output if isinstance(external.output, dict) else {}
            parsed = (
                parsed_cli_output_from_accumulator(output, usage_accumulator)
                if usage_accumulator is not None
                else parse_cli_process_output(self.flavor, output)
            )
            if (
                external.status != "accepted"
                or output.get("returncode") != 0
                or parsed.provider_error
            ):
                # ONE typed failure path (Q-R2): the runner marks nonzero exits failed but
                # still hands us stdout — claude's ERROR envelope there carries the consumed
                # notional (e.g. error_max_budget_usd), which must survive as structured
                # data, never just prose.
                raise _console_failure(
                    self.flavor,
                    external,
                    output,
                    model=_model_from_request(request) or self.model or self.flavor.name,
                    usage_accumulator=usage_accumulator,
                    invocation_id=invocation_id,
                    elapsed_ms=elapsed_ms,
                    parsed=parsed,
                )
            logger.info(
                "console_llm_call flavor=%s tokens_in=%s tokens_out=%s cost_usd=%s duration_ms=%s",
                self.flavor.name,
                parsed.input_tokens,
                parsed.output_tokens,
                parsed.provider_reported_notional_usd,
                parsed.duration_ms,
            )
            provider_cost = parsed.provider_reported_notional_usd
            notional_usd = provider_cost if self.subscription_mode else None
            estimated_usd = None if self.subscription_mode else provider_cost
            return LLMResponse(
                text=parsed.text,
                model=_model_from_request(request) or self.model or self.flavor.name,
                input_tokens=parsed.input_tokens,
                output_tokens=parsed.output_tokens,
                total_tokens=parsed.input_tokens + parsed.output_tokens,
                estimated_usd=estimated_usd,
                cost_class="subscription_notional" if self.subscription_mode else "metered",
                notional_usd=notional_usd,
                normalized_usage=parsed.normalized_usage,
                usage_error=parsed.usage_error,
                usage_diagnostic=parsed.usage_diagnostic,
                provider_reported_notional_usd=(
                    provider_cost if self.subscription_mode else None
                ),
                invocation_id=invocation_id,
                elapsed_ms=elapsed_ms,
                raw=output,
                metadata={
                    "invocation_id": invocation_id,
                    "process_execution_bound": external.metadata.get(
                        "process_execution_bound", process_bound.metadata()
                    ),
                    # v0.10.1: the console door surfaces the same bounded-capture truth as every
                    # other process-backed door — one invariant, no drift.
                    **(
                        {"process_io": external.metadata["process_io"]}
                        if isinstance(external.metadata.get("process_io"), dict)
                        else {}
                    ),
                },
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
    Bridges the sync interface to the engine's shared async process owner (bounded stdin,
    process-tree termination, and reap). Text-only: multimodal message parts are rejected loudly.
    The caller passes
    ``cost_class="subscription_notional"`` to its metering wrapper (via the backend
    registry) — CLI subscriptions report notional cost, never phantom metered USD.
    """

    def __init__(
        self,
        flavor: CliFlavor,
        *,
        timeout_s: float = 240.0,
        extra_argv: Sequence[str] = (),
        external_runner: ExternalProcessCapability | None = None,
        model: str | None = None,
    ) -> None:
        self.flavor = flavor
        self.timeout_s = float(timeout_s)
        self.extra_argv = list(extra_argv)
        self.external_runner = external_runner or ExternalProcessCapability(
            name=f"console_chat_{flavor.name}"
        )
        self.model = model
        self.provider_label = flavor.name
        self.cost_class = "subscription_notional"

    def invoke(self, messages: Any) -> Any:
        prompt = _flatten_langchain_messages(messages)
        # 5R: inside an engine run the ambient invocation window bounds this call — the
        # model's own timeout_s may only NARROW it; standalone calls keep it as-is.
        # (ContextVars propagate into worker threads via copy_context/to_thread.)
        process_bound = _console_effective_bound(
            self.timeout_s, owner=f"console_chat[{self.flavor.name}]"
        )
        with tempfile.TemporaryDirectory(prefix="ai-workflow-console-") as workspace:
            extra_argv = list(self.extra_argv)
            if self.flavor.name == codex_exec.name:
                extra_argv = [*codex_model_argv(self.model, extra_argv), *extra_argv]
            elif self.flavor.name == claude_p.name:
                extra_argv = [
                    *claude_control_argv(self.model, None, extra_argv),
                    *extra_argv,
                ]
            invocation = _build_console_invocation(
                self.flavor, prompt, Path(workspace), extra_argv
            )
            usage_accumulator = usage_accumulator_for(self.flavor)
            identity = current_llm_usage_identity()
            invocation_id = (
                identity.invocation_id
                if identity is not None
                else new_provider_invocation_id()
            )
            started = time.monotonic()
            try:
                external = _run_external_process_sync(
                    self.external_runner,
                    ExternalProcessRequest(
                        command=invocation.argv,
                        cwd=workspace,
                        timeout_s=process_bound.timeout_s,
                        stdin_data=invocation.stdin_data,
                        result_file=invocation.result_file,
                        kill_grace_s=process_bound.kill_grace_s,
                        stdout_observer=(
                            usage_accumulator.feed
                            if usage_accumulator is not None
                            else None
                        ),
                        metadata={
                            "flavor": self.flavor.name,
                            "console_chat": True,
                            "invocation_id": invocation_id,
                            "process_execution_bound": process_bound.metadata(),
                        },
                    ),
                )
            except asyncio.CancelledError:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                parsed_cancelled = (
                    parsed_cli_output_from_accumulator({}, usage_accumulator)
                    if usage_accumulator is not None
                    else None
                )
                if identity is not None and parsed_cancelled is not None:
                    from ai_workflow_engine.models import WorkflowUsageEvent
                    from ai_workflow_engine.usage_events import record_usage_event

                    record_usage_event(
                        WorkflowUsageEvent(
                            provider=self.flavor.name,
                            operation="chat",
                            cost_class=identity.cost_class,
                            node=identity.node,
                            model=identity.model or self.model or self.flavor.name,
                            attempt=identity.attempt,
                            invocation_id=invocation_id,
                            input_tokens=parsed_cancelled.input_tokens,
                            output_tokens=parsed_cancelled.output_tokens,
                            total_tokens=(
                                parsed_cancelled.input_tokens
                                + parsed_cancelled.output_tokens
                            ),
                            input_token_details={
                                "cache_read": parsed_cancelled.cache_read_tokens,
                                "cache_creation": parsed_cancelled.cache_creation_tokens,
                            },
                            output_token_details={
                                "reasoning": parsed_cancelled.reasoning_output_tokens,
                            },
                            normalized_usage=parsed_cancelled.normalized_usage,
                            usage_error=parsed_cancelled.usage_error,
                            usage_diagnostic=parsed_cancelled.usage_diagnostic,
                            provider_reported_notional_usd=(
                                parsed_cancelled.provider_reported_notional_usd
                                if identity.cost_class == "subscription_notional"
                                else None
                            ),
                            estimated_usd=(
                                parsed_cancelled.provider_reported_notional_usd
                                if identity.cost_class == "metered"
                                else None
                            ),
                            success=False,
                            error="caller cancellation",
                            elapsed_ms=elapsed_ms,
                            metadata={"cancelled": True},
                        )
                    )
                raise
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"console CLI binary not found for flavor {self.flavor.name!r} "
                    f"({invocation.argv[0]!r}) — install/authenticate it in this runtime "
                    "or route the role back to an API model"
                ) from exc
            output = external.output if isinstance(external.output, dict) else {}
            elapsed_ms = int((time.monotonic() - started) * 1000)
            parsed = (
                parsed_cli_output_from_accumulator(output, usage_accumulator)
                if usage_accumulator is not None
                else parse_cli_process_output(self.flavor, output)
            )
            if external.status == "partial":
                raise _console_failure(
                    self.flavor,
                    external,
                    output,
                    model=self.model or self.flavor.name,
                    usage_accumulator=usage_accumulator,
                    invocation_id=invocation_id,
                    elapsed_ms=elapsed_ms,
                    parsed=parsed,
                )
            if (
                external.status != "accepted"
                or output.get("returncode") != 0
                or parsed.provider_error
            ):
                raise _console_failure(
                    self.flavor,
                    external,
                    output,
                    model=self.model or self.flavor.name,
                    usage_accumulator=usage_accumulator,
                    invocation_id=invocation_id,
                    elapsed_ms=elapsed_ms,
                    parsed=parsed,
                )
            logger.info(
                "console_chat_call flavor=%s tokens_in=%s tokens_out=%s cost_usd=%s duration_ms=%s",
                self.flavor.name,
                parsed.input_tokens,
                parsed.output_tokens,
                parsed.provider_reported_notional_usd,
                parsed.duration_ms,
            )
            if not parsed.text.strip():
                raise RuntimeError(f"console CLI returned no text ({self.flavor.name})")
            return _ConsoleChatReply(
                content=parsed.text,
                raw=output,
                response_metadata={
                    "invocation_id": invocation_id,
                    "process_execution_bound": external.metadata.get(
                        "process_execution_bound", process_bound.metadata()
                    ),
                    **(
                        {"process_io": external.metadata["process_io"]}
                        if isinstance(external.metadata.get("process_io"), dict)
                        else {}
                    ),
                },
                usage_metadata={
                    "input_tokens": parsed.input_tokens,
                    "output_tokens": parsed.output_tokens,
                    "total_tokens": parsed.input_tokens + parsed.output_tokens,
                    "input_token_details": {
                        "cache_read": parsed.cache_read_tokens,
                        "cache_creation": parsed.cache_creation_tokens,
                    },
                    "output_token_details": {
                        "reasoning": parsed.reasoning_output_tokens,
                    },
                },
                normalized_usage=parsed.normalized_usage,
                usage_error=parsed.usage_error,
                usage_diagnostic=parsed.usage_diagnostic,
                provider_reported_notional_usd=parsed.provider_reported_notional_usd,
            )


class _ConsoleChatReply:
    """Minimal chat-response shape with optional transport metadata."""

    def __init__(
        self,
        content: str,
        raw: dict[str, Any],
        response_metadata: dict[str, Any] | None = None,
        usage_metadata: dict[str, Any] | None = None,
        normalized_usage: Any = None,
        usage_error: Any = None,
        usage_diagnostic: str | None = None,
        provider_reported_notional_usd: float | None = None,
    ):
        self.content = content
        self.raw = raw
        self.response_metadata = dict(response_metadata or {})
        self.usage_metadata = dict(usage_metadata or {})
        self.normalized_usage = normalized_usage
        self.usage_error = usage_error
        self.usage_diagnostic = usage_diagnostic
        self.provider_reported_notional_usd = provider_reported_notional_usd


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
    *,
    image_paths: Sequence[str] = (),
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
        # Same single transport owner as the agent capability: the stdin marker keeps the
        # prompt's argv position, so the variadic --image below still parses as before.
        prompt_argv, stdin_data = codex_prompt_transport(prompt)
        return CliAgentInvocation(
            argv=[
                *flavor.base_argv,
                "--sandbox",
                "workspace-write",
                "--cd",
                str(workspace),
                "--output-last-message",
                str(result_file),
                *codex_structured_output_argv(extra_argv),
                *extra_argv,
                *prompt_argv,
                *(["--image", *image_paths] if image_paths else []),
            ],
            stdin_data=stdin_data,
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


def _console_failure(
    flavor: CliFlavor,
    external: Any,
    output: dict,
    *,
    model: str,
    usage_accumulator: CliUsageAccumulator | None = None,
    invocation_id: str,
    elapsed_ms: int,
    parsed: ParsedCliOutput | None = None,
) -> "ConsoleCliError":
    """Build the typed console failure from whatever the runner captured."""

    aborted = parsed or (
        parsed_cli_output_from_accumulator(output, usage_accumulator)
        if usage_accumulator is not None
        else parse_cli_process_output(flavor, output)
    )
    subtype = aborted.provider_error_subtype or ""
    returncode = output.get("returncode")
    stderr = str(output.get("stderr") or "")
    consumed = (
        f"; consumed notional ~${aborted.provider_reported_notional_usd:.4f}"
        if aborted.provider_reported_notional_usd is not None
        else ""
    )
    base = external.error or (
        "console CLI reported an error result"
        if aborted.provider_error
        else f"console CLI exited with status {external.status}"
    )
    lowered = f"{base} {stderr}".lower()
    if subtype == "error_max_budget_usd":
        failure_kind = "cap"
    elif "timed out" in lowered or "timeout" in lowered:
        failure_kind = "timeout"
    else:
        failure_kind = "provider"
    return ConsoleCliError(
        f"{base}{f' ({subtype})' if subtype else ''}{consumed}: {stderr[-800:]}",
        cli_subtype=subtype,
        notional_usd=aborted.provider_reported_notional_usd,
        returncode=returncode if isinstance(returncode, int) else None,
        num_turns=aborted.num_turns,
        failure_kind=failure_kind,
        process_execution_bound=getattr(external, "metadata", {}).get(
            "process_execution_bound", {}
        ),
        model=model,
        normalized_usage=aborted.normalized_usage,
        usage_error=aborted.usage_error,
        usage_diagnostic=aborted.usage_diagnostic,
        invocation_id=invocation_id,
        elapsed_ms=elapsed_ms,
    )


def _model_from_request(request: LLMRequest) -> str:
    profile = request.metadata.get("model_profile")
    if isinstance(profile, dict):
        model = profile.get("model")
        if isinstance(model, str):
            return model
    return ""


__all__ = ["ConsoleCliError", "ConsoleLLMClient"]
