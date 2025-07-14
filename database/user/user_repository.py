"""Repository for user data persistence."""

import sqlite3
from typing import Optional, List
from datetime import datetime

from database.connection import DatabaseManager
from models.user import User
from core.exceptions import DatabaseError
from core.logging import get_logger

logger = get_logger(__name__)


class UserRepository:
    """Repository for user data persistence following existing patterns."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def upsert_user(self, user_id: int, username: Optional[str], 
                   first_name: Optional[str], last_name: Optional[str]) -> None:
        """Insert or update user information.
        
        Args:
            user_id: Telegram user ID
            username: Telegram username (without @)
            first_name: User's first name
            last_name: User's last name
        """
        try:
            with self.db_manager.get_connection() as conn:
                conn.execute('''
                    INSERT INTO users (user_id, username, first_name, last_name, last_seen, created_at)
                    VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = COALESCE(excluded.username, users.username),
                        first_name = COALESCE(excluded.first_name, users.first_name),
                        last_name = COALESCE(excluded.last_name, users.last_name),
                        last_seen = datetime('now')
                ''', (user_id, username, first_name, last_name))
                
                logger.debug(f"Upserted user {user_id} with username {username}")
        except sqlite3.Error as e:
            logger.error(f"Failed to upsert user {user_id}: {e}")
            raise DatabaseError(f"Failed to upsert user: {e}")
    
    def find_by_username(self, username: str) -> Optional[User]:
        """Find user by username (case-insensitive, handles @ prefix).
        
        Args:
            username: Username to search for (with or without @)
            
        Returns:
            User object if found, None otherwise
        """
        # Clean username - remove @ and convert to lowercase
        clean_username = username.strip().lstrip('@').lower()
        
        if not clean_username:
            return None
        
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT user_id, username, first_name, last_name, last_seen, created_at
                    FROM users
                    WHERE LOWER(username) = ?
                ''', (clean_username,))
                
                row = cursor.fetchone()
                if row:
                    return User(
                        user_id=row[0],
                        username=row[1],
                        first_name=row[2],
                        last_name=row[3],
                        last_seen=datetime.fromisoformat(row[4]),
                        created_at=datetime.fromisoformat(row[5])
                    )
                return None
        except sqlite3.Error as e:
            logger.error(f"Failed to find user by username {username}: {e}")
            raise DatabaseError(f"Failed to find user: {e}")
    
    def find_by_id(self, user_id: int) -> Optional[User]:
        """Find user by Telegram user ID.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            User object if found, None otherwise
        """
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT user_id, username, first_name, last_name, last_seen, created_at
                    FROM users
                    WHERE user_id = ?
                ''', (user_id,))
                
                row = cursor.fetchone()
                if row:
                    return User(
                        user_id=row[0],
                        username=row[1],
                        first_name=row[2],
                        last_name=row[3],
                        last_seen=datetime.fromisoformat(row[4]),
                        created_at=datetime.fromisoformat(row[5])
                    )
                return None
        except sqlite3.Error as e:
            logger.error(f"Failed to find user by id {user_id}: {e}")
            raise DatabaseError(f"Failed to find user: {e}")
    
    def update_last_seen(self, user_id: int) -> None:
        """Update user's last seen timestamp.
        
        Args:
            user_id: Telegram user ID
        """
        try:
            with self.db_manager.get_connection() as conn:
                conn.execute('''
                    UPDATE users 
                    SET last_seen = datetime('now')
                    WHERE user_id = ?
                ''', (user_id,))
        except sqlite3.Error as e:
            logger.error(f"Failed to update last seen for user {user_id}: {e}")
            raise DatabaseError(f"Failed to update last seen: {e}")
    
    def get_all_users(self) -> List[User]:
        """Get all users in the system.
        
        Returns:
            List of all users
        """
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT user_id, username, first_name, last_name, last_seen, created_at
                    FROM users
                    ORDER BY last_seen DESC
                ''')
                
                users = []
                for row in cursor.fetchall():
                    users.append(User(
                        user_id=row[0],
                        username=row[1],
                        first_name=row[2],
                        last_name=row[3],
                        last_seen=datetime.fromisoformat(row[4]),
                        created_at=datetime.fromisoformat(row[5])
                    ))
                return users
        except sqlite3.Error as e:
            logger.error(f"Failed to get all users: {e}")
            raise DatabaseError(f"Failed to get users: {e}")