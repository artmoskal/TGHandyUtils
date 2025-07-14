"""Tests for UserService."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock
from aiogram import types

from services.user_service import UserService
from database.user.user_repository import UserRepository
from models.user import User
from tests.factories.user_factory import UserFactory


class TestUserService:
    """Test cases for UserService."""
    
    @pytest.fixture
    def mock_repository(self):
        """Create mock user repository."""
        return Mock(spec=UserRepository)
    
    @pytest.fixture
    def user_service(self, mock_repository):
        """Create user service with mock repository."""
        return UserService(mock_repository)
    
    @pytest.fixture
    def telegram_user(self):
        """Create mock Telegram user."""
        user = Mock(spec=types.User)
        user.id = 123456
        user.username = "testuser"
        user.first_name = "Test"
        user.last_name = "User"
        return user
    
    def test_track_user(self, user_service, mock_repository, telegram_user):
        """Test tracking a Telegram user."""
        # Track user
        user_service.track_user(telegram_user)
        
        # Verify repository was called
        mock_repository.upsert_user.assert_called_once_with(
            user_id=123456,
            username="testuser",
            first_name="Test",
            last_name="User"
        )
        
        # Verify cache was updated
        assert "testuser" in user_service._cache
        cached_id, expires_at = user_service._cache["testuser"]
        assert cached_id == 123456
        assert expires_at > datetime.now()
    
    def test_track_user_no_username(self, user_service, mock_repository):
        """Test tracking user without username."""
        # Create user without username
        user = Mock(spec=types.User)
        user.id = 123456
        user.username = None
        user.first_name = "Test"
        user.last_name = "User"
        
        # Track user
        user_service.track_user(user)
        
        # Verify repository was called
        mock_repository.upsert_user.assert_called_once()
        
        # Verify no cache entry
        assert len(user_service._cache) == 0
    
    def test_find_user_by_username_cache_hit(self, user_service):
        """Test finding user with cache hit."""
        # Pre-populate cache
        expires_at = datetime.now() + timedelta(minutes=5)
        user_service._cache["testuser"] = (123456, expires_at)
        
        # Find user
        user_id = user_service.find_user_by_username("testuser")
        
        assert user_id == 123456
    
    def test_find_user_by_username_cache_expired(self, user_service, mock_repository):
        """Test finding user with expired cache."""
        # Pre-populate cache with expired entry
        expires_at = datetime.now() - timedelta(minutes=1)
        user_service._cache["testuser"] = (123456, expires_at)
        
        # Mock repository response
        mock_user = UserFactory(user_id=123456, username="testuser")
        mock_repository.find_by_username.return_value = mock_user
        
        # Find user
        user_id = user_service.find_user_by_username("testuser")
        
        # Verify expired entry was removed and new one added
        assert user_id == 123456
        assert "testuser" in user_service._cache
        _, new_expires = user_service._cache["testuser"]
        assert new_expires > datetime.now()
    
    def test_find_user_by_username_database_hit(self, user_service, mock_repository):
        """Test finding user from database."""
        # Mock repository response
        mock_user = UserFactory(user_id=123456, username="testuser")
        mock_repository.find_by_username.return_value = mock_user
        
        # Find user
        user_id = user_service.find_user_by_username("@testuser")
        
        # Verify result
        assert user_id == 123456
        
        # Verify cache was updated
        assert "testuser" in user_service._cache
        cached_id, _ = user_service._cache["testuser"]
        assert cached_id == 123456
    
    def test_find_user_by_username_not_found(self, user_service, mock_repository):
        """Test finding non-existent user."""
        # Mock repository response
        mock_repository.find_by_username.return_value = None
        
        # Find user
        user_id = user_service.find_user_by_username("nonexistent")
        
        # Verify result
        assert user_id is None
        
        # Verify no cache entry
        assert "nonexistent" not in user_service._cache
    
    def test_find_user_by_username_variations(self, user_service, mock_repository):
        """Test username normalization."""
        # Mock repository response
        mock_user = UserFactory(user_id=123456, username="TestUser")
        mock_repository.find_by_username.return_value = mock_user
        
        # Test various formats
        assert user_service.find_user_by_username("@TestUser") == 123456
        assert user_service.find_user_by_username("testuser") == 123456  # From cache
        assert user_service.find_user_by_username("  @TESTUSER  ") == 123456  # From cache
    
    def test_get_user_by_id(self, user_service, mock_repository):
        """Test getting user by ID."""
        # Mock repository response
        mock_user = UserFactory(user_id=123456)
        mock_repository.find_by_id.return_value = mock_user
        
        # Get user
        user = user_service.get_user_by_id(123456)
        
        # Verify
        assert user == mock_user
        mock_repository.find_by_id.assert_called_once_with(123456)
    
    def test_get_user_display_name_exists(self, user_service, mock_repository):
        """Test getting display name for existing user."""
        # Mock repository response
        mock_user = UserFactory(user_id=123456, first_name="Test")
        mock_repository.find_by_id.return_value = mock_user
        
        # Get display name
        name = user_service.get_user_display_name(123456)
        
        assert name == "Test"
    
    def test_get_user_display_name_not_found(self, user_service, mock_repository):
        """Test getting display name for non-existent user."""
        # Mock repository response
        mock_repository.find_by_id.return_value = None
        
        # Get display name
        name = user_service.get_user_display_name(123456)
        
        assert name == "User123456"
    
    def test_clean_cache(self, user_service):
        """Test cache cleaning."""
        # Add mix of valid and expired entries
        now = datetime.now()
        user_service._cache = {
            "expired1": (111111, now - timedelta(minutes=1)),
            "valid1": (222222, now + timedelta(minutes=5)),
            "expired2": (333333, now - timedelta(seconds=1)),
            "valid2": (444444, now + timedelta(minutes=3)),
        }
        
        # Clean cache
        user_service.clean_cache()
        
        # Verify only valid entries remain
        assert len(user_service._cache) == 2
        assert "valid1" in user_service._cache
        assert "valid2" in user_service._cache
        assert "expired1" not in user_service._cache
        assert "expired2" not in user_service._cache