"""Real integration test for screenshot attachment functionality using Factory Boy for realistic data.

This test uses realistic Factory Boy data to catch attachment bugs and ensure
screenshot functionality works with proper test data instead of hardcoded values."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from services.recipient_task_service import RecipientTaskService
from models.unified_recipient import UnifiedRecipient

# Import Factory Boy factories
from tests.factories import (
    TodoistRecipientFactory,
    ScreenshotTaskFactory,
    TelegramUserFactory,
    TelegramMessageFactory
)


class TestScreenshotAttachmentReal:
    """Integration test using real service implementation to catch attachment bugs."""
    
    @pytest.fixture
    def mock_recipient_service(self):
        """Mock recipient service with realistic Factory Boy recipient."""
        service = Mock()
        
        # Create realistic recipient using factory
        test_recipient_factory = TodoistRecipientFactory(
            id=1,
            user_id=12345,
            name="Personal Todoist - Screenshots",
            platform_type="todoist",
            credentials="a" * 40,  # Realistic token length
            platform_config={'project_id': '2147483647'},
            enabled=True,
            is_personal=True
        )
        
        # Convert to UnifiedRecipient model
        test_recipient = UnifiedRecipient(
            id=test_recipient_factory.id,
            name=test_recipient_factory.name,
            platform_type=test_recipient_factory.platform_type,
            credentials=test_recipient_factory.credentials,
            platform_config=test_recipient_factory.platform_config,
            enabled=test_recipient_factory.enabled,
            user_id=test_recipient_factory.user_id,
            is_personal=test_recipient_factory.is_personal
        )
        
        service.get_default_recipients.return_value = [test_recipient]
        service.get_enabled_recipients.return_value = [test_recipient]
        return service
    
    @pytest.fixture
    def mock_task_repo(self):
        """Mock task repository."""
        repo = Mock()
        repo.create.return_value = 999  # Mock task DB ID
        return repo
    
    @pytest.fixture
    def real_task_service(self, mock_recipient_service, mock_task_repo):
        """Create real task service with mocked dependencies."""
        return RecipientTaskService(
            task_repo=mock_task_repo,
            recipient_service=mock_recipient_service
        )
    
    @pytest.fixture
    def sample_screenshot_data(self):
        """Sample screenshot data using Factory Boy for realistic test data."""
        # Create realistic screenshot task for reference
        screenshot_task = ScreenshotTaskFactory(
            title="UI Bug Report - Screenshot Analysis",
            description="Screenshot showing layout issue in mobile view"
        )
        
        return {
            'image_data': b'realistic_image_bytes_from_screenshot_capture_' + screenshot_task.title.encode()[:20],
            'file_name': f'screenshot_{screenshot_task.id}.jpg',
            'extracted_text': f'Bug found: {screenshot_task.description}',
            'summary': 'Code screenshot with TODO comment'
        }
    
    @patch('services.recipient_task_service.TaskPlatformFactory.get_platform')
    def test_screenshot_attachment_is_called(self, mock_platform_factory, real_task_service, sample_screenshot_data):
        """Test that attach_screenshot is actually called with real service implementation."""
        # Setup mock platform
        mock_platform = Mock()
        mock_platform.create_task.return_value = "task_123"
        mock_platform.attach_screenshot.return_value = True
        mock_platform_factory.return_value = mock_platform
        
        # Call real service method
        success, feedback, actions = real_task_service.create_task_for_recipients(
            user_id=123,
            title="Test Task",
            description="Test description",
            due_time="2025-12-31T09:00:00Z",
            specific_recipients=None,
            screenshot_data=sample_screenshot_data
        )
        
        # Verify task creation succeeded
        assert success is True
        
        # CRITICAL: Verify attach_screenshot was actually called
        mock_platform.attach_screenshot.assert_called_once_with(
            "task_123",
            b'fake_image_bytes_123',
            'test_screenshot.jpg'
        )
        
        # Verify task creation was also called
        mock_platform.create_task.assert_called_once()
    
    @patch('services.recipient_task_service.TaskPlatformFactory.get_platform')
    def test_screenshot_attachment_failure_handling(self, mock_platform_factory, real_task_service, sample_screenshot_data):
        """Test handling when screenshot attachment fails."""
        # Setup mock platform where attachment fails
        mock_platform = Mock()
        mock_platform.create_task.return_value = "task_456"
        mock_platform.attach_screenshot.return_value = False  # Attachment fails
        mock_platform_factory.return_value = mock_platform
        
        # Call real service method
        success, feedback, actions = real_task_service.create_task_for_recipients(
            user_id=123,
            title="Test Task",
            description="Test description", 
            due_time="2025-12-31T09:00:00Z",
            specific_recipients=None,
            screenshot_data=sample_screenshot_data
        )
        
        # Task creation should still succeed even if attachment fails
        assert success is True
        
        # Verify attach_screenshot was attempted
        mock_platform.attach_screenshot.assert_called_once_with(
            "task_456",
            b'fake_image_bytes_123',
            'test_screenshot.jpg'
        )
    
    @patch('services.recipient_task_service.TaskPlatformFactory.get_platform')
    def test_no_screenshot_attachment_when_no_data(self, mock_platform_factory, real_task_service):
        """Test that attach_screenshot is NOT called when no screenshot data provided."""
        # Setup mock platform
        mock_platform = Mock()
        mock_platform.create_task.return_value = "task_789"
        mock_platform_factory.return_value = mock_platform
        
        # Call real service method WITHOUT screenshot data
        success, feedback, actions = real_task_service.create_task_for_recipients(
            user_id=123,
            title="Text Only Task",
            description="No screenshot here",
            due_time="2025-12-31T09:00:00Z",
            specific_recipients=None,
            screenshot_data=None  # No screenshot
        )
        
        # Task creation should succeed
        assert success is True
        
        # CRITICAL: Verify attach_screenshot was NOT called
        mock_platform.attach_screenshot.assert_not_called()
        
        # But task creation should still be called
        mock_platform.create_task.assert_called_once()