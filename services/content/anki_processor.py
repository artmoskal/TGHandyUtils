"""Anki flashcard content processor: content -> .apkg (with optional embedded images) over Telegram.

Supports `[i ...]` directives (image placement, question strategy, guide, help), embeds real
images via genanki media, delivers the current message's cards immediately, and accumulates all
cards into a per-user buffer that can be exported as one deck.
"""

import asyncio
import os
import re
import uuid

from aiogram.types import FSInputFile

from core.interfaces import IContentProcessor, ProcessingContext, ServiceResult
from core.logging import get_logger
from services.anki_card_service import AnkiCardService, DEFAULT_DECK_NAME
from services.content.thread_assembly import assemble_thread, collect_screenshots
from services.content.anki_directives import parse_directives, HELP_TEXT
from services.content import anki_buffer

logger = get_logger(__name__)

BUFFER_MEDIA_DIR = "data/temp_cache/anki"


def _strip_html(text: str) -> str:
    """Drop HTML (e.g. embedded <img>/<br>) so the preview shows plain text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _format_preview(cards, budget: int) -> str:
    """Render 'front → back' for as many cards as fit within `budget` characters."""
    lines, shown = [], 0
    for c in cards:
        block = f"• {_strip_html(c.question)}\n   ↳ {_strip_html(c.answer)}"
        candidate = "\n".join(lines + [block])
        if lines and len(candidate) > budget:
            break
        lines.append(block)
        shown += 1
    preview = "\n".join(lines)
    if shown < len(cards):
        preview += f"\n…and {len(cards) - shown} more"
    return preview


class AnkiProcessor(IContentProcessor):
    """Turn assembled content into Anki flashcards and deliver them as an .apkg file."""

    def __init__(self, anki_card_service: AnkiCardService, preferences_repo=None):
        self.anki_card_service = anki_card_service
        self.preferences_repo = preferences_repo

    def _deck_name(self, user_id: int) -> str:
        if self.preferences_repo:
            try:
                prefs = self.preferences_repo.get_preferences(user_id)
                if prefs and prefs.anki_deck_name:
                    return prefs.anki_deck_name
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"Could not read anki_deck_name for {user_id}: {e}")
        return DEFAULT_DECK_NAME

    def _save_images(self, user_id: int, screenshots) -> list:
        """Persist image bytes to the user's buffer dir. Returns [(path, basename)]."""
        out = []
        user_dir = os.path.join(BUFFER_MEDIA_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        for sc in screenshots:
            data = sc.get("image_data") if isinstance(sc, dict) else None
            if not data:
                continue
            basename = f"{uuid.uuid4().hex}.jpg"
            path = os.path.join(user_dir, basename)
            with open(path, "wb") as fh:
                fh.write(data)
            out.append((path, basename))
        return out

    @staticmethod
    def _placement(images, directives):
        """Return (front_basenames, back_basenames) per directives."""
        front, back = [], []
        if not images:
            return front, back
        if directives.multi_split and len(images) >= 2:
            back.append(images[0][1])
            front.append(images[1][1])
            back.extend(b for _, b in images[2:])
        elif directives.image_placement == "front":
            front.extend(b for _, b in images)
        else:
            back.extend(b for _, b in images)
        return front, back

    async def process(self, ctx: ProcessingContext) -> ServiceResult:
        message = ctx.message
        content, _first = assemble_thread(ctx.thread_content)
        directives, content = parse_directives(content)

        if directives.help:
            await message.reply(HELP_TEXT, parse_mode="Markdown", disable_web_page_preview=True)
            return ServiceResult.success_with_data("help", None)

        screenshots = collect_screenshots(ctx.thread_content) if directives.include_image else []
        if (not content or not content.strip()) and not screenshots:
            await message.reply("❌ Nothing to turn into a flashcard - send me some content.")
            return ServiceResult.failure("empty content")

        status_msg = await message.reply("🃏 Generating flashcards…")
        out_path = None
        try:
            cards = await asyncio.to_thread(
                self.anki_card_service.extract_cards,
                content, directives.guide, directives.strategy, directives.count,
            )

            # Embed images into cards per directives.
            images = self._save_images(ctx.user_id, screenshots) if screenshots else []
            front, back = self._placement(images, directives)
            front_html = "".join(f'<img src="{b}">' for b in front)
            back_html = "".join(f'<img src="{b}">' for b in back)
            if front_html or back_html:
                for c in cards:
                    if front_html:
                        c.question = f"{c.question}<br>{front_html}"
                    if back_html:
                        c.answer = f"{c.answer}<br>{back_html}"
            media_files = [p for p, _ in images]

            deck = self._deck_name(ctx.user_id)

            # Immediate delivery of THIS message's cards.
            out_path = await asyncio.to_thread(
                self.anki_card_service.build_package, cards, deck, None, media_files
            )

            # Accumulate into the running deck (keeps media files for later export).
            anki_buffer.add(ctx.user_id, cards, media_files)
            total = anki_buffer.count(ctx.user_id)

            header = (
                f"🃏 {len(cards)} card(s) — added to your deck ({total} total). "
                f"Import this file, or collect more and Export once.\n\n"
            )
            caption = header + _format_preview(cards, budget=1024 - len(header) - 20)

            from keyboards.recipient import get_anki_buffer_keyboard
            await message.reply_document(
                FSInputFile(out_path, filename="flashcards.apkg"),
                caption=caption,
                reply_markup=get_anki_buffer_keyboard(total),
            )
            try:
                await status_msg.delete()
            except Exception:
                pass

            logger.info(f"Delivered {len(cards)} flashcards to user {ctx.user_id} (buffer {total})")
            return ServiceResult.success_with_data(f"{len(cards)} flashcards", {"count": len(cards)})

        except Exception as e:
            logger.error(f"Anki processing failed: {e}")
            try:
                await status_msg.edit_text("❌ Could not generate flashcards from that. Please try again.")
            except Exception:
                await message.reply("❌ Could not generate flashcards from that. Please try again.")
            return ServiceResult.failure(str(e))
        finally:
            # The per-message .apkg is disposable; buffer media files are kept until export/clear.
            if out_path and os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
