"""Unit tests for the content-processing seam (router, resolver, anki processor)."""

from unittest.mock import Mock, AsyncMock

import pytest

from core.interfaces import Intent, ProcessingContext
from models.anki import AnkiCard
from services.content.intent_resolver import IntentResolver
from services.content.anki_processor import AnkiProcessor


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
