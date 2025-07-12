"""Auth request repository - extracted from god class."""

import sqlite3
from typing import List, Optional
from datetime import datetime

from database.connection import DatabaseManager
from models.auth_request import AuthRequest
from core.interfaces import IAuthRequestRepository
from core.exceptions import DatabaseError
from core.logging import get_logger

logger = get_logger(__name__)


class AuthRequestRepository(IAuthRequestRepository):
    """Focused repository for auth request operations."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def create_auth_request(self, requester_user_id: int, target_user_id: int, 
                           recipient_id: int, permissions: List[str]) -> Optional[int]:
        """Create new auth request."""
        # Basic implementation - will be refined after testing
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO auth_requests 
                    (requester_user_id, target_user_id, platform_type, recipient_name, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (requester_user_id, target_user_id, "placeholder", "placeholder", datetime.now()))
                
                auth_request_id = cursor.lastrowid
                logger.info(f"Created auth request {auth_request_id}")
                return auth_request_id
                
        except sqlite3.Error as e:
            logger.error(f"Error creating auth request: {e}")
            raise DatabaseError(f"Failed to create auth request: {e}")
    
    def get_pending_auth_requests_for_user(self, user_id: int) -> List[AuthRequest]:
        """Get pending auth requests for user."""
        # Basic implementation - will be refined after testing
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, requester_user_id, target_user_id, platform_type, recipient_name,
                           status, expires_at, completed_recipient_id, created_at, updated_at
                    FROM auth_requests
                    WHERE target_user_id = ? AND status = 'pending'
                ''', (user_id,))
                
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
            logger.error(f"Failed to get pending auth requests for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get pending auth requests: {e}")
    
    def update_auth_request_status(self, auth_request_id: int, status: str, 
                                 reviewer_user_id: Optional[int] = None) -> bool:
        """Update auth request status."""
        # Basic implementation - will be refined after testing
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    UPDATE auth_requests 
                    SET status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (status, auth_request_id))
                
                updated = cursor.rowcount > 0
                if updated:
                    logger.info(f"Updated auth request {auth_request_id} to status {status}")
                
                return updated
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update auth request {auth_request_id}: {e}")
            raise DatabaseError(f"Failed to update auth request: {e}")