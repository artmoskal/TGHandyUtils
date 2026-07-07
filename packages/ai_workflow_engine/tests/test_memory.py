import pytest
from pydantic import BaseModel

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


def test_structured_state_rejects_model_nested_media_and_rendered_data_uris_loudly():
    class FindingWithImage(BaseModel):
        image: ImageInput

    class FindingWithBytes(BaseModel):
        blob: bytes

    history = [_step("zoom", {"section": 1})]

    image = ImageInput(source="base64", data="aGk=", media_type="image/png")
    image_memory = StructuredStateMemory(reducer=lambda _r, _h: FindingWithImage(image=image))
    with pytest.raises(ValueError, match="byte-free"):
        image_memory.render(REQ, history, _render_ctx())

    bytes_memory = StructuredStateMemory(reducer=lambda _r, _h: FindingWithBytes(blob=b"raw"))
    with pytest.raises(ValueError, match="byte-free"):
        bytes_memory.render(REQ, history, _render_ctx())

    set_memory = StructuredStateMemory(reducer=lambda _r, _h: {"blobs": {b"raw"}})
    with pytest.raises(ValueError, match="byte-free"):
        set_memory.render(REQ, history, _render_ctx())

    data_uri_memory = StructuredStateMemory(
        reducer=lambda _r, _h: {"status": "bad"},
        render_state=lambda _s: "data:image/png;base64,aGk=",
    )
    with pytest.raises(ValueError, match="data URI"):
        data_uri_memory.render(REQ, history, _render_ctx())

    safe_memory = StructuredStateMemory(
        reducer=lambda _r, _h: {
            "sha256": "a" * 64,
            "evidence_ref": "frame-123",
            "uuid": "123e4567-e89b-12d3-a456-426614174000",
        },
        render_state=lambda state: "\n".join(f"{key}: {value}" for key, value in state.items()),
    )
    assert "sha256" in safe_memory.render(REQ, history, _render_ctx())[-1].content


def test_structured_state_redacts_transport_data_uri_tool_args_without_losing_call_fact():
    history = [
        _step(
            "inspect_image",
            {"image_url": "data:image/png;base64,abcdef", "label": "front"},
            output="ok",
        )
    ]

    rendered = StructuredStateMemory().render(REQ, history, _render_ctx())[-1].content

    assert "inspect_image: calls=1" in rendered
    assert "image_url" in rendered
    assert "<data-uri redacted media_type=image/png chars=6>" in rendered
    assert "abcdef" not in rendered
    assert "data:image/png;base64" not in rendered


def test_structured_state_allows_prose_that_only_mentions_data_uri_syntax():
    memory = StructuredStateMemory(
        reducer=lambda _r, _h: {"note": "The page text mentions data:image/png;base64,AAAA."},
        render_state=lambda state: state["note"],
    )

    rendered = memory.render(REQ, [_step("read", {})], _render_ctx())[-1].content

    assert "mentions data:image/png;base64" in rendered


def test_structured_state_rejects_transport_hidden_in_arbitrary_objects():  # AC-S6 fall-through hole
    """A plain (non-pydantic/dataclass) object OR a __slots__ object holding an ImageInput/bytes in
    an attribute must NOT slip past the byte-free guard at the arbitrary-object fall-through."""

    class PlainHolder:  # no __dict__ recursion type, no pydantic/dataclass
        def __init__(self, payload):
            self.hidden = payload

    class SlotHolder:
        __slots__ = ("hidden", "unset")

        def __init__(self, payload):
            self.hidden = payload  # 'unset' deliberately left unassigned

    history = [_step("zoom", {"section": 1})]
    image = ImageInput(source="base64", data="aGk=", media_type="image/png")

    for holder_type in (PlainHolder, SlotHolder):
        for payload in (image, b"rawbytes"):
            memory = StructuredStateMemory(
                reducer=lambda _r, _h, p=payload, t=holder_type: {"f": t(p)},
                render_state=lambda _s: "safe text",
            )
            with pytest.raises(ValueError, match="byte-free"):
                memory.render(REQ, history, _render_ctx())


def test_structured_state_rejects_opaque_default_repr_objects_loudly():  # codex 21:13 gate
    """Unknown custom objects are not safe prompt state, even when they define ``__repr__``."""

    class EmptySlots:
        __slots__ = ()  # no readable attributes, inherits object.__repr__

    history = [_step("zoom", {"section": 1})]

    class StableValue:
        __slots__ = ()

        def __repr__(self):
            return "StableValue()"

    for opaque in (object(), EmptySlots(), StableValue()):
        memory = StructuredStateMemory(
            reducer=lambda _r, _h, o=opaque: {"opaque": o},
            render_state=lambda _s: "safe text",
        )
        with pytest.raises(ValueError, match="unsupported"):
            memory.render(REQ, history, _render_ctx())


