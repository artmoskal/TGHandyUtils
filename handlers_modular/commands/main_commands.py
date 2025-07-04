"""Main bot commands."""

from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot import router
from keyboards.recipient import get_recipient_management_keyboard
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)


@router.message(Command('start'))
async def cmd_start(message: Message, state: FSMContext):
    """Start command - show recipient management."""
    await message.reply(
        "🎯 Welcome to Task Bot!\n\n"
        "I help you create tasks on your Todoist and Trello accounts.\n\n"
        "🚀 QUICK START:\n"
        "1️⃣ Add your accounts: /recipients\n"
        "2️⃣ Create tasks: /create_task\n\n"
        "💡 First time? Start with /recipients to connect your Todoist or Trello account!"
    )


@router.message(Command('recipients'))
async def show_recipient_management(message: Message, state: FSMContext):
    """Show recipient management interface."""
    user_id = message.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        recipients = recipient_service.get_all_recipients(user_id)
        
        keyboard = get_recipient_management_keyboard(recipients)
        
        if recipients:
            text = "🎯 PLATFORM MANAGEMENT\n\n"
            text += "📱 Your connected accounts:\n\n"
            for recipient in recipients:
                status = "✅ Active" if recipient.enabled else "❌ Disabled"
                platform_emoji = "📝" if recipient.platform_type == "todoist" else "📋"
                text += f"{platform_emoji} {recipient.name}\n   Status: {status}\n\n"
            text += "💡 Tap any account above to edit it."
        else:
            text = "🎯 PLATFORM SETUP\n\n"
            text += "👋 Welcome! You haven't connected any accounts yet.\n\n"
            text += "🎯 What are recipients?\n"
            text += "Recipients are your Todoist/Trello accounts where tasks will be created.\n\n"
            text += "🚀 Get started by adding your first account below!"
        
        await message.reply(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Failed to show recipient management for user {user_id}: {e}")
        await message.reply("Error loading recipient management. Please try again.")