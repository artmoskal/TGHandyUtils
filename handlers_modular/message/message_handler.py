"""Main message handler for text, voice, and photo messages."""

from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram import Bot

from bot import router
from core.logging import get_logger
from .threading_handler import process_user_input

logger = get_logger(__name__)


@router.message()
async def handle_message(message: Message, state: FSMContext, bot: Bot = None):
    """Handle all regular messages (text, voice, photos) with threading support."""
    # Skip if in a specific state
    current_state = await state.get_state()
    if current_state:
        return
    
    # Handle voice messages
    if message.voice:
        await handle_voice_message(message, state, bot)
        return
    
    user_id = message.from_user.id
    text = message.text
    
    # Handle both text and photos through unified threading system
    if message.photo or message.document:
        # For photos/documents, use caption as text
        caption_text = message.caption or ""
        await process_user_input_with_photo(
            caption_text,  # Use caption for photos, not message.text
            user_id, 
            message, 
            state,
            bot
        )
    elif text and not text.startswith('/'):
        # Use threading system for text messages
        await process_user_input(text, user_id, message, state)


async def handle_voice_message(message: Message, state: FSMContext, bot: Bot):
    """Handle voice messages with transcription."""
    try:
        from core.initialization import services
        transcription_service = services.get_transcription_service()
        
        # Download voice file
        voice_file = await bot.download(message.voice.file_id)
        
        # Transcribe voice
        transcription = transcription_service.transcribe_audio(voice_file)
        
        if transcription:
            # Process transcribed text through threading system
            await process_user_input(transcription, message.from_user.id, message, state)
        else:
            await message.reply("❌ Could not transcribe voice message. Please try again.", disable_web_page_preview=True)
            
    except Exception as e:
        logger.error(f"Error processing voice message: {e}")
        await message.reply("❌ Error processing voice message. Please try again.", disable_web_page_preview=True)


async def process_user_input_with_photo(text: str, user_id: int, message_obj: Message, state: FSMContext, bot: Bot) -> bool:
    """Process user input with photo attachment - handles image to text conversion."""
    try:
        from core.container import container
        recipient_service = container.recipient_service()
        recipients = recipient_service.get_enabled_recipients(user_id)
        
        if not recipients:
            await message_obj.reply(
                "❌ NO RECIPIENTS CONFIGURED\n\n"
                "You need to connect a recipient first!\n\n"
                "🚀 Use /recipients to add your Todoist or Trello account."
            )
            return False

        # Get user full name for threading
        user_full_name = message_obj.from_user.full_name or "User"
        
        # Process photo/document
        screenshot_data = None
        if message_obj.photo:
            largest_photo = max(message_obj.photo, key=lambda x: x.file_size)
            photo_file = await bot.download(largest_photo.file_id)
            screenshot_data = {
                'file_id': largest_photo.file_id,
                'image_data': photo_file.read(),
                'file_name': f'screenshot_{largest_photo.file_id}.jpg'
            }
        elif message_obj.document:
            if message_obj.document.mime_type and message_obj.document.mime_type.startswith('image/'):
                doc_file = await bot.download(message_obj.document.file_id)
                screenshot_data = {
                    'file_id': message_obj.document.file_id,
                    'image_data': doc_file.read(),
                    'file_name': message_obj.document.file_name or f'document_{message_obj.document.file_id}'
                }
        
        # Add to threading system with photo data
        import time
        import threading
        from collections import defaultdict
        
        # Use same threading variables as threading_handler
        from .threading_handler import message_threads, last_message_time, _message_threads_lock, THREAD_TIMEOUT
        
        current_time = time.time()
        with _message_threads_lock:
            # Add message with photo data as 3-tuple
            message_threads[user_id].append((user_full_name, text, screenshot_data))
            last_message_time[user_id] = current_time
        
        # Check if enough time has passed since the last message
        if len(message_threads[user_id]) > 0:
            import asyncio
            await asyncio.sleep(THREAD_TIMEOUT)
            if current_time == last_message_time[user_id]:  # No new messages received
                # Process the complete thread
                with _message_threads_lock:
                    thread_content = message_threads[user_id].copy()
                    message_threads[user_id].clear()  # Clear the thread
                
                # Get user preferences
                user_prefs = recipient_service.get_user_preferences(user_id)
                owner_name = user_prefs.owner_name if user_prefs else "User"
                location = user_prefs.location if user_prefs else None
                
                from .text_handler import process_thread_with_photos
                await process_thread_with_photos(message_obj, thread_content, owner_name, location, user_id)
                return True
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing user input with photo: {e}")
        await message_obj.reply("❌ Error processing your message. Please try again.")
        return False