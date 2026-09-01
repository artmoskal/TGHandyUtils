"""Unit tests for the typed Anki generation graph."""

from unittest.mock import Mock

import pytest

from models.anki import AnkiCard
from models.anki_workflow import (
    CardBuildPlan,
    ClozeCardScenario,
    ImageAssetPlan,
    RenderedCardEvaluation,
    RenderedCardSet,
    TextCardScenario,
    VisualCardScenario,
)
from ai_workflow_tools.media.image_models import (
    GeneratedImage,
    ImageFreshnessEvidence,
    ImageGenerationEvidence,
)
from ai_workflow_tools.media.voice_generation import GeneratedVoiceAudio
from ai_workflow_engine import ObservationReader
from ai_workflow_engine.engine import InMemoryDetailSink
from ai_workflow_viewer import JsonlObservationViewer, build_observation_graph
from services.content.anki_generation_graph import AnkiGenerationGraph
from services.content.anki_source import build_content_source
from services.content.anki_directives import parse_directives


class FakeCardSetPlanner:
    def __init__(self, plan):
        self._plan = plan
        self.calls = []

    async def plan(self, source, directives, image_asset_plan, can_generate_images):
        self.calls.append((source, directives, image_asset_plan, can_generate_images))
        return self._plan


class FakeSequentialCardSetPlanner:
    def __init__(self, plans):
        self._plans = list(plans)
        self.calls = []

    async def plan(self, source, directives, image_asset_plan, can_generate_images):
        self.calls.append((source, directives, image_asset_plan, can_generate_images))
        return self._plans.pop(0)


class FakeScenarioPlanner:
    def __init__(self, scenario):
        self._scenario = scenario
        self.calls = []

    async def plan(self, *args):
        self.calls.append(args)
        return self._scenario


class FakeImageGenerator:
    def __init__(self, fail=False, generation_evidence=None):
        self.fail = fail
        self.generation_evidence = generation_evidence
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("image failed")
        path = f"{request.output_dir}/{request.output_basename}"
        import os

        os.makedirs(request.output_dir, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"png")
        return GeneratedImage(
            path=path,
            basename=request.output_basename,
            model=request.model,
            size=request.size,
            quality=request.quality,
            output_format=request.output_format,
            reference_image_count=len(request.reference_image_paths),
            style_reference_version=request.style_reference_version,
            generation_evidence=self.generation_evidence,
        )


class FakeVoiceGenerator:
    def __init__(self, fail=False):
        self.fail = fail
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("voice failed")
        path = f"{request.output_dir}/{request.output_basename}"
        import os

        os.makedirs(request.output_dir, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"mp3")
        return GeneratedVoiceAudio(
            path=path,
            basename=request.output_basename,
            provider="elevenlabs",
            model=request.model,
            voice_id="voice-test",
            output_format=request.output_format,
            character_count=len(request.text),
        )


class FakeQualityEvaluator:
    def __init__(self, evaluations):
        self.evaluations = list(evaluations)
        self.calls = []

    async def evaluate(self, *args):
        self.calls.append(args)
        return self.evaluations.pop(0)


class CapturingRunner:
    def __init__(self):
        self.graph_config = None
        self.goal = None
        self.recursion_fallback = None

    async def run(self, _graph, _state, *, workflow_type=None, goal, graph_config=None, recursion_fallback=None, session=None):
        self.graph_config = graph_config
        self.goal = goal
        self.recursion_fallback = recursion_fallback
        # The engine executor reads the final workflow state from the runner result; the running
        # workflow payload (the merged Anki state) lives under the "payload" key.
        return {
            "payload": {
                "rendered": RenderedCardSet(
                    cards=[AnkiCard(type="basic", question="Q", answer="A")],
                    image_asset_plan=ImageAssetPlan(image_role="ignore_media"),
                )
            }
        }


class FailingRunner:
    async def run(self, *_args, **_kwargs):
        raise RuntimeError("runner exploded")


@pytest.mark.unit
def test_graph_rejects_non_path_style_reference_images():
    svc = Mock()

    with pytest.raises(TypeError, match="style_reference_images"):
        AnkiGenerationGraph(svc, style_reference_images=[object()])


@pytest.mark.unit
async def test_graph_invokes_runner_with_explicit_recursion_limit():
    svc = Mock()
    runner = CapturingRunner()
    source = build_content_source([("U", "Q -> A", None)], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        runner=runner,
        max_quality_repairs_per_run=2,
        max_image_generations_per_run=1,
        max_voice_generations_per_run=1,
    ).run(source)

    assert rendered.cards[0].question == "Q"
    assert runner.graph_config == {"recursion_limit": 96}
    assert callable(runner.recursion_fallback)
    assert runner.goal.metadata["telegram_chat_id"] is None
    assert runner.goal.metadata["telegram_message_id"] is None
    assert runner.goal.metadata["run_id"]


@pytest.mark.unit
async def test_graph_recursion_exhaustion_uses_text_fallback_instead_of_leaking_error():
    svc = Mock()
    svc.extract_cards.return_value = [
        AnkiCard(
            question="Which document proves an aircraft is registered?",
            answer="The Certificate of Registration.",
        )
    ]
    source = build_content_source(
        [("U", "[i gen q1] Certificate of Registration is carried on board.")],
        user_id=10,
        owner_name="U",
    )
    graph = AnkiGenerationGraph(svc, enable_image_generation=True)
    graph._graph_recursion_limit = lambda: 1

    rendered = await graph.run(source)

    assert rendered.fallback_used is True
    assert "workflow recursion limit exceeded" in rendered.fallback_reason
    assert rendered.cards[0].question == "Which document proves an aircraft is registered?"
    assert svc.extract_cards.call_args.args[4] == "basic"
    assert any(
        event.node == "fallback_to_text" and event.decision == "recursion_fallback"
        for event in graph.last_run_state["trace"]
    )
    assert any(
        event.node == "validate_rendered_cards" and event.decision == "valid"
        for event in graph.last_run_state["trace"]
    )


@pytest.mark.unit
def test_content_source_preserves_image_analysis_and_bytes():
    screenshot = {"image_data": b"img", "file_id": "f1", "file_name": "diagram.jpg"}
    source = build_content_source(
        [("U", "[SCREENSHOT TEXT]\nValve\n\n[SCREENSHOT DESCRIPTION] diagram", screenshot)],
        user_id=10,
        owner_name="U",
        location="Lisbon",
    )

    assert "[SCREENSHOT TEXT]" in source.content
    assert source.images[0].file_id == "f1"
    assert source.images[0].image_data == b"img"
    assert source.location == "Lisbon"


@pytest.mark.unit
def test_parse_visual_directive():
    directives, cleaned = parse_directives("[i visual] valve diagram")
    assert directives.card_type == "visual"
    assert cleaned == "valve diagram"


@pytest.mark.unit
def test_parse_generated_visual_directive():
    directives, cleaned = parse_directives("[i gen] valve diagram")
    assert directives.card_type == "visual"
    assert directives.image_policy == "generate"
    assert cleaned == "valve diagram"


@pytest.mark.unit
def test_parse_language_voice_directive_defaults_to_pt_generated_visual():
    directives, cleaned = parse_directives("[i langvoice ukr->pt gen] привіт")
    assert directives.language_voice is True
    assert directives.source_language == "ukr"
    assert directives.target_language == "pt"
    assert directives.card_type == "visual"
    assert directives.image_policy == "generate"
    assert directives.strategy == "merge"
    assert directives.count == 1
    assert cleaned == "привіт"


