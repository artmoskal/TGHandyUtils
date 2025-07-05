"""Notification settings callback handlers."""

from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot import router
from keyboards.recipient import get_notification_settings_keyboard, get_delete_confirmation_keyboard
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)


@router.callback_query(lambda c: c.data == "recipient_settings")
async def recipient_settings(callback_query: CallbackQuery, state: FSMContext):
    """Show recipient settings."""
    user_id = callback_query.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        ui_enabled = recipient_service.is_recipient_ui_enabled(user_id)
        notifications_enabled = recipient_service.are_telegram_notifications_enabled(user_id)
        
        ui_status = "Enabled" if ui_enabled else "Disabled"
        ui_toggle_text = "Disable" if ui_enabled else "Enable"
        
        notifications_status = "Enabled" if notifications_enabled else "Disabled"
        notifications_toggle_text = "Disable" if notifications_enabled else "Enable"
        
        keyboard = [
            [{"text": f"{ui_toggle_text} Recipient Selection UI", "callback_data": "toggle_recipient_ui"}],
            [{"text": f"{notifications_toggle_text} Telegram Notifications", "callback_data": "toggle_telegram_notifications"}],
            [{"text": "« Back to Recipients", "callback_data": "back_to_recipients"}]
        ]
        
        await callback_query.message.edit_text(
            f"⚙️ *Recipient Settings*\n\n"
            f"Recipient Selection UI: {ui_status}\n"
            f"Telegram Notifications: {notifications_status}\n\n"
            "• Recipient UI: When enabled, you'll be asked to choose recipients for each task.\n"
            "• Notifications: When enabled, you'll receive telegram reminders for due tasks.",
            reply_markup={"inline_keyboard": keyboard},
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Failed to load recipient settings for user {user_id}: {e}")
        await callback_query.answer("Error loading settings.")


@router.callback_query(lambda c: c.data and c.data == "toggle_recipient_ui")
async def toggle_recipient_ui(callback_query: CallbackQuery, state: FSMContext):
    """Toggle recipient selection UI."""
    user_id = callback_query.from_user.id
    logger.info(f"Toggle recipient UI called for user {user_id}")
    
    try:
        recipient_service = container.recipient_service()
        logger.debug(f"Got recipient service for user {user_id}")
        
        current_status = recipient_service.is_recipient_ui_enabled(user_id)
        logger.debug(f"Current UI status for user {user_id}: {current_status}")
        
        new_status = not current_status
        logger.debug(f"Toggling UI status for user {user_id}: {current_status} -> {new_status}")
        
        success = recipient_service.enable_recipient_ui(user_id, new_status)
        logger.debug(f"Enable recipient UI result for user {user_id}: success={success}")
        
        if success:
            status_text = "enabled" if new_status else "disabled"
            logger.info(f"Successfully toggled recipient UI for user {user_id}: {status_text}")
            await callback_query.answer(f"✅ Recipient UI {status_text}!")
            
            # Refresh settings display
            await recipient_settings(callback_query, state)
        else:
            logger.error(f"Failed to enable recipient UI for user {user_id}: success=False")
            await callback_query.answer("❌ Failed to update setting.")
            
    except Exception as e:
        logger.error(f"Exception in toggle recipient UI for user {user_id}: {e}", exc_info=True)
        await callback_query.answer("Error updating setting.")


@router.callback_query(lambda c: c.data == "toggle_telegram_notifications")
async def toggle_telegram_notifications(callback_query: CallbackQuery, state: FSMContext):
    """Toggle telegram notifications."""
    user_id = callback_query.from_user.id
    logger.error(f"🔍 TELEGRAM NOTIFICATIONS TOGGLE called for user {user_id}")
    
    try:
        recipient_service = container.recipient_service()
        current_status = recipient_service.are_telegram_notifications_enabled(user_id)
        new_status = not current_status
        
        success = recipient_service.set_telegram_notifications(user_id, new_status)
        
        if success:
            status_text = "enabled" if new_status else "disabled"
            await callback_query.answer(f"✅ Telegram notifications {status_text}!")
            
            # Refresh settings display
            await recipient_settings(callback_query, state)
        else:
            await callback_query.answer("❌ Failed to update notification setting.")
            
    except Exception as e:
        logger.error(f"Failed to toggle telegram notifications for user {user_id}: {e}")
        await callback_query.answer("Error updating notification setting.")


@router.callback_query(lambda c: c.data == "notification_settings")
async def notification_settings_callback(callback_query: CallbackQuery, state: FSMContext):
    """Show notification settings."""
    user_id = callback_query.from_user.id
    logger.error(f"🔍 NOTIFICATION SETTINGS called for user {user_id}")
    
    try:
        recipient_service = container.recipient_service()
        prefs = recipient_service.get_user_preferences(user_id)
        
        notifications = "Enabled" if prefs and prefs.telegram_notifications else "Disabled"
        recipient_ui = "Enabled" if prefs and prefs.show_recipient_ui else "Disabled"
        
        text = f"🔔 *Notification Settings*\n\n"
        text += f"Telegram Notifications: {notifications}\n"
        text += f"Recipient Selection UI: {recipient_ui}\n\n"
        text += "• Notifications: Get Telegram reminders for due tasks\n"
        text += "• Recipient UI: Choose recipients for each task"
        
        keyboard = get_notification_settings_keyboard()
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Failed to show notification settings for user {user_id}: {e}")
        await callback_query.answer("Error loading notification settings")


@router.callback_query(lambda c: c.data == "confirm_delete_data")
async def confirm_delete_data_callback(callback_query: CallbackQuery, state: FSMContext):
    """Show data deletion confirmation."""
    keyboard = get_delete_confirmation_keyboard()
    await callback_query.message.edit_text(
        "⚠️ *FINAL CONFIRMATION*\n\n"
        "🚨 *THIS WILL PERMANENTLY DELETE:*\n"
        "• All connected accounts (Todoist, Trello)\n"
        "• All shared recipients\n"
        "• All preferences and settings\n"
        "• All task history associations\n\n"
        "*THIS CANNOT BE UNDONE!*\n\n"
        "Are you absolutely sure?",
        reply_markup=keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await callback_query.answer()


@router.callback_query(lambda c: c.data == "delete_all_data_confirmed")
async def delete_all_data_confirmed(callback_query: CallbackQuery, state: FSMContext):
    """Execute data deletion."""
    user_id = callback_query.from_user.id
    
    try:
        recipient_service = container.recipient_service()
        success = recipient_service.delete_all_user_data(user_id)
        
        if success:
            # Add navigation after successful deletion
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            start_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Start Fresh", callback_data="back_to_menu")]
            ])
            
            await callback_query.message.edit_text(
                "✅ *All Data Deleted*\n\n"
                "Your data has been permanently removed.\n"
                "You can start fresh by using /start",
                parse_mode='Markdown',
                reply_markup=start_keyboard,
                disable_web_page_preview=True
            )
        else:
            # Add retry/back buttons for deletion error
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            error_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Try Again", callback_data="confirm_delete_data")],
                [InlineKeyboardButton(text="🏠 Back to Menu", callback_data="back_to_menu")]
            ])
            
            await callback_query.message.edit_text(
                "❌ *Deletion Failed*\n\n"
                "There was an error deleting your data.\n"
                "Please try again or contact support.",
                parse_mode='Markdown',
                reply_markup=error_keyboard,
                disable_web_page_preview=True
            )
        
        await callback_query.answer()
        await state.clear()
        
    except Exception as e:
        logger.error(f"Failed to delete data for user {user_id}: {e}")
        await callback_query.answer("Error deleting data")