"""Refactored bot handlers using new architecture."""

import asyncio
import time
import json
from typing import Dict, Tuple, List
from collections import defaultdict

from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, Voice, PhotoSize, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram import Bot

from bot import router
from states.platform_states import TaskPlatformState, DropUserDataState, PartnerManagementState
from core.initialization import services
# Onboarding service via services.get_onboarding_service()
from keyboards.inline import (
    get_transcription_keyboard, 
    get_platform_selection_keyboard,
    get_platform_config_keyboard,
    get_trello_board_selection_keyboard,
    get_trello_list_selection_keyboard,
    get_main_menu_keyboard,
    get_task_action_keyboard,
    get_task_list_keyboard,
    get_quick_task_keyboard
)
from platforms import TaskPlatformFactory
from models.task import TaskCreate
# Config access via services.get_config()
from core.logging import get_logger
from core.exceptions import (
    TranscriptionError, ParsingError, TaskCreationError, 
    ValidationError, PlatformError
)

logger = get_logger(__name__)

# Thread collection variables (thread-safe user-scoped storage)
import threading
_message_threads_lock = threading.Lock()
message_threads = defaultdict(list)
last_message_time = defaultdict(float)

@router.message(Command('drop_user_data'))
async def initiate_drop_user_data(message: Message, state: FSMContext):
    """Initiate user data deletion process."""
    await message.reply("Are you sure you want to drop all your data? Reply with 'yes' to confirm or 'no' to cancel.")
    await state.set_state(DropUserDataState.waiting_for_confirmation)

@router.message(DropUserDataState.waiting_for_confirmation)
async def confirm_drop_user_data(message: Message, state: FSMContext):
    """Handle user data deletion confirmation."""
    user_response = message.text.strip().lower()
    user_id = message.from_user.id

    if user_response == 'yes':
        try:
            services.get_task_service().delete_user_data(user_id)
            await message.reply("All your data has been successfully dropped.")
        except Exception as e:
            logger.error(f"Failed to delete user data for {user_id}: {e}")
            await message.reply("Failed to delete your data. Please try again later.")
        await state.clear()
    elif user_response == 'no':
        await message.reply("Data drop canceled.")
        await state.clear()
    else:
        await message.reply("Invalid response. Please reply with 'yes' to confirm or 'no' to cancel.")

@router.message(Command('tasks'))
async def show_tasks_command(message: Message, state: FSMContext):
    """Show user's tasks via command."""
    await show_user_tasks(message, message.from_user.id)

@router.message(Command('menu'))
async def show_main_menu_command(message: Message, state: FSMContext):
    """Show main menu via command."""
    keyboard = get_main_menu_keyboard()
    await message.reply(
        "🎯 **Main Menu**\n\nChoose what you'd like to do:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

@router.message(Command('settings'))
async def show_user_settings(message: Message, state: FSMContext):
    """Show the current user settings."""
    user_id = message.from_user.id
    
    try:
        user_info = services.get_task_service().get_user_platform_info(user_id)
        
        if not user_info or not user_info.get('platform_token'):
            await message.reply(
                "You haven't set up a task management platform yet. Use /set_platform to get started."
            )
            return
        
        platform_type = user_info.get('platform_type', 'todoist')
        location = user_info.get('location', 'not set')
        telegram_notifications = user_info.get('telegram_notifications', True)
        notification_status = "Enabled" if telegram_notifications else "Disabled"
        
        # For Trello, show board and list settings
        platform_settings_info = ""
        if platform_type == 'trello' and user_info.get('platform_settings'):
            settings = user_info['platform_settings']
            if isinstance(settings, dict):
                board_id = settings.get('board_id', 'not set')
                list_id = settings.get('list_id', 'not set')
                platform_settings_info = f"\nBoard ID: {board_id}\nList ID: {list_id}"
            else:
                platform_settings_info = "\nWarning: Your Trello settings appear to be corrupted. Use /set_platform to reconfigure."
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Configure Platforms", callback_data="configure_platforms")],
            [InlineKeyboardButton(text="🔄 Change Platform", callback_data="change_platform")],
            [InlineKeyboardButton(text="👥 Partner Management", callback_data="partner_management")],
            [InlineKeyboardButton(text=f"🔔 Notifications: {notification_status}", callback_data="toggle_notifications")],
            [InlineKeyboardButton(text="« Main Menu", callback_data="main_menu")]
        ])
        
        await message.reply(
            f"⚙️ **Your Settings:**\n\n"
            f"**Platform:** {platform_type.capitalize()}\n"
            f"**Location:** {location}\n"
            f"**Telegram Notifications:** {notification_status}"
            f"{platform_settings_info}\n\n"
            f"To change these settings, use the buttons below.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Failed to get user settings for {user_id}: {e}")
        await message.reply("Failed to retrieve your settings. Please try again later.")

@router.message(Command('start'))
async def start_command(message: Message, state: FSMContext):
    """Handle /start command with enhanced onboarding."""
    user_id = message.from_user.id
    status = await services.get_onboarding_service().get_onboarding_status(user_id)
    
    if status['is_complete']:
        keyboard = get_main_menu_keyboard()
        await message.reply(
            f"Welcome back! You're all set up with {status['platform_type'].title()}.\n\n"
            "🎯 **What would you like to do?**\n"
            "• View and manage your tasks\n"
            "• Create new tasks quickly\n"
            "• Send any message to create a custom task",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    else:
        await services.get_onboarding_service().send_welcome_message(message)

@router.message(Command('set_platform'))
async def start_platform_selection(message: Message, state: FSMContext):
    """Start the process of selecting a task management platform."""
    await services.get_onboarding_service().send_progress_update(message, message.from_user.id)
    await state.set_state(TaskPlatformState.selecting_platform)

@router.callback_query(lambda c: c.data == "start_setup")
async def handle_start_setup(callback_query: CallbackQuery, state: FSMContext):
    """Handle start setup button from welcome message."""
    await services.get_onboarding_service().send_progress_update(callback_query.message, callback_query.from_user.id)
    await state.set_state(TaskPlatformState.selecting_platform)
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("platform_"))
async def process_platform_selection(callback_query: CallbackQuery, state: FSMContext):
    """Process the selection of a task management platform."""
    platform_type = callback_query.data.split("_")[1]
    await state.update_data(platform_type=platform_type)
    
    help_text = services.get_onboarding_service().get_platform_help_text(platform_type)
    
    if platform_type == "todoist":
        await callback_query.message.edit_text(help_text, parse_mode='Markdown')
        await state.set_state(TaskPlatformState.waiting_for_api_key)
    elif platform_type == "trello":
        await callback_query.message.edit_text(help_text, parse_mode='Markdown')
        await state.set_state(TaskPlatformState.waiting_for_api_key)
    
    await callback_query.answer()

