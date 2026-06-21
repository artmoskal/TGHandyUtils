"""Middleware to automatically track users from Telegram updates."""

from typing import Callable, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from composition.container import container
from core.logging import get_logger

logger = get_logger(__name__)


class UserTrackingMiddleware(BaseMiddleware):
    """Middleware to automatically track users."""
    
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any]
    ) -> Any:
        """Process the event and track user if applicable."""
        # Get user from event
        user = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
        elif hasattr(event, 'from_user'):
            user = event.from_user
        
        # Track user if found
        if user:
            try:
                user_service = container.user_service()
                user_service.track_user(user)
                logger.debug(f"Tracked user {user.id} (@{user.username})")
                
                # Debug: Check if this is a command
                if hasattr(event, 'text') and event.text and event.text.startswith('/'):
                    logger.info(f"Command received: {event.text} from user {user.id}")
            except Exception as e:
                logger.error(f"Failed to track user {user.id}: {e}")
        
        # Continue processing
        return await handler(event, data)