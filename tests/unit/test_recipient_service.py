"""Unit tests for recipient service - clean system only."""

import pytest
from unittest.mock import Mock

from services.recipient_service import RecipientService
from models.recipient import (
    UserPlatform, UserPlatformCreate, SharedRecipient, SharedRecipientCreate,
    Recipient, UserPreferencesV2
)


class TestRecipientService:
    """Test cases for RecipientService."""
    
    @pytest.fixture
    def mock_platform_repo(self):
        """Mock user platform repository."""
        mock = Mock()
        mock.get_user_platforms.return_value = [
            UserPlatform(
                id=1,
                telegram_user_id=12345,
                platform_type="todoist",
                credentials="token123",
                platform_config=None,
                enabled=True
            )
        ]
        mock.add_platform.return_value = 1
        mock.remove_platform.return_value = True
        mock.get_platform_by_type.return_value = None
        return mock
    
    @pytest.fixture
    def mock_shared_repo(self):
        """Mock shared recipient repository."""
        mock = Mock()
        mock.get_shared_recipients.return_value = [
            SharedRecipient(
                id=1,
                telegram_user_id=12345,
                name="Team Trello",
                platform_type="trello",
                credentials="key123:token456",
                platform_config={"board_id": "board123", "list_id": "list456"},
                enabled=True
            )
        ]
        mock.add_recipient.return_value = 1
        mock.remove_recipient.return_value = True
        mock.get_recipient_by_id.return_value = None
        return mock
    
    @pytest.fixture
    def mock_prefs_repo(self):
        """Mock preferences repository."""
        mock = Mock()
        mock.get_preferences.return_value = UserPreferencesV2(
            telegram_user_id=12345,
            default_recipients=["platform_1"],
            show_recipient_ui=True
        )
        mock.create_preferences.return_value = True
        mock.update_preferences.return_value = True
        return mock
    
    @pytest.fixture
    def recipient_service(self, mock_platform_repo, mock_shared_repo, mock_prefs_repo):
        """Create recipient service with mocked dependencies."""
        return RecipientService(
            platform_repo=mock_platform_repo,
            shared_repo=mock_shared_repo,
            prefs_repo=mock_prefs_repo
        )
    
    def test_get_all_recipients(self, recipient_service):
        """Test getting all recipients combines platforms and shared."""
        recipients = recipient_service.get_all_recipients(12345)
        
        assert len(recipients) == 2
        assert any(r.name == "My Todoist" and r.type == "user_platform" for r in recipients)
        assert any(r.name == "Team Trello" and r.type == "shared_recipient" for r in recipients)
    
    def test_get_enabled_recipients(self, recipient_service):
        """Test getting only enabled recipients."""
        recipients = recipient_service.get_enabled_recipients(12345)
        
        # Both mock recipients are enabled
        assert len(recipients) == 2
        assert all(r.enabled for r in recipients)
    
    def test_add_user_platform(self, recipient_service, mock_platform_repo):
        """Test adding user platform."""
        platform = UserPlatformCreate(
            platform_type="todoist",
            credentials="new_token",
            enabled=True
        )
        
        recipient_id = recipient_service.add_user_platform(12345, platform)
        
        assert recipient_id == "platform_1"
        mock_platform_repo.add_platform.assert_called_once_with(12345, platform)
    
    def test_add_shared_recipient(self, recipient_service, mock_shared_repo):
        """Test adding shared recipient."""
        recipient = SharedRecipientCreate(
            name="Friend's Todoist",
            platform_type="todoist",
            credentials="friend_token",
            enabled=True
        )
        
        recipient_id = recipient_service.add_shared_recipient(12345, recipient)
        
        assert recipient_id == "shared_1"
        mock_shared_repo.add_recipient.assert_called_once_with(12345, recipient)
    
    def test_get_default_recipients_with_prefs(self, recipient_service):
        """Test getting default recipients when preferences exist."""
        defaults = recipient_service.get_default_recipients(12345)
        
        # Should return all enabled recipients since mock prefs specify platform_1
        assert len(defaults) >= 1
    
    def test_is_recipient_ui_enabled(self, recipient_service):
        """Test checking if recipient UI is enabled."""
        result = recipient_service.is_recipient_ui_enabled(12345)
        assert result is True
    
    def test_get_recipient_credentials_platform(self, recipient_service, mock_platform_repo):
        """Test getting credentials for user platform."""
        # Mock return value for credentials lookup
        mock_platform_repo.get_user_platforms.return_value = [
            UserPlatform(
                id=1,
                telegram_user_id=12345,
                platform_type="todoist",
                credentials="token123",
                platform_config=None,
                enabled=True
            )
        ]
        
        credentials = recipient_service.get_recipient_credentials(12345, "platform_1")
        assert credentials == "token123"
    
    def test_get_recipient_credentials_shared(self, recipient_service, mock_shared_repo):
        """Test getting credentials for shared recipient."""
        mock_shared_repo.get_recipient_by_id.return_value = SharedRecipient(
            id=1,
            telegram_user_id=12345,
            name="Team Trello",
            platform_type="trello",
            credentials="key123:token456",
            platform_config=None,
            enabled=True
        )
        
        credentials = recipient_service.get_recipient_credentials(12345, "shared_1")
        assert credentials == "key123:token456"
    
    def test_remove_recipient_platform(self, recipient_service, mock_platform_repo):
        """Test removing user platform."""
        # Mock platform lookup
        mock_platform_repo.get_user_platforms.return_value = [
            UserPlatform(
                id=1,
                telegram_user_id=12345,
                platform_type="todoist",
                credentials="token123",
                platform_config=None,
                enabled=True
            )
        ]
        
        result = recipient_service.remove_recipient(12345, "platform_1")
        assert result is True
        mock_platform_repo.remove_platform.assert_called_once_with(12345, "todoist")
    
    def test_remove_recipient_shared(self, recipient_service, mock_shared_repo):
        """Test removing shared recipient."""
        result = recipient_service.remove_recipient(12345, "shared_1")
        assert result is True
        mock_shared_repo.remove_recipient.assert_called_once_with(12345, 1)