"""Workflow memory seams for bounded agent prompt history."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel, Field, ValidationError

from ai_workflow_engine.llm_protocol import ChatMessage, ToolCallRequest, ToolResult
from ai_workflow_engine.models import AgentRunRequest, AgentToolStep, EvidenceRef
from ai_workflow_engine.vision import ImageInput


class MemoryNamespace(NamedTuple):
    """Product-neutral memory scope.

    `tenant` is the hard isolation boundary. `subject` is the product-owned
    thing being remembered, such as a site origin, camera, project, or run
    family. `kind` is the record family inside that subject.
    """

    product: str
    tenant: str
    subject: str
    kind: str


class MemoryRecord(BaseModel):
    """One evidence-linked memory record stored under a structured namespace."""

    namespace: MemoryNamespace
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    schema_version: str = "v1"


@runtime_checkable
class MemoryStore(Protocol):
    """Deterministic memory store contract; durable/semantic adapters stay outside core."""

    def put(self, record: MemoryRecord, *, idempotency_key: str | None = None) -> MemoryRecord:
        ...

    def get(self, namespace: MemoryNamespace, key: str) -> MemoryRecord | None:
        ...

    def search(
        self,
        namespace: MemoryNamespace,
        *,
        query: str | None = None,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        ...

    def delete(self, namespace: MemoryNamespace, key: str) -> bool:
        ...


class InMemoryMemoryStore:
    """Single-process deterministic memory store for tests and development."""

    def __init__(self) -> None:
        self._records: dict[MemoryNamespace, dict[str, MemoryRecord]] = {}
        self._idempotency: dict[tuple[MemoryNamespace, str], MemoryRecord] = {}

    def put(self, record: MemoryRecord, *, idempotency_key: str | None = None) -> MemoryRecord:
        record = MemoryRecord.model_validate(record)
        if idempotency_key:
            idempotency_id = (record.namespace, idempotency_key)
            existing = self._idempotency.get(idempotency_id)
            if existing is not None:
                if existing != record:
                    raise ValueError("MemoryStore idempotency key reused for different record")
                return existing
            self._idempotency[idempotency_id] = record
        self._records.setdefault(record.namespace, {})[record.key] = record
        return record

    def get(self, namespace: MemoryNamespace, key: str) -> MemoryRecord | None:
        namespace = _coerce_namespace(namespace)
        return self._records.get(namespace, {}).get(key)

    def search(
        self,
        namespace: MemoryNamespace,
        *,
        query: str | None = None,
        metadata_filter: Mapping[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        namespace = _coerce_namespace(namespace)
        records = list(self._records.get(namespace, {}).values())
        if metadata_filter:
            records = [
                record
                for record in records
                if all(record.metadata.get(key) == value for key, value in metadata_filter.items())
            ]
        if query:
            needle = query.lower()
            records = [
                record
                for record in records
                if needle in json.dumps(record.value, sort_keys=True, default=str).lower()
            ]
        return sorted(records, key=lambda record: record.key)

    def delete(self, namespace: MemoryNamespace, key: str) -> bool:
        namespace = _coerce_namespace(namespace)
        records = self._records.get(namespace)
        if not records or key not in records:
            return False
        del records[key]
        return True


def _coerce_namespace(namespace: MemoryNamespace | tuple[str, str, str, str]) -> MemoryNamespace:
    return MemoryNamespace(*namespace)


def resolve_agent_memory(memory: Any = None) -> AgentMemory:
    """Resolve config/profile-shaped memory selection into a concrete policy."""

    if memory is None:
        return FullReplayMemory()
    if hasattr(memory, "render") and callable(memory.render):
        return memory
    if isinstance(memory, str):
        return _agent_memory_from_mode(memory, {})
    if isinstance(memory, Mapping):
        raw_mode = memory.get("mode")
        if not raw_mode:
            raise ValueError("agent memory config requires mode")
        options = {key: value for key, value in memory.items() if key != "mode"}
        return _agent_memory_from_mode(str(raw_mode), options)
    raise TypeError(f"Unsupported agent memory config: {type(memory).__name__}")


def _agent_memory_from_mode(mode: str, options: Mapping[str, Any]) -> AgentMemory:
    normalized = mode.replace("-", "_").lower()
    if normalized == "full_replay":
        if options:
            raise ValueError("FullReplayMemory does not accept options")
        return FullReplayMemory()
    if normalized == "image_evicting":
        _reject_unknown_options(normalized, options, {"keep_last_images"})
        return ImageEvictingMemory(
            keep_last_images=int(options.get("keep_last_images", 1)),
        )
    if normalized == "structured_state":
        # reducer/render_state are CODE and stay construction-time; config selects
        # only the data-shaped knobs (nested base per the GoPro P1 request).
        _reject_unknown_options(normalized, options, {"base", "heading"})
        return StructuredStateMemory(
            base=options.get("base"),
            heading=str(options.get("heading", StructuredStateMemory.DEFAULT_HEADING)),
        )
    if normalized == "windowed":
        _reject_unknown_options(normalized, options, {"max_turns"})
        if "max_turns" not in options:
            raise ValueError("windowed memory requires max_turns")
        return WindowedMemory(max_turns=int(options["max_turns"]))
    if normalized == "compacting":
        _reject_unknown_options(normalized, options, {"inner", "token_threshold", "keep_last_turns"})
        if "token_threshold" not in options:
            raise ValueError("compacting memory requires token_threshold")
        return CompactingMemory(
            inner=options.get("inner"),
            token_threshold=int(options["token_threshold"]),
            keep_last_turns=int(options.get("keep_last_turns", 4)),
        )
    raise ValueError(f"Unknown agent memory mode: {mode}")


def _reject_unknown_options(mode: str, options: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise ValueError(f"agent memory mode {mode!r} does not accept options: {unknown}")


@dataclass(frozen=True)
class AgentMemoryRenderContext:
    """Rendering callbacks supplied by the planner; memory owns no control state."""

    tool_content: Callable[[AgentToolStep], str]
    images_from_output: Callable[[Any], list[ImageInput]]
    system_prompt: str | None = None


class AgentMemory(Protocol):
    """Projection from canonical agent history to LLM input messages."""

    def render(
        self,
        request: AgentRunRequest,
        history: Sequence[AgentToolStep],
        context: AgentMemoryRenderContext,
    ) -> list[ChatMessage]:
        ...


class FullReplayMemory:
    """Default memory: render every recorded tool step exactly as the planner did before."""

    def render(
        self,
        request: AgentRunRequest,
        history: Sequence[AgentToolStep],
        context: AgentMemoryRenderContext,
    ) -> list[ChatMessage]:
        return render_full_replay_messages(request, history, context)


class ImageEvictingMemory:
    """Prompt memory that keeps only the most recent image-bearing tool turns as images."""

    def __init__(self, *, keep_last_images: int = 1) -> None:
        if keep_last_images < 0:
            raise ValueError("keep_last_images must be >= 0")
        self.keep_last_images = keep_last_images

    def render(
        self,
        request: AgentRunRequest,
        history: Sequence[AgentToolStep],
        context: AgentMemoryRenderContext,
    ) -> list[ChatMessage]:
        keep_indices = self._kept_image_step_indices(history)
        messages: list[ChatMessage] = []
        if context.system_prompt:
            messages.append(ChatMessage(role="system", content=context.system_prompt))
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
            content = context.tool_content(step)
            evidence_refs = _image_evidence_refs(step.output)
            has_raw_images = _contains_image_input(step.output)
            image_bearing = bool(evidence_refs) or has_raw_images
            evicted = image_bearing and (index - 1) not in keep_indices
            if evicted:
                content = _append_eviction_notice(content, evidence_refs, has_raw_images)
                images: list[ImageInput] = []
            else:
                images = context.images_from_output(step.output)
                if evidence_refs and not images:
                    raise ValueError(
                        "ImageEvictingMemory kept image evidence but no image_loader produced an image"
                    )
            messages.append(
                ChatMessage(
                    role="tool",
                    content=content,
                    tool_results=[
                        ToolResult(
                            call_id=call_id,
                            content=content,
                            images=images,
                            is_error=step.status != "accepted",
                        )
                    ],
                )
            )
        return messages

    def _kept_image_step_indices(self, history: Sequence[AgentToolStep]) -> set[int]:
        image_steps = [
            index
            for index, step in enumerate(history)
            if _image_evidence_refs(step.output) or _contains_image_input(step.output)
        ]
        if self.keep_last_images == 0:
            return set()
        return set(image_steps[-self.keep_last_images :])


def render_full_replay_messages(
    request: AgentRunRequest,
    history: Sequence[AgentToolStep],
    context: AgentMemoryRenderContext,
) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    if context.system_prompt:
        messages.append(ChatMessage(role="system", content=context.system_prompt))
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
        content = context.tool_content(step)
        messages.append(
            ChatMessage(
                role="tool",
                content=content,
                tool_results=[
                    ToolResult(
                        call_id=call_id,
                        content=content,
                        images=context.images_from_output(step.output),
                        is_error=step.status != "accepted",
                    )
                ],
            )
        )
    return messages


# --- Structured-state / windowed / compacting policies (GoPro P1 request, named consumer) ---

# Pure derived-state hooks: same (request, history) -> same state, no mutation across turns.
StateReducer = Callable[[AgentRunRequest, Sequence[AgentToolStep]], Any]
StateRenderer = Callable[[Any], str]
# Compacts the DROPPED history prefix into one findings-preserving text block.
Compactor = Callable[[AgentRunRequest, Sequence[AgentToolStep]], str]


def default_tool_state_reducer(
    _request: AgentRunRequest, history: Sequence[AgentToolStep]
) -> dict[str, Any]:
    """Product-neutral working state: per tool — call count, per-call arg digests, ok/error.

    Enough for "visited / remaining"-style prompts without any product code. Deterministic:
    tools sorted by name, digests in call order.
    """

    tools: dict[str, dict[str, Any]] = {}
    for step in history:
        entry = tools.setdefault(step.call.tool_name, {"calls": 0, "args": [], "ok": 0, "error": 0})
        entry["calls"] += 1
        entry["args"].append(_call_args_digest(step.call.payload))
        if step.status == "accepted":
            entry["ok"] += 1
        else:
            entry["error"] += 1
    return {"turns": len(history), "tools": {name: tools[name] for name in sorted(tools)}}


def _call_args_digest(payload: Mapping[str, Any]) -> Any:
    if not payload:
        return ""
    if len(payload) == 1:
        value = next(iter(payload.values()))
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
    return json.dumps(dict(payload), sort_keys=True, default=str)


def default_state_renderer(state: Any) -> str:
    """Compact deterministic text block for the default reducer's state shape."""

    if not isinstance(state, Mapping):
        return json.dumps(state, sort_keys=True, default=str)
    lines = [f"turns: {state.get('turns', 0)}"]
    tools = state.get("tools", {})
    if isinstance(tools, Mapping):
        for name in sorted(tools):
            entry = tools[name]
            args = json.dumps(entry.get("args", []), sort_keys=True, default=str)
            lines.append(
                f"{name}: calls={entry.get('calls', 0)} ok={entry.get('ok', 0)} "
                f"error={entry.get('error', 0)} args={args}"
            )
    return "\n".join(lines)


