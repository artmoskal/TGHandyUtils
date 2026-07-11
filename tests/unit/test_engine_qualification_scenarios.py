"""Q2/Q3 hermetic qualification: real engines/workflows/bundles/viewer, fake ONLY at the
provider boundary. Every scenario runs free here; the live suite reuses these exact
scenario functions (Q3.2 = Phase 4.3 fixture, one set, reviewed once)."""

import json
from pathlib import Path

import pytest

from ai_workflow_engine.llm_protocol import LLMResponse

from tests.support.engine_qualification import (
    QualificationConfig,
    QualificationRunIdentity,
    ScenarioSemanticError,
    render_suite_report_html,
    run_qualification_suite,
)
from tests.support.engine_qualification_scenarios import (
    SCENARIO_FUNCTIONS,
    run_anki_basic_card,
    run_gopro_frame_inspection,
    run_mageqa_local_audit,
    run_slackazz_triage,
)

pytestmark = pytest.mark.unit


class FakeSubscriptionLLM:
    """Scripted subscription-priced client: known notional cost per call, honest tokens."""

    provider_label = "scripted_claude"
    subscription_mode = True

    def __init__(self, *texts: str, notional_usd: float = 0.0123, assert_images: bool = False):
        self.texts = list(texts)
        self.requests = []
        self.notional_usd = notional_usd
        self.assert_images = assert_images
        self.images_seen = False

    async def __call__(self, request):
        self.requests.append(request)
        if self.assert_images:
            self.images_seen = bool(request.images) or any(
                getattr(m, "images", None) for m in getattr(request, "messages", [])
            )
        text = self.texts[min(len(self.requests) - 1, len(self.texts) - 1)]
        return LLMResponse(
            text=text,
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
            cost_class="subscription_notional",
            notional_usd=self.notional_usd,
        )


_AUTHORED_AUDIT_FLOW = json.dumps(
    {
        "flow_id": "local_audit",
        "goal": "probe pages then summarize",
        "nodes": [
            {"kind": "step", "id": "collect_pages"},
            {
                "kind": "fanout",
                "id": "probe",
                "capability": "probe_page",
                "items_key": "collect_pages.pages",
                "max_items": 5,
                "max_parallel": 2,
            },
            {"kind": "step", "id": "summarize_findings"},
        ],
    }
)

_AUDIT_SUMMARY = '{"total_pages": 2, "ok_pages": 2, "inconclusive_pages": 0, "verdict": "two pages verified; one page failed probing"}'


def _fakes():
    """One fake per call site, keyed like the live factory would be."""

    return {
        ("anki_basic_card", "card_author"): FakeSubscriptionLLM(
            '{"front": "What cycle produces ATP in mitochondria?", "back": "The Krebs cycle", "tags": ["bio"]}'
        ),
        ("mageqa_local_audit", "flow_author"): FakeSubscriptionLLM(_AUTHORED_AUDIT_FLOW),
        ("mageqa_local_audit", "audit_summarizer"): FakeSubscriptionLLM(_AUDIT_SUMMARY),
        ("gopro_frame_inspection", "frame_inspector"): FakeSubscriptionLLM(
            '{"objects": ["mug"], "scene_change": false, "confidence": 0.8}', assert_images=True
        ),
        ("slackazz_triage", "triage_classifier"): FakeSubscriptionLLM(
            '{"urgency": "urgent", "reply_hint": "acknowledge and start rollback"}'
        ),
    }


def _factory(fakes):
    def provider_factory(scenario: str, role: str):
        return fakes[(scenario, role)]

    return provider_factory


def _config(tmp_path: Path, **overrides) -> QualificationConfig:
    values = dict(output_dir=str(tmp_path / "out"))
    values.update(overrides)
    return QualificationConfig(**values)


def _identity() -> QualificationRunIdentity:
    return QualificationRunIdentity(
        requested_model="sonnet",
        cli_version="fake-cli 0.0 (hermetic)",
        git_commit="hermetic",
        platform="test",
        started_at="2026-07-11T00:00:00+00:00",
    )


async def test_anki_scenario_passes_hermetically_with_priced_subscription_call(tmp_path):
    fakes = _fakes()
    outcome = await run_anki_basic_card(_config(tmp_path), _factory(fakes), tmp_path)

    assert outcome.status == "passed"
    assert outcome.worker_calls == 1
    assert outcome.notional_usd == 0.0123, "the ledger must see the reported notional cost"
    assert outcome.bundle_path and Path(outcome.bundle_path).exists()


