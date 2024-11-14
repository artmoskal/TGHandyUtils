import asyncio
import time
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
from db_handler import get_todoist_user, save_todoist_user, get_todoist_user_info, drop_user_data
from langchain_parser import parse_description_with_langchain, transcribe
from task_manager import save_task_async
from services.voice_processing import process_voice_message
from keyboards.inline import get_transcription_keyboard

router = bot.router

# Define the finite state machine states
class TodoistAPIState(StatesGroup):
    waiting_for_api_key = State()
    waiting_for_location = State()

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

@router.message(TodoistAPIState.waiting_for_api_key)
async def receive_todoist_key(message: Message, state: FSMContext):
    user_id = message.from_user.id
    api_key = message.text.strip()
    owner_name = message.from_user.full_name  # Capture the owner's name from the message sender

    # Save the API key and owner's name, but location is not yet set
    save_todoist_user(user_id, api_key, owner_name)
    await message.reply(f"Your Todoist account has been linked successfully, {owner_name}!")

    # Check if location is available
    _, _, location = get_todoist_user_info(user_id)
    if not location:
        await message.reply("Please provide your location (city or country) to determine your time zone.")
        await state.set_state(TodoistAPIState.waiting_for_location)
    else:
        await state.clear()

@router.message(TodoistAPIState.waiting_for_location)
async def receive_location(message: Message, state: FSMContext):
    user_id = message.from_user.id
    location = message.text.strip()

    # Update the user record to include the location
    todoist_user, owner_name, _ = get_todoist_user_info(user_id)
    save_todoist_user(user_id, todoist_user, owner_name, location)

    await message.reply(f"Location set to {location}. All tasks will now consider this time zone.")
    await state.clear()

async def process_user_input(text: str, user_id: int, message_obj, state: FSMContext):
    todoist_user, owner_name, location = get_todoist_user_info(user_id)
    
    if not todoist_user:
        await message_obj.reply("Please provide your Todoist API key to link your account.")
        await state.set_state(TodoistAPIState.waiting_for_api_key)
        return False

    # Log for debugging
    print(f"Todoist user found: {todoist_user}")

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
    todoist_user, owner_name, location = get_todoist_user_info(user_id)
    
    if not todoist_user:
        await callback_query.message.reply("Please provide your Todoist API key to link your account.")
        await state.set_state(TodoistAPIState.waiting_for_api_key)
        return

    # Use the stored user name
    user_full_name = data.get('user_full_name', callback_query.from_user.full_name)
    thread_content = [(user_full_name, data['transcribed_text'])]
    await process_thread(callback_query.message, thread_content, owner_name, location, user_id)
    await callback_query.answer("Task created!")
    await state.clear()

@router.callback_query(lambda c: c.data == "transcribe_cancel")
async def cancel_transcription(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text("Transcription cancelled. Please try sending your voice message again.")
    await callback_query.answer()
    await state.clear()

__all__ = ['handle_message']
