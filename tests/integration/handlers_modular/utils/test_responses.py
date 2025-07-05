"""Tests for response utilities."""

import pytest
from unittest.mock import AsyncMock, Mock
from aiogram.types import Message, CallbackQuery

# Mock the imports
with pytest.MonkeyPatch().context() as mp:
    mp.setattr('core.logging.get_logger', Mock())
    from handlers_modular.utils.responses import MessageResponses, CallbackResponses, FormattedResponses


class TestMessageResponses:
    """Test message response utilities."""
    
    @pytest.fixture
    def mock_message(self):
        """Mock Telegram message."""
        message = Mock(spec=Message)
        message.reply = AsyncMock()
        return message
    
    @pytest.mark.asyncio
    async def test_success_reply(self, mock_message):
        """Test success reply format."""
        await MessageResponses.success_reply(mock_message, "Operation completed")
        
        mock_message.reply.assert_called_once_with(
            "✅ Operation completed",
            reply_markup=None,
            parse_mode='Markdown'
        )
    
    @pytest.mark.asyncio
    async def test_error_reply(self, mock_message):
        """Test error reply format."""
        await MessageResponses.error_reply(mock_message, "Something went wrong")
        
        mock_message.reply.assert_called_once_with(
            "❌ Something went wrong",
            reply_markup=None,
            parse_mode='Markdown'
        )
    
    @pytest.mark.asyncio
    async def test_validation_error(self, mock_message):
        """Test validation error format."""
        await MessageResponses.validation_error(mock_message, "Name")
        
        mock_message.reply.assert_called_once_with(
            "❌ Name cannot be empty. Please enter name:"
        )


class TestCallbackResponses:
    """Test callback response utilities."""
    
    @pytest.fixture
    def mock_callback_query(self):
        """Mock callback query."""
        callback = Mock(spec=CallbackQuery)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        return callback
    
    @pytest.mark.asyncio
    async def test_success_edit(self, mock_callback_query):
        """Test success edit format."""
        await CallbackResponses.success_edit(mock_callback_query, "Update successful")
        
        mock_callback_query.message.edit_text.assert_called_once_with(
            "✅ Update successful",
            reply_markup=None,
            parse_mode='Markdown'
        )
        mock_callback_query.answer.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_simple_answer(self, mock_callback_query):
        """Test simple answer format."""
        await CallbackResponses.simple_answer(mock_callback_query, "Done!", show_alert=True)
        
        mock_callback_query.answer.assert_called_once_with("Done!", show_alert=True)


class TestFormattedResponses:
    """Test formatted response templates."""
    
    def test_no_recipients_configured(self):
        """Test no recipients message format."""
        result = FormattedResponses.no_recipients_configured()
        
        assert "❌" in result
        assert "No Recipients Available" in result
        assert "/recipients" in result
    
    def test_loading_error(self):
        """Test loading error format."""
        result = FormattedResponses.loading_error("settings")
        
        assert "❌" in result
        assert "Error loading settings" in result
        assert "try again" in result
    
    def test_update_success(self):
        """Test update success format."""
        result = FormattedResponses.update_success("Name", "Test User")
        
        assert "✅" in result
        assert "Name Updated" in result
        assert "Test User" in result
    
    def test_settings_display(self):
        """Test settings display format."""
        result = FormattedResponses.settings_display(
            "Test User", "Test Location", "Enabled", "Disabled"
        )
        
        assert "⚙️" in result
        assert "Your Settings" in result
        assert "Test User" in result
        assert "Test Location" in result
        assert "Enabled" in result
        assert "Disabled" in result
    
    def test_recipient_management_display_empty(self):
        """Test recipient management display with no recipients."""
        result = FormattedResponses.recipient_management_display(0)
        
        assert "🎯" in result
        assert "Recipients Management" in result
        assert "No recipients configured" in result
    
    def test_recipient_management_display_with_recipients(self):
        """Test recipient management display with recipients."""
        result = FormattedResponses.recipient_management_display(3)
        
        assert "🎯" in result
        assert "Recipients Management" in result
        assert "3 recipients configured" in result