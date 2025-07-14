"""Service for user management with caching."""

from typing import Optional, Dict
from datetime import datetime, timedelta
from aiogram import types

from database.user.user_repository import UserRepository
from models.user import User
from core.logging import get_logger

logger = get_logger(__name__)


class UserService:
    """Service for user management with caching."""
    
    def __init__(self, user_repository: UserRepository):
        self.repository = user_repository
        self._cache: Dict[str, tuple[Optional[int], datetime]] = {}  # username -> (user_id, expires_at)
        self._cache_ttl = timedelta(minutes=5)  # 5 minute cache
    
    def track_user(self, telegram_user: types.User) -> None:
        """Track user from Telegram update.
        
        Args:
            telegram_user: Telegram user object from aiogram
        """
        try:
            # Extract user information
            user_id = telegram_user.id
            username = telegram_user.username
            first_name = telegram_user.first_name
            last_name = telegram_user.last_name
            
            # Update database
            self.repository.upsert_user(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
            
            # Update cache if username exists
            if username:
                clean_username = username.strip().lstrip('@').lower()
                expires_at = datetime.now() + self._cache_ttl
                self._cache[clean_username] = (user_id, expires_at)
                
            logger.debug(f"Tracked user {user_id} (@{username})")
        except Exception as e:
            logger.error(f"Failed to track user {telegram_user.id}: {e}")
    
    def find_user_by_username(self, username: str) -> Optional[int]:
        """Find user ID by username with caching.
        
        Args:
            username: Username to search for (with or without @)
            
        Returns:
            User ID if found, None otherwise
        """
        clean_username = username.strip().lstrip('@').lower()
        
        if not clean_username:
            return None
        
        # Check cache first
        if clean_username in self._cache:
            user_id, expires_at = self._cache[clean_username]
            if datetime.now() < expires_at:
                logger.debug(f"Cache hit for @{clean_username}: {user_id}")
                return user_id
            else:
                # Cache expired, remove it
                del self._cache[clean_username]
        
        # Not in cache, check database
        user = self.repository.find_by_username(username)
        if user:
            # Add to cache
            expires_at = datetime.now() + self._cache_ttl
            self._cache[clean_username] = (user.user_id, expires_at)
            logger.debug(f"Database hit for @{clean_username}: {user.user_id}")
            return user.user_id
        
        logger.debug(f"User not found: @{clean_username}")
        return None
    
    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            User object if found, None otherwise
        """
        return self.repository.find_by_id(user_id)
    
    def get_user_display_name(self, user_id: int) -> str:
        """Get display name for user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Best available display name
        """
        user = self.get_user_by_id(user_id)
        if user:
            return user.display_name
        return f"User{user_id}"
    
    def clean_cache(self) -> None:
        """Remove expired entries from cache."""
        now = datetime.now()
        expired_keys = [
            key for key, (_, expires_at) in self._cache.items()
            if expires_at <= now
        ]
        for key in expired_keys:
            del self._cache[key]
        
        if expired_keys:
            logger.debug(f"Cleaned {len(expired_keys)} expired cache entries")