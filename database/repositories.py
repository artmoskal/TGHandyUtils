"""Database repositories implementing the Repository pattern."""

import json
import sqlite3
from typing import List, Optional, Dict, Any
from datetime import datetime

from database.connection import DatabaseManager
from models.task import TaskDB, TaskCreate, TaskUpdate
from models.user import UserDB, UserCreate, UserUpdate
from core.exceptions import DatabaseError
from core.logging import get_logger
from core.interfaces import ITaskRepository, IUserRepository

logger = get_logger(__name__)

class BaseRepository:
    """Base repository with common database operations."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

class TaskRepository(BaseRepository, ITaskRepository):
    """Repository for task-related database operations."""
    
    def create(self, user_id: int, chat_id: int, message_id: int, 
               task_data: TaskCreate, platform_task_id: Optional[str] = None, 
               platform_type: str = 'todoist') -> Optional[int]:
        """Create a new task in the database."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO tasks (
                        user_id, chat_id, message_id, task_title, task_description, 
                        due_time, platform_task_id, platform_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, chat_id, message_id, task_data.title, 
                    task_data.description, task_data.due_time, 
                    platform_task_id, platform_type
                ))
                
                task_id = cursor.lastrowid
                logger.info(f"Created task {task_id} for user {user_id}")
                return task_id
                
        except sqlite3.Error as e:
            logger.error(f"Failed to create task: {e}")
            raise DatabaseError(f"Failed to create task: {e}")
    
    def get_all(self) -> List[TaskDB]:
        """Get all tasks from the database."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, user_id, chat_id, message_id, task_title, 
                           task_description, due_time, platform_task_id, platform_type
                    FROM tasks ORDER BY due_time ASC
                ''')
                
                return [
                    TaskDB(
                        id=row[0], user_id=row[1], chat_id=row[2], message_id=row[3],
                        task_title=row[4], task_description=row[5], due_time=row[6],
                        platform_task_id=row[7], platform_type=row[8]
                    )
                    for row in cursor.fetchall()
                ]
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get tasks: {e}")
            raise DatabaseError(f"Failed to get tasks: {e}")
    
    def get_by_user(self, user_id: int) -> List[TaskDB]:
        """Get all tasks for a specific user."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, user_id, chat_id, message_id, task_title, 
                           task_description, due_time, platform_task_id, platform_type
                    FROM tasks WHERE user_id = ? ORDER BY due_time ASC
                ''', (user_id,))
                
                return [
                    TaskDB(
                        id=row[0], user_id=row[1], chat_id=row[2], message_id=row[3],
                        task_title=row[4], task_description=row[5], due_time=row[6],
                        platform_task_id=row[7], platform_type=row[8]
                    )
                    for row in cursor.fetchall()
                ]
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get tasks for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get tasks for user: {e}")
    
    def delete(self, task_id: int) -> bool:
        """Delete a task from the database."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
                deleted = cursor.rowcount > 0
                
                if deleted:
                    logger.info(f"Deleted task {task_id}")
                else:
                    logger.warning(f"Task {task_id} not found for deletion")
                
                return deleted
                
        except sqlite3.Error as e:
            logger.error(f"Failed to delete task {task_id}: {e}")
            raise DatabaseError(f"Failed to delete task: {e}")
    
    def update_platform_id(self, task_id: int, platform_task_id: str, platform_type: str) -> bool:
        """Update task with platform-specific ID."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    UPDATE tasks 
                    SET platform_task_id = ?, platform_type = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (platform_task_id, platform_type, task_id))
                
                updated = cursor.rowcount > 0
                
                if updated:
                    logger.info(f"Updated task {task_id} with platform ID {platform_task_id}")
                else:
                    logger.warning(f"Task {task_id} not found for platform ID update")
                
                return updated
                
        except sqlite3.Error as e:
            logger.error(f"Failed to update task platform ID: {e}")
            raise DatabaseError(f"Failed to update task platform ID: {e}")

class UserRepository(BaseRepository, IUserRepository):
    """Repository for user-related database operations."""
    
    def create_or_update(self, user_data: UserCreate) -> bool:
        """Create or update user information."""
        try:
            platform_settings_json = None
            if user_data.platform_settings:
                platform_settings_json = json.dumps(user_data.platform_settings)
            
            with self.db_manager.get_connection() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO users 
                    (telegram_user_id, platform_token, platform_type, owner_name, location, platform_settings)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    user_data.telegram_user_id, user_data.platform_token, 
                    user_data.platform_type, user_data.owner_name, 
                    user_data.location, platform_settings_json
                ))
                
                logger.info(f"Created/updated user {user_data.telegram_user_id}")
                return True
                
        except sqlite3.Error as e:
            logger.error(f"Failed to create/update user: {e}")
            raise DatabaseError(f"Failed to create/update user: {e}")
    
    def get_by_telegram_id(self, telegram_user_id: int) -> Optional[UserDB]:
        """Get user by Telegram user ID."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, telegram_user_id, platform_token, platform_type, 
                           owner_name, location, platform_settings
                    FROM users WHERE telegram_user_id = ?
                ''', (telegram_user_id,))
                
                row = cursor.fetchone()
                if row:
                    return UserDB(
                        id=row[0], telegram_user_id=row[1], platform_token=row[2],
                        platform_type=row[3], owner_name=row[4], location=row[5],
                        platform_settings=row[6]
                    )
                
                return None
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get user {telegram_user_id}: {e}")
            raise DatabaseError(f"Failed to get user: {e}")
    
    def get_platform_info(self, telegram_user_id: int) -> Optional[Dict[str, Any]]:
        """Get user's platform information."""
        user = self.get_by_telegram_id(telegram_user_id)
        if user:
            platform_settings = None
            if user.platform_settings:
                try:
                    platform_settings = json.loads(user.platform_settings)
                except json.JSONDecodeError:
                    logger.error(f"Invalid platform settings JSON for user {telegram_user_id}")
            
            return {
                'platform_token': user.platform_token,
                'platform_type': user.platform_type,
                'owner_name': user.owner_name,
                'location': user.location,
                'platform_settings': platform_settings
            }
        
        return None
    
    def get_platform_token(self, telegram_user_id: int) -> Optional[str]:
        """Get user's platform token."""
        user = self.get_by_telegram_id(telegram_user_id)
        return user.platform_token if user else None
    
    def get_platform_type(self, telegram_user_id: int) -> str:
        """Get user's platform type, defaulting to 'todoist'."""
        user = self.get_by_telegram_id(telegram_user_id)
        return user.platform_type if user else 'todoist'
    
    def delete(self, telegram_user_id: int) -> bool:
        """Delete user and all associated data."""
        try:
            with self.db_manager.get_connection() as conn:
                # Delete user's tasks first
                conn.execute('DELETE FROM tasks WHERE user_id = ?', (telegram_user_id,))
                
                # Delete user
                cursor = conn.execute('DELETE FROM users WHERE telegram_user_id = ?', (telegram_user_id,))
                deleted = cursor.rowcount > 0
                
                if deleted:
                    logger.info(f"Deleted user {telegram_user_id} and all associated data")
                else:
                    logger.warning(f"User {telegram_user_id} not found for deletion")
                
                return deleted
                
        except sqlite3.Error as e:
            logger.error(f"Failed to delete user {telegram_user_id}: {e}")
            raise DatabaseError(f"Failed to delete user: {e}")

# Remove global instances - use DI container instead