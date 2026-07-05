import pytest

from ai_workflow_engine import (
    EvidenceRef,
    FullReplayMemory,
    ImageEvictingMemory,
    InMemoryMemoryStore,
    MemoryNamespace,
    MemoryRecord,
    MemoryStore,
    resolve_agent_memory,
)

pytestmark = pytest.mark.unit


NS_A = MemoryNamespace("mageqa", "tenant-1", "https://shop.example", "finding_history")
NS_B = MemoryNamespace("mageqa", "tenant-2", "https://shop.example", "finding_history")


def test_memory_namespace_is_named_and_accepts_mageqa_shape():
    record = MemoryRecord(
        namespace=("mageqa", "tenant-1", "https://shop.example", "learned_flow"),
        key="checkout",
    )

    assert isinstance(record.namespace, MemoryNamespace)
    assert record.namespace.product == "mageqa"
    assert record.namespace.tenant == "tenant-1"
    assert record.namespace.subject == "https://shop.example"
    assert record.namespace.kind == "learned_flow"

    with pytest.raises(ValueError):
        MemoryRecord(namespace=("mageqa", "tenant-1", "https://shop.example"), key="checkout")


def test_in_memory_memory_store_is_namespaced_and_deterministic():
    store = InMemoryMemoryStore()
    failed = MemoryRecord(
        namespace=NS_A,
        key="test-b",
        value={"test": "checkout", "status": "failed", "reason": "timeout"},
        metadata={"kind": "test_result", "status": "failed"},
        source="run-1",
        evidence_refs=[
            EvidenceRef(role="log", uri="evidence://run-1/checkout.log", media_type="text/plain")
        ],
    )
    passed = MemoryRecord(
        namespace=NS_A,
        key="test-a",
        value={"test": "login", "status": "passed"},
        metadata={"kind": "test_result", "status": "passed"},
        source="run-1",
    )

    store.put(failed)
    store.put(passed)

    assert isinstance(store, MemoryStore)
    assert [record.key for record in store.search(NS_A)] == ["test-a", "test-b"]
    assert [record.key for record in store.search(tuple(NS_A))] == ["test-a", "test-b"]
    assert store.get(NS_B, "test-a") is None
    assert [record.key for record in store.search(NS_A, metadata_filter={"status": "failed"})] == ["test-b"]
    assert [record.key for record in store.search(NS_A, query="timeout")] == ["test-b"]
    assert store.delete(NS_A, "test-a") is True
    assert store.delete(NS_A, "test-a") is False
    assert [record.key for record in store.search(NS_A)] == ["test-b"]


def test_in_memory_memory_store_idempotency_key_reuse_is_loud():
    store = InMemoryMemoryStore()
    record = MemoryRecord(namespace=NS_A, key="attempt-1", value={"status": "failed"})

    assert store.put(record, idempotency_key="write-1") == record
    assert store.put(record, idempotency_key="write-1") == record

    with pytest.raises(ValueError, match="idempotency key reused"):
        store.put(
            MemoryRecord(namespace=NS_A, key="attempt-1", value={"status": "passed"}),
            idempotency_key="write-1",
        )


def test_agent_memory_config_resolution_is_explicit_and_loud():
    assert isinstance(resolve_agent_memory(None), FullReplayMemory)
    assert isinstance(resolve_agent_memory("full_replay"), FullReplayMemory)
    assert isinstance(resolve_agent_memory({"mode": "image_evicting", "keep_last_images": 2}), ImageEvictingMemory)

    for alias_or_unknown in ("full", "default", "image_eviction", "semantic_search"):
        with pytest.raises(ValueError, match="Unknown agent memory mode"):
            resolve_agent_memory(alias_or_unknown)

    with pytest.raises(ValueError, match="requires mode"):
        resolve_agent_memory({"keep_last_images": 1})


# --- GoPro P1 memory wave (R1 StructuredStateMemory, R2 windowed/compacting) -------------

from ai_workflow_engine import (  # noqa: E402
    CompactingMemory,
    StructuredStateMemory,
    WindowedMemory,
    default_tool_state_reducer,
)
from ai_workflow_engine.memory import AgentMemoryRenderContext, render_full_replay_messages  # noqa: E402
from ai_workflow_engine.models import AgentRunRequest, AgentToolCall, AgentToolStep  # noqa: E402
from ai_workflow_engine.vision import ImageInput  # noqa: E402


