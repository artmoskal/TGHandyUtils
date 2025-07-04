"""Task data models."""

from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass
from pydantic import BaseModel, Field, field_validator

@dataclass
class TaskDB:
    """Database representation of a task."""
    id: Optional[int]
    user_id: int
    chat_id: int
    message_id: int
    task_title: str
    task_description: str
    due_time: str
    platform_task_id: Optional[str]
    platform_type: str
    screenshot_file_id: Optional[str] = None  # Telegram file_id for screenshot
    screenshot_filename: Optional[str] = None  # Original filename

class TaskCreate(BaseModel):
    """Model for creating a new task."""
    title: str = Field(description="The title of the task.")
    due_time: str = Field(description="The due time in UTC ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ).")
    description: str = Field(description="The description or details of the task.")
    
    @field_validator('title')
    @classmethod
    def title_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Title cannot be empty')
        return v.strip()
    
    @field_validator('description')
    @classmethod
    def description_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Description cannot be empty')
        return v.strip()

class TaskUpdate(BaseModel):
    """Model for updating a task."""
    title: Optional[str] = None
    description: Optional[str] = None
    due_time: Optional[str] = None

class PlatformTaskData(BaseModel):
    """Model for platform-specific task data."""
    title: str
    description: str
    due_time: str
    board_id: Optional[str] = None  # For Trello
    list_id: Optional[str] = None   # For Trello
    source_attachment: Optional[str] = None  # Additional source information