"""Recipient management callback handlers."""

from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot import router
from states.recipient_states import RecipientState
from keyboards.recipient import (
    get_platform_selection_keyboard,
    get_recipient_management_keyboard,
    get_recipient_edit_keyboard,
    get_trello_configuration_keyboard,
    get_recipient_selection_keyboard
)
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)


@router.callback_query(lambda c: c.data == "add_user_platform")
async def add_user_platform(callback_query: CallbackQuery, state: FSMContext):
    """Start adding user's own account."""
    keyboard = get_platform_selection_keyboard()
    
    await callback_query.message.edit_text(
        "🔧 Add Your Account\n\n"
        "Select the type of account you want to add:",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )
    
    await state.set_state(RecipientState.selecting_platform_type)
    await callback_query.answer()


@router.callback_query(lambda c: c.data.startswith("platform_type_"))
async def handle_platform_type_selection(callback_query: CallbackQuery, state: FSMContext):
    """Handle platform type selection."""
    platform_type = callback_query.data.replace("platform_type_", "")
    
    await state.update_data(platform_type=platform_type)
    await state.set_state(RecipientState.waiting_for_credentials)
    
    # Get credential input instructions
    credentials_text = _get_credentials_instructions(platform_type)
    
    # Add cancel keyboard for navigation
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_setup")]
    ])
    
    await callback_query.message.edit_text(
        f"🔑 **{platform_type.title()} Credentials**\n\n"
        f"{credentials_text}",
        parse_mode='Markdown',
        reply_markup=cancel_keyboard,
        disable_web_page_preview=True
    )
    await callback_query.answer()


@router.callback_query(lambda c: c.data.startswith("trello_board_"))
async def handle_trello_board_selection(callback_query: CallbackQuery, state: FSMContext):
    """Handle Trello board selection."""
    board_id = callback_query.data.replace("trello_board_", "")
    
    try:
        # Get stored data
        data = await state.get_data()
        credentials = data.get('credentials')
        
        if not credentials:
            await callback_query.answer("Error: No credentials found. Please start over.")
            return
        
        # Store board selection
        await state.update_data(board_id=board_id)
        
        # Get lists for this board
        from platforms.trello import TrelloPlatform
        platform = TrelloPlatform(credentials)
        lists = platform.get_lists(board_id)
        
        if not lists:
            await callback_query.answer("❌ No lists found in this board")
            return
        
        keyboard = get_trello_list_selection_keyboard(lists)
        await callback_query.message.edit_text(
            "📋 **Select Trello List**\n\n"
            "Choose which list to use for new tasks:",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error fetching Trello lists: {e}")
        await callback_query.answer("❌ Error loading lists. Please check your credentials.")


@router.callback_query(lambda c: c.data.startswith("trello_list_"))
async def handle_trello_list_selection(callback_query: CallbackQuery, state: FSMContext):
    """Handle Trello list selection and complete setup."""
    list_id = callback_query.data.replace("trello_list_", "")
    
    try:
        # Get stored data
        data = await state.get_data()
        credentials = data.get('credentials')
        board_id = data.get('board_id')
        recipient_name = data.get('recipient_name')
        
        if not all([credentials, board_id]):
            await callback_query.answer("Error: Missing setup data. Please start over.")
            return
        
        # Create recipient with platform config
        user_id = callback_query.from_user.id
        recipient_service = container.recipient_service()
        
        platform_config = {
            'board_id': board_id,
            'list_id': list_id
        }
        
        # Check mode to determine if this should be personal or shared recipient
        mode = data.get('mode', 'user_platform')  # Default to personal for backward compatibility
        
        if mode == "shared_recipient":
            # Create shared recipient
            success = recipient_service.add_shared_recipient(
                user_id=user_id,
                name=recipient_name or "Shared Trello",
                platform_type="trello",
                credentials=credentials,
                platform_config=platform_config
            )
        else:
            # Create personal recipient (default)
            success = recipient_service.add_personal_recipient(
                user_id=user_id,
                name=recipient_name or "My Trello",
                platform_type="trello",
                credentials=credentials,
                platform_config=platform_config
            )
        
        if success:
            # Add navigation after success
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            success_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎯 Create Task", callback_data="create_task")],
                [InlineKeyboardButton(text="🏠 Back to Menu", callback_data="back_to_menu")]
            ])
            
            await callback_query.message.edit_text(
                "✅ **Trello Account Added Successfully!**\n\n"
                "Your account is now connected and ready to use.\n\n"
                "🎯 You can now create tasks that will appear in your selected Trello list.",
                reply_markup=success_keyboard,
                disable_web_page_preview=True
            )
            await state.clear()
        else:
            # Add retry/cancel buttons for errors
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            error_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Try Again", callback_data="add_user_platform")],
                [InlineKeyboardButton(text="🏠 Back to Menu", callback_data="back_to_menu")]
            ])
            
            await callback_query.message.edit_text(
                "❌ **Failed to Add Account**\n\n"
                "There was an error saving your account. Please try again.",
                reply_markup=error_keyboard,
                disable_web_page_preview=True
            )
            
    except Exception as e:
        logger.error(f"Error completing Trello setup: {e}")
        await callback_query.answer("❌ Error completing setup")


