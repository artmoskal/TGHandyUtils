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
        return ImageEvictingMemory(
            keep_last_images=int(options.get("keep_last_images", 1)),
        )
    raise ValueError(f"Unknown agent memory mode: {mode}")


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
