"""Console LLM client backed by a non-interactive CLI flavor."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Sequence

from ai_workflow_engine.engine.external import ExternalProcessCapability, ExternalProcessRequest
from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest, LLMResponse

from .capability import parse_cli_process_output
from .flavors import claude_p, codex_exec
from .models import CliAgentInvocation, CliFlavor


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
    ) -> None:
        self.flavor = flavor
        self.timeout_s = timeout_s
        self.subscription_mode = subscription_mode
        self.extra_argv = list(extra_argv)
        self.external_runner = external_runner or ExternalProcessCapability()

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)
        prompt = _flatten_request(request)
        with tempfile.TemporaryDirectory(prefix="ai-workflow-console-") as workspace:
            invocation = _build_console_invocation(self.flavor, prompt, Path(workspace), self.extra_argv)
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
                raise RuntimeError(f"console CLI exited {output.get('returncode')}: {stderr[-800:]}")
            parsed = parse_cli_process_output(self.flavor, output)
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
        if request.images or any(message.images for message in request.messages):
            raise ValueError(
                "ConsoleLLMClient cannot receive images; use StructuredVisionLLMNode with an API client "
                "or CliAgentCapability with workspace assets"
            )
        for message in request.messages:
            if any(result.images for result in message.tool_results):
                raise ValueError(
                    "ConsoleLLMClient cannot receive tool-result images; use CliAgentCapability"
                )
        if request.tools or request.tool_choice:
            raise ValueError("ConsoleLLMClient cannot host tool-calling turns")


def _build_console_invocation(
    flavor: CliFlavor,
    prompt: str,
    workspace: Path,
    extra_argv: list[str],
) -> CliAgentInvocation:
    workspace.mkdir(parents=True, exist_ok=True)
    if flavor.name == claude_p.name:
        return CliAgentInvocation(
            argv=[*flavor.base_argv, "--output-format", "json", *extra_argv],
            stdin_data=prompt,
        )
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
