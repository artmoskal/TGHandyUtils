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
    # Add back to menu button after cancellation
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Back to Menu", callback_data="back_to_menu")]
    ])
    
    await callback_query.message.edit_text(
        "❌ Task creation cancelled.", 
        reply_markup=back_keyboard,
        disable_web_page_preview=True
    )
    await callback_query.answer()


@router.callback_query(lambda c: c.data.startswith("add_shared_task_"))
async def add_shared_task(callback_query: CallbackQuery, state: FSMContext):
    """Handle adding task to shared recipient."""
    try:
        # Extract recipient ID from callback data
        recipient_id = int(callback_query.data.replace("add_shared_task_", ""))
        user_id = callback_query.from_user.id
        
        # Get the last created task ID from the database
        task_repo = container.task_repository()
        # Get most recent task for this user (ordered by due_time)
        # This is a simplification - ideally we'd store task_id in state or message
        user_tasks = task_repo.get_by_user(user_id)
        
        if not user_tasks:
            await callback_query.answer("❌ No tasks found to add to shared recipient")
            return
        
        # Get the most recently created task (last in the list by ID)
        task_id = max(user_tasks, key=lambda t: t.id).id
        
        # Add task to shared recipient
        task_service = container.recipient_task_service()
        success, feedback = task_service.add_task_to_recipient(user_id, task_id, recipient_id)
        
        if success:
            await callback_query.answer(f"✅ {feedback}")
            # Update the message to show the task was added
            current_text = callback_query.message.text or ""
            updated_text = current_text + f"\n\n✅ {feedback}"
            
            # Remove the button that was just clicked from the message
            from keyboards.recipient import get_post_task_actions_keyboard
            if callback_query.message.reply_markup:
                # Get current actions and remove the clicked one
                current_keyboard = callback_query.message.reply_markup
                actions = {'add_actions': []}
                
                for row in current_keyboard.inline_keyboard:
                    for button in row:
                        if button.callback_data != callback_query.data and button.callback_data.startswith("add_shared_task_"):
                            actions['add_actions'].append({
                                'text': button.text,
                                'callback_data': button.callback_data
                            })
                
                # Update message with reduced keyboard
                if actions['add_actions']:
                    new_keyboard = get_post_task_actions_keyboard(actions)
                else:
                    # No more shared recipients to add, remove keyboard
                    new_keyboard = None
                    
                await callback_query.message.edit_text(
                    updated_text,
                    reply_markup=new_keyboard,
                    disable_web_page_preview=True,
                    parse_mode='Markdown'
                )
        else:
            await callback_query.answer(f"❌ {feedback}")
            
    except Exception as e:
        logger.error(f"Error adding shared task: {e}")
        await callback_query.answer("❌ Error adding task to shared recipient")


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
            # Add navigation for no recipients error
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            error_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎯 Add Recipients", callback_data="show_recipients")],
                [InlineKeyboardButton(text="🏠 Back to Menu", callback_data="back_to_menu")]
            ])
            
            await callback_query.message.edit_text(
                "❌ NO RECIPIENTS CONFIGURED\n\n"
                "You need to connect a recipient first!\n\n"
                "🚀 Use /recipients to add your Todoist or Trello account.",
                reply_markup=error_keyboard,
                disable_web_page_preview=True
            )
            await state.clear()
            return
        
        # Get user preferences
        user_prefs = recipient_service.get_user_preferences(user_id)
        owner_name = user_prefs.owner_name if user_prefs else "User"
        location = user_prefs.location if user_prefs else None
        
        # Parse the transcribed text to create a proper task
        from core.initialization import services
        parsing_service = services.get_parsing_service()
        
        parsed_task_dict = parsing_service.parse_content_to_task(
            transcribed_text,
            owner_name=owner_name,
            location=location
        )
        
        if parsed_task_dict:
            # Use parsed data but keep original text as description
            task_service = container.recipient_task_service()
            success, feedback, actions = task_service.create_task_for_recipients(
                user_id=user_id,
                title=parsed_task_dict['title'],
                description=transcribed_text,  # Keep full transcribed text
                due_time=parsed_task_dict['due_time'],
                specific_recipients=None  # Use default recipients
            )
        else:
            # Fallback if parsing fails
            from datetime import datetime, timezone, timedelta
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            due_time = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
            
            task_service = container.recipient_task_service()
            success, feedback, actions = task_service.create_task_for_recipients(
                user_id=user_id,
                title=transcribed_text[:100],  # Truncate for title
                description=transcribed_text,
                due_time=due_time,
                specific_recipients=None
            )
        
        # Clear state and reset voice processing flag
        await state.clear()
        
        # Reset voice processing flag
        from handlers_modular.message.threading_handler import voice_processing, _message_threads_lock
        with _message_threads_lock:
            voice_processing[user_id] = False
        
        # Send response
        from handlers_modular.base import handle_task_creation_response
        await handle_task_creation_response(callback_query.message, success, feedback, actions)
        
    except Exception as e:
        logger.error(f"Error confirming transcription: {e}", exc_info=True)
        await callback_query.answer("❌ Error processing transcription")
        await state.clear()
        # Show error to user
        await callback_query.message.edit_text(
            f"❌ Error creating task: {str(e)}", 
            disable_web_page_preview=True
        )


