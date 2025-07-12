"""Helper utilities for consistent UI, error handling, and message formatting."""

from .ui_helpers import (
    get_platform_emoji,
    get_status_emoji, 
    get_account_type_emoji,
    get_default_indicator,
    create_back_button,
    escape_markdown,
    format_platform_button
)

from .error_helpers import (
    PlatformError,
    PlatformTimeoutError,
    PlatformConnectionError,
    PlatformAuthError,
    PlatformConfigError,
    with_timeout_and_retry,
    handle_platform_error,
    create_retry_keyboard,
    format_graceful_degradation_message
)

from .message_templates import (
    format_task_success_message,
    format_recipient_selection_message,
    format_account_management_message,
    format_platform_error_message,
    format_ui_disabled_message,
    format_no_recipients_message,
    format_platform_addition_success,
    format_platform_removal_success,
    format_setup_complete_message
)

__all__ = [
    # UI helpers
    'get_platform_emoji',
    'get_status_emoji', 
    'get_account_type_emoji',
    'get_default_indicator',
    'create_back_button',
    'escape_markdown',
    'format_platform_button',
    
    # Error helpers
    'PlatformError',
    'PlatformTimeoutError',
    'PlatformConnectionError',
    'PlatformAuthError',
    'PlatformConfigError',
    'with_timeout_and_retry',
    'handle_platform_error',
    'create_retry_keyboard',
    'format_graceful_degradation_message',
    
    # Message templates
    'format_task_success_message',
    'format_recipient_selection_message',
    'format_account_management_message',
    'format_platform_error_message',
    'format_ui_disabled_message',
    'format_no_recipients_message',
    'format_platform_addition_success',
    'format_platform_removal_success',
    'format_setup_complete_message'
]