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


async def test_gopro_scenario_fails_precisely_on_a_leaked_image_data_uri(tmp_path):
    """A9: the byte-free-state guard is precise — a vision worker that echoes a base64
    image data-URI into result state FAILS (byte-free-state violation), and it is caught
    structurally, not by the universal PNG-header prefix. A clean run still passes, so this
    is not a blanket 'any PNG anywhere' false positive on unrelated text."""

    import base64 as _b64

    leaked = _b64.b64encode(b"\x89PNG\r\n\x1a\nDIFFERENT-IMAGE-BYTES").decode()
    fakes = _fakes()
    # the data-URI rides a legitimate schema field (objects: list[str]) so it SURVIVES into
    # result state, unlike an extra field the output model would strip.
    fakes[("gopro_frame_inspection", "frame_inspector")] = FakeSubscriptionLLM(
        '{"objects": ["mug", "data:image/png;base64,' + leaked + '"], '
        '"scene_change": false, "confidence": 0.8}',
        assert_images=True,
    )
    import pytest as _pytest

    from tests.support.engine_qualification import ScenarioSemanticError

    with _pytest.raises(ScenarioSemanticError, match="data-URI leaked"):
        await run_gopro_frame_inspection(_config(tmp_path), _factory(fakes), tmp_path)


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


async def test_completed_mageqa_viewer_has_no_nodes_left_running(tmp_path):
    """Q4.2 (codex reproducer): a COMPLETED hermetic MageQA run projects a truthful graph —
    no node is left 'running'; the failed-child fanout shows partial, steps show completed,
    and the flow:authored pseudo-node is terminal."""

    from ai_workflow_viewer import FileEventSource, build_observation_graph

    fakes = _fakes()
    outcome = await run_mageqa_local_audit(_config(tmp_path), _factory(fakes), tmp_path)
    assert outcome.status == "passed"

    run_data = FileEventSource(outcome.bundle_path).read()
    graph = build_observation_graph(
        run_data.definition,
        run_data.trace_events,
        run_data.usage_events,
        run_data.details,
        run_id=run_data.run_id,
    )
    statuses = {node_id: node.status for node_id, node in graph.nodes.items()}
    running = [n for n, status in statuses.items() if status == "running"]
    assert not running, f"completed run must leave no node 'running': {statuses}"
    assert statuses.get("probe") == "partial", (
        f"the fanout with one real child failure must project partial: {statuses}"
    )
    assert statuses.get("collect_pages") == "completed"


async def test_partial_fanout_keeps_child_failure_reason_in_projection(tmp_path):
    """QRF.5 (codex reproducer): the partial fanout node keeps WHY its child failed —
    projected status stays 'partial' (typed field wins over error-implies-failed) and the
    child's failure text is present exactly once."""

    from ai_workflow_viewer import FileEventSource, build_observation_graph

    fakes = _fakes()
    outcome = await run_mageqa_local_audit(_config(tmp_path), _factory(fakes), tmp_path)
    assert outcome.status == "passed"

    run_data = FileEventSource(outcome.bundle_path).read()
    graph = build_observation_graph(
        run_data.definition,
        run_data.trace_events,
        run_data.usage_events,
        run_data.details,
        run_id=run_data.run_id,
    )
    probe = graph.nodes["probe"]
    assert probe.status == "partial", f"typed terminal field must win: {probe.status}"
    reasons = [e for e in probe.errors if "unparseable page" in e]
    assert len(reasons) == 1, (
        f"the child failure reason must be preserved exactly ONCE: {probe.errors}"
    )