def test_structured_state_passes_attribute_less_value_objects_no_false_positive():  # anti-regression
    """Byte-free value objects with no readable attributes that could hide transport
    (datetime/UUID/Decimal) — and byte-free EvidenceRef — must PASS, not be false-positives."""

    import datetime as _dt
    import uuid as _uuid
    from decimal import Decimal

    history = [_step("zoom", {"section": 1})]
    value_state = {
        "when": _dt.datetime(2026, 7, 6, 21, 0, 0),
        "id": _uuid.UUID("123e4567-e89b-12d3-a456-426614174000"),
        "amount": Decimal("1.50"),
        "evidence": EvidenceRef(role="frame", uri="/tmp/f.png", media_type="image/png"),
    }
    memory = StructuredStateMemory(
        reducer=lambda _r, _h: value_state,
        render_state=lambda state: " ".join(f"{k}={v}" for k, v in state.items()),
    )

    rendered = memory.render(REQ, history, _render_ctx())[-1].content
    assert "amount=1.50" in rendered  # rendered, not rejected


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


def test_compacting_memory_rejects_structured_state_inner_but_allows_safe_reverse_order():
    with pytest.raises(ValueError, match="StructuredStateMemory\\(base=CompactingMemory"):
        CompactingMemory(inner=StructuredStateMemory(), token_threshold=1, keep_last_turns=1)

    with pytest.raises(ValueError, match="StructuredStateMemory\\(base=CompactingMemory"):
        resolve_agent_memory(
            {
                "mode": "compacting",
                "inner": {"mode": "structured_state"},
                "token_threshold": 1,
                "keep_last_turns": 1,
            }
        )

    history = [_step("zoom", {"section": index}, output="long " * 80) for index in range(1, 4)]
    safe = StructuredStateMemory(
        base={"mode": "compacting", "token_threshold": 1, "keep_last_turns": 1}
    )
    messages = safe.render(REQ, history, _render_ctx(system=None))
    state_block = messages[-1].content

    assert "[memory:compacted]" in "\n".join(message.content or "" for message in messages)
    assert "args=[1, 2, 3]" in state_block


def test_compacting_memory_midrange_threshold_and_uncompactable_over_budget_notice():
    history = [_step("zoom", {"section": index}, output="medium text " * 20) for index in range(1, 6)]
    full = render_full_replay_messages(REQ, history, _render_ctx())
    threshold = CompactingMemory._estimate_tokens(full) - 1

    compacted = CompactingMemory(token_threshold=threshold, keep_last_turns=2).render(
        REQ, history, _render_ctx()
    )
    assert "[memory:compacted]" in compacted[2].content

    passthrough = CompactingMemory(token_threshold=threshold + 2, keep_last_turns=2).render(
        REQ, history, _render_ctx()
    )
    assert _dump(passthrough) == _dump(full)

    huge_recent = [_step("zoom", {"section": index}, output="large " * 400) for index in range(1, 3)]
    over_budget = CompactingMemory(token_threshold=1, keep_last_turns=4).render(
        REQ, huge_recent, _render_ctx(system=None)
    )
    assert "[memory:over_budget]" in over_budget[1].content
    assert "[memory:compacted]" not in "\n".join(message.content or "" for message in over_budget)


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


def test_dropped_turn_excerpts_use_canonical_tool_rendering_and_2arg_compactors_still_work():
    # Codex round-2 architecture smell: compaction must read like normal replay — excerpts
    # go through context.tool_content, not a parallel str(output) rendering.
    history = [
        _step("inspect", {"tile": 1}, output="found: blue mug"),
        _step("inspect", {"tile": 2}, output="found: lamp"),
        _step("inspect", {"tile": 3}, output="found: keyboard"),
    ]
    marked_ctx = AgentMemoryRenderContext(
        tool_content=lambda step: f"RENDERED::{step.output}",
        images_from_output=lambda _output: [],
        system_prompt=None,
    )

    windowed = WindowedMemory(max_turns=1).render(REQ, history, marked_ctx)
    notice = windowed[1].content
    assert "RENDERED::found: blue mug" in notice

    compacted = CompactingMemory(token_threshold=1, keep_last_turns=1).render(
        REQ, history, marked_ctx
    )
    assert "summary below" in compacted[1].content
    assert "findings preserved below" not in compacted[1].content
    assert "RENDERED::found: lamp" in compacted[1].content

    # A plain (request, dropped) compactor — the spec'd pluggable shape — keeps working.
    two_arg = CompactingMemory(
        compactor=lambda _request, dropped: f"TWO-ARG summary of {len(dropped)} turns",
        token_threshold=1,
        keep_last_turns=1,
    ).render(REQ, history, marked_ctx)
    assert "TWO-ARG summary of 2 turns" in two_arg[1].content
