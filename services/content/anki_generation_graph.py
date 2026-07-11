"""LangGraph-backed Anki generation workflow.

This is the first product-specific graph on top of the shared workflow runner. It keeps current
card materialization behavior by reusing AnkiCardService.extract_cards(), while making branching,
image asset decisions, validation, fallback, and tracing explicit.
"""

import inspect
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Sequence, TypedDict

from core.exceptions import ParsingError
from core.logging import get_logger
from models.anki_workflow import (
    AnkiDirectiveConstraints,
    CardBuildPlan,
    ClozeCardScenario,
    ContentSource,
    GeneratedMedia,
    ImageAssetPlan,
    RenderedCardEvaluation,
    RenderedCardSet,
    TextCardScenario,
    VisualCardScenario,
)
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilitySpec,
    CriticismEnvelope,
    EvaluationDecision,
    RuntimeLimits,
    RuntimePlan,
    SafetyPolicy,
    WorkflowArtifact,
    WorkflowProfile,
    WorkflowRunContext,
)
from ai_workflow_tools.media.image_models import ImageGenerationRequest
from ai_workflow_tools.media.voice_generation import VoiceGenerationRequest
from ai_workflow_engine.models import WorkflowGoal, WorkflowTraceEvent, WorkflowUsageSummary
from services.anki_card_service import AnkiCardService
from services.content.anki_directives import parse_directives
from services.content.anki_renderers import (
    ClozeRenderer,
    FallbackTextRenderer,
    TextRenderer,
    VisualRenderer,
)
from services.content.anki_image_prompt_policy import AnkiImagePromptPolicy, ImagePromptContext
from ai_workflow_engine.engine import (
    CapabilityRegistry,
    DetailSink,
    InMemoryTraceSink,
    RuntimePlanCompiler,
    WorkflowRunner,
    cleanup_artifacts,
)

logger = get_logger(__name__)
DEFAULT_GENERATED_MEDIA_ROOT = "data/temp_cache/anki"


class AnkiGraphState(TypedDict, total=False):
    source: ContentSource
    workflow_goal: Any
    workflow_context: Any
    runtime_plan: RuntimePlan
    directives: AnkiDirectiveConstraints
    cleaned_content: str
    image_asset_plan: ImageAssetPlan
    build_plan: CardBuildPlan
    text_scenario: TextCardScenario
    cloze_scenario: ClozeCardScenario
    visual_scenario: VisualCardScenario
    rendered: RenderedCardSet
    quality_evaluation: RenderedCardEvaluation
    evaluation_decision: EvaluationDecision
    generated_media: list
    voice_generated_media: list
    retry_counts: Dict[str, int]
    quality_feedback: str
    trace: list
    usage_summary: WorkflowUsageSummary
    validation_error: str


