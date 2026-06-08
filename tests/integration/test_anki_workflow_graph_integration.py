"""Small API-backed graph-integration checks for the Anki workflow graph.

These tests intentionally use the current mini text model. The parametrized graph-flow cases keep
generated images disabled; the dedicated image smoke cases spend low-quality GPT Image 2 calls.
"""

import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from config import Config
from models.anki import AnkiCard
from services.anki_card_service import AnkiCardService
from services.content.anki_card_set_planner import AnkiCardSetPlanner
from services.content.anki_generation_graph import AnkiGenerationGraph
from services.content.anki_scenario_planners import (
    ClozeScenarioPlanner,
    TextScenarioPlanner,
    VisualScenarioPlanner,
)
from services.content.anki_quality_evaluator import AnkiRenderedCardEvaluator
from services.content.anki_source import build_content_source
from ai_workflow_engine.image_generation import OpenAIImageGenerator

pytestmark = pytest.mark.integration

GRAPH_TEXT_MODEL = os.getenv("ANKI_GRAPH_TEST_MODEL", "gpt-5.4-mini")


def _card_text(card: AnkiCard) -> str:
    if getattr(card, "type", "basic") == "cloze":
        return card.text or ""
    return f"{card.question or ''}\n{card.answer or ''}"


def _configure_graph_models() -> Config:
    if not Config.OPENAI_API_KEY or Config.OPENAI_API_KEY == "test_key_not_used":
        pytest.skip("OPENAI_API_KEY not configured")
    Config.TASK_PARSING_MODEL = "gpt-5.4-mini"
    Config.ANKI_CARD_MODEL = GRAPH_TEXT_MODEL
    Config.ANKI_DECISION_MODEL = GRAPH_TEXT_MODEL
    Config.ANKI_SCENARIO_MODEL = GRAPH_TEXT_MODEL
    Config.ANKI_RENDER_MODEL = GRAPH_TEXT_MODEL
    Config.ANKI_QUALITY_MODEL = GRAPH_TEXT_MODEL
    Config.ANKI_IMAGE_GENERATION_ENABLED = False
    Config.ANKI_IMAGE_MODEL = "gpt-image-2"
    Config.ANKI_IMAGE_SIZE = os.getenv("ANKI_GRAPH_TEST_IMAGE_SIZE", "1536x1024")
    Config.ANKI_IMAGE_QUALITY = os.getenv("ANKI_GRAPH_TEST_IMAGE_QUALITY", "low")
    Config.ANKI_IMAGE_OUTPUT_FORMAT = "png"
    return Config()


def _production_graph(
    service: AnkiCardService,
    config: Config,
    tmp_path,
    enable_image_generation: bool = False,
    enable_auto_image_generation: bool = False,
    enable_quality_evaluation: bool = False,
) -> AnkiGenerationGraph:
    image_generator = OpenAIImageGenerator(config) if enable_image_generation else None
    quality_evaluator = AnkiRenderedCardEvaluator(config, inspect_images=False) if enable_quality_evaluation else None
    return AnkiGenerationGraph(
        service,
        card_set_planner=AnkiCardSetPlanner(config),
        text_scenario_planner=TextScenarioPlanner(config),
        cloze_scenario_planner=ClozeScenarioPlanner(config),
        visual_scenario_planner=VisualScenarioPlanner(config),
        quality_evaluator=quality_evaluator,
        enable_quality_evaluation=enable_quality_evaluation,
        image_generator=image_generator,
        enable_image_generation=enable_image_generation,
        enable_auto_image_generation=enable_auto_image_generation,
        max_image_generations_per_run=1,
        max_quality_repairs_per_run=1,
        image_model=Config.ANKI_IMAGE_MODEL,
        image_size=Config.ANKI_IMAGE_SIZE,
        image_quality=Config.ANKI_IMAGE_QUALITY,
        image_output_format=Config.ANKI_IMAGE_OUTPUT_FORMAT,
        generated_media_root=str(tmp_path / "generated_media"),
    )


def _assert_package_writes(
    service: AnkiCardService,
    cards: list[AnkiCard],
    tmp_path,
    name: str,
    media_files: list[str] | None = None,
) -> Path:
    out_path = tmp_path / f"{name}.apkg"
    service.build_package(cards, deck_name=f"Graph Integration - {name}", output_path=str(out_path), media_files=media_files)
    assert zipfile.is_zipfile(out_path)
    return out_path


def _manual_review_dir() -> Path | None:
    configured = os.getenv("ANKI_MANUAL_REVIEW_DIR")
    candidates = [Path(configured)] if configured else [Path("/app/test-results/anki-manual-review")]
    for path in candidates:
        parent = path.parent
        if parent.exists():
            path.mkdir(parents=True, exist_ok=True)
            return path
    return None


