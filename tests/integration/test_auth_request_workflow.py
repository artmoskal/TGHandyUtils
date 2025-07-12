import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock

from services.sharing_service import SharingService
from models.auth_request import AuthRequest

@pytest.fixture
def mock_sharing_service():
    mock_repo = Mock()
    mock_user_service = Mock()
    return SharingService(mock_repo, mock_user_service)

def test_create_auth_request_success(mock_sharing_service):
    """Test successful auth request creation."""
    # Setup mocks
    mock_sharing_service.user_service.get_user_id_from_username.return_value = 200
    mock_sharing_service.repository.create_auth_request.return_value = 1
    
    auth_request_id = mock_sharing_service.create_auth_request(
        requester_user_id=100,
        target_username="test_user",
        platform_type="google_calendar", 
        recipient_name="Test Calendar"
    )
    
    assert auth_request_id == 1
    
    # Verify repository call
    mock_sharing_service.repository.create_auth_request.assert_called_once()
    call_args = mock_sharing_service.repository.create_auth_request.call_args[1]
    assert call_args['requester_user_id'] == 100
    assert call_args['target_user_id'] == 200
    assert call_args['platform_type'] == "google_calendar"
    assert call_args['recipient_name'] == "Test Calendar"

def test_create_auth_request_user_not_found(mock_sharing_service):
    """Test auth request creation with invalid username."""
    mock_sharing_service.user_service.get_user_id_from_username.return_value = None
    
    with pytest.raises(ValueError) as exc_info:
        mock_sharing_service.create_auth_request(
            requester_user_id=100,
            target_username="unknown_user",
            platform_type="google_calendar",
            recipient_name="Test Calendar"
        )
    
    assert "not found in bot users" in str(exc_info.value)

def test_create_auth_request_self_request(mock_sharing_service):
    """Test auth request creation with same user."""
    mock_sharing_service.user_service.get_user_id_from_username.return_value = 100
    
    with pytest.raises(ValueError) as exc_info:
        mock_sharing_service.create_auth_request(
            requester_user_id=100,
            target_username="self_user",
            platform_type="google_calendar",
            recipient_name="Test Calendar"
        )
    
    assert "Cannot request authentication from yourself" in str(exc_info.value)

def test_complete_auth_request_success(mock_sharing_service):
    """Test successful auth request completion."""
    # Create active auth request
    auth_request = AuthRequest(
        id=1,
        requester_user_id=100,
        target_user_id=200,
        platform_type="google_calendar",
        recipient_name="Test Calendar",
        status='pending',
        expires_at=datetime.utcnow() + timedelta(hours=1)
    )
    
    mock_sharing_service.repository.get_auth_request_by_id.return_value = auth_request
    mock_sharing_service.repository.add_personal_recipient.return_value = 5
    mock_sharing_service.repository.update_auth_request_status.return_value = True
    
    recipient_id = mock_sharing_service.complete_auth_request(
        auth_request_id=1,
        target_user_id=200,
        credentials='{"token": "test_token"}',
        platform_config='{"calendar_id": "primary"}'
    )
    
    assert recipient_id == 5
    
    # Verify recipient created for requester (not target)
    mock_sharing_service.repository.add_personal_recipient.assert_called_with(
        user_id=100,  # Requester gets the account
        name="Test Calendar",
        platform_type="google_calendar",
        credentials='{"token": "test_token"}',
        platform_config='{"calendar_id": "primary"}'
    )
    
    # Verify status update
    mock_sharing_service.repository.update_auth_request_status.assert_called_with(
        1, 'completed', 5
    )

def test_complete_auth_request_expired(mock_sharing_service):
    """Test completing expired auth request."""
    # Create expired auth request
    auth_request = AuthRequest(
        id=1,
        requester_user_id=100,
        target_user_id=200,
        platform_type="google_calendar",
        recipient_name="Test Calendar",
        status='pending',
        expires_at=datetime.utcnow() - timedelta(hours=1)  # Expired
    )
    
    mock_sharing_service.repository.get_auth_request_by_id.return_value = auth_request
    
    with pytest.raises(ValueError) as exc_info:
        mock_sharing_service.complete_auth_request(
            auth_request_id=1,
            target_user_id=200,
            credentials='{"token": "test_token"}'
        )
    
    assert "expired or not active" in str(exc_info.value)

def test_complete_auth_request_wrong_user(mock_sharing_service):
    """Test completing auth request by wrong user."""
    auth_request = AuthRequest(
        id=1,
        requester_user_id=100,
        target_user_id=200,
        platform_type="google_calendar",
        recipient_name="Test Calendar",
        status='pending',
        expires_at=datetime.utcnow() + timedelta(hours=1)
    )
    
    mock_sharing_service.repository.get_auth_request_by_id.return_value = auth_request
    
    with pytest.raises(ValueError) as exc_info:
        mock_sharing_service.complete_auth_request(
            auth_request_id=1,
            target_user_id=300,  # Wrong user
            credentials='{"token": "test_token"}'
        )
    
    assert "Not authorized to complete" in str(exc_info.value)

def test_cancel_auth_request_by_requester(mock_sharing_service):
    """Test cancelling auth request by requester."""
    auth_request = AuthRequest(
        id=1,
        requester_user_id=100,
        target_user_id=200,
        platform_type="google_calendar",
        recipient_name="Test Calendar",
        status='pending',
        expires_at=datetime.utcnow() + timedelta(hours=1)
    )
    
    mock_sharing_service.repository.get_auth_request_by_id.return_value = auth_request
    mock_sharing_service.repository.update_auth_request_status.return_value = True
    
    success = mock_sharing_service.cancel_auth_request(1, 100)  # Requester cancels
    
    assert success is True
    mock_sharing_service.repository.update_auth_request_status.assert_called_with(1, 'cancelled')

def test_cancel_auth_request_by_target(mock_sharing_service):
    """Test cancelling auth request by target user."""
    auth_request = AuthRequest(
        id=1,
        requester_user_id=100,
        target_user_id=200,
        platform_type="google_calendar",
        recipient_name="Test Calendar",
        status='pending',
        expires_at=datetime.utcnow() + timedelta(hours=1)
    )
    
    mock_sharing_service.repository.get_auth_request_by_id.return_value = auth_request
    mock_sharing_service.repository.update_auth_request_status.return_value = True
    
    success = mock_sharing_service.cancel_auth_request(1, 200)  # Target cancels
    
    assert success is True

def test_cancel_auth_request_unauthorized(mock_sharing_service):
    """Test cancelling auth request by unauthorized user."""
    auth_request = AuthRequest(
        id=1,
        requester_user_id=100,
        target_user_id=200,
        platform_type="google_calendar",
        recipient_name="Test Calendar",
        status='pending',
        expires_at=datetime.utcnow() + timedelta(hours=1)
    )
    
    mock_sharing_service.repository.get_auth_request_by_id.return_value = auth_request
    
    # Should return False for unauthorized user
    success = mock_sharing_service.cancel_auth_request(1, 300)  # Unauthorized user
    assert success is False

def test_cleanup_expired_requests(mock_sharing_service):
    """Test cleanup of expired requests."""
    mock_sharing_service.repository.cleanup_expired_auth_requests.return_value = 3
    
    count = mock_sharing_service.cleanup_expired_requests()
    
    assert count == 3
    mock_sharing_service.repository.cleanup_expired_auth_requests.assert_called_once()