@pytest.mark.unit
async def test_graph_reuses_uploaded_image_by_default():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    source = build_content_source(
        [("U", "[SCREENSHOT DESCRIPTION] valve diagram", {"image_data": b"img", "file_id": "f1"})],
        user_id=10,
        owner_name="U",
    )

    rendered = await AnkiGenerationGraph(svc).run(source)

    assert rendered.cards[0].question == "What is shown?"
    assert rendered.image_asset_plan.image_role == "reuse_user_image"
    assert rendered.image_asset_plan.candidate_back_images == [0]
    svc.extract_cards.assert_called_once()


@pytest.mark.unit
async def test_graph_nodes_run_through_generic_capability_runtime(tmp_path):
    from pathlib import Path

    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    source = build_content_source([("U", "Valve diagram")], user_id=10, owner_name="U")
    details = InMemoryDetailSink()
    graph = AnkiGenerationGraph(
        svc,
        detail_sink=details,
        capture_observation_detail_text=True,
        observation_bundle_dir=str(tmp_path / "observations"),
    )

    rendered = await graph.run(source)

    assert rendered.cards[0].question == "What is shown?"
    assert "parse_directives" in graph.capability_registry.names()
    assert "package_cards" in graph.capability_registry.names()
    assert graph.last_run_state["runtime_plan"].workflow_type == "anki_generation"
    assert "generate_image" in graph.last_run_state["runtime_plan"].capability_names
    assert graph.capability_registry.get("generate_image")[0].side_effects == ["external_call", "local_write"]
    assert graph.capability_registry.get("package_cards")[0].side_effects == ["local_write"]
    bundle_path = graph.last_observation_bundle_path()
    assert bundle_path
    bundle_dir = Path(bundle_path)
    reader = ObservationReader(bundle_dir)
    trace_events = list(reader.iter_trace_events())
    detail_envelopes = list(reader.iter_detail_envelopes())
    usage_events = list(reader.iter_usage_events())
    assert reader.meta.bundle_schema_version == 4
    assert reader.meta.status == "completed"
    reader.validate_record_counts(
        trace_count=len(trace_events),
        detail_count=len(detail_envelopes),
        usage_count=len(usage_events),
    )
    assert reader.read_definition().workflow_id == "anki_generation"
    assert (bundle_dir / reader.meta.artifact_manifest_path).exists()
    viewer = JsonlObservationViewer.from_run_bundle(bundle_path, title="Anki generation observation")
    run = viewer.source.read()
    sequences = [record.sequence for record in run.records if record.sequence is not None]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    identities = [(record.type, record.event_id) for record in run.records if record.event_id]
    assert len(identities) == len(set(identities))
    assert any(event.node == "parse_directives" and event.decision == "accepted" for event in run.trace_events)
    assert any(event.node == "package_cards" and event.decision == "accepted" for event in run.trace_events)
    observation = build_observation_graph(run.definition, run.trace_events, run.usage_events, run.details, run_id=run.run_id)
    assert observation.workflow_id == "anki_generation"
    assert observation.run_id
    assert observation.nodes["package_cards"].status == "completed"
    # Product-configured sinks are honored ALONGSIDE the engine-owned bundle now.
    assert details.details
    assert observation.details
    assert all(ref in observation.details for event in observation.timeline for ref in event.detail_refs)
    detail_text = "\n".join(
        reader.read_body_bytes(detail, max_bytes=1_000_000).decode("utf-8")
        for detail in run.details
    )
    assert "Valve diagram" in detail_text
    assert "data:image" not in detail_text
    assert "base64," not in detail_text
    html = viewer.html()
    assert "Anki generation observation" in html
    assert "flowchart TD" in html
    assert "Investigation Graph" in html
    assert "Timeline" in html
    assert "Parse message directives" in html
    assert "Choose card type branch" in html
    assert "package_cards" in html
    assert "tool_payload" in html
    assert "Valve diagram" in html
    assert "Copy raw" in html


@pytest.mark.unit
def test_observation_config_is_engine_owned_and_per_run(tmp_path):
    svc = Mock()
    graph = AnkiGenerationGraph(
        svc,
        capture_observation_detail_text=True,
        observation_bundle_dir=str(tmp_path / "observations"),
    )

    engine_1 = graph._engine()
    engine_2 = graph._engine()

    assert engine_1 is not engine_2
    assert engine_1.executor.runner is not engine_2.executor.runner
    # Observation is CONFIG on the engine — no bundle plumbing in product code.
    assert engine_1.observation.enabled
    assert engine_1.observation.bundle_dir == str(tmp_path / "observations")
    assert engine_1.observation.capture == "full"


@pytest.mark.unit
async def test_observation_bundle_is_finalized_when_engine_run_fails(tmp_path):
    svc = Mock()
    source = build_content_source([("U", "Valve diagram")], user_id=10, owner_name="U")
    graph = AnkiGenerationGraph(
        svc,
        runner=FailingRunner(),
        observation_bundle_dir=str(tmp_path / "observations"),
    )

    with pytest.raises(RuntimeError, match="runner exploded"):
        await graph.run(source)

    bundle_path = graph.last_observation_bundle_path()
    assert bundle_path
    viewer = JsonlObservationViewer.from_run_bundle(bundle_path, title="Failed Anki observation")
    assert viewer.source.read().meta.status == "failed"  # typed v2 meta (M10)


@pytest.mark.unit
def test_anki_workflow_definition_owns_node_and_transition_descriptions():
    definition = AnkiGenerationGraph(Mock())._anki_workflow_definition()

    assert definition.validate_graph() == []
    node = definition.node("parse_directives")
    assert node.title == "Parse message directives"
    assert node.description.startswith("Parse message directives")
    branch = definition.node("route_card_kind")
    assert branch.title == "Choose card type branch"
    assert branch.description.startswith("Choose card type branch")
    assert branch.branches == {
        "basic": "prepare_text_scenario",
        "cloze": "prepare_cloze_scenario",
        "visual_basic": "prepare_visual_scenario",
    }

    transition = next(
        item
        for item in definition.transitions
        if item.source == "route_card_kind" and item.label == "basic"
    )
    assert transition.description == "Use ordinary front/back cards."


