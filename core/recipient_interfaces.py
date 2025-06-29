"""Clean recipient interfaces - no legacy code."""

from abc import ABC, abstractmethod
from typing import List, Optional
from models.recipient import (
    UserPlatform, UserPlatformCreate, UserPlatformUpdate,
    SharedRecipient, SharedRecipientCreate, SharedRecipientUpdate,
    Recipient, UserPreferencesV2, UserPreferencesV2Create, UserPreferencesV2Update
)


class IUserPlatformRepository(ABC):
    """Repository for user-owned platforms."""
    
    @abstractmethod
    def get_user_platforms(self, user_id: int) -> List[UserPlatform]:
        """Get all platforms owned by user."""
        pass
    
    @abstractmethod
    def get_enabled_platforms(self, user_id: int) -> List[UserPlatform]:
        """Get enabled platforms owned by user."""
        pass
    
    @abstractmethod
    def get_platform_by_type(self, user_id: int, platform_type: str) -> Optional[UserPlatform]:
        """Get specific platform by type."""
        pass
    
    @abstractmethod
    def add_platform(self, user_id: int, platform: UserPlatformCreate) -> int:
        """Add new platform. Returns platform ID."""
        pass
    
    @abstractmethod
    def update_platform(self, user_id: int, platform_type: str, updates: UserPlatformUpdate) -> bool:
        """Update platform."""
        pass
    
    @abstractmethod
    def remove_platform(self, user_id: int, platform_type: str) -> bool:
        """Remove platform."""
        pass


class ISharedRecipientRepository(ABC):
    """Repository for shared recipients."""
    
    @abstractmethod
    def get_shared_recipients(self, user_id: int) -> List[SharedRecipient]:
        """Get all shared recipients for user."""
        pass
    
    @abstractmethod
    def get_enabled_recipients(self, user_id: int) -> List[SharedRecipient]:
        """Get enabled shared recipients for user."""
        pass
    
    @abstractmethod
    def get_recipient_by_id(self, user_id: int, recipient_id: int) -> Optional[SharedRecipient]:
        """Get specific shared recipient."""
        pass
    
    @abstractmethod
    def add_recipient(self, user_id: int, recipient: SharedRecipientCreate) -> int:
        """Add shared recipient. Returns recipient ID."""
        pass
    
    @abstractmethod
    def update_recipient(self, user_id: int, recipient_id: int, updates: SharedRecipientUpdate) -> bool:
        """Update shared recipient."""
        pass
    
    @abstractmethod
    def remove_recipient(self, user_id: int, recipient_id: int) -> bool:
        """Remove shared recipient."""
        pass


class IUserPreferencesV2Repository(ABC):
    """Repository for user preferences."""
    
    @abstractmethod
    def get_preferences(self, user_id: int) -> Optional[UserPreferencesV2]:
        """Get user preferences."""
        pass
    
    @abstractmethod
    def create_preferences(self, user_id: int, prefs: UserPreferencesV2Create) -> bool:
        """Create user preferences."""
        pass
    
    @abstractmethod
    def update_preferences(self, user_id: int, updates: UserPreferencesV2Update) -> bool:
        """Update user preferences."""
        pass


class IRecipientService(ABC):
    """Service for managing all recipients."""
    
    @abstractmethod
    def get_all_recipients(self, user_id: int) -> List[Recipient]:
        """Get all recipients (user platforms + shared recipients)."""
        pass
    
    @abstractmethod
    def get_enabled_recipients(self, user_id: int) -> List[Recipient]:
        """Get all enabled recipients."""
        pass
    
    @abstractmethod
    def get_default_recipients(self, user_id: int) -> List[Recipient]:
        """Get default recipients for task creation."""
        pass
    
    @abstractmethod
    def get_recipients_by_ids(self, user_id: int, recipient_ids: List[str]) -> List[Recipient]:
        """Get specific recipients by IDs."""
        pass
    
    @abstractmethod
    def add_user_platform(self, user_id: int, platform: UserPlatformCreate) -> str:
        """Add user platform. Returns recipient ID."""
        pass
    
    @abstractmethod
    def add_shared_recipient(self, user_id: int, recipient: SharedRecipientCreate) -> str:
        """Add shared recipient. Returns recipient ID."""
        pass
    
    @abstractmethod
    def remove_recipient(self, user_id: int, recipient_id: str) -> bool:
        """Remove any recipient by unified ID."""
        pass
    
    @abstractmethod
    def is_recipient_ui_enabled(self, user_id: int) -> bool:
        """Check if recipient selection UI should be shown."""
        pass