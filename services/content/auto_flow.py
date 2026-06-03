"""Auto-mode 5s pre-commit window.

When content_mode == auto we classify the message, show the guess with override buttons, and
create NOTHING for 5 seconds. If the user taps a button the timer is cancelled and the chosen
type is created; otherwise the guess is committed once. Because nothing is created until commit,
there are no inconsistent side effects.
"""

import asyncio
from typing import Dict

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from core.interfaces import Intent, ProcessingContext
from core.logging import get_logger
from services.content.router import get_processor

logger = get_logger(__name__)

AUTO_WINDOW_SECONDS = 5

# token -> {"ctx": ProcessingContext, "status_msg": Message, "task": asyncio.Task}
_pending: Dict[str, dict] = {}
_counter = 0

_GUESS_LABEL = {Intent.REMINDER: "📝 a reminder", Intent.ANKI: "🃏 flashcards"}


def _next_token() -> str:
    global _counter
    _counter += 1
    return str(_counter)


def _keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📝 Reminder", callback_data=f"autocommit:reminder:{token}"),
        InlineKeyboardButton(text="🃏 Card", callback_data=f"autocommit:anki:{token}"),
    ]])


async def start_auto_window(ctx: ProcessingContext, guess: Intent) -> None:
    """Show the guess + override buttons and schedule the delayed commit."""
    token = _next_token()
    status_msg = await ctx.message.reply(
        f"🤔 Looks like {_GUESS_LABEL[guess]}. Creating in {AUTO_WINDOW_SECONDS}s — tap to change.",
        reply_markup=_keyboard(token),
    )
    task = asyncio.create_task(_commit_after_delay(token, guess))
    _pending[token] = {"ctx": ctx, "status_msg": status_msg, "task": task}


async def _commit_after_delay(token: str, guess: Intent) -> None:
    try:
        await asyncio.sleep(AUTO_WINDOW_SECONDS)
    except asyncio.CancelledError:
        return
    entry = _pending.pop(token, None)
    if entry:
        await _finalize(entry, guess, auto=True)


async def commit_choice(token: str, intent: Intent) -> bool:
    """Called by the button handler. Returns False if the window already closed."""
    entry = _pending.pop(token, None)
    if not entry:
        return False
    task = entry.get("task")
    if task and not task.done():
        task.cancel()
    await _finalize(entry, intent, auto=False)
    return True


async def _finalize(entry: dict, intent: Intent, auto: bool) -> None:
    ctx = entry["ctx"]
    status_msg = entry["status_msg"]
    label = "🃏 flashcards" if intent == Intent.ANKI else "📝 reminder"
    prefix = "Auto-detected" if auto else "You chose"
    try:
        await status_msg.edit_text(f"{prefix}: creating {label}…")
    except Exception:
        pass
    processor = get_processor(intent)
    await processor.process(ctx)