def _preserve_manual_review_artifacts(
    name: str,
    source,
    rendered,
    package_path: Path,
) -> None:
    review_root = _manual_review_dir()
    if not review_root:
        return

    review_dir = review_root / name
    review_dir.mkdir(parents=True, exist_ok=True)
    copied_media = []
    for media in rendered.generated_media:
        copied_image = review_dir / media.basename
        shutil.copy2(media.path, copied_image)
        copied_media.append(
            {
                "basename": media.basename,
                "metadata": media.metadata,
                "copied_image": str(copied_image),
            }
        )

    copied_package = review_dir / f"{name}.apkg"
    shutil.copy2(package_path, copied_package)
    (review_dir / "card_summary.json").write_text(
        json.dumps(
            {
                "source": source.content,
                "cards": [card.model_dump() for card in rendered.cards],
                "image_asset_plan": rendered.image_asset_plan.model_dump(),
                "fallback_used": rendered.fallback_used,
                "generated_media": copied_media,
                "package": str(copied_package),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.api
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "message", "expected_kind", "max_cards"),
    [
        (
            "auto_definition",
            "Photosynthesis lets green plants use sunlight, water, and carbon dioxide to produce glucose and oxygen.",
            None,
            3,
        ),
        (
            "forced_cloze",
            "[i cloze] The pitot-static system measures impact pressure and static pressure to derive airspeed.",
            "cloze",
            1,
        ),
        (
            "visual_disabled_fallback",
            "Bernoulli principle: faster fluid flow corresponds to lower static pressure [i visual gen] make it funny[i]",
            "basic",
            2,
        ),
    ],
)
async def test_anki_workflow_small_real_graph_integration(name, message, expected_kind, max_cards, tmp_path):
    config = _configure_graph_models()
    service = AnkiCardService(config)
    graph = _production_graph(service, config, tmp_path, enable_image_generation=False)

    source = build_content_source([(name, message)], user_id=12345, owner_name=name)
    rendered = await graph.run(source)

    assert 1 <= len(rendered.cards) <= max_cards
    assert not rendered.generated_media

    if expected_kind == "cloze":
        assert len(rendered.cards) == 1
        assert rendered.cards[0].type == "cloze"
        assert "{{c" in rendered.cards[0].text
    elif expected_kind == "basic":
        assert all(getattr(card, "type", "basic") == "basic" for card in rendered.cards)

    joined = "\n".join(_card_text(card) for card in rendered.cards)
    assert "[i" not in joined
    assert "make it funny" not in joined.lower()

    if name == "visual_disabled_fallback":
        assert rendered.image_asset_plan.image_role == "ignore_media"

    _assert_package_writes(service, rendered.cards, tmp_path, name)


@pytest.mark.api
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "message", "expected_kind", "max_cards", "expected_terms"),
    [
        (
            "edge_exact_acronym_basic",
            "In REST APIs, CRUD stands for Create, Read, Update, and Delete.",
            "basic",
            1,
            ("crud", "create", "read", "update", "delete"),
        ),
        (
            "edge_forced_cloze_numbers",
            "[i cloze] The sodium-potassium pump exports three Na+ ions and imports two K+ ions per ATP hydrolyzed.",
            "cloze",
            1,
            ("sodium", "potassium", "atp"),
        ),
        (
            "edge_abstract_legal_basic",
            "Strict liability means a defendant can be legally responsible for harm even without proving intent or negligence.",
            "basic",
            1,
            ("strict liability", "intent", "negligence"),
        ),
        (
            "edge_multi_fact_limit",
            "Blood components: red blood cells carry oxygen with hemoglobin. Platelets form clots. White blood cells fight infection.",
            None,
            3,
            ("oxygen", "platelets", "infection"),
        ),
    ],
)
async def test_anki_workflow_edge_case_real_graph_integration(
    name,
    message,
    expected_kind,
    max_cards,
    expected_terms,
    tmp_path,
):
    config = _configure_graph_models()
    service = AnkiCardService(config)
    graph = _production_graph(service, config, tmp_path, enable_image_generation=False)

    source = build_content_source([(name, message)], user_id=12345, owner_name=name)
    rendered = await graph.run(source)

    assert 1 <= len(rendered.cards) <= max_cards
    assert not rendered.generated_media

    if expected_kind == "cloze":
        assert len(rendered.cards) == 1
        assert rendered.cards[0].type == "cloze"
        assert "{{c" in rendered.cards[0].text
    elif expected_kind == "basic":
        assert all(getattr(card, "type", "basic") == "basic" for card in rendered.cards)

    joined = "\n".join(_card_text(card) for card in rendered.cards).lower()
    assert "[i" not in joined
    for term in expected_terms:
        assert term in joined

    package_path = _assert_package_writes(service, rendered.cards, tmp_path, name)
    _preserve_manual_review_artifacts(name, source, rendered, package_path)


