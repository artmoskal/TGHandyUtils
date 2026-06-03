"""Callbacks for the Anki deck buffer: 📦 Export and 🗑 Clear."""

import asyncio
import os

from aiogram.types import CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext

from bot import router
from core.container import container
from core.logging import get_logger
from services.content import anki_buffer

logger = get_logger(__name__)


@router.callback_query(lambda c: c.data == "anki_export")
async def anki_export(callback_query: CallbackQuery, state: FSMContext):
    """Build and send one .apkg containing the whole accumulated deck."""
    user_id = callback_query.from_user.id
    cards, media = anki_buffer.get(user_id)
    if not cards:
        await callback_query.answer("Your deck is empty.")
        return

    await callback_query.answer("📦 Building deck…")
    out_path = None
    try:
        deck = container.recipient_service().get_anki_deck_name(user_id)
        service = container.anki_card_service()
        out_path = await asyncio.to_thread(service.build_package, cards, deck, None, media)
        await callback_query.message.answer_document(
            FSInputFile(out_path, filename="deck.apkg"),
            caption=f"📦 Your full deck: {len(cards)} card(s) → import into '{deck}'.",
        )
    except Exception as e:
        logger.error(f"Anki export failed for {user_id}: {e}")
        await callback_query.message.answer("❌ Could not export the deck. Please try again.")
    finally:
        if out_path and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass


@router.callback_query(lambda c: c.data == "anki_clear")
async def anki_clear(callback_query: CallbackQuery, state: FSMContext):
    """Empty the accumulated deck."""
    user_id = callback_query.from_user.id
    anki_buffer.clear(user_id)
    await callback_query.answer("🗑 Deck cleared.")
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
