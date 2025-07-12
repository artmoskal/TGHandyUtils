"""Message template helpers for consistent formatting across the application."""

from typing import List, Optional
from models.unified_recipient import UnifiedRecipient
from helpers.ui_helpers import get_platform_emoji, escape_markdown


def format_task_success_message(task_title: str, task_description: str, due_time: str, 
                               recipients: List[str], task_urls: List[str], 
                               failed_recipients: List[str] = None) -> str:
    """Format task creation success message with consistent styling."""
    message_parts = ["✅ **Task Created Successfully!**\n"]
    
    # Add task details with enhanced formatting
    escaped_title = escape_markdown(task_title)
    message_parts.append(f"📋 **\"{escaped_title}\"**")
    
    if task_description and task_description.strip():
        # Truncate long descriptions
        desc_preview = task_description[:150] + "..." if len(task_description) > 150 else task_description
        escaped_desc = escape_markdown(desc_preview)
        message_parts.append(f"📄 **Description:** {escaped_desc}")
    
    message_parts.append("")  # Add blank line for spacing
    
    # Add due time
    message_parts.append(f"📅 **Due:** {due_time}")
    message_parts.append("")  # Add blank line for spacing
    
    # Add successful recipients
    if recipients:
        message_parts.append("🎯 **Added to:**")
        for i, recipient_name in enumerate(recipients):
            if i < len(task_urls):
                message_parts.append(f"• {recipient_name}")
        message_parts.append("")
    
    # Add failed recipients if any
    if failed_recipients:
        message_parts.append(f"❌ **Failed:** {', '.join(failed_recipients)}")
    
    return "\n".join(message_parts)


def format_recipient_selection_message(task_title: str, recipient_count: int = 0) -> str:
    """Format recipient selection message."""
    escaped_title = escape_markdown(task_title)
    
    message_parts = [
        "🎯 **Choose Platforms**\n",
        f"Task: **\"{escaped_title}\"**\n"
    ]
    
    if recipient_count == 0:
        message_parts.append("No default platforms configured. Select where to create this task:")
    else:
        message_parts.append(f"Selected: {recipient_count} recipients\n")
        message_parts.append("Choose recipients for your task:")
    
    return "\n".join(message_parts)


def format_account_management_message(recipient_count: int) -> str:
    """Format account management message."""
    message_parts = ["📱 **Account Management**\n"]
    
    if recipient_count == 0:
        message_parts.append("No recipients configured yet.\n")
        message_parts.append("Add your first recipient to get started:")
    else:
        message_parts.append(f"You have {recipient_count} recipients configured.\n")
        message_parts.append("Choose an option:")
    
    return "\n".join(message_parts)


def format_platform_error_message(platform_type: str, error_message: str, is_retryable: bool = True) -> str:
    """Format platform-specific error message."""
    platform_emoji = get_platform_emoji(platform_type)
    platform_display = platform_type.title().replace('_', ' ')
    
    if is_retryable:
        return f"🔴 {platform_emoji} {platform_display} temporary error: {error_message}\n\nYou can retry this operation."
    else:
        return f"🔴 {platform_emoji} {platform_display} configuration error: {error_message}\n\nPlease check your settings."


def format_ui_disabled_message() -> str:
    """Format message shown when recipient UI is disabled."""
    return (
        "🚫 **Cannot Create Task**\n\n"
        "**Problem:** Automatic mode is enabled but no default platforms are configured.\n\n"
        "**Solution (choose one):**\n"
        "1️⃣ Set a default platform: Settings → Manage Accounts → Edit Account → Set as Default\n"
        "2️⃣ Enable manual selection: Settings → Notifications → Toggle Recipient UI\n\n"
        "💡 **Tip:** Default platforms create tasks automatically without asking where to send them."
    )


def format_no_recipients_message() -> str:
    """Format message shown when no recipients are configured."""
    return (
        "❌ **No Recipients Configured**\n\n"
        "Please add at least one account first to create tasks.\n\n"
        "Go to Settings → Manage Accounts to get started."
    )


def format_platform_addition_success(platform_type: str, recipient_name: str) -> str:
    """Format success message for adding task to platform."""
    platform_emoji = get_platform_emoji(platform_type)
    platform_display = platform_type.title().replace('_', ' ')
    
    return (
        f"{platform_emoji} **Added to {recipient_name}**\n\n"
        f"Task successfully created on your {platform_display} account."
    )


def format_platform_removal_success(platform_type: str, recipient_name: str) -> str:
    """Format success message for removing task from platform."""
    platform_emoji = get_platform_emoji(platform_type)
    platform_display = platform_type.title().replace('_', ' ')
    
    return (
        f"{platform_emoji} **Removed from {recipient_name}**\n\n"
        f"Task successfully deleted from your {platform_display} account."
    )


def format_setup_complete_message(platform_type: str) -> str:
    """Format message for successful platform setup."""
    platform_emoji = get_platform_emoji(platform_type)
    platform_display = platform_type.title().replace('_', ' ')
    
    return (
        f"✅ **{platform_display} Account Added Successfully!**\n\n"
        f"{platform_emoji} Your account is now connected and ready to use.\n\n"
        "🎯 You can now create tasks that will appear on your connected platform."
    )