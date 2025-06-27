"""Partner repository for managing user partners and preferences."""

import json
import sqlite3
from typing import List, Optional, Dict, Any
from datetime import datetime

from database.connection import DatabaseManager
from models.partner import Partner, PartnerCreate, PartnerUpdate, UserPreferences, UserPreferencesCreate, UserPreferencesUpdate
from core.exceptions import DatabaseError
from core.logging import get_logger

logger = get_logger(__name__)


class PartnerRepository:
    """Repository for partner-related database operations."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_user_partners(self, user_id: int) -> List[Partner]:
        """Get all partners for a user."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT partner_id, name, platform, credentials, platform_config, 
                           is_self, enabled, created_at, updated_at
                    FROM user_partners 
                    WHERE user_id = ? 
                    ORDER BY is_self DESC, name ASC
                ''', (user_id,))
                
                partners = []
                for row in cursor.fetchall():
                    platform_config = None
                    if row[4]:  # platform_config
                        try:
                            platform_config = json.loads(row[4])
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid platform_config JSON for partner {row[0]}")
                    
                    partners.append(Partner(
                        id=row[0],
                        name=row[1],
                        platform=row[2],
                        credentials=row[3],
                        platform_config=platform_config,
                        is_self=bool(row[5]),
                        enabled=bool(row[6]),
                        created_at=datetime.fromisoformat(row[7]) if row[7] else None,
                        updated_at=datetime.fromisoformat(row[8]) if row[8] else None
                    ))
                
                return partners
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get partners for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get partners: {e}")
    
    def get_enabled_partners(self, user_id: int) -> List[Partner]:
        """Get only enabled partners for a user."""
        partners = self.get_user_partners(user_id)
        return [p for p in partners if p.enabled]
    
    def get_self_partner(self, user_id: int) -> Optional[Partner]:
        """Get the user's self partner."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT partner_id, name, platform, credentials, platform_config, 
                           is_self, enabled, created_at, updated_at
                    FROM user_partners 
                    WHERE user_id = ? AND is_self = 1
                    LIMIT 1
                ''', (user_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                platform_config = None
                if row[4]:  # platform_config
                    try:
                        platform_config = json.loads(row[4])
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid platform_config JSON for self partner")
                
                return Partner(
                    id=row[0],
                    name=row[1],
                    platform=row[2],
                    credentials=row[3],
                    platform_config=platform_config,
                    is_self=bool(row[5]),
                    enabled=bool(row[6]),
                    created_at=datetime.fromisoformat(row[7]) if row[7] else None,
                    updated_at=datetime.fromisoformat(row[8]) if row[8] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get self partner for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get self partner: {e}")
    
    def get_partner_by_id(self, user_id: int, partner_id: str) -> Optional[Partner]:
        """Get a specific partner by ID."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT partner_id, name, platform, credentials, platform_config, 
                           is_self, enabled, created_at, updated_at
                    FROM user_partners 
                    WHERE user_id = ? AND partner_id = ?
                ''', (user_id, partner_id))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                platform_config = None
                if row[4]:  # platform_config
                    try:
                        platform_config = json.loads(row[4])
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid platform_config JSON for partner {partner_id}")
                
                return Partner(
                    id=row[0],
                    name=row[1],
                    platform=row[2],
                    credentials=row[3],
                    platform_config=platform_config,
                    is_self=bool(row[5]),
                    enabled=bool(row[6]),
                    created_at=datetime.fromisoformat(row[7]) if row[7] else None,
                    updated_at=datetime.fromisoformat(row[8]) if row[8] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get partner {partner_id} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get partner: {e}")
    
    def add_partner(self, user_id: int, partner: PartnerCreate) -> str:
        """Add a new partner for a user. Returns the generated partner_id."""
        try:
            # Generate partner_id
            if partner.is_self:
                partner_id = "self"
            else:
                # Generate unique ID based on name
                base_id = partner.name.lower().replace(" ", "_")
                partner_id = f"{base_id}_001"
                
                # Check for conflicts and increment
                existing_partners = self.get_user_partners(user_id)
                existing_ids = [p.id for p in existing_partners]
                counter = 1
                while partner_id in existing_ids:
                    counter += 1
                    partner_id = f"{base_id}_{counter:03d}"
            
            platform_config_json = None
            if partner.platform_config:
                platform_config_json = json.dumps(partner.platform_config)
            
            with self.db_manager.get_connection() as conn:
                conn.execute('''
                    INSERT INTO user_partners 
                    (user_id, partner_id, name, platform, credentials, platform_config, is_self, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, partner_id, partner.name, partner.platform, 
                    partner.credentials, platform_config_json, partner.is_self, partner.enabled
                ))
                
                logger.info(f"Added partner {partner_id} for user {user_id}")
                return partner_id
                
        except sqlite3.Error as e:
            logger.error(f"Failed to add partner for user {user_id}: {e}")
            raise DatabaseError(f"Failed to add partner: {e}")
    
    def update_partner(self, user_id: int, partner_id: str, updates: PartnerUpdate) -> bool:
        """Update a partner's information."""
        try:
            # Build dynamic update query
            set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
            params = []
            
            if updates.name is not None:
                set_clauses.append("name = ?")
                params.append(updates.name)
            
            if updates.platform is not None:
                set_clauses.append("platform = ?")
                params.append(updates.platform)
            
            if updates.credentials is not None:
                set_clauses.append("credentials = ?")
                params.append(updates.credentials)
            
            if updates.platform_config is not None:
                set_clauses.append("platform_config = ?")
                params.append(json.dumps(updates.platform_config))
            
            if updates.enabled is not None:
                set_clauses.append("enabled = ?")
                params.append(updates.enabled)
            
            if not set_clauses or len(set_clauses) == 1:  # Only timestamp update
                return True  # Nothing to update
            
            params.extend([user_id, partner_id])
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute(f'''
                    UPDATE user_partners 
                    SET {', '.join(set_clauses)}
                    WHERE user_id = ? AND partner_id = ?
                ''', params)
                
                updated = cursor.rowcount > 0
                if updated:
                    logger.info(f"Updated partner {partner_id} for user {user_id}")
                else:
                    logger.warning(f"Partner {partner_id} not found for user {user_id}")
                
                return updated
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update partner {partner_id} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to update partner: {e}")
    
    def delete_partner(self, user_id: int, partner_id: str) -> bool:
        """Delete a partner."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    DELETE FROM user_partners 
                    WHERE user_id = ? AND partner_id = ?
                ''', (user_id, partner_id))
                
                deleted = cursor.rowcount > 0
                if deleted:
                    logger.info(f"Deleted partner {partner_id} for user {user_id}")
                else:
                    logger.warning(f"Partner {partner_id} not found for user {user_id}")
                
                return deleted
                
        except sqlite3.Error as e:
            logger.error(f"Failed to delete partner {partner_id} for user {user_id}: {e}")
            raise DatabaseError(f"Failed to delete partner: {e}")
    
    def get_partners_by_ids(self, user_id: int, partner_ids: List[str]) -> List[Partner]:
        """Get multiple partners by their IDs."""
        if not partner_ids:
            return []
        
        try:
            placeholders = ','.join(['?'] * len(partner_ids))
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute(f'''
                    SELECT partner_id, name, platform, credentials, platform_config, 
                           is_self, enabled, created_at, updated_at
                    FROM user_partners 
                    WHERE user_id = ? AND partner_id IN ({placeholders})
                    ORDER BY is_self DESC, name ASC
                ''', [user_id] + partner_ids)
                
                partners = []
                for row in cursor.fetchall():
                    platform_config = None
                    if row[4]:  # platform_config
                        try:
                            platform_config = json.loads(row[4])
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid platform_config JSON for partner {row[0]}")
                    
                    partners.append(Partner(
                        id=row[0],
                        name=row[1],
                        platform=row[2],
                        credentials=row[3],
                        platform_config=platform_config,
                        is_self=bool(row[5]),
                        enabled=bool(row[6]),
                        created_at=datetime.fromisoformat(row[7]) if row[7] else None,
                        updated_at=datetime.fromisoformat(row[8]) if row[8] else None
                    ))
                
                return partners
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get partners by IDs for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get partners by IDs: {e}")


class UserPreferencesRepository:
    """Repository for user preferences."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_preferences(self, user_id: int) -> Optional[UserPreferences]:
        """Get user preferences."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT default_partners, show_sharing_ui, telegram_notifications, 
                           location, created_at, updated_at
                    FROM user_preferences 
                    WHERE user_id = ?
                ''', (user_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                default_partners = None
                if row[0]:  # default_partners
                    try:
                        default_partners = json.loads(row[0])
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid default_partners JSON for user {user_id}")
                
                return UserPreferences(
                    user_id=user_id,
                    default_partners=default_partners,
                    show_sharing_ui=bool(row[1]),
                    telegram_notifications=bool(row[2]),
                    location=row[3],
                    created_at=datetime.fromisoformat(row[4]) if row[4] else None,
                    updated_at=datetime.fromisoformat(row[5]) if row[5] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get preferences for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get preferences: {e}")
    
    def create_or_update_preferences(self, user_id: int, prefs: UserPreferencesCreate) -> bool:
        """Create or update user preferences."""
        try:
            default_partners_json = None
            if prefs.default_partners:
                default_partners_json = json.dumps(prefs.default_partners)
            
            with self.db_manager.get_connection() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO user_preferences 
                    (user_id, default_partners, show_sharing_ui, telegram_notifications, location)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    user_id, default_partners_json, prefs.show_sharing_ui, 
                    prefs.telegram_notifications, prefs.location
                ))
                
                logger.info(f"Updated preferences for user {user_id}")
                return True
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update preferences for user {user_id}: {e}")
            raise DatabaseError(f"Failed to update preferences: {e}")
    
    def update_preferences(self, user_id: int, updates: UserPreferencesUpdate) -> bool:
        """Update specific user preferences."""
        try:
            # Build dynamic update query
            set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
            params = []
            
            if updates.default_partners is not None:
                set_clauses.append("default_partners = ?")
                params.append(json.dumps(updates.default_partners))
            
            if updates.show_sharing_ui is not None:
                set_clauses.append("show_sharing_ui = ?")
                params.append(updates.show_sharing_ui)
            
            if updates.telegram_notifications is not None:
                set_clauses.append("telegram_notifications = ?")
                params.append(updates.telegram_notifications)
            
            if updates.location is not None:
                set_clauses.append("location = ?")
                params.append(updates.location)
            
            if len(set_clauses) == 1:  # Only timestamp update
                return True  # Nothing to update
            
            params.append(user_id)
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute(f'''
                    UPDATE user_preferences 
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