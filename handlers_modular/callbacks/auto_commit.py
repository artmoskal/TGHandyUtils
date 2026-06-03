"""Callback handler for the auto-mode override buttons (📝 Reminder / 🃏 Card)."""

from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot import router
from core.interfaces import Intent
from core.logging import get_logger

logger = get_logger(__name__)


@router.callback_query(lambda c: c.data and c.data.startswith("autocommit:"))
async def on_autocommit(callback_query: CallbackQuery, state: FSMContext):
    """Commit the user's chosen type, cancelling the pending auto-commit timer."""
    try:
        _, choice, token = callback_query.data.split(":", 2)
    except ValueError:
        await callback_query.answer()
        return

    intent = Intent.ANKI if choice == "anki" else Intent.REMINDER

    from services.content.auto_flow import commit_choice
    committed = await commit_choice(token, intent)
    if committed:
        await callback_query.answer("✅ Got it")
    else:
        await callback_query.answer("Already created")
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
