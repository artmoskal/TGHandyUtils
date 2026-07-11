"""Q2: four consumer-shaped qualification scenarios (Anki / MageQA / GoPro / SlackAzz).

Each scenario builds REAL engines/workflows/capabilities and runs them through the
ordinary public API — the ONLY injected boundary is the LLM client (`provider_factory`).
No product repositories are imported; product delivery paths (Telegram, Slack API,
browsers, video) are explicitly out of scope — this suite qualifies universal engine
mechanics per docs/_discussion/2026-07-11-engine-cross-consumer-live-qualification-plan.md.

Provider contract: ``provider_factory(scenario_name, role)`` returns an LLM client for
one call site. Hermetic mode returns scripted fakes; live mode returns a fresh
``ConsoleLLMClient`` (claude -p) with the per-invocation CLI budget cap. A scenario
NEVER falls back to a fake by itself.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel

from tests.support.engine_qualification import (
    QualificationConfig,
    ScenarioOutcome,
    ScenarioSemanticError,
)

ProviderFactory = Callable[[str, str], Any]

# 1x1 red PNG — a real, valid image file for the staged-vision path (no PIL dependency).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842"
    "iQAAAABJRU5ErkJggg=="
)


def _observed_builder(bundle_dir: Path):
    from ai_workflow_engine import ObservationConfig, WorkflowEngineBuilder

    return WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(bundle_dir))
    )


def _profile_scope(config: QualificationConfig):
    """Live runs route the requested model alias onto claude argv (Q0.1) through the
    ordinary model-binding mechanism; hermetic runs stay unrouted."""

    from contextlib import nullcontext

    if not config.live:
        return nullcontext()
    from ai_workflow_engine import ModelProfile
    from ai_workflow_engine.model_binding import model_profile_scope

    return model_profile_scope(
        ModelProfile(name="qualification", provider="claude_p", model=config.model)
    )


def _worker_calls(result: Any) -> int:
    return sum(1 for e in result.usage.events if e.success)


def _outcome(name: str, result: Any, started: float, *, extra_results: tuple = ()) -> ScenarioOutcome:
    results = (result, *extra_results)
    notional = [r.usage.notional_usd for r in results if r.usage.notional_usd is not None]
    return ScenarioOutcome(
        name=name,
        status="passed",
        notional_usd=round(sum(notional), 6) if notional else None,
        worker_calls=sum(_worker_calls(r) for r in results),
        duration_s=round(time.monotonic() - started, 3),
        bundle_path=result.observation_bundle_path,
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioSemanticError(message)


# ------------------------------------------------------------------ scenario 1: Anki card


class CardDraft(BaseModel):
    front: str
    back: str
    tags: list[str] = []


async def run_anki_basic_card(
    config: QualificationConfig, provider_factory: ProviderFactory, workdir: Path
) -> ScenarioOutcome:
    """Simple/static end of the gradient: ONE real structured call -> deterministic
    validate -> branch -> render. Telegram/apkg/image generation are out of scope."""

    from types import SimpleNamespace

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_engine.engine.llm_node import StructuredLLMNode

    started = time.monotonic()
    node = StructuredLLMNode(
        name="author_card",
        config=SimpleNamespace(),
        output_model=CardDraft,
        prompt_template=(
            "Author ONE flashcard about {topic}. Respond with JSON only: "
            '{{"front": "...", "back": "...", "tags": ["..."]}}\n{format_instructions}'
        ),
        input_variables=["topic"],
        llm=provider_factory("anki_basic_card", "card_author"),
        max_repair_rounds=1,
    )
    builder = _observed_builder(workdir / "bundles" / "anki")

    async def author_card(_context, payload):
        return await node.run({"topic": payload["topic"]})

    def validate_card(_context, card: CardDraft):
        ok = bool(card.front.strip()) and bool(card.back.strip()) and card.front != card.back
        return {"card": card.model_dump(), "verdict": "valid" if ok else "invalid"}

    def render_note(_context, payload):
        return {"note": f"{payload['card']['front']} | {payload['card']['back']}", "format": "basic"}

    def reject_note(_context, payload):
        raise ScenarioSemanticError(f"model produced an invalid card: {payload['card']}")

    builder.register_capability("author_card", author_card, kind="llm", output_model=CardDraft)
    builder.register_capability("validate_card", validate_card, kind="deterministic")
    builder.register_capability("render_note", render_note, kind="deterministic")
    builder.register_capability("reject_note", reject_note, kind="deterministic")
    builder.register_guard("card_gate", lambda payload: payload["verdict"])
    builder.register_workflow(
        WorkflowBuilder("anki_basic_card")
        .step("author_card")
        .step("validate_card")
        .branch("card_gate", {"valid": "render_note", "invalid": "reject_note"})
        .step("render_note")
        .step("reject_note")
        .build()
    )
    engine = builder.build()

    with _profile_scope(config):
        result = await engine.run("anki_basic_card", {"topic": "the Krebs cycle"})

    _require(result.status == "completed", f"anki run did not complete: {result.error}")
    _require(" | " in (result.output or {}).get("note", ""), "rendered note is malformed")
    _require(_worker_calls(result) >= 1, "no real worker call was recorded")
    _require(
        sum(1 for e in result.usage.events if e.operation == "chat") <= 2,
        "anki scenario must stay within one call plus one repair",
    )
    _require(bool(result.observation_bundle_path), "observation bundle missing")
    return _outcome("anki_basic_card", result, started)


# ------------------------------------------------------- scenario 2: MageQA local audit


class AuditSummary(BaseModel):
    total_pages: int
    ok_pages: int
    inconclusive_pages: int
    verdict: str


_PAGES = {
    "page1.html": "<html><title>Alpha</title><body><h1>Alpha</h1></body></html>",
    "page2.html": "<html><title></title><body>",  # malformed: empty title, unclosed body
    "page3.html": "<html><title>Gamma</title><body><h1>Gamma</h1></body></html>",
}


async def run_mageqa_local_audit(
    config: QualificationConfig, provider_factory: ProviderFactory, workdir: Path
) -> ScenarioOutcome:
    """Dynamic/heavy end: engine A AUTHORS a flow (real LLM), the artifact survives
    ordinary JSON, engine B runs bounded fanout over LOCAL page probes with one
    deterministic inconclusive result, then a second real call summarizes findings."""

    from types import SimpleNamespace

    from ai_workflow_engine import (
        FlowArtifact,
        WorkflowBuilder,
        WorkflowEngineBuilder,
        build_flow_author_capability,
    )
    from ai_workflow_engine.engine.llm_node import StructuredLLMNode

    started = time.monotonic()
    inputs = workdir / "inputs" / "mageqa"
    inputs.mkdir(parents=True, exist_ok=True)
    for name, content in _PAGES.items():
        (inputs / name).write_text(content, encoding="utf-8")

    processing_builder = _observed_builder(workdir / "bundles" / "mageqa")

    def collect_pages(_context, _payload):
        return {"pages": sorted(str(p) for p in inputs.glob("*.html"))}

    def probe_page(_context, page_path: str):
        text = Path(page_path).read_text(encoding="utf-8")
        title_ok = "<title>" in text and not text.split("<title>", 1)[1].startswith("</title>")
        closed_ok = "</body>" in text
        status = "ok" if (title_ok and closed_ok) else "inconclusive"
        return {"page": Path(page_path).name, "status": status, "evidence_chars": len(text)}

    summarize_node = StructuredLLMNode(
        name="summarize_findings",
        config=SimpleNamespace(),
        output_model=AuditSummary,
        prompt_template=(
            "Probe results: {probes}. Respond with JSON only: "
            '{{"total_pages": N, "ok_pages": N, "inconclusive_pages": N, "verdict": "..."}}\n'
            "{format_instructions}"
        ),
        input_variables=["probes"],
        llm=provider_factory("mageqa_local_audit", "audit_summarizer"),
        max_repair_rounds=1,
    )

    async def summarize_findings(_context, probes):
        import json as _json

        summary = await summarize_node.run({"probes": _json.dumps(probes, sort_keys=True)})
        return summary.model_dump()

    processing_builder.register_capability("collect_pages", collect_pages, kind="deterministic")
    processing_builder.register_capability("probe_page", probe_page, kind="deterministic")
    processing_builder.register_capability(
        "summarize_findings", summarize_findings, kind="llm", output_model=AuditSummary
    )
    processing = processing_builder.build()

    author_spec, author_handler = build_flow_author_capability(
        provider_factory("mageqa_local_audit", "flow_author"),
        registry=processing.registry,
        model_profiles=processing.model_profiles,
        limits=processing.default_profile.limits if processing.default_profile else None,
        allowed_side_effects=["read_only"],
        max_repair_rounds=1,
    )
    author_builder = WorkflowEngineBuilder()
    author_builder.register_capability_spec(author_spec, author_handler)
    author_builder.register_workflow(WorkflowBuilder("authoring").step("author_flow").build())
    author_engine = author_builder.build()

    with _profile_scope(config):
        authored = await author_engine.run(
            "authoring",
            {
                "goal": (
                    "Audit local pages: collect_pages, then fan out probe_page over "
                    "collect_pages.pages (max_items 5), then summarize_findings over the "
                    "fanout output."
                ),
                "context": {"pages": len(_PAGES)},
            },
        )
    _require(authored.status == "completed", f"flow authoring failed: {authored.error}")

    # the artifact must survive ORDINARY JSON (R6 contract) before the peer engine runs it
    import json as _json

    artifact = FlowArtifact.model_validate(_json.loads(_json.dumps(authored.output)))
    with _profile_scope(config):
        executed = await processing.run_authored_flow(artifact, {})

    _require(executed.status == "completed", f"authored audit failed: {executed.error}")
    output = executed.output
    summary = output.model_dump() if hasattr(output, "model_dump") else (output or {})
    _require(summary.get("total_pages") == 3, f"summary lost pages: {summary}")
    _require(summary.get("inconclusive_pages") == 1, f"partial failure lost: {summary}")
    _require(
        any(e.decision == "flow:authored" for e in executed.trace),
        "authored provenance missing from the processing run",
    )
    fanout_events = [e for e in executed.trace if "fanout" in (e.node or "") or e.decision.startswith("fanout")]
    _require(bool(fanout_events) or summary.get("total_pages") == 3, "fanout left no trail")
    return _outcome("mageqa_local_audit", executed, started, extra_results=(authored,))


# --------------------------------------------------- scenario 3: GoPro frame inspection


class FrameObservation(BaseModel):
    objects: list[str]
    scene_change: bool
    confidence: float


async def run_gopro_frame_inspection(
    config: QualificationConfig, provider_factory: ProviderFactory, workdir: Path
) -> ScenarioOutcome:
    """Vision/evidence/memory composition: one staged PNG + a REAL StructuredStateMemory
    prior-state block in the prompt -> one structured vision call -> evidence fingerprint
    in the run; no raw bytes anywhere in state."""

    from types import SimpleNamespace

    from ai_workflow_engine import (
        AgentMemoryRenderContext,
        ImageInput,
        StructuredStateMemory,
        WorkflowBuilder,
    )
    from ai_workflow_engine.models import AgentToolCall, AgentToolStep, AgentRunRequest
    from ai_workflow_engine.vision import StructuredVisionLLMNode

    started = time.monotonic()
    inputs = workdir / "inputs" / "gopro"
    inputs.mkdir(parents=True, exist_ok=True)
    frame_path = inputs / "frame-0001.png"
    frame_path.write_bytes(_TINY_PNG)

    # REAL policy code renders the prior-state block from recorded history (no extra LLM
    # call): the same reducer GoPro runs, projected into the inspection prompt.
    memory = StructuredStateMemory()
    prior_history = [
        AgentToolStep(
            call=AgentToolCall(tool_name="inspect_frame", payload={"frame": "0000"}, rationale="prior"),
            status="accepted",
            output={"objects": ["mug"], "scene_change": False},
        )
    ]
    import json as _json

    render_context = AgentMemoryRenderContext(
        tool_content=lambda step: _json.dumps(step.output, sort_keys=True, default=str),
        images_from_output=lambda _output: [],
    )
    prior_messages = memory.render(
        AgentRunRequest(prompt="Track scene state.", allowed_tools=[]), prior_history, render_context
    )
    state_block = prior_messages[-1].content
    _require(
        StructuredStateMemory.DEFAULT_HEADING in state_block,
        "structured-state render did not produce the state block",
    )

    node = StructuredVisionLLMNode(
        name="inspect_frame",
        config=SimpleNamespace(),
        output_model=FrameObservation,
        prompt_template=(
            "{state_block}\n\nInspect the attached frame image. Respond with JSON only: "
            '{{"objects": ["..."], "scene_change": true/false, "confidence": 0.0-1.0}}\n'
            "{format_instructions}"
        ),
        input_variables=["state_block"],
        llm=provider_factory("gopro_frame_inspection", "frame_inspector"),
        max_repair_rounds=1,
    )
    image = ImageInput(source="path", data=str(frame_path), media_type="image/png", role="frame")

    builder = _observed_builder(workdir / "bundles" / "gopro")

    async def inspect_frame(_context, payload):
        observation = await node.run({"state_block": payload["state_block"]}, images=[image])
        return {"observation": observation.model_dump(), "evidence": image.fingerprint()}

    builder.register_capability("inspect_frame", inspect_frame, kind="llm")
    builder.register_workflow(WorkflowBuilder("gopro_frame_inspection").step("inspect_frame").build())
    engine = builder.build()

    with _profile_scope(config):
        result = await engine.run("gopro_frame_inspection", {"state_block": state_block})

    _require(result.status == "completed", f"frame inspection failed: {result.error}")
    output = result.output or {}
    _require(0.0 <= output["observation"]["confidence"] <= 1.0, "confidence out of range")
    fingerprint = output.get("evidence") or {}
    _require(
        bool(fingerprint.get("sha256") or fingerprint.get("hash") or fingerprint),
        "evidence fingerprint missing",
    )
    _require(_worker_calls(result) >= 1, "no real vision call recorded")
    blob = str(result.model_dump())
    _require(base64.b64encode(_TINY_PNG).decode()[:24] not in blob, "raw image bytes leaked into state")
    return _outcome("gopro_frame_inspection", result, started)


# ------------------------------------------------------- scenario 4: SlackAzz triage


class TriagePlan(BaseModel):
    urgency: str
    reply_hint: str


async def run_slackazz_triage(
    config: QualificationConfig,
    provider_factory: ProviderFactory,
    workdir: Path,
    *,
    resume_decision: str = "approve",
) -> ScenarioOutcome:
    """Operational shape: classify -> branch -> draft -> HUMAN suspend -> resume approve
    -> local artifact, while the external Slack send stays engine-DENIED."""

    from types import SimpleNamespace

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_engine.engine.llm_node import StructuredLLMNode

    started = time.monotonic()
    node = StructuredLLMNode(
        name="classify_message",
        config=SimpleNamespace(),
        output_model=TriagePlan,
        prompt_template=(
            'Triage this Slack message: "{message}". Respond with JSON only: '
            '{{"urgency": "normal" or "urgent", "reply_hint": "..."}}\n{format_instructions}'
        ),
        input_variables=["message"],
        llm=provider_factory("slackazz_triage", "triage_classifier"),
        max_repair_rounds=1,
    )
    builder = _observed_builder(workdir / "bundles" / "slackazz")

    async def classify_message(_context, payload):
        plan = await node.run({"message": payload["message"]})
        return {"plan": plan.model_dump(), "message": payload["message"]}

    def draft_reply(_context, payload):
        plan = payload["plan"]
        return {
            "draft": f"[{plan['urgency']}] {plan['reply_hint']}",
            "message": payload["message"],
        }

    class ApprovalOut(BaseModel):
        status: str
        decision: Optional[str] = None
        draft: str

    def approval(context, payload):
        # On resume the node re-runs with its OWN prior pending output as input — accept both.
        draft = payload.draft if isinstance(payload, ApprovalOut) else payload["draft"]
        event = context.metadata.get("resume_event")
        if event is None:
            return ApprovalOut(status="pending", draft=draft)
        return ApprovalOut(status="answered", decision=event.get("decision"), draft=draft)

    def finalize_draft(_context, payload: ApprovalOut):
        if payload.decision != "approve":
            raise ScenarioSemanticError(f"approval decision was not approve: {payload.decision}")
        return {"status": "approved_draft", "text": payload.draft, "sent": False}

    def send_slack(_context, payload):
        raise AssertionError("send_slack must never execute — external sends are denied")

    builder.register_capability("classify_message", classify_message, kind="llm")
    builder.register_capability("draft_reply", draft_reply, kind="deterministic")
    builder.register_capability("approval", approval, kind="deterministic")
    builder.register_capability("finalize_draft", finalize_draft, kind="deterministic")
    builder.register_capability(
        "send_slack", send_slack, kind="external", side_effects=["external_call"]
    )
    builder.register_workflow(
        WorkflowBuilder("slackazz_triage")
        .step("classify_message")
        .step("draft_reply")
        .human("approval")
        .step("finalize_draft")
        .build()
    )
    builder.register_workflow(WorkflowBuilder("slackazz_send_attempt").step("send_slack").build())
    engine = builder.build()

    with _profile_scope(config):
        first = await engine.run("slackazz_triage", {"message": "prod deploy failed, need rollback"})
    _require(first.status == "requires_user_input", f"triage did not suspend: {first.status}")
    _require(first.snapshot is not None, "suspension produced no snapshot")

    resumed = await engine.resume(first.snapshot, {"decision": resume_decision})
    _require(resumed.status == "completed", f"resume failed: {resumed.error}")
    _require(resumed.output.get("status") == "approved_draft", f"draft lost: {resumed.output}")
    _require(resumed.output.get("sent") is False, "nothing may be sent externally")
    _require(
        any(e.decision == "machine:resumed" for e in resumed.trace),
        "trace does not distinguish the resume",
    )

    denied = await engine.run("slackazz_send_attempt", {"text": "hi"})
    _require(denied.status == "failed", "external send was not denied")
    _require("external_call" in (denied.error or ""), f"denial is not explicit: {denied.error}")

    # The resumed result carries the CUMULATIVE usage summary (resume seeds it) — adding the
    # suspended half again would double-count the classify call and its notional cost.
    outcome = _outcome("slackazz_triage", resumed, started)
    outcome.bundle_path = resumed.observation_bundle_path or first.observation_bundle_path
    return outcome


SCENARIO_FUNCTIONS = {
    "anki_basic_card": run_anki_basic_card,
    "mageqa_local_audit": run_mageqa_local_audit,
    "gopro_frame_inspection": run_gopro_frame_inspection,
    "slackazz_triage": run_slackazz_triage,
}
