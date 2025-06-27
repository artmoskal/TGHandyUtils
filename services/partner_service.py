"""Partner service for managing user partners and preferences."""

from typing import List, Optional, Dict, Any
from database.partner_repository import PartnerRepository, UserPreferencesRepository
from models.partner import Partner, PartnerCreate, PartnerUpdate, UserPreferences, UserPreferencesCreate, UserPreferencesUpdate
from core.exceptions import ValidationError
from core.logging import get_logger

logger = get_logger(__name__)


class PartnerService:
    """Service for partner management operations."""
    
    def __init__(self, partner_repo: PartnerRepository, prefs_repo: UserPreferencesRepository):
        self.partner_repo = partner_repo
        self.prefs_repo = prefs_repo
    
    def get_user_partners(self, user_id: int) -> List[Partner]:
        """Get all partners for a user."""
        return self.partner_repo.get_user_partners(user_id)
    
    def get_enabled_partners(self, user_id: int) -> List[Partner]:
        """Get only enabled partners for a user."""
        return self.partner_repo.get_enabled_partners(user_id)
    
    def get_self_partner(self, user_id: int) -> Optional[Partner]:
        """Get the user's self partner."""
        return self.partner_repo.get_self_partner(user_id)
    
    def get_partner_by_id(self, user_id: int, partner_id: str) -> Optional[Partner]:
        """Get a specific partner by ID."""
        return self.partner_repo.get_partner_by_id(user_id, partner_id)
    
    def add_partner(self, user_id: int, partner: PartnerCreate) -> str:
        """Add a new partner for a user."""
        try:
            # Validate input
            if not partner.name.strip():
                raise ValidationError("Partner name cannot be empty")
            
            if not partner.platform.strip():
                raise ValidationError("Partner platform cannot be empty")
            
            if not partner.credentials.strip():
                raise ValidationError("Partner credentials cannot be empty")
            
            # Check if user already has a self partner
            if partner.is_self:
                existing_self = self.get_self_partner(user_id)
                if existing_self:
                    raise ValidationError("User already has a self partner")
            
            return self.partner_repo.add_partner(user_id, partner)
            
        except Exception as e:
            logger.error(f"Failed to add partner for user {user_id}: {e}")
            raise ValidationError(f"Failed to add partner: {e}")
    
    def update_partner(self, user_id: int, partner_id: str, updates: PartnerUpdate) -> bool:
        """Update a partner's information."""
        try:
            # Validate input
            if updates.name is not None and not updates.name.strip():
                raise ValidationError("Partner name cannot be empty")
            
            if updates.platform is not None and not updates.platform.strip():
                raise ValidationError("Partner platform cannot be empty")
            
            if updates.credentials is not None and not updates.credentials.strip():
                raise ValidationError("Partner credentials cannot be empty")
            
            return self.partner_repo.update_partner(user_id, partner_id, updates)
            
        except Exception as e:
            logger.error(f"Failed to update partner {partner_id} for user {user_id}: {e}")
            raise ValidationError(f"Failed to update partner: {e}")
    
    def delete_partner(self, user_id: int, partner_id: str) -> bool:
        """Delete a partner."""
        try:
            # Prevent deletion of self partner
            partner = self.get_partner_by_id(user_id, partner_id)
            if partner and partner.is_self:
                raise ValidationError("Cannot delete self partner")
            
            return self.partner_repo.delete_partner(user_id, partner_id)
            
        except Exception as e:
            logger.error(f"Failed to delete partner {partner_id} for user {user_id}: {e}")
            raise ValidationError(f"Failed to delete partner: {e}")
    
    def get_partners_by_ids(self, user_id: int, partner_ids: List[str]) -> List[Partner]:
        """Get multiple partners by their IDs."""
        return self.partner_repo.get_partners_by_ids(user_id, partner_ids)
    
    def get_default_partners(self, user_id: int) -> List[Partner]:
        """Get the user's default partners for task creation."""
        prefs = self.prefs_repo.get_preferences(user_id)
        
        if prefs and prefs.default_partners:
            # Get specific partners
            return self.get_partners_by_ids(user_id, prefs.default_partners)
        else:
            # Default to self partner only
            self_partner = self.get_self_partner(user_id)
            return [self_partner] if self_partner else []
    
    def has_self_partner(self, user_id: int) -> bool:
        """Check if user has configured a self partner."""
        return self.get_self_partner(user_id) is not None
    
    def migrate_legacy_user(self, user_id: int, platform_type: str, credentials: str, 
                           platform_config: Optional[Dict[str, Any]] = None) -> str:
        """Migrate a legacy user to the partner system."""
        try:
            # Check if already migrated
            if self.has_self_partner(user_id):
                logger.info(f"User {user_id} already migrated to partner system")
                return "self"
            
            # Create self partner
            self_partner = PartnerCreate(
                name="Me",
                platform=platform_type,
                credentials=credentials,
                platform_config=platform_config,
                is_self=True,
                enabled=True
            )
            
            partner_id = self.add_partner(user_id, self_partner)
            
            # Create default preferences
            prefs = UserPreferencesCreate(
                user_id=user_id,
                default_partners=["self"],
                show_sharing_ui=False,
                telegram_notifications=True
            )
            self.prefs_repo.create_or_update_preferences(user_id, prefs)
            
            logger.info(f"Migrated user {user_id} to partner system")
            return partner_id
            
        except Exception as e:
            logger.error(f"Failed to migrate user {user_id}: {e}")
            raise ValidationError(f"Failed to migrate user: {e}")


