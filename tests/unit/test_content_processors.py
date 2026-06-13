"""Unit tests for the content-processing seam (router, resolver, anki processor)."""

from unittest.mock import Mock, AsyncMock

import pytest

from core.interfaces import Intent, ProcessingContext
from models.anki import AnkiCard
from services.content.intent_resolver import IntentResolver
from services.content.anki_processor import (
    AnkiProcessor,
    TELEGRAM_DOCUMENT_CAPTION_LIMIT,
    TELEGRAM_TEXT_MESSAGE_LIMIT,
    _build_delivery_messages,
    _format_preview,
)


@pytest.mark.unit
def test_intent_resolver_default_is_reminder():
    assert IntentResolver().resolve(123) == Intent.REMINDER


@pytest.mark.unit
def test_intent_resolver_override_wins():
    assert IntentResolver().resolve(123, override=Intent.ANKI) == Intent.ANKI


@pytest.mark.unit
def test_router_maps_intents_to_processors():
    from services.content.router import get_processor
    from services.content.reminder_processor import ReminderProcessor
    assert isinstance(get_processor(Intent.REMINDER), ReminderProcessor)
    assert isinstance(get_processor(Intent.ANKI), AnkiProcessor)


@pytest.mark.unit
async def test_anki_processor_delivers_document():
    svc = Mock()
    svc.extract_cards.return_value = [
        AnkiCard(question="Capital of France?", answer="Paris"),
        AnkiCard(question="2+2?", answer="4"),
    ]
    svc.build_package.return_value = "/tmp/does_not_exist.apkg"

    message = Mock()
    message.reply = AsyncMock(return_value=Mock(delete=AsyncMock()))
    message.reply_document = AsyncMock()

    ctx = ProcessingContext(
        message=message,
        thread_content=[("User", "France facts")],
        user_id=1,
        owner_name="User",
        location=None,
    )

    result = await AnkiProcessor(svc).process(ctx)

    assert result.success
    svc.extract_cards.assert_called_once()
    svc.build_package.assert_called_once()
    message.reply_document.assert_called_once()
    # caption previews BOTH front and back so the user can check correctness
    caption = message.reply_document.call_args.kwargs["caption"]
    assert "Capital of France?" in caption  # front
    assert "Paris" in caption               # back


@pytest.mark.unit
async def test_anki_processor_uses_configured_deck_name():
    from models.unified_recipient import UnifiedUserPreferences

    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="Q", answer="A")]
    svc.build_package.return_value = "/tmp/does_not_exist.apkg"

    repo = Mock()
    repo.get_preferences.return_value = UnifiedUserPreferences(user_id=1, anki_deck_name="Languages::Spanish")

    message = Mock()
    message.reply = AsyncMock(return_value=Mock(delete=AsyncMock()))
    message.reply_document = AsyncMock()

    ctx = ProcessingContext(
        message=message, thread_content=[("U", "vocab")], user_id=1, owner_name="U", location=None
    )

    await AnkiProcessor(svc, preferences_repo=repo).process(ctx)

    # deck name passed positionally to build_package(cards, deck_name)
    assert svc.build_package.call_args.args[1] == "Languages::Spanish"


@pytest.mark.unit
async def test_anki_processor_cloze_preview_shows_hidden_span():
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(type="cloze", text="Paris is the capital of {{c1::France}}.")]
    svc.build_package.return_value = "/tmp/does_not_exist.apkg"

    message = Mock()
    message.reply = AsyncMock(return_value=Mock(delete=AsyncMock()))
    message.reply_document = AsyncMock()

    ctx = ProcessingContext(
        message=message, thread_content=[("U", "paris france")], user_id=771, owner_name="U", location=None
    )
    await AnkiProcessor(svc).process(ctx)

    caption = message.reply_document.call_args.kwargs["caption"]
    assert "(cloze)" in caption
    assert "[France]" in caption  # hidden span shown in brackets for review

    from services.content import anki_buffer
    anki_buffer.clear(771)


@pytest.mark.unit
def test_anki_preview_truncates_single_oversized_card():
    preview = _format_preview(
        [AnkiCard(question="What is in the source image?", answer="A" * 5000)],
        budget=500,
    )

    assert len(preview) <= 500
    assert preview.endswith("…")


@pytest.mark.unit
def test_anki_delivery_caption_splits_oversized_preview():
    caption, followups = _build_delivery_messages(
        "🃏 1 card(s) — added to your deck (1 total).\n\n",
        [AnkiCard(question="Front", answer="Back " + "x" * 5000)],
    )

    assert len(caption) <= TELEGRAM_DOCUMENT_CAPTION_LIMIT
    assert followups
    assert all(len(text) <= TELEGRAM_TEXT_MESSAGE_LIMIT for text in followups)


