# Temporary Cache Cleanup Implementation Plan

## Problem Statement
The `data/temp_cache/` directory contains downloaded Telegram images that are:
- Not being automatically cleaned up after processing
- Accumulating over time, consuming disk space
- Potentially exposing user data if not properly managed
- Causing git conflicts when accidentally tracked

## Current State Analysis

### File Usage Flow
1. User sends image (photo/document) to bot
2. Bot downloads file to `data/temp_cache/` using Telegram file_id as filename
3. Image is processed for OCR/analysis via `ImageProcessingService`
4. Image MAY be attached to Todoist/Trello task
5. **ISSUE**: Image remains in cache indefinitely

### Code Locations
- **Download**: `services/image_processing.py` - `process_image_message()`
- **Cache Management**: `services/temporary_file_cache.py` - Basic implementation exists
- **Usage**: `handlers.py` - `process_user_input_with_photo()`

## Root Cause
The `temporary_file_cache.py` has a `TemporaryFileCache` class with `cleanup_old_files()` method, but:
1. It's not being called automatically after processing
2. No background cleanup task is scheduled
3. No TTL (Time To Live) is enforced

## Implementation Plan

### Phase 1: Immediate Cleanup After Processing
**Goal**: Delete cache files immediately after task creation completes

#### 1.1 Modify `process_user_input_with_photo` in `handlers.py`
```python
async def process_user_input_with_photo(...):
    try:
        # ... existing processing code ...
        
        # After task creation completes
        if screenshot_data and screenshot_data.get('temp_path'):
            try:
                os.unlink(screenshot_data['temp_path'])
                logger.info(f"Cleaned up temp file: {screenshot_data['temp_path']}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp file: {e}")
```

#### 1.2 Update `ImageProcessingService.process_image_message()`
- Add cleanup in finally block
- Ensure cleanup happens even if processing fails

### Phase 2: Scheduled Background Cleanup
**Goal**: Periodic cleanup of orphaned files older than 1 hour

#### 2.1 Create Cleanup Task in `scheduler.py`
```python
async def cleanup_temp_cache():
    """Run every 30 minutes to clean files older than 1 hour"""
    cache = get_screenshot_cache()
    if cache:
        deleted = cache.cleanup_old_files(max_age_minutes=60)
        logger.info(f"Temp cache cleanup: removed {deleted} old files")
```

#### 2.2 Register in Main Application
- Add to existing scheduler
- Run every 30 minutes
- Log cleanup statistics

### Phase 3: Enhanced Cache Management
**Goal**: Implement proper cache lifecycle management

#### 3.1 Enhance `TemporaryFileCache` class
```python
class TemporaryFileCache:
    def __init__(self, cache_dir: str, ttl_minutes: int = 60):
        self.cache_dir = cache_dir
        self.ttl_minutes = ttl_minutes
        self._ensure_cache_dir()
        
    async def store_file_with_ttl(self, file_data: bytes, suffix: str) -> str:
        """Store file and schedule automatic deletion"""
        file_path = self.store_file(file_data, suffix)
        # Schedule deletion after TTL
        asyncio.create_task(self._delete_after_ttl(file_path))
        return file_path
        
    async def _delete_after_ttl(self, file_path: str):
        """Delete file after TTL expires"""
        await asyncio.sleep(self.ttl_minutes * 60)
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                logger.debug(f"TTL cleanup: {file_path}")
        except Exception as e:
            logger.warning(f"TTL cleanup failed: {e}")
```

#### 3.2 Add Cache Statistics
```python
def get_cache_stats(self) -> dict:
    """Get cache usage statistics"""
    files = list(self.cache_dir.glob("*"))
    total_size = sum(f.stat().st_size for f in files)
    oldest = min(files, key=lambda f: f.stat().st_mtime) if files else None
    
    return {
        "file_count": len(files),
        "total_size_mb": total_size / 1024 / 1024,
        "oldest_file_age_hours": (time.time() - oldest.stat().st_mtime) / 3600 if oldest else 0
    }
```

### Phase 4: Configuration & Monitoring
**Goal**: Make cache behavior configurable and observable

#### 4.1 Add Configuration Options
```python
# In config.py
TEMP_CACHE_TTL_MINUTES = int(os.getenv("TEMP_CACHE_TTL_MINUTES", "60"))
TEMP_CACHE_MAX_SIZE_MB = int(os.getenv("TEMP_CACHE_MAX_SIZE_MB", "100"))
TEMP_CACHE_CLEANUP_INTERVAL_MINUTES = int(os.getenv("TEMP_CACHE_CLEANUP_INTERVAL_MINUTES", "30"))
```

#### 4.2 Add Admin Commands
```python
@router.message(Command('cache_stats'))
async def show_cache_stats(message: Message):
    """Show temp cache statistics (admin only)"""
    if message.from_user.id not in ADMIN_IDS:
        return
        
    cache = get_screenshot_cache()
    stats = cache.get_cache_stats()
    
    await message.reply(
        f"📊 Cache Statistics:\n"
        f"Files: {stats['file_count']}\n"
        f"Size: {stats['total_size_mb']:.1f} MB\n"
        f"Oldest: {stats['oldest_file_age_hours']:.1f} hours"
    )
```

## Testing Strategy

### Unit Tests
1. Test immediate cleanup after processing
2. Test TTL-based cleanup
3. Test cleanup failure handling
4. Test cache statistics

### Integration Tests
1. Full image processing flow with cleanup verification
2. Concurrent image processing with proper cleanup
3. Cleanup task scheduling and execution
4. Error scenarios (permissions, disk full, etc.)

### Manual Testing
1. Send multiple images and verify cleanup
2. Monitor disk usage during heavy usage
3. Verify no user data leakage
4. Test with various file types and sizes

## Rollout Plan

### Stage 1: Immediate Cleanup (Low Risk)
- Implement Phase 1 only
- Deploy to test environment
- Monitor for 24 hours
- Deploy to production

### Stage 2: Background Cleanup (Medium Risk)
- Add Phase 2 scheduled cleanup
- Test with shortened intervals (5 min)
- Monitor system resources
- Deploy with 30-minute interval

### Stage 3: Full Implementation (Higher Risk)
- Implement Phases 3-4
- Extensive testing in staging
- Gradual rollout with feature flags
- Monitor performance metrics

## Success Metrics
- Zero cache files older than configured TTL
- Disk usage stable over time
- No performance degradation
- No user data exposed
- Cleanup errors < 1%

## Potential Issues & Mitigations

### Issue 1: File Still Being Processed
**Mitigation**: Check file locks before deletion, retry with backoff

### Issue 2: Disk I/O Performance
**Mitigation**: Batch deletions, use async I/O, rate limiting

### Issue 3: Race Conditions
**Mitigation**: Use file locking, atomic operations, proper error handling

### Issue 4: Permission Errors
**Mitigation**: Proper error handling, admin alerts, fallback to manual cleanup

## Alternative Approaches Considered

1. **In-Memory Cache**: Too memory intensive for images
2. **External Storage (S3)**: Over-engineering for temporary files
3. **OS-level tmpfs**: Platform-specific, complex deployment
4. **Redis with TTL**: Additional dependency for simple use case

## Conclusion
The proposed solution provides a robust, scalable approach to managing temporary cache files with minimal complexity. The phased implementation allows for safe rollout with proper testing at each stage.