"""LLM-backed and replay-backed planners for bounded agent episodes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Optional

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel

from ai_workflow_engine.engine.agent import AgentCapability
from ai_workflow_engine.parsing import STRUCTURED_REPAIR_PROMPT
from ai_workflow_engine.engine.capabilities import CapabilityRegistry, CapabilityRuntime
from ai_workflow_engine.llm_protocol import ChatMessage, LLMCallable, LLMRequest, ToolCallRequest, ToolResult, ToolSpec
from ai_workflow_engine.llm_protocol import record_callable_usage
from ai_workflow_engine.models import AgentRunRequest, AgentStepDecision, AgentToolCall, AgentToolStep, CapabilityContext
from ai_workflow_engine.models import CapabilitySpec, EvidenceRef
from ai_workflow_engine.usage import check_budget_before_call, check_images_per_call, check_input_tokens_per_call
from ai_workflow_engine.usage import estimate_text_tokens
from ai_workflow_engine.vision import ImageInput


class LLMAgentPlanner:
    """AgentEpisodePlanner driven by a plain LLMCallable with tool-calling support."""

    _REPAIR_PROMPT = STRUCTURED_REPAIR_PROMPT

    def __init__(
        self,
        llm: LLMCallable,
        *,
        tool_specs: Mapping[str, ToolSpec],
        system_prompt: Optional[str] = None,
        output_model: Optional[type[BaseModel]] = None,
        pre_parse: Optional[Callable[[str], str]] = None,
        max_repair_rounds: int = 1,
        node_name: str = "agent_planner",
        image_loader: Optional[Callable[[EvidenceRef], bytes]] = None,
    ) -> None:
        self.llm = llm
        self.tool_specs = dict(tool_specs)
        self.system_prompt = system_prompt
        self.output_model = output_model
        self.pre_parse = pre_parse
        self.max_repair_rounds = max(0, max_repair_rounds)
        self.node_name = node_name
        self.image_loader = image_loader
        self.parser = PydanticOutputParser(pydantic_object=output_model) if output_model is not None else None

    async def next_step(
        self,
        context: CapabilityContext,
        request: AgentRunRequest,
        history: list[AgentToolStep],
    ) -> AgentStepDecision:
        base_messages = self._messages_from_history(request, history)
        active_tools = [self.tool_specs[name] for name in request.allowed_tools if name in self.tool_specs]
        last_error = ""
        last_text = ""

        for repair_round in range(self.max_repair_rounds + 1):
            messages = list(base_messages)
            if repair_round:
                messages.append(ChatMessage(role="user", content=self._repair_prompt(last_error, request.prompt)))

            llm_request = LLMRequest(
                messages=messages,
                tools=active_tools,
                metadata={
                    "agent_node": self.node_name,
                    "workflow_id": context.run_context.workflow_id,
                    "workflow_type": context.run_context.workflow_type,
                    "repair_round": repair_round,
                },
            )
            self._check_turn_budget(llm_request)
            response = await self.llm(llm_request)
            # Honor client-declared cost truth first (e.g. ConsoleLLMClient reports subscription
            # cost via response.notional_usd with estimated_usd=None); fall back to the episode's
            # subscription_mode. Never lose a reported notional cost (cost-honesty, RC2).
            subscription = request.subscription_mode or response.cost_class == "subscription_notional"
            notional = response.notional_usd
            if notional is None and subscription:
                notional = response.estimated_usd
            record_callable_usage(
                response,
                node=self.node_name,
                attempt=len(history) + repair_round + 1,
                metadata={"agent_planner": True},
                cost_class="subscription_notional" if subscription else "metered",
                notional_usd=notional,
            )

            if response.tool_calls:
                call = response.tool_calls[0]
                return AgentStepDecision(
                    action="tool",
                    tool_name=call.name,
                    payload=dict(call.arguments),
                    rationale=response.text or response.stop_reason or "",
                )

            last_text = response.text
            try:
                output = self._finish_output(response.text)
                return AgentStepDecision(action="finish", output=output)
            except Exception as exc:
                last_error = str(exc)

        return AgentStepDecision(
            action="fail",
            output=last_text,
            rationale=f"{self.node_name} finish parsing failed: {last_error}",
        )

    def _messages_from_history(self, request: AgentRunRequest, history: Sequence[AgentToolStep]) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        if self.system_prompt:
            messages.append(ChatMessage(role="system", content=self.system_prompt))
        messages.append(ChatMessage(role="user", content=request.prompt))
        for index, step in enumerate(history, start=1):
            call_id = f"step-{index}-{step.call.tool_name}"
            messages.append(
                ChatMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCallRequest(
                            call_id=call_id,
                            name=step.call.tool_name,
                            arguments=dict(step.call.payload),
                        )
                    ],
                )
            )
            images = self._images_from_output(step.output)
            messages.append(
                ChatMessage(
                    role="tool",
                    content=self._tool_content(step),
                    tool_results=[
                        ToolResult(
                            call_id=call_id,
                            content=self._tool_content(step),
                            images=images,
                            is_error=step.status != "accepted",
                        )
                    ],
                )
            )
        return messages

    def _images_from_output(self, value: Any) -> list[ImageInput]:
        if isinstance(value, ImageInput):
            return [value]
        if isinstance(value, EvidenceRef):
            if value.media_type.startswith("image/") and self.image_loader is not None:
                return [ImageInput.from_evidence(value, self.image_loader)]
            return []
        if isinstance(value, Mapping):
            image = None
            try:
                image = ImageInput.model_validate(value)
            except Exception:
                image = None
            if image is not None:
                return [image]

            ref = None
            try:
                ref = EvidenceRef.model_validate(value)
            except Exception:
                ref = None
            if ref is not None:
                return self._images_from_output(ref)
            images: list[ImageInput] = []
            for item in value.values():
                images.extend(self._images_from_output(item))
            return images
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            images: list[ImageInput] = []
            for item in value:
                images.extend(self._images_from_output(item))
            return images
        return []

    def _finish_output(self, text: str) -> Any:
        if self.output_model is None:
            return text
        cleaned = self.pre_parse(text) if self.pre_parse is not None else text
        return self.parser.parse(cleaned)

    def _check_turn_budget(self, request: LLMRequest) -> None:
        check_input_tokens_per_call(self._estimated_text_tokens(request.messages), self.node_name)
        check_images_per_call(self._image_count(request.messages), self.node_name)
        check_budget_before_call("chat", self.node_name)

    @staticmethod
    def _estimated_text_tokens(messages: Sequence[ChatMessage]) -> int:
        parts: list[Any] = []
        for message in messages:
            parts.append(message.content)
            parts.extend(call.arguments for call in message.tool_calls)
            parts.extend(result.content for result in message.tool_results)
        return estimate_text_tokens(parts)

    @staticmethod
    def _image_count(messages: Sequence[ChatMessage]) -> int:
        return sum(len(result.images) for message in messages for result in message.tool_results)

    @classmethod
    def _repair_prompt(cls, error: str, original_prompt: str) -> str:
        return cls._REPAIR_PROMPT.format(error=error, original_prompt=original_prompt)

    @staticmethod
    def _tool_content(step: AgentToolStep) -> str:
        if step.error:
            return step.error
        output = step.output
        if isinstance(output, ImageInput):
            return json.dumps({"image": output.fingerprint()}, sort_keys=True)
        if isinstance(output, BaseModel):
            return output.model_dump_json()
        try:
            return json.dumps(output, sort_keys=True, default=str)
        except TypeError:
            return str(output)


ReplayCompare = Callable[[AgentToolStep, AgentToolStep], Optional[str]]
_MISSING_FINAL_OUTPUT = object()


class ReplayPlanner:
    """Replay recorded agent tool steps without making LLM calls."""

    def __init__(
        self,
        recorded_steps: Sequence[AgentToolStep],
        *,
        on_divergence: Literal["fail", "finish_partial"] = "fail",
        compare: Optional[ReplayCompare] = None,
        final_output: Any = _MISSING_FINAL_OUTPUT,
    ) -> None:
        if on_divergence not in {"fail", "finish_partial"}:
            raise ValueError("on_divergence must be 'fail' or 'finish_partial'")
        self.recorded_steps = list(recorded_steps)
        self.on_divergence = on_divergence
        self.compare = compare or self._compare_status
        self.final_output = final_output

    def next_step(
        self,
        context: CapabilityContext,
        request: AgentRunRequest,
        history: list[AgentToolStep],
    ) -> AgentStepDecision:
        divergence = self._first_divergence(history)
        if divergence:
            output = {"replayed_steps": min(len(history), len(self.recorded_steps)), "diverged": True}
            output["divergence"] = divergence
            if self.on_divergence == "finish_partial":
                return AgentStepDecision(action="finish", output=output, rationale=divergence)
            return AgentStepDecision(action="fail", output=output, rationale=divergence)

        if len(history) >= len(self.recorded_steps):
            output = {"replayed_steps": len(self.recorded_steps), "diverged": False}
            if self.final_output is not _MISSING_FINAL_OUTPUT:
                output = self.final_output
            return AgentStepDecision(action="finish", output=output)

        call = self.recorded_steps[len(history)].call
        return AgentStepDecision(
            action="tool",
            tool_name=call.tool_name,
            payload=dict(call.payload),
            rationale=call.rationale,
        )

    def _first_divergence(self, history: Sequence[AgentToolStep]) -> Optional[str]:
        if len(history) > len(self.recorded_steps):
            return f"unexpected extra replay step: {len(history)} > {len(self.recorded_steps)}"
        for index, live in enumerate(history):
            expected = self.recorded_steps[index]
            divergence = self.compare(expected, live)
            if divergence:
                return f"step {index + 1}: {divergence}"
        return None

    @staticmethod
    def _compare_status(expected: AgentToolStep, live: AgentToolStep) -> Optional[str]:
        if expected.status != live.status:
            return f"status {live.status!r} != recorded {expected.status!r}"
        return None


def build_llm_agent_capability(
    llm: LLMCallable,
    registry: CapabilityRegistry,
    *,
    allowed_tools: Sequence[str],
    name: str = "agent_episode",
    side_effects: tuple[str, ...] = (),
    runtime: CapabilityRuntime | None = None,
    trace_sink: Any = None,
    **planner_kwargs: Any,
) -> AgentCapability:
    """Build an LLM-driven agent capability over registry tools.

    Pass the engine's runtime (or at least its trace sink) so the episode's per-tool trace events
    land in the SAME sink as the rest of the workflow (one unified trace, not a private island):
    ``build_llm_agent_capability(llm, engine.registry, runtime=engine.runtime, ...)``.
    """

    specs = {spec.name: spec for spec in registry.list_specs()}
    tool_specs = {tool_name: _tool_spec_from_capability(specs[tool_name]) for tool_name in allowed_tools}
    planner = LLMAgentPlanner(llm, tool_specs=tool_specs, **planner_kwargs)
    tool_runtime = runtime or CapabilityRuntime(registry, trace_sink)
    return AgentCapability(
        planner,
        tool_runtime,
        name=name,
        side_effects=side_effects,
    )


def _tool_spec_from_capability(spec: CapabilitySpec) -> ToolSpec:
    input_schema = spec.input_model.model_json_schema() if spec.input_model is not None else {}
    return ToolSpec(name=spec.name, description=spec.description, input_schema=input_schema)
