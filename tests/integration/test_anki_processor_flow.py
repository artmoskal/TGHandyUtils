"""Processor-level Anki flow checks with Telegram I/O mocked at the boundary."""

from types import SimpleNamespace

import pytest

from config import Config
from core.interfaces import ProcessingContext
from models.anki import AnkiCard
from models.anki_workflow import GeneratedMedia, ImageAssetPlan, RenderedCardSet
from ai_workflow_engine.models import WorkflowUsageEvent, WorkflowUsageSummary
from services.anki_card_service import AnkiCardService
from services.content import anki_buffer
from services.content.anki_processor import AnkiProcessor

pytestmark = pytest.mark.unit


class FakeGraph:
    def __init__(self, rendered):
        self.rendered = rendered
        self.calls = []

    async def run(self, source, message=None):
        self.calls.append((source, message))
        return self.rendered


class FakeStatusMessage:
    def __init__(self):
        self.deleted = False
        self.edits = []

    async def delete(self):
        self.deleted = True

    async def edit_text(self, text):
        self.edits.append(text)


class FakeTelegramMessage:
    def __init__(self):
        self.chat = SimpleNamespace(id=999)
        self.message_id = 123
        self.replies = []
        self.documents = []
        self.photos = []
        self.audios = []
        self.status = FakeStatusMessage()

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self.status

    async def reply_document(self, document, **kwargs):
        self.documents.append(
            {
                "filename": getattr(document, "filename", None),
                "path": getattr(document, "path", None),
                "caption": kwargs.get("caption", ""),
                "reply_markup": kwargs.get("reply_markup"),
            }
        )

    async def reply_photo(self, photo, **kwargs):
        self.photos.append(
            {
                "filename": getattr(photo, "filename", None),
                "path": getattr(photo, "path", None),
                "caption": kwargs.get("caption", ""),
            }
        )

    async def reply_audio(self, audio, **kwargs):
        self.audios.append(
            {
                "filename": getattr(audio, "filename", None),
                "path": getattr(audio, "path", None),
                "caption": kwargs.get("caption", ""),
            }
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anki_processor_delivers_apkg_and_buffers_cards():
    user_id = 777001
    anki_buffer.clear(user_id)
    config = Config()
    service = AnkiCardService(config)
    rendered = RenderedCardSet(
        cards=[AnkiCard(question="What does CRUD stand for?", answer="Create, Read, Update, Delete")],
        image_asset_plan=ImageAssetPlan(image_role="ignore_media", rationale="No image needed"),
    )
    graph = FakeGraph(rendered)
    message = FakeTelegramMessage()
    processor = AnkiProcessor(service, anki_graph=graph)

    result = await processor.process(
        ProcessingContext(
            message=message,
            thread_content=[("User", "In REST APIs, CRUD stands for Create, Read, Update, and Delete.")],
            user_id=user_id,
            owner_name="User",
        )
    )

    try:
        assert result.success is True
        assert len(graph.calls) == 1
        assert len(message.documents) == 1
        assert message.documents[0]["filename"] == "flashcards.apkg"
        assert "What does CRUD stand for?" in message.documents[0]["caption"]
        assert "Create, Read, Update, Delete" in message.documents[0]["caption"]
        assert message.status.deleted is True
        assert anki_buffer.count(user_id) == 1
    finally:
        anki_buffer.clear(user_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anki_processor_caption_and_photo_preview_show_generated_image(tmp_path):
    user_id = 777002
    anki_buffer.clear(user_id)
    config = Config()
    service = AnkiCardService(config)
    image_path = tmp_path / "generated.png"
    image_path.write_bytes(b"fake image")
    rendered = RenderedCardSet(
        cards=[AnkiCard(question="What controls flow?", answer='A valve<br><img src="generated.png">')],
        image_asset_plan=ImageAssetPlan(image_role="generate_new_visual", rationale="Visual helps recall"),
        generated_media=[
            GeneratedMedia(
                path=str(image_path),
                basename="generated.png",
                source="generated",
                role="back",
                metadata={"provider": "openai"},
            )
        ],
    )
    graph = FakeGraph(rendered)
    message = FakeTelegramMessage()
    processor = AnkiProcessor(service, anki_graph=graph)

    result = await processor.process(
        ProcessingContext(
            message=message,
            thread_content=[("User", "[i gen] A valve controls flow.")],
            user_id=user_id,
            owner_name="User",
        )
    )

    try:
        assert result.success is True
        assert len(message.photos) == 1
        assert "Generated card image preview (back, openai)." == message.photos[0]["caption"]
        caption = message.documents[0]["caption"]
        assert "Images in Anki: 1 generated back." in caption
        assert "[back image: generated.png]" in caption
    finally:
        anki_buffer.clear(user_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anki_processor_sends_comparison_preview_without_embedding_alternative(tmp_path):
    user_id = 777005
    anki_buffer.clear(user_id)
    config = Config()
    service = AnkiCardService(config)
    primary_path = tmp_path / "primary.png"
    comparison_path = tmp_path / "comparison.png"
    primary_path.write_bytes(b"primary")
    comparison_path.write_bytes(b"comparison")
    rendered = RenderedCardSet(
        cards=[AnkiCard(question="What is QNH?", answer='Local pressure setting.<br><img src="primary.png">')],
        image_asset_plan=ImageAssetPlan(image_role="generate_new_visual", rationale="Visual helps recall"),
        generated_media=[
            GeneratedMedia(
                path=str(primary_path),
                basename="primary.png",
                source="generated",
                role="back",
                metadata={
                    "provider": "openai",
                    "comparison_alternatives": (
                        '[{"provider":"gemini","model":"gemini-3.1-flash-image",'
                        f'"path":"{comparison_path}","basename":"comparison.png"}}]'
                    ),
                },
            )
        ],
    )
    graph = FakeGraph(rendered)
    message = FakeTelegramMessage()
    processor = AnkiProcessor(service, anki_graph=graph)

    result = await processor.process(
        ProcessingContext(
            message=message,
            thread_content=[("User", "[i gen] QNH means local pressure setting.")],
            user_id=user_id,
            owner_name="User",
        )
    )

    try:
        assert result.success is True
        assert len(message.photos) == 2
        assert message.photos[0]["caption"] == "Generated card image preview (back, openai)."
        assert message.photos[1]["caption"] == "Comparison image preview (gemini)."
        assert "primary.png" in message.documents[0]["caption"]
        assert "comparison.png" not in message.documents[0]["caption"]
    finally:
        anki_buffer.clear(user_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anki_processor_caption_and_audio_preview_show_generated_voice(tmp_path):
    user_id = 777006
    anki_buffer.clear(user_id)
    config = Config()
    service = AnkiCardService(config)
    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"fake audio")
    rendered = RenderedCardSet(
        cards=[AnkiCard(question="How do you say 'привіт' in Portuguese?", answer="olá<br>[sound:voice.mp3]")],
        image_asset_plan=ImageAssetPlan(image_role="generate_new_visual", rationale="Language visual card"),
        generated_media=[
            GeneratedMedia(
                path=str(audio_path),
                basename="voice.mp3",
                source="generated",
                role="audio",
                metadata={"provider": "elevenlabs"},
            )
        ],
    )
    graph = FakeGraph(rendered)
    message = FakeTelegramMessage()
    processor = AnkiProcessor(service, anki_graph=graph)

    result = await processor.process(
        ProcessingContext(
            message=message,
            thread_content=[("User", "[i langvoice ukr->pt gen] привіт")],
            user_id=user_id,
            owner_name="User",
        )
    )

    try:
        assert result.success is True
        assert len(message.audios) == 1
        assert message.audios[0]["caption"] == "Generated pronunciation audio (elevenlabs)."
        caption = message.documents[0]["caption"]
        assert "Audio in Anki: 1 generated pronunciation." in caption
        assert "[sound:voice.mp3]" in caption
    finally:
        anki_buffer.clear(user_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anki_processor_caption_shows_fallback_used():
    user_id = 777003
    anki_buffer.clear(user_id)
    config = Config()
    service = AnkiCardService(config)
    rendered = RenderedCardSet(
        cards=[AnkiCard(question="What controls flow?", answer="A valve.")],
        image_asset_plan=ImageAssetPlan(image_role="source_content_only", rationale="Fallback to text"),
        fallback_used=True,
        fallback_reason="image generation is disabled",
    )
    graph = FakeGraph(rendered)
    message = FakeTelegramMessage()
    processor = AnkiProcessor(service, anki_graph=graph)

    result = await processor.process(
        ProcessingContext(
            message=message,
            thread_content=[("User", "[i visual gen] A valve controls flow.")],
            user_id=user_id,
            owner_name="User",
        )
    )

    try:
        assert result.success is True
        assert "Fallback used: text card (image generation is disabled)." in message.documents[0]["caption"]
    finally:
        anki_buffer.clear(user_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anki_processor_caption_can_show_usage_summary():
    user_id = 777004
    anki_buffer.clear(user_id)
    config = Config()
    config.WORKFLOW_SHOW_USAGE_IN_REPLY = True
    service = AnkiCardService(config)
    usage = WorkflowUsageSummary(
        events=[
            WorkflowUsageEvent(
                node="anki_card_renderer",
                model="unit-model",
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                estimated_usd=0.0012,
            )
        ]
    )
    rendered = RenderedCardSet(
        cards=[AnkiCard(question="What controls flow?", answer="A valve.")],
        image_asset_plan=ImageAssetPlan(image_role="ignore_media", rationale="No image"),
        usage_summary=usage,
    )
    graph = FakeGraph(rendered)
    message = FakeTelegramMessage()
    processor = AnkiProcessor(service, anki_graph=graph)

    result = await processor.process(
        ProcessingContext(
            message=message,
            thread_content=[("User", "A valve controls flow.")],
            user_id=user_id,
            owner_name="User",
        )
    )

    try:
        assert result.success is True
        caption = message.documents[0]["caption"]
        assert "AI usage:" in caption
        assert "node" in caption
        assert "cache" in caption
        assert "anki_card_renderer" in caption
        assert "txt" in caption
        assert "$0.0012" in caption
        assert "total: 1 text, 0 image, 100 in, 0 cached, 20 out, est $0.0012" in caption
    finally:
        anki_buffer.clear(user_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anki_processor_sends_engine_trace_as_plain_text_debug_message():
    # Regression: the engine trace can carry card HTML (<br>, <img>); the debug message must send
    # as plain text (parse_mode=None) so Telegram's entity parser does not reject it.
    from ai_workflow_engine.models import WorkflowTraceEvent

    user_id = 777010
    anki_buffer.clear(user_id)
    config = Config()
    config.WORKFLOW_DEBUG_TRACE_ENABLED = True
    service = AnkiCardService(config)
    rendered = RenderedCardSet(
        cards=[AnkiCard(question="Q", answer='A<br><img src="x.png">')],
        image_asset_plan=ImageAssetPlan(image_role="ignore_media"),
    )
    graph = FakeGraph(rendered)
    graph.last_run_state = {
        "trace": [
            WorkflowTraceEvent(
                node="render_visual",
                decision="generated",
                metadata={"cards_preview": 'A<br><img src="x.png">'},
            )
        ],
        "usage_summary": None,
    }
    message = FakeTelegramMessage()
    processor = AnkiProcessor(service, anki_graph=graph)

    result = await processor.process(
        ProcessingContext(
            message=message,
            thread_content=[("User", "some content")],
            user_id=user_id,
            owner_name="User",
        )
    )

    try:
        assert result.success is True
        debug = [(text, kwargs) for (text, kwargs) in message.replies if "engine trace" in text.lower()]
        assert debug, "engine trace debug message was not sent"
        assert all(kwargs.get("parse_mode") is None for _text, kwargs in debug)
    finally:
        anki_buffer.clear(user_id)
