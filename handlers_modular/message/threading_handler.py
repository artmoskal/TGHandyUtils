"""Message threading system for grouping rapid messages."""

import asyncio
import time
import threading
from typing import Dict, List
from collections import defaultdict
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)

# Threading system for grouping messages within 1 second (30 seconds if voice messages are involved)
message_threads = defaultdict(list)
last_message_time = defaultdict(float)
voice_processing = defaultdict(bool)  # Track if voice messages are being processed
_message_threads_lock = threading.Lock()
THREAD_TIMEOUT = 1.0  # 1 second timeout for regular messages
VOICE_THREAD_TIMEOUT = 30.0  # 30 second timeout when voice messages are involved



async def process_user_input(text: str, user_id: int, message_obj: Message, state: FSMContext) -> bool:
    """Process user text input with threading support - groups messages within 1 second (30 seconds if voice messages are involved)."""
    try:
        recipient_service = container.recipient_service()
        recipients = recipient_service.get_enabled_recipients(user_id)
        
        if not recipients:
            await message_obj.reply(
                "❌ NO RECIPIENTS CONFIGURED\n\n"
                "You need to connect a recipient first!\n\n"
                "🚀 Use /recipients to add your Todoist or Trello account."
            )
            return False

        # Get user full name for threading (handle forwarded messages)
        if message_obj.forward_from:
            user_full_name = message_obj.forward_from.full_name
        elif message_obj.forward_sender_name:
            user_full_name = message_obj.forward_sender_name
        else:
            user_full_name = message_obj.from_user.full_name or "User"
        
        # Add to threading system
        current_time = time.time()
        with _message_threads_lock:
            message_threads[user_id].append((user_full_name, text))
            last_message_time[user_id] = current_time
        
        # Use dynamic timeout based on whether voice messages are being processed
        timeout_to_use = VOICE_THREAD_TIMEOUT if voice_processing[user_id] else THREAD_TIMEOUT
        
        # Wait for thread timeout and check if this was the last message
        await asyncio.sleep(timeout_to_use)
        
        # Only process if this message was the last one received AND no voice is being processed
        with _message_threads_lock:
            # If voice is being processed, let the voice handler deal with all messages
            if voice_processing[user_id]:
                logger.info(f"Skipping text processing for user {user_id} - voice message is being processed")
                return True
                
            if current_time == last_message_time[user_id] and len(message_threads[user_id]) > 0:
                # Process the complete thread
                thread_content = message_threads[user_id].copy()
                message_threads[user_id].clear()  # Clear the thread
                voice_processing[user_id] = False  # Reset voice processing flag
                
                # Get user preferences
                user_prefs = recipient_service.get_user_preferences(user_id)
                owner_name = user_prefs.owner_name if user_prefs else "User"
                location = user_prefs.location if user_prefs else None
                
                # Import here to avoid circular imports
                from .text_handler import process_thread_with_photos
                await process_thread_with_photos(message_obj, thread_content, owner_name, location, user_id)
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing user input: {e}")
        await message_obj.reply("❌ Error processing your message. Please try again.")
        return False