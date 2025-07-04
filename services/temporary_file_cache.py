"""Temporary file cache for screenshot data."""

import os
import time
import threading
from typing import Optional, Dict, Any
from core.logging import get_logger

logger = get_logger(__name__)


class TemporaryFileCache:
    """Thread-safe temporary file cache with automatic cleanup."""
    
    def __init__(self, cache_dir: str = "data/temp_cache", max_age_seconds: int = 300):  # 5 minutes default
        self.cache_dir = cache_dir
        self.max_age_seconds = max_age_seconds
        self._lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        
        # Ensure cache directory exists
        os.makedirs(cache_dir, exist_ok=True)
        
        # Start cleanup thread
        self._cleanup_thread = threading.Thread(target=self._periodic_cleanup, daemon=True)
        self._cleanup_thread.start()
    
    def store_screenshot(self, file_id: str, image_data: bytes, file_name: str) -> None:
        """Store screenshot data temporarily."""
        try:
            file_path = os.path.join(self.cache_dir, f"{file_id}.jpg")
            
            with self._lock:
                # Write to disk
                with open(file_path, 'wb') as f:
                    f.write(image_data)
                
                # Store metadata
                self._cache[file_id] = {
                    'file_path': file_path,
                    'file_name': file_name,
                    'timestamp': time.time(),
                    'size': len(image_data)
                }
            
            logger.info(f"Stored screenshot {file_id} ({len(image_data)} bytes) at {file_path}")
            
        except Exception as e:
            logger.error(f"Failed to store screenshot {file_id}: {e}")
    
    def get_screenshot(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve screenshot data."""
        try:
            with self._lock:
                if file_id not in self._cache:
                    logger.debug(f"Screenshot {file_id} not found in cache")
                    return None
                
                cache_entry = self._cache[file_id]
                file_path = cache_entry['file_path']
                
                # Check if file still exists
                if not os.path.exists(file_path):
                    logger.warning(f"Screenshot file {file_path} no longer exists")
                    del self._cache[file_id]
                    return None
                
                # Check age
                age = time.time() - cache_entry['timestamp']
                if age > self.max_age_seconds:
                    logger.info(f"Screenshot {file_id} expired ({age:.1f}s > {self.max_age_seconds}s)")
                    self._remove_entry(file_id, cache_entry)
                    return None
                
                # Read file data
                with open(file_path, 'rb') as f:
                    image_data = f.read()
                
                return {
                    'image_data': image_data,
                    'file_name': cache_entry['file_name'],
                    'file_id': file_id
                }
        
        except Exception as e:
            logger.error(f"Failed to retrieve screenshot {file_id}: {e}")
            return None
    
    def _remove_entry(self, file_id: str, cache_entry: Dict[str, Any]) -> None:
        """Remove cache entry and file (assumes lock is held)."""
        try:
            file_path = cache_entry['file_path']
            if os.path.exists(file_path):
                os.remove(file_path)
            del self._cache[file_id]
            logger.debug(f"Removed cached screenshot {file_id}")
        except Exception as e:
            logger.error(f"Failed to remove cache entry {file_id}: {e}")
    
    def _periodic_cleanup(self) -> None:
        """Periodic cleanup of expired entries."""
        while True:
            try:
                time.sleep(60)  # Check every minute
                current_time = time.time()
                expired_entries = []
                
                with self._lock:
                    for file_id, cache_entry in self._cache.items():
                        age = current_time - cache_entry['timestamp']
                        if age > self.max_age_seconds:
                            expired_entries.append((file_id, cache_entry))
                    
                    for file_id, cache_entry in expired_entries:
                        self._remove_entry(file_id, cache_entry)
                
                if expired_entries:
                    logger.info(f"Cleaned up {len(expired_entries)} expired screenshot cache entries")
                    
            except Exception as e:
                logger.error(f"Error in periodic cleanup: {e}")
    
    def clear_all(self) -> None:
        """Clear all cached files."""
        with self._lock:
            entries_to_remove = list(self._cache.items())
            for file_id, cache_entry in entries_to_remove:
                self._remove_entry(file_id, cache_entry)
        logger.info("Cleared all screenshot cache entries")


# Global cache instance
_screenshot_cache = None
_cache_lock = threading.Lock()


def get_screenshot_cache() -> TemporaryFileCache:
    """Get global screenshot cache instance."""
    global _screenshot_cache
    if _screenshot_cache is None:
        with _cache_lock:
            if _screenshot_cache is None:
                _screenshot_cache = TemporaryFileCache()
    return _screenshot_cache