@router.message(TaskPlatformState.waiting_for_api_key)
async def receive_platform_key(message: Message, state: FSMContext):
    """Handle platform API key input."""
    user_id = message.from_user.id
    
    # Basic validation to prevent processing normal messages as API keys
    if not message.text or len(message.text.strip()) < 10:
        await message.reply("❌ Invalid API key format. Please provide a valid API key or use /settings to cancel.")
        return
    
    api_key = message.text.strip()
    owner_name = message.from_user.full_name
    
    # Check for common task-like phrases that shouldn't be treated as API keys
    task_indicators = ["remind me", "schedule", "todo", "task", "buy", "call", "meet", "tomorrow", "today"]
    if any(indicator in api_key.lower() for indicator in task_indicators):
        await message.reply("❌ This looks like a task, not an API key. Please provide your platform API key or use /settings to cancel.")
        return
    
    try:
        # Get the selected platform from state
        state_data = await state.get_data()
        platform_type = state_data.get('platform_type', 'todoist')
        
        # For Trello, validate the API key format and proceed with board selection
        if platform_type == 'trello':
            if ':' not in api_key:
                await message.reply("Invalid format. Please provide your Trello credentials as 'API_KEY:TOKEN'.")
                return
            
            try:
                # Create a Trello platform instance to validate credentials and get boards
                trello = TaskPlatformFactory.get_platform('trello', api_key)
                boards = trello.get_boards()
                
                if not boards:
                    await message.reply("Could not retrieve boards with the provided credentials. Please check and try again.")
                    return
                
                # Store the credentials temporarily
                await state.update_data(api_key=api_key)
                
                # Show board selection keyboard
                keyboard = get_trello_board_selection_keyboard(boards)
                await message.reply("Please select the board where tasks should be created:", reply_markup=keyboard)
                await state.set_state(TaskPlatformState.waiting_for_board_selection)
                return
                
            except Exception as e:
                logger.error(f"Error validating Trello credentials: {e}")
                await message.reply("Error validating Trello credentials. Please check and try again.")
                return
        
        # Check if this is multi-platform configuration
        config_mode = state_data.get('config_mode')
        
        if config_mode == 'multi':
            # Multi-platform configuration: update platform config
            services.get_task_service().update_platform_config(user_id, platform_type, api_key)
            await message.reply(f"✅ {platform_type.capitalize()} has been configured successfully!")
            
            # Return to platform configuration menu
            user_info = services.get_task_service().get_user_platform_info(user_id)
            keyboard = get_platform_config_keyboard(user_info)
            
            await message.reply(
                "⚙️ **Platform Configuration**\n\n"
                "Configure your task management platforms:\n"
                "✅ = Configured\n"
                "❌ = Not configured\n\n"
                "You can configure multiple platforms to create tasks on all of them simultaneously.",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            await state.clear()
        else:
            # Single platform setup (legacy flow)
            services.get_task_service().save_user_platform(user_id, api_key, platform_type, owner_name)
            await message.reply(f"Your {platform_type.capitalize()} account has been linked successfully, {owner_name}!")
            
            # Check if location is needed
            user_info = services.get_task_service().get_user_platform_info(user_id)
            if user_info and not user_info.get('location'):
                location_help = services.get_onboarding_service().get_location_help_text()
                await message.reply(location_help, parse_mode='Markdown')
                await state.set_state(TaskPlatformState.waiting_for_location)
            else:
                await state.clear()
                # Send completion message
                await services.get_onboarding_service().send_progress_update(message, user_id)
            
    except Exception as e:
        logger.error(f"Failed to save platform key for user {user_id}: {e}")
        await message.reply("Failed to save your platform information. Please try again.")

@router.callback_query(lambda c: c.data.startswith("trello_board_"))
async def process_board_selection(callback_query: CallbackQuery, state: FSMContext):
    """Process the selection of a Trello board and show lists for selection."""
    board_id = callback_query.data.replace("trello_board_", "")
    state_data = await state.get_data()
    api_key = state_data.get('api_key')
    
    try:
        # Get lists for the selected board
        trello = TaskPlatformFactory.get_platform('trello', api_key)
        lists = trello.get_lists(board_id)
        
        if not lists:
            await callback_query.message.edit_text("Could not retrieve lists for this board. Please try again.")
            return
        
        # Store the board ID
        await state.update_data(board_id=board_id)
        
        # Show list selection keyboard
        keyboard = get_trello_list_selection_keyboard(lists)
        await callback_query.message.edit_text("Please select the list where tasks should be created:", reply_markup=keyboard)
        await state.set_state(TaskPlatformState.waiting_for_list_selection)
        
    except Exception as e:
        logger.error(f"Error retrieving Trello lists: {e}")
        await callback_query.message.edit_text("Error retrieving lists. Please try again.")
    
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("trello_list_"))
async def process_list_selection(callback_query: CallbackQuery, state: FSMContext):
    """Process the selection of a Trello list and save the user settings."""
    list_id = callback_query.data.replace("trello_list_", "")
    user_id = callback_query.from_user.id
    owner_name = callback_query.from_user.full_name
    
    try:
        state_data = await state.get_data()
        api_key = state_data.get('api_key')
        board_id = state_data.get('board_id')
        
        # Check if this is multi-platform configuration
        config_mode = state_data.get('config_mode')
        
        if config_mode == 'multi':
            # Multi-platform configuration: update platform config with Trello settings
            try:
                key_parts = api_key.split(':')
                if len(key_parts) != 2:
                    await callback_query.message.edit_text("❌ Invalid Trello credentials format. Expected 'key:token'.")
                    return
                
                additional_data = {
                    'key': key_parts[0],
                    'board_id': board_id,
                    'list_id': list_id
                }
                trello_token = key_parts[1]
                services.get_task_service().update_platform_config(user_id, 'trello', trello_token, additional_data)
            except Exception as e:
                logger.error(f"Error processing Trello configuration: {e}")
                await callback_query.message.edit_text("❌ Error processing Trello configuration.")
                return
            
            await callback_query.message.edit_text("✅ Trello has been configured successfully!")
            
            # Return to platform configuration menu
            user_info = services.get_task_service().get_user_platform_info(user_id)
            keyboard = get_platform_config_keyboard(user_info)
            
            await callback_query.message.reply(
                "⚙️ **Platform Configuration**\n\n"
                "Configure your task management platforms:\n"
                "✅ = Configured\n"
                "❌ = Not configured\n\n"
                "You can configure multiple platforms to create tasks on all of them simultaneously.",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            await state.clear()
        else:
            # Single platform setup (legacy flow)
            platform_settings = {
                'board_id': board_id,
                'list_id': list_id
            }
            
            services.get_task_service().save_user_platform(
                user_id, api_key, 'trello', owner_name, 
                platform_settings=platform_settings
            )
            
            await callback_query.message.edit_text(f"Your Trello account has been linked successfully, {owner_name}!")
        
        # Ask for location if not set
        user_info = services.get_task_service().get_user_platform_info(user_id)
        if not user_info.get('location'):
            location_help = services.get_onboarding_service().get_location_help_text()
            await callback_query.message.reply(location_help, parse_mode='Markdown')
            await state.set_state(TaskPlatformState.waiting_for_location)
        else:
            await state.clear()
            # Send completion message
            await services.get_onboarding_service().send_progress_update(callback_query.message, user_id)
        
    except Exception as e:
        logger.error(f"Failed to save Trello configuration for user {user_id}: {e}")
        await callback_query.message.edit_text("Failed to save your Trello configuration. Please try again.")
    
    await callback_query.answer()

@router.message(TaskPlatformState.waiting_for_location)
async def receive_location(message: Message, state: FSMContext):
    """Handle location input."""
    user_id = message.from_user.id
    location = message.text.strip()

    try:
        # Get user's current platform information
        user_info = services.get_task_service().get_user_platform_info(user_id)
        if user_info:
            # Update the user record to include the location
            services.get_task_service().save_user_platform(
                user_id, 
                user_info.get('platform_token'), 
                user_info.get('platform_type', 'todoist'), 
                user_info.get('owner_name'),
                location,
                user_info.get('platform_settings')
            )

            platform_name = user_info.get('platform_type', 'task management platform').capitalize()
            await message.reply(f"Location set to {location}. All tasks in {platform_name} will now consider this time zone.")
            
            # Send completion message
            await services.get_onboarding_service().send_progress_update(message, user_id)
        else:
            await message.reply("Error: Your account information could not be found. Please set up your platform again.")
        
    except Exception as e:
        logger.error(f"Failed to save location for user {user_id}: {e}")
        await message.reply("Failed to save your location. Please try again.")
    
    await state.clear()

async def process_user_input_with_photo(text: str, user_id: int, message_obj: Message, state: FSMContext, bot: Bot) -> bool:
    """Process user input that includes a photo, with threading support."""
    try:
        logger.debug(f"Processing photo message for user {user_id} with caption: '{text}'")
        user_info = services.get_task_service().get_user_platform_info(user_id)
        
        if not user_info or not user_info.get('platform_token'):
            # Prompt user to select a platform first  
            keyboard = get_platform_selection_keyboard()
            await message_obj.reply(
                "Please set up your task management platform first to process screenshots:", 
                reply_markup=keyboard
            )
            await state.set_state(TaskPlatformState.selecting_platform)
            return False

        # Process the image first (handle both inline photos and document attachments)
        processing_msg = await message_obj.answer("📸 Processing image...")
        
        image_service = services.get_image_processing_service()
        if message_obj.photo:
            image_result = await image_service.process_image_message(message_obj.photo, bot)
        elif message_obj.document:
            image_result = await image_service.process_image_message(message_obj.document, bot)
        else:
            await processing_msg.delete()
            await message_obj.reply("❌ No image found in message.")
            return False
        
        await processing_msg.delete()
        
        extracted_text = image_result.get('extracted_text', '')
        summary = image_result.get('summary', '')
        
        if not extracted_text.strip() and not text.strip():
            await message_obj.reply(
                "📸 **Screenshot Processed**\n\n"
                f"🔍 **Analysis:** {summary}\n\n"
                "⚠️ No text was found in the image and no caption provided to create a task from."
            )
            return False

        # Determine the correct user name
        if message_obj.forward_from:
            user_full_name = message_obj.forward_from.full_name
        elif message_obj.forward_sender_name:
            user_full_name = message_obj.forward_sender_name
        else:
            user_full_name = message_obj.from_user.full_name

        # Convert screenshot data to enriched text content
        enriched_content = ""
        if text.strip():
            enriched_content += f"[CAPTION] {text.strip()}\n\n"
            logger.debug(f"Added caption to enriched_content: '{text.strip()}'")
        if extracted_text.strip():
            enriched_content += f"[SCREENSHOT TEXT]\n{extracted_text}\n\n"
        if summary.strip():
            enriched_content += f"[SCREENSHOT DESCRIPTION] {summary}"
        
        logger.debug(f"Final enriched_content: '{enriched_content[:200]}...'")
        
        # Store screenshot data separately for attachment
        screenshot_data = image_result
        
        # Add to threading system with enhanced content
        current_time = time.time()
        with _message_threads_lock:
            # Store both enriched text and screenshot data
            message_threads[user_id].append((user_full_name, enriched_content, screenshot_data))
            last_message_time[user_id] = current_time
        
        # Check if enough time has passed since the last message
        if len(message_threads[user_id]) > 0:
            await asyncio.sleep(services.get_config().THREAD_TIMEOUT)
            if current_time == last_message_time[user_id]:  # No new messages received
                # Process the complete thread
                with _message_threads_lock:
                    thread_content = message_threads[user_id].copy()
                    message_threads[user_id].clear()  # Clear the thread
                
                owner_name = user_info.get('owner_name')
                location = user_info.get('location')
                
                await process_thread(message_obj, thread_content, owner_name, location, user_id)
                return True
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing user input with photo: {e}")
        await message_obj.reply("An error occurred while processing your screenshot. Please try again.")
        return False

async def process_user_input(text: str, user_id: int, message_obj: Message, state: FSMContext) -> bool:
    """Process user text input and create tasks."""
    try:
        logger.debug(f"Processing text message for user {user_id}: '{text}'")
        user_info = services.get_task_service().get_user_platform_info(user_id)
        
        if not user_info or not user_info.get('platform_token'):
            # Prompt user to select a platform
            keyboard = get_platform_selection_keyboard()
            await message_obj.reply(
                "Please select your task management platform to link your account:", 
                reply_markup=keyboard
            )
            await state.set_state(TaskPlatformState.selecting_platform)
            return False

        # Determine the correct user name
        if message_obj.forward_from:
            user_full_name = message_obj.forward_from.full_name
        elif message_obj.forward_sender_name:
            user_full_name = message_obj.forward_sender_name
        else:
            user_full_name = message_obj.from_user.full_name

        current_time = time.time()
        with _message_threads_lock:
            message_threads[user_id].append((user_full_name, text))
            last_message_time[user_id] = current_time
        
        # Check if enough time has passed since the last message
        if len(message_threads[user_id]) > 0:
            await asyncio.sleep(services.get_config().THREAD_TIMEOUT)
            if current_time == last_message_time[user_id]:  # No new messages received
                # Process the complete thread
                with _message_threads_lock:
                    thread_content = message_threads[user_id].copy()
                    message_threads[user_id].clear()  # Clear the thread
                
                owner_name = user_info.get('owner_name')
                location = user_info.get('location')
                
                await process_thread(message_obj, thread_content, owner_name, location, user_id)
                return True
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing user input: {e}")
        await message_obj.reply("An error occurred while processing your message. Please try again.")
        return False


async def process_thread(message: Message, thread_content: List[Tuple], 
                        owner_name: str, location: str, owner_id: int):
    """Process a thread of messages (text/voice/photo) and create a task."""
    try:
        # Separate text content and find any screenshot data
        text_content = []
        screenshot_data = None
        
        for item in thread_content:
            if len(item) == 3:  # (user, content, image_result)
                user, content, image_result = item
                text_content.append((user, content))
                if not screenshot_data:  # Use first screenshot found
                    screenshot_data = image_result
            else:  # (user, content) - text or voice
                text_content.append(item)
        
        # Concatenate thread content
        concatenated_content = "\n".join([f"{sender}: {text}" for sender, text in text_content])
        
        logger.debug(f"Processing thread with {len(thread_content)} items, screenshot_data: {bool(screenshot_data)}")
        logger.debug(f"Concatenated content: '{concatenated_content}'")
        
        # Parse content into task
        parsed_task_dict = services.get_parsing_service().parse_content_to_task(
            concatenated_content,
            owner_name=owner_name,
            location=location
        )
        
        if not parsed_task_dict:
            await message.reply("Failed to parse your message into a task. Please try again.")
            return
        
        # Create task model
        task_data = TaskCreate(**parsed_task_dict)
        
        # Check if user has partner sharing enabled
        if await should_show_partner_selection(owner_id, task_data, message, screenshot_data):
            return  # Partner selection UI shown, will continue via callback
        
        # Create task with screenshot if available (default partners or legacy)
        success, task_url = await services.get_task_service().create_task(
            user_id=owner_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            task_data=task_data,
            screenshot_data=screenshot_data
        )
        
        if success:
            # Check if user wants Telegram notifications
            user_info = services.get_task_service().get_user_platform_info(owner_id)
            telegram_notifications = user_info.get('telegram_notifications', True) if user_info else True
            logger.info(f"User {owner_id} notification preference: {telegram_notifications}")
            
            if telegram_notifications:
                # Get configured platforms to show in message
                configured_platforms = services.get_task_service().get_configured_platforms(owner_id)
                
                # Format due time for display in local time
                try:
                    location = user_info.get('location') if user_info else None
                    due_str = services.get_parsing_service().convert_utc_to_local_display(task_data.due_time, location)
                    
                    # Build success message
                    if len(configured_platforms) > 1:
                        platform_names = ", ".join([p.capitalize() for p in configured_platforms])
                        success_message = f"✅ **Task Created on {platform_names}**\n\n📌 **{task_data.title}**\n⏰ Due: {due_str}"
                    else:
                        platform_name = configured_platforms[0].capitalize() if configured_platforms else "Platform"
                        success_message = f"✅ **Task Created in {platform_name}**\n\n📌 **{task_data.title}**\n⏰ Due: {due_str}"
                    
                    # Add task URLs (might be multiple)
                    if task_url:
                        if '\n' in task_url:  # Multiple URLs
                            success_message += "\n\n**Task Links:**\n" + task_url
                        else:  # Single URL
                            success_message += f"\n🔗 [Open Task]({task_url})"
                    
                    await message.reply(success_message, parse_mode='Markdown', disable_web_page_preview=True)
                except Exception as e:
                    logger.error(f"Error formatting success message: {e}")
                    success_message = f"✅ Task created: {task_data.title}"
                    if task_url:
                        success_message += f"\n\n{task_url}"
                    await message.reply(success_message, disable_web_page_preview=True)
            else:
                # Silent mode - just log the task creation
                logger.info(f"Task created silently for user {owner_id}: {task_data.title}")
        else:
            # Get configured platforms for better error message
            configured_platforms = services.get_task_service().get_configured_platforms(owner_id)
            platform_names = ", ".join([p.capitalize() for p in configured_platforms]) if configured_platforms else "configured platforms"
            await message.reply(f"❌ Failed to create task on {platform_names}. Please check your API credentials in settings.")
            
    except ParsingError as e:
        logger.error(f"Parsing error: {e}")
        await message.reply("Failed to understand your message. Please try rephrasing.")
    except TaskCreationError as e:
        logger.error(f"Task creation error: {e}")
        await message.reply(f"Failed to create task: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in process_thread: {e}")
        await message.reply("An unexpected error occurred. Please try again.")

@router.message()
async def handle_message(message: Message, state: FSMContext, bot: Bot):
    """Main message handler."""
    try:
        logger.debug(f"Processing message from user {message.from_user.id}: photo={bool(message.photo)}, document={bool(message.document)}, text={bool(message.text)}")
        
        if message.voice:
            await handle_voice_message(message, state, bot)
            return
            
        if message.text and message.text.startswith('/'):
            return
            
        if message.reply_to_message and message.reply_to_message.from_user.is_bot:
            return

        # Handle images (both inline photos and document attachments)
        if message.photo:
            caption_text = message.caption or ""
            logger.debug(f"Processing inline photo with caption: '{caption_text}'")
            await process_user_input_with_photo(
                caption_text, 
                message.from_user.id, 
                message, 
                state,
                bot
            )
        elif message.document and message.document.mime_type and message.document.mime_type.startswith('image/'):
            caption_text = message.caption or ""
            logger.debug(f"Processing document image attachment with caption: '{caption_text}'")
            await process_user_input_with_photo(
                caption_text, 
                message.from_user.id, 
                message, 
                state,
                bot
            )
        elif message.text:
            await process_user_input(
                message.text, 
                message.from_user.id, 
                message, 
                state
            )
        else:
            logger.warning(f"Unhandled message type from user {message.from_user.id}: document={message.document}, mime_type={message.document.mime_type if message.document else None}")
        
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await message.reply("An error occurred while processing your message.")

async def handle_voice_message(message: Message, state: FSMContext, bot: Bot):
    """Handle voice message processing."""
    try:
        # Show processing message
        processing_msg = await message.answer("Processing voice message...")
        
        # Process voice message
        voice_service = services.get_voice_processing_service()
        voice_text = await voice_service.process_voice_message(message.voice, bot)
        await state.update_data(transcribed_text=voice_text)
        
        # Delete processing message
        await processing_msg.delete()
        
        # Determine the correct user name
        if message.forward_from:
            user_full_name = message.forward_from.full_name
        elif message.forward_sender_name:
            user_full_name = message.forward_sender_name
        else:
            user_full_name = message.from_user.full_name

        keyboard = get_transcription_keyboard()
        await message.answer(
            f"I transcribed your voice message as:\n\n{voice_text}\n\nIs this correct?",
            reply_markup=keyboard
        )
        
        # Store the user name for later use
        await state.update_data(user_full_name=user_full_name)
        
    except TranscriptionError as e:
        logger.error(f"Transcription error: {e}")
        await message.reply("Failed to transcribe your voice message. Please try again or send a text message.")
    except Exception as e:
        logger.error(f"Error handling voice message: {e}")
        await message.reply("An error occurred while processing your voice message.")


@router.callback_query(lambda c: c.data == "transcribe_confirm")
async def confirm_transcription(callback_query: CallbackQuery, state: FSMContext):
    """Handle transcription confirmation."""
    try:
        data = await state.get_data()
        user_id = callback_query.from_user.id
        
        user_info = services.get_task_service().get_user_platform_info(user_id)
        
        if not user_info or not user_info.get('platform_token'):
            # Prompt user to select a platform
            keyboard = get_platform_selection_keyboard()
            await callback_query.message.reply(
                "Please select your task management platform to link your account:", 
                reply_markup=keyboard
            )
            await state.set_state(TaskPlatformState.selecting_platform)
            return

        # Use the stored user name
        user_full_name = data.get('user_full_name', callback_query.from_user.full_name)
        thread_content = [(user_full_name, data['transcribed_text'])]
        
        owner_name = user_info.get('owner_name')
        location = user_info.get('location')
        
        # Process voice message into task with better feedback
        try:
            # Concatenate thread content
            concatenated_content = "\n".join([f"{sender}: {text}" for sender, text in thread_content])
            
            # Parse content into task
            parsed_task_dict = services.get_parsing_service().parse_content_to_task(
                concatenated_content,
                owner_name=owner_name,
                location=location
            )
            
            if parsed_task_dict:
                from models.task import TaskCreate
                task_data = TaskCreate(**parsed_task_dict)
                
                # Create task
                success, task_url = await services.get_task_service().create_task(
                    user_id=user_id,
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    task_data=task_data
                )
                
                if success:
                    # Format due time for display in local time
                    try:
                        location = user_info.get('location')
                        due_str = services.get_parsing_service().convert_utc_to_local_display(task_data.due_time, location)
                        platform_name = user_info.get('platform_type', 'platform').capitalize()
                        
                        # Build success message with optional task URL
                        success_message = (
                            f"✅ **Voice Task Created in {platform_name}**\n\n"
                            f"📌 **{task_data.title}**\n"
                            f"⏰ **Due:** {due_str}\n\n"
                            f"📝 **Description:** {task_data.description}"
                        )
                        if task_url:
                            success_message += f"\n\n🔗 [Open Task]({task_url})"
                        
                        await callback_query.message.edit_text(success_message, parse_mode='Markdown')
                    except:
                        platform_name = user_info.get('platform_type', 'platform').capitalize()
                        success_message = f"Task created in {platform_name}!"
                        if task_url:
                            success_message += f" {task_url}"
                        await callback_query.answer(success_message)
                else:
                    await callback_query.message.edit_text("Task saved locally but failed to create on your platform.")
            else:
                await callback_query.message.edit_text("Failed to parse your voice message into a task.")
                
        except Exception as parse_error:
            logger.error(f"Error processing voice task: {parse_error}")
            await callback_query.message.edit_text("An error occurred while creating your task.")
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error confirming transcription: {e}")
        await callback_query.answer("An error occurred while creating your task.")

@router.callback_query(lambda c: c.data == "transcribe_cancel")
async def cancel_transcription(callback_query: CallbackQuery, state: FSMContext):
    """Handle transcription cancellation."""
    await callback_query.message.edit_text("Transcription cancelled. Please try sending your voice message again.")
    await callback_query.answer()
    await state.clear()

# Enhanced UI Callback Handlers

@router.callback_query(lambda c: c.data == "main_menu")
async def show_main_menu(callback_query: CallbackQuery, state: FSMContext):
    """Show main menu."""
    # Clear any existing state to prevent contamination
    await state.clear()
    
    keyboard = get_main_menu_keyboard()
    await callback_query.message.edit_text(
        "🎯 **Main Menu**\n\nChoose what you'd like to do:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "change_platform")
async def change_platform(callback_query: CallbackQuery, state: FSMContext):
    """Handle change platform request."""
    keyboard = get_platform_selection_keyboard()
    await callback_query.message.edit_text(
        "🔄 **Change Platform**\n\nSelect your new task management platform:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await state.set_state(TaskPlatformState.selecting_platform)
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "configure_platforms")
async def configure_platforms(callback_query: CallbackQuery, state: FSMContext):
    """Handle platform configuration request."""
    # Clear any existing state to prevent contamination
    await state.clear()
    
    user_id = callback_query.from_user.id
    user_info = services.get_task_service().get_user_platform_info(user_id)
    
    keyboard = get_platform_config_keyboard(user_info)
    
    await callback_query.message.edit_text(
        "⚙️ **Platform Configuration**\n\n"
        "Configure your task management platforms:\n"
        "✅ = Configured\n"
        "❌ = Not configured\n\n"
        "You can configure multiple platforms to create tasks on all of them simultaneously.",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("config_"))
async def handle_platform_config(callback_query: CallbackQuery, state: FSMContext):
    """Handle individual platform configuration."""
    platform_type = callback_query.data.replace("config_", "")
    
    help_text = services.get_onboarding_service().get_platform_help_text(platform_type)
    
    await callback_query.message.edit_text(
        help_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Back to Platform Config", callback_data="configure_platforms")]
        ]),
        parse_mode='Markdown'
    )
    
    await state.set_state(TaskPlatformState.waiting_for_api_key)
    await state.update_data(platform_type=platform_type, config_mode="multi")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "toggle_notifications")