async def test_anki_scenario_reports_malformed_output_as_classified_provider_failure(tmp_path):
    """Q-R2/Q-R4: parse/repair exhaustion returns a CLASSIFIED failed outcome that keeps
    the burn (both attempted calls) — never a bare raise that erases money data."""

    fakes = _fakes()
    fakes[("anki_basic_card", "card_author")] = FakeSubscriptionLLM("utter garbage")
    outcome = await run_anki_basic_card(_config(tmp_path), _factory(fakes), tmp_path)

    assert outcome.status == "failed"
    assert outcome.failure_class == "provider"
    assert outcome.worker_calls == 2, "initial + repair attempts both count as spend"
    assert outcome.notional_usd == pytest.approx(0.0246), "the failed attempts kept their burn"


async def test_mageqa_scenario_authors_runs_and_isolates_a_real_child_failure(tmp_path):
    """Q-R7: page2 RAISES inside the fanout — the engine's partial-isolation contract is
    proven by the fanout TRACE (total=3, succeeded=2, failed=1), not by the LLM's count."""

    fakes = _fakes()
    outcome = await run_mageqa_local_audit(_config(tmp_path), _factory(fakes), tmp_path)

    assert outcome.status == "passed"
    assert outcome.worker_calls == 2, "author + summarizer are the only real calls"
    assert outcome.notional_usd == pytest.approx(0.0246)
    assert outcome.linked_bundles.get("author"), "the author half must have its own bundle (Q-R5)"
    author_fake = fakes[("mageqa_local_audit", "flow_author")]
    assert "probe_page" in author_fake.requests[0].user, "catalog must advertise the toolbox"


async def test_mageqa_rejects_an_artifact_referencing_unknown_capability(tmp_path):
    fakes = _fakes()
    bad_flow = _AUTHORED_AUDIT_FLOW.replace("probe_page", "ghost_probe")
    fakes[("mageqa_local_audit", "flow_author")] = FakeSubscriptionLLM(bad_flow)
    outcome = await run_mageqa_local_audit(_config(tmp_path), _factory(fakes), tmp_path)

    assert outcome.status == "failed"
    assert outcome.failure_class == "provider"
    assert "ghost_probe" in outcome.detail or "unknown" in outcome.detail.lower()
    assert outcome.worker_calls >= 1, "the author attempts still count as spend"


async def test_gopro_scenario_consumes_image_and_prior_state(tmp_path):
    fakes = _fakes()
    outcome = await run_gopro_frame_inspection(_config(tmp_path), _factory(fakes), tmp_path)

    assert outcome.status == "passed"
    assert outcome.worker_calls == 1
    inspector = fakes[("gopro_frame_inspection", "frame_inspector")]
    assert inspector.images_seen, "the staged frame must reach the provider call"
    from ai_workflow_engine import StructuredStateMemory

    assert StructuredStateMemory.DEFAULT_HEADING in inspector.requests[0].user, (
        "the prompt must carry the REAL rendered prior-state block"
    )


async def test_gopro_scenario_reports_malformed_vision_output_as_provider_failure(tmp_path):
    """Failure pair: exhausted parse/repair on the vision call returns a classified failed
    outcome with its burn — loud in the ledger, never a fake pass."""

    fakes = _fakes()
    fakes[("gopro_frame_inspection", "frame_inspector")] = FakeSubscriptionLLM(
        "not json at all", assert_images=True
    )
    outcome = await run_gopro_frame_inspection(_config(tmp_path), _factory(fakes), tmp_path)

    assert outcome.status == "failed"
    assert outcome.failure_class == "provider"
    assert outcome.worker_calls == 2 and outcome.failed_worker_calls == 0, (
        "both parse-failed attempts are ATTEMPTED spend (transport succeeded)"
    )