def test_failed_node_error_is_projected_once():
    """QRF.6 (codex reproducer): the original failure event and the terminal record carry
    the same text — the projected node shows it once, never duplicated."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_engine.models import WorkflowTraceEvent
    from ai_workflow_viewer import build_observation_graph

    definition = WorkflowBuilder("dup_err").step("boom").build()
    events = [
        WorkflowTraceEvent(node="boom", decision="failed", error="kaput", run_id="r1"),
        WorkflowTraceEvent(node="boom", node_status="failed", error="kaput", run_id="r1"),
    ]
    graph = build_observation_graph(definition, events, run_id="r1")
    assert graph.nodes["boom"].status == "failed"
    assert graph.nodes["boom"].errors == ["kaput"], "same evidence text appears exactly once"



async def test_v010_runtime_contracts_project_through_the_public_door_and_viewer(tmp_path):
    """Phase 4.1 integrated hermetic qualification: ONE real workflow through
    ``WorkflowEngine.run`` with a real observation bundle proves the v0.10 contracts compose
    and are projected truthfully — an accepted planner task stays done, a timeout-salvaged
    task stays a terminal PARTIAL with its output/artifact/error, two retrace rounds are
    structured, and the effective execution window is recorded. The bundle + viewer show the
    same outcome (no MageQA imports, no manual orchestration loop, providers not even needed —
    deterministic capabilities so the contract, not a provider, is under test)."""

    from ai_workflow_engine import (
        ObservationConfig,
        PlanArtifact,
        PlanTask,
        Retrace,
        WorkflowArtifact,
        WorkflowBuilder,
        WorkflowEngineBuilder,
    )
    from ai_workflow_engine.models import (
        CapabilityResult,
        RuntimeLimits,
        SafetyPolicy,
        WorkflowProfile,
    )
    from ai_workflow_viewer import FileEventSource, build_observation_graph
    from ai_workflow_viewer.observability import _observation_view_data, observation_graph_to_html

    bundle_dir = tmp_path / "bundle"

    async def planner(_ctx, _payload):
        return PlanArtifact(
            goal="audit pages under a bounded window",
            tasks=[
                PlanTask(task_id="full", description="whole page", capability="full_audit", payload=1),
                # a timeout-salvaged task: the capability RETURNS a terminal PARTIAL carrying its
                # salvaged output + artifact + the deadline error — exactly the shape the CLI agent
                # returns after its subprocess is stopped at the soft window (Phase 3). The whole
                # run is bounded by the profile timeout, so every node still carries a window.
                PlanTask(
                    task_id="salvage",
                    description="cut short at the deadline",
                    capability="salvaged_audit",
                    payload=2,
                ),
            ],
        )

    async def full_audit(_ctx, payload):
        return {"audited": payload, "pages": 10}

    async def salvaged_audit(_ctx, _payload):
        return CapabilityResult(
            status="partial",
            output={"pages_done": 3, "pages_total": 10},
            error="incomplete: stopped at the execution-window deadline (3 of 10 pages)",
            artifacts=[WorkflowArtifact(path="artifacts/page3.png", artifact_id="frame-3", kind="media")],
            metadata={"pages_done": 3},
        )

    rounds: list[int] = []

    async def refine(ctx, payload):
        prov = getattr(ctx, "retrace_provenance", None)
        rounds.append(prov.round if prov is not None else 0)
        return payload

    async def gate(_ctx, _payload):
        # reject the first two evaluations (-> two retrace rounds), then accept
        attempts = len(rounds)
        return CapabilityResult(
            status="accepted" if attempts >= 3 else "rejected",
            error=None if attempts >= 3 else "needs another pass",
        )

    engine = (
        WorkflowEngineBuilder()
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(bundle_dir)))
        .with_profile(
            WorkflowProfile(
                workflow_type="v010_qual",
                limits=RuntimeLimits(timeout_s=30.0),
                safety=SafetyPolicy(allowed_side_effects=[]),
            )
        )
        .register_capability("planner", planner, kind="llm")
        .register_capability("full_audit", full_audit, kind="deterministic")
        .register_capability("salvaged_audit", salvaged_audit, kind="deterministic")
        .register_capability("refine", refine, kind="deterministic")
        .register_capability("gate", gate, kind="llm")
        .register_workflow(
            WorkflowBuilder("v010_qual")
            .plan("audit", capability="planner")
            .step("refine")
            .evaluate("qa", target="refine", evaluator="gate", on_reject=Retrace("refine", max_retrace=2))
            .build()
        )
        .build()
    )

    result = await engine.run("v010_qual", {})

    # --- two structured retrace rounds actually happened, delivered to the target ---
    assert rounds == [0, 1, 2], f"expected first pass then retrace rounds 1 and 2: {rounds}"

    # --- planner-task truth surfaces through the OBSERVATION BUNDLE (the public read model),
    # not just in-process state: the salvaged task stayed a terminal PARTIAL and its error
    # survived into the record — never laundered to done with the error cleared ---
    run_data = FileEventSource(str(bundle_dir)).read()
    trace_text = " ".join(
        f"{e.decision or ''}|{e.error or ''}|{e.node}" for e in run_data.trace_events
    )
    assert "incomplete: stopped at the execution-window deadline" in trace_text, (
        "the salvaged task's partial error must survive into the bundle, never be cleared"
    )

    # --- the observation bundle projects the SAME outcome through the viewer ---
    graph = build_observation_graph(
        run_data.definition, run_data.trace_events, run_data.usage_events, run_data.details,
        run_id=run_data.run_id,
    )
    assert graph.nodes["audit"].status == "partial", (
        "the plan node with a partial task must project partial: "
        f"{ {k: v.status for k, v in graph.nodes.items()} }"
    )
    metrics = {n["id"]: n["metrics"] for n in _observation_view_data(run_data.definition, graph)["nodes"]}
    # the evaluator node projects the retrace round -> target from persisted truth
    assert any("retrace round 2" in m and "refine" in m for m in metrics.get("qa", [])), metrics.get("qa")
    # a bounded run means the capability nodes carry a projected window
    all_metrics = " ".join(m for ms in metrics.values() for m in ms)
    assert "window: soft" in all_metrics, f"a bounded run must project a window: {metrics}"
    # the viewer HTML renders the same truth
    page = observation_graph_to_html(run_data.definition, graph)
    assert "retrace round 2" in page


async def test_v010_real_timeout_through_engine_run_salvages_reaps_and_projects(
    tmp_path, monkeypatch
):
    """5R finding 6 — the promised END-TO-END guarantee, no manufactured partial: a REAL
    slow subprocess (fake CLI writes a PNG then sleeps far past the budget) behind the REAL
    CliAgentCapability, invoked through ``WorkflowEngine.run`` under an enforced
    ``RuntimeLimits.timeout_s``. The engine window stops the child BEFORE the hard deadline,
    the pre-deadline artifact is salvaged into a truthful PARTIAL, the child is REAPED (no
    survivor, no zombie), and the observation bundle + viewer project the same truth."""

    import asyncio
    import os
    import sys
    import time as _t

    from ai_workflow_engine import ObservationConfig, WorkflowBuilder, WorkflowEngineBuilder
    from ai_workflow_engine.models import RuntimeLimits, SafetyPolicy, WorkflowProfile
    from ai_workflow_tools.cli_agents import CliAgentCapability, CliAgentRequest, claude_p
    from ai_workflow_viewer import FileEventSource, build_observation_graph
    from ai_workflow_viewer.observability import _observation_view_data, observation_graph_to_html

    fake_cli = Path(__file__).parents[2] / "packages" / "ai_workflow_tools" / "tests" / "fake_cli.py"
    assert fake_cli.exists(), f"fake CLI missing at {fake_cli}"
    workspace = tmp_path / "workspace"
    record_path = tmp_path / "record.json"
    bundle_dir = tmp_path / "bundle"
    monkeypatch.setenv("FAKE_CLI_MODE", "sleep")
    monkeypatch.setenv("FAKE_CLI_SLEEP_S", "20")  # far past every bound below
    monkeypatch.setenv("FAKE_CLI_WORKSPACE", str(workspace))
    monkeypatch.setenv("FAKE_CLI_RECORD", str(record_path))

    cap = CliAgentCapability(
        claude_p.model_copy(update={"base_argv": [sys.executable, str(fake_cli)]}),
        name="browser_probe",
        side_effects=[],
    )
    engine = (
        WorkflowEngineBuilder()
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(bundle_dir)))
        .with_profile(
            WorkflowProfile(
                workflow_type="real_timeout",
                limits=RuntimeLimits(timeout_s=3.0),  # the ONLY bound — no request timeout
                safety=SafetyPolicy(allowed_side_effects=[]),
            )
        )
        .register_capability("browser_probe", cap, spec=cap.spec)
        .register_workflow(WorkflowBuilder("real_timeout").step("browser_probe").build())
        .build()
    )

    started = _t.monotonic()
    result = await engine.run(
        "real_timeout",
        CliAgentRequest(
            prompt="Capture the page", workspace_dir=str(workspace), allowed_tools=[]
        ).model_dump(),
    )
    elapsed = _t.monotonic() - started

    # the engine window (run budget), not the 20s sleep, decided when this ended — and the
    # capability salvaged + returned BEFORE the authoritative hard deadline (no containment).
    assert result.status == "partial", f"a stopped bounded run is PARTIAL truth: {result.status}"
    assert elapsed < 6.0, f"the run must end at the ~3s window, not the 20s sleep ({elapsed:.1f}s)"
    node = result.node("browser_probe")
    assert node is not None and node.status == "partial"

    # the pre-deadline PNG survived as salvage on the real result path
    cap_output = node.output
    assert getattr(cap_output, "status", None) == "truncated"
    assert [a.role for a in cap_output.artifacts] == ["screenshot"]

    # the child was killed AND reaped — no survivor, no zombie outliving the engine's claim
    pid = json.loads(record_path.read_text(encoding="utf-8"))["pid"]
    reap_deadline = _t.monotonic() + 2.0
    while _t.monotonic() < reap_deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        os.kill(pid, 9)
        raise AssertionError(f"child {pid} still alive after the run — not killed/reaped")

    # the observation bundle + viewer project the SAME truth from persisted records
    run_data = FileEventSource(str(bundle_dir)).read()
    graph = build_observation_graph(
        run_data.definition, run_data.trace_events, run_data.usage_events, run_data.details,
        run_id=run_data.run_id,
    )
    assert graph.nodes["browser_probe"].status == "partial"
    assert any("timed out" in e for e in graph.nodes["browser_probe"].errors), (
        f"the timeout evidence must persist: {graph.nodes['browser_probe'].errors}"
    )
    metrics = {
        n["id"]: n["metrics"]
        for n in _observation_view_data(run_data.definition, graph)["nodes"]
    }
    assert any(m.startswith("window: soft") for m in metrics["browser_probe"]), (
        f"the enforced window must be projected from persisted truth: {metrics['browser_probe']}"
    )
    assert any(m.startswith("process: work") for m in metrics["browser_probe"]), (
        f"the process work/reap split must be projected: {metrics['browser_probe']}"
    )
    page = observation_graph_to_html(run_data.definition, graph)
    assert "window: soft" in page and "process: work" in page and "timed out" in page


async def test_v0101_real_process_flood_and_unsafe_result_project_bounded_through_bundle_viewer(
    tmp_path,
):
    """v0.10.1 Phase 2 gate: a REAL subprocess flood + an unsafe (symlink) result file, run
    through ``WorkflowEngine.run`` with a real observation bundle. The engine bounds capture
    (retained memory O(cap), not O(6 MiB)), rejects the symlinked result as failed WITHOUT
    disclosing its target, and the bundle + viewer project a CONCISE settlement summary — no
    flood copied anywhere."""

    import os
    import sys

    from ai_workflow_engine import ObservationConfig, WorkflowBuilder, WorkflowEngineBuilder
    from ai_workflow_engine.engine.external import ExternalProcessCapability, ExternalProcessRequest
    from ai_workflow_engine.models import SafetyPolicy, WorkflowProfile
    from ai_workflow_viewer import FileEventSource, build_observation_graph
    from ai_workflow_viewer.observability import _observation_view_data, observation_graph_to_html

    bundle_dir = tmp_path / "bundle"
    secret = tmp_path / "host-secret.env"
    secret.write_text("SENTINEL-E2E-DO-NOT-LEAK-77aa", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result_link = workspace / "codex-last-message.txt"
    os.symlink(secret, result_link)

    cap = ExternalProcessCapability(name="probe", side_effects=["external_call"])
    engine = (
        WorkflowEngineBuilder()
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(bundle_dir)))
        .with_profile(
            WorkflowProfile(
                workflow_type="proc_e2e",
                safety=SafetyPolicy(allowed_side_effects=["external_call"]),
            )
        )
        .register_capability("probe", cap, spec=cap.spec)
        .register_workflow(WorkflowBuilder("proc_e2e").step("probe").build())
        .build()
    )
    # a child that floods stdout with 6 MiB AND leaves the symlinked result in place, exit 0
    flood = "import sys; sys.stdout.write('F' * (6 * 1024 * 1024))"
    result = await engine.run(
        "proc_e2e",
        ExternalProcessRequest(
            command=[sys.executable, "-c", flood],
            cwd=str(workspace),
            result_file=str(result_link),
            timeout_s=30.0,
        ),
    )

    # exit 0 but the requested result file is a symlink -> failed settlement, target NOT disclosed
    assert result.status == "failed", f"an unsafe result file is a failed settlement: {result.status}"
    envelope = result.model_dump_json()
    assert "SENTINEL-E2E-DO-NOT-LEAK-77aa" not in envelope, "the symlink target leaked into the result"
    # capture stayed bounded despite 6 MiB of stdout
    assert len(result.output["stdout"]) <= 2 * 1024 * 1024

    # the observation bundle + viewer project the same bounded truth from persisted records
    run_data = FileEventSource(str(bundle_dir)).read()
    graph = build_observation_graph(
        run_data.definition, run_data.trace_events, run_data.usage_events, run_data.details,
        run_id=run_data.run_id,
    )
    metrics = {n["id"]: n["metrics"] for n in _observation_view_data(run_data.definition, graph)["nodes"]}
    probe_metrics = " || ".join(metrics.get("probe", []))
    assert "capture: stdout" in probe_metrics and "truncated" in probe_metrics
    assert "result file: unsafe (symlink)" in probe_metrics

    page = observation_graph_to_html(run_data.definition, graph)
    # the FULL 6 MiB flood is never copied anywhere — only the bounded salvage (<= 1 MiB cap)
    # can appear as extractable detail, and the page stays far below the source volume.
    assert "F" * (2 * 1024 * 1024) not in page, "the full flood must never reach the page"
    assert len(page) < 3 * 1024 * 1024, f"the page must stay bounded, not echo the flood ({len(page)})"
    assert "SENTINEL-E2E-DO-NOT-LEAK-77aa" not in page, "the viewer must never disclose the symlink target"
    # and the source counter (total bytes observed) is preserved for diagnosis
    assert any("6.0 MiB" in m for m in metrics["probe"]), "the 6 MiB source total must remain visible"
