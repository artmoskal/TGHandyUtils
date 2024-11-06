import asyncio
import time
from typing import Dict, Tuple, List

from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import logging

import bot
from db_handler import get_todoist_user, save_todoist_user, get_todoist_user_info, drop_user_data
from langchain_parser import parse_description_with_langchain
from task_manager import save_task_async

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

@router.message()
async def handle_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    todoist_user, owner_name, location = get_todoist_user_info(user_id)

    if not todoist_user:
        await message.reply("Please provide your Todoist API key to link your account.")
        await state.set_state(TodoistAPIState.waiting_for_api_key)
        return

    # Determine the sender name based on different types of forwarded messages
    if message.forward_date:
        # Forwarded from a user
        if message.forward_from:
            sender_name = f"{message.forward_from.first_name} {message.forward_from.last_name or ''}".strip()
        # Forwarded from a channel or a group chat
        elif message.forward_from_chat:
            sender_name = message.forward_from_chat.title
        # If no recognizable information is available, fallback to a default name
        else:
            sender_name = "Unknown sender"
    else:
        sender_name = message.from_user.full_name

    message_time = time.time()

    # Cancel any existing processing task for the user
    if user_id in thread_storage:
        _, thread_content, existing_task = thread_storage[user_id]
        if existing_task:
            existing_task.cancel()

        # Add the new message to the thread
        thread_content.append((sender_name, message.text))
    else:
        # Start a new thread for this user
        thread_content = [(sender_name, message.text)]

    # Create a new task to process the thread after 1 second of inactivity
    processing_task = asyncio.create_task(schedule_thread_processing(user_id, owner_name, location, message))

    # Update the thread storage
    thread_storage[user_id] = (message_time, thread_content, processing_task)

async def schedule_thread_processing(user_id: int, owner_name: str, location: str, message: Message):
    try:
        await asyncio.sleep(1)  # Wait for 1 second of inactivity

        # If no new message has been added, process the thread
        if user_id in thread_storage:
            _, thread_content, _ = thread_storage[user_id]
            await process_thread(message, thread_content, owner_name, location)
            del thread_storage[user_id]  # Clear the thread storage after processing
    except asyncio.CancelledError:
        # Task was cancelled because a new message was received
        pass

async def process_thread(message: Message, thread_content: List[Tuple[str, str]], owner_name: str, location: str):
    concatenated_content = "\n".join([f"{sender}: {text}" for sender, text in thread_content])
    parsed_task = parse_description_with_langchain(
        concatenated_content,
        owner_name=owner_name,
        location=location
    )
    if parsed_task:
        await save_task_async(parsed_task, message)

__all__ = ['handle_message']