def _assert_state_output_safe(state: Any, rendered: Any, *, who: str) -> str:
    """Byte-free, LOUDLY (GoPro AC-S6): never silently strip content from a prompt."""

    from ai_workflow_engine.engine.checkpoints import assert_checkpoint_payload_safe

    try:
        assert_checkpoint_payload_safe(state)
    except ValueError as exc:
        raise ValueError(
            f"{who} reducer output must be byte-free (no raw bytes / ImageInput): {exc}"
        ) from exc
    if not isinstance(rendered, str):
        raise ValueError(
            f"{who} renderer must return str, got {type(rendered).__name__} — "
            "render state to compact text, never transport objects"
        )
    return rendered


class StructuredStateMemory:
    """Inject derived working-state as compact structured facts (GoPro R1).

    Stateless: the state is derived from ``history`` at render time via a PURE reducer —
    never mutated across turns; replay-safe by construction. The state block is appended
    as the LAST memory-produced message (maximally salient to weak models); during repair
    rounds the planner's repair prompt lands after it — that order is deliberate and
    pinned by test.
    """

    DEFAULT_HEADING = "WORKING STATE (acknowledge before deciding)"

    def __init__(
        self,
        *,
        base: Any = None,
        reducer: StateReducer | None = None,
        render_state: StateRenderer | None = None,
        heading: str = DEFAULT_HEADING,
        reducer_label: str = "",
    ) -> None:
        self.base = resolve_agent_memory(base)
        if reducer is not None and not callable(reducer):
            raise ValueError("StructuredStateMemory reducer must be callable")
        if render_state is not None and not callable(render_state):
            raise ValueError("StructuredStateMemory render_state must be callable")
        self.reducer: StateReducer = reducer or default_tool_state_reducer
        self.render_state: StateRenderer = render_state or default_state_renderer
        self.heading = heading
        self.reducer_label = reducer_label or getattr(
            self.reducer, "__qualname__", self.reducer.__class__.__name__
        )

    def render(
        self,
        request: AgentRunRequest,
        history: Sequence[AgentToolStep],
        context: AgentMemoryRenderContext,
    ) -> list[ChatMessage]:
        messages = self.base.render(request, history, context)
        messages.append(ChatMessage(role="user", content=self._state_block(request, history)))
        return messages

    def projection_stats(
        self, request: AgentRunRequest, history: Sequence[AgentToolStep]
    ) -> dict[str, Any]:
        """Counts/labels only (AC-S5) — pure recompute, no state carried on the object."""

        return {
            "state_chars": len(self._state_block(request, history)),
            "reducer_label": self.reducer_label,
        }

    def _state_block(self, request: AgentRunRequest, history: Sequence[AgentToolStep]) -> str:
        state = self.reducer(request, history)
        rendered = _assert_state_output_safe(
            state, self.render_state(state), who="StructuredStateMemory"
        )
        return f"{self.heading}\n{rendered}"


