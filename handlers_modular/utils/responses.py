"""Common response utilities for handlers."""

from typing import Optional
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from core.logging import get_logger

logger = get_logger(__name__)


class MessageResponses:
    """Standardized message response utilities."""
    
    @staticmethod
    async def success_reply(message: Message, text: str, keyboard: Optional[InlineKeyboardMarkup] = None):
        """Send a success message reply."""
        await message.reply(
            f"✅ {text}",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    
    @staticmethod
    async def error_reply(message: Message, text: str, keyboard: Optional[InlineKeyboardMarkup] = None):
        """Send an error message reply."""
        await message.reply(
            f"❌ {text}",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    
    @staticmethod
    async def info_reply(message: Message, text: str, keyboard: Optional[InlineKeyboardMarkup] = None):
        """Send an info message reply."""
        await message.reply(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    
    @staticmethod
    async def validation_error(message: Message, field_name: str):
        """Send a validation error for empty fields."""
        await message.reply(f"❌ {field_name} cannot be empty. Please enter {field_name.lower()}:", disable_web_page_preview=True)


class CallbackResponses:
    """Standardized callback response utilities."""
    
    @staticmethod
    async def success_edit(callback_query: CallbackQuery, text: str, keyboard: Optional[InlineKeyboardMarkup] = None):
        """Edit callback message with success text."""
        await callback_query.message.edit_text(
            f"✅ {text}",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        await callback_query.answer()
    
    @staticmethod
    async def error_edit(callback_query: CallbackQuery, text: str, keyboard: Optional[InlineKeyboardMarkup] = None):
        """Edit callback message with error text."""
        await callback_query.message.edit_text(
            f"❌ {text}",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        await callback_query.answer()
    
    @staticmethod
    async def info_edit(callback_query: CallbackQuery, text: str, keyboard: Optional[InlineKeyboardMarkup] = None):
        """Edit callback message with info text."""
        await callback_query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        await callback_query.answer()
    
    @staticmethod
    async def simple_answer(callback_query: CallbackQuery, text: str, show_alert: bool = False):
        """Send a simple callback answer."""
        await callback_query.answer(text, show_alert=show_alert)


class FormattedResponses:
    """Pre-formatted response templates."""
    
    @staticmethod
    def no_recipients_configured() -> str:
        """Standard no recipients error message."""
        return (
            "❌ **No Recipients Available**\n\n"
            "You need to add and enable at least one recipient first.\n\n"
            "🚀 Use /recipients to add your Todoist or Trello account."
        )
    
    @staticmethod
    def loading_error(action: str) -> str:
        """Standard loading error message."""
        return f"❌ Error loading {action}. Please try again."
    
    @staticmethod
    def update_success(item: str, value: str) -> str:
        """Standard update success message."""
        return f"✅ **{item} Updated**\n\n{item} has been set to: {value}"
    
    @staticmethod
    def update_failure(item: str) -> str:
        """Standard update failure message."""
        return f"❌ Failed to update {item}. Please try again."
    
    @staticmethod
    def operation_cancelled(operation: str) -> str:
        """Standard cancellation message."""
        return f"❌ {operation} cancelled."
    
    @staticmethod
    def settings_display(owner_name: str, location: str, notifications: str, recipient_ui: str) -> str:
        """Standard settings display format."""
        return (
            f"⚙️ **Your Settings**\n\n"
            f"👤 **Name:** {owner_name}\n"
            f"🌍 **Location:** {location}\n"
            f"🔔 **Notifications:** {notifications}\n"
            f"🎯 **Recipient UI:** {recipient_ui}\n\n"
            f"Select an option to update:"
        )
    
    @staticmethod
    def recipient_management_display(count: int) -> str:
        """Standard recipient management display format."""
        if count == 0:
            return (
                "🎯 **Recipients Management**\n\n"
                "No recipients configured yet.\n\n"
                "Add your first recipient to get started:"
            )
        else:
            return (
                f"🎯 **Recipients Management**\n\n"
                f"You have {count} recipients configured.\n\n"
                f"Choose an option:"
            )