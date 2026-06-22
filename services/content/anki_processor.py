"""Anki flashcard content processor: content -> .apkg (with optional embedded images) over Telegram.

Supports `[i ...]` directives (image placement, question strategy, guide, help), embeds real
images via genanki media, delivers the current message's cards immediately, and accumulates all
cards into a per-user buffer that can be exported as one deck.
"""

import asyncio
import json
import os
import re
import uuid
from collections import Counter

from aiogram.types import FSInputFile

from core.interfaces import IContentProcessor, ProcessingContext, ServiceResult
from core.logging import get_logger
from services.anki_card_service import AnkiCardService, DEFAULT_DECK_NAME
from services.content.thread_assembly import assemble_thread, collect_screenshots
from services.content.anki_directives import parse_directives, HELP_TEXT
from services.content.anki_source import build_content_source
from services.content import anki_buffer
from ai_workflow_engine.usage import format_usage_summary

logger = get_logger(__name__)

BUFFER_MEDIA_DIR = "data/temp_cache/anki"
TELEGRAM_DOCUMENT_CAPTION_LIMIT = 1024
TELEGRAM_TEXT_MESSAGE_LIMIT = 4096
_IMG_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']?([^\"'>\s]+)", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _image_sources(text: str) -> list[str]:
    return [os.path.basename(src) for src in _IMG_RE.findall(text or "")]