async def toggle_notifications(callback_query: CallbackQuery, state: FSMContext):
    """Toggle Telegram notification preference."""
    user_id = callback_query.from_user.id
    
    try:
        user_info = services.get_task_service().get_user_platform_info(user_id)
        current_setting = user_info.get('telegram_notifications', True) if user_info else True
        new_setting = not current_setting
        
        success = services.get_task_service().update_notification_preference(user_id, new_setting)
        
        if success:
            status = "enabled" if new_setting else "disabled"
            await callback_query.answer(f"Telegram notifications {status}!")
            
            # Refresh settings display
            user_info = services.get_task_service().get_user_platform_info(user_id)
            if user_info:
                platform_type = user_info.get('platform_type', 'todoist')
                location = user_info.get('location', 'not set')
                notification_status = "Enabled" if new_setting else "Disabled"
                
                platform_settings_info = ""
                if platform_type == 'trello' and user_info.get('platform_settings'):
                    settings = user_info['platform_settings']
                    if isinstance(settings, dict):
                        board_id = settings.get('board_id', 'not set')
                        list_id = settings.get('list_id', 'not set')
                        platform_settings_info = f"\nBoard ID: {board_id}\nList ID: {list_id}"
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⚙️ Configure Platforms", callback_data="configure_platforms")],
                    [InlineKeyboardButton(text="🔄 Change Platform", callback_data="change_platform")],
                    [InlineKeyboardButton(text=f"🔔 Notifications: {notification_status}", callback_data="toggle_notifications")],
                    [InlineKeyboardButton(text="« Main Menu", callback_data="main_menu")]
                ])
                
                await callback_query.message.edit_text(
                    f"⚙️ **Your Settings:**\n\n"
                    f"**Platform:** {platform_type.capitalize()}\n"
                    f"**Location:** {location}\n"
                    f"**Telegram Notifications:** {notification_status}"
                    f"{platform_settings_info}\n\n"
                    f"To change these settings, use the buttons below.",
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
        else:
            await callback_query.answer("Failed to update notification setting.")
            
    except Exception as e:
        logger.error(f"Error toggling notifications for user {user_id}: {e}")
        await callback_query.answer("Error updating notification setting.")

@router.callback_query(lambda c: c.data == "show_settings")
async def show_settings_callback(callback_query: CallbackQuery, state: FSMContext):
    """Show user settings via callback."""
    # Clear any existing state to prevent contamination
    await state.clear()
    
    user_id = callback_query.from_user.id
    
    try:
        user_info = services.get_task_service().get_user_platform_info(user_id)
        
        if not user_info or not user_info.get('platform_token'):
            await callback_query.message.edit_text(
                "You haven't set up a task management platform yet. Use the button below to get started.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🛠️ Set Up Platform", callback_data="start_setup")],
                    [InlineKeyboardButton(text="« Main Menu", callback_data="main_menu")]
                ]),
                parse_mode='Markdown'
            )
            await callback_query.answer()
            return
        
        platform_type = user_info.get('platform_type', 'todoist')
        location = user_info.get('location', 'not set')
        telegram_notifications = user_info.get('telegram_notifications', True)
        notification_status = "Enabled" if telegram_notifications else "Disabled"
        
        # For Trello, show board and list settings
        platform_settings_info = ""
        if platform_type == 'trello' and user_info.get('platform_settings'):
            settings = user_info['platform_settings']
            if isinstance(settings, dict):
                board_id = settings.get('board_id', 'not set')
                list_id = settings.get('list_id', 'not set')
                platform_settings_info = f"\nBoard ID: {board_id}\nList ID: {list_id}"
            else:
                platform_settings_info = "\nWarning: Your Trello settings appear to be corrupted. Use 'Change Platform' to reconfigure."
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Configure Platforms", callback_data="configure_platforms")],
            [InlineKeyboardButton(text="🔄 Change Platform", callback_data="change_platform")],
            [InlineKeyboardButton(text="👥 Partner Management", callback_data="partner_management")],
            [InlineKeyboardButton(text=f"🔔 Notifications: {notification_status}", callback_data="toggle_notifications")],
            [InlineKeyboardButton(text="« Main Menu", callback_data="main_menu")]
        ])
        
        await callback_query.message.edit_text(
            f"⚙️ **Your Settings:**\n\n"
            f"**Platform:** {platform_type.capitalize()}\n"
            f"**Location:** {location}\n"
            f"**Telegram Notifications:** {notification_status}"
            f"{platform_settings_info}\n\n"
            f"To change these settings, use the buttons below.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Failed to get user settings for {user_id}: {e}")
        await callback_query.message.edit_text(
            "Failed to retrieve your settings. Please try again later.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Main Menu", callback_data="main_menu")]
            ])
        )
    
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "show_tasks")
async def show_tasks_callback(callback_query: CallbackQuery, state: FSMContext):
    """Show user's tasks."""
    await show_user_tasks(callback_query.message, callback_query.from_user.id, edit=True)
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "quick_task")
async def show_quick_tasks(callback_query: CallbackQuery, state: FSMContext):
    """Show quick task options."""
    keyboard = get_quick_task_keyboard()
    await callback_query.message.edit_text(
        "⚡ **Quick Tasks**\n\nSelect a preset task or create a custom one:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "show_help")
async def show_help(callback_query: CallbackQuery, state: FSMContext):
    """Show help information."""
    help_text = """
❓ **Help & Commands**

**Creating Tasks:**
• Send any message to create a custom task
• Use voice messages for hands-free task creation
• Try: "Remind me to call John tomorrow at 3 PM"

**Commands:**
• `/start` - Main menu and welcome
• `/tasks` - View your pending tasks
• `/settings` - View and change settings
• `/menu` - Show main menu anytime

**Quick Tasks:**
Use the Quick Task button for common task types with preset timing.

**Voice Messages:**
Send a voice message and I'll transcribe it, then create a task from the content.

**Screenshots:**
Send a screenshot and I'll extract text from it and analyze the content to create a task.

**Natural Language:**
I understand phrases like:
• "tomorrow at 5 PM"
• "next Monday"
• "in 2 hours"
• "every day at 9 AM"
    """
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Main Menu", callback_data="main_menu")]
    ])
    
    await callback_query.message.edit_text(help_text, reply_markup=keyboard, parse_mode='Markdown')
    await callback_query.answer()

