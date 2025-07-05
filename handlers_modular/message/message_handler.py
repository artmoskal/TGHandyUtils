"""Main message handler for text, voice, and photo messages."""

from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram import Bot

from bot import router
from core.logging import get_logger
from .threading_handler import process_user_input

logger = get_logger(__name__)


from states.recipient_states import RecipientState

from aiogram.filters import StateFilter

@router.message(StateFilter(None))  # Only handle messages when user is NOT in any state
async def handle_message(message: Message, state: FSMContext, bot: Bot):
    """Handle all regular messages (text, voice, photos) with threading support."""
    
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
        voice_service = services.get_voice_processing_service()
        
        # Process voice message
        transcription = await voice_service.process_voice_message(message.voice, bot)
        
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
        
        # Process photo/document with text extraction
        screenshot_data = None
        extracted_text = ""
        summary = ""
        
        from core.initialization import services
        image_service = services.get_image_processing_service()
        
        if message_obj.photo:
            logger.info(f"Processing screenshot with ImageProcessingService")
            analysis_result = await image_service.process_image_message(message_obj.photo, bot)
            extracted_text = analysis_result.get('extracted_text', '')
            summary = analysis_result.get('summary', '')
            screenshot_data = {
                'file_id': analysis_result.get('file_id'),
                'image_data': analysis_result.get('image_data'),
                'file_name': analysis_result.get('file_name')
            }
            logger.info(f"Extracted {len(extracted_text)} characters from screenshot")
        elif message_obj.document and message_obj.document.mime_type and message_obj.document.mime_type.startswith('image/'):
            logger.info(f"Processing document image with ImageProcessingService")
            analysis_result = await image_service.process_image_message(message_obj.document, bot)
            extracted_text = analysis_result.get('extracted_text', '')
            summary = analysis_result.get('summary', '')
            screenshot_data = {
                'file_id': analysis_result.get('file_id'),
                'image_data': analysis_result.get('image_data'),
                'file_name': analysis_result.get('file_name')
            }
        
        # Create enriched content combining caption, extracted text, and summary
        enriched_content = ""
        if text.strip():
            enriched_content += f"[CAPTION] {text.strip()}\n\n"
            logger.info(f"Added caption to enriched_content: '{text.strip()}'")
        if extracted_text.strip():
            enriched_content += f"[SCREENSHOT TEXT]\n{extracted_text}\n\n"
            logger.info(f"Added extracted text: {len(extracted_text)} characters")
        if summary.strip():
            enriched_content += f"[SCREENSHOT DESCRIPTION] {summary}"
            logger.info(f"Added screenshot summary: {len(summary)} characters")
        
        logger.info(f"Final enriched_content: '{enriched_content[:200]}...'")
        
        # Add to threading system with enriched content and photo data
        import time
        import threading
        from collections import defaultdict
        
        # Use same threading variables as threading_handler
        from .threading_handler import message_threads, last_message_time, _message_threads_lock, THREAD_TIMEOUT
        
        current_time = time.time()
        with _message_threads_lock:
            # Add message with enriched content and screenshot data as 3-tuple
            message_threads[user_id].append((user_full_name, enriched_content, screenshot_data))
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