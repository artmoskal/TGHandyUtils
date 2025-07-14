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
    
    def create_auth_request(self, requester_user_id: int, target_user_id: int,
                           platform_type: str, recipient_name: str, expires_at: datetime) -> AuthRequest:
        """Create a new authentication request.
        
        Args:
            requester_user_id: ID of user making the request
            target_user_id: ID of user who will authenticate
            platform_type: Type of platform (todoist, trello, etc.)
            recipient_name: Name for the recipient account
            expires_at: When the request expires
            
        Returns:
            Created AuthRequest object
        """
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO auth_requests 
                    (requester_user_id, target_user_id, platform_type, recipient_name, 
                     status, expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'pending', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ''', (requester_user_id, target_user_id, platform_type, recipient_name, 
                     expires_at.isoformat()))
                
                auth_request_id = cursor.lastrowid
                
                # Fetch the created request
                return self.get_auth_request_by_id(auth_request_id)
                
        except sqlite3.Error as e:
            logger.error(f"Failed to create auth request: {e}")
            raise DatabaseError(f"Failed to create auth request: {e}")
    
    def get_auth_request_by_id(self, auth_request_id: int) -> Optional[AuthRequest]:
        """Get auth request by ID.
        
        Args:
            auth_request_id: ID of the auth request
            
        Returns:
            AuthRequest if found, None otherwise
        """
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, requester_user_id, target_user_id, platform_type, 
                           recipient_name, status, expires_at, completed_recipient_id,
                           created_at, updated_at
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
                    expires_at=datetime.fromisoformat(row[6]),
                    completed_recipient_id=row[7],
                    created_at=datetime.fromisoformat(row[8]),
                    updated_at=datetime.fromisoformat(row[9])
                )
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get auth request {auth_request_id}: {e}")
            raise DatabaseError(f"Failed to get auth request: {e}")
    
    def get_pending_auth_requests(self, target_user_id: int) -> List[AuthRequest]:
        """Get all pending auth requests for a user.
        
        Args:
            target_user_id: ID of the target user
            
        Returns:
            List of pending AuthRequest objects
        """
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, requester_user_id, target_user_id, platform_type, 
                           recipient_name, status, expires_at, completed_recipient_id,
                           created_at, updated_at
                    FROM auth_requests
                    WHERE target_user_id = ? AND status = 'pending' 
                          AND expires_at > CURRENT_TIMESTAMP
                    ORDER BY created_at DESC
                ''', (target_user_id,))
                
                requests = []
                for row in cursor.fetchall():
                    requests.append(AuthRequest(
                        id=row[0],
                        requester_user_id=row[1],
                        target_user_id=row[2],
                        platform_type=row[3],
                        recipient_name=row[4],
                        status=row[5],
                        expires_at=datetime.fromisoformat(row[6]),
                        completed_recipient_id=row[7],
                        created_at=datetime.fromisoformat(row[8]),
                        updated_at=datetime.fromisoformat(row[9])
                    ))
                
                return requests
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get pending auth requests for user {target_user_id}: {e}")
            raise DatabaseError(f"Failed to get pending auth requests: {e}")
    
    def get_auth_requests_by_requester(self, requester_user_id: int) -> List[AuthRequest]:
        """Get all auth requests made by a user.
        
        Args:
            requester_user_id: ID of the requester
            
        Returns:
            List of AuthRequest objects
        """
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, requester_user_id, target_user_id, platform_type, 
                           recipient_name, status, expires_at, completed_recipient_id,
                           created_at, updated_at
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
                        expires_at=datetime.fromisoformat(row[6]),
                        completed_recipient_id=row[7],
                        created_at=datetime.fromisoformat(row[8]),
                        updated_at=datetime.fromisoformat(row[9])
                    ))
                
                return requests
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get auth requests by requester {requester_user_id}: {e}")
            raise DatabaseError(f"Failed to get auth requests: {e}")
    
    def update_auth_request_status(self, auth_request_id: int, status: str, 
                                  completed_recipient_id: Optional[int] = None) -> bool:
        """Update the status of an auth request.
        
        Args:
            auth_request_id: ID of the auth request
            status: New status (pending, completed, expired, cancelled)
            completed_recipient_id: ID of recipient if completed
            
        Returns:
            True if updated successfully
        """
        try:
            with self.db_manager.get_connection() as conn:
                if completed_recipient_id:
                    conn.execute('''
                        UPDATE auth_requests 
                        SET status = ?, completed_recipient_id = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (status, completed_recipient_id, auth_request_id))
                else:
                    conn.execute('''
                        UPDATE auth_requests 
                        SET status = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (status, auth_request_id))
                
                return True
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update auth request {auth_request_id} status: {e}")
            raise DatabaseError(f"Failed to update auth request status: {e}")