async def show_user_tasks(message_obj, user_id: int, edit: bool = False, page: int = 0):
    """Show user's tasks with pagination."""
    try:
        task_service = services.get_task_service()
        tasks = task_service.task_repo.get_by_user(user_id)
        
        if not tasks:
            text = "📭 **No Tasks Found**\n\nYou don't have any pending tasks. Create one by sending me a message!"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Create Task", callback_data="quick_task")],
                [InlineKeyboardButton(text="« Main Menu", callback_data="main_menu")]
            ])
        else:
            text = f"📋 **Your Tasks** ({len(tasks)} pending)\n\nSelect a task to manage:"
            keyboard = get_task_list_keyboard(tasks, page, user_id=user_id)
        
        if edit:
            await message_obj.edit_text(text, reply_markup=keyboard, parse_mode='Markdown')
        else:
            await message_obj.reply(text, reply_markup=keyboard, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Failed to show tasks for user {user_id}: {e}")
        error_text = "❌ Failed to load tasks. Please try again later."
        if edit:
            await message_obj.edit_text(error_text)
        else:
            await message_obj.reply(error_text)

@router.callback_query(lambda c: c.data.startswith("task_view_"))
async def view_task(callback_query: CallbackQuery, state: FSMContext):
    """View a specific task with actions."""
    task_id = callback_query.data.replace("task_view_", "")
    
    try:
        task_service = services.get_task_service()
        tasks = task_service.task_repo.get_by_user(callback_query.from_user.id)
        task = next((t for t in tasks if str(t.id) == task_id), None)
        
        if not task:
            await callback_query.answer("Task not found", show_alert=True)
            return
        
        # Format due time in local time
        try:
            user_info = task_service.get_user_platform_info(callback_query.from_user.id)
            location = user_info.get('location') if user_info else None
            due_str = services.get_parsing_service().convert_utc_to_local_display(task.due_time, location)
        except:
            due_str = task.due_time
        
        text = f"""
📌 **Task Details**

**{task.task_title}**

{task.task_description}

⏰ **Due:** {due_str}
🔗 **Platform:** {task.platform_type.title()}
        """
        
        keyboard = get_task_action_keyboard(task_id)
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Failed to view task {task_id}: {e}")
        await callback_query.answer("Failed to load task details", show_alert=True)
    
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("task_delete_"))
async def delete_task(callback_query: CallbackQuery, state: FSMContext):
    """Delete a task."""
    task_id = callback_query.data.replace("task_delete_", "")
    
    try:
        task_service = services.get_task_service()
        success = task_service.task_repo.delete(int(task_id))
        
        if success:
            await callback_query.answer("✅ Task deleted successfully", show_alert=True)
            await show_user_tasks(callback_query.message, callback_query.from_user.id, edit=True)
        else:
            await callback_query.answer("❌ Failed to delete task", show_alert=True)
            
    except Exception as e:
        logger.error(f"Failed to delete task {task_id}: {e}")
        await callback_query.answer("❌ Failed to delete task", show_alert=True)