class WindowedMemory:
    """Keep the last ``max_turns`` tool turns verbatim; older turns drop WITH a notice.

    The dropped prefix is summarized into one compact, byte-free notice message (tool
    tallies via the default reducer) — findings are never silently dropped (invariant 2).
    """

    def __init__(self, *, max_turns: int) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        self.max_turns = max_turns

    def render(
        self,
        request: AgentRunRequest,
        history: Sequence[AgentToolStep],
        context: AgentMemoryRenderContext,
    ) -> list[ChatMessage]:
        if len(history) <= self.max_turns:
            return render_full_replay_messages(request, history, context)
        dropped = history[: -self.max_turns]
        recent = history[-self.max_turns :]
        messages: list[ChatMessage] = []
        if context.system_prompt:
            messages.append(ChatMessage(role="system", content=context.system_prompt))
        messages.append(ChatMessage(role="user", content=request.prompt))
        messages.append(
            ChatMessage(role="user", content=_dropped_turns_notice("window_dropped", request, dropped))
        )
        tail_context = AgentMemoryRenderContext(
            tool_content=context.tool_content,
            images_from_output=context.images_from_output,
            system_prompt=None,
        )
        body = render_full_replay_messages(request, recent, tail_context)
        # body starts with the request prompt again — drop that duplicate, keep the turns.
        messages.extend(body[1:])
        return messages


