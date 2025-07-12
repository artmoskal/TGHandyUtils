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
from models.auth_request import AuthRequest
from models.shared_authorization import SharedAuthorization
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
                           is_personal, is_default, enabled, created_at, updated_at
                    FROM recipients 
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
                        created_at=datetime.fromisoformat(row[9]) if row[9] else None,
                        updated_at=datetime.fromisoformat(row[10]) if row[10] else None
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
        """Get default recipients for task creation with smart fallback."""
        recipients = self.get_all_recipients(user_id)
        
        # First try to get explicitly marked default recipients
        defaults = [r for r in recipients if r.is_default and r.enabled]
        
        # Return empty list if no defaults are explicitly set
        # This respects user's choice to have no default recipients
        if not defaults:
            logger.info(f"No default recipients found for user {user_id}, respecting user choice")
        
        return defaults
    
    def get_recipient_by_id(self, user_id: int, recipient_id: int) -> Optional[UnifiedRecipient]:
        """Get specific recipient by ID."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, user_id, name, platform_type, credentials, platform_config,
                           is_personal, is_default, enabled, created_at, updated_at
                    FROM recipients 
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
                    created_at=datetime.fromisoformat(row[9]) if row[9] else None,
                    updated_at=datetime.fromisoformat(row[10]) if row[10] else None
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
                    INSERT INTO recipients 
                    (user_id, name, platform_type, credentials, platform_config, 
                     is_personal, is_default, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, recipient.name, recipient.platform_type, recipient.credentials,
                    config_json, recipient.is_personal, recipient.is_default, recipient.enabled
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
            
            if updates.enabled is not None:
                set_clauses.append("enabled = ?")
                params.append(updates.enabled)
            
            if updates.is_default is not None:
                set_clauses.append("is_default = ?")
                params.append(updates.is_default)
            
            if len(set_clauses) == 1:  # Only timestamp
                return True
            
            params.extend([user_id, recipient_id])
            
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute(f'''
                    UPDATE recipients 
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
                    DELETE FROM recipients 
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
    
    # User preferences methods moved to UserPreferencesRepository
    
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

    # Authentication request methods
    def create_auth_request(self, requester_user_id: int, target_user_id: int,
                           platform_type: str, recipient_name: str, expires_at: datetime) -> int:
        """Create new authentication request."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO auth_requests 
                    (requester_user_id, target_user_id, platform_type, recipient_name, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (requester_user_id, target_user_id, platform_type, recipient_name, expires_at))
                
                auth_request_id = cursor.lastrowid
                logger.info(f"Created auth request {auth_request_id}")
                return auth_request_id
                
        except sqlite3.Error as e:
            logger.error(f"Error creating auth request: {e}")
            raise DatabaseError(f"Failed to create auth request: {e}")

    def get_auth_request_by_id(self, auth_request_id: int) -> Optional[AuthRequest]:
        """Get authentication request by ID."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, requester_user_id, target_user_id, platform_type, recipient_name,
                           status, expires_at, completed_recipient_id, created_at, updated_at
                    FROM auth_requests
                    WHERE id = ?
                ''', (auth_request_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return AuthRequest(
                    id=row[0],
                    requester_user_id=row[1],
                    target_user_id=row[2],
                    platform_type=row[3],
                    recipient_name=row[4],
                    status=row[5],
                    expires_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    completed_recipient_id=row[7],
                    created_at=datetime.fromisoformat(row[8]) if row[8] else None,
                    updated_at=datetime.fromisoformat(row[9]) if row[9] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Error getting auth request {auth_request_id}: {e}")
            raise DatabaseError(f"Failed to get auth request: {e}")

    def get_pending_auth_requests_for_user(self, user_id: int) -> List[AuthRequest]:
        """Get pending authentication requests targeting a user."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, requester_user_id, target_user_id, platform_type, recipient_name,
                           status, expires_at, completed_recipient_id, created_at, updated_at
                    FROM auth_requests
                    WHERE target_user_id = ? AND status = 'pending' AND expires_at > ?
                    ORDER BY created_at DESC
                ''', (user_id, datetime.utcnow()))
                
                requests = []
                for row in cursor.fetchall():
                    requests.append(AuthRequest(
                        id=row[0],
                        requester_user_id=row[1],
                        target_user_id=row[2],
                        platform_type=row[3],
                        recipient_name=row[4],
                        status=row[5],
                        expires_at=datetime.fromisoformat(row[6]) if row[6] else None,
                        completed_recipient_id=row[7],
                        created_at=datetime.fromisoformat(row[8]) if row[8] else None,
                        updated_at=datetime.fromisoformat(row[9]) if row[9] else None
                    ))
                
                return requests
                
        except sqlite3.Error as e:
            logger.error(f"Error getting pending auth requests for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get pending auth requests: {e}")

    def get_auth_requests_by_requester(self, requester_user_id: int) -> List[AuthRequest]:
        """Get all auth requests created by a user."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, requester_user_id, target_user_id, platform_type, recipient_name,
                           status, expires_at, completed_recipient_id, created_at, updated_at
                    FROM auth_requests
                    WHERE requester_user_id = ?
                    ORDER BY created_at DESC
                ''', (requester_user_id,))
                
                requests = []
                for row in cursor.fetchall():
                    requests.append(AuthRequest(
                        id=row[0],
                        requester_user_id=row[1],
                        target_user_id=row[2],
                        platform_type=row[3],
                        recipient_name=row[4],
                        status=row[5],
                        expires_at=datetime.fromisoformat(row[6]) if row[6] else None,
                        completed_recipient_id=row[7],
                        created_at=datetime.fromisoformat(row[8]) if row[8] else None,
                        updated_at=datetime.fromisoformat(row[9]) if row[9] else None
                    ))
                
                return requests
                
        except sqlite3.Error as e:
            logger.error(f"Error getting auth requests by requester {requester_user_id}: {e}")
            raise DatabaseError(f"Failed to get auth requests: {e}")

    def update_auth_request_status(self, auth_request_id: int, status: str, 
                                 completed_recipient_id: int = None) -> bool:
        """Update authentication request status."""
        try:
            with self.db_manager.get_connection() as conn:
                if completed_recipient_id:
                    cursor = conn.execute('''
                        UPDATE auth_requests 
                        SET status = ?, completed_recipient_id = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (status, completed_recipient_id, auth_request_id))
                else:
                    cursor = conn.execute('''
                        UPDATE auth_requests 
                        SET status = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (status, auth_request_id))
                
                success = cursor.rowcount > 0
                if success:
                    logger.info(f"Updated auth request {auth_request_id} status to {status}")
                
                return success
                
        except sqlite3.Error as e:
            logger.error(f"Error updating auth request status: {e}")
            raise DatabaseError(f"Failed to update auth request: {e}")

    def cleanup_expired_auth_requests(self) -> int:
        """Mark expired auth requests as expired."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    UPDATE auth_requests 
                    SET status = 'expired', updated_at = CURRENT_TIMESTAMP
                    WHERE status = 'pending' AND expires_at < ?
                ''', (datetime.utcnow(),))
                
                count = cursor.rowcount
                if count > 0:
                    logger.info(f"Marked {count} auth requests as expired")
                
                return count
                
        except sqlite3.Error as e:
            logger.error(f"Error cleaning up expired auth requests: {e}")
            raise DatabaseError(f"Failed to cleanup expired requests: {e}")

    def add_personal_recipient(self, user_id: int, name: str, platform_type: str, 
                             credentials: str, platform_config: str = None) -> int:
        """Add personal recipient (helper method for auth completion)."""
        config_dict = None
        if platform_config:
            try:
                config_dict = json.loads(platform_config) if isinstance(platform_config, str) else platform_config
            except json.JSONDecodeError:
                logger.warning(f"Invalid platform config JSON: {platform_config}")
        
        from models.unified_recipient import UnifiedRecipientCreate
        recipient = UnifiedRecipientCreate(
            name=name,
            platform_type=platform_type,
            credentials=credentials,
            platform_config=config_dict,
            is_personal=True,
            enabled=True
        )
        
        return self.add_recipient(user_id, recipient)

    def add_shared_recipient(self, user_id: int, name: str, platform_type: str, 
                           shared_authorization_id: int) -> int:
        """Add shared recipient (linked to authorization)."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO recipients 
                    (user_id, name, platform_type, credentials, is_personal, enabled, shared_authorization_id)
                    VALUES (?, ?, ?, '', ?, ?, ?)
                ''', (user_id, name, platform_type, False, True, shared_authorization_id))
                
                recipient_id = cursor.lastrowid
                logger.info(f"Added shared recipient {name} for user {user_id}")
                return recipient_id
                
        except sqlite3.Error as e:
            logger.error(f"Failed to add shared recipient for user {user_id}: {e}")
            raise DatabaseError(f"Failed to add shared recipient: {e}")

    # Shared authorization methods
    def create_shared_authorization(self, owner_user_id: int, grantee_user_id: int, 
                                  owner_recipient_id: int, permission_level: str = 'use') -> int:
        """Create shared authorization."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO shared_authorizations 
                    (owner_user_id, grantee_user_id, owner_recipient_id, permission_level)
                    VALUES (?, ?, ?, ?)
                ''', (owner_user_id, grantee_user_id, owner_recipient_id, permission_level))
                
                auth_id = cursor.lastrowid
                logger.info(f"Created shared authorization {auth_id}")
                return auth_id
                
        except sqlite3.Error as e:
            logger.error(f"Error creating shared authorization: {e}")
            raise DatabaseError(f"Failed to create shared authorization: {e}")

    def get_shared_authorization_by_id(self, auth_id: int) -> Optional[SharedAuthorization]:
        """Get shared authorization by ID."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, owner_user_id, grantee_user_id, owner_recipient_id, permission_level,
                           status, created_at, updated_at, last_used_at
                    FROM shared_authorizations
                    WHERE id = ?
                ''', (auth_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return SharedAuthorization(
                    id=row[0],
                    owner_user_id=row[1],
                    grantee_user_id=row[2],
                    owner_recipient_id=row[3],
                    permission_level=row[4],
                    status=row[5],
                    created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    updated_at=datetime.fromisoformat(row[7]) if row[7] else None,
                    last_used_at=datetime.fromisoformat(row[8]) if row[8] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Error getting shared authorization {auth_id}: {e}")
            raise DatabaseError(f"Failed to get shared authorization: {e}")

    def get_shared_authorizations_by_owner(self, owner_user_id: int) -> List[SharedAuthorization]:
        """Get all authorizations created by user (as owner)."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, owner_user_id, grantee_user_id, owner_recipient_id, permission_level,
                           status, created_at, updated_at, last_used_at
                    FROM shared_authorizations
                    WHERE owner_user_id = ?
                    ORDER BY created_at DESC
                ''', (owner_user_id,))
                
                authorizations = []
                for row in cursor.fetchall():
                    authorizations.append(SharedAuthorization(
                        id=row[0],
                        owner_user_id=row[1],
                        grantee_user_id=row[2],
                        owner_recipient_id=row[3],
                        permission_level=row[4],
                        status=row[5],
                        created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                        updated_at=datetime.fromisoformat(row[7]) if row[7] else None,
                        last_used_at=datetime.fromisoformat(row[8]) if row[8] else None
                    ))
                
                return authorizations
                
        except sqlite3.Error as e:
            logger.error(f"Error getting shared authorizations by owner {owner_user_id}: {e}")
            raise DatabaseError(f"Failed to get shared authorizations: {e}")

    def get_shared_authorizations_by_grantee(self, grantee_user_id: int) -> List[SharedAuthorization]:
        """Get all authorizations granted to user (as grantee)."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, owner_user_id, grantee_user_id, owner_recipient_id, permission_level,
                           status, created_at, updated_at, last_used_at
                    FROM shared_authorizations
                    WHERE grantee_user_id = ?
                    ORDER BY created_at DESC
                ''', (grantee_user_id,))
                
                authorizations = []
                for row in cursor.fetchall():
                    authorizations.append(SharedAuthorization(
                        id=row[0],
                        owner_user_id=row[1],
                        grantee_user_id=row[2],
                        owner_recipient_id=row[3],
                        permission_level=row[4],
                        status=row[5],
                        created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                        updated_at=datetime.fromisoformat(row[7]) if row[7] else None,
                        last_used_at=datetime.fromisoformat(row[8]) if row[8] else None
                    ))
                
                return authorizations
                
        except sqlite3.Error as e:
            logger.error(f"Error getting shared authorizations by grantee {grantee_user_id}: {e}")
            raise DatabaseError(f"Failed to get shared authorizations: {e}")

    def update_shared_authorization_status(self, auth_id: int, status: str) -> bool:
        """Update shared authorization status."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    UPDATE shared_authorizations 
                    SET status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (status, auth_id))
                
                success = cursor.rowcount > 0
                if success:
                    logger.info(f"Updated shared authorization {auth_id} status to {status}")
                
                return success
                
        except sqlite3.Error as e:
            logger.error(f"Error updating shared authorization status: {e}")
            raise DatabaseError(f"Failed to update shared authorization: {e}")

    def get_shared_authorization(self, owner_user_id: int, grantee_user_id: int, 
                               owner_recipient_id: int) -> Optional[SharedAuthorization]:
        """Get existing shared authorization."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, owner_user_id, grantee_user_id, owner_recipient_id, permission_level,
                           status, created_at, updated_at, last_used_at
                    FROM shared_authorizations
                    WHERE owner_user_id = ? AND grantee_user_id = ? AND owner_recipient_id = ?
                ''', (owner_user_id, grantee_user_id, owner_recipient_id))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return SharedAuthorization(
                    id=row[0],
                    owner_user_id=row[1],
                    grantee_user_id=row[2],
                    owner_recipient_id=row[3],
                    permission_level=row[4],
                    status=row[5],
                    created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    updated_at=datetime.fromisoformat(row[7]) if row[7] else None,
                    last_used_at=datetime.fromisoformat(row[8]) if row[8] else None
                )
                
        except sqlite3.Error as e:
            logger.error(f"Error getting shared authorization: {e}")
            raise DatabaseError(f"Failed to get shared authorization: {e}")