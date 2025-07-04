"""Task action callback handlers."""

from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot import router
from core.container import container
from core.logging import get_logger
from models.task import TaskCreate

logger = get_logger(__name__)


@router.callback_query(lambda c: c.data == "cancel_task")
async def cancel_task(callback_query: CallbackQuery, state: FSMContext):
    """Cancel task creation."""
    await state.clear()
    await callback_query.message.edit_text("❌ Task creation cancelled.", disable_web_page_preview=True)
    await callback_query.answer()


@router.callback_query(lambda c: c.data == "transcribe_confirm")
async def confirm_transcription(callback_query: CallbackQuery, state: FSMContext):
    """Handle transcription confirmation."""
    try:
        data = await state.get_data()
        transcribed_text = data.get('transcribed_text')
        user_full_name = data.get('user_full_name', callback_query.from_user.full_name)
        user_id = callback_query.from_user.id
        
        if not transcribed_text:
            await callback_query.answer("❌ No transcription data found")
            return
        
        # Process the transcribed text as a regular message
        await callback_query.message.edit_text("✅ Creating task from voice message...", disable_web_page_preview=True)
        
        # Check if user has platforms configured
        recipient_service = container.recipient_service()
        recipients = recipient_service.get_enabled_recipients(user_id)
        
        if not recipients:
            await callback_query.message.edit_text(
                "❌ NO RECIPIENTS CONFIGURED\n\n"
                "You need to connect a recipient first!\n\n"
                "🚀 Use /recipients to add your Todoist or Trello account.",
                disable_web_page_preview=True
            )
            await state.clear()
            return
        
        # Get user preferences
        user_prefs = recipient_service.get_user_preferences(user_id)
        owner_name = user_prefs.owner_name if user_prefs else "User"
        location = user_prefs.location if user_prefs else None
        
        # Create task from transcribed text
        task_service = container.recipient_task_service()
        task_data = TaskCreate(
            description=transcribed_text,
            owner=owner_name,
            location=location
        )
        
        # Create task for all enabled recipients
        success, feedback, actions = task_service.create_task_for_recipients(
            user_id=user_id,
            task_data=task_data,
            recipients=recipients
        )
        
        # Clear state
        await state.clear()
        
        # Send response
        from handlers_modular.base import handle_task_creation_response
        await handle_task_creation_response(callback_query.message, success, feedback, actions)
        
    except Exception as e:
        logger.error(f"Error confirming transcription: {e}")
        await callback_query.answer("❌ Error processing transcription")
        await state.clear()


@router.callback_query(lambda c: c.data == "transcribe_cancel")
async def cancel_transcription(callback_query: CallbackQuery, state: FSMContext):
    """Handle transcription cancellation."""
    await state.clear()
    await callback_query.message.edit_text("❌ Voice message transcription cancelled.", disable_web_page_preview=True)
    await callback_query.answer()


@router.callback_query(lambda c: c.data.startswith("add_task_to_"))
async def handle_add_task_to_recipient(callback_query: CallbackQuery):
    """Handle adding task to additional recipient."""
    try:
        # Parse recipient_id and task_id from callback data
        data_parts = callback_query.data.replace("add_task_to_", "").split("_")
        if len(data_parts) != 2:
            await callback_query.answer("❌ Invalid data format")
            return
        
        recipient_id, task_id = data_parts
        user_id = callback_query.from_user.id
        
        # Get task service and add task to recipient
        task_service = container.recipient_task_service()
        success, message = task_service.add_task_to_recipient(
            user_id=user_id,
            task_id=int(task_id),
            recipient_id=int(recipient_id)
        )
        
        if success:
            # Update message to show success
            await callback_query.message.edit_text(
                callback_query.message.text + f"\n\n{message}",
                parse_mode='Markdown',
                reply_markup=None,  # Remove keyboard
                disable_web_page_preview=True
            )
            await callback_query.answer("✅ Task added successfully!")
        else:
            await callback_query.answer(f"❌ {message}")
            
    except Exception as e:
        logger.error(f"Error adding task to recipient: {e}")
        await callback_query.answer("❌ Error adding task")


@router.callback_query(lambda c: c.data.startswith("remove_task_from_"))
async def handle_remove_task_from_recipient(callback_query: CallbackQuery):
    """Handle removing task from a recipient."""
    try:
        # Parse recipient_id and task_id from callback data
        data_parts = callback_query.data.replace("remove_task_from_", "").split("_")
        if len(data_parts) != 2:
            await callback_query.answer("❌ Invalid data format")
            return
        
        recipient_id, task_id = data_parts
        
        # For now, just acknowledge - removal is more complex as we need platform task IDs
        await callback_query.answer("⚠️ Task removal coming soon!")
        
        # Update message to show acknowledgment
        recipient_service = container.recipient_service()
        recipient = recipient_service.get_recipient_by_id(callback_query.from_user.id, int(recipient_id))
        recipient_name = recipient.name if recipient else "recipient"
        
        await callback_query.message.edit_text(
            callback_query.message.text + f"\n\n❌ _Removal from {recipient_name} coming soon_",
            parse_mode='Markdown',
            reply_markup=None,
            disable_web_page_preview=True
        )
            
    except Exception as e:
        logger.error(f"Error removing task from recipient: {e}")
        await callback_query.answer("❌ Error removing task")


@router.callback_query(lambda c: c.data == "task_actions_done")
async def handle_task_actions_done(callback_query: CallbackQuery):
    """Handle completion of post-task actions."""
    # Remove the keyboard
    await callback_query.message.edit_reply_markup(reply_markup=None)
    await callback_query.answer("✅ Done!")