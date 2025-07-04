"""Navigation menu callback handlers."""

from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot import router
from keyboards.recipient import (
    get_main_menu_keyboard,
    get_settings_main_keyboard,
    get_recipient_management_keyboard,
    get_recipient_selection_keyboard
)
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)


@router.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback_query: CallbackQuery, state: FSMContext):
    """Go back to main menu."""
    keyboard = get_main_menu_keyboard()
    await callback_query.message.edit_text(
        "🎯 *Main Menu*\n\nChoose what you'd like to do:",
        reply_markup=keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await callback_query.answer()


@router.callback_query(lambda c: c.data == "back_to_settings")
async def back_to_settings_callback(callback_query: CallbackQuery, state: FSMContext):
    """Go back to settings menu."""
    await show_settings_callback(callback_query, state)


@router.callback_query(lambda c: c.data == "show_settings")
async def show_settings_callback(callback_query: CallbackQuery, state: FSMContext):
    """Show settings from menu."""
    user_id = callback_query.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        prefs = recipient_service.get_user_preferences(user_id)
        
        # Build settings display
        owner_name = prefs.owner_name if prefs and prefs.owner_name else "Not set"
        location = prefs.location if prefs and prefs.location else "Not set"
        notifications = "Enabled" if prefs and prefs.telegram_notifications else "Disabled"
        recipient_ui = "Enabled" if prefs and prefs.show_recipient_ui else "Disabled"
        
        text = f"⚙️ *Your Settings*\n\n"
        text += f"👤 *Name:* {owner_name}\n"
        text += f"🌍 *Location:* {location}\n"
        text += f"🔔 *Notifications:* {notifications}\n"
        text += f"🎯 *Recipient UI:* {recipient_ui}\n\n"
        text += "Select an option to update:"
        
        keyboard = get_settings_main_keyboard()
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Failed to show settings for user {user_id}: {e}")
        await callback_query.answer("Error loading settings")


@router.callback_query(lambda c: c.data == "show_recipients")
async def show_recipients_callback(callback_query: CallbackQuery, state: FSMContext):
    """Show recipients management."""
    user_id = callback_query.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        recipients = recipient_service.get_recipients_by_user(user_id)
        
        if not recipients:
            keyboard = get_recipient_management_keyboard([])
            await callback_query.message.edit_text(
                "🎯 *Recipients Management*\n\n"
                "No recipients configured yet.\n\n"
                "Add your first recipient to get started:",
                reply_markup=keyboard,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        else:
            keyboard = get_recipient_management_keyboard(recipients)
            text = f"🎯 *Recipients Management*\n\n"
            text += f"You have {len(recipients)} recipients configured.\n\n"
            text += "Choose an option:"
            
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
            
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Error showing recipients for user {user_id}: {e}")
        await callback_query.answer("❌ Error loading recipients")


@router.callback_query(lambda c: c.data == "create_task")
async def create_task_callback(callback_query: CallbackQuery, state: FSMContext):
    """Show task creation interface."""
    user_id = callback_query.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        recipients = recipient_service.get_enabled_recipients(user_id)
        
        if not recipients:
            await callback_query.message.edit_text(
                "❌ *No Recipients Available*\n\n"
                "You need to add and enable at least one recipient first.\n\n"
                "Use the Recipients menu to add your accounts.",
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            await callback_query.answer()
            return
        
        # Check if recipient UI is enabled
        prefs = recipient_service.get_user_preferences(user_id)
        if prefs and prefs.show_recipient_ui:
            # Show recipient selection UI
            keyboard = get_recipient_selection_keyboard(recipients, [])
            await callback_query.message.edit_text(
                "🎯 *Create Task*\n\n"
                "Choose recipients for your task:\n"
                "Selected: 0 recipients",
                reply_markup=keyboard,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            await state.update_data(selected_recipients=[])
        else:
            # Skip recipient selection, use all enabled recipients
            await callback_query.message.edit_text(
                "📝 *Create Task*\n\n"
                f"Task will be sent to all {len(recipients)} enabled recipients.\n\n"
                "Enter your task description:",
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            # Set state to waiting for task input
            from states.recipient_states import RecipientState
            await state.set_state(RecipientState.waiting_for_task)
            await state.update_data(selected_recipients=[str(r.id) for r in recipients])
        
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Error creating task interface for user {user_id}: {e}")
        await callback_query.answer("❌ Error loading task creation")