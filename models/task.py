"""Task data models."""

from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass
from pydantic import BaseModel, Field, field_validator

@dataclass
class TaskDB:
    """Database representation of a task (platform tracking moved to TaskRecipient)."""
    id: Optional[int]
    user_id: int
    chat_id: int
    message_id: int
    title: str
    description: str
    due_time: str
    screenshot_file_id: Optional[str] = None

class TaskCreate(BaseModel):
    """Model for creating a new task."""
    title: str = Field(description="The title of the task.")
    due_time: str = Field(description="The due time in UTC ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ).")
    description: Optional[str] = Field(description="The description or details of the task.")
    
    @field_validator('title')
    @classmethod
    def title_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Title cannot be empty')
        return v.strip()
    
    @field_validator('description')
    @classmethod
    def description_validator(cls, v):
        # Allow empty descriptions (they can be None in database)
        if v is None:
            return ""
        return v.strip()

class TaskUpdate(BaseModel):
    """Model for updating a task."""
    title: Optional[str] = None
    description: Optional[str] = None
    due_time: Optional[str] = None

@dataclass
class TaskRecipient:
    """Database representation of a task-recipient relationship."""
    id: Optional[int]
    task_id: int
    recipient_id: int
    platform_task_id: str
    platform_type: str
    created_at: str
    status: str = 'active'

class PlatformTaskData(BaseModel):
    """Model for platform-specific task data."""
    title: str
    description: str
    due_time: str
    board_id: Optional[str] = None  # For Trello
    list_id: Optional[str] = None   # For Trello
    source_attachment: Optional[str] = None  # Additional source information