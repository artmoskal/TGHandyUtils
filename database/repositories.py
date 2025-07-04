"""Database repositories implementing the Repository pattern."""

import json
import sqlite3
from typing import List, Optional, Dict, Any
from datetime import datetime

from database.connection import DatabaseManager
from models.task import TaskDB, TaskCreate, TaskUpdate
from core.exceptions import DatabaseError
from core.logging import get_logger
from core.interfaces import ITaskRepository

logger = get_logger(__name__)

class BaseRepository:
    """Base repository with common database operations."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

class TaskRepository(BaseRepository, ITaskRepository):
    """Repository for task-related database operations."""
    
    def create(self, user_id: int, chat_id: int, message_id: int, 
               task_data: TaskCreate, platform_task_id: Optional[str] = None, 
               platform_type: str = 'todoist', screenshot_file_id: Optional[str] = None,
               screenshot_filename: Optional[str] = None) -> Optional[int]:
        """Create a new task in the database."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO tasks (
                        user_id, chat_id, message_id, task_title, task_description, 
                        due_time, platform_task_id, platform_type, screenshot_file_id, screenshot_filename
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, chat_id, message_id, task_data.title, 
                    task_data.description, task_data.due_time, 
                    platform_task_id, platform_type, screenshot_file_id, screenshot_filename
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
                           task_description, due_time, platform_task_id, platform_type,
                           screenshot_file_id, screenshot_filename
                    FROM tasks ORDER BY due_time ASC
                ''')
                
                return [
                    TaskDB(
                        id=row[0], user_id=row[1], chat_id=row[2], message_id=row[3],
                        task_title=row[4], task_description=row[5], due_time=row[6],
                        platform_task_id=row[7], platform_type=row[8],
                        screenshot_file_id=row[9], screenshot_filename=row[10]
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
                           task_description, due_time, platform_task_id, platform_type,
                           screenshot_file_id, screenshot_filename
                    FROM tasks WHERE user_id = ? ORDER BY due_time ASC
                ''', (user_id,))
                
                return [
                    TaskDB(
                        id=row[0], user_id=row[1], chat_id=row[2], message_id=row[3],
                        task_title=row[4], task_description=row[5], due_time=row[6],
                        platform_task_id=row[7], platform_type=row[8],
                        screenshot_file_id=row[9], screenshot_filename=row[10]
                    )
                    for row in cursor.fetchall()
                ]
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get tasks for user {user_id}: {e}")
            raise DatabaseError(f"Failed to get tasks for user: {e}")
    
    def get_by_id(self, task_id: int) -> Optional[TaskDB]:
        """Get a single task by ID."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, user_id, chat_id, message_id, task_title, 
                           task_description, due_time, platform_task_id, platform_type,
                           screenshot_file_id, screenshot_filename
                    FROM tasks WHERE id = ?
                ''', (task_id,))
                
                row = cursor.fetchone()
                if row:
                    return TaskDB(
                        id=row[0], user_id=row[1], chat_id=row[2], message_id=row[3],
                        task_title=row[4], task_description=row[5], due_time=row[6],
                        platform_task_id=row[7], platform_type=row[8],
                        screenshot_file_id=row[9], screenshot_filename=row[10]
                    )
                return None
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get task {task_id}: {e}")
            raise DatabaseError(f"Failed to get task: {e}")
    
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

