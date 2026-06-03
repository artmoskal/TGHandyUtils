"""Anki flashcard content processor: content -> .apkg sent over Telegram."""

import asyncio
import os

from aiogram.types import FSInputFile

from core.interfaces import IContentProcessor, ProcessingContext, ServiceResult
from core.logging import get_logger
from services.anki_card_service import AnkiCardService
from services.content.thread_assembly import assemble_thread

logger = get_logger(__name__)


class AnkiProcessor(IContentProcessor):
    """Turn assembled content into Anki flashcards and deliver them as an .apkg file.

    No recipients required - this is independent of the reminder/platform system.
    """

    def __init__(self, anki_card_service: AnkiCardService, preferences_repo=None):
        self.anki_card_service = anki_card_service
        self.preferences_repo = preferences_repo

    def _deck_name(self, user_id: int):
        """The user's configured deck name, or None to use the service default."""
        if not self.preferences_repo:
            return None
        try:
            prefs = self.preferences_repo.get_preferences(user_id)
            if prefs and prefs.anki_deck_name:
                return prefs.anki_deck_name
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Could not read anki_deck_name for {user_id}: {e}")
        return None

    async def process(self, ctx: ProcessingContext) -> ServiceResult:
        message = ctx.message
        content, _screenshot = assemble_thread(ctx.thread_content)

        if not content or not content.strip():
            await message.reply("❌ Nothing to turn into a flashcard - send me some content.")
            return ServiceResult.failure("empty content")

        status_msg = await message.reply("🃏 Generating flashcards…")
        out_path = None
        try:
            cards = await asyncio.to_thread(self.anki_card_service.extract_cards, content)
            deck_name = self._deck_name(ctx.user_id)
            if deck_name:
                out_path = await asyncio.to_thread(self.anki_card_service.build_package, cards, deck_name)
            else:
                out_path = await asyncio.to_thread(self.anki_card_service.build_package, cards)

            preview = "\n".join(f"• {c.question}" for c in cards[:5])
            if len(cards) > 5:
                preview += f"\n…and {len(cards) - 5} more"

            await message.reply_document(
                FSInputFile(out_path, filename="flashcards.apkg"),
                caption=(
                    f"🃏 {len(cards)} flashcard(s) ready. Open this file in Anki to import.\n\n"
                    f"{preview}"
                ),
            )
            try:
                await status_msg.delete()
            except Exception:
                pass

            logger.info(f"Delivered {len(cards)} flashcards to user {ctx.user_id}")
            return ServiceResult.success_with_data(f"{len(cards)} flashcards", {"count": len(cards)})

        except Exception as e:
            logger.error(f"Anki processing failed: {e}")
            try:
                await status_msg.edit_text("❌ Could not generate flashcards from that. Please try again.")
            except Exception:
                await message.reply("❌ Could not generate flashcards from that. Please try again.")
            return ServiceResult.failure(str(e))
        finally:
            if out_path and os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
