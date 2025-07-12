"""Parameter objects for method signatures with many parameters."""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass(frozen=True)
class TaskCreationRequest:
    """Parameter object for task creation requests.
    
    Groups all parameters needed for creating a task across recipients.
    Immutable to ensure thread safety and prevent accidental modifications.
    """
    user_id: int
    title: str
    description: str = ""
    due_time: Optional[str] = None
    specific_recipients: Optional[List[int]] = None
    screenshot_data: Optional[Dict[str, Any]] = None
    chat_id: int = 0
    message_id: int = 0
    
    def __post_init__(self):
        """Validate required fields after initialization."""
        if not self.user_id:
            raise ValueError("user_id is required")
        if not self.title or not self.title.strip():
            raise ValueError("title is required and cannot be empty")
        
        # Type validation
        if not isinstance(self.user_id, int):
            raise TypeError("user_id must be an integer")
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TaskCreationRequest':
        """Create TaskCreationRequest from dictionary.
        
        Useful for creating from request data or database results.
        """
        return cls(
            user_id=data.get('user_id'),
            title=data.get('title', ''),
            description=data.get('description', ''),
            due_time=data.get('due_time'),
            specific_recipients=data.get('specific_recipients'),
            screenshot_data=data.get('screenshot_data'),
            chat_id=data.get('chat_id', 0),
            message_id=data.get('message_id', 0)
        )


@dataclass(frozen=True)
class TaskFeedbackData:
    """Parameter object for task feedback generation.
    
    Groups all data needed to generate success/failure feedback messages.
    """
    recipients: List[str]
    task_urls: Dict[str, str]
    failed_recipients: List[str]
    title: str
    description: str
    due_time: str
    user_id: int
    
    @property
    def successful_count(self) -> int:
        """Count of successfully created tasks."""
        return len(self.task_urls)
    
    @property
    def failed_count(self) -> int:
        """Count of failed task creations."""
        return len(self.failed_recipients)
    
    @property
    def total_count(self) -> int:
        """Total count of recipients."""
        return len(self.recipients)
    
    def has_failures(self) -> bool:
        """Check if any task creation failed."""
        return len(self.failed_recipients) > 0
    
    def all_failed(self) -> bool:
        """Check if all task creations failed."""
        return len(self.failed_recipients) == len(self.recipients)


@dataclass(frozen=True)
class RecipientData:
    """Parameter object for recipient creation.
    
    Groups parameters for adding personal or shared recipients.
    """
    user_id: int
    name: str
    platform_type: str
    credentials: str
    platform_config: Optional[Dict[str, Any]] = None
    is_personal: bool = True
    is_default: bool = False
    enabled: bool = True
    
    def __post_init__(self):
        """Validate required fields."""
        if not self.name or not self.name.strip():
            raise ValueError("name is required and cannot be empty")
        if not self.platform_type:
            raise ValueError("platform_type is required")
        if self.platform_type not in ['todoist', 'trello', 'google_calendar']:
            raise ValueError(f"Invalid platform_type: {self.platform_type}")


@dataclass(frozen=True)
class AuthRequestData:
    """Parameter object for authentication requests.
    
    Groups parameters for creating auth requests between users.
    """
    requester_user_id: int
    target_user_id: int
    platform_type: str
    recipient_name: str
    expires_at: str
    
    def __post_init__(self):
        """Validate required fields."""
        if not self.requester_user_id or not self.target_user_id:
            raise ValueError("Both requester_user_id and target_user_id are required")
        if self.requester_user_id == self.target_user_id:
            raise ValueError("Cannot create auth request to yourself")
        if not self.platform_type or not self.recipient_name:
            raise ValueError("platform_type and recipient_name are required")


@dataclass(frozen=True)
class PlatformTaskData:
    """Parameter object for platform-specific task creation.
    
    Groups task data for creating on individual platforms.
    """
    title: str
    description: str
    due_time: Optional[str] = None
    screenshot_data: Optional[Dict[str, Any]] = None
    source_attachment: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for platform APIs."""
        data = {
            'title': self.title,
            'description': self.description
        }
        if self.due_time:
            data['due_time'] = self.due_time
        if self.screenshot_data:
            data['screenshot_data'] = self.screenshot_data
        if self.source_attachment:
            data['source_attachment'] = self.source_attachment
        return data