@pytest.mark.unit
async def test_graph_routes_cloze_directive_to_cloze_render():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(type="cloze", text="Paris is in {{c1::France}}.")]
    source = build_content_source([("U", "[i cloze] Paris is in France")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(svc).run(source)

    assert rendered.cards[0].type == "cloze"
    assert svc.extract_cards.call_args.args[4] == "cloze"
    assert svc.extract_cards.call_args.args[0] == "U: Paris is in France"


@pytest.mark.unit
async def test_graph_uses_ai_card_set_planner_when_type_is_auto():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(type="cloze", text="Paris is in {{c1::France}}.")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="cloze",
            image_policy="none",
            source_facts=["Paris is in France"],
            study_goal="Memorize the country containing Paris",
            fallback_kind="basic",
        )
    )
    source = build_content_source([("U", "Paris is in France")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(svc, card_set_planner=planner).run(source)

    assert rendered.cards[0].type == "cloze"
    assert len(planner.calls) == 1
    assert svc.extract_cards.call_args.args[4] == "cloze"


@pytest.mark.unit
async def test_graph_passes_directive_cleaned_source_to_ai_planners():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What controls flow?", answer="A valve.")]
    type_planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="basic",
            image_policy="none",
            source_facts=["A valve controls flow"],
            study_goal="Ask what controls flow",
            fallback_kind="basic",
        )
    )
    scenario_planner = FakeScenarioPlanner(
        TextCardScenario(
            source_content="A valve controls flow.",
            rendering_guide="Ask what controls flow.",
            facts_to_test=["A valve controls flow"],
        )
    )
    source = build_content_source(
        [("U", "Valve controls fluid flow [i q1] make it concise[i]")],
        user_id=10,
        owner_name="U",
    )

    await AnkiGenerationGraph(
        svc,
        card_set_planner=type_planner,
        text_scenario_planner=scenario_planner,
    ).run(source)

    type_source = type_planner.calls[0][0]
    scenario_source = scenario_planner.calls[0][0]
    assert type_source.content == "U: Valve controls fluid flow"
    assert scenario_source.content == "U: Valve controls fluid flow"
    assert scenario_planner.calls[0][1].guide == "make it concise"


@pytest.mark.unit
async def test_graph_uses_ai_planned_count_when_user_did_not_force_count():
    svc = Mock()
    svc.extract_cards.return_value = [
        AnkiCard(question="Q1", answer="A1"),
        AnkiCard(question="Q2", answer="A2"),
    ]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="basic",
            image_policy="none",
            count=2,
            source_facts=["Fact one", "Fact two"],
            study_goal="Create two focused cards",
            fallback_kind="basic",
        )
    )
    source = build_content_source([("U", "Fact one. Fact two.")], user_id=10, owner_name="U")

    await AnkiGenerationGraph(svc, card_set_planner=planner).run(source)

    assert svc.extract_cards.call_args.args[2] == "count"
    assert svc.extract_cards.call_args.args[3] == 2
    assert svc.extract_cards.call_args.args[4] == "basic"


@pytest.mark.unit
def test_graph_trace_details_expose_count_and_coverage_preview():
    plan_details = AnkiGenerationGraph._trace_details_for_node(
        "plan_card_type",
        {
            "build_plan": CardBuildPlan(
                card_kind="basic",
                image_policy="none",
                count=1,
                source_facts=[
                    "Annex 1 is Personnel Licensing.",
                    "Annex 2 is Rules of the Air.",
                    "Annex 3 is Meteorological Service.",
                    "Annex 4 is Aeronautical Charts.",
                ],
                study_goal="Remember the ICAO annex list as one set",
                fallback_kind="basic",
            )
        },
    )
    rendered_details = AnkiGenerationGraph._trace_details_for_node(
        "render_text_or_cloze",
        {
            "rendered": Mock(
                cards=[
                    AnkiCard(
                        question="Which ICAO annexes are in this set?",
                        answer="Annex 1: Personnel Licensing; Annex 2: Rules of the Air.",
                    )
                ],
                fallback_used=False,
                fallback_reason=None,
            )
        },
    )

    assert plan_details["count"] == 1
    assert plan_details["source_facts_count"] == 4
    assert len(plan_details["source_facts_preview"]) == 3
    assert "ICAO annex list" in plan_details["study_goal"]
    assert rendered_details["card_count"] == 1
    assert rendered_details["cards_preview"][0]["question"] == "Which ICAO annexes are in this set?"


@pytest.mark.unit
async def test_graph_clamps_ai_planned_count_without_user_override():
    svc = Mock()
    svc.extract_cards.return_value = [
        AnkiCard(question="Q1", answer="A1"),
        AnkiCard(question="Q2", answer="A2"),
        AnkiCard(question="Q3", answer="A3"),
    ]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="basic",
            image_policy="none",
            count=5,
            source_facts=["Fact one", "Fact two", "Fact three", "Fact four", "Fact five"],
            study_goal="Planner over-requested cards",
            fallback_kind="basic",
        )
    )
    source = build_content_source([("U", "Fact one. Fact two. Fact three. Fact four. Fact five.")], user_id=10, owner_name="U")

    await AnkiGenerationGraph(svc, card_set_planner=planner).run(source)

    assert svc.extract_cards.call_args.args[2] == "count"
    assert svc.extract_cards.call_args.args[3] == 3


@pytest.mark.unit
async def test_graph_uses_text_scenario_planner_output():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What controls flow?", answer="A valve.")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="basic",
            image_policy="none",
            source_facts=["A valve controls flow"],
            study_goal="Ask what controls flow",
            fallback_kind="basic",
        )
    )
    scenario_planner = FakeScenarioPlanner(
        TextCardScenario(
            source_content="A valve controls flow.",
            rendering_guide="Ask what controls flow; do not reveal valve on the front.",
            facts_to_test=["A valve controls flow"],
            strategy="split",
            count=1,
        )
    )
    source = build_content_source([("U", "Valve controls fluid flow")], user_id=10, owner_name="U")

    await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        text_scenario_planner=scenario_planner,
    ).run(source)

    assert len(scenario_planner.calls) == 1
    assert svc.extract_cards.call_args.args[0] == "A valve controls flow."
    assert "do not reveal valve" in svc.extract_cards.call_args.args[1]


@pytest.mark.unit
async def test_graph_quality_evaluator_repairs_render_once():
    svc = Mock()
    svc.extract_cards.side_effect = [
        [AnkiCard(question="Valve?", answer="A valve controls flow.")],
        [AnkiCard(question="What controls flow?", answer="A valve.")],
    ]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="basic",
            image_policy="none",
            source_facts=["A valve controls flow"],
            study_goal="Ask what controls flow",
            fallback_kind="basic",
        )
    )
    scenario_planner = FakeScenarioPlanner(
        TextCardScenario(
            source_content="A valve controls flow.",
            rendering_guide="Ask what controls flow.",
            facts_to_test=["A valve controls flow"],
        )
    )
    quality = FakeQualityEvaluator(
        [
            RenderedCardEvaluation(
                accepted=False,
                issues=["front is too vague"],
                severity="medium",
                repair_strategy="repair_render",
                guidance="Make the front self-contained.",
            ),
            RenderedCardEvaluation(accepted=True),
        ]
    )
    source = build_content_source([("U", "A valve controls flow.")], user_id=10, owner_name="U")

    graph = AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        text_scenario_planner=scenario_planner,
        quality_evaluator=quality,
    )

    rendered = await graph.run(source)

    assert rendered.cards[0].question == "What controls flow?"
    assert len(quality.calls) == 2
    assert svc.extract_cards.call_count == 2
    assert "Quality repair" in svc.extract_cards.call_args.args[1]
    assert any(
        event.node == "evaluate_rendered_cards"
        and event.metadata.get("decision_action") == "repair"
        and event.metadata.get("target_capability") == "repair_rendered_cards"
        for event in graph.last_run_state["trace"]
    )


