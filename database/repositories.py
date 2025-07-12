"""Database repositories implementing the Repository pattern."""

import json
import sqlite3
from typing import List, Optional, Dict, Any
from datetime import datetime

from database.connection import DatabaseManager
from models.task import TaskDB, TaskCreate, TaskUpdate, TaskRecipient
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
               task_data: TaskCreate, screenshot_file_id: Optional[str] = None) -> Optional[int]:
        """Create a new task in the database (platform tracking moved to task_recipients table)."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO tasks (
                        user_id, chat_id, message_id, title, description, 
                        due_time, screenshot_file_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, chat_id, message_id, task_data.title, 
                    task_data.description, task_data.due_time, 
                    screenshot_file_id
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
                    SELECT id, user_id, chat_id, message_id, title, 
                           description, due_time, screenshot_file_id
                    FROM tasks ORDER BY due_time ASC
                ''')
                
                return [
                    TaskDB(
                        id=row[0], user_id=row[1], chat_id=row[2], message_id=row[3],
                        title=row[4], description=row[5], due_time=row[6],
                        screenshot_file_id=row[7]
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
                    SELECT id, user_id, chat_id, message_id, title, 
                           description, due_time, screenshot_file_id
                    FROM tasks WHERE user_id = ? ORDER BY due_time ASC
                ''', (user_id,))
                
                return [
                    TaskDB(
                        id=row[0], user_id=row[1], chat_id=row[2], message_id=row[3],
                        title=row[4], description=row[5], due_time=row[6],
                        screenshot_file_id=row[7]
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
                    SELECT id, user_id, chat_id, message_id, title, 
                           description, due_time, screenshot_file_id
                    FROM tasks WHERE id = ?
                ''', (task_id,))
                
                row = cursor.fetchone()
                if row:
                    return TaskDB(
                        id=row[0], user_id=row[1], chat_id=row[2], message_id=row[3],
                        title=row[4], description=row[5], due_time=row[6],
                        screenshot_file_id=row[7]
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
    
    def add_recipient(self, task_id: int, recipient_id: int, 
                     platform_task_id: str, platform_type: str) -> bool:
        """Add a recipient to a task with platform-specific ID."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO task_recipients (task_id, recipient_id, platform_task_id, platform_type)
                    VALUES (?, ?, ?, ?)
                ''', (task_id, recipient_id, platform_task_id, platform_type))
                
                success = cursor.rowcount > 0
                
                if success:
                    logger.info(f"Added recipient {recipient_id} to task {task_id} with platform ID {platform_task_id}")
                else:
                    logger.warning(f"Failed to add recipient {recipient_id} to task {task_id}")
                
                return success
                
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                logger.warning(f"Recipient {recipient_id} already exists for task {task_id}")
                return False
            logger.error(f"Integrity error adding recipient to task: {e}")
            raise DatabaseError(f"Failed to add recipient to task: {e}")
        except sqlite3.Error as e:
            logger.error(f"Failed to add recipient to task: {e}")
            raise DatabaseError(f"Failed to add recipient to task: {e}")
    
    def remove_recipient(self, task_id: int, recipient_id: int) -> bool:
        """Remove a recipient from a task."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    DELETE FROM task_recipients 
                    WHERE task_id = ? AND recipient_id = ?
                ''', (task_id, recipient_id))
                
                deleted = cursor.rowcount > 0
                
                if deleted:
                    logger.info(f"Removed recipient {recipient_id} from task {task_id}")
                else:
                    logger.warning(f"Recipient {recipient_id} not found for task {task_id}")
                
                return deleted
                
        except sqlite3.Error as e:
            logger.error(f"Failed to remove recipient from task: {e}")
            raise DatabaseError(f"Failed to remove recipient from task: {e}")
    
    def get_task_recipients(self, task_id: int) -> List[TaskRecipient]:
        """Get all recipients for a specific task."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, task_id, recipient_id, platform_task_id, platform_type, created_at, status
                    FROM task_recipients 
                    WHERE task_id = ? AND status = 'active'
                    ORDER BY created_at ASC
                ''', (task_id,))
                
                return [
                    TaskRecipient(
                        id=row[0], task_id=row[1], recipient_id=row[2],
                        platform_task_id=row[3], platform_type=row[4], 
                        created_at=row[5], status=row[6]
                    )
                    for row in cursor.fetchall()
                ]
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get recipients for task {task_id}: {e}")
            raise DatabaseError(f"Failed to get task recipients: {e}")
    
    def get_recipient_tasks(self, recipient_id: int) -> List[TaskRecipient]:
        """Get all tasks for a specific recipient."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, task_id, recipient_id, platform_task_id, platform_type, created_at, status
                    FROM task_recipients 
                    WHERE recipient_id = ? AND status = 'active'
                    ORDER BY created_at DESC
                ''', (recipient_id,))
                
                return [
                    TaskRecipient(
                        id=row[0], task_id=row[1], recipient_id=row[2],
                        platform_task_id=row[3], platform_type=row[4], 
                        created_at=row[5], status=row[6]
                    )
                    for row in cursor.fetchall()
                ]
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get tasks for recipient {recipient_id}: {e}")
            raise DatabaseError(f"Failed to get recipient tasks: {e}")
    
    def get_task_recipient(self, task_id: int, recipient_id: int) -> Optional[TaskRecipient]:
        """Get a specific task-recipient relationship."""
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT id, task_id, recipient_id, platform_task_id, platform_type, created_at, status
                    FROM task_recipients 
                    WHERE task_id = ? AND recipient_id = ? AND status = 'active'
                ''', (task_id, recipient_id))
                
                row = cursor.fetchone()
                if row:
                    return TaskRecipient(
                        id=row[0], task_id=row[1], recipient_id=row[2],
                        platform_task_id=row[3], platform_type=row[4], 
                        created_at=row[5], status=row[6]
                    )
                return None
                
        except sqlite3.Error as e:
            logger.error(f"Failed to get task-recipient relationship: {e}")
            raise DatabaseError(f"Failed to get task-recipient relationship: {e}")

