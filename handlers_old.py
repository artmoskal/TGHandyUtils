import asyncio
import time
import json
from typing import Dict, Tuple, List
from collections import defaultdict

from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Voice
import logging
from aiogram import Bot

import bot
from db_handler import (
    get_platform_token, 
    get_platform_type, 
    get_platform_user_info, 
    save_platform_user,
    drop_user_data
)
from langchain_parser import parse_description_with_langchain, transcribe
from task_manager import save_task_async
from services.voice_processing import process_voice_message
from keyboards.inline import (
    get_transcription_keyboard, 
    get_platform_selection_keyboard,
    get_trello_board_selection_keyboard,
    get_trello_list_selection_keyboard
)
from platforms import TaskPlatformFactory
from platforms.trello import TrelloPlatform

router = bot.router

# Define the finite state machine states
class TaskPlatformState(StatesGroup):
    selecting_platform = State()
    waiting_for_api_key = State()
    waiting_for_location = State()
    # Trello-specific states
    waiting_for_board_selection = State()
    waiting_for_list_selection = State()

# Store threads in memory temporarily
thread_storage: Dict[int, Tuple[float, List[Tuple[str, str]], asyncio.Task]] = {}

logger = logging.getLogger(__name__)

# Define the finite state machine states
class DropUserDataState(StatesGroup):
    waiting_for_confirmation = State()

# Thread collection variables
message_threads = defaultdict(list)
last_message_time = defaultdict(float)
THREAD_TIMEOUT = 1.0  # 1 second timeout

