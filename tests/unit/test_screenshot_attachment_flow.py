"""Tests for screenshot attachment flow."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from services.recipient_task_service import RecipientTaskService
from services.temporary_file_cache import TemporaryFileCache
from models.unified_recipient import UnifiedRecipient
from models.task import TaskDB


class TestScreenshotAttachmentFlow:
    """Test screenshot attachment in recipient task service."""
    
    def setup_method(self):
        """Set up test mocks."""
        self.mock_task_repo = Mock()
        self.mock_recipient_service = Mock()
        self.service = RecipientTaskService(self.mock_task_repo, self.mock_recipient_service)
        
        # Create test recipients
        self.todoist_recipient = UnifiedRecipient(
            id=1,
            user_id=123,
            name="My Todoist",
            platform_type="todoist",
            credentials="test_token",
            enabled=True,
            is_personal=True,
            is_default=True
        )
        
        self.trello_recipient = UnifiedRecipient(
            id=2,
            user_id=123,
            name="My Trello",
            platform_type="trello", 
            credentials="test_key:test_token",
            platform_config={"board_id": "board123", "list_id": "list123"},
            enabled=True,
            is_personal=True,
            is_default=False
        )
    
    @patch('services.recipient_task_service.TaskPlatformFactory')
    @patch('services.temporary_file_cache.get_screenshot_cache')
    def test_screenshot_attachment_with_image_data(self, mock_get_cache, mock_factory):
        """Test screenshot attachment when image_data is available."""
        # Setup mocks
        mock_platform = Mock()
        mock_platform.create_task.return_value = "task123"
        mock_platform.attach_screenshot.return_value = True
        mock_factory.get_platform.return_value = mock_platform
        
        screenshot_data = {
            'image_data': b'fake_image_bytes',
            'file_name': 'test.jpg',
            'file_id': 'file123'
        }
        
        # Test
        success, url = self.service._create_platform_task(
            self.trello_recipient, 
            "Test Task", 
            "Description", 
            "2025-07-04T10:00:00Z",
            screenshot_data
        )
        
        # Verify
        assert success is True
        assert url == "https://trello.com/c/task123"
        mock_platform.create_task.assert_called_once()
        mock_platform.attach_screenshot.assert_called_once_with("task123", b'fake_image_bytes', 'test.jpg')
    
    @patch('services.recipient_task_service.TaskPlatformFactory')
    @patch('services.temporary_file_cache.get_screenshot_cache')
    def test_screenshot_attachment_from_cache(self, mock_get_cache, mock_factory):
        """Test screenshot attachment retrieved from cache."""
        # Setup mocks
        mock_platform = Mock()
        mock_platform.create_task.return_value = "task123"
        mock_platform.attach_screenshot.return_value = True
        mock_factory.get_platform.return_value = mock_platform
        
        mock_cache = Mock()
        mock_cache.get_screenshot.return_value = {
            'image_data': b'cached_image_bytes',
            'file_name': 'cached.jpg',
            'file_id': 'file123'
        }
        mock_get_cache.return_value = mock_cache
        
        screenshot_data = {
            'file_id': 'file123',
            'file_name': 'test.jpg'
            # No image_data - should retrieve from cache
        }
        
        # Test
        success, url = self.service._create_platform_task(
            self.trello_recipient,
            "Test Task",
            "Description", 
            "2025-07-04T10:00:00Z",
            screenshot_data
        )
        
        # Verify
        assert success is True
        assert url == "https://trello.com/c/task123"
        mock_cache.get_screenshot.assert_called_once_with('file123')
        mock_platform.attach_screenshot.assert_called_once_with("task123", b'cached_image_bytes', 'cached.jpg')
    
    @patch('services.recipient_task_service.TaskPlatformFactory')
    @patch('services.temporary_file_cache.get_screenshot_cache')
    def test_screenshot_attachment_cache_miss(self, mock_get_cache, mock_factory):
        """Test when screenshot not found in cache."""
        # Setup mocks
        mock_platform = Mock()
        mock_platform.create_task.return_value = "task123"
        mock_factory.get_platform.return_value = mock_platform
        
        mock_cache = Mock()
        mock_cache.get_screenshot.return_value = None  # Cache miss
        mock_get_cache.return_value = mock_cache
        
        screenshot_data = {
            'file_id': 'file123',
            'file_name': 'test.jpg'
            # No image_data and cache miss
        }
        
        # Test
        success, url = self.service._create_platform_task(
            self.trello_recipient,
            "Test Task",
            "Description",
            "2025-07-04T10:00:00Z", 
            screenshot_data
        )
        
        # Verify
        assert success is True  # Task creation should still succeed
        assert url == "https://trello.com/c/task123"
        mock_cache.get_screenshot.assert_called_once_with('file123')
        mock_platform.attach_screenshot.assert_not_called()  # No screenshot attachment
    
    def test_add_task_to_recipient_with_screenshot(self):
        """Test adding existing task with screenshot to recipient."""
        # Setup task with screenshot
        task = TaskDB(
            id=1,
            user_id=123,
            chat_id=456,
            message_id=789,
            task_title="Test Task",
            task_description="Description",
            due_time="2025-07-04T10:00:00Z",
            platform_task_id="original_task",
            platform_type="todoist",
            screenshot_file_id="file123",
            screenshot_filename="test.jpg"
        )
        
        self.mock_task_repo.get_by_id.return_value = task
        self.mock_recipient_service.get_recipient_by_id.return_value = self.trello_recipient
        
        with patch.object(self.service, '_create_platform_task') as mock_create:
            mock_create.return_value = (True, "https://trello.com/c/new_task")
            
            success, message = self.service.add_task_to_recipient(123, 1, 2)
            
            # Verify screenshot data is passed
            call_args = mock_create.call_args
            screenshot_data = call_args[0][4]  # 5th argument
            
            assert screenshot_data is not None
            assert screenshot_data['file_id'] == 'file123'
            assert screenshot_data['file_name'] == 'test.jpg'
            assert success is True
    
    def test_full_integration_flow(self):
        """Test full flow: create task -> add to additional recipient."""
        # Mock recipients
        self.mock_recipient_service.get_default_recipients.return_value = [self.todoist_recipient]
        self.mock_recipient_service.get_enabled_recipients.return_value = [self.todoist_recipient, self.trello_recipient]
        self.mock_recipient_service.get_recipient_by_id.return_value = self.trello_recipient
        self.mock_task_repo.create.return_value = 1
        
        # Mock task retrieval for second step
        task = TaskDB(
            id=1, user_id=123, chat_id=456, message_id=789,
            task_title="Test Task", task_description="Description",
            due_time="2025-07-04T10:00:00Z", platform_task_id="todoist_task",
            platform_type="todoist", screenshot_file_id="file123",
            screenshot_filename="test.jpg"
        )
        self.mock_task_repo.get_by_id.return_value = task
        
        with patch.object(self.service, '_create_platform_task') as mock_create:
            mock_create.return_value = (True, "https://platform.com/task")
            
            # Step 1: Create initial task with screenshot
            screenshot_data = {
                'image_data': b'image_bytes',
                'file_name': 'test.jpg', 
                'file_id': 'file123'
            }
            
            success1, feedback1, actions1 = self.service.create_task_for_recipients(
                user_id=123,
                title="Test Task",
                description="Description",
                due_time="2025-07-04T10:00:00Z",
                screenshot_data=screenshot_data
            )
            
            # Step 2: Add to additional recipient
            success2, message2 = self.service.add_task_to_recipient(123, 1, 2)
            
            # Verify both succeed
            assert success1 is True
            assert success2 is True
            
            # Verify screenshot data flows through both calls
            assert mock_create.call_count == 2
            
            # First call should have image_data
            first_call_screenshot = mock_create.call_args_list[0][0][4]
            assert first_call_screenshot['image_data'] == b'image_bytes'
            
            # Second call should have file_id (for cache lookup)
            second_call_screenshot = mock_create.call_args_list[1][0][4]
            assert second_call_screenshot['file_id'] == 'file123'