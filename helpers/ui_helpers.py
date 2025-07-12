"""UI helper utilities for consistent formatting across the application."""

from typing import Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_platform_emoji(platform_type: str) -> str:
    """Get emoji for platform type."""
    emoji_map = {
        "todoist": "📋",
        "trello": "🏗️", 
        "google_calendar": "📅"
    }
    return emoji_map.get(platform_type, "📱")


def get_status_emoji(enabled: bool) -> str:
    """Get status emoji for enabled/disabled state."""
    return "✅" if enabled else "❌"


def get_account_type_emoji(is_personal: bool) -> str:
    """Get account type emoji."""
    return "👤" if is_personal else "👥"


def get_default_indicator(is_default: bool) -> str:
    """Get default status indicator."""
    return " ⭐" if is_default else ""


def create_back_button(callback_data: str, text: str = "« Back") -> InlineKeyboardButton:
    """Create standardized back button."""
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def escape_markdown(text: str) -> str:
    """Escape special Markdown characters."""
    special_chars = ['_', '*', '[', ']', '`', '\\']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text


def format_platform_button(platform_type: str, recipient_name: str, action: str) -> str:
    """Format platform button text with emoji and action."""
    platform_emoji = get_platform_emoji(platform_type)
    return f"{platform_emoji} {action} {recipient_name}"