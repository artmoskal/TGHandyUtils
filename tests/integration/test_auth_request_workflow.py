import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock

from services.sharing_service import SharingService
from models.auth_request import AuthRequest

pytestmark = pytest.mark.integration

@pytest.fixture
def mock_sharing_service():
    mock_repo = Mock()
    mock_user_service = Mock()
    return SharingService(mock_repo, mock_user_service)

def test_create_auth_request_success(mock_sharing_service):
    """Test successful auth request creation."""
    # Setup mocks
    mock_sharing_service.user_service.find_user_by_username.return_value = 200
    
    # Create mock auth request that will be returned
    mock_auth_request = AuthRequest(
        id=1,
        requester_user_id=100,
        target_user_id=200,
        platform_type="google_calendar",
        recipient_name="Test Calendar",
        status='pending',
        expires_at=datetime.now() + timedelta(hours=24)
    )
    mock_sharing_service.repository.create_auth_request.return_value = mock_auth_request
    
    result = mock_sharing_service.create_auth_request(
        requester_user_id=100,
        target_username="test_user",
        platform_type="google_calendar", 
        recipient_name="Test Calendar"
    )
    
    assert result['status'] == 'created'
    assert result['auth_request'] == mock_auth_request
    assert result['target_user_id'] == 200
    assert result['target_username'] == 'test_user'
    
    # Verify repository call
    mock_sharing_service.repository.create_auth_request.assert_called_once()
    call_args = mock_sharing_service.repository.create_auth_request.call_args[1]
    assert call_args['requester_user_id'] == 100
    assert call_args['target_user_id'] == 200
    assert call_args['platform_type'] == "google_calendar"
    assert call_args['recipient_name'] == "Test Calendar"

def test_create_auth_request_user_not_found(mock_sharing_service):
    """Test auth request creation with user not found - returns bot link info."""
    mock_sharing_service.user_service.find_user_by_username.return_value = None
    
    result = mock_sharing_service.create_auth_request(
        requester_user_id=100,
        target_username="unknown_user",
        platform_type="google_calendar",
        recipient_name="Test Calendar"
    )
    
    assert result['status'] == 'user_not_found'
    assert result['target_username'] == 'unknown_user'

def test_create_auth_request_self_request(mock_sharing_service):
    """Test auth request creation with same user."""
    mock_sharing_service.user_service.find_user_by_username.return_value = 100
    
    with pytest.raises(ValueError) as exc_info:
        mock_sharing_service.create_auth_request(
            requester_user_id=100,
            target_username="self_user",
            platform_type="google_calendar",
            recipient_name="Test Calendar"
        )
    
    assert "can't request authentication from yourself" in str(exc_info.value)

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
    mock_sharing_service.repository.add_recipient.return_value = 5
    mock_sharing_service.repository.update_auth_request_status.return_value = True
    
    recipient_id = mock_sharing_service.complete_auth_request(
        auth_request_id=1,
        target_user_id=200,
        credentials='{"token": "test_token"}',
        platform_config='{"calendar_id": "primary"}'
    )
    
    assert recipient_id == 5
    
    # Verify recipient created for requester (not target)
    # Check that add_recipient was called with correct user_id and UnifiedRecipientCreate object
    assert mock_sharing_service.repository.add_recipient.called
    call_args = mock_sharing_service.repository.add_recipient.call_args
    assert call_args[0][0] == 100  # Requester gets the account
    
    # Check the UnifiedRecipientCreate object properties
    recipient_create = call_args[0][1]
    assert recipient_create.name == "Test Calendar"
    assert recipient_create.platform_type == "google_calendar"
    assert recipient_create.credentials == '{"token": "test_token"}'
    assert recipient_create.platform_config == '{"calendar_id": "primary"}'
    assert recipient_create.is_personal == True
    assert recipient_create.enabled == True
    
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