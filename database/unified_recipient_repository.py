"""Unified recipient repository - single repository for all recipients."""

import json
import sqlite3
from typing import List, Optional, Dict, Any
from datetime import datetime

from database.connection import DatabaseManager
from models.unified_recipient import (
    UnifiedRecipient, UnifiedRecipientCreate, UnifiedRecipientUpdate,
    UnifiedUserPreferences, UnifiedUserPreferencesCreate, UnifiedUserPreferencesUpdate
)
from core.exceptions import DatabaseError
from core.logging import get_logger

logger = get_logger(__name__)


class UnifiedRecipientRepository:
    """Single repository for all recipient operations."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_all_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get all recipients for user."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, user_id, name, platform_type, credentials, platform_config,
                           is_personal, is_default, enabled, shared_by, created_at, updated_at
                    FROM unified_recipients 
                    WHERE user_id = ?
                    ORDER BY is_personal DESC, name
                ''', (user_id,))
                
                recipients = []
                for row in cursor.fetchall():
                    config = None
                    if row[5]:
                        try:
                            config = json.loads(row[5])
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid config JSON for recipient {row[0]}")
                    
                    recipients.append(UnifiedRecipient(
                        id=row[0],
                        user_id=row[1],
                        name=row[2],
                        platform_type=row[3],
                        credentials=row[4],
                        platform_config=config,
                        is_personal=bool(row[6]),
                        is_default=bool(row[7]),
                        enabled=bool(row[8]),
                        shared_by=row[9],
                        created_at=datetime.fromisoformat(row[10]) if row[10] else None,
                        updated_at=datetime.fromisoformat(row[11]) if row[11] else None
                    ))
                
                return recipients
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get recipients for {user_id}: {e}")
            raise DatabaseError(f"Failed to get recipients: {e}")
    
    def get_enabled_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get enabled recipients for user."""
        recipients = self.get_all_recipients(user_id)
        return [r for r in recipients if r.enabled]
    
    def get_personal_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get personal recipients for user."""
        recipients = self.get_all_recipients(user_id)
        return [r for r in recipients if r.is_personal]
    
    def get_shared_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get shared recipients for user."""
        recipients = self.get_all_recipients(user_id)
        return [r for r in recipients if not r.is_personal]
    
    def get_default_recipients(self, user_id: int) -> List[UnifiedRecipient]:
        """Get default recipients for task creation."""
        recipients = self.get_all_recipients(user_id)
        return [r for r in recipients if r.is_default and r.enabled]
    
    def get_recipient_by_id(self, user_id: int, recipient_id: int) -> Optional[UnifiedRecipient]:
        """Get specific recipient by ID."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, user_id, name, platform_type, credentials, platform_config,
                           is_personal, is_default, enabled, shared_by, created_at, updated_at
                    FROM unified_recipients 
                    WHERE user_id = ? AND id = ?
                ''', (user_id, recipient_id))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                config = None
                if row[5]:
                    try:
                        config = json.loads(row[5])
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid config JSON for recipient {row[0]}")
                
                return UnifiedRecipient(
                    id=row[0],
                    user_id=row[1],
                    name=row[2],
                    platform_type=row[3],
                    credentials=row[4],
                    platform_config=config,
                    is_personal=bool(row[6]),
                    is_default=bool(row[7]),
                    enabled=bool(row[8]),
                    shared_by=row[9],
                    created_at=datetime.fromisoformat(row[10]) if row[10] else None,
                    updated_at=datetime.fromisoformat(row[11]) if row[11] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get recipient {recipient_id} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get recipient: {e}")
    
    def add_recipient(self, user_id: int, recipient: UnifiedRecipientCreate) -> int:
        """Add new recipient. Returns recipient ID."""
        try:
            config_json = None
            if recipient.platform_config:
                config_json = json.dumps(recipient.platform_config)
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO unified_recipients 
                    (user_id, name, platform_type, credentials, platform_config, 
                     is_personal, is_default, enabled, shared_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, recipient.name, recipient.platform_type, recipient.credentials,
                    config_json, recipient.is_personal, recipient.is_default, 
                    recipient.enabled, recipient.shared_by
                ))
                
                recipient_id = cursor.lastrowid
                logger.info(f"Added recipient {recipient.name} for user {user_id}")
                return recipient_id
                
        except sqlite3.Error as e:
            logger.error(f"Failed to add recipient for user {user_id}: {e}")
            raise DatabaseError(f"Failed to add recipient: {e}")
    
    def update_recipient(self, user_id: int, recipient_id: int, updates: UnifiedRecipientUpdate) -> bool:
        """Update recipient."""
        try:
            set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
            params = []
            
            if updates.name is not None:
                set_clauses.append("name = ?")
                params.append(updates.name)
            
            if updates.credentials is not None:
                set_clauses.append("credentials = ?")
                params.append(updates.credentials)
            
            if updates.platform_config is not None:
                set_clauses.append("platform_config = ?")
                params.append(json.dumps(updates.platform_config))
            
            if updates.is_default is not None:
                set_clauses.append("is_default = ?")
                params.append(updates.is_default)
            
            if updates.enabled is not None:
                set_clauses.append("enabled = ?")
                params.append(updates.enabled)
            
            if updates.shared_by is not None:
                set_clauses.append("shared_by = ?")
                params.append(updates.shared_by)
            
            if len(set_clauses) == 1:  # Only timestamp
                return True
            
            params.extend([user_id, recipient_id])
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute(f'''
                    UPDATE unified_recipients 
                    SET {', '.join(set_clauses)}
                    WHERE user_id = ? AND id = ?
                ''', params)
                
                updated = cursor.rowcount > 0
                if updated:
                    logger.info(f"Updated recipient {recipient_id} for user {user_id}")
                
                return updated
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update recipient {recipient_id} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to update recipient: {e}")
    
    def remove_recipient(self, user_id: int, recipient_id: int) -> bool:
        """Remove recipient."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    DELETE FROM unified_recipients 
                    WHERE user_id = ? AND id = ?
                ''', (user_id, recipient_id))
                
                deleted = cursor.rowcount > 0
                if deleted:
                    logger.info(f"Removed recipient {recipient_id} for user {user_id}")
                
                return deleted
                
        except sqlite3.Error as e:
            logger.error(f"Failed to remove recipient {recipient_id} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to remove recipient: {e}")
    
    def toggle_recipient_enabled(self, user_id: int, recipient_id: int) -> bool:
        """Toggle recipient enabled status."""
        try:
            # Get current status
            recipient = self.get_recipient_by_id(user_id, recipient_id)
            if not recipient:
                return False
            
            # Toggle status
            new_status = not recipient.enabled
            updates = UnifiedRecipientUpdate(enabled=new_status)
            return self.update_recipient(user_id, recipient_id, updates)
            
        except Exception as e:
            logger.error(f"Failed to toggle recipient {recipient_id} for user {user_id}: {e}")
            return False
    
    # User preferences methods
    def get_user_preferences(self, user_id: int) -> Optional[UnifiedUserPreferences]:
        """Get user preferences."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT user_id, show_recipient_ui, telegram_notifications, 
                           owner_name, location, created_at, updated_at
                    FROM user_preferences_unified 
                    WHERE user_id = ?
                ''', (user_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return UnifiedUserPreferences(
                    user_id=row[0],
                    show_recipient_ui=bool(row[1]),
                    telegram_notifications=bool(row[2]),
                    owner_name=row[3],
                    location=row[4],
                    created_at=datetime.fromisoformat(row[5]) if row[5] else None,
                    updated_at=datetime.fromisoformat(row[6]) if row[6] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get preferences for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get preferences: {e}")
    
    def create_user_preferences(self, user_id: int, prefs: UnifiedUserPreferencesCreate) -> bool:
        """Create user preferences."""
        try:
            with self.db_manager.get_connection() as conn:
                conn.execute('''
                    INSERT INTO user_preferences_unified 
                    (user_id, show_recipient_ui, telegram_notifications, owner_name, location)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    user_id, prefs.show_recipient_ui, prefs.telegram_notifications,
                    prefs.owner_name, prefs.location
                ))
                
                logger.info(f"Created preferences for user {user_id}")
                return True
                
        except sqlite3.Error as e:
            logger.error(f"Failed to create preferences for user {user_id}: {e}")
            raise DatabaseError(f"Failed to create preferences: {e}")
    
    def update_user_preferences(self, user_id: int, updates: UnifiedUserPreferencesUpdate) -> bool:
        """Update user preferences."""
        try:
            set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
            params = []
            
            if updates.show_recipient_ui is not None:
                set_clauses.append("show_recipient_ui = ?")
                params.append(updates.show_recipient_ui)
            
            if updates.telegram_notifications is not None:
                set_clauses.append("telegram_notifications = ?")
                params.append(updates.telegram_notifications)
            
            if updates.owner_name is not None:
                set_clauses.append("owner_name = ?")
                params.append(updates.owner_name)
            
            if updates.location is not None:
                set_clauses.append("location = ?")
                params.append(updates.location)
            
            if len(set_clauses) == 1:  # Only timestamp
                return True
            
            params.append(user_id)
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute(f'''
                    UPDATE user_preferences_unified 
                    SET {', '.join(set_clauses)}
                    WHERE user_id = ?
                ''', params)
                
                updated = cursor.rowcount > 0
                if updated:
                    logger.info(f"Updated preferences for user {user_id}")
                
                return updated
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update preferences for user {user_id}: {e}")
            raise DatabaseError(f"Failed to update preferences: {e}")
    
    def delete_all_user_data(self, user_id: int) -> bool:
        """Delete all user data for GDPR compliance."""
        try:
            with self.db_manager.get_connection() as conn:
                # Delete all recipients
                conn.execute('DELETE FROM recipients WHERE user_id = ?', (user_id,))
                
                # Delete preferences
                conn.execute('DELETE FROM user_preferences_unified WHERE user_id = ?', (user_id,))
                
                logger.info(f"Deleted all data for user {user_id}")
                return True
                
        except sqlite3.Error as e:
            logger.error(f"Failed to delete all user data for {user_id}: {e}")
            return False