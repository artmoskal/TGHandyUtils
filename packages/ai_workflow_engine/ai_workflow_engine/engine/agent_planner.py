"""LLM-backed and replay-backed planners for bounded agent episodes."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Literal, Optional

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, ValidationError

from ai_workflow_engine.engine.agent import AgentCapability
from ai_workflow_engine._runtime_state import current_observation_capture
from ai_workflow_engine.parsing import STRUCTURED_REPAIR_PROMPT
from ai_workflow_engine.engine.capabilities import CapabilityRegistry, CapabilityRuntime
from ai_workflow_engine.llm_protocol import ChatMessage, LLMCallable, LLMRequest, ToolSpec
from ai_workflow_engine.llm_protocol import record_callable_usage
from ai_workflow_engine.memory import AgentMemory, AgentMemoryRenderContext, resolve_agent_memory
from ai_workflow_engine.models import AgentRunRequest, AgentStepDecision, AgentToolCall, AgentToolStep, CapabilityContext
from ai_workflow_engine.models import CapabilitySpec, EvidenceRef
from ai_workflow_engine.observability_capture import (
    ObservationCapture,
    engine_worker_observation_scope,
    llm_request_payload,
    llm_response_payload,
)
from ai_workflow_engine.usage import check_budget_before_call, check_images_per_call, check_input_tokens_per_call
from ai_workflow_engine.usage import estimate_text_tokens
from ai_workflow_engine.vision import ImageInput

logger = logging.getLogger(__name__)


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
        memory: Optional[AgentMemory | str | Mapping[str, Any]] = None,
        trace_sink: Any = None,
        detail_sink: Any = None,
        capture_detail_text: bool = False,
    ) -> None:
        self.llm = llm
        self.tool_specs = dict(tool_specs)
        self.system_prompt = system_prompt
        self.output_model = output_model
        self.pre_parse = pre_parse
        self.max_repair_rounds = max(0, max_repair_rounds)
        self.node_name = node_name
        self.image_loader = image_loader
        self.memory = resolve_agent_memory(memory)
        self.trace_sink = trace_sink
        self.detail_sink = detail_sink
        self._capture_detail_text = capture_detail_text
        self.observation = ObservationCapture(
            trace_sink,
            detail_sink=detail_sink,
            mode="full" if capture_detail_text else "off",
        )
        self.parser = PydanticOutputParser(pydantic_object=output_model) if output_model is not None else None

    async def next_step(
        self,
        context: CapabilityContext,
        request: AgentRunRequest,
        history: list[AgentToolStep],
    ) -> AgentStepDecision:
        base_messages = self._messages_from_history(request, history, context)
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
                    # Per-run passthrough: the consumer's AgentRunRequest.metadata reaches the
                    # LLMCallable (e.g. an on_token sink / session id for per-call TTS routing in a
                    # voice brain). Engine observability keys are applied AFTER so they always win.
                    **request.metadata,
                    "agent_node": self.node_name,
                    "workflow_id": context.run_context.workflow_id,
                    "workflow_type": context.run_context.workflow_type,
                    "repair_round": repair_round,
                },
            )
            self._check_turn_budget(llm_request)
            self._record_llm_request(llm_request, history, repair_round)
            try:
                with engine_worker_observation_scope():
                    response = await self.llm(llm_request)
            except Exception as exc:
                self._record_llm_error(llm_request, history, repair_round, exc)
                raise
            self._record_llm_response(llm_request, response, history, repair_round)
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

    def _messages_from_history(
        self,
        request: AgentRunRequest,
        history: Sequence[AgentToolStep],
        context: CapabilityContext | None = None,
    ) -> list[ChatMessage]:
        memory = self._effective_memory(context)
        messages = memory.render(request, history, self._memory_render_context())
        self._record_memory_projection(request, history, messages, context, memory)
        return messages

    def _effective_memory(self, context: CapabilityContext | None) -> AgentMemory:
        """Node-scoped memory selection (GoPro R4): a node-level ``memory=`` config wins
        over the construction-time default. The config was already resolved loudly at
        graph validation; re-resolving per call is cheap and keeps memory stateless."""

        metadata = getattr(context, "metadata", None) if context is not None else None
        config = metadata.get("agent_memory") if isinstance(metadata, dict) else None
        if config is None:
            return self.memory
        return resolve_agent_memory(config)

    def _record_memory_projection(
        self,
        request: AgentRunRequest,
        history: Sequence[AgentToolStep],
        messages: Sequence[ChatMessage],
        context: CapabilityContext | None,
        memory: AgentMemory | None = None,
    ) -> None:
        memory = memory if memory is not None else self.memory
        # AC-S5: counts/labels only in metadata — the state block's TEXT rides the
        # existing detail-capture path (payload), so it is absent when capture is off.
        stats: dict[str, Any] = {}
        stats_fn = getattr(memory, "projection_stats", None)
        if callable(stats_fn):
            stats = dict(stats_fn(request, history))
        self._observation_capture().record(
            node=self.node_name,
            attempt=len(history) + 1,
            decision="memory:projection",
            phase="memory:projection",
            kind="memory_projection",
            payload={
                "memory_mode": memory.__class__.__name__,
                "prompt": request.prompt,
                "history": list(history),
                "messages": list(messages),
            },
            metadata={
                "memory_mode": memory.__class__.__name__,
                "history_steps": len(history),
                "message_count": len(messages),
                "image_count": self._image_count(messages),
                **stats,
                "workflow_id": context.run_context.workflow_id if context else None,
                "workflow_type": context.run_context.workflow_type if context else None,
            },
            digest_metadata_key="projection_digest",
        )

    def _record_llm_request(
        self,
        request: LLMRequest,
        history: Sequence[AgentToolStep],
        repair_round: int,
    ) -> None:
        self._observation_capture().record(
            node=self.node_name,
            attempt=len(history) + repair_round + 1,
            decision="llm:request",
            phase="llm:request",
            kind="rendered_prompt",
            payload=llm_request_payload(request),
            metadata={
                "workflow_id": request.metadata.get("workflow_id"),
                "workflow_type": request.metadata.get("workflow_type"),
                "repair_round": repair_round,
                "message_count": len(request.messages),
                "tool_count": len(request.tools),
            },
            digest_metadata_key="prompt_digest",
        )

    def _record_llm_response(
        self,
        request: LLMRequest,
        response: Any,
        history: Sequence[AgentToolStep],
        repair_round: int,
    ) -> None:
        self._observation_capture().record(
            node=self.node_name,
            attempt=len(history) + repair_round + 1,
            decision="llm:response",
            phase="llm:response",
            kind="llm_response",
            payload=llm_response_payload(response),
            metadata={
                "workflow_id": request.metadata.get("workflow_id"),
                "workflow_type": request.metadata.get("workflow_type"),
                "repair_round": repair_round,
                "model": getattr(response, "model", ""),
                "tool_call_count": len(getattr(response, "tool_calls", [])),
                "total_tokens": getattr(response, "total_tokens", 0),
            },
            digest_metadata_key="response_digest",
        )

    def _record_llm_error(
        self,
        request: LLMRequest,
        history: Sequence[AgentToolStep],
        repair_round: int,
        exc: Exception,
    ) -> None:
        error = str(exc) or exc.__class__.__name__
        self._observation_capture().record(
            node=self.node_name,
            attempt=len(history) + repair_round + 1,
            decision="llm:response",
            phase="llm:response",
            kind="llm_response",
            payload={"error_type": exc.__class__.__name__, "error": error},
            severity="error",
            error=error,
            metadata={
                "workflow_id": request.metadata.get("workflow_id"),
                "workflow_type": request.metadata.get("workflow_type"),
                "repair_round": repair_round,
                "error_type": exc.__class__.__name__,
            },
            digest_metadata_key="response_digest",
        )

    def _observation_capture(self) -> ObservationCapture:
        return current_observation_capture() or self.observation

    def _memory_render_context(self) -> AgentMemoryRenderContext:
        return AgentMemoryRenderContext(
            tool_content=self._tool_content,
            images_from_output=self._images_from_output,
            system_prompt=self.system_prompt,
        )

    def _images_from_output(self, value: Any) -> list[ImageInput]:
        if isinstance(value, ImageInput):
            return [value]
        if isinstance(value, EvidenceRef):
            if value.media_type.startswith("image/") and self.image_loader is not None:
                return [ImageInput.from_evidence(value, self.image_loader)]
            return []
        if isinstance(value, Mapping):
            image = self._coerce_image_input(value)
            if image is not None:
                return [image]

            ref = self._coerce_evidence_ref(value)
            if ref is not None:
                return self._images_from_output(ref)
            return self._images_from_items(value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return self._images_from_items(value)
        return []

    def _coerce_image_input(self, value: Mapping[Any, Any]) -> ImageInput | None:
        try:
            return ImageInput.model_validate(value)
        except ValidationError as exc:
            if _looks_like_image_input(value):
                logger.warning(
                    "agent_planner_invalid_image_output node=%s error=%s",
                    self.node_name,
                    str(exc),
                )
            return None

    def _coerce_evidence_ref(self, value: Mapping[Any, Any]) -> EvidenceRef | None:
        try:
            return EvidenceRef.model_validate(value)
        except ValidationError as exc:
            if _looks_like_evidence_ref(value):
                logger.warning(
                    "agent_planner_invalid_evidence_output node=%s error=%s",
                    self.node_name,
                    str(exc),
                )
            return None

    def _images_from_items(self, values: Iterable[Any]) -> list[ImageInput]:
        images: list[ImageInput] = []
        for item in values:
            images.extend(self._images_from_output(item))
        return images

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


def _looks_like_image_input(value: Mapping[Any, Any]) -> bool:
    return bool({"source", "data"} & set(value.keys()))


def _looks_like_evidence_ref(value: Mapping[Any, Any]) -> bool:
    return bool({"ref_id", "uri"} & set(value.keys()))


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

    if runtime is None and (
        planner_kwargs.get("detail_sink") is not None
        or planner_kwargs.get("capture_detail_text")
    ):
        raise ValueError(
            "Agent observability detail capture requires runtime=engine.runtime; "
            "otherwise prompt/tool/memory details can land in a private or missing sink"
        )
    specs = {spec.name: spec for spec in registry.list_specs()}
    tool_specs = {tool_name: _tool_spec_from_capability(specs[tool_name]) for tool_name in allowed_tools}
    tool_runtime = runtime or CapabilityRuntime(registry, trace_sink)
    planner_kwargs.setdefault("trace_sink", tool_runtime.trace_sink)
    planner_kwargs.setdefault("detail_sink", tool_runtime.detail_sink)
    planner_kwargs.setdefault("capture_detail_text", getattr(tool_runtime, "_capture_detail_text", False))
    planner = LLMAgentPlanner(llm, tool_specs=tool_specs, **planner_kwargs)
    return AgentCapability(
        planner,
        tool_runtime,
        name=name,
        side_effects=side_effects,
    )


def _tool_spec_from_capability(spec: CapabilitySpec) -> ToolSpec:
    input_schema = spec.input_model.model_json_schema() if spec.input_model is not None else {}
    return ToolSpec(name=spec.name, description=spec.description, input_schema=input_schema)
