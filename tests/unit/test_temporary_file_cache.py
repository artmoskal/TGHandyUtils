"""Tests for temporary file cache."""

import pytest
import os
import time
import tempfile
import shutil
from services.temporary_file_cache import TemporaryFileCache


class TestTemporaryFileCache:
    """Test cases for temporary file cache."""
    
    def setup_method(self):
        """Set up test with temporary directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.cache = TemporaryFileCache(cache_dir=self.temp_dir, max_age_seconds=2)
    
    def teardown_method(self):
        """Clean up test directory."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def test_store_and_retrieve_screenshot(self):
        """Test storing and retrieving screenshot data."""
        file_id = "test_file_123"
        image_data = b"fake_image_data_bytes"
        file_name = "test_screenshot.jpg"
        
        # Store screenshot
        self.cache.store_screenshot(file_id, image_data, file_name)
        
        # Retrieve screenshot
        result = self.cache.get_screenshot(file_id)
        
        assert result is not None
        assert result['image_data'] == image_data
        assert result['file_name'] == file_name
        assert result['file_id'] == file_id
    
    def test_get_nonexistent_screenshot(self):
        """Test retrieving screenshot that doesn't exist."""
        result = self.cache.get_screenshot("nonexistent_file")
        assert result is None
    
    def test_screenshot_expiration(self):
        """Test that screenshots expire after max age."""
        file_id = "test_file_expire"
        image_data = b"fake_image_data"
        file_name = "test.jpg"
        
        # Store screenshot
        self.cache.store_screenshot(file_id, image_data, file_name)
        
        # Should be retrievable immediately
        result = self.cache.get_screenshot(file_id)
        assert result is not None
        
        # Wait for expiration (cache has 2 second timeout)
        time.sleep(2.5)
        
        # Should be expired now
        result = self.cache.get_screenshot(file_id)
        assert result is None
    
    def test_cache_directory_creation(self):
        """Test that cache directory is created."""
        assert os.path.exists(self.temp_dir)
    
    def test_file_storage_on_disk(self):
        """Test that files are actually stored on disk."""
        file_id = "test_file_disk"
        image_data = b"test_data_on_disk"
        file_name = "disk_test.jpg"
        
        self.cache.store_screenshot(file_id, image_data, file_name)
        
        # Check that file exists on disk
        expected_path = os.path.join(self.temp_dir, f"{file_id}.jpg")
        assert os.path.exists(expected_path)
        
        # Check file contents
        with open(expected_path, 'rb') as f:
            stored_data = f.read()
        assert stored_data == image_data
    
    def test_clear_all_cache(self):
        """Test clearing all cached files."""
        # Store multiple screenshots
        for i in range(3):
            file_id = f"test_file_{i}"
            image_data = f"test_data_{i}".encode()
            file_name = f"test_{i}.jpg"
            self.cache.store_screenshot(file_id, image_data, file_name)
        
        # Verify files exist
        for i in range(3):
            result = self.cache.get_screenshot(f"test_file_{i}")
            assert result is not None
        
        # Clear all
        self.cache.clear_all()
        
        # Verify all files are gone
        for i in range(3):
            result = self.cache.get_screenshot(f"test_file_{i}")
            assert result is None
    
    def test_global_cache_instance(self):
        """Test that global cache instance works."""
        from services.temporary_file_cache import get_screenshot_cache
        
        cache1 = get_screenshot_cache()
        cache2 = get_screenshot_cache()
        
        # Should be the same instance
        assert cache1 is cache2