@pytest.mark.unit
async def test_anki_processor_empty_content_replies_error():
    svc = Mock()
    message = Mock()
    message.reply = AsyncMock()

    ctx = ProcessingContext(
        message=message,
        thread_content=[],  # genuinely empty thread -> nothing to turn into a card
        user_id=1,
        owner_name="User",
        location=None,
    )

    result = await AnkiProcessor(svc).process(ctx)

    assert not result.success
    svc.extract_cards.assert_not_called()
    message.reply.assert_called_once()


@pytest.mark.unit
def test_anki_export_caption_mentions_media_count():
    from handlers_modular.callbacks.anki_buffer_cb import _export_caption

    caption = _export_caption(3, 2, "Biology::Cells")

    assert "3 card(s)" in caption
    assert "Biology::Cells" in caption
    assert "Includes 2 media file(s)." in caption


# ======================================================================================
# Reminder ("todoist") path migrated onto the executable workflow engine
# (services/content/reminder_generation_graph.py). Parity tests: the engine drives
# parse -> create, reusing the existing services verbatim.
# ======================================================================================

from core.interfaces import ServiceResult
from services.content.reminder_generation_graph import ReminderGenerationGraph
from services.content.reminder_processor import ReminderProcessor


class _FakeParsing:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def parse_content_to_task(self, content, owner_name=None, location=None, user_id=None):
        self.calls.append((content, owner_name, location, user_id))
        return self.parsed