@router.callback_query(lambda c: c.data == "cancel_setup")
async def cancel_setup(callback_query: CallbackQuery, state: FSMContext):
    """Cancel recipient setup."""
    await state.clear()
    await callback_query.message.edit_text(
        "❌ Setup cancelled.\n\n"
        "You can start again anytime using /recipients",
        disable_web_page_preview=True
    )
    await callback_query.answer()


@router.callback_query(lambda c: c.data == "back_to_trello_boards")
async def back_to_trello_boards(callback_query: CallbackQuery, state: FSMContext):
    """Go back to Trello board selection."""
    try:
        data = await state.get_data()
        credentials = data.get('credentials')
        
        if not credentials:
            await callback_query.answer("Error: No credentials found")
            return
            
        from platforms.trello import TrelloPlatform
        platform = TrelloPlatform(credentials)
        boards = platform.get_user_boards()
        
        keyboard = get_trello_board_selection_keyboard(boards)
        await callback_query.message.edit_text(
            "📋 **Select Trello Board**\n\n"
            "Choose which board to use:",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error going back to boards: {e}")
        await callback_query.answer("❌ Error loading boards")


@router.callback_query(lambda c: c.data == "add_shared_recipient")
async def add_shared_recipient(callback_query: CallbackQuery, state: FSMContext):
    """Start adding shared recipient."""
    # Add cancel keyboard for shared recipient name input
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_recipients")]
    ])
    
    await callback_query.message.edit_text(
        "👥 **Add Shared Recipient**\n\n"
        "Enter the name for this shared recipient:",
        reply_markup=cancel_keyboard,
        disable_web_page_preview=True
    )
    await state.set_state(RecipientState.waiting_for_recipient_name)
    await callback_query.answer()


@router.callback_query(lambda c: c.data.startswith("recipient_edit_"))
async def handle_recipient_action(callback_query: CallbackQuery, state: FSMContext):
    """Handle recipient-related actions."""
    recipient_id = int(callback_query.data.replace("recipient_edit_", ""))
    user_id = callback_query.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        recipient = recipient_service.get_recipient_by_id(user_id, recipient_id)
        
        if not recipient:
            await callback_query.answer("❌ Recipient not found")
            return
            
        keyboard = get_recipient_edit_keyboard(recipient.id, recipient.platform_type)
        
        status = "✅ Active" if recipient.enabled else "❌ Disabled"
        text = f"🎯 **{recipient.name}**\n\n"
        text += f"📱 Platform: {recipient.platform_type.title()}\n"
        text += f"📊 Status: {status}\n\n"
        text += "What would you like to do?"
        
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Error handling recipient action: {e}")
        await callback_query.answer("❌ Error loading recipient")


@router.callback_query(lambda c: c.data.startswith("toggle_recipient_") and c.data != "toggle_recipient_ui")
async def handle_toggle_recipient(callback_query: CallbackQuery, state: FSMContext):
    """Toggle recipient enabled/disabled status."""
    recipient_id = int(callback_query.data.replace("toggle_recipient_", ""))
    user_id = callback_query.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        
        # Toggle the recipient
        success = recipient_service.toggle_recipient_enabled(user_id, recipient_id)
        
        if success:
            # Get updated recipient to show new status
            recipient = recipient_service.get_recipient_by_id(user_id, recipient_id)
            status = "enabled" if recipient.enabled else "disabled"
            
            await callback_query.answer(f"✅ {recipient.name} {status}")
            
            # Update the message to show new status
            keyboard = get_recipient_edit_keyboard(recipient.id, recipient.platform_type)
            status_text = "✅ Active" if recipient.enabled else "❌ Disabled"
            text = f"🎯 **{recipient.name}**\n\n"
            text += f"📱 Platform: {recipient.platform_type.title()}\n"
            text += f"📊 Status: {status_text}\n\n"
            text += "What would you like to do?"
            
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
        else:
            await callback_query.answer("❌ Failed to update recipient")
            
    except Exception as e:
        logger.error(f"Error toggling recipient: {e}")
        await callback_query.answer("❌ Error updating recipient")


