import json
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from core.container import container
from core.logging import get_logger
from states.recipient_states import RecipientState
from states.sharing_states import SharingState, AuthRequestState

logger = get_logger(__name__)
router = Router()

@router.message(Command("share"))
async def handle_share_command(message: Message, state: FSMContext):
    """Start sharing workflow with two options."""
    user_id = message.from_user.id
    
    # Cache user info
    user_service = container.user_service()
    user_service.cache_user_info(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Share Existing Account", callback_data="share_existing")],
        [InlineKeyboardButton(text="🔐 Request New Authentication", callback_data="request_auth")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_sharing")]
    ])
    
    await message.reply(
        "🤝 **Account Sharing Options**\n\n"
        "**Share Existing Account:** Share one of your connected accounts\n"
        "**Request New Authentication:** Ask someone to authenticate a new account for you\n\n"
        "What would you like to do?",
        reply_markup=keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await state.set_state(SharingState.selecting_share_type)

@router.callback_query(lambda c: c.data == "request_auth")
async def handle_request_auth(callback_query: CallbackQuery, state: FSMContext):
    """Handle authentication request flow."""
    # Show platform selection
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Todoist", callback_data="auth_platform_todoist")],
        [InlineKeyboardButton(text="📋 Trello", callback_data="auth_platform_trello")],
        [InlineKeyboardButton(text="📅 Google Calendar", callback_data="auth_platform_google_calendar")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_sharing")]
    ])
    
    await callback_query.message.edit_text(
        "🔐 **Request Authentication**\n\n"
        "Select the platform you want someone to authenticate for you:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await state.set_state(AuthRequestState.selecting_platform)

@router.callback_query(lambda c: c.data.startswith("auth_platform_"))
async def handle_auth_platform_selection(callback_query: CallbackQuery, state: FSMContext):
    """Handle platform selection for auth request."""
    platform_type = callback_query.data.replace("auth_platform_", "")
    await state.update_data(platform_type=platform_type)
    
    await callback_query.message.edit_text(
        f"📝 **Name Your {platform_type.title()} Account**\n\n"
        f"Enter a name for this account (e.g., 'Work {platform_type.title()}', 'Personal Tasks'):",
        parse_mode='Markdown'
    )
    await state.set_state(AuthRequestState.waiting_for_account_name)

@router.message(AuthRequestState.waiting_for_account_name)
async def handle_account_name_input(message: Message, state: FSMContext):
    """Handle account name input."""
    account_name = message.text.strip()
    
    if len(account_name) < 3 or len(account_name) > 50:
        await message.reply("❌ Account name must be between 3 and 50 characters.")
        return
    
    await state.update_data(account_name=account_name)
    
    await message.reply(
        "👤 **Enter Username**\n\n"
        "Enter the Telegram username (with or without @) of the person who will authenticate this account:\n\n"
        "⚠️ They must have used this bot before.",
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await state.set_state(AuthRequestState.waiting_for_target_username)

@router.message(AuthRequestState.waiting_for_target_username)
async def handle_auth_target_username(message: Message, state: FSMContext):
    """Handle target username for auth request."""
    username = message.text.strip().lstrip('@')
    
    if not username or len(username) < 3:
        await message.reply("❌ Please enter a valid username.")
        return
    
    try:
        state_data = await state.get_data()
        platform_type = state_data['platform_type']
        account_name = state_data['account_name']
        
        # Create auth request
        sharing_service = container.sharing_service()
        auth_request_id = sharing_service.create_auth_request(
            requester_user_id=message.from_user.id,
            target_username=username,
            platform_type=platform_type,
            recipient_name=account_name
        )
        
        # Send notification to target user
        await send_auth_request_notification(auth_request_id, message.from_user.first_name)
        
        await message.reply(
            f"✅ **Authentication Request Sent!**\n\n"
            f"📤 **Sent to:** @{username}\n"
            f"📋 **Account:** {account_name} ({platform_type.title()})\n"
            f"⏰ **Expires in:** 24 hours\n\n"
            f"They will receive instructions to authenticate the account.\n"
            f"You'll be notified when complete.\n\n"
            f"Use /requests to view pending requests.",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error creating auth request: {e}")
        await message.reply(f"❌ **Error:** {str(e)}")
        await state.clear()

async def send_auth_request_notification(auth_request_id: int, requester_name: str):
    """Send authentication request notification."""
    try:
        repository = container.unified_recipient_repository()
        auth_request = repository.get_auth_request_by_id(auth_request_id)
        
        if not auth_request:
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔐 Authenticate Now", callback_data=f"auth_request_{auth_request_id}")],
            [InlineKeyboardButton(text="❌ Decline", callback_data=f"decline_auth_{auth_request_id}")]
        ])
        
        from core.container import container
        bot = container.bot()
        
        await bot.send_message(
            chat_id=auth_request.target_user_id,
            text=f"🔐 **Authentication Request**\n\n"
                 f"**From:** {requester_name}\n"
                 f"**Platform:** {auth_request.platform_type.title()}\n"
                 f"**Account Name:** {auth_request.recipient_name}\n"
                 f"**Expires:** In 24 hours\n\n"
                 f"{requester_name} is asking you to authenticate a {auth_request.platform_type.title()} account for them.\n\n"
                 f"If you accept, you'll go through the normal account setup process, but the account will be added to their recipients list instead of yours.",
            reply_markup=keyboard,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error sending auth request notification: {e}")

@router.callback_query(lambda c: c.data.startswith("auth_request_"))
async def handle_auth_request_acceptance(callback_query: CallbackQuery, state: FSMContext):
    """Handle authentication request acceptance."""
    try:
        auth_request_id = int(callback_query.data.replace("auth_request_", ""))
        user_id = callback_query.from_user.id
        
        # Validate request
        repository = container.unified_recipient_repository()
        auth_request = repository.get_auth_request_by_id(auth_request_id)
        
        if not auth_request or auth_request.target_user_id != user_id:
            await callback_query.answer("❌ Invalid request.", show_alert=True)
            return
        
        if not auth_request.is_active():
            await callback_query.message.edit_text(
                "❌ **Request Expired**\n\n"
                "This authentication request has expired or is no longer active."
            )
            return
        
        # Store auth request ID in state for completion
        await state.update_data(auth_request_id=auth_request_id)
        
        # Start platform-specific authentication flow
        platform_type = auth_request.platform_type
        
        if platform_type == 'google_calendar':
            # Start OAuth flow
            google_oauth_service = container.google_oauth_service()
            oauth_url = google_oauth_service.get_authorization_url(user_id)
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Open Google Authorization", url=oauth_url)],
                [InlineKeyboardButton(text="📝 I Have the Code", callback_data="enter_auth_code_for_request")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel_auth_{auth_request_id}")]
            ])
            
            await callback_query.message.edit_text(
                f"🔐 **Authenticate {auth_request.recipient_name}**\n\n"
                f"You're authenticating this account for {auth_request.requester_user_id}\n\n"
                "1. Click 'Open Google Authorization'\n"
                "2. Sign in and grant permissions\n"
                "3. Copy the authorization code\n"
                "4. Click 'I Have the Code' and paste it\n\n"
                "⚠️ The account will be added to their recipients, not yours.",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            await state.set_state(AuthRequestState.waiting_for_oauth)
            
        else:
            # For other platforms, ask for credentials directly
            await callback_query.message.edit_text(
                f"🔑 **Authenticate {auth_request.recipient_name}**\n\n"
                f"Enter your {platform_type.title()} credentials:\n\n"
                f"{get_platform_credential_instructions(platform_type)}\n\n"
                "⚠️ The account will be added to their recipients, not yours.",
                parse_mode='Markdown'
            )
            await state.set_state(AuthRequestState.waiting_for_credentials)
            
    except Exception as e:
        logger.error(f"Error handling auth request: {e}")
        await callback_query.answer("❌ Error processing request.", show_alert=True)

@router.callback_query(lambda c: c.data == "enter_auth_code_for_request")
async def handle_enter_auth_code_for_request(callback_query: CallbackQuery, state: FSMContext):
    """Handle entering OAuth code for auth request."""
    await callback_query.message.edit_text(
        "📝 **Enter Authorization Code**\n\n"
        "Paste the authorization code you copied from Google:",
        parse_mode='Markdown'
    )
    await state.set_state(AuthRequestState.waiting_for_auth_code)

@router.message(AuthRequestState.waiting_for_auth_code)
async def handle_auth_code_for_request(message: Message, state: FSMContext):
    """Handle OAuth code input for auth request."""
    code = message.text.strip()
    
    try:
        state_data = await state.get_data()
        auth_request_id = state_data['auth_request_id']
        
        # Exchange code for credentials
        google_oauth_service = container.google_oauth_service()
        credentials = google_oauth_service.exchange_code_for_token(code)
        
        # Complete the auth request
        sharing_service = container.sharing_service()
        recipient_id = sharing_service.complete_auth_request(
            auth_request_id=auth_request_id,
            target_user_id=message.from_user.id,
            credentials=credentials,
            platform_config='{"calendar_id": "primary"}'
        )
        
        # Get auth request for notification
        repository = container.unified_recipient_repository()
        auth_request = repository.get_auth_request_by_id(auth_request_id)
        
        # Notify requester
        await notify_auth_request_completed(auth_request, message.from_user.first_name)
        
        await message.reply(
            "✅ **Authentication Completed!**\n\n"
            f"The Google Calendar account has been authenticated and added to the requester's recipients.\n\n"
            "Thank you for helping!",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
        # Delete credential message for security
        await message.delete()
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error completing OAuth auth request: {e}")
        await message.reply(f"❌ **Error:** {str(e)}")

@router.message(AuthRequestState.waiting_for_credentials)
async def handle_auth_credentials_input(message: Message, state: FSMContext):
    """Handle credential input for auth request."""
    credentials = message.text.strip()
    
    try:
        state_data = await state.get_data()
        auth_request_id = state_data['auth_request_id']
        
        # Get auth request
        repository = container.unified_recipient_repository()
        auth_request = repository.get_auth_request_by_id(auth_request_id)
        
        if not auth_request:
            await message.reply("❌ Authentication request not found.")
            await state.clear()
            return
        
        # Complete the auth request
        sharing_service = container.sharing_service()
        recipient_id = sharing_service.complete_auth_request(
            auth_request_id=auth_request_id,
            target_user_id=message.from_user.id,
            credentials=credentials
        )
        
        # Notify requester
        await notify_auth_request_completed(auth_request, message.from_user.first_name)
        
        await message.reply(
            "✅ **Authentication Completed!**\n\n"
            f"The {auth_request.platform_type.title()} account has been authenticated and added to the requester's recipients.\n\n"
            "Thank you for helping!",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
        # Delete credential message for security
        await message.delete()
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error completing auth request: {e}")
        await message.reply(f"❌ **Error:** {str(e)}")

async def notify_auth_request_completed(auth_request, authenticator_name: str):
    """Notify requester that authentication is complete."""
    try:
        from core.container import container
        bot = container.bot()
        
        await bot.send_message(
            chat_id=auth_request.requester_user_id,
            text=f"✅ **Authentication Completed!**\n\n"
                 f"**Account:** {auth_request.recipient_name} ({auth_request.platform_type.title()})\n"
                 f"**Authenticated by:** {authenticator_name}\n\n"
                 f"The account has been added to your recipients.\n"
                 f"You can now use it to create tasks!\n\n"
                 f"Check /recipients to see your new account.",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error notifying auth completion: {e}")

@router.message(Command("requests"))
async def handle_requests_command(message: Message):
    """Show pending authentication requests."""
    user_id = message.from_user.id
    
    try:
        sharing_service = container.sharing_service()
        
        # Get requests where user is requester or target
        repository = container.unified_recipient_repository()
        sent_requests = repository.get_auth_requests_by_requester(user_id)
        received_requests = repository.get_pending_auth_requests_for_user(user_id)
        
        if not sent_requests and not received_requests:
            await message.reply(
                "📋 **No Authentication Requests**\n\n"
                "You don't have any pending authentication requests.",
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            return
        
        text = "📋 **Authentication Requests**\n\n"
        keyboard = []
        
        if received_requests:
            text += "**📥 Received Requests:**\n"
            for req in received_requests:
                requester_info = container.user_service().get_user_info(req.requester_user_id)
                requester_name = requester_info.get('first_name', f'User{req.requester_user_id}') if requester_info else f'User{req.requester_user_id}'
                
                text += f"• {req.platform_type.title()} - {req.recipient_name}\n"
                text += f"  From: {requester_name}\n"
                text += f"  Expires: {req.expires_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"🔐 Authenticate: {req.recipient_name[:20]}",
                        callback_data=f"auth_request_{req.id}"
                    )
                ])
        
        if sent_requests:
            text += "\n**📤 Sent Requests:**\n"
            for req in sent_requests:
                if req.status == 'pending':
                    target_info = container.user_service().get_user_info(req.target_user_id)
                    target_name = target_info.get('first_name', f'User{req.target_user_id}') if target_info else f'User{req.target_user_id}'
                    
                    text += f"• {req.platform_type.title()} - {req.recipient_name}\n"
                    text += f"  To: {target_name}\n"
                    text += f"  Status: {req.status.title()}\n"
                    text += f"  Expires: {req.expires_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                    
                    keyboard.append([
                        InlineKeyboardButton(
                            text=f"❌ Cancel: {req.recipient_name[:20]}",
                            callback_data=f"cancel_auth_req_{req.id}"
                        )
                    ])
        
        await message.reply(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error showing requests: {e}")
        await message.reply("❌ Error loading requests.")

def get_platform_credential_instructions(platform_type: str) -> str:
    """Get platform-specific credential instructions."""
    instructions = {
        'todoist': "Enter your Todoist API token:\n\n1. Go to Todoist Settings\n2. Navigate to Integrations\n3. Copy your API token",
        'trello': "Enter your Trello API key and token (separated by colon):\n\nFormat: YOUR_API_KEY:YOUR_TOKEN\n\n1. Get API key from trello.com/app-key\n2. Generate token using the link on that page"
    }
    return instructions.get(platform_type, "Enter your credentials for this platform")

@router.callback_query(lambda c: c.data == "cancel_sharing")
async def handle_cancel_sharing(callback_query: CallbackQuery, state: FSMContext):
    """Cancel sharing workflow."""
    await callback_query.message.edit_text("❌ **Sharing Cancelled**")
    await state.clear()