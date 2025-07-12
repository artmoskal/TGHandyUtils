import uuid
import sqlite3
from typing import Optional
from datetime import datetime, timedelta
from core.logging import get_logger

logger = get_logger(__name__)

class OAuthStateManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def create_pending_request(self, user_id: int) -> str:
        """Create unique state for OAuth request with 1 hour expiration."""
        state = f"{user_id}_{uuid.uuid4().hex[:8]}"
        expires_at = datetime.utcnow() + timedelta(hours=1)
        
        with self.db_manager.get_connection() as conn:
            # Clean up expired states
            conn.execute(
                "DELETE FROM oauth_states WHERE expires_at < ?",
                (datetime.utcnow(),)
            )
            
            # Store new state
            conn.execute(
                "INSERT OR REPLACE INTO oauth_states (user_id, state, expires_at) VALUES (?, ?, ?)",
                (user_id, state, expires_at)
            )
            conn.commit()
        
        logger.info(f"Created OAuth state for user {user_id}: {state}")
        return state

    def complete_oauth_request(self, state: str, code: str) -> Optional[int]:
        """Complete OAuth and return user_id if valid."""
        try:
            # Extract user_id from state
            parts = state.split('_')
            if len(parts) != 2:
                logger.warning(f"Invalid state format: {state}")
                return None
                
            user_id = int(parts[0])
            
            with self.db_manager.get_connection() as conn:
                # Verify state exists and not expired
                cursor = conn.execute(
                    "SELECT user_id FROM oauth_states WHERE user_id = ? AND state = ? AND expires_at > ?",
                    (user_id, state, datetime.utcnow())
                )
            
                if not cursor.fetchone():
                    logger.warning(f"Invalid or expired OAuth state: {state}")
                    return None
                
                # Store OAuth code
                conn.execute(
                    "UPDATE oauth_states SET oauth_code = ? WHERE user_id = ? AND state = ?",
                    (code, user_id, state)
                )
                conn.commit()
            
            logger.info(f"OAuth completed for user {user_id}")
            return user_id
            
        except (ValueError, sqlite3.Error) as e:
            logger.error(f"Error completing OAuth request: {e}")
            return None

    def get_oauth_code(self, user_id: int) -> Optional[str]:
        """Get OAuth code for user (one-time use)."""
        with self.db_manager.get_connection() as conn:
            cursor = conn.execute(
                "SELECT oauth_code, state FROM oauth_states WHERE user_id = ? AND oauth_code IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                (user_id,)
            )
            
            row = cursor.fetchone()
            if row:
                code, state = row
                # Delete after retrieval
                conn.execute(
                    "DELETE FROM oauth_states WHERE user_id = ? AND state = ?",
                    (user_id, state)
                )
                conn.commit()
                return code
        
        return None