class CompactingMemory:
    """Wrap any memory; above a token threshold replace OLD turns with a summary (GoPro R2).

    Deterministic and stateless: the decision is recomputed from the full history each
    render. Under the threshold the inner rendering passes through byte-identical. Over
    it, the last ``keep_last_turns`` turns render verbatim through the inner memory and
    the older prefix is compacted by a pluggable ``compactor`` (rule-based default; the
    output is validated byte-free, loudly).
    """

    def __init__(
        self,
        *,
        inner: Any = None,
        compactor: Compactor | None = None,
        token_threshold: int,
        keep_last_turns: int = 4,
    ) -> None:
        if token_threshold < 1:
            raise ValueError("token_threshold must be >= 1")
        if keep_last_turns < 1:
            raise ValueError("keep_last_turns must be >= 1")
        if compactor is not None and not callable(compactor):
            raise ValueError("CompactingMemory compactor must be callable")
        self.inner = resolve_agent_memory(inner)
        self.compactor: Compactor = compactor or default_rule_based_compactor
        self.token_threshold = token_threshold
        self.keep_last_turns = keep_last_turns

    def render(
        self,
        request: AgentRunRequest,
        history: Sequence[AgentToolStep],
        context: AgentMemoryRenderContext,
    ) -> list[ChatMessage]:
        full = self.inner.render(request, history, context)
        if len(history) <= self.keep_last_turns or self._estimate_tokens(full) <= self.token_threshold:
            return full
        dropped = history[: -self.keep_last_turns]
        recent = history[-self.keep_last_turns :]
        summary = _assert_state_output_safe(
            None, self.compactor(request, dropped), who="CompactingMemory"
        )
        messages: list[ChatMessage] = []
        if context.system_prompt:
            messages.append(ChatMessage(role="system", content=context.system_prompt))
        messages.append(ChatMessage(role="user", content=request.prompt))
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    f"[memory:compacted] {len(dropped)} earlier turn(s) summarized "
                    f"(findings preserved below):\n{summary}"
                ),
            )
        )
        tail_context = AgentMemoryRenderContext(
            tool_content=context.tool_content,
            images_from_output=context.images_from_output,
            system_prompt=None,
        )
        body = self.inner.render(request, recent, tail_context)
        messages.extend(body[1:])
        return messages

    @staticmethod
    def _estimate_tokens(messages: Sequence[ChatMessage]) -> int:
        from ai_workflow_engine.usage import estimate_text_tokens

        return estimate_text_tokens(list(messages))


