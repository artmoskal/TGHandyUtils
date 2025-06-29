"""Unified recipient models - single entity for all recipients."""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class UnifiedRecipient:
    """Single recipient entity for all account types."""
    id: int
    user_id: int
    name: str
    platform_type: str
    credentials: str
    platform_config: Optional[Dict[str, Any]] = None
    is_personal: bool = False
    is_default: bool = False
    enabled: bool = True
    shared_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class UnifiedRecipientCreate:
    """Data for creating a unified recipient."""
    name: str
    platform_type: str
    credentials: str
    platform_config: Optional[Dict[str, Any]] = None
    is_personal: bool = False
    is_default: bool = False
    enabled: bool = True
    shared_by: Optional[str] = None


@dataclass
class UnifiedRecipientUpdate:
    """Data for updating a unified recipient."""
    name: Optional[str] = None
    credentials: Optional[str] = None
    platform_config: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None
    enabled: Optional[bool] = None
    shared_by: Optional[str] = None


@dataclass
class UnifiedUserPreferences:
    """Clean user preferences without recipient list."""
    user_id: int
    show_recipient_ui: bool = False
    telegram_notifications: bool = True
    owner_name: Optional[str] = None
    location: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class UnifiedUserPreferencesCreate:
    """Data for creating user preferences."""
    show_recipient_ui: bool = False
    telegram_notifications: bool = True
    owner_name: Optional[str] = None
    location: Optional[str] = None


@dataclass
class UnifiedUserPreferencesUpdate:
    """Data for updating user preferences."""
    show_recipient_ui: Optional[bool] = None
    telegram_notifications: Optional[bool] = None
    owner_name: Optional[str] = None
    location: Optional[str] = None