class UserPreferencesService:
    """Service for user preferences management."""
    
    def __init__(self, prefs_repo: UserPreferencesRepository):
        self.prefs_repo = prefs_repo
    
    def get_preferences(self, user_id: int) -> Optional[UserPreferences]:
        """Get user preferences."""
        return self.prefs_repo.get_preferences(user_id)
    
    def get_preferences_or_default(self, user_id: int) -> UserPreferences:
        """Get user preferences or return defaults."""
        prefs = self.get_preferences(user_id)
        if prefs:
            return prefs
        
        # Return default preferences
        return UserPreferences(
            user_id=user_id,
            default_partners=["self"],
            show_sharing_ui=False,
            telegram_notifications=True,
            location=None
        )
    
    def create_or_update_preferences(self, user_id: int, prefs: UserPreferencesCreate) -> bool:
        """Create or update user preferences."""
        return self.prefs_repo.create_or_update_preferences(user_id, prefs)
    
    def update_preferences(self, user_id: int, updates: UserPreferencesUpdate) -> bool:
        """Update specific user preferences."""
        return self.prefs_repo.update_preferences(user_id, updates)
    
    def update_sharing_ui_enabled(self, user_id: int, enabled: bool) -> bool:
        """Update the sharing UI visibility setting."""
        updates = UserPreferencesUpdate(show_sharing_ui=enabled)
        return self.update_preferences(user_id, updates)
    
    def update_telegram_notifications(self, user_id: int, enabled: bool) -> bool:
        """Update Telegram notifications setting."""
        updates = UserPreferencesUpdate(telegram_notifications=enabled)
        return self.update_preferences(user_id, updates)
    
    def update_location(self, user_id: int, location: str) -> bool:
        """Update user location."""
        updates = UserPreferencesUpdate(location=location)
        return self.update_preferences(user_id, updates)
    
    def update_default_partners(self, user_id: int, partner_ids: List[str]) -> bool:
        """Update default partners for task creation."""
        updates = UserPreferencesUpdate(default_partners=partner_ids)
        return self.update_preferences(user_id, updates)
    
    def is_sharing_ui_enabled(self, user_id: int) -> bool:
        """Check if sharing UI should be shown for user."""
        prefs = self.get_preferences(user_id)
        return prefs.show_sharing_ui if prefs else False
    
    def get_telegram_notifications_enabled(self, user_id: int) -> bool:
        """Check if Telegram notifications are enabled for user."""
        prefs = self.get_preferences(user_id)
        return prefs.telegram_notifications if prefs else True
    
    def get_user_location(self, user_id: int) -> Optional[str]:
        """Get user's location setting."""
        prefs = self.get_preferences(user_id)
        return prefs.location if prefs else None