@router.callback_query(lambda c: c.data.startswith("tasks_page_"))
async def change_tasks_page(callback_query: CallbackQuery, state: FSMContext):
    """Change tasks page."""
    page = int(callback_query.data.replace("tasks_page_", ""))
    await show_user_tasks(callback_query.message, callback_query.from_user.id, edit=True, page=page)
    await callback_query.answer()

# Partner Selection for Task Creation

async def should_show_partner_selection(user_id: int, task_data, message: Message, screenshot_data: dict = None) -> bool:
    """Check if we should show partner selection UI for task creation."""
    try:
        # Check if partner services are available
        container = services.get_container()
        if not hasattr(container, 'partner_service'):
            return False  # Partner system not available, use legacy
        
        from core.container import get_partner_service, get_user_preferences_service
        partner_service = get_partner_service()
        prefs_service = get_user_preferences_service()
        
        # Check if user has sharing UI enabled
        prefs = prefs_service.get_preferences_or_default(user_id)
        if not prefs.show_sharing_ui:
            return False  # User disabled sharing UI
        
        # Get user's partners
        partners = partner_service.get_enabled_partners(user_id)
        if len(partners) <= 1:
            return False  # User has only one partner (or none), no need to select
        
        # Show partner selection UI
        await show_partner_selection_for_task(user_id, task_data, message, partners, screenshot_data)
        return True
        
    except Exception as e:
        logger.error(f"Error checking partner selection for user {user_id}: {e}")
        return False  # Fall back to default behavior

