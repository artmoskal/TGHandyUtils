"""Partner model definitions for shared task functionality."""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class Partner:
    """Represents a partner (including self) for task sharing."""
    
    id: str  # 'self', 'wife_001', etc.
    name: str
    platform: str
    credentials: str
    platform_config: Optional[Dict[str, Any]] = None
    is_self: bool = False
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class PartnerCreate:
    """Data for creating a new partner."""
    
    name: str
    platform: str
    credentials: str
    platform_config: Optional[Dict[str, Any]] = None
    is_self: bool = False
    enabled: bool = True


@dataclass
class PartnerUpdate:
    """Data for updating an existing partner."""
    
    name: Optional[str] = None
    platform: Optional[str] = None
    credentials: Optional[str] = None
    platform_config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


@dataclass
class UserPreferences:
    """User preferences for partner sharing and notifications."""
    
    user_id: int
    default_partners: Optional[list] = None  # List of partner IDs
    show_sharing_ui: bool = False
    telegram_notifications: bool = True
    location: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class UserPreferencesCreate:
    """Data for creating user preferences."""
    
    user_id: int
    default_partners: Optional[list] = None
    show_sharing_ui: bool = False
    telegram_notifications: bool = True
    location: Optional[str] = None


@dataclass
class UserPreferencesUpdate:
    """Data for updating user preferences."""
    
    default_partners: Optional[list] = None
    show_sharing_ui: Optional[bool] = None
    telegram_notifications: Optional[bool] = None
    location: Optional[str] = None