@pytest.mark.api
@pytest.mark.asyncio
async def test_anki_workflow_real_quality_evaluator_graph_integration(tmp_path):
    config = _configure_graph_models()
    service = AnkiCardService(config)
    graph = _production_graph(service, config, tmp_path, enable_quality_evaluation=True)

    source = build_content_source(
        [("quality_eval", "In REST APIs, CRUD stands for Create, Read, Update, and Delete.")],
        user_id=12345,
        owner_name="quality_eval",
    )
    rendered = await graph.run(source)

    assert len(rendered.cards) == 1
    joined = "\n".join(_card_text(card) for card in rendered.cards).lower()
    assert "crud" in joined
    assert "create" in joined
    assert "delete" in joined
    _assert_package_writes(service, rendered.cards, tmp_path, "real_quality_evaluator")


@pytest.mark.api
@pytest.mark.image_generation
@pytest.mark.asyncio
async def test_anki_workflow_forced_generated_image_smoke(tmp_path):
    config = _configure_graph_models()
    Config.ANKI_IMAGE_GENERATION_ENABLED = True
    service = AnkiCardService(config)
    graph = AnkiGenerationGraph(
        service,
        card_set_planner=None,
        image_generator=OpenAIImageGenerator(config),
        enable_image_generation=True,
        image_model=Config.ANKI_IMAGE_MODEL,
        image_size=Config.ANKI_IMAGE_SIZE,
        image_quality=Config.ANKI_IMAGE_QUALITY,
        image_output_format=Config.ANKI_IMAGE_OUTPUT_FORMAT,
        generated_media_root=str(tmp_path / "generated_media"),
    )

    source = build_content_source(
        [
            (
                "image_graph_integration",
                "Bernoulli principle: faster fluid flow corresponds to lower static pressure "
                "[i visual gen] make a simple memorable illustration with no text[i]",
            )
        ],
        user_id=12345,
        owner_name="image_graph_integration",
    )
    rendered = await graph.run(source)

    assert len(rendered.cards) == 1
    assert len(rendered.generated_media) == 1
    media = rendered.generated_media[0]
    assert media.metadata["model"] == "gpt-image-2"
    assert media.metadata["size"] == Config.ANKI_IMAGE_SIZE
    assert media.metadata["quality"] == Config.ANKI_IMAGE_QUALITY
    assert os.path.exists(media.path)
    assert os.path.getsize(media.path) > 1000

    card_markup = "\n".join(_card_text(card) for card in rendered.cards)
    assert media.basename in card_markup
    assert "[i" not in card_markup

    package_path = _assert_package_writes(
        service,
        rendered.cards,
        tmp_path,
        "real_generated_image",
        media_files=[media.path],
    )

    _preserve_manual_review_artifacts("forced_generated_image", source, rendered, package_path)


@pytest.mark.api
@pytest.mark.image_generation
@pytest.mark.asyncio
async def test_anki_workflow_auto_generated_image_full_flow_graph_integration(tmp_path):
    config = _configure_graph_models()
    Config.ANKI_IMAGE_GENERATION_ENABLED = True
    service = AnkiCardService(config)
    graph = _production_graph(
        service,
        config,
        tmp_path,
        enable_image_generation=True,
        enable_auto_image_generation=True,
    )

    source = build_content_source(
        [
            (
                "auto_visual_full_flow",
                "Airplane wing lift: air moves faster over the curved upper surface than below. "
                "That creates lower pressure above and higher pressure below, so the wing is pushed upward.",
            )
        ],
        user_id=12345,
        owner_name="auto_visual_full_flow",
    )
    rendered = await graph.run(source)

    assert rendered.image_asset_plan.image_role == "generate_new_visual"
    assert len(rendered.cards) == 1
    assert len(rendered.generated_media) == 1
    media = rendered.generated_media[0]
    assert media.metadata["model"] == "gpt-image-2"
    assert media.metadata["size"] == Config.ANKI_IMAGE_SIZE
    assert media.metadata["quality"] == Config.ANKI_IMAGE_QUALITY
    assert os.path.exists(media.path)
    assert os.path.getsize(media.path) > 1000

    card_markup = "\n".join(_card_text(card) for card in rendered.cards)
    assert media.basename in card_markup
    assert "[i" not in card_markup

    package_path = _assert_package_writes(
        service,
        rendered.cards,
        tmp_path,
        "auto_generated_image_full_flow",
        media_files=[media.path],
    )
    _preserve_manual_review_artifacts("auto_generated_image_full_flow", source, rendered, package_path)