class AnkiGenerationGraph:
    """Build and run the Anki graph."""

    def __init__(
        self,
        anki_card_service: AnkiCardService,
        runner: Optional[WorkflowRunner] = None,
        card_set_planner: Any = None,
        text_scenario_planner: Any = None,
        cloze_scenario_planner: Any = None,
        visual_scenario_planner: Any = None,
        text_renderer: Any = None,
        cloze_renderer: Any = None,
        visual_renderer: Any = None,
        fallback_renderer: Any = None,
        quality_evaluator: Any = None,
        enable_quality_evaluation: bool = True,
        evaluate_fallback_cards: bool = False,
        max_quality_repairs_per_run: int = 1,
        image_generator: Any = None,
        enable_image_generation: bool = False,
        enable_auto_image_generation: bool = False,
        max_image_generations_per_run: int = 1,
        image_model: str = "gpt-image-2",
        image_size: str = "auto",
        image_quality: str = "auto",
        image_output_format: str = "png",
        image_provider: str = "",
        voice_generator: Any = None,
        enable_voice_generation: bool = False,
        max_voice_generations_per_run: int = 1,
        voice_model: str = "eleven_multilingual_v2",
        voice_output_format: str = "mp3_44100_128",
        image_prompt_policy: Any = None,
        style_reference_images: Optional[Sequence[str]] = None,
        style_reference_version: str = "",
        generated_media_root: str = DEFAULT_GENERATED_MEDIA_ROOT,
        detail_sink: Optional[DetailSink] = None,
        capture_observation_detail_text: bool = False,
        observation_bundle_dir: Optional[str] = None,
        observation_retention_limit: Optional[int] = None,
    ):
        self.anki_card_service = anki_card_service
        self.runner = runner
        self._runner_config = getattr(anki_card_service, "config", None)
        self.card_set_planner = card_set_planner
        self.text_scenario_planner = text_scenario_planner
        self.cloze_scenario_planner = cloze_scenario_planner
        self.visual_scenario_planner = visual_scenario_planner
        self.text_renderer = text_renderer or TextRenderer(anki_card_service)
        self.cloze_renderer = cloze_renderer or ClozeRenderer(anki_card_service)
        self.visual_renderer = visual_renderer or VisualRenderer(anki_card_service)
        self.fallback_renderer = fallback_renderer or FallbackTextRenderer(anki_card_service)
        self.quality_evaluator = quality_evaluator
        self.enable_quality_evaluation = enable_quality_evaluation
        self.evaluate_fallback_cards = evaluate_fallback_cards
        self.max_quality_repairs_per_run = max(0, max_quality_repairs_per_run)
        self.image_generator = image_generator
        self.enable_image_generation = enable_image_generation
        self.enable_auto_image_generation = enable_auto_image_generation
        self.max_image_generations_per_run = max(0, max_image_generations_per_run)
        self.image_model = image_model
        self.image_size = image_size
        self.image_quality = image_quality
        self.image_output_format = image_output_format
        self.image_provider = (
            image_provider
            or getattr(getattr(anki_card_service, "config", None), "ANKI_IMAGE_PROVIDER", "openai")
        )
        self.voice_generator = voice_generator
        self.enable_voice_generation = enable_voice_generation
        self.max_voice_generations_per_run = max(0, max_voice_generations_per_run)
        self.voice_model = voice_model
        self.voice_output_format = voice_output_format
        self.image_prompt_policy = image_prompt_policy or AnkiImagePromptPolicy(
            getattr(anki_card_service, "config", None),
            image_provider=self.image_provider,
        )
        self.style_reference_images = self._normalize_style_reference_images(style_reference_images or [])
        self.style_reference_version = style_reference_version
        self.generated_media_root = generated_media_root
        self.capability_trace_sink = InMemoryTraceSink()
        self.detail_sink = detail_sink
        self.capture_observation_detail_text = capture_observation_detail_text
        self.observation_bundle_dir = observation_bundle_dir
        self.observation_retention_limit = observation_retention_limit
        self.capability_registry = CapabilityRegistry()
        self.last_run_state: Optional[AnkiGraphState] = None
        self.last_run_result: Any = None
        self._last_observation_bundle_path: Optional[str] = None
        # The Anki workflow now runs on the reusable executable engine (no product-owned graph).
        self._capabilities_registered = False
        self._register_workflow_capabilities()

    @staticmethod
    def _normalize_style_reference_images(style_reference_images: Sequence[Any]) -> list[str]:
        normalized = []
        for path in style_reference_images:
            if not path:
                continue
            if not isinstance(path, (str, os.PathLike)):
                raise TypeError(
                    "style_reference_images must contain filesystem paths, "
                    f"got {type(path).__name__}"
                )
            normalized.append(os.fspath(path))
        return normalized

    async def run(self, source: ContentSource, message: Any = None) -> RenderedCardSet:
        self._clear_observation_details()
        run_id = str(uuid.uuid4())
        # The engine auto-opens the bundle at observation_bundle_dir/run_id (goal metadata pins
        # the run id); pre-compute the path so it is known even when the run raises.
        self._last_observation_bundle_path = (
            str(Path(self.observation_bundle_dir) / run_id) if self.observation_bundle_dir else None
        )
        engine = self._engine()
        runtime_plan = self._runtime_plan_for_source(source)
        goal = WorkflowGoal(
            workflow_type="anki_generation",
            objective="Generate focused Anki flashcards from Telegram content",
            constraints={
                "application": "anki",
                "uploaded_image_count": len(source.images),
                "default_count_policy": "prefer_fewer_cards",
                "supports_user_images": True,
                "supports_generated_images": self._image_generator_ready(),
                "supports_auto_generated_images": self.enable_auto_image_generation,
                "supports_voice_generation": self._voice_generator_ready(),
            },
            delivery_target="telegram_anki_package",
            user_id=source.user_id,
            metadata={
                "run_id": run_id,
                "telegram_chat_id": self._optional_int(getattr(getattr(message, "chat", None), "id", None)),
                "telegram_message_id": self._optional_int(getattr(message, "message_id", None)),
            },
        )
        run_context = WorkflowRunContext(
            workflow_id=run_id,
            workflow_type=goal.workflow_type,
            goal_id=goal.goal_id,
            delivery_target=goal.delivery_target,
            user_id=goal.user_id,
            metadata=dict(goal.metadata),
        )
        initial_state: AnkiGraphState = {
            "source": source,
            "trace": [],
            "retry_counts": {},
            "generated_media": [],
            "voice_generated_media": [],
            "runtime_plan": runtime_plan,
            "workflow_goal": goal,
            "workflow_context": run_context,
        }
        # A4: the ENGINE run session owns the bundle lifecycle — it finalizes exactly once
        # with the true terminal status (raise -> failed; envelope status otherwise), and the
        # terminal_status hook lets product post-validation mark a "completed" engine run as
        # failed BEFORE the durable record is written. This class no longer finalizes.
        result = await engine.run(
            "anki_generation",
            initial_state,
            goal=goal,
            recursion_fallback=self._engine_recursion_fallback,
            recursion_limit=self._graph_recursion_limit(),
            terminal_status=self._terminal_bundle_status,
        )
        self.last_run_result = result
        self._last_observation_bundle_path = result.observation_bundle_path
        final_state = result.output if isinstance(result.output, dict) else {}
        self.last_run_state = final_state
        rendered = final_state.get("rendered")
        if not rendered:
            raise ParsingError("Anki graph produced no rendered cards")
        usage_summary = result.usage or final_state.get("usage_summary")
        if usage_summary:
            rendered = rendered.model_copy(update={"usage_summary": usage_summary})
        return rendered

    @staticmethod
    def _terminal_bundle_status(result: Any) -> Optional[str]:
        """Product post-validation for the durable record: no rendered cards == failed."""

        final_state = result.output if isinstance(result.output, dict) else {}
        if not final_state.get("rendered"):
            return "failed"
        return None

    def last_observation_bundle_path(self) -> Optional[str]:
        """Return the most recent durable observation bundle path, when enabled."""

        return self._last_observation_bundle_path

    def _engine_recursion_fallback(self, wrapper_state: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
        """Adapt the product recursion fallback to the engine's wrapper state shape.

        The engine carries the Anki state under the ``payload`` key, so unwrap it, run the existing
        text fallback, and return an engine-level update that re-wraps the merged Anki state.
        """

        anki_state = wrapper_state.get("payload", {}) or {}
        fallback_update = self._recursion_fallback(anki_state, exc)
        return {"payload": {**anki_state, **fallback_update}}

    def _recursion_fallback(self, state: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
        fallback_state: AnkiGraphState = dict(state)
        fallback_state.setdefault("trace", [])
        fallback_state.setdefault("retry_counts", {})
        fallback_state.setdefault("generated_media", [])
        fallback_state.setdefault("voice_generated_media", [])
        fallback_state["validation_error"] = (
            "workflow recursion limit exceeded; used text fallback"
        )
        fallback_state["quality_feedback"] = str(exc)

        if "directives" not in fallback_state or "cleaned_content" not in fallback_state:
            fallback_state.update(self._parse_directives(fallback_state))

        generated_media = [
            *fallback_state.get("generated_media", []),
            *fallback_state.get("voice_generated_media", []),
        ]
        if generated_media:
            self._cleanup_generated_media(generated_media)
            fallback_state["generated_media"] = []
            fallback_state["voice_generated_media"] = []

        fallback_update = self._fallback_to_text(fallback_state)
        fallback_update = self._merge_trace(
            fallback_state,
            fallback_update,
            "fallback_to_text",
            decision="recursion_fallback",
            elapsed_ms=0,
        )
        validation_state = {**fallback_state, **fallback_update}
        validation_update = self._validate_rendered_cards(validation_state)
        if validation_update.get("validation_error"):
            raise ParsingError(f"Recursion fallback failed validation: {validation_update['validation_error']}")
        validation_update = self._merge_trace(
            validation_state,
            validation_update,
            "validate_rendered_cards",
            decision="valid",
            elapsed_ms=0,
        )
        return {
            **fallback_update,
            **validation_update,
            "validation_error": "",
        }

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        return value if isinstance(value, int) else None

    def _graph_recursion_limit(self) -> int:
        """Bound LangGraph cycles explicitly while leaving room for configured repairs."""

        return (
            64
            + (12 * self.max_quality_repairs_per_run)
            + (4 * self.max_image_generations_per_run)
            + (4 * self.max_voice_generations_per_run)
        )

    def _engine(self):
        """Build the executable workflow engine for one Anki run.

        Orchestration (node dispatch, branching, retry/retrace cycles, fallback, side-effect/budget
        policy, trace) is owned by the reusable engine — including observation: the engine
        auto-opens, routes, finalizes, and prunes the per-run bundle from the observation config.
        This class contributes only the domain capabilities, routing decisions, and the
        declarative workflow shape — no graph wiring, no bundle mechanics.
        """

        from ai_workflow_engine import ObservationConfig, WorkflowEngine

        self._register_workflow_capabilities()
        observation = ObservationConfig(
            enabled=bool(self.observation_bundle_dir),
            bundle_dir=self.observation_bundle_dir or "data/observations",
            retention_limit=self.observation_retention_limit,
            capture="full" if self.capture_observation_detail_text else "off",
        )
        engine = WorkflowEngine(
            registry=self.capability_registry,
            trace_sink=self.capability_trace_sink,
            detail_sink=self.detail_sink,
            capture_detail_text=self.capture_observation_detail_text,
            observation=observation,
            config=self._runner_config,
        )
        if self.runner is not None:
            # Test hook/custom runner: do not mutate shared runner state here. Production runs use
            # the per-engine runner above so usage sinks and budgets stay run-local.
            engine.executor.runner = self.runner
        engine.register_workflow(self._anki_workflow_definition(), profile=self._workflow_profile())
        return engine

    def _observation_details(self) -> list[Any]:
        if self.detail_sink is None:
            return []
        return list(getattr(self.detail_sink, "details", []) or [])

    def _clear_observation_details(self) -> None:
        self.capability_trace_sink.events.clear()
        if self.detail_sink is None:
            return
        clear = getattr(self.detail_sink, "clear", None)
        if callable(clear):
            clear()

    def _workflow_profile(self) -> WorkflowProfile:
        config = getattr(self.anki_card_service, "config", None)
        return WorkflowProfile(
            workflow_type="anki_generation",
            requested_capabilities=self.capability_registry.names(),
            limits=self._engine_limits(config),
            safety=SafetyPolicy(
                allowed_side_effects=["read_only", "local_write", "external_call", "notification"]
            ),
        )

    def _register_workflow_capabilities(self) -> None:
        if self._capabilities_registered:
            return
        node_fns = [
            ("parse_directives", self._parse_directives),
            ("plan_image_assets", self._plan_image_assets),
            ("plan_card_type", self._plan_card_type),
            ("prepare_text_scenario", self._prepare_text_scenario),
            ("prepare_cloze_scenario", self._prepare_cloze_scenario),
            ("prepare_visual_scenario", self._prepare_visual_scenario),
            ("validate_text_scenario", self._validate_text_scenario),
            ("validate_cloze_scenario", self._validate_cloze_scenario),
            ("validate_visual_scenario", self._validate_visual_scenario),
            ("generate_image", self._generate_image),
            ("generate_voice", self._generate_voice),
            ("render_text_or_cloze", self._render_text_or_cloze),
            ("render_visual", self._render_visual),
            ("validate_rendered_cards", self._validate_rendered_cards),
            ("evaluate_rendered_cards", self._evaluate_rendered_cards),
            ("repair_rendered_cards", self._repair_rendered_cards),
            ("fallback_to_text", self._fallback_to_text),
            ("package_cards", self._package_cards),
        ]
        for name, fn in node_fns:
            if name in self.capability_registry.names():
                continue
            self.capability_registry.register(
                CapabilitySpec(
                    name=name,
                    kind=self._capability_kind(name),
                    description=f"Anki workflow node: {name}",
                    side_effects=self._capability_side_effects(name),
                ),
                self._node_handler(name, fn),
            )
        route_fns = [
            ("route_card_kind", self._route_card_kind),
            ("route_text_validation", self._route_scenario_validation),
            ("route_cloze_validation", self._route_scenario_validation),
            ("route_visual_validation", self._route_visual_validation),
            ("route_image_generation", self._route_image_generation),
            ("route_rendered_validation", self._route_rendered_validation),
            ("route_quality_evaluation", self._route_quality_evaluation),
            ("route_render_repair", self._route_render_repair),
        ]
        for name, route_fn in route_fns:
            if name in self.capability_registry.names():
                continue
            self.capability_registry.register(
                CapabilitySpec(name=name, kind="deterministic", description=f"Anki routing decision: {name}"),
                self._route_decider(route_fn),
            )
        self._capabilities_registered = True

    def _node_handler(self, name: str, fn):
        """Wrap a node function as a full-state-merging capability executed by the engine.

        Preserves prior node behavior: run the function, record the Anki trace + decision, and on
        error (except the terminal text fallback) capture validation_error so the engine's routing
        layer recovers instead of crashing.
        """

        async def handler(_context: CapabilityContext, state: AnkiGraphState) -> Dict[str, Any]:
            start = time.monotonic()
            try:
                update = fn(state)
                if inspect.isawaitable(update):
                    update = await update
                update = update or {}
                elapsed_ms = int((time.monotonic() - start) * 1000)
                decision = self._decision_for_node(name, update)
                merged = self._merge_trace(state, update, name, decision=decision, elapsed_ms=elapsed_ms)
                return {**state, **merged}
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                logger.error("Anki workflow node failed %s: %s", name, exc)
                if name == "fallback_to_text":
                    raise
                merged = self._merge_trace(
                    state, {"validation_error": str(exc)}, name, error=str(exc), elapsed_ms=elapsed_ms
                )
                return {**state, **merged}

        return handler

    @staticmethod
    def _route_decider(route_fn):
        def decider(_context: CapabilityContext, state: AnkiGraphState) -> str:
            return route_fn(state)

        return decider

    def _anki_workflow_definition(self):
        from ai_workflow_engine.workflow import END, Transition, WorkflowDefinition, WorkflowNode

        steps = [
            (
                "parse_directives",
                "Parse message directives",
                "Parse message directives. Extract user flags, remove control text, and normalize Anki constraints.",
            ),
            (
                "plan_image_assets",
                "Plan image usage",
                "Plan image usage. Decide whether uploaded media is ignored, reused, referenced, or generated.",
            ),
            (
                "plan_card_type",
                "Plan card type and count",
                "Plan card type and count. Choose basic, cloze, or visual cards before scenario work starts.",
            ),
            (
                "prepare_text_scenario",
                "Prepare basic-card scenario",
                "Prepare basic-card scenario. Build the source and guidance used by the text renderer.",
            ),
            (
                "prepare_cloze_scenario",
                "Prepare cloze scenario",
                "Prepare cloze scenario. Build deletion strategy and source guidance for cloze cards.",
            ),
            (
                "prepare_visual_scenario",
                "Prepare visual scenario",
                "Prepare visual scenario. Build visual-card structure and media requirements.",
            ),
            (
                "validate_text_scenario",
                "Validate basic-card scenario",
                "Validate basic-card scenario. Check that text-card planning is renderable.",
            ),
            (
                "validate_cloze_scenario",
                "Validate cloze scenario",
                "Validate cloze scenario. Check that cloze planning is renderable.",
            ),
            (
                "validate_visual_scenario",
                "Validate visual scenario",
                "Validate visual scenario. Check visual-card planning and media needs.",
            ),
            (
                "generate_image",
                "Generate image asset",
                "Generate image asset. Create local visual media when the plan requires new imagery.",
            ),
            (
                "generate_voice",
                "Generate voice asset",
                "Generate voice asset. Add pronunciation audio when language-voice directives request it.",
            ),
            (
                "render_text_or_cloze",
                "Render basic or cloze cards",
                "Render basic or cloze cards. Materialize final Anki card text from the selected scenario.",
            ),
            (
                "render_visual",
                "Render visual cards",
                "Render visual cards. Materialize final Anki cards with planned visual media.",
            ),
            (
                "validate_rendered_cards",
                "Validate rendered cards",
                "Validate rendered cards. Ensure cards exist and have usable front/back content.",
            ),
            (
                "evaluate_rendered_cards",
                "Evaluate final quality",
                "Evaluate final quality. Ask the quality evaluator whether to accept, retry, repair, or fallback.",
            ),
            (
                "repair_rendered_cards",
                "Prepare render repair",
                "Prepare render repair. Feed evaluator guidance back into the next render attempt.",
            ),
            (
                "fallback_to_text",
                "Fallback to text cards",
                "Fallback to text cards. Produce a safe basic-card set when richer paths fail.",
            ),
            (
                "package_cards",
                "Package cards",
                "Package cards. Write the final Anki package artifacts for delivery.",
            ),
        ]
        branches = {
            "route_card_kind": {
                "title": "Choose card type branch",
                "description": "Choose card type branch. Sends the run to basic, cloze, or visual scenario preparation.",
                "labels": {
                    "basic": ("prepare_text_scenario", "Use ordinary front/back cards."),
                    "cloze": ("prepare_cloze_scenario", "Use cloze-deletion cards."),
                    "visual_basic": ("prepare_visual_scenario", "Use visual cards."),
                },
            },
            "route_text_validation": {
                "title": "Route basic-card validation",
                "description": "Route basic-card validation. Continue, retry planning, or fall back to safer text cards.",
                "labels": {
                    "valid": ("render_text_or_cloze", "Scenario can render."),
                    "retry": ("prepare_text_scenario", "Revise the basic-card scenario."),
                    "fallback": ("fallback_to_text", "Stop retrying and build safe text cards."),
                },
            },
            "route_cloze_validation": {
                "title": "Route cloze validation",
                "description": "Route cloze validation. Continue, retry planning, or fall back to safer text cards.",
                "labels": {
                    "valid": ("render_text_or_cloze", "Scenario can render."),
                    "retry": ("prepare_cloze_scenario", "Revise the cloze scenario."),
                    "fallback": ("fallback_to_text", "Stop retrying and build safe text cards."),
                },
            },
            "route_visual_validation": {
                "title": "Route visual validation",
                "description": "Route visual validation. Render, generate media, retry planning, or fall back.",
                "labels": {
                    "render": ("render_visual", "Visual scenario is ready to render."),
                    "generate": ("generate_image", "Generate required visual media first."),
                    "retry": ("prepare_visual_scenario", "Revise the visual scenario."),
                    "fallback": ("fallback_to_text", "Stop visual path and build safe text cards."),
                },
            },
            "route_image_generation": {
                "title": "Route image generation result",
                "description": "Route image generation result. Continue with visual rendering or fall back.",
                "labels": {
                    "valid": ("render_visual", "Generated media is ready."),
                    "fallback": ("fallback_to_text", "Generated media failed or was unavailable."),
                },
            },
            "route_rendered_validation": {
                "title": "Route rendered-card validation",
                "description": "Route rendered-card validation. Continue to quality evaluation or fall back.",
                "labels": {
                    "valid": ("evaluate_rendered_cards", "Rendered cards are structurally usable."),
                    "fallback": ("fallback_to_text", "Rendered cards are unusable; fall back."),
                },
            },
            "route_quality_evaluation": {
                "title": "Route quality decision",
                "description": "Route quality decision. Accept, repair, retry an earlier planner, or fall back.",
                "labels": {
                    "valid": ("package_cards", "Quality accepted; package cards."),
                    "retry_card_plan": ("plan_card_type", "Return to card-set planning."),
                    "repair_render": ("repair_rendered_cards", "Repair the current rendered card set."),
                    "retry_text_scenario": ("prepare_text_scenario", "Return to basic-card scenario planning."),
                    "retry_cloze_scenario": ("prepare_cloze_scenario", "Return to cloze scenario planning."),
                    "retry_visual_scenario": ("prepare_visual_scenario", "Return to visual scenario planning."),
                    "fallback": ("fallback_to_text", "Use safe fallback cards."),
                },
            },
            "route_render_repair": {
                "title": "Route render repair",
                "description": "Route render repair. Send repaired state to the right renderer.",
                "labels": {
                    "render_text_or_cloze": ("render_text_or_cloze", "Re-render text or cloze cards."),
                    "render_visual": ("render_visual", "Re-render visual cards."),
                },
            },
        }
        sequential = [
            ("parse_directives", "plan_image_assets"),
            ("plan_image_assets", "plan_card_type"),
            ("plan_card_type", "route_card_kind"),
            ("prepare_text_scenario", "validate_text_scenario"),
            ("prepare_cloze_scenario", "validate_cloze_scenario"),
            ("prepare_visual_scenario", "validate_visual_scenario"),
            ("validate_text_scenario", "route_text_validation"),
            ("validate_cloze_scenario", "route_cloze_validation"),
            ("validate_visual_scenario", "route_visual_validation"),
            ("generate_image", "route_image_generation"),
            ("render_text_or_cloze", "validate_rendered_cards"),
            ("render_visual", "generate_voice"),
            ("generate_voice", "validate_rendered_cards"),
            ("validate_rendered_cards", "route_rendered_validation"),
            ("evaluate_rendered_cards", "route_quality_evaluation"),
            ("repair_rendered_cards", "route_render_repair"),
            ("fallback_to_text", "validate_rendered_cards"),
            ("package_cards", END),
        ]
        nodes = [
            WorkflowNode(
                id=name,
                kind="step",
                capability=name,
                title=title,
                description=description,
            )
            for name, title, description in steps
        ]
        for bid, bmap in branches.items():
            labels = bmap["labels"]
            nodes.append(
                WorkflowNode(
                    id=bid,
                    kind="branch",
                    decider=bid,
                    branches={label: target for label, (target, _description) in labels.items()},
                    title=bmap["title"],
                    description=bmap["description"],
                )
            )
        # Machine-level pre-set gates on every loop-closing label: a SAFETY NET strictly above
        # the product deciders' own stop logic (deciders halt first; the gate catches runaway).
        loop_bounds = {
            ("route_text_validation", "retry"): 3,
            ("route_text_validation", "fallback"): 2,
            ("route_cloze_validation", "retry"): 3,
            ("route_cloze_validation", "fallback"): 2,
            ("route_visual_validation", "retry"): 3,
            ("route_visual_validation", "fallback"): 2,
            ("route_image_generation", "fallback"): 2,
            ("route_rendered_validation", "fallback"): 2,
            ("route_quality_evaluation", "retry_card_plan"): 2,
            ("route_quality_evaluation", "repair_render"): 3,
            ("route_quality_evaluation", "retry_text_scenario"): 2,
            ("route_quality_evaluation", "retry_cloze_scenario"): 2,
            ("route_quality_evaluation", "retry_visual_scenario"): 2,
            ("route_quality_evaluation", "fallback"): 2,
        }
        transitions = [Transition(source=src, target=tgt) for src, tgt in sequential]
        for bid, bmap in branches.items():
            for label, (tgt, transition_description) in bmap["labels"].items():
                transitions.append(
                    Transition(
                        source=bid, target=tgt, label=label, policy="decision",
                        max_traversals=loop_bounds.get((bid, label)),
                        description=transition_description,
                    )
                )
        return WorkflowDefinition(
            workflow_id="anki_generation",
            nodes=nodes,
            transitions=transitions,
            entry="parse_directives",
            description="Anki flashcard generation",
        )

    @staticmethod
    def _capability_kind(name: str) -> str:
        if name in {"plan_card_type", "prepare_text_scenario", "prepare_cloze_scenario", "prepare_visual_scenario"}:
            return "llm"
        if name in {"generate_image"}:
            return "media"
        if name in {"generate_voice"}:
            return "voice"
        if name in {"package_cards"}:
            return "tool"
        return "deterministic"

    @staticmethod
    def _capability_side_effects(name: str) -> list[str]:
        if name in {"generate_image", "generate_voice"}:
            return ["external_call", "local_write"]
        if name == "package_cards":
            return ["local_write"]
        return []

    def _runtime_plan_for_source(self, source: ContentSource) -> RuntimePlan:
        config = getattr(self.anki_card_service, "config", None)
        profile = WorkflowProfile(
            workflow_type="anki_generation",
            requested_capabilities=self.capability_registry.names(),
            constraints={
                "application": "anki",
                "uploaded_image_count": len(source.images),
                "default_count_policy": "prefer_fewer_cards",
            },
            limits=self._engine_limits(config),
            safety=SafetyPolicy(
                allowed_side_effects=[
                    "read_only",
                    "local_write",
                    "external_call",
                    "notification",
                ]
            ),
        )
        return RuntimePlanCompiler().compile(profile, self.capability_registry)

    def _engine_limits(self, config: Any) -> RuntimeLimits:
        """Anki's structural limits layered on the ONE application budget boundary
        (`config.engine_runtime_limits` — R1: no per-graph WORKFLOW_MAX_* reads)."""

        from config import engine_runtime_limits

        return engine_runtime_limits(config).model_copy(
            update={
                "max_steps": 32,
                "max_retries": self.max_quality_repairs_per_run,
                "max_parallel_children": 1,
            }
        )

    @staticmethod
    def _merge_trace(
        state: AnkiGraphState,
        update: Dict[str, Any],
        node: str,
        decision: Optional[str] = None,
        error: Optional[str] = None,
        elapsed_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        trace = list(state.get("trace", []))
        artifacts = [
            media.path
            for media in [*update.get("generated_media", []), *update.get("voice_generated_media", [])]
            if getattr(media, "path", None)
        ]
        details = AnkiGenerationGraph._trace_details_for_node(node, update)
        trace.append(
            WorkflowTraceEvent(
                node=node,
                attempt=state.get("retry_counts", {}).get(node, 0) + 1,
                decision=decision,
                error=error,
                artifacts=artifacts,
                elapsed_ms=elapsed_ms,
                metadata=details,
            )
        )
        ctx = state.get("workflow_context")
        logger.info(
            "workflow_node %s",
            json.dumps(
                {
                    "workflow_id": getattr(ctx, "workflow_id", None),
                    "workflow_type": getattr(ctx, "workflow_type", None),
                    "user_id": getattr(ctx, "user_id", None),
                    "node": node,
                    "attempt": state.get("retry_counts", {}).get(node, 0) + 1,
                    "branch_decision": decision,
                    "validation_error": error or update.get("validation_error") or None,
                    "elapsed_ms": elapsed_ms,
                    "artifacts": artifacts,
                    "details": details or None,
                },
                sort_keys=True,
            ),
        )
        update["trace"] = trace
        return update

    @staticmethod
    def _trace_details_for_node(node: str, update: Dict[str, Any]) -> Dict[str, Any]:
        if node == "plan_card_type" and update.get("build_plan"):
            plan = update["build_plan"]
            return {
                "card_kind": plan.card_kind,
                "image_policy": plan.image_policy,
                "count": plan.count,
                "source_facts_count": len(plan.source_facts),
                "source_facts_preview": AnkiGenerationGraph._short_list(plan.source_facts),
                "study_goal": AnkiGenerationGraph._short(plan.study_goal),
            }
        if node in ("prepare_text_scenario", "prepare_cloze_scenario", "prepare_visual_scenario"):
            scenario = (
                update.get("text_scenario")
                or update.get("cloze_scenario")
                or update.get("visual_scenario")
            )
            if not scenario:
                return {}
            return AnkiGenerationGraph._scenario_trace_details(scenario)
        if node in ("render_text_or_cloze", "render_visual", "fallback_to_text") and update.get("rendered"):
            return AnkiGenerationGraph._rendered_trace_details(update["rendered"])
        if node == "evaluate_rendered_cards" and update.get("quality_evaluation"):
            evaluation = update["quality_evaluation"]
            details = {
                "accepted": evaluation.accepted,
                "repair_strategy": evaluation.repair_strategy,
                "severity": evaluation.severity,
                "issues": AnkiGenerationGraph._short_list(evaluation.issues),
            }
            decision = update.get("evaluation_decision")
            if decision:
                details.update(
                    {
                        "decision_action": decision.action,
                        "target_capability": decision.target_capability,
                        "retrace_to": decision.retrace_to,
                        "user_visible_effect": AnkiGenerationGraph._short(decision.user_visible_effect),
                    }
                )
            return details
        return {}

    @staticmethod
    def _scenario_trace_details(scenario: Any) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "source_length": len(getattr(scenario, "source_content", "") or ""),
            "rendering_guide": AnkiGenerationGraph._short(getattr(scenario, "rendering_guide", None)),
        }
        facts = getattr(scenario, "facts_to_test", None)
        if facts is not None:
            details["facts_to_test_count"] = len(facts)
            details["facts_to_test_preview"] = AnkiGenerationGraph._short_list(facts)
        cloze_targets = getattr(scenario, "cloze_targets", None)
        if cloze_targets is not None:
            details["cloze_targets_count"] = len(cloze_targets)
            details["cloze_targets_preview"] = AnkiGenerationGraph._short_list(cloze_targets)
        if hasattr(scenario, "question_text"):
            details["question"] = AnkiGenerationGraph._short(scenario.question_text)
            details["answer"] = AnkiGenerationGraph._short(scenario.answer_text)
            details["layout"] = scenario.layout
            details["image_count"] = scenario.image_count
        if getattr(scenario, "count", None) is not None:
            details["count"] = scenario.count
        if getattr(scenario, "strategy", None) is not None:
            details["strategy"] = scenario.strategy
        return details

    @staticmethod
    def _rendered_trace_details(rendered: RenderedCardSet) -> Dict[str, Any]:
        previews = []
        for card in rendered.cards[:3]:
            previews.append(
                {
                    "type": getattr(card, "type", "basic"),
                    "question": AnkiGenerationGraph._short(getattr(card, "question", None)),
                    "answer": AnkiGenerationGraph._short(getattr(card, "answer", None)),
                    "text": AnkiGenerationGraph._short(getattr(card, "text", None)),
                }
            )
        return {
            "card_count": len(rendered.cards),
            "fallback_used": rendered.fallback_used,
            "fallback_reason": AnkiGenerationGraph._short(rendered.fallback_reason),
            "cards_preview": previews,
        }

    @staticmethod
    def _short(value: Any, limit: int = 220) -> Optional[str]:
        if value is None:
            return None
        text = " ".join(str(value).split())
        if not text:
            return None
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _short_list(values: Sequence[Any], limit: int = 3) -> list[str]:
        return [
            item
            for item in (AnkiGenerationGraph._short(value) for value in values[:limit])
            if item
        ]

    @staticmethod
    def _decision_for_node(name: str, update: Dict[str, Any]) -> Optional[str]:
        if name == "plan_image_assets" and update.get("image_asset_plan"):
            return update["image_asset_plan"].image_role
        if name == "plan_card_type" and update.get("build_plan"):
            return update["build_plan"].card_kind
        if name == "generate_image" and update.get("generated_media"):
            return "generated"
        if name == "generate_voice" and update.get("voice_generated_media"):
            return "generated"
        if name == "evaluate_rendered_cards" and update.get("quality_evaluation"):
            evaluation = update["quality_evaluation"]
            return "accepted" if evaluation.accepted else evaluation.repair_strategy
        return None

    def _parse_directives(self, state: AnkiGraphState) -> Dict[str, Any]:
        source = state["source"]
        raw, cleaned = parse_directives(source.content, source_has_images=bool(source.images))
        directives = AnkiDirectiveConstraints(
            include_image=raw.include_image,
            image_placement=raw.image_placement,
            image_policy=raw.image_policy,
            multi_split=raw.multi_split,
            strategy=raw.strategy,
            count=raw.count,
            card_type=raw.card_type,
            guide_mode=raw.guide_mode,
            guide=raw.guide,
            help=raw.help,
            language_voice=raw.language_voice,
            source_language=raw.source_language,
            target_language=raw.target_language,
        )
        return {"directives": directives, "cleaned_content": cleaned}

    def _plan_image_assets(self, state: AnkiGraphState) -> Dict[str, Any]:
        source = state["source"]
        directives = state["directives"]
        image_count = len(source.images)

        if not directives.include_image:
            plan = ImageAssetPlan(
                image_role="source_content_only",
                rationale="User disabled image media; OCR/summary may still inform cards",
            )
            return {"image_asset_plan": plan}

        if image_count == 0:
            if directives.card_type == "visual" or directives.image_policy == "generate":
                plan = ImageAssetPlan(
                    image_role="generate_new_visual",
                    rationale="Visual card requested without uploaded media",
                )
            else:
                plan = ImageAssetPlan(image_role="ignore_media", rationale="No uploaded images")
            return {"image_asset_plan": plan}

        if directives.image_policy == "reference":
            plan = ImageAssetPlan(
                image_role="use_as_reference",
                reference_images=list(range(image_count)),
                rationale="Uploaded image selected as reference for generated visual",
            )
            return {"image_asset_plan": plan}

        if directives.image_policy == "generate":
            plan = ImageAssetPlan(
                image_role="generate_new_visual",
                rationale=(
                    "Generate a new visual from extracted source content; uploaded images remain "
                    "content/OCR sources unless a later AI plan explicitly selects them as references"
                ),
            )
            return {"image_asset_plan": plan}

        if directives.image_policy == "reuse_user_image":
            directives.image_placement = directives.image_placement or "back"

        if directives.multi_split and image_count >= 2:
            front = [1]
            back = [0] + list(range(2, image_count))
        elif directives.image_placement == "front":
            front, back = list(range(image_count)), []
        else:
            front, back = [], list(range(image_count))

        plan = ImageAssetPlan(
            image_role="reuse_user_image",
            candidate_front_images=front,
            candidate_back_images=back,
            rationale="Uploaded image selected as card media",
        )
        return {"image_asset_plan": plan}

    async def _plan_card_type(self, state: AnkiGraphState) -> Dict[str, Any]:
        directives = self._directives_for_planning(state)
        image_plan = state["image_asset_plan"]
        can_generate = self._can_generate_images(directives)
        planning_source = self._source_for_planning(state)

        if self.card_set_planner:
            try:
                plan = await self.card_set_planner.plan(
                    planning_source,
                    directives,
                    image_plan,
                    can_generate,
                )
            except Exception as exc:
                logger.warning("Anki card-set AI planner failed; using heuristic fallback: %s", exc)
                if directives.card_type:
                    plan = self._forced_card_plan(directives, image_plan, can_generate)
                else:
                    plan = self._heuristic_card_plan(directives, image_plan, can_generate)
        elif directives.card_type:
            plan = self._forced_card_plan(directives, image_plan, can_generate)
        else:
            plan = self._heuristic_card_plan(directives, image_plan, can_generate)

        plan = self._normalize_build_plan(plan, directives, image_plan, can_generate)
        image_plan = self._image_plan_for_build_plan(plan, image_plan, len(state["source"].images))
        return {"build_plan": plan, "image_asset_plan": image_plan}

    @staticmethod
    def _forced_card_plan(
        directives: AnkiDirectiveConstraints,
        image_plan: ImageAssetPlan,
        can_generate: bool,
    ) -> CardBuildPlan:
        constraints = [f"forced:{directives.card_type}"] if directives.card_type else []
        needs_generation = image_plan.image_role in ("generate_new_visual", "use_as_reference")
        if directives.card_type == "cloze":
            kind = "cloze"
            fallback = "basic"
        elif directives.card_type == "visual":
            kind = (
                "visual_basic"
                if image_plan.uses_uploaded_media or (needs_generation and can_generate)
                else "basic"
            )
            fallback = "basic"
        else:
            kind = "basic"
            fallback = "basic"
        return CardBuildPlan(
            card_kind=kind,
            image_policy=AnkiGenerationGraph._image_policy_for_asset_plan(image_plan),
            count=directives.count,
            study_goal="Create focused Anki flashcards from the supplied content",
            visual_rationale=image_plan.rationale if kind == "visual_basic" else None,
            fallback_kind=fallback,
            user_constraints_applied=constraints,
        )

    @staticmethod
    def _heuristic_card_plan(
        directives: AnkiDirectiveConstraints,
        image_plan: ImageAssetPlan,
        can_generate: bool,
    ) -> CardBuildPlan:
        # Used only when no AI planner is configured or the planner fails. Runtime wiring injects
        # the AI planner; this keeps tests and local packaging paths deterministic.
        image_policy = AnkiGenerationGraph._image_policy_for_asset_plan(image_plan)
        return CardBuildPlan(
            card_kind="basic",
            image_policy=image_policy,
            count=directives.count,
            study_goal="Create focused Anki flashcards from the supplied content",
            visual_rationale=image_plan.rationale if image_policy != "none" and can_generate else None,
            fallback_kind="basic",
            user_constraints_applied=["heuristic_fallback"],
        )

    @staticmethod
    def _image_policy_for_asset_plan(
        image_plan: ImageAssetPlan,
    ) -> Literal["none", "reuse_user_image", "reference", "generate"]:
        if image_plan.image_role == "reuse_user_image":
            return "reuse_user_image"
        if image_plan.image_role == "use_as_reference":
            return "reference"
        if image_plan.image_role == "generate_new_visual":
            return "generate"
        return "none"

    def _image_generator_ready(self) -> bool:
        return self.enable_image_generation and self.image_generator is not None

    def _voice_generator_ready(self) -> bool:
        return self.enable_voice_generation and self.voice_generator is not None

    def _can_generate_voice(self, directives: AnkiDirectiveConstraints) -> bool:
        return directives.language_voice and self._voice_generator_ready()

    def _can_generate_images(self, directives: AnkiDirectiveConstraints) -> bool:
        if not self._image_generator_ready():
            return False
        if self.enable_auto_image_generation:
            return True
        return directives.image_policy in ("generate", "reference")

    def _image_generation_unavailable_reason(
        self,
        directives: Optional[AnkiDirectiveConstraints] = None,
    ) -> str:
        if not self.enable_image_generation:
            return "image generation is disabled"
        if self.image_generator is None:
            return "image generator is not configured"
        if (
            directives
            and directives.image_policy not in ("generate", "reference")
            and not self.enable_auto_image_generation
        ):
            return "auto image generation is disabled"
        return "requested visual card is unavailable"

    def _voice_generation_unavailable_reason(self) -> str:
        if not self.enable_voice_generation:
            return "voice generation is disabled"
        if self.voice_generator is None:
            return "voice generator is not configured"
        return "voice generation is unavailable"

    @staticmethod
    def _normalize_build_plan(
        plan: CardBuildPlan,
        directives: AnkiDirectiveConstraints,
        image_plan: ImageAssetPlan,
        can_generate: bool,
    ) -> CardBuildPlan:
        constraints = list(plan.user_constraints_applied)
        if directives.card_type and f"forced:{directives.card_type}" not in constraints:
            constraints.append(f"forced:{directives.card_type}")

        card_kind = plan.card_kind
        image_policy = plan.image_policy
        fallback_kind = plan.fallback_kind or "basic"

        if directives.card_type == "basic":
            card_kind = "basic"
            image_policy = AnkiGenerationGraph._image_policy_for_asset_plan(image_plan)
            fallback_kind = "basic"
        elif directives.card_type == "cloze":
            card_kind = "cloze"
            image_policy = AnkiGenerationGraph._image_policy_for_asset_plan(image_plan)
            fallback_kind = "basic"
        elif directives.card_type == "visual":
            image_policy = AnkiGenerationGraph._image_policy_for_asset_plan(image_plan)
            fallback_kind = "basic"

        generation_requested = image_policy in ("generate", "reference")
        media_available = bool(image_plan.uses_uploaded_media or image_plan.reference_images)
        generation_available = generation_requested and can_generate
        visual_possible = (image_policy == "reuse_user_image" and media_available) or generation_available

        if directives.card_type == "visual":
            if visual_possible:
                card_kind = "visual_basic"
            else:
                card_kind = "basic"
                image_policy = "none"
                constraints.append("visual_downgraded_unavailable")
        elif card_kind == "visual_basic" and not visual_possible:
            card_kind = fallback_kind
            image_policy = "none"
            constraints.append("visual_downgraded_unavailable")
        elif card_kind != "visual_basic" and image_policy in ("generate", "reference"):
            image_policy = "none"

        return CardBuildPlan(
            card_kind=card_kind,
            image_policy=image_policy,
            count=AnkiGenerationGraph._target_count(plan, directives),
            source_facts=plan.source_facts,
            study_goal=plan.study_goal or "Create focused Anki flashcards from the supplied content",
            visual_rationale=plan.visual_rationale if card_kind == "visual_basic" else None,
            fallback_kind=fallback_kind,
            user_constraints_applied=constraints,
        )

    @staticmethod
    def _target_count(plan: CardBuildPlan, directives: AnkiDirectiveConstraints) -> Optional[int]:
        if directives.count is not None:
            return directives.count
        if plan.count is None:
            return None
        return max(1, min(plan.count, 3))

    @staticmethod
    def _scenario_strategy(
        plan: CardBuildPlan,
        directives: AnkiDirectiveConstraints,
    ) -> Literal["split", "merge", "count"]:
        if directives.strategy == "merge":
            return "merge"
        if directives.strategy == "count" or plan.count is not None:
            return "count"
        return directives.strategy

    @staticmethod
    def _image_plan_for_build_plan(
        plan: CardBuildPlan,
        current_plan: ImageAssetPlan,
        uploaded_image_count: int,
    ) -> ImageAssetPlan:
        if plan.image_policy == "none":
            return ImageAssetPlan(
                image_role="source_content_only" if uploaded_image_count else "ignore_media",
                rationale="Card planner chose text/cloze without embedded image media",
            )
        if plan.image_policy == "reuse_user_image":
            if current_plan.uses_uploaded_media:
                return current_plan
            return ImageAssetPlan(
                image_role="reuse_user_image",
                candidate_back_images=list(range(uploaded_image_count)),
                rationale=plan.visual_rationale or current_plan.rationale or "Card planner chose uploaded image media",
            )
        if plan.image_policy == "reference":
            return ImageAssetPlan(
                image_role="use_as_reference",
                reference_images=current_plan.reference_images or list(range(uploaded_image_count)),
                rationale=plan.visual_rationale or current_plan.rationale or "Card planner chose uploaded images as references",
            )
        if plan.image_policy == "generate":
            return ImageAssetPlan(
                image_role="generate_new_visual",
                rationale=plan.visual_rationale or current_plan.rationale or "Card planner chose generated visual media",
            )

        return current_plan

    @staticmethod
    def _route_card_kind(state: AnkiGraphState) -> Literal["basic", "cloze", "visual_basic"]:
        return state["build_plan"].card_kind

    async def _prepare_text_scenario(self, state: AnkiGraphState) -> Dict[str, Any]:
        directives = self._directives_for_planning(state)
        plan = state["build_plan"]
        planning_source = self._source_for_planning(state)
        if self.text_scenario_planner:
            scenario = await self.text_scenario_planner.plan(planning_source, directives, plan)
            scenario.strategy = self._scenario_strategy(plan, directives)
            scenario.count = plan.count
            if directives.guide and not scenario.guide:
                scenario.guide = directives.guide
        else:
            scenario = TextCardScenario(
                source_content=self._source_content_for_scenario(state),
                guide=directives.guide,
                strategy=self._scenario_strategy(plan, directives),
                count=plan.count,
            )
        return {"text_scenario": scenario, "validation_error": ""}

    async def _prepare_cloze_scenario(self, state: AnkiGraphState) -> Dict[str, Any]:
        directives = self._directives_for_planning(state)
        plan = state["build_plan"]
        planning_source = self._source_for_planning(state)
        if self.cloze_scenario_planner:
            scenario = await self.cloze_scenario_planner.plan(planning_source, directives, plan)
            scenario.count = plan.count
            if directives.guide and not scenario.guide:
                scenario.guide = directives.guide
        else:
            scenario = ClozeCardScenario(
                source_content=self._source_content_for_scenario(state),
                guide=directives.guide,
                count=plan.count,
            )
        return {"cloze_scenario": scenario, "validation_error": ""}

    async def _prepare_visual_scenario(self, state: AnkiGraphState) -> Dict[str, Any]:
        image_plan = state["image_asset_plan"]
        directives = self._directives_for_planning(state)
        plan = state["build_plan"]
        planning_source = self._source_for_planning(state)
        if self.visual_scenario_planner:
            scenario = await self.visual_scenario_planner.plan(planning_source, directives, plan, image_plan)
            if plan.fallback_kind and not scenario.fallback_kind:
                scenario.fallback_kind = plan.fallback_kind
        else:
            reference_policy = "none"
            if image_plan.image_role == "use_as_reference":
                reference_policy = "source_reference"
            elif image_plan.image_role == "generate_new_visual" and self.style_reference_images:
                reference_policy = "style_reference"

            source_content = self._source_content_for_scenario(state)
            scenario = VisualCardScenario(
                source_content=source_content,
                question_text="What should you remember from this visual?",
                answer_text=source_content,
                visual_prompt=self._build_visual_prompt(state),
                reference_image_policy=reference_policy,
                layout="text_front_image_back",
                image_count=1,
                fallback_kind=plan.fallback_kind,
            )
        return {"visual_scenario": scenario, "validation_error": ""}

    def _validate_text_scenario(self, state: AnkiGraphState) -> Dict[str, Any]:
        scenario = state.get("text_scenario")
        error = "" if scenario and scenario.source_content.strip() else "empty text scenario"
        return self._validation_update(state, error)

    def _validate_cloze_scenario(self, state: AnkiGraphState) -> Dict[str, Any]:
        scenario = state.get("cloze_scenario")
        error = "" if scenario and scenario.source_content.strip() else "empty cloze scenario"
        return self._validation_update(state, error)

    def _validate_visual_scenario(self, state: AnkiGraphState) -> Dict[str, Any]:
        scenario = state.get("visual_scenario")
        source = state["source"]
        if scenario and scenario.question_text.strip() and scenario.answer_text.strip() and (
            scenario.source_content.strip() or source.images
        ):
            return self._validation_update(state, "")
        return self._validation_update(state, "visual scenario missing source, question_text, or answer_text")

    @staticmethod
    def _validation_update(state: AnkiGraphState, error: str) -> Dict[str, Any]:
        if not error:
            return {"validation_error": ""}
        retry_counts = dict(state.get("retry_counts", {}))
        key = "scenario_validation"
        retry_counts[key] = retry_counts.get(key, 0) + 1
        return {"validation_error": error, "retry_counts": retry_counts}

    @staticmethod
    def _route_scenario_validation(state: AnkiGraphState) -> Literal["valid", "retry", "fallback"]:
        if not state.get("validation_error"):
            return "valid"
        retry_count = state.get("retry_counts", {}).get("scenario_validation", 0)
        return "retry" if retry_count <= 1 else "fallback"

    def _route_visual_validation(self, state: AnkiGraphState) -> Literal["render", "generate", "retry", "fallback"]:
        if state.get("validation_error"):
            retry_count = state.get("retry_counts", {}).get("scenario_validation", 0)
            return "retry" if retry_count <= 1 else "fallback"
        return "generate" if self._visual_needs_generation(state) else "render"

    @staticmethod
    def _route_image_generation(state: AnkiGraphState) -> Literal["valid", "fallback"]:
        return "valid" if not state.get("validation_error") else "fallback"

    @staticmethod
    def _visual_needs_generation(state: AnkiGraphState) -> bool:
        return state["build_plan"].image_policy in ("generate", "reference")

    @staticmethod
    def _source_content_for_scenario(state: AnkiGraphState) -> str:
        source = state.get("cleaned_content", "").strip()
        if source:
            return source
        if state["source"].images:
            return "Uploaded image source. Use the attached image and extracted OCR/description as the study source."
        return state["source"].content.strip()

    @staticmethod
    def _source_for_planning(state: AnkiGraphState) -> ContentSource:
        """Pass a directive-cleaned source to AI planning nodes while preserving image metadata."""
        return state["source"].model_copy(update={"content": AnkiGenerationGraph._source_content_for_scenario(state)})

    @staticmethod
    def _directives_for_planning(state: AnkiGraphState) -> AnkiDirectiveConstraints:
        feedback = (state.get("quality_feedback") or "").strip()
        directives = state["directives"]
        if not feedback:
            return directives
        guide = directives.guide or ""
        joined = f"{guide}\nQuality feedback for retry: {feedback}".strip()
        return directives.model_copy(update={"guide": joined})

    def _build_visual_prompt(self, state: AnkiGraphState) -> str:
        source = AnkiGenerationGraph._source_content_for_scenario(state)
        return self.image_prompt_policy.build_visual_scenario_prompt(source, state.get("directives"))

    def _build_image_generation_prompt(
        self,
        state: AnkiGraphState,
        reference_image_count: int,
    ) -> str:
        scenario = state["visual_scenario"]
        return self.image_prompt_policy.build_generation_prompt(
            scenario,
            ImagePromptContext(
                provider=self.image_prompt_policy.image_provider,
                reference_image_policy=scenario.reference_image_policy,
                reference_image_count=reference_image_count,
                layout=scenario.layout,
                image_role=self._generated_media_role(scenario.layout),
            ),
        )

    def _reference_image_paths(self, state: AnkiGraphState, output_dir: str) -> tuple[list[str], list[str]]:
        scenario = state["visual_scenario"]
        image_plan = state["image_asset_plan"]
        source = state["source"]
        reference_paths = []
        temp_paths = []

        for style_reference_image in self.style_reference_images:
            if os.path.exists(style_reference_image):
                reference_paths.append(style_reference_image)
            else:
                logger.warning("style_reference_image_missing path=%s", style_reference_image)

        if scenario.reference_image_policy != "source_reference":
            return reference_paths, temp_paths

        os.makedirs(output_dir, exist_ok=True)
        for image_index in image_plan.reference_images:
            if image_index >= len(source.images):
                continue
            content_image = source.images[image_index]
            if not content_image.image_data:
                continue
            basename = f"reference_{uuid.uuid4().hex}.jpg"
            path = os.path.join(output_dir, basename)
            with open(path, "wb") as fh:
                fh.write(content_image.image_data)
            reference_paths.append(path)
            temp_paths.append(path)

        return reference_paths, temp_paths

    def _render_text_or_cloze(self, state: AnkiGraphState) -> Dict[str, Any]:
        directives = state["directives"]
        plan = state["build_plan"]
        repair_guidance = state.get("quality_feedback")
        if plan.card_kind == "cloze":
            scenario = state.get("cloze_scenario")
            if not scenario:
                raise ParsingError("Missing cloze scenario for render")
            rendered = self.cloze_renderer.render(
                scenario,
                state["image_asset_plan"],
                self._scenario_strategy(plan, directives),
                repair_guidance=repair_guidance,
            )
        else:
            scenario = state.get("text_scenario")
            if not scenario:
                raise ParsingError("Missing text scenario for render")
            rendered = self.text_renderer.render(
                scenario,
                state["image_asset_plan"],
                self._scenario_strategy(plan, directives),
                repair_guidance=repair_guidance,
            )
        rendered = self._apply_planned_fallback_metadata(state, rendered)
        return {"rendered": rendered, "validation_error": ""}

    def _scenario_for_evaluation(self, state: AnkiGraphState) -> Any:
        plan = state.get("build_plan")
        if not plan:
            return state.get("text_scenario") or state.get("cloze_scenario") or state.get("visual_scenario")
        if plan.card_kind == "cloze":
            return state.get("cloze_scenario")
        if plan.card_kind == "visual_basic":
            return state.get("visual_scenario")
        return state.get("text_scenario")

    def _scenario_for_render_repair(self, state: AnkiGraphState) -> Any:
        scenario = self._scenario_for_evaluation(state)
        if not scenario:
            raise ParsingError("Missing scenario for text/cloze render")
        return scenario

    async def _generate_image(self, state: AnkiGraphState) -> Dict[str, Any]:
        directives = state["directives"]
        if not self._can_generate_images(directives):
            return {"validation_error": self._image_generation_unavailable_reason(directives)}
        retry_counts = dict(state.get("retry_counts", {}))
        image_generations = retry_counts.get("image_generation", 0)
        if image_generations >= self.max_image_generations_per_run:
            return {
                "validation_error": "image generation run limit reached",
                "retry_counts": retry_counts,
            }
        retry_counts["image_generation"] = image_generations + 1

        scenario = state["visual_scenario"]
        source = state["source"]
        output_dir = os.path.join(self.generated_media_root, str(source.user_id))
        reference_paths, temp_reference_paths = self._reference_image_paths(state, output_dir)
        workflow_context = state.get("workflow_context")
        request = ImageGenerationRequest(
            prompt=self._build_image_generation_prompt(state, len(reference_paths)),
            output_dir=output_dir,
            output_basename=f"{uuid.uuid4().hex}.{self.image_output_format}",
            model=self.image_model,
            size=self.image_size,
            quality=self.image_quality,
            output_format=self.image_output_format,
            reference_image_paths=reference_paths,
            style_reference_version=self.style_reference_version or None,
            workflow_id=getattr(workflow_context, "workflow_id", None),
        )
        try:
            image = await self.image_generator.generate(request)
        finally:
            for path in temp_reference_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass

        media = GeneratedMedia(
            path=image.path,
            basename=image.basename,
            source="generated",
            role=self._generated_media_role(scenario.layout),
            metadata={
                "provider": image.provider or "",
                "prompt_policy_provider": self.image_prompt_policy.image_provider,
                "model": image.model,
                "size": image.size,
                "quality": image.quality,
                "output_format": image.output_format,
                "reference_image_count": str(image.reference_image_count),
                "style_reference_version": image.style_reference_version or "",
                "request_id": image.request_id or "",
                "estimated_usd": str(image.estimated_usd or ""),
                "comparison_alternatives": json.dumps(
                    image.usage_metadata.get("comparison_alternatives", []),
                    sort_keys=True,
                ),
                "comparison_errors": json.dumps(
                    image.usage_metadata.get("comparison_errors", []),
                    sort_keys=True,
                ),
            },
        )
        return {"generated_media": [media], "validation_error": "", "retry_counts": retry_counts}

    @staticmethod
    def _generated_media_role(layout: str) -> Literal["front", "back", "both"]:
        if layout in ("image_front_text_back", "image_front_image_back"):
            return "front"
        if layout == "image_both_sides":
            return "both"
        return "back"

    def _render_visual(self, state: AnkiGraphState) -> Dict[str, Any]:
        directives = state["directives"]
        plan = state["build_plan"]
        scenario = state["visual_scenario"]
        rendered = self.visual_renderer.render(
            scenario,
            state["image_asset_plan"],
            self._scenario_strategy(plan, directives),
            plan.count,
            list(state.get("generated_media", [])),
            repair_guidance=state.get("quality_feedback"),
        )
        return {"rendered": rendered, "validation_error": ""}

    async def _generate_voice(self, state: AnkiGraphState) -> Dict[str, Any]:
        directives = state["directives"]
        rendered = state.get("rendered")
        if not rendered or not directives.language_voice:
            return {"validation_error": ""}

        existing_voice_media = list(state.get("voice_generated_media", []))
        if existing_voice_media:
            return {
                "rendered": self._attach_audio_to_rendered_cards(rendered, existing_voice_media),
                "voice_generated_media": existing_voice_media,
                "validation_error": "",
            }

        retry_counts = dict(state.get("retry_counts", {}))
        voice_generations = retry_counts.get("voice_generation", 0)
        if voice_generations >= self.max_voice_generations_per_run:
            return {
                "rendered": self._with_voice_fallback_note(rendered, "voice generation run limit reached"),
                "validation_error": "",
                "retry_counts": retry_counts,
            }
        if not self._can_generate_voice(directives):
            return {
                "rendered": self._with_voice_fallback_note(rendered, self._voice_generation_unavailable_reason()),
                "validation_error": "",
                "retry_counts": retry_counts,
            }

        voice_text = self._voice_text_for_state(state)
        if not voice_text:
            return {
                "rendered": self._with_voice_fallback_note(rendered, "voice text is empty"),
                "validation_error": "",
                "retry_counts": retry_counts,
            }

        retry_counts["voice_generation"] = voice_generations + 1
        source = state["source"]
        output_dir = os.path.join(self.generated_media_root, str(source.user_id))
        workflow_context = state.get("workflow_context")
        request = VoiceGenerationRequest(
            text=voice_text,
            output_dir=output_dir,
            output_basename=f"{uuid.uuid4().hex}.mp3",
            model=self.voice_model,
            output_format=self.voice_output_format,
            workflow_id=getattr(workflow_context, "workflow_id", None),
            metadata={
                "source_language": directives.source_language or "",
                "target_language": directives.target_language or "pt",
            },
        )
        try:
            audio = await self.voice_generator.generate(request)
        except Exception as exc:
            logger.warning("Anki voice generation failed: %s", exc)
            return {
                "rendered": self._with_voice_fallback_note(rendered, str(exc)),
                "validation_error": "",
                "retry_counts": retry_counts,
            }

        media = GeneratedMedia(
            path=audio.path,
            basename=audio.basename,
            source="generated",
            role="audio",
            metadata={
                "provider": audio.provider,
                "model": audio.model,
                "voice_id": audio.voice_id,
                "output_format": audio.output_format,
                "request_id": audio.request_id or "",
                "character_count": str(audio.character_count),
                "source_language": directives.source_language or "",
                "target_language": directives.target_language or "pt",
            },
        )
        return {
            "rendered": self._attach_audio_to_rendered_cards(rendered, [media]),
            "voice_generated_media": [media],
            "validation_error": "",
            "retry_counts": retry_counts,
        }

    @staticmethod
    def _attach_audio_to_rendered_cards(
        rendered: RenderedCardSet,
        audio_media: list[GeneratedMedia],
    ) -> RenderedCardSet:
        if not audio_media:
            return rendered
        sound_html = "".join(f"<br>[sound:{media.basename}]" for media in audio_media)
        cards = []
        for card in rendered.cards:
            if getattr(card, "type", "basic") == "cloze":
                cards.append(card.model_copy(update={"text": f"{card.text or ''}{sound_html}"}))
            else:
                cards.append(card.model_copy(update={"answer": f"{card.answer or ''}{sound_html}"}))
        media = [*rendered.generated_media]
        existing = {item.basename for item in media}
        media.extend(item for item in audio_media if item.basename not in existing)
        return rendered.model_copy(update={"cards": cards, "generated_media": media})

    @staticmethod
    def _with_voice_fallback_note(rendered: RenderedCardSet, reason: str) -> RenderedCardSet:
        reason = (reason or "voice generation failed").strip()
        note = f"voice fallback: {reason}"
        current = rendered.fallback_reason or ""
        fallback_reason = f"{current}; {note}" if current else note
        return rendered.model_copy(update={"fallback_reason": fallback_reason})

    @staticmethod
    def _voice_text_for_state(state: AnkiGraphState) -> str:
        scenario = state.get("visual_scenario")
        if scenario and (scenario.voice_text or "").strip():
            return scenario.voice_text.strip()
        if scenario and (scenario.answer_text or "").strip():
            return scenario.answer_text.strip()
        rendered = state.get("rendered")
        if rendered and rendered.cards:
            return (rendered.cards[0].answer or rendered.cards[0].text or "").strip()
        return ""

    def _validate_rendered_cards(self, state: AnkiGraphState) -> Dict[str, Any]:
        rendered = state.get("rendered")
        if not rendered or not rendered.cards:
            return {"validation_error": "no rendered cards"}
        for card in rendered.cards:
            if getattr(card, "type", "basic") == "cloze":
                if "{{c" not in (card.text or ""):
                    return {"validation_error": "cloze card without deletion"}
            elif not ((card.question or "").strip() or (card.text or "").strip()):
                return {"validation_error": "card without front"}
        return {"validation_error": ""}

    @staticmethod
    def _route_rendered_validation(state: AnkiGraphState) -> Literal["valid", "fallback"]:
        if not state.get("validation_error"):
            return "valid"
        rendered = state.get("rendered")
        if rendered and rendered.fallback_used:
            raise ParsingError(f"Fallback card failed validation: {state['validation_error']}")
        return "fallback"

    async def _evaluate_rendered_cards(self, state: AnkiGraphState) -> Dict[str, Any]:
        rendered = state["rendered"]
        if rendered.fallback_used and not self.evaluate_fallback_cards:
            evaluation = RenderedCardEvaluation(accepted=True)
            return {
                "quality_evaluation": evaluation,
                "evaluation_decision": self._evaluation_decision_for_rendered(evaluation, state),
                "validation_error": "",
                "quality_feedback": "",
            }
        if not self.enable_quality_evaluation or not self.quality_evaluator:
            evaluation = RenderedCardEvaluation(accepted=True)
            return {
                "quality_evaluation": evaluation,
                "evaluation_decision": self._evaluation_decision_for_rendered(evaluation, state),
                "validation_error": "",
                "quality_feedback": "",
            }

        evaluation = await self.quality_evaluator.evaluate(
            self._source_for_planning(state),
            state["directives"],
            state["build_plan"],
            self._scenario_for_evaluation(state),
            rendered,
        )
        if evaluation.accepted:
            return {
                "quality_evaluation": evaluation,
                "evaluation_decision": self._evaluation_decision_for_rendered(evaluation, state),
                "validation_error": "",
                "quality_feedback": "",
            }

        retry_counts = dict(state.get("retry_counts", {}))
        retry_counts["quality_validation"] = retry_counts.get("quality_validation", 0) + 1
        issue_text = "; ".join(evaluation.issues) or evaluation.guidance or "quality rejected rendered cards"
        update = {
            "quality_evaluation": evaluation,
            "evaluation_decision": self._evaluation_decision_for_rendered(
                evaluation,
                state,
                quality_attempt=retry_counts["quality_validation"],
            ),
            "validation_error": issue_text,
            "quality_feedback": evaluation.guidance or issue_text,
            "retry_counts": retry_counts,
        }
        if evaluation.repair_strategy in ("retry_card_plan", "retry_scenario", "fallback"):
            self._cleanup_generated_media(rendered.generated_media)
            update["generated_media"] = []
            update["voice_generated_media"] = []
            update["rendered"] = None
        return update

    def _evaluation_decision_for_rendered(
        self,
        evaluation: RenderedCardEvaluation,
        state: AnkiGraphState,
        *,
        quality_attempt: int = 0,
    ) -> EvaluationDecision:
        if evaluation.accepted:
            return EvaluationDecision(
                action="accept",
                rationale="Rendered card set passed quality evaluation.",
                confidence=1.0,
            )

        plan = state.get("build_plan")
        card_kind = getattr(plan, "card_kind", "basic")
        issue_text = "; ".join(evaluation.issues) or evaluation.guidance or "quality rejected rendered cards"
        criticism = CriticismEnvelope(
            observed=issue_text,
            expected=(
                "A production-ready card set should cover the selected source facts, keep the "
                "front answerable without leaking the answer, and use the selected media/card "
                "strategy without dropping important material."
            ),
            severity=self._generic_criticism_severity(evaluation.severity),
            target_capability=None,
            user_visible_effect=issue_text,
        )

        if quality_attempt > self.max_quality_repairs_per_run:
            return EvaluationDecision(
                action="fallback",
                rationale="Quality repair/retrace budget exhausted.",
                target_capability="fallback_to_text",
                criticism=criticism.model_copy(update={"target_capability": "fallback_to_text"}),
                user_visible_effect=issue_text,
                confidence=0.95,
            )

        if evaluation.repair_strategy == "repair_render":
            if card_kind == "visual_basic":
                target = "prepare_visual_scenario"
                return EvaluationDecision(
                    action="retrace_to",
                    rationale="Visual render repair needs a revised visual scenario before rerendering.",
                    target_capability=target,
                    retrace_to=target,
                    criticism=criticism.model_copy(update={"target_capability": target}),
                    user_visible_effect=issue_text,
                    confidence=0.9,
                )
            return EvaluationDecision(
                action="repair",
                rationale="Renderer can repair the card using evaluator guidance.",
                target_capability="repair_rendered_cards",
                criticism=criticism.model_copy(update={"target_capability": "repair_rendered_cards"}),
                user_visible_effect=issue_text,
                confidence=0.9,
            )

        if evaluation.repair_strategy == "retry_card_plan":
            return EvaluationDecision(
                action="retrace_to",
                rationale="The selected card type/count/fact coverage needs to be planned again.",
                target_capability="plan_card_type",
                retrace_to="plan_card_type",
                criticism=criticism.model_copy(update={"target_capability": "plan_card_type"}),
                user_visible_effect=issue_text,
                confidence=0.9,
            )

        if evaluation.repair_strategy == "retry_scenario":
            target = self._scenario_target_for_card_kind(card_kind)
            return EvaluationDecision(
                action="retrace_to",
                rationale="Scenario needs revision before rendering can produce a good card.",
                target_capability=target,
                retrace_to=target,
                criticism=criticism.model_copy(update={"target_capability": target}),
                user_visible_effect=issue_text,
                confidence=0.9,
            )

        return EvaluationDecision(
            action="fallback",
            rationale="Evaluator requested fallback or no targeted repair strategy was available.",
            target_capability="fallback_to_text",
            criticism=criticism.model_copy(update={"target_capability": "fallback_to_text"}),
            user_visible_effect=issue_text,
            confidence=0.8,
        )

    @staticmethod
    def _scenario_target_for_card_kind(card_kind: str) -> str:
        if card_kind == "cloze":
            return "prepare_cloze_scenario"
        if card_kind == "visual_basic":
            return "prepare_visual_scenario"
        return "prepare_text_scenario"

    @staticmethod
    def _generic_criticism_severity(severity: str) -> Literal["low", "medium", "high", "blocking"]:
        if severity == "none":
            return "low"
        if severity == "high":
            return "blocking"
        if severity in ("low", "medium"):
            return severity
        return "medium"

    def _route_quality_evaluation(
        self,
        state: AnkiGraphState,
    ) -> Literal[
        "valid",
        "retry_card_plan",
        "repair_render",
        "retry_text_scenario",
        "retry_cloze_scenario",
        "retry_visual_scenario",
        "fallback",
    ]:
        evaluation = state.get("quality_evaluation")
        if not evaluation or evaluation.accepted:
            return "valid"
        rendered = state.get("rendered")
        if rendered and rendered.fallback_used:
            raise ParsingError(f"Fallback card failed quality evaluation: {state['validation_error']}")
        if state.get("retry_counts", {}).get("quality_validation", 0) > self.max_quality_repairs_per_run:
            return "fallback"
        if evaluation.repair_strategy == "retry_card_plan":
            return "retry_card_plan"
        if state["build_plan"].card_kind == "visual_basic" and evaluation.repair_strategy == "repair_render":
            return "retry_visual_scenario"
        if evaluation.repair_strategy == "repair_render":
            return "repair_render"
        if evaluation.repair_strategy == "retry_scenario":
            plan = state["build_plan"]
            if plan.card_kind == "cloze":
                return "retry_cloze_scenario"
            if plan.card_kind == "visual_basic":
                return "retry_visual_scenario"
            return "retry_text_scenario"
        return "fallback"

    def _repair_rendered_cards(self, state: AnkiGraphState) -> Dict[str, Any]:
        # The actual repair is performed by the next render node. This node keeps repair routing
        # explicit in the trace and preserves evaluator guidance for the renderer.
        scenario = self._scenario_for_render_repair(state)
        feedback = state.get("quality_feedback") or state.get("validation_error") or ""
        if feedback:
            current = getattr(scenario, "rendering_guide", None) or ""
            scenario = scenario.model_copy(
                update={"rendering_guide": f"{current}\nQuality repair: {feedback}".strip()}
            )
        plan = state["build_plan"]
        if plan.card_kind == "cloze":
            return {"cloze_scenario": scenario, "rendered": None, "validation_error": ""}
        if plan.card_kind == "visual_basic":
            return {"visual_scenario": scenario, "rendered": None, "validation_error": ""}
        return {"text_scenario": scenario, "rendered": None, "validation_error": ""}

    @staticmethod
    def _cleanup_generated_media(media: list[GeneratedMedia]) -> None:
        artifacts = [
            WorkflowArtifact(
                path=item.path,
                kind="media",
                source=item.source,
                owner_node="generate_voice" if item.role == "audio" else "generate_image",
                cleanup_on_failure=item.source == "generated",
                metadata={"role": item.role, **item.metadata},
            )
            for item in media
        ]
        cleanup_artifacts(artifacts)

    @staticmethod
    def _route_render_repair(state: AnkiGraphState) -> Literal["render_text_or_cloze", "render_visual"]:
        return "render_visual" if state["build_plan"].card_kind == "visual_basic" else "render_text_or_cloze"

    def _fallback_to_text(self, state: AnkiGraphState) -> Dict[str, Any]:
        directives = state["directives"]
        plan = state.get("build_plan")
        content = self._source_content_for_scenario(state)
        strategy = self._scenario_strategy(plan, directives) if plan else directives.strategy
        count = plan.count if plan else directives.count
        rendered = self.fallback_renderer.render(
            content,
            directives,
            plan,
            strategy,
            count,
            repair_guidance=state.get("quality_feedback"),
        )
        rendered = rendered.model_copy(update={"fallback_reason": self._fallback_reason_for_state(state)})
        return {"rendered": rendered, "image_asset_plan": rendered.image_asset_plan, "validation_error": ""}

    def _apply_planned_fallback_metadata(
        self,
        state: AnkiGraphState,
        rendered: RenderedCardSet,
    ) -> RenderedCardSet:
        if "visual_downgraded_unavailable" not in state["build_plan"].user_constraints_applied:
            return rendered
        return rendered.model_copy(
            update={
                "fallback_used": True,
                "fallback_reason": self._fallback_reason_for_state(state),
            }
        )

    def _fallback_reason_for_state(self, state: AnkiGraphState) -> str:
        reason = (state.get("validation_error") or "").strip()
        plan = state.get("build_plan")
        if plan and "visual_downgraded_unavailable" in plan.user_constraints_applied:
            return self._image_generation_unavailable_reason(state.get("directives"))
        if reason:
            return reason
        return "text fallback"

    @staticmethod
    def _package_cards(state: AnkiGraphState) -> Dict[str, Any]:
        # Packaging is still performed by AnkiProcessor so Telegram delivery and buffer behavior stay
        # unchanged. This node marks the graph as ready for packaging.
        return {"validation_error": ""}