@pytest.mark.unit
async def test_graph_quality_repair_cap_falls_back():
    svc = Mock()
    svc.extract_cards.side_effect = [
        [AnkiCard(question="Valve?", answer="A valve controls flow.")],
        [AnkiCard(question="What controls flow?", answer="A valve.")],
    ]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="basic",
            image_policy="none",
            source_facts=["A valve controls flow"],
            study_goal="Ask what controls flow",
            fallback_kind="basic",
        )
    )
    quality = FakeQualityEvaluator(
        [
            RenderedCardEvaluation(
                accepted=False,
                issues=["bad"],
                severity="high",
                repair_strategy="repair_render",
                guidance="Repair it.",
            )
        ]
    )
    source = build_content_source([("U", "A valve controls flow.")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        quality_evaluator=quality,
        max_quality_repairs_per_run=0,
    ).run(source)

    assert rendered.fallback_used is True
    assert len(quality.calls) == 1
    assert svc.extract_cards.call_count == 2


@pytest.mark.unit
async def test_graph_quality_retry_card_plan_replans_with_feedback():
    svc = Mock()
    svc.extract_cards.side_effect = [
        [AnkiCard(question="Which annex matters?", answer="Annex 1.")],
        [
            AnkiCard(question="Which ICAO annexes cover people and rules?", answer="Annex 1 and Annex 2."),
            AnkiCard(question="Which ICAO annexes cover weather and charts?", answer="Annex 3 and Annex 4."),
        ],
    ]
    planner = FakeSequentialCardSetPlanner(
        [
            CardBuildPlan(
                card_kind="basic",
                image_policy="none",
                count=1,
                source_facts=["Annex 1 is Personnel Licensing."],
                study_goal="Remember ICAO annexes",
                fallback_kind="basic",
            ),
            CardBuildPlan(
                card_kind="basic",
                image_policy="none",
                count=2,
                source_facts=[
                    "Annex 1 is Personnel Licensing.",
                    "Annex 2 is Rules of the Air.",
                    "Annex 3 is Meteorological Service.",
                    "Annex 4 is Aeronautical Charts.",
                ],
                study_goal="Remember ICAO annexes in two logical groups",
                fallback_kind="basic",
            ),
        ]
    )
    scenario_planner = FakeScenarioPlanner(
        TextCardScenario(
            source_content=(
                "Annex 1 - Personnel Licensing. Annex 2 - Rules of the Air. "
                "Annex 3 - Meteorological Service. Annex 4 - Aeronautical Charts."
            ),
            rendering_guide="Create grouped recall cards for the annex set.",
            facts_to_test=[
                "Annex 1 is Personnel Licensing.",
                "Annex 2 is Rules of the Air.",
                "Annex 3 is Meteorological Service.",
                "Annex 4 is Aeronautical Charts.",
            ],
        )
    )
    quality = FakeQualityEvaluator(
        [
            RenderedCardEvaluation(
                accepted=False,
                issues=["rendered card sampled one annex and dropped the sibling set"],
                severity="high",
                repair_strategy="retry_card_plan",
                guidance="Replan the target count/source_facts: cover all four annexes, split into two groups if needed.",
            ),
            RenderedCardEvaluation(accepted=True),
        ]
    )
    source = build_content_source(
        [
            (
                "U",
                (
                    "ICAO Annexes: Annex 1 Personnel Licensing; Annex 2 Rules of the Air; "
                    "Annex 3 Meteorological Service; Annex 4 Aeronautical Charts."
                ),
            )
        ],
        user_id=10,
        owner_name="U",
    )

    graph = AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        text_scenario_planner=scenario_planner,
        quality_evaluator=quality,
    )

    rendered = await graph.run(source)

    assert len(planner.calls) == 2
    assert "Quality feedback for retry" in planner.calls[1][1].guide
    assert "cover all four annexes" in planner.calls[1][1].guide
    assert len(scenario_planner.calls) == 2
    assert svc.extract_cards.call_count == 2
    assert svc.extract_cards.call_args.args[3] == 2
    assert len(rendered.cards) == 2
    assert any(
        event.node == "evaluate_rendered_cards"
        and event.metadata.get("decision_action") == "retrace_to"
        and event.metadata.get("retrace_to") == "plan_card_type"
        for event in graph.last_run_state["trace"]
    )


@pytest.mark.unit
async def test_graph_uses_cloze_scenario_planner_targets():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(type="cloze", text="The pump exports {{c1::three Na+}}.")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="cloze",
            image_policy="none",
            source_facts=["The pump exports three Na+"],
            study_goal="Memorize pump direction/count",
            fallback_kind="basic",
        )
    )
    scenario_planner = FakeScenarioPlanner(
        ClozeCardScenario(
            source_content="The pump exports three Na+.",
            rendering_guide="Hide the exported ion count.",
            cloze_targets=["three Na+"],
            max_deletions=1,
            count=1,
        )
    )
    source = build_content_source([("U", "The pump exports three Na+.")], user_id=10, owner_name="U")

    await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        cloze_scenario_planner=scenario_planner,
    ).run(source)

    assert "Cloze targets to hide: three Na+" in svc.extract_cards.call_args.args[1]
    assert "Use no more than 1" in svc.extract_cards.call_args.args[1]
    assert svc.extract_cards.call_args.args[4] == "cloze"


@pytest.mark.unit
async def test_graph_user_count_constraint_overrides_ai_planned_count():
    svc = Mock()
    svc.extract_cards.return_value = [
        AnkiCard(question="Q1", answer="A1"),
        AnkiCard(question="Q2", answer="A2"),
        AnkiCard(question="Q3", answer="A3"),
    ]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="basic",
            image_policy="none",
            count=1,
            source_facts=["Fact one", "Fact two", "Fact three"],
            study_goal="Planner wants one card, but user requested three",
            fallback_kind="basic",
        )
    )
    source = build_content_source([("U", "[i q3] Fact one. Fact two. Fact three.")], user_id=10, owner_name="U")

    await AnkiGenerationGraph(svc, card_set_planner=planner).run(source)

    assert svc.extract_cards.call_args.args[2] == "count"
    assert svc.extract_cards.call_args.args[3] == 3


@pytest.mark.unit
async def test_graph_forced_card_type_constrains_ai_type_planner():
    svc = Mock()
    svc.extract_cards.return_value = [
        AnkiCard(type="cloze", text="Paris is in {{c1::France}}."),
        AnkiCard(type="cloze", text="France contains {{c1::Paris}}."),
    ]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="basic",
            image_policy="none",
            count=2,
            study_goal="Planner suggested basic, but user forced cloze",
            fallback_kind="basic",
        )
    )
    source = build_content_source([("U", "[i cloze] Paris is in France")], user_id=10, owner_name="U")

    await AnkiGenerationGraph(svc, card_set_planner=planner).run(source)

    assert len(planner.calls) == 1
    assert svc.extract_cards.call_args.args[4] == "cloze"
    assert svc.extract_cards.call_args.args[3] == 2


@pytest.mark.unit
async def test_graph_forced_cloze_can_still_reuse_uploaded_image():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(type="cloze", text="The valve controls {{c1::flow}}.")]
    source = build_content_source(
        [("U", "[i cloze] valve controls flow", {"image_data": b"img", "file_id": "f1"})],
        user_id=10,
        owner_name="U",
    )

    rendered = await AnkiGenerationGraph(svc).run(source)

    assert rendered.image_asset_plan.image_role == "reuse_user_image"
    assert rendered.image_asset_plan.candidate_back_images == [0]
    assert svc.extract_cards.call_args.args[4] == "cloze"


