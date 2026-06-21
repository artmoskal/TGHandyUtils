"""Shared task response delivery helpers."""

from typing import Dict, List, Optional

from aiogram.types import Message

from keyboards.recipient import get_post_task_actions_keyboard


async def handle_task_creation_response(
    message: Message,
    success: bool,
    feedback: Optional[str],
    actions: Optional[Dict[str, List[Dict[str, str]]]],
) -> None:
    """Reply with the task creation result and optional recipient action keyboard."""

    if not success:
        await message.reply("❌ Error creating task. Please try again.", disable_web_page_preview=True)
        return

    if actions and (actions.get("remove_actions") or actions.get("add_actions")):
        keyboard = get_post_task_actions_keyboard(actions)
        await message.reply(
            feedback or "✅ Task created!",
            reply_markup=keyboard,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    else:
        await message.reply(
            feedback or "✅ Task created!",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
