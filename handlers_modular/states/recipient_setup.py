"""Recipient setup state handlers."""

from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot import router
from states.recipient_states import RecipientState
from keyboards.recipient import get_platform_selection_keyboard
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)


@router.message(RecipientState.waiting_for_credentials)
async def handle_credentials_input(message: Message, state: FSMContext):
    """Handle credentials input for platform."""
    user_id = message.from_user.id
    credentials = message.text.strip()
    
    try:
        state_data = await state.get_data()
        platform_type = state_data.get('platform_type')
        mode = state_data.get('mode', 'user_platform')
        
        recipient_service = container.recipient_service()
        
        logger.error(f"🔍 CREDENTIALS HANDLER: user={user_id}, platform={platform_type}, mode={mode}, state_data={state_data}")
        
        # For Trello, we need additional configuration (list selection)
        if platform_type == "trello":
            await state.update_data(credentials=credentials)
            await handle_trello_configuration(message, state)
            return
        
        if mode == "user_platform":
            # Add personal recipient
            name = f"My {platform_type.title()}"
            logger.error(f"🔍 CREATING PERSONAL RECIPIENT: {name}")
            recipient_id = recipient_service.add_personal_recipient(
                user_id=user_id,
                name=name,
                platform_type=platform_type,
                credentials=credentials
            )
            
            await message.reply(
                f"✅ Successfully connected your {platform_type.title()}!\n\n"
                f"🎯 What's next?\n"
                f"• Use /create_task to create your first task\n"
                f"• Use /recipients to manage your accounts\n\n"
                f"💡 Try it now: Just send me any message and I'll create a task!",
                disable_web_page_preview=True
            )
            
        elif mode == "shared_recipient":
            # Add shared recipient
            name = state_data.get('recipient_name', f"Shared {platform_type.title()}")
            logger.error(f"🔍 CREATING SHARED RECIPIENT: {name}")
            
            recipient_id = recipient_service.add_shared_recipient(
                user_id=user_id,
                name=name,
                platform_type=platform_type,
                credentials=credentials
            )
            
            await message.reply(
                f"✅ Successfully added shared recipient '{name}'!\n\n"
                f"🎯 What's next?\n"
                f"• Use /create_task to create tasks\n"
                f"• Use /recipients to manage recipients\n\n"
                f"💡 Tasks will now be sent to both your accounts and shared recipients!",
                disable_web_page_preview=True
            )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Failed to handle credentials for user {user_id}: {e}")
        await message.reply("❌ Failed to connect account. Please check your credentials and try again.", disable_web_page_preview=True)
        await state.clear()


@router.message(RecipientState.waiting_for_recipient_name)
async def handle_recipient_name(message: Message, state: FSMContext):
    """Handle recipient name input."""
    name = message.text.strip()
    
    if not name:
        await message.reply("❌ Name cannot be empty. Please enter a name:", disable_web_page_preview=True)
        return
    
    await state.update_data(recipient_name=name, mode="shared_recipient")
    
    keyboard = get_platform_selection_keyboard()
    
    await message.reply(
        f"👥 *Adding: {name}*\n\n"
        "Select the account type:",
        reply_markup=keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    
    await state.set_state(RecipientState.selecting_platform_type)


async def handle_trello_configuration(message: Message, state: FSMContext):
    """Handle Trello board/list configuration after credentials."""
    try:
        state_data = await state.get_data()
        credentials = state_data.get('credentials')
        
        if not credentials:
            await message.reply("❌ No credentials found. Please start over.", disable_web_page_preview=True)
            await state.clear()
            return
        
        # Get boards using the credentials
        from platforms.trello import TrelloPlatform
        platform = TrelloPlatform(credentials)
        boards = platform.get_boards()
        
        if not boards:
            await message.reply("❌ No boards found. Please check your credentials.", disable_web_page_preview=True)
            await state.clear()
            return
        
        # Import the keyboard function from management.py
        from handlers_modular.callbacks.recipient.management import get_trello_board_selection_keyboard
        keyboard = get_trello_board_selection_keyboard(boards)
        
        await message.reply(
            "📋 **Select Trello Board**\n\n"
            "Choose which board to use:",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error handling Trello configuration: {e}")
        await message.reply("❌ Error loading Trello boards. Please check your credentials.", disable_web_page_preview=True)
        await state.clear()