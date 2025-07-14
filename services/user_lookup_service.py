"""Simple service to check if user exists in our system."""

from typing import Optional
from core.logging import get_logger

logger = get_logger(__name__)


class UserLookupService:
    """Simple service to check if user exists in our system."""
    
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self._cache = {}  # Simple in-memory cache for session
    
    def find_user_by_username(self, username: str) -> Optional[int]:
        """Check if user exists by looking at recipients table.
        Returns user_id if found, None otherwise."""
        
        # Clean username
        clean_username = username.strip().lstrip('@').lower()
        
        # Check cache first
        if clean_username in self._cache:
            logger.debug(f"Found @{clean_username} in cache: {self._cache[clean_username]}")
            return self._cache[clean_username]
        
        # Try to find user in database
        try:
            with self.db_manager.get_connection() as conn:
                # Check if we have this user_id anywhere
                # This is limited since we don't store usernames, but we can check
                # if a specific user_id exists
                cursor = conn.execute('''
                    SELECT DISTINCT user_id 
                    FROM recipients 
                    WHERE user_id > 0
                ''')
                
                known_user_ids = [row[0] for row in cursor.fetchall()]
                logger.debug(f"Known user IDs in system: {known_user_ids}")
                
                # Without a username-to-userid mapping, we can't match
                # But at least we know these users have used the bot
                
        except Exception as e:
            logger.error(f"Error checking database for users: {e}")
        
        # For now, we can't reliably match usernames to user_ids
        # without additional tracking. Return None to trigger bot link flow.
        return None
    
    def cache_user(self, user_id: int, username: str):
        """Cache user info for future lookups within this session."""
        if username and user_id:
            clean_username = username.strip().lstrip('@').lower()
            self._cache[clean_username] = user_id
            logger.debug(f"Cached user: @{clean_username} -> {user_id}")