def _render_ctx(system: str | None = "sys") -> AgentMemoryRenderContext:
    def _images(output):
        refs = output if isinstance(output, list) else [output]
        return [
            ImageInput(source="base64", data="aGk=", media_type="image/png")
            for ref in refs
            if isinstance(ref, EvidenceRef) and (ref.media_type or "").startswith("image/")
        ]

    return AgentMemoryRenderContext(
        tool_content=lambda step: str(step.output),
        images_from_output=_images,
        system_prompt=system,
    )


def _image_ref(index: int) -> EvidenceRef:
    return EvidenceRef(role="screenshot", uri=f"/tmp/shot-{index}.png", media_type="image/png")


def _step(tool: str, payload: dict, *, output="ok", status="accepted") -> AgentToolStep:
    return AgentToolStep(
        call=AgentToolCall(tool_name=tool, payload=payload, rationale="r"),
        status=status,
        output=output,
    )


def _dump(messages) -> list[dict]:
    return [message.model_dump(mode="json") for message in messages]


REQ = AgentRunRequest(prompt="inventory the room", allowed_tools=[])


def test_structured_state_purity_same_inputs_same_messages():  # AC-S1
    memory = StructuredStateMemory()
    history = [_step("zoom", {"section": 1}), _step("zoom", {"section": 2}, status="failed")]

    first = memory.render(REQ, history, _render_ctx())
    second = memory.render(REQ, history, _render_ctx())

    assert _dump(first) == _dump(second)
    assert first[-1].role == "user"
    assert first[-1].content.startswith(StructuredStateMemory.DEFAULT_HEADING)


def test_default_reducer_output_is_exact_and_product_neutral():  # AC-S4
    history = [
        _step("zoom", {"section": 3}),
        _step("zoom", {"section": 7}, status="failed"),
        _step("navigate", {"url": "x", "depth": 2}),
    ]

    state = default_tool_state_reducer(REQ, history)

    assert state == {
        "turns": 3,
        "tools": {
            "navigate": {"calls": 1, "args": ['{"depth": 2, "url": "x"}'], "ok": 1, "error": 0},
            "zoom": {"calls": 2, "args": [3, 7], "ok": 1, "error": 1},
        },
    }


def test_structured_state_composes_with_image_evicting_base():  # AC-S3
    memory = StructuredStateMemory(base={"mode": "image_evicting", "keep_last_images": 1})
    history = [
        _step("zoom", {"section": 1}, output=_image_ref(1)),
        _step("zoom", {"section": 2}, output=_image_ref(2)),
    ]

    messages = memory.render(REQ, history, _render_ctx())

    image_count = sum(len(r.images) for m in messages for r in m.tool_results)
    assert image_count == 1  # eviction still bounds images
    assert messages[-1].content.startswith(StructuredStateMemory.DEFAULT_HEADING)
    assert "zoom: calls=2" in messages[-1].content  # state block present
    # the evicted turn keeps its text + an auditable eviction marker
    assert any("[memory:image_evicted]" in (m.content or "") for m in messages if m.role == "tool")


def test_memory_config_resolution_new_modes_and_nested_base():  # AC-S6 config half
    structured = resolve_agent_memory(
        {"mode": "structured_state", "base": {"mode": "image_evicting", "keep_last_images": 2}}
    )
    assert isinstance(structured, StructuredStateMemory)
    assert isinstance(structured.base, ImageEvictingMemory)
    assert structured.base.keep_last_images == 2

    assert isinstance(resolve_agent_memory("structured_state"), StructuredStateMemory)

    with pytest.raises(ValueError, match="Unknown agent memory mode"):
        resolve_agent_memory({"mode": "structured_state", "base": {"mode": "nope"}})
    with pytest.raises(ValueError, match="does not accept options"):
        resolve_agent_memory({"mode": "image_evicting", "typo_knob": 1})
    with pytest.raises(ValueError, match="requires max_turns"):
        resolve_agent_memory("windowed")
    with pytest.raises(ValueError, match="requires token_threshold"):
        resolve_agent_memory("compacting")
    with pytest.raises(ValueError, match="must be callable"):
        StructuredStateMemory(reducer="not-callable")
    with pytest.raises(ValueError, match="must be callable"):
        CompactingMemory(compactor="nope", token_threshold=10)


