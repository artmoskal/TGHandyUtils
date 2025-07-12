"""User preferences repository - extracted from god class."""

import sqlite3
from typing import Optional
from datetime import datetime

from database.connection import DatabaseManager
from models.unified_recipient import (
    UnifiedUserPreferences, UnifiedUserPreferencesCreate, UnifiedUserPreferencesUpdate
)
from core.interfaces import IUserPreferencesRepository
from core.exceptions import DatabaseError
from core.logging import get_logger

logger = get_logger(__name__)


class UserPreferencesRepository(IUserPreferencesRepository):
    """Focused repository for user preferences only."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_preferences(self, user_id: int) -> Optional[UnifiedUserPreferences]:
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
    
    def create_preferences(self, user_id: int, prefs: UnifiedUserPreferencesCreate) -> bool:
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
    
    def update_preferences(self, user_id: int, updates: UnifiedUserPreferencesUpdate) -> bool:
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