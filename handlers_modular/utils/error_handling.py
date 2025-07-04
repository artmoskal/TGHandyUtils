"""Error handling utilities for handlers."""

import functools
from typing import Callable, Any
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from core.logging import get_logger
from .responses import MessageResponses, CallbackResponses

logger = get_logger(__name__)


def handle_message_errors(error_message: str = "An error occurred. Please try again."):
    """Decorator for handling errors in message handlers."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(message: Message, state: FSMContext, *args, **kwargs):
            try:
                return await func(message, state, *args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {func.__name__} for user {message.from_user.id}: {e}", exc_info=True)
                await MessageResponses.error_reply(message, error_message)
                # Clear state on error to prevent stuck states
                await state.clear()
        return wrapper
    return decorator


def handle_callback_errors(error_message: str = "An error occurred. Please try again."):
    """Decorator for handling errors in callback handlers."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(callback_query: CallbackQuery, state: FSMContext = None, *args, **kwargs):
            try:
                return await func(callback_query, state, *args, **kwargs) if state else await func(callback_query, *args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {func.__name__} for user {callback_query.from_user.id}: {e}", exc_info=True)
                await CallbackResponses.error_edit(callback_query, error_message)
                # Clear state on error if state is provided
                if state:
                    await state.clear()
        return wrapper
    return decorator


def handle_service_errors(service_name: str = "service"):
    """Decorator for handling service-related errors."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {service_name} operation {func.__name__}: {e}", exc_info=True)
                raise ServiceError(f"{service_name.title()} operation failed: {str(e)}")
        return wrapper
    return decorator


class ServiceError(Exception):
    """Custom exception for service errors."""
    pass


class ErrorHandler:
    """Centralized error handling utilities."""
    
    @staticmethod
    async def handle_validation_error(message: Message, field_name: str):
        """Handle validation errors for message inputs."""
        await MessageResponses.validation_error(message, field_name)
    
    @staticmethod
    async def handle_service_error(message: Message, operation: str):
        """Handle service-related errors."""
        await MessageResponses.error_reply(
            message, 
            f"Error {operation}. Please try again."
        )
    
    @staticmethod
    async def handle_callback_service_error(callback_query: CallbackQuery, operation: str):
        """Handle service-related errors in callbacks."""
        await CallbackResponses.error_edit(
            callback_query,
            f"Error {operation}. Please try again."
        )
    
    @staticmethod
    async def handle_no_recipients_error(message: Message):
        """Handle no recipients configured error."""
        from .responses import FormattedResponses
        await message.reply(
            FormattedResponses.no_recipients_configured(),
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    
    @staticmethod
    async def handle_callback_no_recipients_error(callback_query: CallbackQuery):
        """Handle no recipients configured error in callbacks."""
        from .responses import FormattedResponses
        await CallbackResponses.error_edit(
            callback_query,
            FormattedResponses.no_recipients_configured()
        )
    
    @staticmethod
    def log_user_action(func_name: str, user_id: int, action: str):
        """Log user actions for debugging."""
        logger.info(f"{func_name}: User {user_id} - {action}")
    
    @staticmethod
    def log_error(func_name: str, user_id: int, error: Exception):
        """Log errors with user context."""
        logger.error(f"{func_name}: User {user_id} - Error: {error}", exc_info=True)


# Convenience decorators with specific error messages
message_handler_errors = handle_message_errors("❌ Error processing your message. Please try again.")
callback_handler_errors = handle_callback_errors("❌ Error processing your request. Please try again.")
state_handler_errors = handle_message_errors("❌ Error processing your input. Please try again.")
command_handler_errors = handle_message_errors("❌ Error executing command. Please try again.")
settings_handler_errors = handle_message_errors("❌ Error updating settings. Please try again.")
recipient_handler_errors = handle_callback_errors("❌ Error managing recipients. Please try again.")
task_handler_errors = handle_message_errors("❌ Error creating task. Please try again.")