"""Content mode settings callbacks (reminder / anki / auto)."""

from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot import router
from keyboards.recipient import get_content_mode_keyboard
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)

_MODE_LABELS = {
    "reminder": "📝 Reminders/Tasks",
    "anki": "🃏 Anki Flashcards",
    "auto": "🤖 Auto",
}


@router.callback_query(lambda c: c.data == "content_mode_settings")
async def content_mode_settings(callback_query: CallbackQuery, state: FSMContext):
    """Show the content-mode selector."""
    user_id = callback_query.from_user.id
    try:
        recipient_service = container.recipient_service()
        current = recipient_service.get_content_mode(user_id)

        text = (
            "🧠 *Content Mode*\n\n"
            "What should I do with the content you send me?\n\n"
            "📝 *Reminders/Tasks* — create a reminder (default)\n"
            "🃏 *Anki Flashcards* — generate flashcards and send an .apkg file\n"
            "🤖 *Auto* — decide per message (you can override with /tasks or /anki)\n\n"
            f"Current: *{_MODE_LABELS.get(current, current)}*"
        )
        await callback_query.message.edit_text(
            text,
            reply_markup=get_content_mode_keyboard(current),
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Failed to show content mode settings for {user_id}: {e}")
        await callback_query.answer("Error loading content mode settings")


@router.callback_query(lambda c: c.data and c.data.startswith("set_content_mode_"))
async def set_content_mode(callback_query: CallbackQuery, state: FSMContext):
    """Persist the chosen content mode and refresh the selector."""
    user_id = callback_query.from_user.id
    mode = callback_query.data.replace("set_content_mode_", "")
    try:
        recipient_service = container.recipient_service()
        success = recipient_service.set_content_mode(user_id, mode)
        if success:
            await callback_query.answer(f"✅ Mode set: {_MODE_LABELS.get(mode, mode)}")
            await content_mode_settings(callback_query, state)
        else:
            await callback_query.answer("❌ Failed to update mode.")
    except ValueError:
        await callback_query.answer("❌ Invalid mode.")
    except Exception as e:
        logger.error(f"Failed to set content mode for {user_id}: {e}")
        await callback_query.answer("Error updating content mode")
