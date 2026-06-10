"""G1 — vision/multimodal structured LLM node (AC-G1)."""

import base64
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ai_workflow_engine import (
    WEAK_MODEL_CLEANER,
    ImageInput,
    StructuredVisionLLMNode,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    assert_checkpoint_payload_safe,
)
from ai_workflow_engine.models import EvidenceRef, RuntimeLimits, WorkflowProfile

pytestmark = pytest.mark.unit

B64_PIXEL = base64.b64encode(b"\x89PNG-fake-pixel-payload-for-tests").decode("ascii")


class FrameReading(BaseModel):
    objects: list[str]
    confidence: float


class CapturingVisionLLM:
    """OpenAI-style fake: records the exact messages, returns clean JSON."""

    def __init__(self, response='{"objects": ["mug"], "confidence": 0.9}'):
        self.response = response
        self.calls = 0
        self.last_messages = None

    def invoke(self, messages):
        self.calls += 1
        self.last_messages = messages
        return SimpleNamespace(content=self.response)


class WeakOllamaStyleLLM(CapturingVisionLLM):
    """Ollama/qwen-style fake: same interface, but wraps JSON in think-tags + fences."""

    def __init__(self):
        super().__init__(
            '<think>I see a mug.</think>\n```json\n{"objects": ["mug"], "confidence": 0.7}\n```'
        )


def _vision_node(llm) -> StructuredVisionLLMNode:
    return StructuredVisionLLMNode(
        name="inspect_frame",
        config=object(),
        output_model=FrameReading,
        prompt_template="What objects are in frame {frame_id}?",
        input_variables=["frame_id"],
        llm=llm,
        pre_parse=WEAK_MODEL_CLEANER,
    )


def _frame_images() -> list[ImageInput]:
    return [
        ImageInput(source="base64", data=B64_PIXEL, media_type="image/png", role="candidate_frame"),
        ImageInput(source="url", data="https://cam.local/frame-2.jpg", role="context"),
    ]


def _engine_for(llm, *, limits: RuntimeLimits | None = None):
    node = _vision_node(llm)

    async def inspect(ctx, payload):
        return await node.run({"frame_id": payload["frame_id"]}, images=payload["images"])

    builder = WorkflowEngineBuilder()
    builder.register_capability("inspect_frame", inspect, kind="llm", metered=True)
    profile = WorkflowProfile(workflow_type="frame_inspection", limits=limits or RuntimeLimits())
    builder.register_workflow(
        WorkflowBuilder("frame_inspection").step("inspect_frame").build(), profile=profile
    )
    return builder.build()


# ---------------------------------------------------------------- AC-G1 core

async def test_same_vision_workflow_runs_on_openai_and_ollama_style_backends():
    payload = {"frame_id": "f-1", "images": _frame_images()}

    openai_engine = _engine_for(CapturingVisionLLM())
    ollama_engine = _engine_for(WeakOllamaStyleLLM())

    openai_result = await openai_engine.run("frame_inspection", payload)
    ollama_result = await ollama_engine.run("frame_inspection", payload)

    # Zero workflow-definition changes between backends; both produce structured output.
    assert openai_result.status == "completed"
    assert openai_result.output.objects == ["mug"]
    assert ollama_result.status == "completed"
    assert ollama_result.output.objects == ["mug"]
    assert ollama_result.output.confidence == 0.7  # parsed from think-tagged, fenced output


async def test_images_are_attached_as_content_parts_and_resent_on_repair():
    llm = CapturingVisionLLM()
    llm_responses = ["not json at all", '{"objects": ["cable"], "confidence": 0.8}']

    class RepairingLLM(CapturingVisionLLM):
        def __init__(self):
            super().__init__()
            self.all_messages = []

        def invoke(self, messages):
            self.calls += 1
            self.all_messages.append(messages)
            return SimpleNamespace(content=llm_responses[self.calls - 1])

    repairing = RepairingLLM()
    node = _vision_node(repairing)

    result = await node.run({"frame_id": "f-2"}, images=_frame_images())

    assert result.objects == ["cable"]
    assert repairing.calls == 2
    for messages in repairing.all_messages:  # first AND repair attempt carry the images
        parts = messages[-1].content
        image_parts = [p for p in parts if isinstance(p, dict) and p.get("type") == "image_url"]
        assert len(image_parts) == 2
        assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert image_parts[1]["image_url"]["url"] == "https://cam.local/frame-2.jpg"


