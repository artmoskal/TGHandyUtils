"""Unit tests for RecipientTaskService - the actual service being used."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime
from typing import Tuple, Optional, Dict

from services.recipient_task_service import RecipientTaskService
from models.unified_recipient import UnifiedRecipient
from models.task import TaskDB


class TestRecipientTaskService:
    """Test cases for RecipientTaskService."""
    
    @pytest.fixture
    def mock_task_repo(self):
        """Mock task repository with get_by_id method."""
        mock = Mock()
        # Mock task for testing
        mock_task = TaskDB(
            id=117,
            user_id=12345,
            chat_id=67890,
            message_id=111,
            task_title="Original Task Title",
            task_description="Original task description with important details",
            due_time="2025-06-29T09:00:00Z",
            platform_task_id="original_123",
            platform_type="todoist"
        )
        mock.get_by_id.return_value = mock_task
        mock.create.return_value = 117
        return mock
    
    @pytest.fixture
    def mock_recipient_service(self):
        """Mock recipient service."""
        mock = Mock()
        # Mock recipients
        mock.get_recipient_by_id.return_value = UnifiedRecipient(
            id=57,
            user_id=12345,
            name="Alona",
            platform_type="trello",
            credentials="test_credentials",
            platform_config={"board_id": "board123", "list_id": "list456"},
            is_personal=False,
            is_default=False,
            enabled=True,
            shared_by="Shared Account",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        mock.get_enabled_recipients.return_value = [
            UnifiedRecipient(
                id=56,
                user_id=12345,
                name="My Todoist",
                platform_type="todoist",
                credentials="token123",
                platform_config=None,
                is_personal=True,
                is_default=True,
                enabled=True,
                shared_by=None,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        ]
        return mock
    
    @pytest.fixture
    def task_service(self, mock_task_repo, mock_recipient_service):
        """Create task service with mocked dependencies."""
        return RecipientTaskService(
            task_repo=mock_task_repo,
            recipient_service=mock_recipient_service
        )
    
    def test_add_task_to_recipient_success(self, task_service, mock_task_repo, mock_recipient_service):
        """Test successfully adding an existing task to a new recipient."""
        # Mock platform creation
        with patch.object(task_service, '_create_platform_task') as mock_create:
            mock_create.return_value = (True, "https://trello.com/c/newcard123")
            
            success, message = task_service.add_task_to_recipient(
                user_id=12345,
                task_id=117,
                recipient_id=57
            )
            
            # Verify success
            assert success is True
            assert "✅ Added to Alona" in message
            assert "https://trello.com/c/newcard123" in message
            
            # Verify task was fetched from database
            mock_task_repo.get_by_id.assert_called_once_with(117)
            
            # Verify platform task was created with original content
            mock_create.assert_called_once()
            call_args = mock_create.call_args[0]
            assert call_args[1] == "Original Task Title"  # Original title, not "Task 117"
            assert call_args[2] == "Original task description with important details"
            assert call_args[3] == "2025-06-29T09:00:00Z"  # Original due time
    
    def test_add_task_to_recipient_task_not_found(self, task_service, mock_task_repo):
        """Test error when task doesn't exist in database."""
        mock_task_repo.get_by_id.return_value = None
        
        success, message = task_service.add_task_to_recipient(
            user_id=12345,
            task_id=999,
            recipient_id=57
        )
        
        assert success is False
        assert "❌ Task 999 not found" in message
    
    def test_add_task_to_recipient_recipient_not_found(self, task_service, mock_recipient_service):
        """Test error when recipient doesn't exist."""
        mock_recipient_service.get_recipient_by_id.return_value = None
        
        success, message = task_service.add_task_to_recipient(
            user_id=12345,
            task_id=117,
            recipient_id=999
        )
        
        assert success is False
        assert "❌ Recipient 999 not found" in message
    
    def test_add_task_to_recipient_disabled_recipient(self, task_service, mock_recipient_service):
        """Test error when trying to add to disabled recipient."""
        recipient = mock_recipient_service.get_recipient_by_id.return_value
        recipient.enabled = False
        
        success, message = task_service.add_task_to_recipient(
            user_id=12345,
            task_id=117,
            recipient_id=57
        )
        
        assert success is False
        assert "❌ Alona is disabled" in message
    
    def test_create_platform_task_with_trello_config(self, task_service):
        """Test platform task creation includes Trello board/list config."""
        recipient = UnifiedRecipient(
            id=57,
            user_id=12345,
            name="Alona",
            platform_type="trello",
            credentials="key:token",
            platform_config={"board_id": "board123", "list_id": "list456"},
            is_personal=False,
            is_default=False,
            enabled=True,
            shared_by="Shared",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        with patch('platforms.base.TaskPlatformFactory.get_platform') as mock_factory:
            mock_platform = Mock()
            mock_platform.create_task.return_value = "card123"
            mock_factory.return_value = mock_platform
            
            success, url = task_service._create_platform_task(
                recipient=recipient,
                title="Test Task",
                description="Test Description",
                due_time="2025-06-29T09:00:00Z",
                screenshot_data=None
            )
            
            # Verify platform was called with correct data
            mock_platform.create_task.assert_called_once()
            call_data = mock_platform.create_task.call_args[0][0]
            
            # Check that Trello config was included
            assert call_data['board_id'] == "board123"
            assert call_data['list_id'] == "list456"
    
    def test_generate_post_task_actions(self, task_service, mock_recipient_service):
        """Test generation of post-task action buttons."""
        # Setup: 2 recipients total, 1 was used
        all_recipients = [
            UnifiedRecipient(
                id=56,
                user_id=12345,
                name="My Todoist",
                platform_type="todoist",
                credentials="token",
                platform_config=None,
                is_personal=True,
                is_default=True,
                enabled=True,
                shared_by=None,
                created_at=datetime.now(),
                updated_at=datetime.now()
            ),
            UnifiedRecipient(
                id=57,
                user_id=12345,
                name="Alona",
                platform_type="trello",
                credentials="key:token",
                platform_config={"board_id": "b1", "list_id": "l1"},
                is_personal=False,
                is_default=False,
                enabled=True,
                shared_by="Shared",
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        ]
        
        used_recipients = [all_recipients[0]]  # Only Todoist was used
        mock_recipient_service.get_enabled_recipients.return_value = all_recipients
        
        actions = task_service._generate_post_task_actions(
            user_id=12345,
            used_recipients=used_recipients,
            task_id=117
        )
        
        # Should have remove action for used recipient
        assert len(actions['remove_actions']) == 1
        assert actions['remove_actions'][0]['text'] == "❌ Remove from My Todoist"
        assert actions['remove_actions'][0]['callback_data'] == "remove_task_from_56_117"
        
        # Should have add action for unused recipient
        assert len(actions['add_actions']) == 1
        assert actions['add_actions'][0]['text'] == "➕ Add to Alona"
        assert actions['add_actions'][0]['callback_data'] == "add_task_to_57_117"