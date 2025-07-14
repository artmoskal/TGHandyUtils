"""Tests for updated SharingService with username discovery."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

from services.sharing_service import SharingService
from models.auth_request import AuthRequest
from models.user import User


class TestSharingServiceUpdated:
    """Test cases for updated sharing service."""
    
    @pytest.fixture
    def mock_repository(self):
        """Create mock repository."""
        return Mock()
    
    @pytest.fixture
    def mock_user_service(self):
        """Create mock user service."""
        return Mock()
    
    @pytest.fixture
    def sharing_service(self, mock_repository, mock_user_service):
        """Create sharing service with mocks."""
        return SharingService(mock_repository, mock_user_service)
    
    def test_create_auth_request_existing_user(self, sharing_service, mock_repository, mock_user_service):
        """Test creating auth request for existing user."""
        # Setup mocks
        mock_user_service.find_user_by_username.return_value = 200  # User exists
        
        mock_auth_request = AuthRequest(
            id=1,
            requester_user_id=100,
            target_user_id=200,
            platform_type="todoist",
            recipient_name="Test Account",
            status="pending",
            expires_at=datetime.now() + timedelta(hours=24),
            completed_recipient_id=None,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        mock_repository.create_auth_request.return_value = mock_auth_request
        
        # Call method
        result = sharing_service.create_auth_request(
            requester_user_id=100,
            target_username="@testuser",
            platform_type="todoist",
            recipient_name="Test Account"
        )
        
        # Verify result
        assert result['status'] == 'created'
        assert result['auth_request'] == mock_auth_request
        assert result['target_user_id'] == 200
        assert result['target_username'] == 'testuser'
        
        # Verify repository was called
        mock_repository.create_auth_request.assert_called_once()
        call_kwargs = mock_repository.create_auth_request.call_args[1]
        assert call_kwargs['requester_user_id'] == 100
        assert call_kwargs['target_user_id'] == 200
        assert call_kwargs['platform_type'] == 'todoist'
        assert call_kwargs['recipient_name'] == 'Test Account'
    
    def test_create_auth_request_non_existing_user(self, sharing_service, mock_user_service):
        """Test creating auth request for non-existing user."""
        # Setup mocks
        mock_user_service.find_user_by_username.return_value = None  # User not found
        
        # Call method
        result = sharing_service.create_auth_request(
            requester_user_id=100,
            target_username="@unknownuser",
            platform_type="trello",
            recipient_name="Test Board"
        )
        
        # Verify result
        assert result['status'] == 'user_not_found'
        assert result['target_username'] == 'unknownuser'
    
    def test_create_auth_request_self_request(self, sharing_service, mock_user_service):
        """Test preventing self auth requests."""
        # Setup mocks
        mock_user_service.find_user_by_username.return_value = 100  # Same as requester
        
        # Call method should raise error
        with pytest.raises(ValueError) as exc:
            sharing_service.create_auth_request(
                requester_user_id=100,
                target_username="@myusername",
                platform_type="todoist",
                recipient_name="My Account"
            )
        
        assert "can't request authentication from yourself" in str(exc.value)
    
    def test_create_auth_request_self_request_allowed(self, mock_repository, mock_user_service):
        """Test allowing self auth requests when config allows it."""
        # Create config that allows self auth
        mock_config = Mock()
        mock_config.ALLOW_SELF_AUTH_REQUESTS = True
        
        # Create service with config
        service = SharingService(mock_repository, mock_user_service, mock_config)
        
        # Setup mocks
        mock_user_service.find_user_by_username.return_value = 100  # Same as requester
        
        mock_auth_request = AuthRequest(
            id=1,
            requester_user_id=100,
            target_user_id=100,
            platform_type="todoist",
            recipient_name="Test Account",
            status="pending",
            expires_at=datetime.now() + timedelta(hours=24),
            completed_recipient_id=None,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        mock_repository.create_auth_request.return_value = mock_auth_request
        
        # Call method - should NOT raise error
        result = service.create_auth_request(
            requester_user_id=100,
            target_username="@myusername",
            platform_type="todoist",
            recipient_name="My Account"
        )
        
        # Verify result
        assert result['status'] == 'created'
        assert result['auth_request'] == mock_auth_request
    
    def test_create_auth_request_invalid_platform(self, sharing_service):
        """Test validation of platform type."""
        # Call method with invalid platform
        with pytest.raises(ValueError) as exc:
            sharing_service.create_auth_request(
                requester_user_id=100,
                target_username="@testuser",
                platform_type="invalid_platform",
                recipient_name="Test"
            )
        
        assert "Invalid platform type" in str(exc.value)
    
    def test_get_pending_auth_requests(self, sharing_service, mock_repository):
        """Test getting pending auth requests."""
        # Setup mock
        mock_requests = [
            Mock(spec=AuthRequest),
            Mock(spec=AuthRequest)
        ]
        mock_repository.get_pending_auth_requests.return_value = mock_requests
        
        # Call method
        result = sharing_service.get_pending_auth_requests(100)
        
        # Verify
        assert result == mock_requests
        mock_repository.get_pending_auth_requests.assert_called_once_with(100)
    
    def test_complete_auth_request_success(self, sharing_service, mock_repository):
        """Test successful auth request completion."""
        # Setup mock auth request
        mock_auth_request = Mock(spec=AuthRequest)
        mock_auth_request.id = 1
        mock_auth_request.requester_user_id = 100
        mock_auth_request.target_user_id = 200
        mock_auth_request.platform_type = "todoist"
        mock_auth_request.recipient_name = "Test Account"
        mock_auth_request.is_active.return_value = True
        
        mock_repository.get_auth_request_by_id.return_value = mock_auth_request
        mock_repository.add_recipient.return_value = 5
        mock_repository.update_auth_request_status.return_value = True
        
        # Call method
        recipient_id = sharing_service.complete_auth_request(
            auth_request_id=1,
            target_user_id=200,
            credentials="test_token",
            platform_config='{"project_id": "123"}'
        )
        
        # Verify
        assert recipient_id == 5
        
        # Verify recipient created for requester
        mock_repository.add_recipient.assert_called_once()
        
        # Verify status updated
        mock_repository.update_auth_request_status.assert_called_once_with(
            1, 'completed', 5
        )
    
    def test_complete_auth_request_wrong_user(self, sharing_service, mock_repository):
        """Test completion by wrong user."""
        # Setup mock
        mock_auth_request = Mock(spec=AuthRequest)
        mock_auth_request.target_user_id = 200
        
        mock_repository.get_auth_request_by_id.return_value = mock_auth_request
        
        # Call should raise error
        with pytest.raises(ValueError) as exc:
            sharing_service.complete_auth_request(
                auth_request_id=1,
                target_user_id=300,  # Wrong user
                credentials="token"
            )
        
        assert "Not authorized" in str(exc.value)
    
    def test_cancel_auth_request_authorized(self, sharing_service, mock_repository):
        """Test cancelling by authorized user."""
        # Setup mock
        mock_auth_request = Mock(spec=AuthRequest)
        mock_auth_request.requester_user_id = 100
        mock_auth_request.target_user_id = 200
        
        mock_repository.get_auth_request_by_id.return_value = mock_auth_request
        mock_repository.update_auth_request_status.return_value = True
        
        # Test requester cancelling
        result = sharing_service.cancel_auth_request(1, 100)
        assert result is True
        
        # Test target cancelling
        result = sharing_service.cancel_auth_request(1, 200)
        assert result is True
    
    def test_cancel_auth_request_unauthorized(self, sharing_service, mock_repository):
        """Test cancelling by unauthorized user."""
        # Setup mock
        mock_auth_request = Mock(spec=AuthRequest)
        mock_auth_request.requester_user_id = 100
        mock_auth_request.target_user_id = 200
        
        mock_repository.get_auth_request_by_id.return_value = mock_auth_request
        
        # Should return False for unauthorized user
        result = sharing_service.cancel_auth_request(1, 300)
        assert result is False
        
        # Verify status was NOT updated
        mock_repository.update_auth_request_status.assert_not_called()