"""Test for tuple return fixes in recipient_task_service."""

import pytest
from unittest.mock import Mock, patch
from core.interfaces import ServiceResult
from services.recipient_task_service import RecipientTaskService
from models.unified_recipient import UnifiedRecipient
from models.parameter_objects import PlatformTaskData as PlatformTaskParams
from helpers.error_helpers import PlatformError


pytestmark = pytest.mark.unit
class TestTupleReturnFix:
    """Test that all methods return ServiceResult instead of tuples."""
    
    def test_create_platform_task_returns_service_result(self):
        """Test _create_platform_task returns ServiceResult instead of tuple."""
        # Arrange
        service = RecipientTaskService(Mock(), Mock())
        recipient = UnifiedRecipient(
            id=1,
            user_id=123,
            name="Test Todoist",
            platform_type="todoist",
            enabled=True,
            credentials={"api_token": "test_token"},
            platform_config={}
        )
        task_data = PlatformTaskParams(
            title="Test Task",
            description="Test Description",
            due_time="2024-01-01T09:00:00Z"
        )
        
        # Mock platform
        mock_platform = Mock()
        mock_platform.create_task.return_value = "task123"
        
        with patch('services.recipient_task_service.TaskPlatformFactory.get_platform', return_value=mock_platform):
            # Act
            result = service._create_platform_task(recipient, task_data)
            
            # Assert
            assert isinstance(result, ServiceResult)
            assert result.success is True
            assert result.message == "Task created successfully on Test Todoist"
            assert result.data == "https://todoist.com/showTask?id=task123"
    
    def test_create_platform_task_failure_returns_service_result(self):
        """Test _create_platform_task returns ServiceResult on failure."""
        # Arrange
        service = RecipientTaskService(Mock(), Mock())
        recipient = UnifiedRecipient(
            id=1,
            user_id=123,
            name="Test Todoist",
            platform_type="todoist",
            enabled=True,
            credentials={"api_token": "test_token"},
            platform_config={}
        )
        task_data = PlatformTaskParams(
            title="Test Task",
            description="Test Description",
            due_time="2024-01-01T09:00:00Z"
        )
        
        # Mock platform initialization failure
        with patch('services.recipient_task_service.TaskPlatformFactory.get_platform', return_value=None):
            # Act
            result = service._create_platform_task(recipient, task_data)
            
            # Assert
            assert isinstance(result, ServiceResult)
            assert result.success is False
            assert "Failed to initialize" in result.message
            assert result.data is None