@pytest.mark.unit
async def test_graph_ai_planner_can_ignore_uploaded_image_media():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="basic",
            image_policy="none",
            source_facts=["The image summary mentions a valve"],
            study_goal="Ask about the valve fact without embedding the screenshot",
            fallback_kind="basic",
        )
    )
    source = build_content_source(
        [("U", "[SCREENSHOT DESCRIPTION] valve diagram", {"image_data": b"img", "file_id": "f1"})],
        user_id=10,
        owner_name="U",
    )

    rendered = await AnkiGenerationGraph(svc, card_set_planner=planner).run(source)

    assert rendered.image_asset_plan.image_role == "source_content_only"
    assert rendered.image_asset_plan.candidate_back_images == []
    assert svc.extract_cards.call_args.args[4] == "basic"


@pytest.mark.unit
async def test_graph_ai_visual_choice_generates_when_possible(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["The valve controls flow"],
            study_goal="Remember the valve visually",
            visual_rationale="A simple valve illustration is memorable and feasible",
            fallback_kind="basic",
        )
    )
    generator = FakeImageGenerator()
    source = build_content_source([("U", "Valve controls fluid flow")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        image_generator=generator,
        enable_image_generation=True,
        enable_auto_image_generation=True,
        generated_media_root=str(tmp_path),
    ).run(source)

    assert len(generator.requests) == 1
    assert rendered.image_asset_plan.image_role == "generate_new_visual"
    assert rendered.generated_media[0].source == "generated"


@pytest.mark.unit
async def test_graph_propagates_private_image_identity_and_safe_evidence_to_bundle(tmp_path):
    import json
    from pathlib import Path

    from services.content.anki_media_contract import continuity_name

    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["The valve controls flow"],
            study_goal="Remember the valve visually",
            visual_rationale="A simple valve illustration is useful",
            fallback_kind="basic",
        )
    )
    evidence = ImageGenerationEvidence(
        conversation_mode="reuse",
        reference_images_used=0,
        generation=3,
        reused=True,
        rotated=False,
        freshness=ImageFreshnessEvidence(
            schema=4,
            strategy="resultFreshnessFenceV3",
            source="captured",
            state="result_observed",
            anchor_owned=True,
            result_owned=True,
            result_scope_count=1,
            candidate_count=2,
            reason=None,
        ),
    )
    generator = FakeImageGenerator(generation_evidence=evidence)
    source = build_content_source(
        [("U", "Valve controls fluid flow")],
        user_id=987654321,
        owner_name="U",
    )
    graph = AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        image_generator=generator,
        enable_image_generation=True,
        enable_auto_image_generation=True,
        generated_media_root=str(tmp_path / "media"),
        style_reference_version="ppla-split-v3",
        observation_bundle_dir=str(tmp_path / "observations"),
    )

    rendered = await graph.run(source)

    request = generator.requests[0]
    assert request.continuity_key == continuity_name(
        user_id=987654321,
        style_version="ppla-split-v3",
    )
    assert "987654321" not in request.continuity_key
    assert request.idempotency_key.startswith("anki-image-")
    assert request.workflow_id not in request.idempotency_key

    metadata = rendered.generated_media[0].metadata
    assert metadata["generation_conversation_mode"] == "reuse"
    assert metadata["generation_generation"] == "3"
    assert metadata["generation_reused"] == "true"
    freshness = json.loads(metadata["generation_freshness"])
    assert freshness["strategy"] == "resultFreshnessFenceV3"
    assert freshness["candidate_count"] == 2

    generated_trace = next(
        event for event in graph.last_run_state["trace"] if event.node == "generate_image"
    )
    assert generated_trace.metadata["generated_media"] == metadata

    bundle = Path(graph.last_observation_bundle_path())
    reader = ObservationReader(bundle)
    manifest = json.loads(
        (bundle / reader.meta.artifact_manifest_path).read_text(encoding="utf-8")
    )
    generated = next(item for item in manifest if item["owner_node"] == "generate_image")
    assert generated["copied"] is True
    assert generated["metadata"]["generation_conversation_mode"] == "reuse"
    durable_text = "\n".join(
        [
            *(event.model_dump_json() for event in reader.iter_trace_events()),
            *(detail.model_dump_json() for detail in reader.iter_detail_envelopes()),
            *(usage.model_dump_json() for usage in reader.iter_usage_events()),
            json.dumps(manifest),
        ]
    )
    assert "source_url" not in durable_text
    assert "CHATGPT_BROWSER_API_TOKEN" not in durable_text


@pytest.mark.unit
async def test_graph_bundle_redacts_browser_token_from_failed_image_call(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from ai_workflow_tools.media.image_generation import ChatGptBrowserImageGenerator

    secret = "browser-secret-must-never-persist"

    class _Unauthorized:
        status_code = 401
        text = f"authorization failed for {secret} at https://provider.invalid/signed/private"

        @staticmethod
        def json():
            return {
                "detail": (
                    f"authorization failed for {secret} at "
                    "https://provider.invalid/signed/private"
                )
            }

    def post(*_args, **_kwargs):
        return _Unauthorized()

    config = SimpleNamespace(
        WORKFLOW_CHATGPT_BROWSER_URL="https://browser.example",
        WORKFLOW_CHATGPT_BROWSER_TOKEN=secret,
        WORKFLOW_CHATGPT_BROWSER_TIMEOUT_SECONDS=5,
        WORKFLOW_CHATGPT_BROWSER_RATE_WAIT_MAX_SECONDS=1,
        WORKFLOW_CHATGPT_BROWSER_CONVERSATION_MODE="reuse",
        WORKFLOW_USAGE_TRACKING_ENABLED=False,
    )
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What controls flow?", answer="A valve")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["A valve controls flow"],
            study_goal="Remember the valve",
            visual_rationale="A diagram is useful",
            fallback_kind="basic",
        )
    )
    graph = AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        image_generator=ChatGptBrowserImageGenerator(config, http_post=post),
        enable_image_generation=True,
        enable_auto_image_generation=True,
        generated_media_root=str(tmp_path / "media"),
        observation_bundle_dir=str(tmp_path / "observations"),
    )
    source = build_content_source([("U", "Valve controls fluid flow")], user_id=7, owner_name="U")

    rendered = await graph.run(source)

    assert rendered.fallback_used is True
    bundle = Path(graph.last_observation_bundle_path())
    durable_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in bundle.iterdir()
        if path.is_file()
    )
    assert secret not in durable_text
    assert "provider.invalid/signed" not in durable_text
    assert "[REDACTED]" in durable_text
    assert "[REDACTED_URL]" in durable_text


@pytest.mark.unit
async def test_graph_image_generation_cap_prevents_provider_call(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="Q", answer="A")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["The valve controls flow"],
            study_goal="Remember the valve visually",
            visual_rationale="A visual might help",
            fallback_kind="basic",
        )
    )
    generator = FakeImageGenerator()
    source = build_content_source([("U", "Valve controls fluid flow")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        image_generator=generator,
        enable_image_generation=True,
        enable_auto_image_generation=True,
        max_image_generations_per_run=0,
        generated_media_root=str(tmp_path),
    ).run(source)

    assert generator.requests == []
    assert rendered.fallback_used is True
    assert rendered.generated_media == []


@pytest.mark.unit
async def test_graph_marks_planned_visual_downgrade_without_quality_retry():
    svc = Mock()
    svc.extract_cards.return_value = [
        AnkiCard(
            question="What prevails when national Air Law conflicts with ICAO Air Law?",
            answer="The individual state's Air Law.",
        )
    ]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["National Air Law prevails over ICAO Air Law within the state."],
            study_goal="Remember state Air Law precedence",
            visual_rationale="Visual requested",
            fallback_kind="basic",
        )
    )
    quality = FakeQualityEvaluator(
        [
            RenderedCardEvaluation(
                accepted=False,
                issues=["would incorrectly reject the planned fallback"],
                repair_strategy="retry_scenario",
            )
        ]
    )
    source = build_content_source(
        [("U", "National Air Law prevails over ICAO Air Law within a sovereign state. [i gen q1]")],
        user_id=10,
        owner_name="U",
    )

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        quality_evaluator=quality,
        enable_image_generation=False,
    ).run(source)

    assert rendered.fallback_used is True
    assert rendered.fallback_reason == "image generation is disabled"
    assert rendered.generated_media == []
    assert quality.calls == []
    svc.extract_cards.assert_called_once()