@router.message(Command('drop_user_data'))
async def initiate_drop_user_data(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await message.reply("Are you sure you want to drop all your data? Reply with 'yes' to confirm or 'no' to cancel.")
    await state.set_state(DropUserDataState.waiting_for_confirmation)

@router.message(Command('settings'))
async def show_user_settings(message: Message, state: FSMContext):
    """Show the current user settings."""
    user_id = message.from_user.id
    user_info = get_platform_user_info(user_id)
    
    if not user_info or not user_info.get('platform_token'):
        await message.reply(
            "You haven't set up a task management platform yet. Use /set_platform to get started."
        )
        return
    
    platform_type = user_info.get('platform_type', 'todoist')
    location = user_info.get('location', 'not set')
    
    # For Trello, show board and list settings
    platform_settings_info = ""
    if platform_type == 'trello' and user_info.get('platform_settings'):
        try:
            settings = json.loads(user_info['platform_settings'])
            board_id = settings.get('board_id', 'not set')
            list_id = settings.get('list_id', 'not set')
            platform_settings_info = f"\nBoard ID: {board_id}\nList ID: {list_id}"
        except json.JSONDecodeError:
            platform_settings_info = "\nWarning: Your Trello settings appear to be corrupted. Use /set_platform to reconfigure."
    
    await message.reply(
        f"Your current settings:\n\n"
        f"Platform: {platform_type.capitalize()}\n"
        f"Location: {location}"
        f"{platform_settings_info}\n\n"
        f"To change these settings, use /set_platform"
    )

@router.message(Command('set_platform'))
async def start_platform_selection(message: Message, state: FSMContext):
    """Start the process of selecting a task management platform."""
    keyboard = get_platform_selection_keyboard()
    await message.reply("Please select your task management platform:", reply_markup=keyboard)
    await state.set_state(TaskPlatformState.selecting_platform)

@router.callback_query(lambda c: c.data.startswith("platform_"))
async def process_platform_selection(callback_query: CallbackQuery, state: FSMContext):
    """Process the selection of a task management platform."""
    platform_type = callback_query.data.split("_")[1]
    await state.update_data(platform_type=platform_type)
    
    if platform_type == "todoist":
        await callback_query.message.edit_text("Please provide your Todoist API token.")
        await state.set_state(TaskPlatformState.waiting_for_api_key)
    elif platform_type == "trello":
        await callback_query.message.edit_text(
            "Please provide your Trello API credentials in the format 'API_KEY:TOKEN'.\n\n"
            "You can get your API key from https://trello.com/app-key and generate a token from there."
        )
        await state.set_state(TaskPlatformState.waiting_for_api_key)
    
    await callback_query.answer()

@router.message(DropUserDataState.waiting_for_confirmation)
async def confirm_drop_user_data(message: Message, state: FSMContext):
    user_response = message.text.strip().lower()
    user_id = message.from_user.id

    if user_response == 'yes':
        drop_user_data(user_id)
        await message.reply("All your data has been successfully dropped.")
        await state.clear()
    elif user_response == 'no':
        await message.reply("Data drop canceled.")
        await state.clear()
    else:
        await message.reply("Invalid response. Please reply with 'yes' to confirm or 'no' to cancel.")

@router.message(TaskPlatformState.waiting_for_api_key)
async def receive_platform_key(message: Message, state: FSMContext):
    user_id = message.from_user.id
    api_key = message.text.strip()
    owner_name = message.from_user.full_name
    
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
    
    # For other platforms (Todoist), save directly
    save_platform_user(user_id, api_key, platform_type, owner_name)
    await message.reply(f"Your {platform_type.capitalize()} account has been linked successfully, {owner_name}!")
    
    # Check if location is available
    user_info = get_platform_user_info(user_id)
    if user_info and not user_info.get('location'):
        await message.reply("Please provide your location (city or country) to determine your time zone.")
        await state.set_state(TaskPlatformState.waiting_for_location)
    else:
        await state.clear()

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
    
    state_data = await state.get_data()
    api_key = state_data.get('api_key')
    board_id = state_data.get('board_id')
    
    # Create platform settings JSON
    platform_settings = json.dumps({
        'board_id': board_id,
        'list_id': list_id
    })
    
    # Save all Trello information
    save_platform_user(user_id, api_key, 'trello', owner_name, None, platform_settings)
    
    await callback_query.message.edit_text(f"Your Trello account has been linked successfully, {owner_name}!")
    
    # Ask for location if not set
    user_info = get_platform_user_info(user_id)
    if not user_info.get('location'):
        await callback_query.message.reply("Please provide your location (city or country) to determine your time zone.")
        await state.set_state(TaskPlatformState.waiting_for_location)
    else:
        await state.clear()
    
    await callback_query.answer()

@router.message(TaskPlatformState.waiting_for_location)
async def receive_location(message: Message, state: FSMContext):
    user_id = message.from_user.id
    location = message.text.strip()

    # Get user's current platform information
    user_info = get_platform_user_info(user_id)
    if user_info:
        # Update the user record to include the location
        save_platform_user(
            user_id, 
            user_info.get('platform_token'), 
            user_info.get('platform_type', 'todoist'), 
            user_info.get('owner_name'),
            location,
            user_info.get('platform_settings')
        )

        platform_name = user_info.get('platform_type', 'task management platform').capitalize()
        await message.reply(f"Location set to {location}. All tasks in {platform_name} will now consider this time zone.")
    else:
        await message.reply("Error: Your account information could not be found. Please set up your platform again.")
    
    await state.clear()

async def process_user_input(text: str, user_id: int, message_obj, state: FSMContext):
    user_info = get_platform_user_info(user_id)
    
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
    message_threads[user_id].append((user_full_name, text))
    last_message_time[user_id] = current_time
    
    # Check if enough time has passed since the last message
    if len(message_threads[user_id]) > 0:
        await asyncio.sleep(THREAD_TIMEOUT)
        if current_time == last_message_time[user_id]:  # No new messages received
            # Process the complete thread
            thread_content = message_threads[user_id].copy()
            message_threads[user_id].clear()  # Clear the thread
            
            owner_name = user_info.get('owner_name')
            location = user_info.get('location')
            
            await process_thread(message_obj, thread_content, owner_name, location, message_obj.from_user.id)
            return True
    return True

@router.message()
async def handle_message(message: Message, state: FSMContext, bot: Bot):
    if message.voice:
        await handle_voice_message(message, state, bot)
        return
        
    if message.text and message.text.startswith('/'):
        return
        
    if message.reply_to_message and message.reply_to_message.from_user.is_bot:
        return

    await process_user_input(
        message.text, 
        message.from_user.id, 
        message, 
        state
    )

async def extract_text_from_voice(voice: Voice, bot: Bot):
    # Download the file
    file = await bot.get_file(voice.file_id)
    downloaded_file = await bot.download_file(file.file_path)
    
    # Create a named temporary file-like object
    from io import BytesIO
    audio_data = BytesIO(downloaded_file.read())
    # Voice messages in Telegram are typically in OGG format
    audio_data.name = "voice_message.ogg"
    
    # Pass the properly formatted file to transcribe
    return await transcribe(audio_data)

async def schedule_thread_processing(user_id: int, owner_name: str, location: str, message: Message):
    try:
        await asyncio.sleep(1)  # Wait for 1 second of inactivity

        # If no new message has been added, process the thread
        if user_id in thread_storage:
            _, thread_content, _ = thread_storage[user_id]
            await process_thread(message, thread_content, owner_name, location, message.from_user.id)
            del thread_storage[user_id]  # Clear the thread storage after processing
    except asyncio.CancelledError:
        # Task was cancelled because a new message was received
        pass

async def process_thread(message: Message, thread_content: List[Tuple[str, str]], owner_name: str, location: str, owner_id: int):
    concatenated_content = "\n".join([f"{sender}: {text}" for sender, text in thread_content])
    parsed_task = parse_description_with_langchain(
        concatenated_content,
        owner_name=owner_name,
        location=location
    )
    if parsed_task:
        await save_task_async(parsed_task, message, owner_id)

async def handle_voice_message(message: Message, state: FSMContext, bot: Bot):
    # Show processing message
    processing_msg = await message.answer("Processing voice message...")
    
    voice_text = await process_voice_message(message.voice, bot)
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

@router.callback_query(lambda c: c.data == "transcribe_confirm")
async def confirm_transcription(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback_query.from_user.id
    
    user_info = get_platform_user_info(user_id)
    
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
    
    await process_thread(callback_query.message, thread_content, owner_name, location, user_id)
    
    platform_name = user_info.get('platform_type', 'platform').capitalize()
    await callback_query.answer(f"Task created in {platform_name}!")
    await state.clear()

@router.callback_query(lambda c: c.data == "transcribe_cancel")
async def cancel_transcription(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text("Transcription cancelled. Please try sending your voice message again.")
    await callback_query.answer()
    await state.clear()

__all__ = ['handle_message']