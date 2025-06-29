"""Clean recipient service - no legacy code."""

from typing import List, Optional
from models.recipient import (
    UserPlatform, UserPlatformCreate, UserPlatformUpdate,
    SharedRecipient, SharedRecipientCreate, SharedRecipientUpdate,
    Recipient, UserPreferencesV2, UserPreferencesV2Create, UserPreferencesV2Update
)
from core.recipient_interfaces import (
    IRecipientService, IUserPlatformRepository, ISharedRecipientRepository, IUserPreferencesV2Repository
)
from core.logging import get_logger

logger = get_logger(__name__)


class RecipientService(IRecipientService):
    """Service for managing all recipients."""
    
    def __init__(
        self,
        platform_repo: IUserPlatformRepository,
        shared_repo: ISharedRecipientRepository,
        prefs_repo: IUserPreferencesV2Repository
    ):
        self.platform_repo = platform_repo
        self.shared_repo = shared_repo
        self.prefs_repo = prefs_repo
    
    def get_all_recipients(self, user_id: int) -> List[Recipient]:
        """Get all recipients (user platforms + shared recipients)."""
        recipients = []
        
        # Add user platforms
        platforms = self.platform_repo.get_user_platforms(user_id)
        for platform in platforms:
            recipients.append(Recipient(
                id=f"platform_{platform.id}",
                name=f"My {platform.platform_type.title()}",
                platform_type=platform.platform_type,
                type="user_platform",
                enabled=platform.enabled
            ))
        
        # Add shared recipients
        shared = self.shared_repo.get_shared_recipients(user_id)
        for recipient in shared:
            recipients.append(Recipient(
                id=f"shared_{recipient.id}",
                name=recipient.name,
                platform_type=recipient.platform_type,
                type="shared_recipient",
                enabled=recipient.enabled
            ))
        
        # Sort by name for consistent ordering
        recipients.sort(key=lambda r: r.name)
        return recipients
    
    def get_enabled_recipients(self, user_id: int) -> List[Recipient]:
        """Get all enabled recipients."""
        recipients = self.get_all_recipients(user_id)
        return [r for r in recipients if r.enabled]
    
    def get_default_recipients(self, user_id: int) -> List[Recipient]:
        """Get default recipients for task creation."""
        prefs = self.prefs_repo.get_preferences(user_id)
        
        if prefs and prefs.default_recipients:
            # Use specified defaults
            return self.get_recipients_by_ids(user_id, prefs.default_recipients)
        else:
            # Use all enabled recipients as default
            return self.get_enabled_recipients(user_id)
    
    def get_recipients_by_ids(self, user_id: int, recipient_ids: List[str]) -> List[Recipient]:
        """Get specific recipients by IDs."""
        all_recipients = self.get_all_recipients(user_id)
        recipient_map = {r.id: r for r in all_recipients}
        
        result = []
        for recipient_id in recipient_ids:
            if recipient_id in recipient_map:
                result.append(recipient_map[recipient_id])
            else:
                logger.warning(f"Recipient {recipient_id} not found for user {user_id}")
        
        return result
    
    def add_user_platform(self, user_id: int, platform: UserPlatformCreate) -> str:
        """Add user platform. Returns recipient ID."""
        platform_id = self.platform_repo.add_platform(user_id, platform)
        recipient_id = f"platform_{platform_id}"
        
        logger.info(f"Added user platform {platform.platform_type} as recipient {recipient_id}")
        return recipient_id
    
    def add_shared_recipient(self, user_id: int, recipient: SharedRecipientCreate) -> str:
        """Add shared recipient. Returns recipient ID."""
        recipient_id = self.shared_repo.add_recipient(user_id, recipient)
        unified_id = f"shared_{recipient_id}"
        
        logger.info(f"Added shared recipient {recipient.name} as {unified_id}")
        return unified_id
    
    def remove_recipient(self, user_id: int, recipient_id: str) -> bool:
        """Remove any recipient by unified ID."""
        if recipient_id.startswith("platform_"):
            # Remove user platform
            platform_id = int(recipient_id.replace("platform_", ""))
            
            # Find platform type by ID
            platforms = self.platform_repo.get_user_platforms(user_id)
            for platform in platforms:
                if platform.id == platform_id:
                    return self.platform_repo.remove_platform(user_id, platform.platform_type)
            
            logger.warning(f"Platform {platform_id} not found for user {user_id}")
            return False
            
        elif recipient_id.startswith("shared_"):
            # Remove shared recipient
            shared_id = int(recipient_id.replace("shared_", ""))
            return self.shared_repo.remove_recipient(user_id, shared_id)
        
        else:
            logger.error(f"Invalid recipient ID format: {recipient_id}")
            return False
    
    def is_recipient_ui_enabled(self, user_id: int) -> bool:
        """Check if recipient selection UI should be shown."""
        logger.debug(f"Checking recipient UI status for user {user_id}")
        prefs = self.prefs_repo.get_preferences(user_id)
        logger.debug(f"Retrieved preferences for user {user_id}: {prefs}")
        result = prefs.show_recipient_ui if prefs else False
        logger.debug(f"Recipient UI enabled for user {user_id}: {result}")
        return result
    
    def enable_recipient_ui(self, user_id: int, enabled: bool) -> bool:
        """Enable/disable recipient selection UI."""
        logger.debug(f"Enabling recipient UI for user {user_id}: {enabled}")
        prefs = self.prefs_repo.get_preferences(user_id)
        logger.debug(f"Current preferences for user {user_id}: {prefs}")
        
        try:
            if prefs:
                # Update existing preferences
                logger.debug(f"Updating existing preferences for user {user_id}")
                updates = UserPreferencesV2Update(show_recipient_ui=enabled)
                logger.debug(f"Update object: {updates}")
                result = self.prefs_repo.update_preferences(user_id, updates)
                logger.debug(f"Update preferences result for user {user_id}: {result}")
                return result
            else:
                # Create new preferences
                logger.debug(f"Creating new preferences for user {user_id}")
                new_prefs = UserPreferencesV2Create(show_recipient_ui=enabled)
                logger.debug(f"New preferences object: {new_prefs}")
                result = self.prefs_repo.create_preferences(user_id, new_prefs)
                logger.debug(f"Create preferences result for user {user_id}: {result}")
                return result
        except Exception as e:
            logger.error(f"Exception in enable_recipient_ui for user {user_id}: {e}", exc_info=True)
            return False
    
    def update_default_recipients(self, user_id: int, recipient_ids: List[str]) -> bool:
        """Update default recipients for task creation."""
        prefs = self.prefs_repo.get_preferences(user_id)
        
        if prefs:
            # Update existing preferences
            updates = UserPreferencesV2Update(default_recipients=recipient_ids)
            return self.prefs_repo.update_preferences(user_id, updates)
        else:
            # Create new preferences
            new_prefs = UserPreferencesV2Create(default_recipients=recipient_ids)
            return self.prefs_repo.create_preferences(user_id, new_prefs)
    
    def get_user_platform(self, user_id: int, platform_type: str) -> Optional[UserPlatform]:
        """Get specific user platform by type."""
        return self.platform_repo.get_platform_by_type(user_id, platform_type)
    
    def get_shared_recipient_by_unified_id(self, user_id: int, recipient_id: str) -> Optional[SharedRecipient]:
        """Get shared recipient by unified ID."""
        if not recipient_id.startswith("shared_"):
            return None
        
        shared_id = int(recipient_id.replace("shared_", ""))
        return self.shared_repo.get_recipient_by_id(user_id, shared_id)
    
    def get_recipient_credentials(self, user_id: int, recipient_id: str) -> Optional[str]:
        """Get credentials for a specific recipient."""
        if recipient_id.startswith("platform_"):
            # Get from user platform
            platform_id = int(recipient_id.replace("platform_", ""))
            platforms = self.platform_repo.get_user_platforms(user_id)
            for platform in platforms:
                if platform.id == platform_id:
                    return platform.credentials
            return None
            
        elif recipient_id.startswith("shared_"):
            # Get from shared recipient
            shared_id = int(recipient_id.replace("shared_", ""))
            shared = self.shared_repo.get_recipient_by_id(user_id, shared_id)
            return shared.credentials if shared else None
        
        return None
    
    def get_recipient_config(self, user_id: int, recipient_id: str) -> Optional[dict]:
        """Get platform config for a specific recipient."""
        if recipient_id.startswith("platform_"):
            # Get from user platform
            platform_id = int(recipient_id.replace("platform_", ""))
            platforms = self.platform_repo.get_user_platforms(user_id)
            for platform in platforms:
                if platform.id == platform_id:
                    return platform.platform_config
            return None
            
        elif recipient_id.startswith("shared_"):
            # Get from shared recipient
            shared_id = int(recipient_id.replace("shared_", ""))
            shared = self.shared_repo.get_recipient_by_id(user_id, shared_id)
            return shared.platform_config if shared else None
        
        return None
    
    def update_owner_name(self, user_id: int, owner_name: str) -> bool:
        """Update user's owner name."""
        prefs = self.prefs_repo.get_preferences(user_id)
        logger.info(f"Current prefs for user {user_id}: {prefs}")
        
        if prefs:
            # Update existing preferences
            updates = UserPreferencesV2Update(owner_name=owner_name)
            logger.info(f"Updating existing prefs with: {updates}")
            result = self.prefs_repo.update_preferences(user_id, updates)
            logger.info(f"Update result: {result}")
            return result
        else:
            # Create new preferences
            new_prefs = UserPreferencesV2Create(owner_name=owner_name)
            logger.info(f"Creating new prefs: {new_prefs}")
            result = self.prefs_repo.create_preferences(user_id, new_prefs)
            logger.info(f"Create result: {result}")
            return result
    
    def update_location(self, user_id: int, location: str) -> bool:
        """Update user's location for timezone handling."""
        prefs = self.prefs_repo.get_preferences(user_id)
        
        if prefs:
            # Update existing preferences
            updates = UserPreferencesV2Update(location=location)
            return self.prefs_repo.update_preferences(user_id, updates)
        else:
            # Create new preferences
            new_prefs = UserPreferencesV2Create(location=location)
            return self.prefs_repo.create_preferences(user_id, new_prefs)
    
    def get_user_preferences(self, user_id: int) -> Optional[UserPreferencesV2]:
        """Get user preferences (convenience method)."""
        return self.prefs_repo.get_preferences(user_id)
    
    def delete_all_user_data(self, user_id: int) -> bool:
        """Delete all user data for GDPR compliance."""
        try:
            # Delete user platforms
            platforms = self.platform_repo.get_user_platforms(user_id)
            for platform in platforms:
                self.platform_repo.delete_platform(user_id, platform.id)
            
            # Delete shared recipients  
            shared = self.shared_repo.get_shared_recipients(user_id)
            for recipient in shared:
                self.shared_repo.delete_recipient(user_id, recipient.id)
            
            # Delete preferences
            prefs = self.prefs_repo.get_preferences(user_id)
            if prefs:
                # Delete by updating with empty data and then removing
                try:
                    with self.prefs_repo.db_manager.get_connection() as conn:
                        conn.execute(
                            "DELETE FROM user_preferences_v2 WHERE telegram_user_id = ?", 
                            (user_id,)
                        )
                except Exception as e:
                    logger.error(f"Failed to delete preferences for user {user_id}: {e}")
                    return False
            
            logger.info(f"Deleted all data for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete all user data for {user_id}: {e}")
            return False
    
    def toggle_recipient_enabled(self, user_id: int, recipient_id: str) -> bool:
        """Toggle recipient enabled status."""
        if recipient_id.startswith("platform_"):
            # Toggle user platform
            platform_id = int(recipient_id.replace("platform_", ""))
            platforms = self.platform_repo.get_user_platforms(user_id)
            for platform in platforms:
                if platform.id == platform_id:
                    # Get current status and toggle it
                    new_status = not platform.enabled
                    from models.recipient import UserPlatformUpdate
                    updates = UserPlatformUpdate(enabled=new_status)
                    return self.platform_repo.update_platform(user_id, platform.platform_type, updates)
            return False
            
        elif recipient_id.startswith("shared_"):
            # Toggle shared recipient
            shared_id = int(recipient_id.replace("shared_", ""))
            shared = self.shared_repo.get_recipient_by_id(user_id, shared_id)
            if shared:
                new_status = not shared.enabled
                from models.recipient import SharedRecipientUpdate
                updates = SharedRecipientUpdate(enabled=new_status)
                return self.shared_repo.update_recipient(user_id, shared_id, updates)
            return False
        
        return False
    
    def are_telegram_notifications_enabled(self, user_id: int) -> bool:
        """Check if telegram notifications are enabled for user."""
        prefs = self.prefs_repo.get_preferences(user_id)
        return prefs.telegram_notifications if prefs else True  # Default to enabled
    
    def set_telegram_notifications(self, user_id: int, enabled: bool) -> bool:
        """Enable/disable telegram notifications for user."""
        prefs = self.prefs_repo.get_preferences(user_id)
        
        if prefs:
            # Update existing preferences
            updates = UserPreferencesV2Update(telegram_notifications=enabled)
            return self.prefs_repo.update_preferences(user_id, updates)
        else:
            # Create new preferences
            new_prefs = UserPreferencesV2Create(telegram_notifications=enabled)
            return self.prefs_repo.create_preferences(user_id, new_prefs)