def test_structured_state_rejects_bytes_and_image_inputs_loudly():  # AC-S6 render half
    history = [_step("zoom", {"section": 1})]

    byte_memory = StructuredStateMemory(reducer=lambda _r, _h: {"blob": b"raw"})
    with pytest.raises(ValueError, match="byte-free"):
        byte_memory.render(REQ, history, _render_ctx())

    image_memory = StructuredStateMemory(
        reducer=lambda _r, _h: {"img": ImageInput(source="base64", data="aGk=", media_type="image/png")}
    )
    with pytest.raises(ValueError, match="byte-free"):
        image_memory.render(REQ, history, _render_ctx())

    bad_renderer = StructuredStateMemory(render_state=lambda _s: {"not": "a string"})
    with pytest.raises(ValueError, match="must return str"):
        bad_renderer.render(REQ, history, _render_ctx())


def test_structured_state_reducer_label_defaults_and_overrides():
    assert StructuredStateMemory().reducer_label == "default_tool_state_reducer"
    labeled = StructuredStateMemory(reducer=lambda _r, _h: {}, reducer_label="gopro_findings")
    assert labeled.reducer_label == "gopro_findings"


def test_windowed_memory_keeps_recent_turns_and_notices_dropped():  # R2
    memory = WindowedMemory(max_turns=2)
    history = [_step("zoom", {"section": index}) for index in range(1, 6)]

    messages = memory.render(REQ, history, _render_ctx())

    notice = messages[2]
    assert notice.role == "user"
    assert "[memory:window_dropped] 3 earlier turn(s)" in notice.content
    assert "zoom: calls=3" in notice.content  # findings-preserving tally, never silent
    tool_calls = [c.arguments["section"] for m in messages for c in m.tool_calls]
    assert tool_calls == [4, 5]  # last N verbatim

    short = WindowedMemory(max_turns=9).render(REQ, history, _render_ctx())
    assert _dump(short) == _dump(render_full_replay_messages(REQ, history, _render_ctx()))

    with pytest.raises(ValueError, match="max_turns"):
        WindowedMemory(max_turns=0)


def test_compacting_memory_passthrough_under_threshold_and_compacts_over():  # R2
    history = [_step("zoom", {"section": index}, output=f"long finding {index} " * 20) for index in range(1, 8)]

    passthrough = CompactingMemory(token_threshold=1_000_000, keep_last_turns=2)
    assert _dump(passthrough.render(REQ, history, _render_ctx())) == _dump(
        render_full_replay_messages(REQ, history, _render_ctx())
    )

    compacting = CompactingMemory(token_threshold=1, keep_last_turns=2)
    messages = compacting.render(REQ, history, _render_ctx())
    summary = messages[2]
    assert "[memory:compacted] 5 earlier turn(s)" in summary.content
    assert "zoom: calls=5" in summary.content
    tool_calls = [c.arguments["section"] for m in messages for c in m.tool_calls]
    assert tool_calls == [6, 7]

    nested = CompactingMemory(
        inner={"mode": "image_evicting", "keep_last_images": 1}, token_threshold=1, keep_last_turns=1
    )
    assert isinstance(nested.inner, ImageEvictingMemory)


def test_dropped_turn_finding_text_survives_windowing_and_compaction_bounded():
    # Codex re-validation follow-up: activity tallies alone are NOT findings-preservation.
    # The default compactor must carry bounded excerpts of dropped outputs.
    history = [
        _step("inspect", {"tile": 1}, output="found: blue mug on desk"),
        _step("inspect", {"tile": 2}, output="found: lamp near window " + "x" * 500),
        _step("inspect", {"tile": 3}, output="found: keyboard"),
        _step("inspect", {"tile": 4}, output="nothing new"),
    ]

    windowed = WindowedMemory(max_turns=1).render(REQ, history, _render_ctx())
    notice = windowed[2].content
    assert "found: blue mug on desk" in notice
    assert "found: lamp near window" in notice
    assert "…" in notice  # the 500-char output is truncated, not carried whole
    assert "x" * 200 not in notice  # bounded excerpt, never the full blob

    compacted = CompactingMemory(token_threshold=1, keep_last_turns=1).render(
        REQ, history, _render_ctx()
    )
    summary = compacted[2].content
    assert "found: blue mug on desk" in summary
    assert "found: keyboard" in summary