def _strip_html(text: str) -> str:
    """Drop HTML (e.g. embedded <img>/<br>) so the preview shows plain text."""
    text = _BR_RE.sub("\n", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _cloze_to_display(text: str) -> str:
    """Turn '{{c1::answer}}' into '[answer]' so the hidden span is visible in the preview."""
    return re.sub(r"\{\{c\d+::(.*?)(?:::.*?)?\}\}", r"[\1]", text)


def _ellipsize(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"
    return text[: limit - 1].rstrip() + "…"


def _field_preview(text: str, label: str, indent: str) -> list[str]:
    plain = _strip_html(text)
    images = _image_sources(text)
    lines = [plain] if plain else []
    if images and not plain:
        lines.append(f"[{label} image only]")
    for src in images:
        lines.append(f"[{label} image: {src}]")
    if not lines:
        return [""]
    return [lines[0], *[f"{indent}{line}" for line in lines[1:]]]


def _preview_block(card) -> str:
    if getattr(card, "type", "basic") == "cloze" and card.text:
        lines = _field_preview(_cloze_to_display(card.text), "cloze", "  ")
        return "• (cloze) " + "\n  ".join(lines)

    front_lines = _field_preview(card.question, "front", "  ")
    back_lines = _field_preview(card.answer, "back", "      ")
    block = [f"• {front_lines[0]}"]
    block.extend(front_lines[1:])
    block.append(f"   ↳ {back_lines[0]}")
    block.extend(back_lines[1:])
    return "\n".join(block)


def _format_preview(cards, budget: int) -> str:
    """Render each card (front → back, or cloze sentence) for as many as fit within `budget`."""
    if budget <= 0:
        return ""
    lines, shown = [], 0
    for c in cards:
        block = _preview_block(c)
        candidate = "\n".join(lines + [block])
        if len(block) > budget and not lines:
            lines.append(_ellipsize(block, budget))
            shown += 1
            break
        if len(candidate) > budget:
            break
        lines.append(block)
        shown += 1
    preview = "\n".join(lines)
    if shown < len(cards):
        preview += f"\n…and {len(cards) - shown} more"
    return preview


def _split_text_message(text: str, limit: int = TELEGRAM_TEXT_MESSAGE_LIMIT) -> list[str]:
    """Split long Telegram text into message-sized chunks without relying on caption limits."""
    if not text:
        return []
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return chunks


def _build_delivery_messages(header: str, cards) -> tuple[str, list[str]]:
    """Build a safe document caption plus optional follow-up messages for long previews."""
    full_preview = _format_preview(cards, budget=TELEGRAM_TEXT_MESSAGE_LIMIT - 128)
    full_text = (header + full_preview).strip()
    if len(full_text) <= TELEGRAM_DOCUMENT_CAPTION_LIMIT:
        return full_text, []

    marker = "\n\nFull preview follows."
    caption_budget = TELEGRAM_DOCUMENT_CAPTION_LIMIT - len(marker)
    if len(header) < caption_budget - 80:
        preview_budget = caption_budget - len(header)
        caption = (header + _format_preview(cards, budget=preview_budget) + marker).strip()
    else:
        caption = _ellipsize(header.strip(), caption_budget).strip() + marker
    caption = _ellipsize(caption, TELEGRAM_DOCUMENT_CAPTION_LIMIT)

    followups = _split_text_message("Card preview / details:\n\n" + full_text)
    return caption, followups


def _format_delivery_notes(
    rendered,
    front_images: list[str],
    back_images: list[str],
    show_usage: bool = False,
) -> str:
    notes = []
    image_bits = []
    if front_images:
        image_bits.append(f"{len(front_images)} uploaded front")
    if back_images:
        image_bits.append(f"{len(back_images)} uploaded back")
    generated = getattr(rendered, "generated_media", []) or []
    if generated:
        image_counts = Counter(media.role for media in generated if media.role != "audio")
        image_bits.extend(f"{count} generated {role}" for role, count in sorted(image_counts.items()))
    if image_bits:
        notes.append("Images in Anki: " + ", ".join(image_bits) + ".")
    audio_count = sum(1 for media in generated if media.role == "audio")
    if audio_count:
        notes.append(f"Audio in Anki: {audio_count} generated pronunciation.")
    fallback_reason = (getattr(rendered, "fallback_reason", None) or "").strip()
    if getattr(rendered, "fallback_used", False):
        suffix = f" ({fallback_reason})." if fallback_reason else "."
        notes.append("Fallback used: text card" + suffix)
    elif fallback_reason.startswith("voice fallback:"):
        notes.append("Voice fallback: " + fallback_reason.removeprefix("voice fallback:").strip())
    if show_usage:
        usage = format_usage_summary(getattr(rendered, "usage_summary", None))
        if usage:
            notes.append(usage)
    return "\n".join(notes)


async def _send_generated_media_previews(message, generated_media) -> None:
    if not generated_media:
        return
    for media in generated_media[:3]:
        if not os.path.exists(media.path):
            continue
        provider = (media.metadata or {}).get("provider") or (media.metadata or {}).get("model") or "generated"
        if media.role == "audio":
            if not hasattr(message, "reply_audio"):
                continue
            try:
                await message.reply_audio(
                    FSInputFile(media.path, filename=media.basename),
                    caption=f"Generated pronunciation audio ({provider}).",
                )
            except Exception as exc:
                logger.warning("Could not send generated audio preview %s: %s", media.path, exc)
            continue
        if not hasattr(message, "reply_photo"):
            continue
        try:
            await message.reply_photo(
                FSInputFile(media.path, filename=media.basename),
                caption=f"Generated card image preview ({media.role}, {provider}).",
            )
        except Exception as exc:  # preview should not block package delivery
            logger.warning("Could not send generated media preview %s: %s", media.path, exc)
        for artifact in _comparison_artifacts(media)[:2]:
            path = artifact.get("path") or ""
            if not path or not os.path.exists(path):
                continue
            provider_label = artifact.get("provider") or artifact.get("model") or "comparison"
            try:
                await message.reply_photo(
                    FSInputFile(path, filename=artifact.get("basename") or os.path.basename(path)),
                    caption=f"Comparison image preview ({provider_label}).",
                )
            except Exception as exc:
                logger.warning("Could not send comparison media preview %s: %s", path, exc)


def _comparison_artifacts(media) -> list[dict]:
    raw = (media.metadata or {}).get("comparison_alternatives") or ""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


class AnkiProcessor(IContentProcessor):
    """Turn assembled content into Anki flashcards and deliver them as an .apkg file."""

    def __init__(self, anki_card_service: AnkiCardService, preferences_repo=None, anki_graph=None):
        self.anki_card_service = anki_card_service
        self.preferences_repo = preferences_repo
        if anki_graph is None:
            raise ValueError("AnkiProcessor requires an injected AnkiGenerationGraph")
        self.anki_graph = anki_graph

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

    async def _send_debug_trace(self, message, usage=None) -> None:
        """Forward the engine's run trace to Telegram as a separate debug message (if enabled).

        Traceability/formatting lives in the engine (``format_trace_events``); this only delivers it.
        """

        if not getattr(self.anki_card_service.config, "WORKFLOW_DEBUG_TRACE_ENABLED", False):
            return
        state = getattr(self.anki_graph, "last_run_state", None) or {}
        events = state.get("trace") or []
        if not events:
            return
        from ai_workflow_engine import format_trace_events

        try:
            text = format_trace_events(
                events,
                usage=usage or state.get("usage_summary"),
                title="🔎 Anki engine trace (debug)",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not format debug trace: %s", exc)
            return
        # Telegram caps messages at 4096 chars; chunk on line boundaries. Send as PLAIN text
        # (parse_mode=None) because the trace can contain card HTML (<br>, <img>) and raw model
        # text that would otherwise break Telegram entity parsing.
        chunk = ""
        for line in text.split("\n"):
            if len(chunk) + len(line) + 1 > 3900:
                try:
                    await message.reply(chunk, parse_mode=None)
                except Exception as exc:
                    logger.warning("Could not send debug trace: %s", exc)
                    return
                chunk = ""
            chunk += line + "\n"
        if chunk.strip():
            try:
                await message.reply(chunk, parse_mode=None)
            except Exception as exc:
                logger.warning("Could not send debug trace: %s", exc)

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
        media_files = []
        buffered = False
        try:
            source = build_content_source(ctx.thread_content, ctx.user_id, ctx.owner_name, ctx.location)
            rendered = await self.anki_graph.run(source, message=message)
            cards = rendered.cards
            image_plan = rendered.image_asset_plan

            # Embed uploaded images into cards per graph image-asset decision.
            images = self._save_images(ctx.user_id, screenshots) if screenshots and image_plan.uses_uploaded_media else []
            front = [images[i][1] for i in image_plan.candidate_front_images if i < len(images)]
            back = [images[i][1] for i in image_plan.candidate_back_images if i < len(images)]
            front_html = "".join(f'<img src="{b}">' for b in front)
            back_html = "".join(f'<img src="{b}">' for b in back)
            if front_html or back_html:
                for c in cards:
                    if getattr(c, "type", "basic") == "cloze":
                        # cloze has a single Text field shown on both sides
                        c.text = f"{c.text}<br>{front_html}{back_html}"
                    else:
                        if front_html:
                            c.question = f"{c.question}<br>{front_html}"
                        if back_html:
                            c.answer = f"{c.answer}<br>{back_html}"
            generated_media_files = [media.path for media in rendered.generated_media]
            media_files = [p for p, _ in images] + generated_media_files

            deck = self._deck_name(ctx.user_id)

            # Immediate delivery of THIS message's cards.
            out_path = await asyncio.to_thread(
                self.anki_card_service.build_package, cards, deck, None, media_files
            )

            # Accumulate into the running deck (keeps media files for later export).
            anki_buffer.add(ctx.user_id, cards, media_files)
            buffered = True
            total = anki_buffer.count(ctx.user_id)

            header = (
                f"🃏 {len(cards)} card(s) — added to your deck ({total} total). "
                f"Import this file, or collect more and Export once.\n\n"
            )
            show_usage = getattr(self.anki_card_service.config, "WORKFLOW_SHOW_USAGE_IN_REPLY", False)
            notes = _format_delivery_notes(rendered, front, back, show_usage=show_usage)
            if notes:
                header += notes + "\n\n"
            caption, followups = _build_delivery_messages(header, cards)

            from keyboards.recipient import get_anki_buffer_keyboard
            await _send_generated_media_previews(message, rendered.generated_media)
            await message.reply_document(
                FSInputFile(out_path, filename="flashcards.apkg"),
                caption=caption,
                reply_markup=get_anki_buffer_keyboard(total),
            )
            for followup in followups:
                try:
                    await message.reply(followup)
                except Exception as exc:
                    logger.warning("Could not send Anki delivery follow-up: %s", exc)
            try:
                await status_msg.delete()
            except Exception:
                pass

            await self._send_debug_trace(message, rendered.usage_summary)
            logger.info(f"Delivered {len(cards)} flashcards to user {ctx.user_id} (buffer {total})")
            return ServiceResult.success_with_data(f"{len(cards)} flashcards", {"count": len(cards)})

        except Exception as e:
            logger.error(f"Anki processing failed: {e}")
            try:
                await status_msg.edit_text("❌ Could not generate flashcards from that. Please try again.")
            except Exception:
                await message.reply("❌ Could not generate flashcards from that. Please try again.")
            await self._send_debug_trace(message)
            return ServiceResult.failure(str(e))
        finally:
            # The per-message .apkg is disposable; buffer media files are kept until export/clear.
            if out_path and os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            if media_files and not buffered:
                for path in media_files:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
