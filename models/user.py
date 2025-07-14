"""User model for storing Telegram user information."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class User:
    """Telegram user information."""
    user_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    last_seen: datetime
    created_at: datetime
    
    @property
    def display_name(self) -> str:
        """Get best available display name."""
        if self.first_name:
            return self.first_name
        if self.username:
            return f"@{self.username}"
        return f"User{self.user_id}"
    
    @property
    def full_name(self) -> str:
        """Get full name if available."""
        parts = []
        if self.first_name:
            parts.append(self.first_name)
        if self.last_name:
            parts.append(self.last_name)
        return " ".join(parts) if parts else self.display_name
    
    def __str__(self) -> str:
        """String representation."""
        return f"{self.display_name} ({self.user_id})"