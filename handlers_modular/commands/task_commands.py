"""Task-related commands."""

from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot import router
from states.recipient_states import RecipientState
from keyboards.recipient import get_recipient_selection_keyboard
from core.container import container
from core.logging import get_logger
from helpers.error_messages import ErrorMessages

logger = get_logger(__name__)


@router.message(Command('create_task'))
async def create_task_with_recipients(message: Message, state: FSMContext):
    """Create task with recipient selection."""
    user_id = message.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        recipients = recipient_service.get_enabled_recipients(user_id)
        
        if not recipients:
            await message.reply(
                ErrorMessages.NO_RECIPIENTS_SETUP_HELP,
                disable_web_page_preview=True
            )
            return
        
        # Check if recipient UI is enabled
        if recipient_service.is_recipient_ui_enabled(user_id):
            # Show recipient selection
            keyboard = get_recipient_selection_keyboard(recipients, [])
            
            await message.reply(
                "🎯 *Create Task*\n\n"
                "First, choose recipients for your task:",
                reply_markup=keyboard,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            
            await state.set_state(RecipientState.selecting_recipients)
            await state.update_data(selected_recipients=[])
        else:
            # Use default recipients
            await message.reply(
                "📝 Enter your task description (will be created on default recipients):",
                disable_web_page_preview=True
            )
            await state.set_state(RecipientState.waiting_for_task)
        
    except Exception as e:
        logger.error(f"Failed to start task creation for user {user_id}: {e}")
        await message.reply("Error starting task creation. Please try again.", disable_web_page_preview=True)