@pytest.mark.api
@pytest.mark.image_generation
@pytest.mark.asyncio
async def test_anki_workflow_auto_generated_image_countercurrent_graph_integration(tmp_path):
    config = _configure_graph_models()
    Config.ANKI_IMAGE_GENERATION_ENABLED = True
    service = AnkiCardService(config)
    graph = _production_graph(
        service,
        config,
        tmp_path,
        enable_image_generation=True,
        enable_auto_image_generation=True,
    )

    source = build_content_source(
        [
            (
                "auto_visual_countercurrent",
                "Fish gills use countercurrent exchange: water flows across the gill lamellae "
                "opposite to blood flow, maintaining an oxygen diffusion gradient along the whole exchange surface.",
            )
        ],
        user_id=12345,
        owner_name="auto_visual_countercurrent",
    )
    rendered = await graph.run(source)

    assert rendered.image_asset_plan.image_role == "generate_new_visual"
    assert len(rendered.cards) == 1
    assert len(rendered.generated_media) == 1
    media = rendered.generated_media[0]
    assert media.metadata["model"] == "gpt-image-2"
    assert media.metadata["size"] == Config.ANKI_IMAGE_SIZE
    assert media.metadata["quality"] == Config.ANKI_IMAGE_QUALITY
    assert os.path.exists(media.path)
    assert os.path.getsize(media.path) > 1000

    card_markup = "\n".join(_card_text(card) for card in rendered.cards).lower()
    assert media.basename.lower() in card_markup
    assert "countercurrent" in card_markup
    assert "[i" not in card_markup

    package_path = _assert_package_writes(
        service,
        rendered.cards,
        tmp_path,
        "auto_generated_image_countercurrent",
        media_files=[media.path],
    )
    _preserve_manual_review_artifacts("auto_generated_image_countercurrent", source, rendered, package_path)


# --------------------------------------------------------------------------------------
# Whole-flow processor check: real migrated graph -> AnkiProcessor -> Telegram delivery
# (Telegram transport mocked at the boundary; the engine + graph + packaging are real.)
# --------------------------------------------------------------------------------------

from core.interfaces import ProcessingContext  # noqa: E402
from services.content import anki_buffer  # noqa: E402
from services.content.anki_processor import AnkiProcessor  # noqa: E402


class _FakeStatusMessage:
    def __init__(self):
        self.deleted = False
        self.edits = []

    async def delete(self):
        self.deleted = True

    async def edit_text(self, text):
        self.edits.append(text)


class _FakeTelegramMessage:
    def __init__(self):
        self.chat = type("Chat", (), {"id": 999})()
        self.message_id = 4242
        self.replies = []
        self.documents = []
        self.photos = []
        self.audios = []
        self.status = _FakeStatusMessage()

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self.status

    async def reply_document(self, document, **kwargs):
        self.documents.append(
            {"filename": getattr(document, "filename", None), "caption": kwargs.get("caption", "")}
        )

    async def reply_photo(self, photo, **kwargs):
        self.photos.append({"caption": kwargs.get("caption", "")})

    async def reply_audio(self, audio, **kwargs):
        self.audios.append({"caption": kwargs.get("caption", "")})


@pytest.mark.api
@pytest.mark.asyncio
async def test_anki_processor_whole_flow_real_graph_delivers_apkg(tmp_path):
    config = _configure_graph_models()
    service = AnkiCardService(config)
    graph = _production_graph(service, config, tmp_path, enable_image_generation=False)
    user_id = 778899
    anki_buffer.clear(user_id)
    processor = AnkiProcessor(service, anki_graph=graph)
    message = _FakeTelegramMessage()

    result = await processor.process(
        ProcessingContext(
            message=message,
            thread_content=[("User", "In REST APIs, CRUD stands for Create, Read, Update, and Delete.")],
            user_id=user_id,
            owner_name="User",
        )
    )

    assert result.success is True
    assert len(graph.last_run_state["trace"]) > 0  # ran through the migrated engine
    assert len(message.documents) == 1
    assert message.documents[0]["filename"] == "flashcards.apkg"
    caption = message.documents[0]["caption"]
    assert "CRUD" in caption or "Create" in caption
    assert message.status.deleted is True
