"""Error handling helpers for platform operations."""

import time
import logging
from typing import Callable, Tuple, Optional, Any
from functools import wraps
import requests
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from helpers.ui_helpers import escape_markdown
from core.interfaces import ServiceResult

logger = logging.getLogger(__name__)


class PlatformError(Exception):
    """Base exception for platform-related errors."""
    def __init__(self, platform: str, message: str, retryable: bool = True):
        self.platform = platform
        self.message = message
        self.retryable = retryable
        super().__init__(f"{platform}: {message}")


class PlatformTimeoutError(PlatformError):
    """Exception for platform API timeouts."""
    def __init__(self, platform: str):
        super().__init__(platform, "Request timed out", retryable=True)


class PlatformConnectionError(PlatformError):
    """Exception for platform connection errors."""
    def __init__(self, platform: str, message: str = "Connection failed"):
        super().__init__(platform, message, retryable=True)


class PlatformAuthError(PlatformError):
    """Exception for platform authentication errors."""
    def __init__(self, platform: str, message: str = "Authentication failed"):
        super().__init__(platform, message, retryable=False)


class PlatformConfigError(PlatformError):
    """Exception for platform configuration errors."""
    def __init__(self, platform: str, message: str = "Configuration error"):
        super().__init__(platform, message, retryable=False)


def with_timeout_and_retry(max_retries: int = 3, backoff_factor: float = 2.0):
    """Decorator to add retry logic to platform methods. Uses 30s timeout for all REST calls."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            platform_name = getattr(args[0], '__class__', type(args[0])).__name__ if args else "Unknown"
            
            for attempt in range(max_retries):
                try:
                    logger.debug(f"Attempt {attempt + 1}/{max_retries} for {platform_name}.{func.__name__}")
                    result = func(*args, **kwargs)
                    
                    if attempt > 0:
                        logger.info(f"{platform_name}.{func.__name__} succeeded after {attempt + 1} attempts")
                    
                    return result
                    
                except requests.exceptions.Timeout:
                    error = PlatformTimeoutError(platform_name)
                    logger.warning(f"Timeout on attempt {attempt + 1}/{max_retries}: {error}")
                    if attempt == max_retries - 1:
                        raise error
                        
                except requests.exceptions.ConnectionError as e:
                    error = PlatformConnectionError(platform_name, str(e))
                    logger.warning(f"Connection error on attempt {attempt + 1}/{max_retries}: {error}")
                    if attempt == max_retries - 1:
                        raise error
                        
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code in [401, 403]:
                        # Don't retry auth errors
                        raise PlatformAuthError(platform_name, f"HTTP {e.response.status_code}: {e.response.text}")
                    elif e.response.status_code in [400, 404, 410, 422]:
                        # Don't retry client errors (410 Gone = deprecated API endpoint)
                        raise PlatformConfigError(platform_name, f"HTTP {e.response.status_code}: {e.response.text}")
                    else:
                        # Retry server errors
                        logger.warning(f"HTTP error on attempt {attempt + 1}/{max_retries}: {e}")
                        if attempt == max_retries - 1:
                            raise PlatformError(platform_name, f"HTTP {e.response.status_code}: {e.response.text}")
                            
                except Exception as e:
                    # Handle other exceptions
                    logger.warning(f"Unexpected error on attempt {attempt + 1}/{max_retries}: {e}")
                    if attempt == max_retries - 1:
                        raise PlatformError(platform_name, str(e))
                
                # Exponential backoff before retry
                if attempt < max_retries - 1:
                    sleep_time = backoff_factor ** attempt
                    logger.debug(f"Waiting {sleep_time:.1f}s before retry...")
                    time.sleep(sleep_time)
            
            return None
        return wrapper
    return decorator


def handle_platform_error(platform: str, error: Exception) -> ServiceResult:
    """
    Handle platform errors and return user-friendly messages.
    
    Args:
        platform: Platform name (todoist, trello, etc.)
        error: The exception that occurred
        
    Returns:
        ServiceResult with success=False and appropriate message
        data field contains 'retryable' boolean
    """
    platform_emoji_map = {
        "todoist": "📋",
        "trello": "🏗️", 
        "google_calendar": "📅"
    }
    
    platform_display = escape_markdown(platform.title().replace('_', ' '))
    emoji = platform_emoji_map.get(platform, "📱")
    
    if isinstance(error, PlatformTimeoutError):
        return ServiceResult(False, f"🔴 {platform_display} is temporarily unavailable. Please try again in a moment.", {"retryable": True})
        
    elif isinstance(error, PlatformConnectionError):
        return ServiceResult(False, f"🔴 {platform_display} connection failed. Please check your internet connection.", {"retryable": True})
        
    elif isinstance(error, PlatformAuthError):
        return ServiceResult(False, f"🔴 {platform_display} authorization expired. Please re-connect your account in Settings.", {"retryable": False})
        
    elif isinstance(error, PlatformConfigError):
        if platform == "trello":
            return ServiceResult(False, f"🔴 {platform_display} configuration error. Please check your board permissions in Settings.", {"retryable": False})
        else:
            return ServiceResult(False, f"🔴 {platform_display} configuration error. Please check your account settings.", {"retryable": False})
            
    elif isinstance(error, PlatformError):
        if error.retryable:
            return ServiceResult(False, f"🔴 {platform_display} temporary error: {error.message}", {"retryable": True})
        else:
            return ServiceResult(False, f"🔴 {platform_display} error: {error.message}", {"retryable": False})
    
    else:
        # Generic error
        return ServiceResult(False, f"🔴 {platform_display} unexpected error. Please try again.", {"retryable": True})


def create_retry_keyboard(original_callback: str, platform: str) -> InlineKeyboardMarkup:
    """Create keyboard with retry option for failed platform operations."""
    platform_emoji_map = {
        "todoist": "📋",
        "trello": "🏗️", 
        "google_calendar": "📅"
    }
    
    emoji = platform_emoji_map.get(platform, "📱")
    
    keyboard = [
        [InlineKeyboardButton(text=f"🔄 Retry {emoji} {escape_markdown(platform.title())}", callback_data=f"retry_{original_callback}")],
        [InlineKeyboardButton(text="🏠 Continue Anyway", callback_data="task_actions_done")]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def format_graceful_degradation_message(title: str, successful_platforms: list, failed_platforms: list) -> str:
    """Format message for graceful degradation when some platforms fail."""
    message_parts = ["✅ **Task Created Successfully!**\n"]
    
    if title:
        from helpers.ui_helpers import escape_markdown
        escaped_title = escape_markdown(title)
        message_parts.append(f"📋 **\"{escaped_title}\"**\n")
    
    if successful_platforms:
        message_parts.append("🎯 **Successfully added to:**")
        for platform_name in successful_platforms:
            from helpers.ui_helpers import get_platform_emoji
            emoji = get_platform_emoji(platform_name.lower())
            message_parts.append(f"• {emoji} {platform_name}")
        message_parts.append("")
    
    if failed_platforms:
        message_parts.append("⚠️ **Some platforms failed:**")
        for platform_name in failed_platforms:
            from helpers.ui_helpers import get_platform_emoji
            emoji = get_platform_emoji(platform_name.lower())
            message_parts.append(f"• {emoji} {platform_name} - Will retry automatically")
        message_parts.append("")
        message_parts.append("💾 **Task saved locally** - You can manually add to failed platforms later.")
    
    return "\n".join(message_parts)