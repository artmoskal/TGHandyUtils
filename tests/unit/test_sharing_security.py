import pytest
from unittest.mock import Mock
from datetime import datetime, timedelta

from services.sharing_service import SharingService
from models.shared_authorization import SharedAuthorization

@pytest.fixture
def mock_sharing_service():
    mock_repo = Mock()
    mock_user_service = Mock()
    return SharingService(mock_repo, mock_user_service)

def test_shared_authorization_prevents_self_sharing(mock_sharing_service):
    """Test that users cannot share accounts with themselves."""
    with pytest.raises(ValueError) as exc_info:
        mock_sharing_service.create_shared_authorization(
            owner_user_id=100,
            grantee_user_id=100,  # Same user
            owner_recipient_id=1
        )
    
    assert "Cannot share account with yourself" in str(exc_info.value)

def test_shared_authorization_prevents_duplicate(mock_sharing_service):
    """Test that duplicate authorizations are prevented."""
    # Mock existing authorization
    existing_auth = SharedAuthorization(
        id=1,
        owner_user_id=100,
        grantee_user_id=200,
        owner_recipient_id=1,
        status='accepted'
    )
    
    mock_sharing_service.repository.get_shared_authorization.return_value = existing_auth
    
    with pytest.raises(ValueError) as exc_info:
        mock_sharing_service.create_shared_authorization(
            owner_user_id=100,
            grantee_user_id=200,
            owner_recipient_id=1
        )
    
    assert "Authorization already exists" in str(exc_info.value)

def test_accept_authorization_by_correct_grantee(mock_sharing_service):
    """Test that only the correct grantee can accept authorization."""
    auth = SharedAuthorization(
        id=1,
        owner_user_id=100,
        grantee_user_id=200,
        owner_recipient_id=1,
        status='pending'
    )
    
    mock_sharing_service.repository.get_shared_authorization_by_id.return_value = auth
    mock_sharing_service.repository.update_shared_authorization_status.return_value = True
    mock_sharing_service.repository.get_recipient_by_id.return_value = Mock(
        name="Test Account", platform_type="todoist"
    )
    mock_sharing_service.repository.add_shared_recipient.return_value = 5
    
    # Correct grantee accepts
    success = mock_sharing_service.accept_shared_authorization(1, 200)
    
    assert success is True

def test_accept_authorization_by_wrong_user(mock_sharing_service):
    """Test that wrong user cannot accept authorization."""
    auth = SharedAuthorization(
        id=1,
        owner_user_id=100,
        grantee_user_id=200,
        owner_recipient_id=1,
        status='pending'
    )
    
    mock_sharing_service.repository.get_shared_authorization_by_id.return_value = auth
    
    # Should return False for wrong user
    success = mock_sharing_service.accept_shared_authorization(1, 300)  # Wrong user
    assert success is False

def test_accept_authorization_wrong_status(mock_sharing_service):
    """Test that only pending authorizations can be accepted."""
    auth = SharedAuthorization(
        id=1,
        owner_user_id=100,
        grantee_user_id=200,
        owner_recipient_id=1,
        status='revoked'  # Not pending
    )
    
    mock_sharing_service.repository.get_shared_authorization_by_id.return_value = auth
    
    # Should return False for wrong status
    success = mock_sharing_service.accept_shared_authorization(1, 200)
    assert success is False

def test_revoke_authorization_by_owner(mock_sharing_service):
    """Test that owner can revoke authorization."""
    auth = SharedAuthorization(
        id=1,
        owner_user_id=100,
        grantee_user_id=200,
        owner_recipient_id=1,
        status='accepted'
    )
    
    mock_sharing_service.repository.get_shared_authorization_by_id.return_value = auth
    mock_sharing_service.repository.update_shared_authorization_status.return_value = True
    
    success = mock_sharing_service.revoke_shared_authorization(1, 100)  # Owner revokes
    
    assert success is True
    mock_sharing_service.repository.update_shared_authorization_status.assert_called_with(1, 'revoked')

def test_revoke_authorization_by_non_owner(mock_sharing_service):
    """Test that non-owner cannot revoke authorization."""
    auth = SharedAuthorization(
        id=1,
        owner_user_id=100,
        grantee_user_id=200,
        owner_recipient_id=1,
        status='accepted'
    )
    
    mock_sharing_service.repository.get_shared_authorization_by_id.return_value = auth
    
    # Should return False for non-owner
    success = mock_sharing_service.revoke_shared_authorization(1, 300)  # Not owner
    assert success is False

def test_decline_authorization_by_grantee(mock_sharing_service):
    """Test that grantee can decline authorization."""
    auth = SharedAuthorization(
        id=1,
        owner_user_id=100,
        grantee_user_id=200,
        owner_recipient_id=1,
        status='pending'
    )
    
    mock_sharing_service.repository.get_shared_authorization_by_id.return_value = auth
    mock_sharing_service.repository.update_shared_authorization_status.return_value = True
    
    success = mock_sharing_service.decline_shared_authorization(1, 200)  # Grantee declines
    
    assert success is True
    mock_sharing_service.repository.update_shared_authorization_status.assert_called_with(1, 'declined')

def test_decline_authorization_by_non_grantee(mock_sharing_service):
    """Test that non-grantee cannot decline authorization."""
    auth = SharedAuthorization(
        id=1,
        owner_user_id=100,
        grantee_user_id=200,
        owner_recipient_id=1,
        status='pending'
    )
    
    mock_sharing_service.repository.get_shared_authorization_by_id.return_value = auth
    
    # Should return False for non-grantee
    success = mock_sharing_service.decline_shared_authorization(1, 300)  # Not grantee
    assert success is False

def test_platform_type_validation(mock_sharing_service):
    """Test that only valid platform types are accepted for auth requests."""
    mock_sharing_service.user_service.get_user_id_from_username.return_value = 200
    
    with pytest.raises(ValueError) as exc_info:
        mock_sharing_service.create_auth_request(
            requester_user_id=100,
            target_username="test_user",
            platform_type="invalid_platform",  # Invalid platform
            recipient_name="Test Account"
        )
    
    assert "Invalid platform type" in str(exc_info.value)