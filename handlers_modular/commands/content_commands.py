"""/anki and /tasks commands: per-message overrides of the content mode.

  /anki <text>   -> make flashcards from <text> now
  /anki          -> next message becomes flashcards
  /tasks <text>  -> make a reminder from <text> now
  /tasks         -> next message becomes a reminder
"""

from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot import router
from composition.container import container
from core.interfaces import Intent
from core.logging import get_logger
from helpers.error_messages import ErrorMessages
from services.content import overrides

logger = get_logger(__name__)


async def _handle_content_command(message: Message, command: CommandObject, intent: Intent):
    user_id = message.from_user.id
    args = (command.args or "").strip()

    # /anki help -> show the tag reference
    if intent == Intent.ANKI and args.lower() in ("help", "h", "?"):
        from services.content.anki_directives import HELP_TEXT
        await message.reply(HELP_TEXT, parse_mode="Markdown", disable_web_page_preview=True)
        return

    recipient_service = container.recipient_service()
    prefs = recipient_service.get_user_preferences(user_id)
    owner_name = prefs.owner_name if prefs else "User"
    location = prefs.location if prefs else None

    # No content -> arm a one-shot override for the next message.
    if not args:
        overrides.arm(user_id, intent)
        if intent == Intent.ANKI:
            await message.reply(
                "🃏 Send me the content and I'll turn it into flashcards.\n\n"
                "Tip: use `/anki help` to see formatting tags (image placement, "
                "question style, etc.).",
                parse_mode="Markdown",
            )
        else:
            await message.reply("📝 Send me what you'd like to be reminded about.")
        return

    # Reminders need a destination; flashcards do not.
    if intent == Intent.REMINDER and not recipient_service.get_enabled_recipients(user_id):
        await message.reply(ErrorMessages.NO_RECIPIENTS_SETUP_HELP)
        return

    from handlers_modular.message.text_handler import process_thread_with_photos
    thread_content = [(owner_name or "User", args)]
    await process_thread_with_photos(
        message, thread_content, owner_name, location, user_id, intent_override=intent
    )


@router.message(Command("anki"))
async def cmd_anki(message: Message, command: CommandObject, state: FSMContext):
    await _handle_content_command(message, command, Intent.ANKI)


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, command: CommandObject, state: FSMContext):
    await _handle_content_command(message, command, Intent.REMINDER)
