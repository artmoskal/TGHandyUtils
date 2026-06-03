"""Unit tests for /anki and /tasks command overrides (Stage 3)."""

from unittest.mock import Mock, AsyncMock, patch

import pytest

from core.interfaces import Intent
from services.content import overrides


@pytest.mark.unit
def test_parse_caption_override_variants():
    assert overrides.parse_caption_override("/anki photosynthesis") == (Intent.ANKI, "photosynthesis")
    assert overrides.parse_caption_override("/tasks buy milk") == (Intent.REMINDER, "buy milk")
    assert overrides.parse_caption_override("just text") == (None, "just text")
    assert overrides.parse_caption_override("") == (None, "")
    assert overrides.parse_caption_override(None) == (None, None)


@pytest.mark.unit
def test_arm_peek_consume_cycle():
    overrides.arm(42, Intent.ANKI)
    assert overrides.peek(42) == Intent.ANKI
    assert overrides.peek(42) == Intent.ANKI  # peek does not clear
    assert overrides.consume(42) == Intent.ANKI
    assert overrides.consume(42) is None


@pytest.mark.unit
async def test_cmd_anki_with_content_routes_to_anki_processor():
    from handlers_modular.commands.content_commands import cmd_anki

    message = Mock()
    message.from_user = Mock(id=7)
    message.reply = AsyncMock()
    command = Mock()
    command.args = "make a card about mitochondria"

    with patch('handlers_modular.commands.content_commands.container') as mock_container, \
         patch('handlers_modular.message.text_handler.process_thread_with_photos', new=AsyncMock()) as ptw:
        rs = mock_container.recipient_service.return_value
        rs.get_user_preferences.return_value = Mock(owner_name="U", location=None)
        rs.get_enabled_recipients.return_value = [Mock()]

        await cmd_anki(message, command, Mock())

        ptw.assert_awaited_once()
        assert ptw.call_args.kwargs["intent_override"] == Intent.ANKI


@pytest.mark.unit
async def test_cmd_anki_without_content_arms_override():
    from handlers_modular.commands.content_commands import cmd_anki

    overrides.consume(8)  # ensure clean
    message = Mock()
    message.from_user = Mock(id=8)
    message.reply = AsyncMock()
    command = Mock()
    command.args = None

    with patch('handlers_modular.commands.content_commands.container') as mock_container:
        mock_container.recipient_service.return_value.get_user_preferences.return_value = Mock(
            owner_name="U", location=None
        )
        await cmd_anki(message, command, Mock())

    assert overrides.consume(8) == Intent.ANKI
    message.reply.assert_awaited_once()


@pytest.mark.unit
async def test_cmd_tasks_without_recipients_shows_help():
    from handlers_modular.commands.content_commands import cmd_tasks

    message = Mock()
    message.from_user = Mock(id=9)
    message.reply = AsyncMock()
    command = Mock()
    command.args = "remember this"

    with patch('handlers_modular.commands.content_commands.container') as mock_container, \
         patch('handlers_modular.message.text_handler.process_thread_with_photos', new=AsyncMock()) as ptw:
        rs = mock_container.recipient_service.return_value
        rs.get_user_preferences.return_value = Mock(owner_name="U", location=None)
        rs.get_enabled_recipients.return_value = []  # no recipients

        await cmd_tasks(message, command, Mock())

        ptw.assert_not_awaited()
        message.reply.assert_awaited_once()