# ---------------------------------------------------------------- privacy invariant

async def test_no_image_bytes_in_trace_usage_or_checkpoint():
    engine = _engine_for(CapturingVisionLLM())
    result = await engine.run("frame_inspection", {"frame_id": "f-3", "images": _frame_images()})

    assert result.status == "completed"
    trace_dump = json.dumps([event.model_dump() for event in result.trace], default=str)
    usage_dump = json.dumps([event.model_dump() for event in result.usage.events], default=str)
    assert B64_PIXEL not in trace_dump
    assert B64_PIXEL not in usage_dump
    # Fingerprints (not bytes) are recorded on the metered call.
    vision_events = [e for e in result.usage.events if e.metadata.get("input_images")]
    assert vision_events and vision_events[0].metadata["input_images"] == 2
    assert all("sha12" in fp for fp in vision_events[0].metadata["image_fingerprints"])


def test_imageinput_repr_never_leaks_payload_and_checkpoint_guard_rejects_it():
    image = ImageInput(source="base64", data=B64_PIXEL, media_type="image/png", role="frame")

    assert B64_PIXEL not in repr(image)
    assert B64_PIXEL not in str(image)
    assert image.fingerprint()["length"] == len(B64_PIXEL)
    with pytest.raises(ValueError, match="EvidenceRefs"):
        assert_checkpoint_payload_safe({"state": [image]})


def test_from_evidence_bridge_loads_bytes_only_at_call_boundary():
    ref = EvidenceRef(role="contents", uri="frame://cam-1/latest", media_type="image/png")
    loaded = {"count": 0}

    def loader(evidence: EvidenceRef) -> bytes:
        loaded["count"] += 1
        return b"raw-frame-bytes"

    image = ImageInput.from_evidence(ref, loader)

    assert loaded["count"] == 1
    assert image.source == "base64"
    assert image.role == "contents"
    assert image.metadata["evidence_ref_id"] == ref.ref_id
    assert base64.b64decode(image.data) == b"raw-frame-bytes"


def test_from_path_reads_file_at_call_time(tmp_path):
    path = tmp_path / "frame.png"
    path.write_bytes(b"png-bytes-here")
    image = ImageInput.from_path(str(path), role="candidate_frame")

    part = image.as_content_part()
    assert part["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) == b"png-bytes-here"


# ---------------------------------------------------------------- budget + timeout

async def test_budget_exhaustion_denies_metered_vision_capability():
    called = {"n": 0}

    async def paid_vision(ctx, payload):
        called["n"] += 1
        return FrameReading(objects=[], confidence=1.0)

    builder = WorkflowEngineBuilder()
    builder.register_capability("inspect_frame", paid_vision, kind="llm", metered=True)
    builder.register_workflow(
        WorkflowBuilder("frame_inspection").step("inspect_frame").build(),
        profile=WorkflowProfile(
            workflow_type="frame_inspection", limits=RuntimeLimits(max_estimated_usd=0.0)
        ),
    )
    result = await builder.build().run("frame_inspection", {"frame_id": "f", "images": []})

    assert result.status == "failed"
    assert called["n"] == 0  # denied before the handler ran
    assert "budget" in (result.error or "")


async def test_capability_timeout_applies_to_vision_call():
    import asyncio

    async def slow_vision(ctx, payload):
        await asyncio.sleep(0.2)
        return FrameReading(objects=["late"], confidence=0.1)

    builder = WorkflowEngineBuilder()
    builder.register_capability("inspect_frame", slow_vision, kind="llm", timeout_s=0.05)
    builder.register_workflow(WorkflowBuilder("frame_inspection").step("inspect_frame").build())
    result = await builder.build().run("frame_inspection", {"frame_id": "f", "images": []})

    assert result.status == "failed"
