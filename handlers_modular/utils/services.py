"""Service access utilities for handlers."""

from typing import Optional
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)


class ServiceFactory:
    """Centralized service access with error handling."""
    
    @staticmethod
    def get_recipient_service():
        """Get recipient service from container."""
        try:
            return container.recipient_service()
        except Exception as e:
            logger.error(f"Failed to get recipient service: {e}")
            raise
    
    @staticmethod
    def get_task_service():
        """Get task service from container."""
        try:
            return container.recipient_task_service()
        except Exception as e:
            logger.error(f"Failed to get task service: {e}")
            raise
    
    @staticmethod
    def get_parsing_service():
        """Get parsing service from container."""
        try:
            from core.initialization import services
            return services.get_parsing_service()
        except Exception as e:
            logger.error(f"Failed to get parsing service: {e}")
            raise


class UserHelper:
    """User-related helper functions."""
    
    @staticmethod
    def get_user_preferences(user_id: int):
        """Get user preferences with error handling."""
        try:
            recipient_service = ServiceFactory.get_recipient_service()
            return recipient_service.get_user_preferences(user_id)
        except Exception as e:
            logger.error(f"Failed to get user preferences for {user_id}: {e}")
            return None
    
    @staticmethod
    def get_enabled_recipients(user_id: int):
        """Get enabled recipients for user."""
        try:
            recipient_service = ServiceFactory.get_recipient_service()
            return recipient_service.get_enabled_recipients(user_id)
        except Exception as e:
            logger.error(f"Failed to get enabled recipients for {user_id}: {e}")
            return []
    
    @staticmethod
    def get_all_recipients(user_id: int):
        """Get all recipients for user."""
        try:
            recipient_service = ServiceFactory.get_recipient_service()
            return recipient_service.get_recipients_by_user(user_id)
        except Exception as e:
            logger.error(f"Failed to get recipients for {user_id}: {e}")
            return []
    
    @staticmethod
    def format_user_settings(user_id: int) -> dict:
        """Get formatted user settings for display."""
        prefs = UserHelper.get_user_preferences(user_id)
        
        return {
            'owner_name': prefs.owner_name if prefs and prefs.owner_name else "Not set",
            'location': prefs.location if prefs and prefs.location else "Not set", 
            'notifications': "Enabled" if prefs and prefs.telegram_notifications else "Disabled",
            'recipient_ui': "Enabled" if prefs and prefs.show_recipient_ui else "Disabled"
        }


class RecipientHelper:
    """Recipient-related helper functions."""
    
    @staticmethod
    def get_recipient_by_id(user_id: int, recipient_id: int):
        """Get specific recipient by ID."""
        try:
            recipient_service = ServiceFactory.get_recipient_service()
            return recipient_service.get_recipient_by_id(user_id, recipient_id)
        except Exception as e:
            logger.error(f"Failed to get recipient {recipient_id} for user {user_id}: {e}")
            return None
    
    @staticmethod
    def format_recipient_status(recipient) -> dict:
        """Format recipient status for display."""
        if not recipient:
            return {'status': 'Not found', 'toggle_text': 'Enable'}
        
        status = "✅ Active" if recipient.enabled else "❌ Disabled"
        toggle_text = "Disable" if recipient.enabled else "Enable"
        
        return {
            'status': status,
            'toggle_text': toggle_text,
            'platform': recipient.platform_type.title()
        }