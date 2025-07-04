"""Menu and utility commands."""

from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot import router
from keyboards.recipient import get_main_menu_keyboard
from core.logging import get_logger

logger = get_logger(__name__)


@router.message(Command('menu'))
async def show_main_menu(message: Message, state: FSMContext):
    """Show main menu."""
    keyboard = get_main_menu_keyboard()
    await message.reply(
        "🎯 *Main Menu*\n\nChoose what you'd like to do:",
        reply_markup=keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )


@router.message(Command('cancel'))
async def cancel_command(message: Message, state: FSMContext):
    """Cancel any ongoing operation and clear state."""
    await state.clear()
    await message.reply("❌ Cancelled. You can now use any command normally.", disable_web_page_preview=True)