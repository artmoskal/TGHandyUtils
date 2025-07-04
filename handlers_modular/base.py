"""Base handler functionality."""

from typing import Optional, Dict, List, Any
from aiogram.types import Message
from keyboards.recipient import get_post_task_actions_keyboard
from core.logging import get_logger

logger = get_logger(__name__)


async def handle_task_creation_response(message: Message, success: bool, feedback: Optional[str], actions: Optional[Dict[str, List[Dict[str, str]]]]):
    """Unified handler for task creation responses with action buttons."""
    if not success:
        await message.reply("❌ Error creating task. Please try again.", disable_web_page_preview=True)
        return
    
    # If we have actions, show keyboard with action buttons
    if actions and (actions.get("remove_actions") or actions.get("add_actions")):
        keyboard = get_post_task_actions_keyboard(actions)
        await message.reply(feedback or "✅ Task created!", reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
    else:
        # No actions available, just show the feedback
        await message.reply(feedback or "✅ Task created!", parse_mode='Markdown', disable_web_page_preview=True)