@pytest.mark.unit
async def test_forced_generated_visual_does_not_downgrade_to_planner_cloze_fallback():
    svc = Mock()
    svc.extract_cards.return_value = [
        AnkiCard(
            question="Which Air Law prevails when national law conflicts with ICAO Air Law?",
            answer="The individual state's Air Law.",
        )
    ]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="cloze",
            image_policy="generate",
            count=1,
            source_facts=["The individual state's Air Law prevails over ICAO Air Law."],
            study_goal="Remember Air Law precedence",
            fallback_kind="cloze",
        )
    )
    source = build_content_source(
        [("U", "The Air Law of the individual state prevails over International (ICAO) Air Law. [i gen q1]")],
        user_id=10,
        owner_name="U",
    )

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        enable_image_generation=False,
    ).run(source)

    assert rendered.cards[0].type == "basic"
    assert rendered.fallback_used is True
    assert rendered.fallback_reason == "image generation is disabled"
    assert svc.extract_cards.call_args.args[3] == 1
    assert svc.extract_cards.call_args.args[4] == "basic"


@pytest.mark.unit
async def test_explicit_gen_combines_overtaking_facts_into_one_visual_card(tmp_path):
    """The real 2026-08-25 failure: the generic planner proposed two cards even though
    explicit generated-visual mode has one paid image slot and one visual scenario."""

    svc = Mock()
    facts = [
        (
            "The overtaking aircraft, whether climbing, descending or in horizontal flight, "
            "must keep out of the way by altering its heading to the right."
        ),
        (
            "The overtaking aircraft must not pass over, under or in front of the other "
            "aircraft unless well clear."
        ),
    ]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            count=2,
            source_facts=facts,
            study_goal="Learn both parts of the overtaking right-of-way rule",
            visual_rationale="The allowed and prohibited paths form one visual rule",
            fallback_kind="basic",
        )
    )
    visual_planner = FakeScenarioPlanner(
        VisualCardScenario(
            source_content=" ".join(facts),
            question_text="What must an overtaking aircraft do, and which paths must it avoid?",
            facts_to_test=facts,
            visual_prompt=(
                "Show the correct rightward overtaking path in green; cross out passing over, "
                "under, or in front in red; preserve the unless-well-clear qualification."
            ),
            layout="text_front_image_back",
            image_count=1,
            answer_text=(
                "Alter heading to the right; do not pass over, under, or in front unless well clear."
            ),
            rendering_guide="One card must cover both complementary parts of the overtaking rule.",
        )
    )
    generator = FakeImageGenerator()
    quality = FakeQualityEvaluator([RenderedCardEvaluation(accepted=True)])
    source = build_content_source(
        [
            (
                "U",
                (
                    "An overtaking aircraft must alter heading right to keep clear and must not "
                    "pass over, under, or in front unless well clear. [i gen]"
                ),
            )
        ],
        user_id=10,
        owner_name="U",
    )

    graph = AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        visual_scenario_planner=visual_planner,
        image_generator=generator,
        quality_evaluator=quality,
        enable_image_generation=True,
        generated_media_root=str(tmp_path),
    )
    rendered = await graph.run(source)

    normalized = graph.last_run_state["build_plan"]
    assert normalized.card_kind == "visual_basic"
    assert normalized.count == 1
    assert normalized.source_facts == facts
    assert "explicit_generated_visual_single_card" in normalized.user_constraints_applied
    assert len(generator.requests) == 1
    assert len(rendered.cards) == 1
    assert rendered.fallback_used is False
    assert "heading to the right" in rendered.cards[0].answer
    assert "over, under, or in front" in rendered.cards[0].answer
    assert "rightward overtaking path in green" in generator.requests[0].prompt
    assert "cross out passing over, under, or in front in red" in generator.requests[0].prompt
    svc.extract_cards.assert_not_called()


