"""Tests for telegram notification preferences."""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from services.recipient_service import RecipientService
from models.recipient import UserPreferencesV2, UserPreferencesV2Create, UserPreferencesV2Update
from scheduler import _send_reminder
from models.task import TaskDB


class TestNotificationPreferences:
    """Test notification preference functionality."""
    
    @pytest.fixture
    def mock_prefs_repo(self):
        """Mock preferences repository."""
        return Mock()
    
    @pytest.fixture
    def mock_platform_repo(self):
        """Mock platform repository."""
        return Mock()
    
    @pytest.fixture
    def mock_shared_repo(self):
        """Mock shared repository."""
        return Mock()
    
    @pytest.fixture
    def recipient_service(self, mock_platform_repo, mock_shared_repo, mock_prefs_repo):
        """Create recipient service with mocked dependencies."""
        return RecipientService(mock_platform_repo, mock_shared_repo, mock_prefs_repo)
    
    @pytest.fixture
    def sample_task(self):
        """Sample task for testing."""
        return TaskDB(
            id=1,
            user_id=12345,
            chat_id=67890,
            message_id=111,
            task_title="Test Notification",
            task_description="Test notification preferences",
            due_time="2024-01-01T12:00:00Z",
            platform_task_id="task_123",
            platform_type="todoist"
        )
    
    def test_are_telegram_notifications_enabled_default_true(self, recipient_service, mock_prefs_repo):
        """Test that notifications are enabled by default when no preferences exist."""
        mock_prefs_repo.get_preferences.return_value = None
        
        result = recipient_service.are_telegram_notifications_enabled(12345)
        
        assert result is True
        mock_prefs_repo.get_preferences.assert_called_once_with(12345)
    
    def test_are_telegram_notifications_enabled_with_preferences(self, recipient_service, mock_prefs_repo):
        """Test reading notification preference from existing preferences."""
        prefs = UserPreferencesV2(
            telegram_user_id=12345,
            default_recipients=[],
            telegram_notifications=False,
            show_recipient_ui=True
        )
        mock_prefs_repo.get_preferences.return_value = prefs
        
        result = recipient_service.are_telegram_notifications_enabled(12345)
        
        assert result is False
        mock_prefs_repo.get_preferences.assert_called_once_with(12345)
    
    def test_set_telegram_notifications_new_user(self, recipient_service, mock_prefs_repo):
        """Test setting notifications for user with no existing preferences."""
        mock_prefs_repo.get_preferences.return_value = None
        mock_prefs_repo.create_preferences.return_value = True
        
        result = recipient_service.set_telegram_notifications(12345, False)
        
        assert result is True
        mock_prefs_repo.get_preferences.assert_called_once_with(12345)
        mock_prefs_repo.create_preferences.assert_called_once()
        
        # Check the created preferences
        call_args = mock_prefs_repo.create_preferences.call_args
        user_id, prefs_create = call_args[0]
        assert user_id == 12345
        assert isinstance(prefs_create, UserPreferencesV2Create)
        assert prefs_create.telegram_notifications is False
    
    def test_set_telegram_notifications_existing_user(self, recipient_service, mock_prefs_repo):
        """Test updating notifications for user with existing preferences."""
        existing_prefs = UserPreferencesV2(
            telegram_user_id=12345,
            default_recipients=["platform_1"],
            telegram_notifications=True,
            show_recipient_ui=False
        )
        mock_prefs_repo.get_preferences.return_value = existing_prefs
        mock_prefs_repo.update_preferences.return_value = True
        
        result = recipient_service.set_telegram_notifications(12345, False)
        
        assert result is True
        mock_prefs_repo.get_preferences.assert_called_once_with(12345)
        mock_prefs_repo.update_preferences.assert_called_once()
        
        # Check the update
        call_args = mock_prefs_repo.update_preferences.call_args
        user_id, prefs_update = call_args[0]
        assert user_id == 12345
        assert isinstance(prefs_update, UserPreferencesV2Update)
        assert prefs_update.telegram_notifications is False
    
    @pytest.mark.asyncio
    async def test_scheduler_default_behavior(self, sample_task):
        """Test that scheduler sends reminders by default (fallback behavior)."""
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            
            await _send_reminder(sample_task)
            
            # Should send message (default behavior when DI fails)
            mock_bot.send_message.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_scheduler_fallback_behavior(self, sample_task):
        """Test that scheduler defaults to sending when DI is not available.""" 
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            
            # Should default to sending when DI container is not wired
            await _send_reminder(sample_task)
            
            # Should send message (fallback behavior)
            mock_bot.send_message.assert_called_once()
    
    def test_toggle_notifications_enable(self, recipient_service, mock_prefs_repo):
        """Test enabling notifications when currently disabled."""
        existing_prefs = UserPreferencesV2(
            telegram_user_id=12345,
            default_recipients=["platform_1"],
            telegram_notifications=False,
            show_recipient_ui=True
        )
        mock_prefs_repo.get_preferences.return_value = existing_prefs
        mock_prefs_repo.update_preferences.return_value = True
        
        result = recipient_service.set_telegram_notifications(12345, True)
        
        assert result is True
        call_args = mock_prefs_repo.update_preferences.call_args
        prefs_update = call_args[0][1]
        assert prefs_update.telegram_notifications is True
    
    def test_toggle_notifications_disable(self, recipient_service, mock_prefs_repo):
        """Test disabling notifications when currently enabled."""
        existing_prefs = UserPreferencesV2(
            telegram_user_id=12345,
            default_recipients=["shared_2"],
            telegram_notifications=True,
            show_recipient_ui=False
        )
        mock_prefs_repo.get_preferences.return_value = existing_prefs
        mock_prefs_repo.update_preferences.return_value = True
        
        result = recipient_service.set_telegram_notifications(12345, False)
        
        assert result is True
        call_args = mock_prefs_repo.update_preferences.call_args
        prefs_update = call_args[0][1]
        assert prefs_update.telegram_notifications is False
    
    def test_notification_preferences_independent_of_ui_preferences(self, recipient_service, mock_prefs_repo):
        """Test that notification preferences don't affect UI preferences."""
        existing_prefs = UserPreferencesV2(
            telegram_user_id=12345,
            default_recipients=["platform_1", "shared_2"],
            telegram_notifications=True,
            show_recipient_ui=True
        )
        mock_prefs_repo.get_preferences.return_value = existing_prefs
        mock_prefs_repo.update_preferences.return_value = True
        
        # Change only notifications
        recipient_service.set_telegram_notifications(12345, False)
        
        # UI preference should not be affected
        call_args = mock_prefs_repo.update_preferences.call_args
        prefs_update = call_args[0][1]
        assert prefs_update.telegram_notifications is False
        assert not hasattr(prefs_update, 'show_recipient_ui') or prefs_update.show_recipient_ui is None