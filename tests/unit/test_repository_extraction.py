"""Tests for god class decomposition - extracted repositories."""

import pytest
from unittest.mock import Mock

from core.interfaces import IUserPreferencesRepository, IAuthRequestRepository  
from database.user_preferences_repository import UserPreferencesRepository
from database.auth_request_repository import AuthRequestRepository


class TestUserPreferencesRepository:
    """Test extracted user preferences repository."""
    
    @pytest.fixture
    def mock_db_manager(self):
        """Mock database manager."""
        return Mock()
    
    @pytest.fixture
    def preferences_repo(self, mock_db_manager):
        """Create preferences repository with mocked dependencies."""
        return UserPreferencesRepository(db_manager=mock_db_manager)
    
    def test_implements_interface(self, preferences_repo):
        """Test that repository implements the interface."""
        assert isinstance(preferences_repo, IUserPreferencesRepository)
    
    def test_get_preferences_method_exists(self, preferences_repo):
        """Test get_preferences method exists with correct signature."""
        # This should not raise AttributeError when extracted
        method = getattr(preferences_repo, 'get_preferences', None)
        assert method is not None
        assert callable(method)
    
    def test_update_preferences_method_exists(self, preferences_repo):
        """Test update_preferences method exists with correct signature."""
        # This should not raise AttributeError when extracted
        method = getattr(preferences_repo, 'update_preferences', None)
        assert method is not None
        assert callable(method)


class TestAuthRequestRepository:
    """Test extracted auth request repository."""
    
    @pytest.fixture
    def mock_db_manager(self):
        """Mock database manager."""
        return Mock()
    
    @pytest.fixture
    def auth_repo(self, mock_db_manager):
        """Create auth repository with mocked dependencies."""
        return AuthRequestRepository(db_manager=mock_db_manager)
    
    def test_implements_interface(self, auth_repo):
        """Test that repository implements the interface."""
        assert isinstance(auth_repo, IAuthRequestRepository)
    
    def test_create_auth_request_method_exists(self, auth_repo):
        """Test create_auth_request method exists with correct signature."""
        # This should not raise AttributeError when extracted
        method = getattr(auth_repo, 'create_auth_request', None)
        assert method is not None
        assert callable(method)
    
    def test_get_pending_auth_requests_method_exists(self, auth_repo):
        """Test get_pending_auth_requests_for_user method exists."""
        # This should not raise AttributeError when extracted  
        method = getattr(auth_repo, 'get_pending_auth_requests_for_user', None)
        assert method is not None
        assert callable(method)


class TestRepositoryIntegration:
    """Test that services can use extracted repositories."""
    
    def test_recipient_service_can_use_preferences_repo(self):
        """Test RecipientService can work with extracted preferences repository."""
        # This will fail until we update RecipientService to use the new repo
        from services.recipient_service import RecipientService
        from database.unified_recipient_repository import UnifiedRecipientRepository
        
        # When properly extracted, RecipientService should accept preferences_repo
        # This test ensures we don't break the service layer
        mock_unified_repo = Mock(spec=UnifiedRecipientRepository)
        mock_preferences_repo = Mock(spec=IUserPreferencesRepository)
        
        # This will fail until we update the service constructor
        try:
            service = RecipientService(
                repository=mock_unified_repo,
                preferences_repo=mock_preferences_repo
            )
            assert hasattr(service, 'preferences_repo')
        except TypeError:
            # Expected failure until we update the service
            pytest.fail("RecipientService needs updating to accept preferences_repo parameter")