"""Main bot commands."""

from datetime import datetime, timedelta
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from bot import router
from keyboards.recipient import get_recipient_management_keyboard
from composition.container import container
from core.logging import get_logger
from helpers.ui_helpers import escape_markdown

logger = get_logger(__name__)


@router.message(Command('start'))
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    """Start command - handle auth parameters or show welcome."""
    # Cache user info for future lookups
    if message.from_user.username:
        from services.user_lookup_service import UserLookupService
        lookup_service = UserLookupService(container.database_manager())
        lookup_service.cache_user(message.from_user.id, message.from_user.username)
    
    # Check for auth parameter
    if command.args and command.args.startswith('auth_'):
        encoded = command.args[5:]  # Remove 'auth_' prefix
        
        from utils.auth_link_utils import decode_auth_request_data
        data = decode_auth_request_data(encoded)
        
        if data:
            # Create the actual auth request NOW with real user_id
            auth_request_repo = container.auth_request_repository()
            expires_at = datetime.utcnow() + timedelta(hours=24)
            
            # For now, just show the request details since auth_request functionality is incomplete
            await message.reply(
                f"🔐 **Authentication Request**\n\n"
                f"**From:** {escape_markdown(data['requester_name'])}\n"
                f"**Platform:** {escape_markdown(data['platform_type'].title())}\n"
                f"**Account Name:** {escape_markdown(data['recipient_name'])}\n\n"
                f"⚠️ This feature is currently under development.\n\n"
                f"To set up this account:\n"
                f"1. Use /recipients to add a new {escape_markdown(data['platform_type'].title())} account\n"
                f"2. Let {escape_markdown(data['requester_name'])} know when it's ready",
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            return
        else:
            await message.reply("❌ Invalid or expired link.")
    
    # Normal start message
    await message.reply(
        "🎯 Welcome to Task Bot!\n\n"
        "I help you create tasks on your Todoist and Trello accounts.\n\n"
        "🚀 QUICK START:\n"
        "1️⃣ Add your accounts: /recipients\n"
        "2️⃣ Create tasks: /create_task\n\n"
        "💡 First time? Start with /recipients to connect your Todoist or Trello account!",
        disable_web_page_preview=True
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
        
        await message.reply(text, reply_markup=keyboard, disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Failed to show recipient management for user {user_id}: {e}")
        await message.reply("Error loading recipient management. Please try again.", disable_web_page_preview=True)