def default_rule_based_compactor(
    request: AgentRunRequest, dropped: Sequence[AgentToolStep]
) -> str:
    """Findings-preserving rule-based summary of dropped turns (no LLM call, ever)."""

    return default_state_renderer(default_tool_state_reducer(request, dropped))


def _dropped_turns_notice(
    marker: str, request: AgentRunRequest, dropped: Sequence[AgentToolStep]
) -> str:
    summary = default_rule_based_compactor(request, dropped)
    return (
        f"[memory:{marker}] {len(dropped)} earlier turn(s) not replayed verbatim; "
        f"their tool activity:\n{summary}"
    )


def _append_eviction_notice(
    content: str,
    evidence_refs: Sequence[EvidenceRef],
    has_raw_images: bool,
) -> str:
    if has_raw_images and not evidence_refs:
        raise ValueError("ImageEvictingMemory cannot evict image output without an EvidenceRef")
    refs = [
        {
            "ref_id": ref.ref_id,
            "role": ref.role,
            "uri": ref.uri,
            "media_type": ref.media_type,
            "fingerprint": ref.metadata.get("fingerprint"),
            "summary": ref.summary,
        }
        for ref in evidence_refs
    ]
    marker = json.dumps({"memory": "image_evicted", "evidence_refs": refs}, sort_keys=True)
    return f"{content}\n[memory:image_evicted] {marker}"


def _image_evidence_refs(value: Any) -> list[EvidenceRef]:
    if isinstance(value, EvidenceRef):
        return _image_ref_if_image(value)
    if isinstance(value, Mapping):
        return _image_evidence_refs_from_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _image_evidence_refs_from_items(value)
    return []


def _image_ref_if_image(ref: EvidenceRef) -> list[EvidenceRef]:
    if ref.media_type and ref.media_type.startswith("image/"):
        return [ref]
    return []


def _image_evidence_refs_from_mapping(value: Mapping[Any, Any]) -> list[EvidenceRef]:
    try:
        ref = EvidenceRef.model_validate(value)
    except ValidationError:
        ref = None
    if ref is not None:
        return _image_ref_if_image(ref)
    return _image_evidence_refs_from_items(value.values())


def _image_evidence_refs_from_items(values: Iterable[Any]) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for item in values:
        refs.extend(_image_evidence_refs(item))
    return refs


def _contains_image_input(value: Any) -> bool:
    if isinstance(value, ImageInput):
        return True
    if isinstance(value, Mapping):
        try:
            ImageInput.model_validate(value)
            return True
        except ValidationError:
            return any(_contains_image_input(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_image_input(item) for item in value)
    return False
