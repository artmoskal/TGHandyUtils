"""Clean recipient keyboards - no legacy code."""

from typing import List, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from models.unified_recipient import UnifiedRecipient
from helpers.ui_helpers import get_platform_emoji, get_status_emoji, get_account_type_emoji, get_default_indicator


def get_recipient_management_keyboard(recipients: List[UnifiedRecipient]) -> InlineKeyboardMarkup:
    """Get account management keyboard."""
    keyboard = []
    
    # Show existing accounts first
    if recipients:
        keyboard.append([InlineKeyboardButton(text="📱 Your Connected Accounts:", callback_data="noop")])
        
        for recipient in recipients:
            status = get_status_emoji(recipient.enabled)
            platform_emoji = get_platform_emoji(recipient.platform_type)
            account_type = get_account_type_emoji(recipient.is_personal)
            default_indicator = get_default_indicator(recipient.is_default)
            callback_data = f"recipient_edit_{recipient.id}"
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{status} {account_type} {platform_emoji} {recipient.name}{default_indicator}", 
                    callback_data=callback_data
                )
            ])
        keyboard.append([InlineKeyboardButton(text="━━━━━━━━━━━━━━━━━━", callback_data="noop")])
    
    # Add new account options
    keyboard.extend([
        [InlineKeyboardButton(text="➕ Add Personal Account", callback_data="add_user_platform")],
        [InlineKeyboardButton(text="👥 Add Shared Account", callback_data="add_shared_recipient")],
        [InlineKeyboardButton(text="« Back to Menu", callback_data="back_to_menu")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_platform_selection_keyboard() -> InlineKeyboardMarkup:
    """Get account type selection keyboard."""
    keyboard = [
        [InlineKeyboardButton(text=f"{get_platform_emoji('todoist')} Todoist", callback_data="platform_type_todoist")],
        [InlineKeyboardButton(text=f"{get_platform_emoji('trello')} Trello", callback_data="platform_type_trello")],
        [InlineKeyboardButton(text=f"{get_platform_emoji('google_calendar')} Google Calendar", callback_data="platform_type_google_calendar")],
        [InlineKeyboardButton(text="« Back", callback_data="back_to_recipients")]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_recipient_selection_keyboard(recipients: List[UnifiedRecipient], selected_recipients: List[str] = None) -> InlineKeyboardMarkup:
    """Get recipient selection keyboard for task creation."""
    keyboard = []
    selected_recipients = selected_recipients or []
    
    # Add recipient selection buttons
    if recipients:
        keyboard.append([InlineKeyboardButton(text="🎯 Select Recipients:", callback_data="noop")])
        
        # Create rows of recipient buttons (2 per row)
        recipient_buttons = []
        for recipient in recipients:
            # Show selection status with checkmarks
            if recipient.id in selected_recipients:
                status = "☑️"  # Selected
            else:
                status = "☐"  # Not selected
            
            recipient_buttons.append(
                InlineKeyboardButton(
                    text=f"{status} {recipient.name}",
                    callback_data=f"select_recipient_{recipient.id}"
                )
            )
        
        # Split into rows of 2
        for i in range(0, len(recipient_buttons), 2):
            row = recipient_buttons[i:i+2]
            keyboard.append(row)
    
    # Action buttons
    keyboard.append([
        InlineKeyboardButton(text="✅ Create Task", callback_data="confirm_recipients"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_task")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_recipient_edit_keyboard(recipient_id: int, platform_type: str = None, is_default: bool = False) -> InlineKeyboardMarkup:
    """Get account edit keyboard."""
    # Default toggle button
    default_text = "⚪ Remove from Default" if is_default else "⭐ Set as Default"
    keyboard = [
        [InlineKeyboardButton(text=default_text, callback_data=f"toggle_default_{str(recipient_id)}")],
        [InlineKeyboardButton(text="🔄 Enable/Disable Account", callback_data=f"toggle_recipient_{str(recipient_id)}")],
    ]
    
    # Add configure button for platforms that need configuration (like Trello)
    if platform_type == "trello":
        keyboard.append([InlineKeyboardButton(text="⚙️ Configure Trello Board", callback_data=f"configure_recipient_{str(recipient_id)}")])
    
    keyboard.extend([
        [InlineKeyboardButton(text="🗑️ Delete Account", callback_data=f"recipient_remove_{str(recipient_id)}")],
        [InlineKeyboardButton(text="« Back to Accounts", callback_data="back_to_recipients")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_recipient_settings_keyboard() -> InlineKeyboardMarkup:
    """Get recipient settings keyboard."""
    keyboard = [
        [InlineKeyboardButton(text="🔄 Toggle UI", callback_data="toggle_recipient_ui")],
        [InlineKeyboardButton(text="« Back", callback_data="back_to_recipients")]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_trello_configuration_keyboard() -> InlineKeyboardMarkup:
    """Get Trello configuration keyboard with back navigation."""
    keyboard = [
        [InlineKeyboardButton(text="« Back to Settings", callback_data="back_to_recipients")]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_transcription_keyboard() -> InlineKeyboardMarkup:
    """Get the keyboard for confirming or cancelling transcription."""
    keyboard = [
        [
            InlineKeyboardButton(text="✅ Confirm", callback_data="transcribe_confirm"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="transcribe_cancel")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Get the main menu keyboard."""
    keyboard = [
        [InlineKeyboardButton(text="📝 Create Task", callback_data="create_task")],
        [InlineKeyboardButton(text="📱 My Accounts", callback_data="show_recipients")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="show_settings")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_settings_main_keyboard() -> InlineKeyboardMarkup:
    """Get the main settings keyboard."""
    keyboard = [
        [InlineKeyboardButton(text="👤 Profile Settings", callback_data="profile_settings")],
        [InlineKeyboardButton(text="📱 Manage Accounts", callback_data="show_recipients")],
        [InlineKeyboardButton(text="🔔 Notifications", callback_data="notification_settings")],
        [InlineKeyboardButton(text="🗑️ Delete All Data", callback_data="confirm_delete_data")],
        [InlineKeyboardButton(text="« Back to Menu", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_profile_settings_keyboard() -> InlineKeyboardMarkup:
    """Get profile settings keyboard."""
    keyboard = [
        [InlineKeyboardButton(text="👤 Update Name", callback_data="update_owner_name")],
        [InlineKeyboardButton(text="🌍 Update Location", callback_data="update_location")],
        [InlineKeyboardButton(text="« Back to Settings", callback_data="back_to_settings")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_notification_settings_keyboard() -> InlineKeyboardMarkup:
    """Get notification settings keyboard."""
    keyboard = [
        [InlineKeyboardButton(text="🔄 Toggle Telegram Notifications", callback_data="toggle_telegram_notifications")],
        [InlineKeyboardButton(text="🔄 Toggle Recipient UI", callback_data="toggle_recipient_ui")],
        [InlineKeyboardButton(text="« Back to Settings", callback_data="back_to_settings")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_delete_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Get data deletion confirmation keyboard."""
    keyboard = [
        [
            InlineKeyboardButton(text="🗑️ DELETE ALL DATA", callback_data="delete_all_data_confirmed"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_settings")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_back_to_settings_keyboard() -> InlineKeyboardMarkup:
    """Get simple back to settings keyboard."""
    keyboard = [
        [InlineKeyboardButton(text="« Back to Settings", callback_data="back_to_settings")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_post_task_actions_keyboard(actions: Dict[str, List[Dict[str, str]]]) -> InlineKeyboardMarkup:
    """Get keyboard with post-task action buttons."""
    keyboard = []
    
    # Add remove buttons (up to 3)
    if actions.get("remove_actions"):
        for action in actions["remove_actions"][:3]:
            keyboard.append([
                InlineKeyboardButton(
                    text=action["text"], 
                    callback_data=action["callback_data"]
                )
            ])
    
    # Add a separator if we have both types
    if actions.get("remove_actions") and actions.get("add_actions"):
        keyboard.append([InlineKeyboardButton(text="➖➖➖➖➖➖➖➖", callback_data="noop")])
    
    # Add add buttons (up to 6 to accommodate more recipients)
    if actions.get("add_actions"):
        for action in actions["add_actions"][:6]:
            keyboard.append([
                InlineKeyboardButton(
                    text=action["text"],
                    callback_data=action["callback_data"]
                )
            ])
    
    # Add done button
    keyboard.append([InlineKeyboardButton(text="✅ Done", callback_data="task_actions_done")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)