async def test_slackazz_scenario_suspends_resumes_and_denies_external_send(tmp_path):
    fakes = _fakes()
    outcome = await run_slackazz_triage(_config(tmp_path), _factory(fakes), tmp_path)

    assert outcome.status == "passed"
    assert outcome.worker_calls == 1, "classification is the only real call"
    assert outcome.notional_usd == pytest.approx(0.0123)
    # Q-R5: BOTH lifecycle halves are observable — resumed bundle is the primary and ends
    # completed; the suspension half stays linked.
    assert outcome.bundle_path and outcome.linked_bundles.get("suspension")
    assert outcome.bundle_path != outcome.linked_bundles["suspension"]
    import json as _json

    resumed_trace = (Path(outcome.bundle_path) / "trace.jsonl").read_text(encoding="utf-8")
    assert "machine:resumed" in resumed_trace
    meta = _json.loads((Path(outcome.bundle_path) / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("status") == "completed", f"resumed bundle must finalize completed: {meta}"


async def test_slackazz_reject_decision_fails_loudly_through_the_real_resume_path(tmp_path):
    """The REJECT resume path: same engine, same snapshot mechanics — a non-approve
    decision must fail the run loudly, never quietly complete."""

    fakes = _fakes()
    with pytest.raises(ScenarioSemanticError):
        await run_slackazz_triage(
            _config(tmp_path), _factory(fakes), tmp_path, resume_decision="reject"
        )


def test_full_hermetic_suite_produces_ledger_report_and_viewer_pages(tmp_path):
    """Q3.2: the WHOLE cross-consumer suite, free — real engines, bundles, ledger,
    summary JSON, index.html, and one viewer page per scenario."""

    fakes = _fakes()
    factory = _factory(fakes)
    config = _config(tmp_path)
    runners = {
        name: (lambda cfg, fn=fn: fn(cfg, factory, tmp_path))
        for name, fn in SCENARIO_FUNCTIONS.items()
    }

    report = run_qualification_suite(config, runners, identity=_identity())

    assert report.completed, f"suite stopped: {report.stopped_reason}"
    assert [o.status for o in report.outcomes] == ["passed"] * 4
    assert report.worker_calls_total == 5  # 1 + 2 + 1 + 1
    assert report.notional_usd_total == pytest.approx(0.0123 * 5)
    assert report.worker_calls_total <= config.max_worker_calls
    assert report.notional_usd_total < config.suite_budget_usd

    summary_path = Path(config.output_dir) / "qualification-summary.json"
    assert summary_path.exists()
    persisted = json.loads(summary_path.read_text(encoding="utf-8"))
    assert persisted["completed"] is True
    assert len(persisted["outcomes"]) == 4

    index = render_suite_report_html(report)
    html = Path(index).read_text(encoding="utf-8")
    assert "COMPLETED" in html
    assert "sonnet" in html and "hermetic" in html
    assert "NEVER summed with metered" in html, "money-honesty labeling is mandatory"
    for name in SCENARIO_FUNCTIONS:
        assert name in html
        viewer = Path(config.output_dir) / f"{name}.html"
        assert viewer.exists(), f"viewer page missing for {name}"
        assert "<html" in viewer.read_text(encoding="utf-8").lower()
    # Q-R5: linked lifecycle halves get their own pages
    assert (Path(config.output_dir) / "mageqa_local_audit--author.html").exists()
    assert (Path(config.output_dir) / "slackazz_triage--suspension.html").exists()
    # Q-R6: the PERSISTED manifest carries finished_at and the viewer paths
    final = json.loads(summary_path.read_text(encoding="utf-8"))
    assert final["identity"]["finished_at"], "finished_at must be stamped on completion"
    assert all(o["viewer_html_path"] for o in final["outcomes"]), (
        "persisted outcomes must carry their viewer paths after report generation"
    )
    # Q-R7: the GoPro staged frame is a durable bundle artifact the viewer can resolve
    gopro = next(o for o in report.outcomes if o.name == "gopro_frame_inspection")
    artifacts = json.loads((Path(gopro.bundle_path) / "artifacts.json").read_text(encoding="utf-8"))
    assert artifacts, "gopro bundle must archive the staged-frame artifact"


def test_suite_persists_partial_report_and_stops_on_scenario_failure(tmp_path):
    fakes = _fakes()
    fakes[("anki_basic_card", "card_author")] = FakeSubscriptionLLM("garbage")
    factory = _factory(fakes)
    config = _config(tmp_path)
    runners = {
        name: (lambda cfg, fn=fn: fn(cfg, factory, tmp_path))
        for name, fn in SCENARIO_FUNCTIONS.items()
    }

    report = run_qualification_suite(config, runners, identity=_identity())

    persisted = json.loads(
        (Path(config.output_dir) / "qualification-summary.json").read_text(encoding="utf-8")
    )
    assert report.completed is False and persisted["completed"] is False
    assert persisted["stopped_reason"], "the stop reason must be persisted"
    assert len(persisted["outcomes"]) == 1, "later scenarios must NOT have started"
    assert persisted["outcomes"][0]["status"] == "failed"
    assert persisted["outcomes"][0]["failure_class"] == "provider"
    assert persisted["outcomes"][0]["notional_usd"] == pytest.approx(0.0246), (
        "the failed scenario's burn must be persisted for the ledger"
    )
    assert persisted["identity"]["finished_at"], "stopped suites stamp finished_at too"
