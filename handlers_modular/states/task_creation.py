"""Task creation state handlers."""

from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot import router
from states.recipient_states import RecipientState
from core.container import container
from models.task import TaskCreate
from core.logging import get_logger

logger = get_logger(__name__)


@router.message(RecipientState.waiting_for_task)
async def handle_task_creation(message: Message, state: FSMContext):
    """Handle task creation with recipient system."""
    user_id = message.from_user.id
    task_description = message.text.strip()
    
    if not task_description:
        await message.reply("❌ Task description cannot be empty. Please enter a description:", disable_web_page_preview=True)
        return
    
    try:
        state_data = await state.get_data()
        selected_recipients = state_data.get('selected_recipients', [])
        
        # Parse task description using AI to extract task details and timing
        from core.initialization import services
        parsing_service = services.get_parsing_service()
        
        try:
            # Get user info for proper parsing context
            recipient_service = container.recipient_service()
            user_prefs = recipient_service.get_user_preferences(user_id)
            owner_name = user_prefs.owner_name if user_prefs else "User"
            location = user_prefs.location if user_prefs else None
            
            # Use the proper parsing method that handles time correctly
            parsed_task_dict = parsing_service.parse_content_to_task(
                task_description,
                owner_name=owner_name,
                location=location
            )
            
            if parsed_task_dict:
                task_data = TaskCreate(**parsed_task_dict)
            else:
                raise Exception("Parsing returned None")
                
        except Exception as e:
            logger.warning(f"Failed to parse task description '{task_description}' for user {user_id}: {e}")
            # Fallback: all tasks without time go to tomorrow 9AM UTC
            task_data = TaskCreate(
                description=task_description,
                owner=user_prefs.owner_name if user_prefs else "User",
                location=user_prefs.location if user_prefs else None
            )
        
        # Get recipients for the task
        recipient_service = container.recipient_service()
        if selected_recipients:
            # Use selected recipients
            recipients = []
            for recipient_id in selected_recipients:
                recipient = recipient_service.get_recipient_by_id(user_id, int(recipient_id))
                if recipient and recipient.enabled:
                    recipients.append(recipient)
        else:
            # Use all enabled recipients
            recipients = recipient_service.get_enabled_recipients(user_id)
        
        if not recipients:
            await message.reply(
                "❌ NO RECIPIENTS CONFIGURED\n\n"
                "You need to connect a recipient first!\n\n"
                "🚀 Use /recipients to add your Todoist or Trello account.",
                disable_web_page_preview=True
            )
            await state.clear()
            return
        
        # Create the task
        task_service = container.recipient_task_service()
        recipient_ids = [r.id for r in recipients]
        success, feedback, actions = task_service.create_task_for_recipients(
            user_id=user_id,
            title=task_data.title,
            description=task_data.description,
            due_time=task_data.due_time,
            specific_recipients=recipient_ids,
            chat_id=message.chat.id,
            message_id=message.message_id
        )
        
        # Clear state
        await state.clear()
        
        # Send response
        from handlers_modular.base import handle_task_creation_response
        await handle_task_creation_response(message, success, feedback, actions)
        
    except Exception as e:
        logger.error(f"Failed to create task for user {user_id}: {e}")
        await message.reply("❌ Error creating task. Please try again.", disable_web_page_preview=True)
        await state.clear()