@pytest.mark.unit
async def test_graph_uses_visual_scenario_planner_prompt_and_rendering_guide(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="Why does a wing lift?", answer="Pressure difference.")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["Wing lift comes from pressure difference"],
            study_goal="Remember lift visually",
            visual_rationale="A diagram helps",
            fallback_kind="basic",
        )
    )
    visual_planner = FakeScenarioPlanner(
        VisualCardScenario(
            source_content="Faster upper airflow lowers pressure above the wing.",
            question_text="Why does a wing generate lift?",
            front_intent="Ask why a wing generates lift.",
            back_intent="Explain pressure difference.",
            facts_to_test=["Faster upper airflow lowers pressure"],
            visual_prompt="Clean airfoil diagram with flow arrows; no text.",
            layout="text_front_image_back",
            image_count=1,
            answer_text="Faster upper airflow lowers pressure above the wing.",
            rendering_guide="Ask why the wing generates lift; put diagram on the back.",
        )
    )
    generator = FakeImageGenerator()
    source = build_content_source([("U", "Airplane wing lift")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        visual_scenario_planner=visual_planner,
        image_generator=generator,
        enable_image_generation=True,
        enable_auto_image_generation=True,
        generated_media_root=str(tmp_path),
    ).run(source)

    assert "PPLA deck visual style:" in generator.requests[0].prompt
    assert "Top priorities: explain the exact answer relationship" in generator.requests[0].prompt
    assert "Clean airfoil diagram with flow arrows; no text." in generator.requests[0].prompt
    assert "The recurring high-wing trainer aircraft is a reusable deck character" in generator.requests[0].prompt
    assert "Do not include it by default" in generator.requests[0].prompt
    assert "Prefer humor from the studied concept itself" in generator.requests[0].prompt
    assert "broken propellers" in generator.requests[0].prompt
    assert "centered translucent spinning propeller disk" in generator.requests[0].prompt
    assert "exactly one centered two-blade propeller" in generator.requests[0].prompt
    assert "crop or omit the nose/aircraft" in generator.requests[0].prompt
    assert "pseudo-real institutional logos" in generator.requests[0].prompt
    assert "shields, crests, globe marks" in generator.requests[0].prompt
    assert "plain covers" in generator.requests[0].prompt
    assert "typography/callout cards" in generator.requests[0].prompt
    assert "Default tone is catchy, witty, memorable, and lightly humorous" in generator.requests[0].prompt
    assert "Factual accuracy, source grounding, exact labels" in generator.requests[0].prompt
    assert "visually encode the answer relationship" in generator.requests[0].prompt
    assert "only repeats the question term" in generator.requests[0].prompt
    assert "Respect the requested composition style" in generator.requests[0].prompt
    assert "funny story scene" in generator.requests[0].prompt
    assert "Do not flatten a requested humorous/collage/story concept" in generator.requests[0].prompt
    assert "make memorability a primary design constraint" in generator.requests[0].prompt
    assert "A smiling mascot, tidy tile grid, or generic icon set alone does not satisfy" in generator.requests[0].prompt
    assert "Do not hard-code, invent, genericize, or optimize item names" in generator.requests[0].prompt
    assert "Do not abbreviate source labels" in generator.requests[0].prompt
    assert "pair the abbreviation with its plain-English meaning" in generator.requests[0].prompt
    assert "do not invent unofficial acronym expansions" in generator.requests[0].prompt
    assert "full answer text inside the image" in generator.requests[0].prompt
    assert "conditional items" in generator.requests[0].prompt
    assert "misleading counts" in generator.requests[0].prompt
    assert "Do not invent numeric values" in generator.requests[0].prompt
    assert "impossible aviation units" in generator.requests[0].prompt
    assert "source/scenario supplied no numeric values" in generator.requests[0].prompt
    assert "Provider-specific image API guidance for OpenAI" in generator.requests[0].prompt
    assert "Images edit endpoint" in generator.requests[0].prompt
    assert "Generated card role: back side." in generator.requests[0].prompt
    assert "Reference image policy: none" in generator.requests[0].prompt
    assert rendered.generated_media[0].role == "back"
    assert rendered.generated_media[0].metadata["prompt_policy_provider"] == "openai"
    assert rendered.cards[0].question == "Why does a wing generate lift?"
    assert "Faster upper airflow lowers pressure above the wing." in rendered.cards[0].answer
    svc.extract_cards.assert_not_called()


@pytest.mark.unit
async def test_graph_generated_media_role_follows_visual_layout(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["A valve controls flow"],
            study_goal="Use the image as recall cue",
            fallback_kind="basic",
        )
    )
    visual_planner = FakeScenarioPlanner(
        VisualCardScenario(
            source_content="A valve controls flow.",
            question_text="What controls flow?",
            visual_prompt="Simple valve illustration; no text.",
            layout="image_front_text_back",
            image_count=1,
            answer_text="A valve controls flow.",
            rendering_guide="Put the generated image on the front.",
        )
    )
    source = build_content_source([("U", "A valve controls flow")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        visual_scenario_planner=visual_planner,
        image_generator=FakeImageGenerator(),
        enable_image_generation=True,
        enable_auto_image_generation=True,
        generated_media_root=str(tmp_path),
    ).run(source)

    assert rendered.generated_media[0].role == "front"
    assert '<img src="' in rendered.cards[0].question


@pytest.mark.unit
async def test_graph_ai_visual_choice_downgrades_when_generation_unavailable():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="Q", answer="A")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["The valve controls flow"],
            study_goal="Remember the valve visually",
            visual_rationale="A visual might help",
            fallback_kind="basic",
        )
    )
    source = build_content_source([("U", "Valve controls fluid flow")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(svc, card_set_planner=planner).run(source)

    assert rendered.image_asset_plan.image_role == "ignore_media"
    assert rendered.generated_media == []
    assert rendered.fallback_used is True
    assert rendered.fallback_reason == "image generation is disabled"
    assert svc.extract_cards.call_args.args[4] == "basic"


@pytest.mark.unit
async def test_graph_ai_visual_choice_does_not_auto_spend_without_auto_image_flag(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="Q", answer="A")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["The valve controls flow"],
            study_goal="Remember the valve visually",
            visual_rationale="A visual might help",
            fallback_kind="basic",
        )
    )
    generator = FakeImageGenerator()
    source = build_content_source([("U", "Valve controls fluid flow")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        image_generator=generator,
        enable_image_generation=True,
        generated_media_root=str(tmp_path),
    ).run(source)

    assert generator.requests == []
    assert rendered.fallback_used is True
    assert rendered.fallback_reason == "auto image generation is disabled"
    assert svc.extract_cards.call_args.args[4] == "basic"


@pytest.mark.unit
async def test_graph_visual_without_uploaded_image_downgrades_when_generator_disabled():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="Q", answer="A")]
    source = build_content_source([("U", "[i visual] abstract legal definition")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(svc).run(source)

    assert rendered.cards[0].question == "Q"
    assert rendered.image_asset_plan.image_role == "ignore_media"
    assert rendered.fallback_used is True
    assert rendered.fallback_reason == "image generation is disabled"
    assert svc.extract_cards.call_args.args[4] == "basic"


@pytest.mark.unit
async def test_graph_generates_visual_when_enabled(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    generator = FakeImageGenerator()
    source = build_content_source([("U", "[i gen] valve diagram")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        image_generator=generator,
        enable_image_generation=True,
        image_model="gpt-image-2",
        generated_media_root=str(tmp_path),
    ).run(source)

    assert len(generator.requests) == 1
    assert generator.requests[0].model == "gpt-image-2"
    assert rendered.generated_media[0].source == "generated"
    assert '<img src="' in rendered.cards[0].answer


@pytest.mark.unit
async def test_graph_language_voice_generates_visual_and_audio(tmp_path):
    svc = Mock()
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            count=1,
            source_facts=["привіт means olá"],
            study_goal="Learn Portuguese translation and pronunciation",
            visual_rationale="A visual mnemonic helps remember the greeting",
            fallback_kind="basic",
        )
    )
    visual_planner = FakeScenarioPlanner(
        VisualCardScenario(
            source_content="привіт",
            question_text="How do you say 'привіт' in Portuguese?",
            visual_prompt="Friendly greeting scene with one short Portuguese caption.",
            layout="text_front_image_back",
            image_count=1,
            answer_text="olá",
            voice_text="olá",
            rendering_guide="Back has Portuguese translation, image, and pronunciation audio.",
        )
    )
    image_generator = FakeImageGenerator()
    voice_generator = FakeVoiceGenerator()
    source = build_content_source([("U", "[i langvoice ukr->pt gen] привіт")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        visual_scenario_planner=visual_planner,
        image_generator=image_generator,
        voice_generator=voice_generator,
        enable_image_generation=True,
        enable_voice_generation=True,
        image_model="gpt-image-2",
        voice_model="eleven_multilingual_v2",
        generated_media_root=str(tmp_path),
    ).run(source)

    assert len(image_generator.requests) == 1
    assert len(voice_generator.requests) == 1
    assert voice_generator.requests[0].text == "olá"
    assert voice_generator.requests[0].metadata["target_language"] == "pt"
    assert rendered.cards[0].question == "How do you say 'привіт' in Portuguese?"
    assert "olá" in rendered.cards[0].answer
    assert '<img src="' in rendered.cards[0].answer
    assert "[sound:" in rendered.cards[0].answer
    assert [media.role for media in rendered.generated_media] == ["back", "audio"]


@pytest.mark.unit
async def test_graph_passes_instruction_block_guidance_to_visual_prompt(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    generator = FakeImageGenerator()
    source = build_content_source(
        [("U", "Valve controls fluid flow [i gen] make the image funny[i]")],
        user_id=10,
        owner_name="U",
    )

    await AnkiGenerationGraph(
        svc,
        image_generator=generator,
        enable_image_generation=True,
        image_model="gpt-image-2",
        generated_media_root=str(tmp_path),
    ).run(source)

    assert len(generator.requests) == 1
    assert "Valve controls fluid flow" in generator.requests[0].prompt
    assert "make the image funny" in generator.requests[0].prompt


@pytest.mark.unit
async def test_graph_treats_image_source_after_tag_text_as_guidance(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    generator = FakeImageGenerator()
    source = build_content_source(
        [("U", "[i gen] make it funny", {"image_data": b"img", "file_id": "f1"})],
        user_id=10,
        owner_name="U",
    )

    await AnkiGenerationGraph(
        svc,
        image_generator=generator,
        enable_image_generation=True,
        image_model="gpt-image-2",
        generated_media_root=str(tmp_path),
    ).run(source)

    assert len(generator.requests) == 1
    assert "Uploaded image source" in generator.requests[0].prompt
    assert "make it funny" in generator.requests[0].prompt
    assert "[i gen]" not in generator.requests[0].prompt
    assert generator.requests[0].reference_image_paths == []


@pytest.mark.unit
async def test_graph_uses_uploaded_image_as_generation_reference(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    generator = FakeImageGenerator()
    source = build_content_source(
        [("U", "[i ref] valve diagram", {"image_data": b"img", "file_id": "f1"})],
        user_id=10,
        owner_name="U",
    )

    rendered = await AnkiGenerationGraph(
        svc,
        image_generator=generator,
        enable_image_generation=True,
        generated_media_root=str(tmp_path),
    ).run(source)

    assert rendered.image_asset_plan.image_role == "use_as_reference"
    assert generator.requests[0].reference_image_paths
    assert rendered.generated_media[0].metadata["reference_image_count"] == "1"


@pytest.mark.unit
async def test_graph_passes_style_references_to_image_generator(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    character_reference = tmp_path / "character.png"
    design_reference = tmp_path / "design.png"
    character_reference.write_bytes(b"character")
    design_reference.write_bytes(b"design")
    generator = FakeImageGenerator()
    source = build_content_source(
        [("U", "[i gen] valve diagram")],
        user_id=10,
        owner_name="U",
    )

    rendered = await AnkiGenerationGraph(
        svc,
        image_generator=generator,
        enable_image_generation=True,
        style_reference_images=[str(character_reference), str(design_reference)],
        style_reference_version="study-split-v1",
        generated_media_root=str(tmp_path),
    ).run(source)

    assert generator.requests[0].reference_image_paths == [
        str(character_reference),
        str(design_reference),
    ]
    assert rendered.generated_media[0].metadata["reference_image_count"] == "2"
    assert rendered.generated_media[0].metadata["style_reference_version"] == "study-split-v1"


@pytest.mark.unit
async def test_graph_uses_gemini_provider_specific_image_prompt(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="Q", answer="A")]
    planner = FakeCardSetPlanner(
        CardBuildPlan(
            card_kind="visual_basic",
            image_policy="generate",
            source_facts=["QNH is local pressure setting"],
            study_goal="Remember QNH",
            fallback_kind="basic",
        )
    )
    visual_planner = FakeScenarioPlanner(
        VisualCardScenario(
            source_content="QNH is local pressure setting.",
            question_text="What is QNH?",
            visual_prompt="Show local pressure setting on an altimeter.",
            layout="text_front_image_back",
            image_count=1,
            answer_text="QNH is the local pressure setting.",
        )
    )
    generator = FakeImageGenerator()
    source = build_content_source([("U", "[i gen] QNH local pressure setting")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        card_set_planner=planner,
        visual_scenario_planner=visual_planner,
        image_generator=generator,
        enable_image_generation=True,
        image_provider="gemini",
        generated_media_root=str(tmp_path),
    ).run(source)

    assert "Provider-specific image API guidance for Gemini/Nano Banana" in generator.requests[0].prompt
    assert "Reference images are sent as inline image parts" in generator.requests[0].prompt
    assert "The text brief has priority over copied reference elements" in generator.requests[0].prompt
    assert "Gemini response-format sizing is not forced by default" in generator.requests[0].prompt
    assert "Be conservative with instrument faces and numeric labels" in generator.requests[0].prompt
    assert "Do not show any numbers, numeric readouts" in generator.requests[0].prompt
    assert rendered.generated_media[0].metadata["prompt_policy_provider"] == "gemini"


@pytest.mark.unit
async def test_graph_falls_back_when_image_generation_fails(tmp_path):
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="Q", answer="A")]
    source = build_content_source([("U", "[i gen] valve diagram")], user_id=10, owner_name="U")

    rendered = await AnkiGenerationGraph(
        svc,
        image_generator=FakeImageGenerator(fail=True),
        enable_image_generation=True,
        generated_media_root=str(tmp_path),
    ).run(source)

    assert rendered.fallback_used is True
    assert rendered.generated_media == []


@pytest.mark.unit
def test_zero_budget_config_means_no_cap_not_zero_spend():
    """Regression (now at the ONE app boundary, R1): config.py defaults
    WORKFLOW_MAX_ESTIMATED_USD_PER_RUN to 0 meaning "disabled" — the engine treats 0.0 as a
    hard zero-spend cap, so the boundary must translate 0 -> None or every metered call dies
    ("exceeded: $x > $0.000000")."""
    from config import engine_runtime_limits

    class _Cfg:
        WORKFLOW_MAX_ESTIMATED_USD_PER_RUN = 0

    assert engine_runtime_limits(_Cfg()).max_estimated_usd is None
    _Cfg.WORKFLOW_MAX_ESTIMATED_USD_PER_RUN = 0.0
    assert engine_runtime_limits(_Cfg()).max_estimated_usd is None
    _Cfg.WORKFLOW_MAX_ESTIMATED_USD_PER_RUN = 2.5
    assert engine_runtime_limits(_Cfg()).max_estimated_usd == 2.5
    _Cfg.WORKFLOW_MAX_ESTIMATED_USD_PER_RUN = ""
    assert engine_runtime_limits(_Cfg()).max_estimated_usd is None


@pytest.mark.unit
def test_engine_limits_cross_typed_boundary_with_zero_meaning_no_cap():
    """v0.9 typed-budget migration: every WORKFLOW_MAX_* ceiling crosses into typed
    RuntimeLimits via _engine_limits — app `0` (no cap) becomes typed None, real values
    pass through, and the engine reads ONLY the typed profile (no duck-read remains)."""
    from services.content.anki_generation_graph import AnkiGenerationGraph

    class _Cfg:
        WORKFLOW_MAX_TEXT_CALLS_PER_RUN = 16
        WORKFLOW_MAX_IMAGE_CALLS_PER_RUN = 1
        WORKFLOW_MAX_ESTIMATED_USD_PER_RUN = 0  # app convention: 0 = no cap
        WORKFLOW_MAX_WORKER_CALLS_PER_RUN = 0  # 0 = no cap
        WORKFLOW_MAX_INPUT_TOKENS_PER_CALL = 20000

    svc = Mock()
    svc.config = _Cfg()
    graph = AnkiGenerationGraph(svc)

    limits = graph._workflow_profile().limits

    assert limits.max_text_calls == 16
    assert limits.max_image_calls == 1
    assert limits.max_estimated_usd is None
    assert limits.max_worker_calls is None
    assert limits.max_input_tokens_per_call == 20000

    from ai_workflow_engine.budget import budget_from_limits

    budget = budget_from_limits(limits)
    assert budget.max_text_calls == 16
    assert budget.max_estimated_usd is None
