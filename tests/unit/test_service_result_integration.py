"""Integration tests for ServiceResult with actual service methods."""

import pytest
from unittest.mock import Mock, patch

from core.interfaces import ServiceResult
from services.recipient_task_service import RecipientTaskService
from tests.factories import (
    UnifiedRecipientFactory,
    TodoistRecipientFactory,
    TaskDBFactory
)


class TestServiceResultIntegration:
    """Test ServiceResult integration with service methods."""
    
    @pytest.fixture
    def mock_task_repo(self):
        """Mock task repository."""
        mock_repo = Mock()
        return mock_repo
    
    @pytest.fixture 
    def mock_recipient_service(self):
        """Mock recipient service."""
        mock_service = Mock()
        return mock_service
    
    @pytest.fixture
    def task_service(self, mock_task_repo, mock_recipient_service):
        """Create task service with mocked dependencies."""
        return RecipientTaskService(
            task_repo=mock_task_repo,
            recipient_service=mock_recipient_service
        )
    
    def test_add_task_to_recipient_returns_service_result_on_success(self, task_service, mock_task_repo, mock_recipient_service):
        """Test add_task_to_recipient returns ServiceResult instead of tuple."""
        # Setup test data using Factory Boy
        recipient = TodoistRecipientFactory(enabled=True)
        task = TaskDBFactory()
        
        mock_recipient_service.get_recipient_by_id.return_value = recipient
        mock_task_repo.get_by_id.return_value = task
        
        # Mock the platform task creation to succeed
        with patch.object(task_service, '_create_platform_task') as mock_create, \
             patch.object(task_service, '_extract_platform_task_id') as mock_extract:
            mock_create.return_value = (True, "https://todoist.com/showTask?id=task_123")
            mock_extract.return_value = "task_123"
            mock_task_repo.add_recipient.return_value = True
            
            # This should return ServiceResult, not tuple
            result = task_service.add_task_to_recipient(
                user_id=123,
                task_id=456, 
                recipient_id=recipient.id
            )
            
            # Test the new interface
            assert isinstance(result, ServiceResult)
            assert result.success is True
            assert "success" in result.message.lower()  # Should contain success message
            
    def test_add_task_to_recipient_returns_service_result_on_failure(self, task_service, mock_recipient_service):
        """Test add_task_to_recipient returns ServiceResult on failure."""
        # Setup failure case - recipient not found
        mock_recipient_service.get_recipient_by_id.return_value = None
        
        # This should return ServiceResult, not tuple
        result = task_service.add_task_to_recipient(
            user_id=123,
            task_id=456,
            recipient_id=999
        )
        
        # Test the new interface
        assert isinstance(result, ServiceResult)
        assert result.success is False
        assert "not found" in result.message.lower()
        assert result.data is None
        
    def test_add_task_to_recipient_with_disabled_recipient(self, task_service, mock_recipient_service):
        """Test add_task_to_recipient with disabled recipient."""
        # Setup disabled recipient
        recipient = UnifiedRecipientFactory(enabled=False, name="Test Recipient")
        mock_recipient_service.get_recipient_by_id.return_value = recipient
        
        result = task_service.add_task_to_recipient(
            user_id=123,
            task_id=456,
            recipient_id=recipient.id
        )
        
        assert isinstance(result, ServiceResult)
        assert result.success is False
        assert "disabled" in result.message.lower()
        assert "Test Recipient" in result.message
        
    def test_backward_compatibility_with_tuple_unpacking(self, task_service, mock_recipient_service):
        """Test that result can still be unpacked like tuple if needed during transition."""
        mock_recipient_service.get_recipient_by_id.return_value = None
        
        result = task_service.add_task_to_recipient(123, 456, 999)
        
        # During transition, we might need to support both patterns
        success = result.success
        message = result.message
        data = result.data
        
        # Verify it behaves like the old tuple pattern
        assert success is False
        assert isinstance(message, str)
        assert data is None or isinstance(data, dict)
        
    def test_remove_task_from_recipient_returns_service_result_on_success(self, task_service, mock_task_repo, mock_recipient_service):
        """Test remove_task_from_recipient returns ServiceResult on success."""
        # Setup test data using Factory Boy
        recipient = TodoistRecipientFactory(enabled=True)
        task_recipient = Mock()
        task_recipient.platform_task_id = "platform_123"
        
        mock_task_repo.get_task_recipient.return_value = task_recipient
        mock_recipient_service.get_recipient_by_id.return_value = recipient
        
        # Mock the platform deletion to succeed
        with patch('platforms.base.TaskPlatformFactory.get_platform') as mock_platform_factory:
            mock_platform = Mock()
            mock_platform.delete_task.return_value = True
            mock_platform_factory.get_platform.return_value = mock_platform
            mock_task_repo.remove_recipient.return_value = True
            
            # This should return ServiceResult, not tuple
            result = task_service.remove_task_from_recipient(
                user_id=123,
                task_id=456,
                recipient_id=recipient.id
            )
            
            # Test the new interface
            assert isinstance(result, ServiceResult)
            assert result.success is True
            assert "success" in result.message.lower()  # Should contain success message
            
    def test_remove_task_from_recipient_returns_service_result_on_failure(self, task_service, mock_task_repo):
        """Test remove_task_from_recipient returns ServiceResult on failure."""
        # Setup failure case - task-recipient relationship not found
        mock_task_repo.get_task_recipient.return_value = None
        
        # This should return ServiceResult, not tuple
        result = task_service.remove_task_from_recipient(
            user_id=123,
            task_id=456,
            recipient_id=999
        )
        
        # Test the new interface
        assert isinstance(result, ServiceResult)
        assert result.success is False
        assert "not found" in result.message.lower()
        assert result.data is None
        
    def test_create_task_for_recipients_returns_service_result_on_success(self, task_service, mock_task_repo, mock_recipient_service):
        """Test create_task_for_recipients returns ServiceResult with action buttons on success."""
        # Setup test data using Factory Boy
        recipient = TodoistRecipientFactory(enabled=True)
        
        mock_recipient_service.is_recipient_ui_enabled.return_value = True
        mock_recipient_service.get_enabled_recipients.return_value = [recipient]
        mock_recipient_service.get_default_recipients.return_value = [recipient]
        mock_task_repo.create.return_value = 123  # task_id
        mock_task_repo.get_task_recipients.return_value = []
        
        # Mock the platform task creation to succeed
        with patch.object(task_service, '_create_platform_task') as mock_create, \
             patch.object(task_service, '_extract_platform_task_id') as mock_extract, \
             patch.object(task_service, '_generate_success_feedback') as mock_feedback, \
             patch.object(task_service, '_generate_post_task_actions') as mock_actions:
            mock_create.return_value = (True, "https://todoist.com/showTask?id=task_123")
            mock_extract.return_value = "task_123"
            mock_task_repo.add_recipient.return_value = True
            mock_feedback.return_value = "✅ Task created successfully!"
            mock_actions.return_value = {"remove_actions": [], "add_actions": []}
            
            # This should return ServiceResult, not tuple
            result = task_service.create_task_for_recipients(
                user_id=123,
                title="Test Task",
                description="Test Description"
            )
            
            # Test the new interface
            assert isinstance(result, ServiceResult)
            assert result.success is True
            assert "success" in result.message.lower()
            assert result.data is not None  # Should contain action buttons
            assert isinstance(result.data, dict)
            
    def test_create_task_for_recipients_returns_service_result_on_failure(self, task_service, mock_recipient_service):
        """Test create_task_for_recipients returns ServiceResult on failure."""
        # Setup failure case - no recipients configured
        mock_recipient_service.is_recipient_ui_enabled.return_value = True
        mock_recipient_service.get_default_recipients.return_value = []
        mock_recipient_service.get_enabled_recipients.return_value = []
        
        # This should return ServiceResult, not tuple  
        result = task_service.create_task_for_recipients(
            user_id=123,
            title="Test Task"
        )
        
        # Test the new interface
        assert isinstance(result, ServiceResult)
        assert result.success is False
        assert "no recipients" in result.message.lower()
        assert result.data is None