async def show_partner_selection_for_task(user_id: int, task_data, message: Message, partners: list, screenshot_data: dict = None):
    """Show partner selection UI for task creation."""
    try:
        # Store task data in memory with a unique key
        task_key = f"task_creation_{user_id}_{int(time.time())}"
        
        # Store in a simple global dict (you could use Redis or other storage)
        if not hasattr(show_partner_selection_for_task, 'pending_tasks'):
            show_partner_selection_for_task.pending_tasks = {}
        
        show_partner_selection_for_task.pending_tasks[task_key] = {
            'task_data': task_data,
            'message': message,
            'screenshot_data': screenshot_data,
            'user_id': user_id,
            'timestamp': time.time()
        }
        
        # Build partner selection keyboard
        keyboard = get_task_sharing_keyboard(partners, task_key)
        
        # Format task preview
        due_str = "not set"
        if task_data.due_time:
            try:
                from dateutil import parser as date_parser
                due_time = date_parser.isoparse(task_data.due_time)
                user_info = services.get_task_service().get_user_platform_info(user_id)
                location = user_info.get('location') if user_info else None
                due_str = services.get_parsing_service().convert_utc_to_local_display(task_data.due_time, location)
            except:
                due_str = "not set"
        
        preview_text = (
            f"📋 **Task Preview**\n\n"
            f"📌 **{task_data.title}**\n"
            f"⏰ **Due:** {due_str}\n"
        )
        
        if task_data.description and task_data.description != task_data.title:
            preview_text += f"📝 **Description:** {task_data.description}\n"
        
        preview_text += f"\n👥 **Select partners to share this task with:**"
        
        await message.reply(
            preview_text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing partner selection for user {user_id}: {e}")
        # Fall back to default task creation
        success, task_url = await services.get_task_service().create_task(
            user_id=user_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            task_data=task_data,
            screenshot_data=screenshot_data
        )

def get_task_sharing_keyboard(partners: list, task_key: str):
    """Generate keyboard for task sharing partner selection."""
    buttons = []
    
    # Add partner selection buttons (up to 2 per row)
    for i in range(0, len(partners), 2):
        row = []
        for j in range(i, min(i + 2, len(partners))):
            partner = partners[j]
            button_text = f"✅ {partner.name} ({partner.platform.title()})"
            row.append(InlineKeyboardButton(
                text=button_text,
                callback_data=f"select_partner_{task_key}_{partner.id}"
            ))
        buttons.append(row)
    
    # Add action buttons
    buttons.extend([
        [
            InlineKeyboardButton(text="🎯 Select All", callback_data=f"select_all_{task_key}"),
            InlineKeyboardButton(text="👤 Self Only", callback_data=f"select_self_{task_key}")
        ],
        [
            InlineKeyboardButton(text="✅ Create Task", callback_data=f"create_task_{task_key}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel_task_{task_key}")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Task Sharing Callback Handlers

@router.callback_query(lambda c: c.data.startswith("select_partner_"))
async def toggle_partner_selection(callback_query: CallbackQuery, state: FSMContext):
    """Toggle partner selection for task creation."""
    try:
        # Parse callback data
        parts = callback_query.data.split("_", 3)
        if len(parts) < 4:
            await callback_query.answer("Invalid selection.")
            return
        
        task_key = parts[2] + "_" + parts[3].split("_")[0] + "_" + parts[3].split("_")[1] 
        partner_id = "_".join(parts[3].split("_")[2:])
        
        # Get pending task
        if not hasattr(show_partner_selection_for_task, 'pending_tasks'):
            await callback_query.answer("Task expired. Please try again.")
            return
        
        pending_task = show_partner_selection_for_task.pending_tasks.get(task_key)
        if not pending_task:
            await callback_query.answer("Task expired. Please try again.")
            return
        
        # Initialize selected partners if not exists
        if 'selected_partners' not in pending_task:
            pending_task['selected_partners'] = set()
        
        # Toggle partner selection
        if partner_id in pending_task['selected_partners']:
            pending_task['selected_partners'].remove(partner_id)
        else:
            pending_task['selected_partners'].add(partner_id)
        
        # Update keyboard to reflect selection
        from core.container import get_partner_service
        partner_service = get_partner_service()
        user_id = pending_task['user_id']
        partners = partner_service.get_enabled_partners(user_id)
        
        # Update button states
        keyboard = get_updated_task_sharing_keyboard(partners, task_key, pending_task['selected_partners'])
        
        await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        await callback_query.answer(f"Partner selection updated.")
        
    except Exception as e:
        logger.error(f"Error toggling partner selection: {e}")
        await callback_query.answer("Error updating selection.")

def get_updated_task_sharing_keyboard(partners: list, task_key: str, selected_partners: set):
    """Generate updated keyboard showing current partner selections."""
    buttons = []
    
    # Add partner selection buttons with current state
    for i in range(0, len(partners), 2):
        row = []
        for j in range(i, min(i + 2, len(partners))):
            partner = partners[j]
            is_selected = partner.id in selected_partners
            status = "✅" if is_selected else "❌"
            button_text = f"{status} {partner.name} ({partner.platform.title()})"
            row.append(InlineKeyboardButton(
                text=button_text,
                callback_data=f"select_partner_{task_key}_{partner.id}"
            ))
        buttons.append(row)
    
    # Add action buttons
    buttons.extend([
        [
            InlineKeyboardButton(text="🎯 Select All", callback_data=f"select_all_{task_key}"),
            InlineKeyboardButton(text="👤 Self Only", callback_data=f"select_self_{task_key}")
        ],
        [
            InlineKeyboardButton(text="✅ Create Task", callback_data=f"create_task_{task_key}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel_task_{task_key}")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.callback_query(lambda c: c.data.startswith("select_all_"))
async def select_all_partners(callback_query: CallbackQuery, state: FSMContext):
    """Select all partners for task creation."""
    try:
        task_key = callback_query.data.replace("select_all_", "")
        
        # Get pending task
        if not hasattr(show_partner_selection_for_task, 'pending_tasks'):
            await callback_query.answer("Task expired. Please try again.")
            return
        
        pending_task = show_partner_selection_for_task.pending_tasks.get(task_key)
        if not pending_task:
            await callback_query.answer("Task expired. Please try again.")
            return
        
        # Select all partners
        from core.container import get_partner_service
        partner_service = get_partner_service()
        user_id = pending_task['user_id']
        partners = partner_service.get_enabled_partners(user_id)
        
        pending_task['selected_partners'] = {partner.id for partner in partners}
        
        # Update keyboard
        keyboard = get_updated_task_sharing_keyboard(partners, task_key, pending_task['selected_partners'])
        await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        await callback_query.answer("All partners selected.")
        
    except Exception as e:
        logger.error(f"Error selecting all partners: {e}")
        await callback_query.answer("Error selecting all partners.")

@router.callback_query(lambda c: c.data.startswith("select_self_"))
async def select_self_only(callback_query: CallbackQuery, state: FSMContext):
    """Select only self partner for task creation."""
    try:
        task_key = callback_query.data.replace("select_self_", "")
        
        # Get pending task
        if not hasattr(show_partner_selection_for_task, 'pending_tasks'):
            await callback_query.answer("Task expired. Please try again.")
            return
        
        pending_task = show_partner_selection_for_task.pending_tasks.get(task_key)
        if not pending_task:
            await callback_query.answer("Task expired. Please try again.")
            return
        
        # Select only self partner
        from core.container import get_partner_service
        partner_service = get_partner_service()
        user_id = pending_task['user_id']
        partners = partner_service.get_enabled_partners(user_id)
        
        # Find self partner
        self_partner = None
        for partner in partners:
            if partner.is_self:
                self_partner = partner
                break
        
        if self_partner:
            pending_task['selected_partners'] = {self_partner.id}
        else:
            pending_task['selected_partners'] = set()
        
        # Update keyboard
        keyboard = get_updated_task_sharing_keyboard(partners, task_key, pending_task['selected_partners'])
        await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        await callback_query.answer("Self-only selected.")
        
    except Exception as e:
        logger.error(f"Error selecting self only: {e}")
        await callback_query.answer("Error selecting self only.")

@router.callback_query(lambda c: c.data.startswith("create_task_"))
async def create_task_with_selected_partners(callback_query: CallbackQuery, state: FSMContext):
    """Create task with selected partners."""
    try:
        task_key = callback_query.data.replace("create_task_", "")
        
        # Get pending task
        if not hasattr(show_partner_selection_for_task, 'pending_tasks'):
            await callback_query.answer("Task expired. Please try again.")
            return
        
        pending_task = show_partner_selection_for_task.pending_tasks.get(task_key)
        if not pending_task:
            await callback_query.answer("Task expired. Please try again.")
            return
        
        # Get selected partners (default to all if none selected)
        selected_partner_ids = pending_task.get('selected_partners', set())
        if not selected_partner_ids:
            from core.container import get_partner_service
            partner_service = get_partner_service()
            partners = partner_service.get_enabled_partners(pending_task['user_id'])
            selected_partner_ids = {partner.id for partner in partners}
        
        # Update message to show creation in progress
        await callback_query.message.edit_text(
            "⏳ Creating task with selected partners...",
            parse_mode='Markdown'
        )
        
        # TODO: Create task with specific partners
        # For now, use default behavior
        task_data = pending_task['task_data']
        message = pending_task['message']
        screenshot_data = pending_task['screenshot_data']
        user_id = pending_task['user_id']
        
        success, task_url = await services.get_task_service().create_task(
            user_id=user_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            task_data=task_data,
            screenshot_data=screenshot_data
        )
        
        if success:
            # Send success message
            await send_task_creation_success(user_id, task_data, task_url, callback_query.message, selected_partner_ids)
        else:
            await callback_query.message.edit_text(
                "❌ Failed to create task. Please try again.",
                parse_mode='Markdown'
            )
        
        # Clean up pending task
        del show_partner_selection_for_task.pending_tasks[task_key]
        
    except Exception as e:
        logger.error(f"Error creating task with selected partners: {e}")
        await callback_query.answer("Error creating task.")

@router.callback_query(lambda c: c.data.startswith("cancel_task_"))
async def cancel_task_creation(callback_query: CallbackQuery, state: FSMContext):
    """Cancel task creation."""
    try:
        task_key = callback_query.data.replace("cancel_task_", "")
        
        # Clean up pending task
        if hasattr(show_partner_selection_for_task, 'pending_tasks'):
            show_partner_selection_for_task.pending_tasks.pop(task_key, None)
        
        await callback_query.message.edit_text(
            "❌ Task creation cancelled.",
            parse_mode='Markdown'
        )
        await callback_query.answer("Task creation cancelled.")
        
    except Exception as e:
        logger.error(f"Error cancelling task creation: {e}")
        await callback_query.answer("Error cancelling task.")

async def send_task_creation_success(user_id: int, task_data, task_url: str, message: Message, selected_partner_ids: set):
    """Send task creation success message with partner info."""
    try:
        # Get partner names
        from core.container import get_partner_service
        partner_service = get_partner_service()
        
        partner_names = []
        for partner_id in selected_partner_ids:
            partner = partner_service.get_partner_by_id(user_id, partner_id)
            if partner:
                partner_names.append(f"{partner.name} ({partner.platform.title()})")
        
        # Format due time
        due_str = "not set"
        if task_data.due_time:
            try:
                user_info = services.get_task_service().get_user_platform_info(user_id)
                location = user_info.get('location') if user_info else None
                due_str = services.get_parsing_service().convert_utc_to_local_display(task_data.due_time, location)
            except:
                due_str = "not set"
        
        # Build success message
        partners_text = ", ".join(partner_names) if partner_names else "Default partners"
        success_message = (
            f"✅ **Task Created for {partners_text}**\n\n"
            f"📌 **{task_data.title}**\n"
            f"⏰ **Due:** {due_str}"
        )
        
        # Add task URLs
        if task_url:
            if '\n' in task_url:  # Multiple URLs
                success_message += "\n\n**Task Links:**\n" + task_url
            else:  # Single URL  
                success_message += f"\n🔗 [Open Task]({task_url})"
        
        await message.edit_text(
            success_message,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error sending task success message: {e}")
        await message.edit_text(
            "✅ Task created successfully!",
            parse_mode='Markdown'
        )

# Partner Management Handlers

@router.callback_query(lambda c: c.data == "partner_management")
async def show_partner_management(callback_query: CallbackQuery, state: FSMContext):
    """Show partner management interface."""
    await state.clear()
    
    user_id = callback_query.from_user.id
    
    try:
        # Check if partner services are available
        container = services.get_container()
        if not hasattr(container, 'partner_service'):
            await callback_query.message.edit_text(
                "Partner management is not available yet. Please use the platform configuration for now.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Back to Settings", callback_data="show_settings")]
                ]),
                parse_mode='Markdown'
            )
            await callback_query.answer()
            return
        
        # Get partner service
        from core.container import get_partner_service, get_user_preferences_service
        partner_service = get_partner_service()
        prefs_service = get_user_preferences_service()
        
        # Get user's partners
        partners = partner_service.get_user_partners(user_id)
        
        # Get keyboard with partners
        from keyboards.inline import get_partner_management_keyboard
        keyboard = get_partner_management_keyboard(partners)
        
        # Build message
        if partners:
            partner_list = "\n".join([
                f"• {partner.name} ({partner.platform.title()}) {'✅' if partner.enabled else '❌'}"
                for partner in partners
            ])
            message_text = f"👥 **Partner Management**\n\n**Current Partners:**\n{partner_list}\n\nUse the buttons below to manage your partners."
        else:
            message_text = "👥 **Partner Management**\n\nNo partners configured yet. Add a partner to start sharing tasks!"
        
        await callback_query.message.edit_text(
            message_text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Failed to show partner management for user {user_id}: {e}")
        await callback_query.message.edit_text(
            "Failed to load partner management. Please try again later.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Back to Settings", callback_data="show_settings")]
            ])
        )
    
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "add_partner")
async def show_add_partner(callback_query: CallbackQuery, state: FSMContext):
    """Show platform selection for adding a new partner."""
    await state.clear()
    
    from keyboards.inline import get_add_partner_keyboard
    keyboard = get_add_partner_keyboard()
    
    await callback_query.message.edit_text(
        "➕ **Add New Partner**\n\nSelect the platform for your new partner:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("new_partner_"))
async def start_new_partner_setup(callback_query: CallbackQuery, state: FSMContext):
    """Start setup for a new partner."""
    platform = callback_query.data.replace("new_partner_", "")
    
    # Store platform in state
    await state.update_data(new_partner_platform=platform)
    await state.set_state(PartnerManagementState.waiting_partner_name)
    
    await callback_query.message.edit_text(
        f"➕ **Add {platform.title()} Partner**\n\n"
        f"Please enter a name for this partner (e.g., 'Wife', 'John', 'Team Lead'):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Back", callback_data="add_partner")]
        ]),
        parse_mode='Markdown'
    )
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "sharing_settings")
async def show_sharing_settings(callback_query: CallbackQuery, state: FSMContext):
    """Show sharing settings interface."""
    await state.clear()
    
    user_id = callback_query.from_user.id
    
    try:
        from core.container import get_user_preferences_service, get_partner_service
        prefs_service = get_user_preferences_service()
        partner_service = get_partner_service()
        
        # Get user preferences
        prefs = prefs_service.get_preferences_or_default(user_id)
        partners = partner_service.get_user_partners(user_id)
        
        from keyboards.inline import get_sharing_settings_keyboard
        keyboard = get_sharing_settings_keyboard(prefs.show_sharing_ui, prefs.default_partners)
        
        # Build message
        sharing_status = "Enabled" if prefs.show_sharing_ui else "Disabled"
        default_partners_text = "None"
        if prefs.default_partners:
            partner_names = []
            for partner_id in prefs.default_partners:
                partner = partner_service.get_partner_by_id(user_id, partner_id)
                if partner:
                    partner_names.append(partner.name)
            default_partners_text = ", ".join(partner_names) if partner_names else "None"
        
        message_text = (
            f"⚙️ **Sharing Settings**\n\n"
            f"**Show Sharing UI:** {sharing_status}\n"
            f"**Default Partners:** {default_partners_text}\n\n"
            f"The sharing UI allows you to choose which partners to share tasks with during creation."
        )
        
        await callback_query.message.edit_text(
            message_text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Failed to show sharing settings for user {user_id}: {e}")
        await callback_query.message.edit_text(
            "Failed to load sharing settings. Please try again later.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Back", callback_data="partner_management")]
            ])
        )
    
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "toggle_sharing_ui")
async def toggle_sharing_ui(callback_query: CallbackQuery, state: FSMContext):
    """Toggle the sharing UI setting."""
    user_id = callback_query.from_user.id
    
    try:
        from core.container import get_user_preferences_service
        prefs_service = get_user_preferences_service()
        
        # Get current setting and toggle it
        prefs = prefs_service.get_preferences_or_default(user_id)
        new_value = not prefs.show_sharing_ui
        
        # Update setting
        prefs_service.update_sharing_ui_enabled(user_id, new_value)
        
        # Refresh the sharing settings view
        await show_sharing_settings(callback_query, state)
        
    except Exception as e:
        logger.error(f"Failed to toggle sharing UI for user {user_id}: {e}")
        await callback_query.answer("Failed to update setting.", show_alert=True)