class _FakeTaskSvc:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def create_task_for_recipients(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.mark.unit
async def test_reminder_graph_threads_parsed_task_into_create():
    parsing = _FakeParsing({"title": "Call Bob", "due_time": "2026-06-20T09:00:00Z"})
    task_svc = _FakeTaskSvc(ServiceResult.success_with_data("✅ created", {"add_actions": []}))
    graph = ReminderGenerationGraph(parsing, task_svc)

    out = await graph.run(
        content="call bob friday",
        owner_id=7,
        owner_name="Art",
        location="Lisbon",
        screenshot_data={"file_id": "f1"},
        chat_id=11,
        message_id=22,
    )

    # parse output flows into create -> proves the engine threaded the two nodes in order
    td = out["task_data"]
    assert td["title"] == "Call Bob"
    assert td["due_time"] == "2026-06-20T09:00:00Z"
    assert td["description"] == "call bob friday"  # original content preserved (parity)
    assert out["create_result"]["success"] is True
    assert out["create_result"]["message"] == "✅ created"
    assert out["usage"] is not None  # ran inside the engine's usage scope

    # create_task_for_recipients received exactly the parsed task + passthrough context (parity)
    call = task_svc.calls[0]
    assert call["user_id"] == 7
    assert call["title"] == "Call Bob"
    assert call["due_time"] == "2026-06-20T09:00:00Z"
    assert call["description"] == "call bob friday"
    assert call["screenshot_data"] == {"file_id": "f1"}
    assert call["chat_id"] == 11 and call["message_id"] == 22
    assert call["specific_recipients"] is None
    # parsing received the user context
    assert parsing.calls[0] == ("call bob friday", "Art", "Lisbon", 7)


@pytest.mark.unit
async def test_reminder_graph_fallback_when_parse_returns_none():
    from datetime import datetime, timezone

    parsing = _FakeParsing(None)
    task_svc = _FakeTaskSvc(ServiceResult.success_with_data("ok", None))
    graph = ReminderGenerationGraph(parsing, task_svc)
    long_content = "x" * 250

    out = await graph.run(content=long_content, owner_id=1)

    td = out["task_data"]
    assert td["title"] == long_content[:100]  # first 100 chars, parity with old fallback
    assert td["description"] == long_content
    due = datetime.fromisoformat(td["due_time"])
    assert due.hour == 9 and due.minute == 0  # tomorrow 09:00 UTC
    assert due.date() > datetime.now(timezone.utc).date()
    # the fallback task is still handed to create unchanged
    assert task_svc.calls[0]["title"] == long_content[:100]


@pytest.mark.unit
async def test_reminder_graph_create_failure_surfaces_result():
    parsing = _FakeParsing({"title": "T", "due_time": "2026-06-20T09:00:00Z"})
    task_svc = _FakeTaskSvc(ServiceResult.failure("NO_DEFAULT_RECIPIENTS"))
    graph = ReminderGenerationGraph(parsing, task_svc)

    out = await graph.run(content="c", owner_id=1)

    assert out["create_result"]["success"] is False
    assert out["create_result"]["message"] == "NO_DEFAULT_RECIPIENTS"


@pytest.mark.unit
async def test_reminder_processor_delegates_to_engine_and_replies(monkeypatch):
    parsing = _FakeParsing({"title": "Call Bob", "due_time": "2026-06-20T09:00:00Z"})
    task_svc = _FakeTaskSvc(ServiceResult.success_with_data("✅ created", {"add_actions": []}))

    fake_services = Mock()
    fake_services.get_parsing_service.return_value = parsing
    monkeypatch.setattr("core.initialization.services", fake_services)

    fake_container = Mock()
    fake_container.recipient_task_service.return_value = task_svc
    fake_container.recipient_service.return_value = Mock()
    monkeypatch.setattr("core.container.container", fake_container)

    handle = AsyncMock()
    monkeypatch.setattr("handlers_modular.base.handle_task_creation_response", handle)

    message = Mock()
    message.reply = AsyncMock()
    message.chat = Mock(id=11)
    message.message_id = 22
    ctx = ProcessingContext(
        message=message,
        thread_content=[("U", "call bob friday")],
        user_id=7,
        owner_name="Art",
        location="Lisbon",
    )

    result = await ReminderProcessor().process(ctx)

    assert result.success
    handle.assert_awaited_once()
    # parity: the parsed task reached create_task_for_recipients through the engine.
    # description is the assembled thread (assemble_thread prefixes the sender name).
    assert task_svc.calls[0]["title"] == "Call Bob"
    assert task_svc.calls[0]["description"] == "U: call bob friday"
    assert parsing.calls[0][0] == "U: call bob friday"


@pytest.mark.unit
def test_reminder_graph_runner_takes_config_from_parsing_service():
    # Regression: RecipientTaskService has no .config; the runner must read it from the parsing
    # service or the engine budget integration silently disables (config=None).
    parsing = _FakeParsing(None)
    parsing.config = "CFG-OBJ"
    task_svc = _FakeTaskSvc(ServiceResult.success_with_data("ok", None))
    graph = ReminderGenerationGraph(parsing, task_svc)
    assert graph.runner.config == "CFG-OBJ"


@pytest.mark.unit
async def test_reminder_processor_no_default_recipients_shows_picker(monkeypatch):
    parsing = _FakeParsing({"title": "T", "due_time": "2026-06-20T09:00:00Z"})
    task_svc = _FakeTaskSvc(ServiceResult.failure("NO_DEFAULT_RECIPIENTS"))

    fake_services = Mock()
    fake_services.get_parsing_service.return_value = parsing
    monkeypatch.setattr("core.initialization.services", fake_services)

    recipient_service = Mock()
    recipient_service.is_recipient_ui_enabled.return_value = True
    recipient_service.get_enabled_recipients.return_value = [Mock(id=5, name="Me", platform_type="todoist")]
    fake_container = Mock()
    fake_container.recipient_task_service.return_value = task_svc
    fake_container.recipient_service.return_value = recipient_service
    fake_container.task_repository.return_value.create.return_value = 99
    monkeypatch.setattr("core.container.container", fake_container)
    monkeypatch.setattr("helpers.ui_helpers.format_platform_button", lambda *a, **k: "Add to Me")
    monkeypatch.setattr("keyboards.recipient.get_post_task_actions_keyboard", lambda actions: "KB")

    message = Mock()
    message.reply = AsyncMock()
    message.chat = Mock(id=1)
    message.message_id = 2
    ctx = ProcessingContext(
        message=message, thread_content=[("U", "x")], user_id=7, owner_name="A", location=None
    )

    result = await ReminderProcessor().process(ctx)

    assert not result.success
    args, kwargs = message.reply.call_args
    assert "No default recipients set" in args[0]
    assert kwargs.get("reply_markup") == "KB"


@pytest.mark.unit
async def test_reminder_processor_create_exception_replies_generic_error(monkeypatch):
    parsing = _FakeParsing({"title": "T", "due_time": "2026-06-20T09:00:00Z"})

    class _BoomTaskSvc:
        def create_task_for_recipients(self, **kwargs):
            raise RuntimeError("todoist down")

    fake_services = Mock()
    fake_services.get_parsing_service.return_value = parsing
    monkeypatch.setattr("core.initialization.services", fake_services)

    fake_container = Mock()
    fake_container.recipient_task_service.return_value = _BoomTaskSvc()
    fake_container.recipient_service.return_value = Mock()
    monkeypatch.setattr("core.container.container", fake_container)

    message = Mock()
    message.reply = AsyncMock()
    message.chat = Mock(id=1)
    message.message_id = 2
    ctx = ProcessingContext(
        message=message, thread_content=[("U", "x")], user_id=7, owner_name="A", location=None
    )

    result = await ReminderProcessor().process(ctx)

    assert not result.success
    # engine swallows the capability exception -> create_result None -> generic error (parity)
    assert "Error creating task" in message.reply.call_args[0][0]
