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
