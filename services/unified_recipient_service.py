"""Unified recipient service - treats everyone (including self) as recipients with clear categorization."""

from typing import List, Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass

from models.recipient import Recipient
from core.recipient_interfaces import IRecipientService
from core.logging import get_logger

logger = get_logger(__name__)


class RecipientCategory(Enum):
    """Recipient category for clear UX distinction."""
    PERSONAL = "personal"      # User's own accounts (auto-selected)
    AVAILABLE = "available"    # Shared accounts (manual selection)


@dataclass
class CategorizedRecipient:
    """Wrapper for recipients with category information."""
    recipient: Recipient
    category: RecipientCategory
    is_auto_selected: bool
    display_name: str


class UnifiedRecipientService:
    """Unified service for managing all recipient types with clear categorization."""
    
    def __init__(self, recipient_service: IRecipientService):
        self.recipient_service = recipient_service
    
    def get_categorized_recipients(self, user_id: int) -> Dict[RecipientCategory, List[CategorizedRecipient]]:
        """Get all recipients organized by category."""
        logger.debug(f"Getting categorized recipients for user {user_id}")
        
        all_recipients = self.recipient_service.get_all_recipients(user_id)
        categorized = {
            RecipientCategory.PERSONAL: [],
            RecipientCategory.AVAILABLE: []
        }
        
        for recipient in all_recipients:
            if recipient.type == "user_platform":
                # Personal accounts - auto-selected for tasks
                categorized_recipient = CategorizedRecipient(
                    recipient=recipient,
                    category=RecipientCategory.PERSONAL,
                    is_auto_selected=True,
                    display_name=self._get_personal_display_name(recipient)
                )
                categorized[RecipientCategory.PERSONAL].append(categorized_recipient)
                
            elif recipient.type == "shared_recipient":
                # Shared accounts - manual selection
                categorized_recipient = CategorizedRecipient(
                    recipient=recipient,
                    category=RecipientCategory.AVAILABLE,
                    is_auto_selected=False,
                    display_name=recipient.name  # Use custom name as-is
                )
                categorized[RecipientCategory.AVAILABLE].append(categorized_recipient)
        
        logger.debug(f"Categorized recipients: {len(categorized[RecipientCategory.PERSONAL])} personal, {len(categorized[RecipientCategory.AVAILABLE])} available")
        return categorized
    
    def get_default_task_recipients(self, user_id: int) -> List[Recipient]:
        """Get recipients that should automatically receive tasks (personal accounts only)."""
        logger.debug(f"Getting default task recipients for user {user_id}")
        
        categorized = self.get_categorized_recipients(user_id)
        personal_recipients = categorized[RecipientCategory.PERSONAL]
        
        # Only include enabled personal recipients
        default_recipients = [
            cat_recipient.recipient 
            for cat_recipient in personal_recipients 
            if cat_recipient.recipient.enabled
        ]
        
        logger.info(f"Default task recipients for user {user_id}: {[r.name for r in default_recipients]}")
        return default_recipients
    
    def get_available_recipients(self, user_id: int) -> List[Recipient]:
        """Get recipients available for manual addition (shared accounts only)."""
        logger.debug(f"Getting available recipients for user {user_id}")
        
        categorized = self.get_categorized_recipients(user_id)
        available_recipients = categorized[RecipientCategory.AVAILABLE]
        
        # Only include enabled shared recipients
        available = [
            cat_recipient.recipient 
            for cat_recipient in available_recipients 
            if cat_recipient.recipient.enabled
        ]
        
        logger.debug(f"Available recipients for user {user_id}: {[r.name for r in available]}")
        return available
    
    def get_recipient_display_info(self, user_id: int) -> Dict[str, Any]:
        """Get formatted recipient information for UI display."""
        categorized = self.get_categorized_recipients(user_id)
        
        personal_info = []
        for cat_recipient in categorized[RecipientCategory.PERSONAL]:
            personal_info.append({
                "id": cat_recipient.recipient.id,
                "name": cat_recipient.display_name,
                "platform_type": cat_recipient.recipient.platform_type,
                "enabled": cat_recipient.recipient.enabled,
                "status": "✅ Active" if cat_recipient.recipient.enabled else "❌ Disabled"
            })
        
        available_info = []
        for cat_recipient in categorized[RecipientCategory.AVAILABLE]:
            available_info.append({
                "id": cat_recipient.recipient.id,
                "name": cat_recipient.display_name,
                "platform_type": cat_recipient.recipient.platform_type,
                "enabled": cat_recipient.recipient.enabled,
                "status": "✅ Active" if cat_recipient.recipient.enabled else "❌ Disabled"
            })
        
        return {
            "personal": personal_info,
            "available": available_info,
            "total_personal": len(personal_info),
            "total_available": len(available_info),
            "total_enabled_personal": len([p for p in personal_info if p["enabled"]]),
            "total_enabled_available": len([a for a in available_info if a["enabled"]])
        }
    
    def _get_personal_display_name(self, recipient: Recipient) -> str:
        """Generate display name for personal accounts."""
        # For now, keep existing naming but make it clearer
        platform_name = recipient.platform_type.title()
        return f"My {platform_name}"
    
    def generate_post_task_actions(self, user_id: int, used_recipients: List[Recipient]) -> Dict[str, List[Dict[str, str]]]:
        """Generate post-task creation action buttons."""
        logger.debug(f"Generating post-task actions for user {user_id}")
        
        categorized = self.get_categorized_recipients(user_id)
        used_recipient_ids = {r.id for r in used_recipients}
        
        # Actions to remove from personal accounts that were used
        remove_actions = []
        for cat_recipient in categorized[RecipientCategory.PERSONAL]:
            if cat_recipient.recipient.id in used_recipient_ids:
                remove_actions.append({
                    "text": f"❌ Remove from {cat_recipient.display_name}",
                    "callback_data": f"remove_task_from_{cat_recipient.recipient.id}",
                    "recipient_id": cat_recipient.recipient.id,
                    "recipient_name": cat_recipient.display_name
                })
        
        # Actions to add to available accounts that weren't used
        add_actions = []
        for cat_recipient in categorized[RecipientCategory.AVAILABLE]:
            if cat_recipient.recipient.id not in used_recipient_ids and cat_recipient.recipient.enabled:
                add_actions.append({
                    "text": f"➕ Add to {cat_recipient.display_name}",
                    "callback_data": f"add_task_to_{cat_recipient.recipient.id}",
                    "recipient_id": cat_recipient.recipient.id,
                    "recipient_name": cat_recipient.display_name
                })
        
        logger.debug(f"Generated {len(remove_actions)} remove actions, {len(add_actions)} add actions")
        return {
            "remove_actions": remove_actions,
            "add_actions": add_actions
        }