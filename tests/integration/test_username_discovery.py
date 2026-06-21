"""Integration test for username discovery feature."""

import pytest
from datetime import datetime, timedelta

from composition.container import container
from models.user import User
from models.auth_request import AuthRequest


class TestUsernameDiscovery:
    """Test username discovery functionality end-to-end."""
    
    @pytest.fixture
    def setup_test_data(self):
        """Setup test data in database."""
        # Get services from container
        user_repo = container.user_repository()
        user_service = container.user_service()
        sharing_service = container.sharing_service()
        
        # Insert test users
        user_repo.upsert_user(100001, "alice", "Alice", "Smith")
        user_repo.upsert_user(100002, "bob", "Bob", "Jones")
        
        return {
            'user_repo': user_repo,
            'user_service': user_service,
            'sharing_service': sharing_service
        }
    
    def test_existing_user_auth_request(self, setup_test_data):
        """Test creating auth request for existing user."""
        services = setup_test_data
        sharing_service = services['sharing_service']
        
        # Alice requests auth from Bob
        result = sharing_service.create_auth_request(
            requester_user_id=100001,
            target_username="@bob",
            platform_type="todoist",
            recipient_name="Bob's Todoist"
        )
        
        # Should create auth request
        assert result['status'] == 'created'
        assert 'auth_request' in result
        assert result['target_user_id'] == 100002
        
        # Verify auth request was created
        auth_request = result['auth_request']
        assert isinstance(auth_request, AuthRequest)
        assert auth_request.requester_user_id == 100001
        assert auth_request.target_user_id == 100002
        assert auth_request.platform_type == "todoist"
        assert auth_request.recipient_name == "Bob's Todoist"
    
    def test_non_existing_user_bot_link(self, setup_test_data):
        """Test bot link generation for non-existing user."""
        services = setup_test_data
        sharing_service = services['sharing_service']
        
        # Alice requests auth from unknown user
        result = sharing_service.create_auth_request(
            requester_user_id=100001,
            target_username="@charlie",
            platform_type="trello",
            recipient_name="Charlie's Trello"
        )
        
        # Should return user_not_found
        assert result['status'] == 'user_not_found'
        assert result['target_username'] == 'charlie'
    
    def test_username_lookup_variations(self, setup_test_data):
        """Test username lookup with different formats."""
        services = setup_test_data
        user_service = services['user_service']
        
        # Test various username formats
        assert user_service.find_user_by_username("alice") == 100001
        assert user_service.find_user_by_username("@alice") == 100001
        assert user_service.find_user_by_username("ALICE") == 100001
        assert user_service.find_user_by_username(" @Alice ") == 100001
    
    def test_self_request_prevention(self, setup_test_data, monkeypatch):
        """Test self-request behavior based on config flag."""
        services = setup_test_data
        sharing_service = services['sharing_service']
        
        # Test 1: Self-requests blocked by default
        monkeypatch.setattr(sharing_service.config, 'ALLOW_SELF_AUTH_REQUESTS', False)
        
        with pytest.raises(ValueError) as exc:
            sharing_service.create_auth_request(
                requester_user_id=100001,
                target_username="@alice",
                platform_type="todoist",
                recipient_name="My Todoist"
            )
        assert "can't request authentication from yourself" in str(exc.value)
        
        # Test 2: Self-requests allowed when flag is set
        monkeypatch.setattr(sharing_service.config, 'ALLOW_SELF_AUTH_REQUESTS', True)
        
        result = sharing_service.create_auth_request(
            requester_user_id=100001,
            target_username="@alice",
            platform_type="todoist",
            recipient_name="My Todoist"
        )
        assert result['status'] == 'created'
        assert result['target_user_id'] == 100001
    
    def test_user_tracking_updates_info(self, setup_test_data):
        """Test that user tracking updates user information."""
        services = setup_test_data
        user_service = services['user_service']
        user_repo = services['user_repo']
        
        # Create mock telegram user with updated info
        from unittest.mock import Mock
        telegram_user = Mock()
        telegram_user.id = 100001
        telegram_user.username = "alice_new"
        telegram_user.first_name = "Alice Updated"
        telegram_user.last_name = "Smith Updated"
        
        # Track user
        user_service.track_user(telegram_user)
        
        # Verify user info was updated
        user = user_repo.find_by_id(100001)
        assert user.username == "alice_new"
        assert user.first_name == "Alice Updated"
        assert user.last_name == "Smith Updated"
    
    def test_get_pending_auth_requests(self, setup_test_data):
        """Test retrieving pending auth requests."""
        services = setup_test_data
        sharing_service = services['sharing_service']
        repo = container.unified_recipient_repository()
        
        # Get initial count of requests
        initial_bob_requests = repo.get_pending_auth_requests(100002)
        initial_alice_requests = repo.get_pending_auth_requests(100001)
        
        # Create multiple auth requests
        sharing_service.create_auth_request(100001, "@bob", "todoist", "Bob's Todoist")
        sharing_service.create_auth_request(100002, "@alice", "trello", "Alice's Trello")
        
        # Get Bob's pending requests (requests TO Bob)
        bob_requests = repo.get_pending_auth_requests(100002)
        assert len(bob_requests) == len(initial_bob_requests) + 1
        # Find the request from Alice to Bob
        alice_to_bob = next((r for r in bob_requests if r.requester_user_id == 100001), None)
        assert alice_to_bob is not None
        assert alice_to_bob.platform_type == "todoist"
        
        # Get Alice's pending requests (requests TO Alice)
        alice_requests = repo.get_pending_auth_requests(100001)
        assert len(alice_requests) == len(initial_alice_requests) + 1
        # Find the request from Bob to Alice
        bob_to_alice = next((r for r in alice_requests if r.requester_user_id == 100002), None)
        assert bob_to_alice is not None
        assert bob_to_alice.platform_type == "trello"