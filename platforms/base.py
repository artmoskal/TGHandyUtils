from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

class AbstractTaskPlatform(ABC):
    """Abstract base class for task management platforms."""
    
    @abstractmethod
    def create_task(self, task_data: Dict[str, Any]) -> Optional[str]:
        """
        Create a task on the platform.
        
        Args:
            task_data (dict): Dictionary containing task information
                - title (str): The task title
                - description (str): The task description
                - due_time (str): The due time in ISO 8601 format
                
        Returns:
            str: Task ID if successful, None otherwise
        """
        pass
    
    @abstractmethod
    def update_task(self, task_id: str, task_data: Dict[str, Any]) -> bool:
        """
        Update a task on the platform.
        
        Args:
            task_id (str): The ID of the task to update
            task_data (dict): Dictionary containing task information to update
                
        Returns:
            bool: True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def delete_task(self, task_id: str) -> bool:
        """
        Delete a task on the platform.
        
        Args:
            task_id (str): The ID of the task to delete
                
        Returns:
            bool: True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a task from the platform.
        
        Args:
            task_id (str): The ID of the task to retrieve
                
        Returns:
            dict: Task data if successful, None otherwise
        """
        pass
    
    @abstractmethod
    def get_task_url(self, task_id: str) -> str:
        """
        Generate a direct URL to a task on the platform.
        
        Args:
            task_id (str): The ID of the task
            
        Returns:
            str: Direct URL to the task
        """
        pass

class TaskPlatformFactory:
    """Factory for creating task platform instances."""
    
    @staticmethod
    def get_platform(platform_type: str, api_token: str) -> Optional['AbstractTaskPlatform']:
        """
        Get a task platform instance based on the platform type.
        
        Args:
            platform_type (str): The type of platform ('todoist', 'trello')
            api_token (str): The API token for the platform
                
        Returns:
            AbstractTaskPlatform: An instance of the appropriate task platform
        """
        if platform_type == 'todoist':
            from platforms.todoist import TodoistPlatform
            return TodoistPlatform(api_token)
        elif platform_type == 'trello':
            from platforms.trello import TrelloPlatform
            return TrelloPlatform(api_token)
        else:
            logger.error(f"Unsupported platform type: {platform_type}")
            return None