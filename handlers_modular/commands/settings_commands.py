"""Settings-related commands."""

from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot import router
from keyboards.recipient import get_settings_main_keyboard, get_delete_confirmation_keyboard
from ..utils import (
    UserHelper, 
    MessageResponses, 
    FormattedResponses, 
    ErrorHandler,
    command_handler_errors
)

from core.logging import get_logger

logger = get_logger(__name__)


@router.message(Command('settings'))
@command_handler_errors
async def show_settings(message: Message, state: FSMContext):
    """Show user settings."""
    user_id = message.from_user.id
    ErrorHandler.log_user_action("show_settings", user_id, "Settings command called")
    
    # Clear any existing state to prevent conflicts
    await state.clear()
    
    # Get formatted user settings
    settings = UserHelper.format_user_settings(user_id)
    
    # Build settings display using formatted response
    text = FormattedResponses.settings_display(
        settings['owner_name'],
        settings['location'], 
        settings['notifications'],
        settings['recipient_ui']
    )
    
    keyboard = get_settings_main_keyboard()
    await MessageResponses.info_reply(message, text, keyboard)


@router.message(Command('drop_user_data'))
async def initiate_drop_user_data(message: Message, state: FSMContext):
    """Initiate user data deletion process."""
    keyboard = get_delete_confirmation_keyboard()
    await message.reply(
        "⚠️ *DELETE ALL DATA*\n\n"
        "This will permanently delete:\n"
        "• All your connected accounts\n"
        "• All shared recipients\n"  
        "• All your preferences\n"
        "• All associated data\n\n"
        "*This action cannot be undone!*\n\n"
        "Are you sure you want to continue?",
        reply_markup=keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )