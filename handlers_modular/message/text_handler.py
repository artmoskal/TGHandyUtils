"""Text message processing with photo support."""

from typing import List, Tuple, Optional, Dict
from aiogram.types import Message

from core.container import container
from core.logging import get_logger
from models.task import TaskCreate
from handlers_modular.base import handle_task_creation_response
from helpers.ui_helpers import format_platform_button

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
        
        logger.debug(f"Processing thread with photos - {len(thread_content)} messages")
        logger.debug(f"Content: {concatenated_content}")
        logger.debug(f"Has screenshot: {screenshot_data is not None}")
        
        # Parse using recipient parsing service
        from core.initialization import services
        parsing_service = services.get_parsing_service()
        
        parsed_task_dict = parsing_service.parse_content_to_task(
            concatenated_content,
            owner_name=owner_name,
            location=location
        )
        
        if parsed_task_dict:
            logger.debug(f"LLM parsed successfully: {parsed_task_dict}")
            
            # Always use the original concatenated content as description to avoid duplication
            # LLM can create the title, but description should be the raw conversation
            task_data = TaskCreate(
                title=parsed_task_dict['title'],
                description=concatenated_content,  # Use original content
                due_time=parsed_task_dict['due_time']
            )
        else:
            logger.debug(f"LLM parsing failed, using fallback")
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
        
        # Handle special case when no default recipients are set
        if not success and feedback == "NO_DEFAULT_RECIPIENTS":
            # Check if UI is enabled - this should only happen when UI is enabled
            ui_enabled = recipient_service.is_recipient_ui_enabled(owner_id)
            if not ui_enabled:
                # This shouldn't happen with the new logic, but safety check
                await message.reply(
                    "⚙️ **Automatic Mode Active**\n\n"
                    "Recipient selection is disabled. Please set at least one platform as default in Settings → Manage Accounts.",
                    disable_web_page_preview=True
                )
                return
            
            # Create a temporary task in database first, then show recipient buttons for it
            task_repo = container.task_repository()
            
            # Create task without any recipients  
            task_id = task_repo.create(
                user_id=owner_id,
                chat_id=message.chat.id,
                message_id=message.message_id,
                task_data=task_data,
                screenshot_file_id=screenshot_data.get('file_id') if screenshot_data else None
            )
            
            if task_id:
                # Generate action buttons for all available recipients
                recipients = recipient_service.get_enabled_recipients(owner_id)
                from keyboards.recipient import get_post_task_actions_keyboard
                
                # Create actions for adding to any recipient
                add_actions = []
                for recipient in recipients:
                    add_actions.append({
                        "text": format_platform_button(recipient.platform_type, recipient.name, "Add to"),
                        "callback_data": f"add_task_to_{recipient.id}_{task_id}",
                        "recipient_id": str(recipient.id),
                        "recipient_name": recipient.name
                    })
                
                actions = {
                    "add_actions": add_actions,
                    "remove_actions": []
                }
                
                keyboard = get_post_task_actions_keyboard(actions)
                
                await message.reply(
                    f"✅ **Task Created**\n\n"
                    f"**{task_data.title}**\n\n"
                    f"📅 **Due:** {task_data.due_time}\n\n"
                    f"No default recipients set. Choose where to add this task:",
                    reply_markup=keyboard,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
            else:
                await message.reply("❌ Error creating task. Please try again.", disable_web_page_preview=True)
            return
        
        # Note: Post-task actions (add to other recipients) are now handled 
        # by recipient_task_service._generate_post_task_actions() to avoid duplication
        
        # Use unified response handler
        await handle_task_creation_response(message, success, feedback, actions)
            
    except Exception as e:
        logger.error(f"Error processing thread with photos: {e}")
        await message.reply("❌ Error creating task from messages. Please try again.", disable_web_page_preview=True)