@router.callback_query(lambda c: c.data.startswith("configure_recipient_"))
async def handle_configure_recipient(callback_query: CallbackQuery, state: FSMContext):
    """Configure recipient platform settings."""
    recipient_id = int(callback_query.data.replace("configure_recipient_", ""))
    user_id = callback_query.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        recipient = recipient_service.get_recipient_by_id(user_id, recipient_id)
        
        if not recipient:
            await callback_query.answer("❌ Recipient not found")
            return
            
        if recipient.platform_type == "trello":
            # For Trello, show board/list configuration
            keyboard = get_trello_configuration_keyboard()
            await callback_query.message.edit_text(
                f"🔧 **Configure {recipient.name}**\n\n"
                "Trello Configuration Options:",
                reply_markup=keyboard,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            
            # Store recipient_id for configuration
            await state.update_data(configuring_recipient_id=recipient_id)
        else:
            await callback_query.answer("❌ Configuration not available for this platform")
            
    except Exception as e:
        logger.error(f"Error configuring recipient: {e}")
        await callback_query.answer("❌ Error loading configuration")


def _get_credentials_instructions(platform_type: str) -> str:
    """Get credential input instructions for platform."""
    if platform_type == "todoist":
        return ("📝 TODOIST SETUP GUIDE\n\n"
                "1. Go to https://app.todoist.com/app/settings/integrations\n"
                "2. Copy your API Token\n"
                "3. Send it here\n\n"
                "🔒 Your token will be stored securely.")
    elif platform_type == "trello":
        return ("📋 TRELLO SETUP GUIDE\n\n"
                "1. Go to https://trello.com/app-key\n"
                "2. Copy your API Key from the top\n"
                "3. Click 'Token' link (or visit manually generated token)\n"
                "4. Allow access and copy the Token\n"
                "5. Send them in this format:\n"
                "`api_key,token`\n\n"
                "🔒 Your credentials will be stored securely.")
    else:
        return "Enter your account credentials:"


def get_trello_list_selection_keyboard(lists):
    """Create keyboard for Trello list selection."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for trello_list in lists:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"📝 {trello_list['name']}",
                callback_data=f"trello_list_{trello_list['id']}"
            )
        ])
    
    # Add back button
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="⬅️ Back to Boards", callback_data="back_to_trello_boards")
    ])
    
    return keyboard


@router.callback_query(lambda c: c.data.startswith("select_recipient_"))
async def handle_recipient_selection(callback_query: CallbackQuery, state: FSMContext):
    """Handle recipient selection for task creation."""
    recipient_id = callback_query.data.replace("select_recipient_", "")
    
    try:
        state_data = await state.get_data()
        selected_recipients = state_data.get('selected_recipients', [])
        
        # Toggle recipient selection
        if recipient_id in selected_recipients:
            selected_recipients.remove(recipient_id)
        else:
            selected_recipients.append(recipient_id)
        
        await state.update_data(selected_recipients=selected_recipients)
        
        # Update keyboard to show selection
        user_id = callback_query.from_user.id
        recipient_service = container.recipient_service()
        recipients = recipient_service.get_enabled_recipients(user_id)
        
        keyboard = get_recipient_selection_keyboard(recipients, selected_recipients)
        
        selected_count = len(selected_recipients)
        await callback_query.message.edit_text(
            f"🎯 *Create Task*\n\n"
            f"Choose recipients for your task:\n"
            f"Selected: {selected_count} recipients",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
        await callback_query.answer(f"{'Added' if recipient_id in selected_recipients else 'Removed'} recipient")
        
    except Exception as e:
        logger.error(f"Failed to handle recipient selection: {e}")
        await callback_query.answer("Error updating selection")


@router.callback_query(lambda c: c.data == "confirm_recipients")
async def confirm_recipients(callback_query: CallbackQuery, state: FSMContext):
    """Confirm recipient selection and proceed to task input."""
    try:
        state_data = await state.get_data()
        selected_recipients = state_data.get('selected_recipients', [])
        
        if not selected_recipients:
            await callback_query.answer("Please select at least one recipient")
            return
        
        # Add cancel keyboard for task input
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_menu")]
        ])
        
        await callback_query.message.edit_text(
            f"📝 *Create Task*\n\n"
            f"Recipients selected: {len(selected_recipients)}\n\n"
            f"Now enter your task description:",
            parse_mode='Markdown',
            reply_markup=cancel_keyboard,
            disable_web_page_preview=True
        )
        
        await state.set_state(RecipientState.waiting_for_task)
        await callback_query.answer("Recipients confirmed!")
        
    except Exception as e:
        logger.error(f"Failed to confirm recipients: {e}")
        await callback_query.answer("Error confirming selection")


@router.callback_query(lambda c: c.data == "back_to_recipients")
async def back_to_recipients(callback_query: CallbackQuery, state: FSMContext):
    """Go back to recipient management."""
    user_id = callback_query.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        recipients = recipient_service.get_recipients_by_user(user_id)
        
        if not recipients:
            keyboard = get_recipient_management_keyboard()
            await callback_query.message.edit_text(
                "🎯 *Recipients Management*\n\n"
                "No recipients configured yet.\n\n"
                "Add your first recipient to get started:",
                reply_markup=keyboard,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        else:
            keyboard = get_recipient_management_keyboard(recipients)
            text = f"🎯 *Recipients Management*\n\n"
            text += f"You have {len(recipients)} recipients configured.\n\n"
            text += "Choose an option:"
            
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
            
    except Exception as e:
        logger.error(f"Error going back to recipients: {e}")
        await callback_query.answer("❌ Error loading recipients")


def get_trello_board_selection_keyboard(boards):
    """Create keyboard for Trello board selection.""" 
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for board in boards:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"📋 {board['name']}",
                callback_data=f"trello_board_{board['id']}"
            )
        ])
    
    # Add cancel button
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_setup")
    ])
    
    return keyboard


@router.callback_query(lambda c: c.data.startswith("recipient_remove_"))
async def handle_recipient_removal(callback_query: CallbackQuery, state: FSMContext):
    """Handle recipient removal request."""
    try:
        recipient_id = int(callback_query.data.replace("recipient_remove_", ""))
        recipient_service = container.recipient_service()
        user_id = callback_query.from_user.id
        
        # Get recipient to show confirmation
        recipient = recipient_service.get_recipient_by_id(user_id, recipient_id)
        
        if not recipient:
            await callback_query.answer("❌ Recipient not found")
            return
            
        # Show confirmation dialog
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Yes, Delete", callback_data=f"confirm_remove_{recipient_id}")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"recipient_edit_{recipient_id}")]
        ])
        
        await callback_query.message.edit_text(
            f"🗑️ **Delete Account**\n\n"
            f"Are you sure you want to delete:\n"
            f"**{recipient.name}** ({recipient.platform_type.title()})\n\n"
            f"⚠️ This action cannot be undone.",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error handling recipient removal: {e}")
        await callback_query.answer("❌ Error deleting account")


@router.callback_query(lambda c: c.data.startswith("confirm_remove_"))
async def handle_confirm_recipient_removal(callback_query: CallbackQuery, state: FSMContext):
    """Handle confirmed recipient removal."""
    try:
        recipient_id = int(callback_query.data.replace("confirm_remove_", ""))
        recipient_service = container.recipient_service()
        user_id = callback_query.from_user.id
        
        # Remove the recipient
        success = recipient_service.remove_recipient(user_id, recipient_id)
        
        if success:
            await callback_query.answer("✅ Account deleted successfully")
            
            # Return to main recipients page
            recipients = recipient_service.get_recipients_by_user(user_id)
            keyboard = get_recipient_management_keyboard(recipients)
            
            await callback_query.message.edit_text(
                "📱 **Account Management**\n\n"
                "Manage your connected accounts and shared recipients:",
                reply_markup=keyboard,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        else:
            await callback_query.answer("❌ Failed to delete account")
            # Stay on current page
            
    except Exception as e:
        logger.error(f"Error confirming recipient removal: {e}")
        await callback_query.answer("❌ Error confirming deletion")