# Partner Creation Message Handlers

@router.message(PartnerManagementState.waiting_partner_name)
async def handle_partner_name_input(message: Message, state: FSMContext):
    """Handle partner name input."""
    user_id = message.from_user.id
    partner_name = message.text.strip()
    
    if not partner_name:
        await message.reply("Please enter a valid name for your partner.")
        return
    
    # Store name and ask for credentials
    await state.update_data(new_partner_name=partner_name)
    
    # Get platform from state
    data = await state.get_data()
    platform = data.get('new_partner_platform', 'todoist')
    
    await state.set_state(PartnerManagementState.waiting_partner_credentials)
    
    if platform == 'todoist':
        await message.reply(
            f"Great! Now please enter the Todoist API token for **{partner_name}**.\n\n"
            f"They can find their API token at: https://todoist.com/prefs/integrations",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Cancel", callback_data="add_partner")]
            ])
        )
    elif platform == 'trello':
        await message.reply(
            f"Great! Now please enter the Trello credentials for **{partner_name}** in format:\n"
            f"`api_key:token`\n\n"
            f"They can get their credentials from: https://trello.com/app-key",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Cancel", callback_data="add_partner")]
            ])
        )
    else:
        await message.reply(
            f"Great! Now please enter the {platform} credentials for **{partner_name}**:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Cancel", callback_data="add_partner")]
            ])
        )

