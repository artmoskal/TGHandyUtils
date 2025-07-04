"""Unit tests for recipient service - unified system only."""

import pytest
from unittest.mock import Mock

from services.recipient_service import RecipientService
from models.unified_recipient import UnifiedRecipient, UnifiedRecipientCreate


class TestRecipientService:
    """Test cases for RecipientService (unified system)."""
    
    @pytest.fixture
    def mock_recipient_repo(self):
        """Mock unified recipient repository."""
        mock = Mock()
        mock.get_all_recipients.return_value = [
            UnifiedRecipient(
                id=1,
                user_id=12345,
                name="My Todoist",
                platform_type="todoist",
                credentials="token123",
                platform_config=None,
                is_personal=True,
                is_default=True,
                enabled=True
            ),
            UnifiedRecipient(
                id=2,
                user_id=12345,
                name="Team Trello",
                platform_type="trello",
                credentials="key123:token456",
                platform_config={"board_id": "board123", "list_id": "list456"},
                is_personal=False,
                is_default=False,
                enabled=True
            )
        ]
        mock.get_enabled_recipients.return_value = [
            # Both recipients are enabled in mock data
            mock.get_all_recipients.return_value[0],
            mock.get_all_recipients.return_value[1]
        ]
        mock.get_personal_recipients.return_value = [
            mock.get_all_recipients.return_value[0]  # Only first is personal
        ]
        mock.get_shared_recipients.return_value = [
            mock.get_all_recipients.return_value[1]  # Only second is shared
        ]
        mock.get_default_recipients.return_value = [
            mock.get_all_recipients.return_value[0]  # Only first is default
        ]
        mock.add_recipient.return_value = 3
        mock.remove_recipient.return_value = True
        mock.toggle_recipient_enabled.return_value = True
        mock.get_recipient_by_id.return_value = mock.get_all_recipients.return_value[0]
        return mock
    
    @pytest.fixture
    def recipient_service(self, mock_recipient_repo):
        """Create recipient service with mocked dependencies."""
        return RecipientService(repository=mock_recipient_repo)
    
    def test_get_all_recipients(self, recipient_service, mock_recipient_repo):
        """Test getting all recipients."""
        recipients = recipient_service.get_all_recipients(12345)
        
        assert len(recipients) == 2
        assert any(r.name == "My Todoist" and r.platform_type == "todoist" for r in recipients)
        assert any(r.name == "Team Trello" and r.platform_type == "trello" for r in recipients)
        mock_recipient_repo.get_all_recipients.assert_called_once_with(12345)
    
    def test_get_enabled_recipients(self, recipient_service, mock_recipient_repo):
        """Test getting only enabled recipients."""
        recipients = recipient_service.get_enabled_recipients(12345)
        
        assert len(recipients) == 2
        assert all(r.enabled for r in recipients)
        mock_recipient_repo.get_enabled_recipients.assert_called_once_with(12345)
    
    def test_get_personal_recipients(self, recipient_service, mock_recipient_repo):
        """Test getting personal recipients."""
        recipients = recipient_service.get_personal_recipients(12345)
        
        assert len(recipients) == 1
        assert recipients[0].is_personal is True
        assert recipients[0].name == "My Todoist"
        mock_recipient_repo.get_personal_recipients.assert_called_once_with(12345)
    
    def test_get_shared_recipients(self, recipient_service, mock_recipient_repo):
        """Test getting shared recipients."""
        recipients = recipient_service.get_shared_recipients(12345)
        
        assert len(recipients) == 1
        assert recipients[0].is_personal is False
        assert recipients[0].name == "Team Trello"
        mock_recipient_repo.get_shared_recipients.assert_called_once_with(12345)
    
    def test_get_default_recipients(self, recipient_service, mock_recipient_repo):
        """Test getting default recipients."""
        defaults = recipient_service.get_default_recipients(12345)
        
        assert len(defaults) == 1
        assert defaults[0].is_default is True
        assert defaults[0].name == "My Todoist"
        mock_recipient_repo.get_default_recipients.assert_called_once_with(12345)
    
    def test_get_default_recipients_fallback(self, recipient_service, mock_recipient_repo):
        """Test default recipients fallback when no defaults set."""
        # Mock no default recipients
        mock_recipient_repo.get_default_recipients.return_value = []
        
        defaults = recipient_service.get_default_recipients(12345)
        
        # Should fallback to enabled personal recipients
        assert len(defaults) == 1
        assert defaults[0].is_personal is True
    
    def test_get_recipient_by_id(self, recipient_service, mock_recipient_repo):
        """Test getting specific recipient by ID."""
        recipient = recipient_service.get_recipient_by_id(12345, 1)
        
        assert recipient is not None
        assert recipient.id == 1
        assert recipient.name == "My Todoist"
        mock_recipient_repo.get_recipient_by_id.assert_called_once_with(12345, 1)
    
    def test_add_personal_recipient(self, recipient_service, mock_recipient_repo):
        """Test adding new personal recipient."""
        recipient_id = recipient_service.add_personal_recipient(
            user_id=12345,
            name="New Todoist",
            platform_type="todoist",
            credentials="new_token",
            is_default=False
        )
        
        assert recipient_id == 3
        mock_recipient_repo.add_recipient.assert_called_once()
    
    def test_add_shared_recipient(self, recipient_service, mock_recipient_repo):
        """Test adding new shared recipient."""
        recipient_id = recipient_service.add_shared_recipient(
            user_id=12345,
            name="Friend's Trello",
            platform_type="trello",
            credentials="shared_token",
            shared_by="friend@example.com"
        )
        
        assert recipient_id == 3
        mock_recipient_repo.add_recipient.assert_called_once()
    
    def test_remove_recipient(self, recipient_service, mock_recipient_repo):
        """Test removing recipient."""
        result = recipient_service.remove_recipient(12345, 1)
        
        assert result is True
        mock_recipient_repo.remove_recipient.assert_called_once_with(12345, 1)
    
    def test_toggle_recipient_enabled(self, recipient_service, mock_recipient_repo):
        """Test toggling recipient enabled status."""
        result = recipient_service.toggle_recipient_enabled(12345, 1)
        
        assert result is True
        mock_recipient_repo.toggle_recipient_enabled.assert_called_once_with(12345, 1)