@router.callback_query(lambda c: c.data == "transcribe_cancel")
async def cancel_transcription(callback_query: CallbackQuery, state: FSMContext):
    """Handle transcription cancellation."""
    await state.clear()
    
    # Reset voice processing flag
    from handlers_modular.message.threading_handler import voice_processing, _message_threads_lock
    user_id = callback_query.from_user.id
    with _message_threads_lock:
        voice_processing[user_id] = False
    # Add back to menu button after cancellation
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Back to Menu", callback_data="back_to_menu")]
    ])
    
    await callback_query.message.edit_text(
        "❌ Voice message transcription cancelled.", 
        reply_markup=back_keyboard,
        disable_web_page_preview=True
    )
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
            # Get the current task from database to know which recipients were already used
            task_repo = container.task_repository()
            task = task_repo.get_by_id(int(task_id))
            
            # For now, we'll regenerate the actions assuming the current recipient was just added
            # In a more sophisticated implementation, we'd track which recipients were used for this task
            updated_actions = task_service._generate_post_task_actions(
                user_id=user_id, 
                used_recipients=[],  # We don't track used recipients in the current design
                task_id=int(task_id), 
                exclude_recipient_ids=[int(recipient_id)]
            )
            
            # If there are still more recipients available, show the updated keyboard
            if updated_actions and (updated_actions.get("add_actions") or updated_actions.get("remove_actions")):
                from keyboards.recipient import get_post_task_actions_keyboard
                updated_keyboard = get_post_task_actions_keyboard(updated_actions)
            else:
                # No more recipients available, just show "Done" button
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                updated_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Done", callback_data="task_actions_done")]
                ])
            
            # Update message to show success with updated keyboard
            await callback_query.message.edit_text(
                callback_query.message.text + f"\n\n{message}",
                parse_mode='Markdown',
                reply_markup=updated_keyboard,
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
        user_id = callback_query.from_user.id
        
        # Get task service and remove task from recipient
        task_service = container.recipient_task_service()
        success, message = task_service.remove_task_from_recipient(
            user_id=user_id,
            task_id=int(task_id),
            recipient_id=int(recipient_id)
        )
        
        if success:
            # Regenerate buttons with the removed recipient available for adding again
            updated_actions = task_service._generate_post_task_actions(
                user_id=user_id, 
                used_recipients=[],  # We don't track used recipients in the current design
                task_id=int(task_id), 
                exclude_recipient_ids=[]  # Don't exclude any since we just removed one
            )
            
            # If there are still actions available, show the updated keyboard
            if updated_actions and (updated_actions.get("add_actions") or updated_actions.get("remove_actions")):
                from keyboards.recipient import get_post_task_actions_keyboard
                updated_keyboard = get_post_task_actions_keyboard(updated_actions)
            else:
                # No more actions available, just show "Done" button
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                updated_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Done", callback_data="task_actions_done")]
                ])
            
            # Update message to show success with updated keyboard
            await callback_query.message.edit_text(
                callback_query.message.text + f"\n\n{message}",
                parse_mode='Markdown',
                reply_markup=updated_keyboard,
                disable_web_page_preview=True
            )
            await callback_query.answer("✅ Task removed successfully!")
        else:
            await callback_query.answer(f"❌ {message}")
            
    except Exception as e:
        logger.error(f"Error removing task from recipient: {e}")
        await callback_query.answer("❌ Error removing task")


@router.callback_query(lambda c: c.data == "task_actions_done")
async def handle_task_actions_done(callback_query: CallbackQuery):
    """Handle completion of post-task actions."""
    # Remove the keyboard
    await callback_query.message.edit_reply_markup(reply_markup=None)
    await callback_query.answer("✅ Done!")