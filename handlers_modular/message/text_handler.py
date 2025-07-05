"""Text message processing with photo support."""

from typing import List, Tuple, Optional, Dict
from aiogram.types import Message

from core.container import container
from core.logging import get_logger
from models.task import TaskCreate
from handlers_modular.base import handle_task_creation_response

logger = get_logger(__name__)


async def process_thread_with_photos(message: Message, thread_content: List[Tuple], 
                                    owner_name: str, location: str, owner_id: int):
    """Process a thread of messages that may include photos and create a task."""
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
            else:  # (user, content)
                text_content.append(item)
        
        # Concatenate thread content
        concatenated_content = "\n".join([f"{sender}: {text}" for sender, text in text_content])
        
        logger.error(f"🔍 DEBUG: Processing thread with photos - {len(thread_content)} messages")
        logger.error(f"🔍 DEBUG: Content: {concatenated_content}")
        logger.error(f"🔍 DEBUG: Has screenshot: {screenshot_data is not None}")
        
        # Parse using recipient parsing service
        from core.initialization import services
        parsing_service = services.get_parsing_service()
        
        parsed_task_dict = parsing_service.parse_content_to_task(
            concatenated_content,
            owner_name=owner_name,
            location=location
        )
        
        if parsed_task_dict:
            logger.error(f"🔍 DEBUG: LLM parsed successfully: {parsed_task_dict}")
            
            # Always use the original concatenated content as description to avoid duplication
            # LLM can create the title, but description should be the raw conversation
            task_data = TaskCreate(
                title=parsed_task_dict['title'],
                description=concatenated_content,  # Use original content
                due_time=parsed_task_dict['due_time']
            )
        else:
            logger.error(f"🔍 DEBUG: LLM parsing failed, using fallback")
            # Fallback: all tasks without time go to tomorrow 9AM UTC
            from datetime import datetime, timezone, timedelta
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            due_time = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
            
            task_data = TaskCreate(
                title=concatenated_content[:100],  # Truncate long titles
                description=concatenated_content,
                due_time=due_time
            )
        
        # Create task using recipient task service WITH screenshot data
        task_service = container.recipient_task_service()
        recipient_service = container.recipient_service()
        
        # First create tasks for personal recipients only
        success, feedback, actions = task_service.create_task_for_recipients(
            user_id=owner_id,
            title=task_data.title,
            description=task_data.description,
            due_time=task_data.due_time,
            specific_recipients=None,  # Use default recipients (personal only)
            screenshot_data=screenshot_data,
            chat_id=message.chat.id,
            message_id=message.message_id
        )
        
        # Note: Post-task actions (add to other recipients) are now handled 
        # by recipient_task_service._generate_post_task_actions() to avoid duplication
        
        # Use unified response handler
        await handle_task_creation_response(message, success, feedback, actions)
            
    except Exception as e:
        logger.error(f"Error processing thread with photos: {e}")
        await message.reply("❌ Error creating task from messages. Please try again.", disable_web_page_preview=True)