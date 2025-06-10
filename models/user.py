"""User data models."""

from typing import Optional, Dict, Any
from dataclasses import dataclass
from pydantic import BaseModel, Field, validator

@dataclass
class UserDB:
    """Database representation of a user."""
    id: Optional[int]
    telegram_user_id: int
    platform_token: str
    platform_type: str
    owner_name: str
    location: Optional[str]
    platform_settings: Optional[str]

class UserCreate(BaseModel):
    """Model for creating a new user."""
    telegram_user_id: int
    platform_token: str
    platform_type: str = Field(default='todoist')
    owner_name: str
    location: Optional[str] = None
    platform_settings: Optional[Dict[str, Any]] = None
    
    @validator('platform_type')
    def validate_platform_type(cls, v):
        allowed_platforms = ['todoist', 'trello']
        if v not in allowed_platforms:
            raise ValueError(f'Platform type must be one of {allowed_platforms}')
        return v
    
    @validator('platform_token')
    def token_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Platform token cannot be empty')
        return v.strip()

class UserUpdate(BaseModel):
    """Model for updating user information."""
    platform_token: Optional[str] = None
    platform_type: Optional[str] = None
    owner_name: Optional[str] = None
    location: Optional[str] = None
    platform_settings: Optional[Dict[str, Any]] = None