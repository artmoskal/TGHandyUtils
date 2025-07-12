"""Clean unified recipient service - no ID prefixing, no hardcoded names."""

from typing import List, Optional
from models.unified_recipient import (
    UnifiedRecipient, UnifiedRecipientCreate, UnifiedRecipientUpdate,
    UnifiedUserPreferences, UnifiedUserPreferencesCreate, UnifiedUserPreferencesUpdate
)
from database.unified_recipient_repository import UnifiedRecipientRepository
from core.logging import get_logger

logger = get_logger(__name__)


class RecipientService:
    """Clean service for unified recipient management."""
    
    def __init__(self, repository: UnifiedRecipientRepository):
        self.repository = repository
    
    def get_all_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get all recipients for user."""
        return self.repository.get_all_recipients(user_id)
    
    def get_recipients_by_user(self, user_id: int) -> List[UnifiedRecipient]:
        """Get all recipients for user (alias for get_all_recipients)."""
        return self.repository.get_all_recipients(user_id)
    
    def get_enabled_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get enabled recipients for user."""
        return self.repository.get_enabled_recipients(user_id)
    
    def get_personal_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get personal recipients for user (auto-selected for tasks)."""
        return self.repository.get_personal_recipients(user_id)
    
    def get_shared_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get shared recipients for user (manual selection)."""
        return self.repository.get_shared_recipients(user_id)
    
    def get_default_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get recipients that should automatically receive tasks."""
        # Use personal recipients as default for task creation
        default_recipients = self.repository.get_default_recipients(user_id)
        
        logger.info(f"Default recipients for user {user_id}: {[r.name for r in default_recipients]}")
        return default_recipients
    
    def get_recipient_by_id(self, user_id: int, recipient_id: int) -> Optional[UnifiedRecipient]:
        """Get specific recipient by ID."""
        return self.repository.get_recipient_by_id(user_id, recipient_id)
    
    def add_personal_recipient(self, user_id: int, name: str, platform_type: str, credentials: str, 
                              platform_config: Optional[dict] = None) -> int:
        """Add personal recipient (user's own account)."""
        # Check if user has any recipients - if not, make this one default
        existing_recipients = self.repository.get_all_recipients(user_id)
        is_first_recipient = len(existing_recipients) == 0
        
        recipient = UnifiedRecipientCreate(
            name=name,
            platform_type=platform_type,
            credentials=credentials,
            platform_config=platform_config,
            is_personal=True,
            is_default=is_first_recipient,  # First recipient becomes default
            enabled=True
        )
        
        recipient_id = self.repository.add_recipient(user_id, recipient)
        logger.info(f"Added personal recipient {name} for user {user_id} (default: {is_first_recipient})")
        return recipient_id
    
    def add_shared_recipient(self, user_id: int, name: str, platform_type: str, credentials: str,
                            platform_config: Optional[dict] = None, shared_by_info: Optional[str] = None) -> int:
        """Add shared recipient (account shared by others)."""
        # Store sharing attribution in platform_config
        if shared_by_info:
            if platform_config:
                platform_config['shared_by'] = shared_by_info
            else:
                platform_config = {'shared_by': shared_by_info}
        
        recipient = UnifiedRecipientCreate(
            name=name,
            platform_type=platform_type,
            credentials=credentials,
            platform_config=platform_config,
            is_personal=False,
            is_default=False,  # Shared recipients are never default
            enabled=True
        )
        
        recipient_id = self.repository.add_recipient(user_id, recipient)
        logger.info(f"Added shared recipient {name} for user {user_id}")
        return recipient_id
    
    def toggle_default_status(self, user_id: int, recipient_id: int) -> bool:
        """Toggle default status for a recipient. Returns new default status."""
        recipient = self.repository.get_recipient_by_id(user_id, recipient_id)
        if not recipient:
            raise ValueError(f"Recipient {recipient_id} not found for user {user_id}")
        
        new_default_status = not recipient.is_default
        
        # Update recipient in database
        update_data = UnifiedRecipientUpdate(is_default=new_default_status)
        self.repository.update_recipient(user_id, recipient_id, update_data)
        
        logger.info(f"Toggled default status for recipient {recipient.name} (user {user_id}): {new_default_status}")
        return new_default_status
    
    def update_recipient(self, user_id: int, recipient_id: int, updates: UnifiedRecipientUpdate) -> bool:
        """Update recipient."""
        return self.repository.update_recipient(user_id, recipient_id, updates)
    
    def remove_recipient(self, user_id: int, recipient_id: int) -> bool:
        """Remove recipient."""
        return self.repository.remove_recipient(user_id, recipient_id)
    
    def toggle_recipient_enabled(self, user_id: int, recipient_id: int) -> bool:
        """Toggle recipient enabled status."""
        return self.repository.toggle_recipient_enabled(user_id, recipient_id)
    
    # NOTE: Default recipient logic now uses is_personal=True
    # No need for separate set_default method
    
    def get_recipient_credentials(self, user_id: int, recipient_id: int) -> Optional[str]:
        """Get credentials for specific recipient."""
        recipient = self.repository.get_recipient_by_id(user_id, recipient_id)
        return recipient.credentials if recipient else None
    
    def get_recipient_config(self, user_id: int, recipient_id: int) -> Optional[dict]:
        """Get platform config for specific recipient."""
        recipient = self.repository.get_recipient_by_id(user_id, recipient_id)
        return recipient.platform_config if recipient else None
    
    # User preferences methods
    def get_user_preferences(self, user_id: int) -> Optional[UnifiedUserPreferences]:
        """Get user preferences."""
        return self.repository.get_user_preferences(user_id)
    
    def is_recipient_ui_enabled(self, user_id: int) -> bool:
        """Check if recipient selection UI should be shown."""
        prefs = self.repository.get_user_preferences(user_id)
        return prefs.show_recipient_ui if prefs else False
    
    def enable_recipient_ui(self, user_id: int, enabled: bool) -> bool:
        """Enable/disable recipient selection UI."""
        prefs = self.repository.get_user_preferences(user_id)
        
        if prefs:
            updates = UnifiedUserPreferencesUpdate(show_recipient_ui=enabled)
            return self.repository.update_user_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(show_recipient_ui=enabled)
            return self.repository.create_user_preferences(user_id, new_prefs)
    
    def are_telegram_notifications_enabled(self, user_id: int) -> bool:
        """Check if telegram notifications are enabled."""
        prefs = self.repository.get_user_preferences(user_id)
        return prefs.telegram_notifications if prefs else True
    
    def set_telegram_notifications(self, user_id: int, enabled: bool) -> bool:
        """Enable/disable telegram notifications."""
        prefs = self.repository.get_user_preferences(user_id)
        
        if prefs:
            updates = UnifiedUserPreferencesUpdate(telegram_notifications=enabled)
            return self.repository.update_user_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(telegram_notifications=enabled)
            return self.repository.create_user_preferences(user_id, new_prefs)
    
    def update_owner_name(self, user_id: int, owner_name: str) -> bool:
        """Update user's owner name."""
        prefs = self.repository.get_user_preferences(user_id)
        
        if prefs:
            updates = UnifiedUserPreferencesUpdate(owner_name=owner_name)
            return self.repository.update_user_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(owner_name=owner_name)
            return self.repository.create_user_preferences(user_id, new_prefs)
    
    def update_location(self, user_id: int, location: str) -> bool:
        """Update user's location."""
        prefs = self.repository.get_user_preferences(user_id)
        
        if prefs:
            updates = UnifiedUserPreferencesUpdate(location=location)
            return self.repository.update_user_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(location=location)
            return self.repository.create_user_preferences(user_id, new_prefs)
    
    def delete_all_user_data(self, user_id: int) -> bool:
        """Delete all user data for GDPR compliance."""
        return self.repository.delete_all_user_data(user_id)