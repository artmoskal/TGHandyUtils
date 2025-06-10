"""Task management service."""

import json
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
from dateutil import parser

from core.interfaces import ITaskService, ITaskRepository, IUserRepository
from models.task import TaskCreate, PlatformTaskData
from models.user import UserCreate
from platforms import TaskPlatformFactory
from core.exceptions import ValidationError, TaskCreationError, PlatformError
from core.logging import get_logger

logger = get_logger(__name__)

class TaskService(ITaskService):
    """Service for task management operations."""
    
    def __init__(self, task_repo: ITaskRepository, user_repo: IUserRepository):
        self.task_repo = task_repo
        self.user_repo = user_repo
    
    async def create_task(self, user_id: int, chat_id: int, message_id: int, 
                         task_data: TaskCreate, initiator_link: Optional[str] = None) -> bool:
        """Create a task both locally and on the platform.
        
        Args:
            user_id: Telegram user ID
            chat_id: Telegram chat ID
            message_id: Telegram message ID
            task_data: Task creation data
            initiator_link: Optional link to original message
            
        Returns:
            True if successful, False otherwise
            
        Raises:
            ValidationError: If task data is invalid
            TaskCreationError: If task creation fails
        """
        try:
            # Validate due time
            validated_due_time = self._validate_due_time(task_data.due_time)
            task_data.due_time = validated_due_time.isoformat()
            
            # Append initiator link to description if provided
            if initiator_link:
                task_data.description += f"\n\nOriginal message: {initiator_link}"
            
            # Get user platform info
            user_info = self.user_repo.get_platform_info(user_id)
            if not user_info or not user_info.get('platform_token'):
                raise TaskCreationError("User platform not configured")
            
            platform_type = user_info.get('platform_type', 'todoist')
            
            # Save task to local database first
            task_db_id = self.task_repo.create(
                user_id=user_id,
                chat_id=chat_id,
                message_id=message_id,
                task_data=task_data,
                platform_type=platform_type
            )
            
            if not task_db_id:
                raise TaskCreationError("Failed to save task to local database")
            
            logger.info(f"Task saved locally with ID {task_db_id} for user {user_id}")
            
            # Create task on platform
            platform_task_id, error_message = self._create_platform_task(task_data, user_info)
            
            if platform_task_id:
                # Update local task with platform ID
                self.task_repo.update_platform_id(task_db_id, platform_task_id, platform_type)
                logger.info(f"Task {task_db_id} created on {platform_type} with ID {platform_task_id}")
                return True
            else:
                logger.warning(f"Task {task_db_id} saved locally but failed to create on {platform_type}: {error_message}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to create task: {e}")
            raise TaskCreationError(f"Task creation failed: {e}")
    
    def _validate_due_time(self, due_time_str: str) -> datetime:
        """Validate and adjust due time if necessary.
        
        Args:
            due_time_str: Due time in ISO format
            
        Returns:
            Validated datetime object
            
        Raises:
            ValidationError: If due time format is invalid
        """
        try:
            due_time = parser.isoparse(due_time_str).astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)
            
            logger.debug(f"Parsed due time: {due_time.isoformat()}")
            logger.debug(f"Current UTC time: {now_utc.isoformat()}")
            
            # Check if the due time is in the past
            if due_time <= now_utc:
                logger.warning("Due time is in the past, adjusting to one hour from now")
                due_time = now_utc.replace(microsecond=0) + timedelta(hours=1)
            
            return due_time
            
        except Exception as e:
            logger.error(f"Error parsing due time: {e}")
            # Return default time (tomorrow at 9 AM UTC) if parsing fails
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            default_time = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
            logger.warning(f"Using default due time: {default_time.isoformat()}")
            return default_time
    
    def _create_platform_task(self, task_data: TaskCreate, user_info: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """Create task on the user's platform.
        
        Args:
            task_data: Task creation data
            user_info: User platform information
            
        Returns:
            Tuple of (task_id, error_message)
        """
        try:
            platform_type = user_info.get('platform_type', 'todoist')
            platform_token = user_info.get('platform_token')
            
            if not platform_token:
                return None, "Platform token not found"
            
            # Get platform instance
            platform = TaskPlatformFactory.get_platform(platform_type, platform_token)
            if not platform:
                return None, f"Failed to initialize {platform_type} platform"
            
            # Prepare platform-specific task data
            platform_task_data = PlatformTaskData(
                title=task_data.title,
                description=task_data.description,
                due_time=task_data.due_time
            )
            
            # Add platform-specific settings
            if user_info.get('platform_settings'):
                settings = user_info['platform_settings']
                if isinstance(settings, str):
                    try:
                        settings = json.loads(settings)
                    except json.JSONDecodeError:
                        return None, f"Invalid {platform_type} settings format"
                
                # Add settings to task data
                if platform_type == 'trello':
                    platform_task_data.board_id = settings.get('board_id')
                    platform_task_data.list_id = settings.get('list_id')
                    
                    if not platform_task_data.board_id or not platform_task_data.list_id:
                        return None, "Incomplete Trello configuration"
            
            # Create task on platform
            task_id = platform.create_task(platform_task_data.dict())
            
            if task_id:
                logger.debug(f"Created task with ID: {task_id} on platform {platform_type}")
                return task_id, None
            else:
                return None, f"Platform {platform_type} returned no task ID"
                
        except Exception as e:
            error_msg = f"Error creating task on {platform_type}: {e}"
            logger.error(error_msg)
            return None, error_msg
    
    def save_user_platform(self, telegram_user_id: int, platform_token: str, 
                          platform_type: str, owner_name: str, 
                          location: Optional[str] = None, 
                          platform_settings: Optional[Dict[str, Any]] = None) -> bool:
        """Save or update user platform information.
        
        Args:
            telegram_user_id: Telegram user ID
            platform_token: Platform API token
            platform_type: Platform type (todoist, trello)
            owner_name: User's name
            location: User's location
            platform_settings: Platform-specific settings
            
        Returns:
            True if successful
            
        Raises:
            ValidationError: If validation fails
        """
        try:
            user_data = UserCreate(
                telegram_user_id=telegram_user_id,
                platform_token=platform_token,
                platform_type=platform_type,
                owner_name=owner_name,
                location=location,
                platform_settings=platform_settings
            )
            
            return self.user_repo.create_or_update(user_data)
            
        except Exception as e:
            logger.error(f"Failed to save user platform info: {e}")
            raise ValidationError(f"Failed to save user platform info: {e}")
    
    def get_user_platform_info(self, telegram_user_id: int) -> Optional[Dict[str, Any]]:
        """Get user's platform information.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            Dictionary with platform info or None
        """
        return self.user_repo.get_platform_info(telegram_user_id)
    
    def get_platform_token(self, telegram_user_id: int) -> Optional[str]:
        """Get user's platform token.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            Platform token or None
        """
        return self.user_repo.get_platform_token(telegram_user_id)
    
    def get_platform_type(self, telegram_user_id: int) -> str:
        """Get user's platform type.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            Platform type (defaults to 'todoist')
        """
        return self.user_repo.get_platform_type(telegram_user_id)
    
    def delete_user_data(self, telegram_user_id: int) -> bool:
        """Delete all user data.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            True if successful
        """
        return self.user_repo.delete(telegram_user_id)

# Remove global instance - use DI container instead