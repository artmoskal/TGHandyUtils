"""Clean recipient repositories - no legacy code."""

import json
import sqlite3
from typing import List, Optional, Dict, Any
from datetime import datetime

from database.connection import DatabaseManager
from models.recipient import (
    UserPlatform, UserPlatformCreate, UserPlatformUpdate,
    SharedRecipient, SharedRecipientCreate, SharedRecipientUpdate,
    UserPreferencesV2, UserPreferencesV2Create, UserPreferencesV2Update
)
from core.recipient_interfaces import (
    IUserPlatformRepository, ISharedRecipientRepository, IUserPreferencesV2Repository
)
from core.exceptions import DatabaseError
from core.logging import get_logger

logger = get_logger(__name__)




class UserPlatformRepository(IUserPlatformRepository):
    """Repository for user-owned platforms."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_user_platforms(self, user_id: int) -> List[UserPlatform]:
        """Get all platforms owned by user."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, telegram_user_id, platform_type, credentials, platform_config, 
                           enabled, created_at, updated_at
                    FROM user_platforms 
                    WHERE telegram_user_id = ?
                    ORDER BY platform_type
                ''', (user_id,))
                
                platforms = []
                for row in cursor.fetchall():
                    config = None
                    if row[4]:
                        try:
                            config = json.loads(row[4])
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid config JSON for platform {row[0]}")
                    
                    platforms.append(UserPlatform(
                        id=row[0],
                        telegram_user_id=row[1],
                        platform_type=row[2],
                        credentials=row[3],
                        platform_config=config,
                        enabled=bool(row[5]),
                        created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                        updated_at=datetime.fromisoformat(row[7]) if row[7] else None
                    ))
                
                return platforms
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get user platforms for {user_id}: {e}")
            raise DatabaseError(f"Failed to get user platforms: {e}")
    
    def get_enabled_platforms(self, user_id: int) -> List[UserPlatform]:
        """Get enabled platforms owned by user."""
        platforms = self.get_user_platforms(user_id)
        return [p for p in platforms if p.enabled]
    
    def get_platform_by_type(self, user_id: int, platform_type: str) -> Optional[UserPlatform]:
        """Get specific platform by type."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, telegram_user_id, platform_type, credentials, platform_config, 
                           enabled, created_at, updated_at
                    FROM user_platforms 
                    WHERE telegram_user_id = ? AND platform_type = ?
                ''', (user_id, platform_type))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                config = None
                if row[4]:
                    try:
                        config = json.loads(row[4])
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid config JSON for platform {row[0]}")
                
                return UserPlatform(
                    id=row[0],
                    telegram_user_id=row[1],
                    platform_type=row[2],
                    credentials=row[3],
                    platform_config=config,
                    enabled=bool(row[5]),
                    created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    updated_at=datetime.fromisoformat(row[7]) if row[7] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get platform {platform_type} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get platform: {e}")
    
    def add_platform(self, user_id: int, platform: UserPlatformCreate) -> int:
        """Add new platform. Returns platform ID."""
        try:
            config_json = None
            if platform.platform_config:
                config_json = json.dumps(platform.platform_config)
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO user_platforms 
                    (telegram_user_id, platform_type, credentials, platform_config, enabled)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    user_id, platform.platform_type, platform.credentials,
                    config_json, platform.enabled
                ))
                
                platform_id = cursor.lastrowid
                logger.info(f"Added platform {platform.platform_type} for user {user_id}")
                return platform_id
                
        except sqlite3.Error as e:
            logger.error(f"Failed to add platform for user {user_id}: {e}")
            raise DatabaseError(f"Failed to add platform: {e}")
    
    def update_platform(self, user_id: int, platform_type: str, updates: UserPlatformUpdate) -> bool:
        """Update platform."""
        try:
            set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
            params = []
            
            if updates.credentials is not None:
                set_clauses.append("credentials = ?")
                params.append(updates.credentials)
            
            if updates.platform_config is not None:
                set_clauses.append("platform_config = ?")
                params.append(json.dumps(updates.platform_config))
            
            if updates.enabled is not None:
                set_clauses.append("enabled = ?")
                params.append(updates.enabled)
            
            if len(set_clauses) == 1:  # Only timestamp
                return True
            
            params.extend([user_id, platform_type])
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute(f'''
                    UPDATE user_platforms 
                    SET {', '.join(set_clauses)}
                    WHERE telegram_user_id = ? AND platform_type = ?
                ''', params)
                
                updated = cursor.rowcount > 0
                if updated:
                    logger.info(f"Updated platform {platform_type} for user {user_id}")
                
                return updated
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update platform {platform_type} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to update platform: {e}")
    
    def remove_platform(self, user_id: int, platform_type: str) -> bool:
        """Remove platform."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    DELETE FROM user_platforms 
                    WHERE telegram_user_id = ? AND platform_type = ?
                ''', (user_id, platform_type))
                
                deleted = cursor.rowcount > 0
                if deleted:
                    logger.info(f"Removed platform {platform_type} for user {user_id}")
                
                return deleted
                
        except sqlite3.Error as e:
            logger.error(f"Failed to remove platform {platform_type} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to remove platform: {e}")


class SharedRecipientRepository(ISharedRecipientRepository):
    """Repository for shared recipients."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_shared_recipients(self, user_id: int) -> List[SharedRecipient]:
        """Get all shared recipients for user."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, telegram_user_id, name, platform_type, credentials, platform_config,
                           shared_by, enabled, created_at, updated_at
                    FROM shared_recipients 
                    WHERE telegram_user_id = ?
                    ORDER BY name
                ''', (user_id,))
                
                recipients = []
                for row in cursor.fetchall():
                    config = None
                    if row[5]:
                        try:
                            config = json.loads(row[5])
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid config JSON for recipient {row[0]}")
                    
                    recipients.append(SharedRecipient(
                        id=row[0],
                        telegram_user_id=row[1],
                        name=row[2],
                        platform_type=row[3],
                        credentials=row[4],
                        platform_config=config,
                        shared_by=row[6],
                        enabled=bool(row[7]),
                        created_at=datetime.fromisoformat(row[8]) if row[8] else None,
                        updated_at=datetime.fromisoformat(row[9]) if row[9] else None
                    ))
                
                return recipients
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get shared recipients for {user_id}: {e}")
            raise DatabaseError(f"Failed to get shared recipients: {e}")
    
    def get_enabled_recipients(self, user_id: int) -> List[SharedRecipient]:
        """Get enabled shared recipients for user."""
        recipients = self.get_shared_recipients(user_id)
        return [r for r in recipients if r.enabled]
    
    def get_recipient_by_id(self, user_id: int, recipient_id: int) -> Optional[SharedRecipient]:
        """Get specific shared recipient."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, telegram_user_id, name, platform_type, credentials, platform_config,
                           shared_by, enabled, created_at, updated_at
                    FROM shared_recipients 
                    WHERE telegram_user_id = ? AND id = ?
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
                
                return SharedRecipient(
                    id=row[0],
                    telegram_user_id=row[1],
                    name=row[2],
                    platform_type=row[3],
                    credentials=row[4],
                    platform_config=config,
                    shared_by=row[6],
                    enabled=bool(row[7]),
                    created_at=datetime.fromisoformat(row[8]) if row[8] else None,
                    updated_at=datetime.fromisoformat(row[9]) if row[9] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get shared recipient {recipient_id} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get shared recipient: {e}")
    
    def add_recipient(self, user_id: int, recipient: SharedRecipientCreate) -> int:
        """Add shared recipient. Returns recipient ID."""
        try:
            config_json = None
            if recipient.platform_config:
                config_json = json.dumps(recipient.platform_config)
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO shared_recipients 
                    (telegram_user_id, name, platform_type, credentials, platform_config, shared_by, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, recipient.name, recipient.platform_type, recipient.credentials,
                    config_json, recipient.shared_by, recipient.enabled
                ))
                
                recipient_id = cursor.lastrowid
                logger.info(f"Added shared recipient {recipient.name} for user {user_id}")
                return recipient_id
                
        except sqlite3.Error as e:
            logger.error(f"Failed to add shared recipient for user {user_id}: {e}")
            raise DatabaseError(f"Failed to add shared recipient: {e}")
    
    def update_recipient(self, user_id: int, recipient_id: int, updates: SharedRecipientUpdate) -> bool:
        """Update shared recipient."""
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
            
            if updates.shared_by is not None:
                set_clauses.append("shared_by = ?")
                params.append(updates.shared_by)
            
            if updates.enabled is not None:
                set_clauses.append("enabled = ?")
                params.append(updates.enabled)
            
            if len(set_clauses) == 1:  # Only timestamp
                return True
            
            params.extend([user_id, recipient_id])
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute(f'''
                    UPDATE shared_recipients 
                    SET {', '.join(set_clauses)}
                    WHERE telegram_user_id = ? AND id = ?
                ''', params)
                
                updated = cursor.rowcount > 0
                if updated:
                    logger.info(f"Updated shared recipient {recipient_id} for user {user_id}")
                
                return updated
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update shared recipient {recipient_id} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to update shared recipient: {e}")
    
    def remove_recipient(self, user_id: int, recipient_id: int) -> bool:
        """Remove shared recipient."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    DELETE FROM shared_recipients 
                    WHERE telegram_user_id = ? AND id = ?
                ''', (user_id, recipient_id))
                
                deleted = cursor.rowcount > 0
                if deleted:
                    logger.info(f"Removed shared recipient {recipient_id} for user {user_id}")
                
                return deleted
                
        except sqlite3.Error as e:
            logger.error(f"Failed to remove shared recipient {recipient_id} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to remove shared recipient: {e}")


class UserPreferencesV2Repository(IUserPreferencesV2Repository):
    """Repository for user preferences."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_preferences(self, user_id: int) -> Optional[UserPreferencesV2]:
        """Get user preferences."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT telegram_user_id, default_recipients, show_recipient_ui, 
                           telegram_notifications, owner_name, location, created_at, updated_at
                    FROM user_preferences_v2 
                    WHERE telegram_user_id = ?
                ''', (user_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                default_recipients = []
                if row[1]:
                    try:
                        default_recipients = json.loads(row[1])
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid default_recipients JSON for user {user_id}")
                
                return UserPreferencesV2(
                    telegram_user_id=row[0],
                    default_recipients=default_recipients,
                    show_recipient_ui=bool(row[2]),
                    telegram_notifications=bool(row[3]),
                    owner_name=row[4],
                    location=row[5],
                    created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    updated_at=datetime.fromisoformat(row[7]) if row[7] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get preferences for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get preferences: {e}")
    
    def create_preferences(self, user_id: int, prefs: UserPreferencesV2Create) -> bool:
        """Create user preferences."""
        try:
            default_recipients_json = None
            if prefs.default_recipients:
                default_recipients_json = json.dumps(prefs.default_recipients)
            
            with self.db_manager.get_connection() as conn:
                conn.execute('''
                    INSERT INTO user_preferences_v2 
                    (telegram_user_id, default_recipients, show_recipient_ui, telegram_notifications, owner_name, location)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, default_recipients_json, 
                    prefs.show_recipient_ui, prefs.telegram_notifications,
                    prefs.owner_name, prefs.location
                ))
                
                logger.info(f"Created preferences for user {user_id}")
                return True
                
        except sqlite3.Error as e:
            logger.error(f"Failed to create preferences for user {user_id}: {e}")
            raise DatabaseError(f"Failed to create preferences: {e}")
    
    def update_preferences(self, user_id: int, updates: UserPreferencesV2Update) -> bool:
        """Update user preferences."""
        logger.debug(f"Updating preferences for user {user_id}: {updates}")
        try:
            set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
            params = []
            
            if updates.default_recipients is not None:
                set_clauses.append("default_recipients = ?")
                params.append(json.dumps(updates.default_recipients))
                logger.debug(f"Updating default_recipients for user {user_id}")
            
            if updates.show_recipient_ui is not None:
                set_clauses.append("show_recipient_ui = ?")
                params.append(updates.show_recipient_ui)
                logger.debug(f"Updating show_recipient_ui for user {user_id}: {updates.show_recipient_ui}")
            
            if updates.telegram_notifications is not None:
                set_clauses.append("telegram_notifications = ?")
                params.append(updates.telegram_notifications)
                logger.debug(f"Updating telegram_notifications for user {user_id}")
            
            if updates.owner_name is not None:
                set_clauses.append("owner_name = ?")
                params.append(updates.owner_name)
                logger.debug(f"Updating owner_name for user {user_id}")
            
            if updates.location is not None:
                set_clauses.append("location = ?")
                params.append(updates.location)
                logger.debug(f"Updating location for user {user_id}")
            
            if len(set_clauses) == 1:  # Only timestamp
                logger.debug(f"No fields to update for user {user_id}")
                return True
            
            params.append(user_id)
            
            query = f'''
                UPDATE user_preferences_v2 
                SET {', '.join(set_clauses)}
                WHERE telegram_user_id = ?
            '''
            logger.debug(f"Executing update query for user {user_id}: {query} with params: {params}")
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute(query, params)
                
                updated = cursor.rowcount > 0
                logger.debug(f"Update result for user {user_id}: rowcount={cursor.rowcount}, updated={updated}")
                
                if updated:
                    logger.info(f"Successfully updated preferences for user {user_id}")
                else:
                    logger.warning(f"No rows updated for user {user_id} - user may not exist in database")
                
                return updated
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update preferences for user {user_id}: {e}")
            raise DatabaseError(f"Failed to update preferences: {e}")