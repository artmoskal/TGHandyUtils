"""Clean recipient models - no legacy code."""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime


@dataclass
class UserPlatform:
    """Platform owned by the user."""
    id: int
    telegram_user_id: int
    platform_type: str  # 'todoist', 'trello'
    credentials: str
    platform_config: Optional[Dict[str, Any]] = None
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class UserPlatformCreate:
    """Data for creating a user platform."""
    platform_type: str
    credentials: str
    platform_config: Optional[Dict[str, Any]] = None
    enabled: bool = True


@dataclass
class UserPlatformUpdate:
    """Data for updating a user platform."""
    credentials: Optional[str] = None
    platform_config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


@dataclass
class SharedRecipient:
    """Platform shared by another user."""
    id: int
    telegram_user_id: int
    name: str  # "Wife's Trello", "Team Todoist"
    platform_type: str
    credentials: str
    platform_config: Optional[Dict[str, Any]] = None
    shared_by: Optional[str] = None
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class SharedRecipientCreate:
    """Data for creating a shared recipient."""
    name: str
    platform_type: str
    credentials: str
    platform_config: Optional[Dict[str, Any]] = None
    shared_by: Optional[str] = None
    enabled: bool = True


@dataclass
class SharedRecipientUpdate:
    """Data for updating a shared recipient."""
    name: Optional[str] = None
    credentials: Optional[str] = None
    platform_config: Optional[Dict[str, Any]] = None
    shared_by: Optional[str] = None
    enabled: Optional[bool] = None


@dataclass
class Recipient:
    """Unified recipient (user platform or shared recipient)."""
    id: str  # "platform_1" or "shared_5"
    name: str  # "My Todoist" or "Wife's Trello"
    platform_type: str
    type: str  # 'user_platform' or 'shared_recipient'
    enabled: bool


@dataclass
class UserPreferencesV2:
    """Clean user preferences."""
    telegram_user_id: int
    default_recipients: List[str]  # recipient IDs
    show_recipient_ui: bool = False
    telegram_notifications: bool = True
    owner_name: Optional[str] = None
    location: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class UserPreferencesV2Create:
    """Data for creating user preferences."""
    default_recipients: List[str] = None
    show_recipient_ui: bool = False
    telegram_notifications: bool = True
    owner_name: Optional[str] = None
    location: Optional[str] = None


@dataclass
class UserPreferencesV2Update:
    """Data for updating user preferences."""
    default_recipients: Optional[List[str]] = None
    show_recipient_ui: Optional[bool] = None
    telegram_notifications: Optional[bool] = None
    owner_name: Optional[str] = None
    location: Optional[str] = None