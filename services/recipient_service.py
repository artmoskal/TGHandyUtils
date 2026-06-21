"""Clean unified recipient service - no ID prefixing, no hardcoded names."""

from typing import List, Optional
from models.unified_recipient import (
    UnifiedRecipient, UnifiedRecipientCreate, UnifiedRecipientUpdate,
    UnifiedUserPreferences, UnifiedUserPreferencesCreate, UnifiedUserPreferencesUpdate
)
from models.parameter_objects import RecipientCreationData, SharedRecipientCreationData
from database.unified_recipient_repository import UnifiedRecipientRepository
from core.interfaces import IUserPreferencesRepository, IConfig
from core.logging import get_logger

logger = get_logger(__name__)


class RecipientService:
    """Clean service for unified recipient management."""
    
    def __init__(self, repository: UnifiedRecipientRepository, preferences_repo: IUserPreferencesRepository,
                 config: Optional[IConfig] = None):
        self.repository = repository
        self.preferences_repo = preferences_repo
        # Needed only by the location/timezone update path (LLM timezone parsing). The composition
        # root injects it; kept optional so unrelated callers/tests need not supply it.
        self.config = config
    
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
    
    def add_personal_recipient(self, user_id: int, recipient_data: RecipientCreationData) -> int:
        """Add personal recipient (user's own account) using parameter object."""
        # Check if user has any recipients - if not, make this one default
        existing_recipients = self.repository.get_all_recipients(user_id)
        is_first_recipient = len(existing_recipients) == 0
        
        recipient = UnifiedRecipientCreate(
            name=recipient_data.name,
            platform_type=recipient_data.platform_type,
            credentials=recipient_data.credentials,
            platform_config=recipient_data.platform_config,
            is_personal=True,
            is_default=is_first_recipient,  # First recipient becomes default
            enabled=True
        )
        
        recipient_id = self.repository.add_recipient(user_id, recipient)
        logger.info(f"Added personal recipient {recipient_data.name} for user {user_id} (default: {is_first_recipient})")
        return recipient_id
    
    def add_shared_recipient(self, user_id: int, recipient_data: SharedRecipientCreationData) -> int:
        """Add shared recipient (account shared by others) using parameter object."""
        # Store sharing attribution in platform_config
        platform_config = recipient_data.platform_config.copy() if recipient_data.platform_config else {}
        if recipient_data.shared_by_info:
            platform_config['shared_by'] = recipient_data.shared_by_info
        
        recipient = UnifiedRecipientCreate(
            name=recipient_data.name,
            platform_type=recipient_data.platform_type,
            credentials=recipient_data.credentials,
            platform_config=platform_config,
            is_personal=False,
            is_default=False,  # Shared recipients are never default
            enabled=True
        )
        
        recipient_id = self.repository.add_recipient(user_id, recipient)
        logger.info(f"Added shared recipient {recipient_data.name} for user {user_id}")
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
        return self.preferences_repo.get_preferences(user_id)
    
    def is_recipient_ui_enabled(self, user_id: int) -> bool:
        """Check if recipient selection UI should be shown."""
        prefs = self.preferences_repo.get_preferences(user_id)
        return prefs.show_recipient_ui if prefs else False
    
    def enable_recipient_ui(self, user_id: int, enabled: bool) -> bool:
        """Enable/disable recipient selection UI."""
        prefs = self.preferences_repo.get_preferences(user_id)
        
        if prefs:
            updates = UnifiedUserPreferencesUpdate(show_recipient_ui=enabled)
            return self.preferences_repo.update_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(show_recipient_ui=enabled)
            return self.preferences_repo.create_preferences(user_id, new_prefs)
    
    def are_telegram_notifications_enabled(self, user_id: int) -> bool:
        """Check if telegram notifications are enabled."""
        prefs = self.preferences_repo.get_preferences(user_id)
        return prefs.telegram_notifications if prefs else True
    
    def set_telegram_notifications(self, user_id: int, enabled: bool) -> bool:
        """Enable/disable telegram notifications."""
        prefs = self.preferences_repo.get_preferences(user_id)
        
        if prefs:
            updates = UnifiedUserPreferencesUpdate(telegram_notifications=enabled)
            return self.preferences_repo.update_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(telegram_notifications=enabled)
            return self.preferences_repo.create_preferences(user_id, new_prefs)
    
    def get_content_mode(self, user_id: int) -> str:
        """Get the user's content-processing mode (reminder | anki | auto)."""
        prefs = self.preferences_repo.get_preferences(user_id)
        return prefs.content_mode if (prefs and prefs.content_mode) else "reminder"

    def set_content_mode(self, user_id: int, mode: str) -> bool:
        """Set the user's content-processing mode."""
        if mode not in ("reminder", "anki", "auto"):
            raise ValueError(f"Invalid content mode: {mode}")
        prefs = self.preferences_repo.get_preferences(user_id)
        if prefs:
            updates = UnifiedUserPreferencesUpdate(content_mode=mode)
            return self.preferences_repo.update_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(content_mode=mode)
            return self.preferences_repo.create_preferences(user_id, new_prefs)

    def get_anki_deck_name(self, user_id: int) -> str:
        """Get the user's configured Anki deck name (falls back to the default)."""
        from services.anki_card_service import DEFAULT_DECK_NAME
        prefs = self.preferences_repo.get_preferences(user_id)
        return prefs.anki_deck_name if (prefs and prefs.anki_deck_name) else DEFAULT_DECK_NAME

    def update_anki_deck_name(self, user_id: int, deck_name: str) -> bool:
        """Set the user's Anki deck name."""
        prefs = self.preferences_repo.get_preferences(user_id)
        if prefs:
            updates = UnifiedUserPreferencesUpdate(anki_deck_name=deck_name)
            return self.preferences_repo.update_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(anki_deck_name=deck_name)
            return self.preferences_repo.create_preferences(user_id, new_prefs)

    def update_owner_name(self, user_id: int, owner_name: str) -> bool:
        """Update user's owner name."""
        prefs = self.preferences_repo.get_preferences(user_id)
        
        if prefs:
            updates = UnifiedUserPreferencesUpdate(owner_name=owner_name)
            return self.preferences_repo.update_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(owner_name=owner_name)
            return self.preferences_repo.create_preferences(user_id, new_prefs)
    
    def update_location(self, user_id: int, location: str) -> bool:
        """Update user's location and calculate timezone offset."""
        from services.parsing_service import ParsingService
        
        if self.config is None:
            raise RuntimeError(
                "RecipientService.update_location needs config for timezone parsing; "
                "inject it via the composition root."
            )
        # Calculate UTC offset for the new location using LLM
        parsing_service = ParsingService(self.config, self.preferences_repo)
        utc_offset = parsing_service.parse_timezone_with_llm(location)
        
        prefs = self.preferences_repo.get_preferences(user_id)
        
        if prefs:
            # Update location and UTC offset together
            updates = UnifiedUserPreferencesUpdate(location=location, utc_offset=utc_offset)
            return self.preferences_repo.update_preferences(user_id, updates)
        else:
            new_prefs = UnifiedUserPreferencesCreate(location=location, utc_offset=utc_offset)
            return self.preferences_repo.create_preferences(user_id, new_prefs)
    
    def delete_all_user_data(self, user_id: int) -> bool:
        """Delete all user data for GDPR compliance."""
        return self.repository.delete_all_user_data(user_id)