@router.message(PartnerManagementState.waiting_partner_credentials)
async def handle_partner_credentials_input(message: Message, state: FSMContext):
    """Handle partner credentials input."""
    user_id = message.from_user.id
    credentials = message.text.strip()
    
    if not credentials:
        await message.reply("Please enter valid credentials for your partner.")
        return
    
    try:
        # Get data from state
        data = await state.get_data()
        partner_name = data.get('new_partner_name')
        platform = data.get('new_partner_platform')
        
        if not partner_name or not platform:
            await message.reply("Something went wrong. Please start over.")
            await state.clear()
            return
        
        # Validate credentials
        if not await _validate_partner_credentials(message, platform, credentials):
            return
        
        # Handle platform-specific configuration
        if platform == 'trello':
            await _handle_trello_partner_setup(message, state, credentials, partner_name)
        else:
            await _create_partner_directly(message, state, user_id, partner_name, platform, credentials)
            
    except Exception as e:
        logger.error(f"Failed to add partner for user {user_id}: {e}")
        await _send_partner_error_message(message, state)

async def _validate_partner_credentials(message: Message, platform: str, credentials: str) -> bool:
    """Validate partner credentials by creating platform instance."""
    from platforms import TaskPlatformFactory
    platform_instance = TaskPlatformFactory.get_platform(platform, credentials)
    if not platform_instance:
        await message.reply(
            f"❌ Invalid {platform} credentials. Please check and try again.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Cancel", callback_data="add_partner")]
            ])
        )
        return False
    return True

async def _handle_trello_partner_setup(message: Message, state: FSMContext, credentials: str, partner_name: str):
    """Handle Trello-specific partner setup with board selection."""
    await state.update_data(new_partner_credentials=credentials)
    await state.set_state(PartnerManagementState.waiting_partner_settings)
    
    try:
        from platforms import TaskPlatformFactory
        platform_instance = TaskPlatformFactory.get_platform('trello', credentials)
        boards = platform_instance.get_boards()
        if not boards:
            await message.reply("❌ No boards found. Please check your Trello credentials.")
            return
        
        from keyboards.inline import get_trello_board_selection_keyboard
        keyboard = get_trello_board_selection_keyboard(boards)
        
        await message.reply(
            f"✅ Credentials verified! Now select a board for **{partner_name}**:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Failed to get Trello boards: {e}")
        await message.reply("❌ Failed to fetch boards. Please check your credentials.")

async def _create_partner_directly(message: Message, state: FSMContext, user_id: int, partner_name: str, platform: str, credentials: str):
    """Create partner directly for non-Trello platforms."""
    from core.container import get_partner_service
    from models.partner import PartnerCreate
    
    partner_service = get_partner_service()
    new_partner = PartnerCreate(
        name=partner_name,
        platform=platform,
        credentials=credentials,
        is_self=False,
        enabled=True
    )
    
    partner_id = partner_service.add_partner(user_id, new_partner)
    
    await message.reply(
        f"✅ **{partner_name}** has been added as a {platform.title()} partner!",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Back to Partners", callback_data="partner_management")]
        ])
    )
    await state.clear()

async def _send_partner_error_message(message: Message, state: FSMContext):
    """Send error message for partner creation failure."""
    await message.reply(
        "❌ Failed to add partner. Please try again later.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Back to Partners", callback_data="partner_management")]
        ])
    )
    await state.clear()

__all__ = ['handle_message']