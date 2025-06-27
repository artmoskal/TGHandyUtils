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
    
    @abstractmethod
    def attach_screenshot(self, task_id: str, image_data: bytes, file_name: str) -> bool:
        """
        Attach a screenshot to a task on the platform.
        
        Args:
            task_id (str): The ID of the task
            image_data (bytes): Screenshot data
            file_name (str): Name of the file
            
        Returns:
            bool: True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def get_token_from_settings(self, platform_settings: Dict[str, Any]) -> Optional[str]:
        """
        Extract platform token from settings dictionary.
        
        Args:
            platform_settings: Dictionary containing platform configuration
            
        Returns:
            Platform token string or None if not found/invalid
        """
        pass
    
    @abstractmethod
    def is_configured(self, platform_settings: Dict[str, Any]) -> bool:
        """
        Check if platform is properly configured.
        
        Args:
            platform_settings: Dictionary containing platform configuration
            
        Returns:
            True if platform is configured, False otherwise
        """
        pass
    
    @classmethod
    @abstractmethod
    def is_configured_static(cls, platform_settings: Dict[str, Any]) -> bool:
        """
        Check if platform is properly configured without instantiation.
        
        Args:
            platform_settings: Dictionary containing platform configuration
            
        Returns:
            True if platform is configured, False otherwise
        """
        pass

class TaskPlatformFactory:
    """Factory for creating task platform instances using registry pattern."""
    
    _registry: Dict[str, type] = None
    
    @classmethod
    def _get_registry(cls) -> Dict[str, type]:
        """Get or initialize the registry to avoid shared mutable state issues."""
        if cls._registry is None:
            cls._registry = {}
        return cls._registry
    
    @classmethod
    def register(cls, platform_type: str, platform_class: type) -> None:
        """
        Register a platform implementation.
        
        Args:
            platform_type: The platform identifier (e.g., 'todoist', 'trello')
            platform_class: The platform implementation class
        """
        if not issubclass(platform_class, AbstractTaskPlatform):
            raise ValueError(f"{platform_class} must inherit from AbstractTaskPlatform")
        registry = cls._get_registry()
        registry[platform_type] = platform_class
        logger.info(f"Registered platform: {platform_type}")
    
    @classmethod
    def get_platform(cls, platform_type: str, api_token: str) -> Optional['AbstractTaskPlatform']:
        """
        Get a task platform instance based on the platform type.
        
        Args:
            platform_type: The type of platform ('todoist', 'trello', etc.)
            api_token: The API token for the platform
                
        Returns:
            AbstractTaskPlatform: An instance of the appropriate task platform
        """
        registry = cls._get_registry()
        platform_class = registry.get(platform_type)
        if not platform_class:
            logger.error(f"Unsupported platform type: {platform_type}. Available: {list(registry.keys())}")
            return None
        
        try:
            return platform_class(api_token)
        except Exception as e:
            logger.error(f"Failed to create {platform_type} platform: {e}")
            return None
    
    @classmethod
    def get_registered_platforms(cls) -> list[str]:
        """Get list of all registered platform types."""
        registry = cls._get_registry()
        return list(registry.keys())
    
    @classmethod
    def is_platform_configured(cls, platform_type: str, platform_settings: Dict[str, Any]) -> bool:
        """Check if a platform is configured without instantiation."""
        registry = cls._get_registry()
        platform_class = registry.get(platform_type)
        if not platform_class:
            return False
        return platform_class.is_configured_static(platform_settings)


# Auto-registration decorator
def register_platform(platform_type: str):
    """Decorator to automatically register platform implementations."""
    def decorator(cls):
        TaskPlatformFactory.register(platform_type, cls)
        return cls
    return decorator