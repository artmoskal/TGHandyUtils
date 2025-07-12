"""Tests for parameter object refactoring - Test-First Development."""

import pytest
from datetime import datetime
from unittest.mock import Mock

# These imports will fail until we create the parameter objects
from models.parameter_objects import TaskCreationRequest, TaskFeedbackData


class TestTaskCreationRequest:
    """Test the TaskCreationRequest parameter object."""
    
    def test_task_creation_request_basic(self):
        """Test basic TaskCreationRequest creation."""
        # This will fail until we create TaskCreationRequest
        request = TaskCreationRequest(
            user_id=12345,
            title="Test Task",
            description="Test Description"
        )
        
        assert request.user_id == 12345
        assert request.title == "Test Task"
        assert request.description == "Test Description"
        assert request.due_time is None  # Optional field
        assert request.specific_recipients is None  # Optional field
        assert request.screenshot_data is None  # Optional field
        assert request.chat_id == 0  # Default value
        assert request.message_id == 0  # Default value
    
    def test_task_creation_request_full(self):
        """Test TaskCreationRequest with all fields."""
        screenshot_data = {"file_id": "abc123", "analyzed_text": "Important task"}
        
        request = TaskCreationRequest(
            user_id=12345,
            title="Test Task",
            description="Test Description",
            due_time="2024-01-01T10:00:00Z",
            specific_recipients=[1, 2, 3],
            screenshot_data=screenshot_data,
            chat_id=67890,
            message_id=11111
        )
        
        assert request.user_id == 12345
        assert request.title == "Test Task"
        assert request.description == "Test Description"
        assert request.due_time == "2024-01-01T10:00:00Z"
        assert request.specific_recipients == [1, 2, 3]
        assert request.screenshot_data == screenshot_data
        assert request.chat_id == 67890
        assert request.message_id == 11111
    
    def test_task_creation_request_validation(self):
        """Test TaskCreationRequest validation."""
        # Missing required fields should raise ValueError
        with pytest.raises(ValueError, match="user_id is required"):
            TaskCreationRequest(
                user_id=None,
                title="Test Task",
                description="Test Description"
            )
        
        with pytest.raises(ValueError, match="title is required"):
            TaskCreationRequest(
                user_id=12345,
                title="",
                description="Test Description"
            )
    
    def test_task_creation_request_factory_method(self):
        """Test factory method for creating from dict."""
        data = {
            "user_id": 12345,
            "title": "Test Task",
            "description": "Test Description",
            "due_time": "2024-01-01T10:00:00Z",
            "specific_recipients": [1, 2, 3]
        }
        
        request = TaskCreationRequest.from_dict(data)
        
        assert request.user_id == 12345
        assert request.title == "Test Task"
        assert request.description == "Test Description"
        assert request.due_time == "2024-01-01T10:00:00Z"
        assert request.specific_recipients == [1, 2, 3]


class TestTaskFeedbackData:
    """Test the TaskFeedbackData parameter object."""
    
    def test_task_feedback_data_basic(self):
        """Test basic TaskFeedbackData creation."""
        # This will fail until we create TaskFeedbackData
        feedback_data = TaskFeedbackData(
            recipients=["Recipient 1", "Recipient 2"],
            task_urls={"Recipient 1": "url1", "Recipient 2": "url2"},
            failed_recipients=[],
            title="Test Task",
            description="Test Description",
            due_time="2024-01-01T10:00:00Z",
            user_id=12345
        )
        
        assert feedback_data.recipients == ["Recipient 1", "Recipient 2"]
        assert feedback_data.task_urls == {"Recipient 1": "url1", "Recipient 2": "url2"}
        assert feedback_data.failed_recipients == []
        assert feedback_data.title == "Test Task"
        assert feedback_data.description == "Test Description"
        assert feedback_data.due_time == "2024-01-01T10:00:00Z"
        assert feedback_data.user_id == 12345
    
    def test_task_feedback_data_with_failures(self):
        """Test TaskFeedbackData with failed recipients."""
        feedback_data = TaskFeedbackData(
            recipients=["Recipient 1", "Recipient 2"],
            task_urls={"Recipient 1": "url1"},
            failed_recipients=["Recipient 2"],
            title="Test Task",
            description="Test Description",
            due_time="2024-01-01T10:00:00Z",
            user_id=12345
        )
        
        assert len(feedback_data.failed_recipients) == 1
        assert "Recipient 2" in feedback_data.failed_recipients
        assert "Recipient 2" not in feedback_data.task_urls
    
    def test_task_feedback_data_success_count(self):
        """Test TaskFeedbackData success count calculation."""
        feedback_data = TaskFeedbackData(
            recipients=["R1", "R2", "R3"],
            task_urls={"R1": "url1", "R2": "url2"},
            failed_recipients=["R3"],
            title="Test Task",
            description="Test Description",
            due_time="2024-01-01T10:00:00Z",
            user_id=12345
        )
        
        # Should have a property to calculate successful recipients
        assert feedback_data.successful_count == 2
        assert feedback_data.failed_count == 1
        assert feedback_data.total_count == 3


class TestServiceIntegration:
    """Test that services can use the new parameter objects."""
    
    def test_recipient_task_service_uses_parameter_objects(self):
        """Test RecipientTaskService can accept parameter objects."""
        from services.recipient_task_service import RecipientTaskService
        
        # Mock dependencies
        mock_task_repo = Mock()
        mock_task_repo.create.return_value = 123  # Mock task ID
        
        mock_recipient_service = Mock()
        mock_recipient_service.is_recipient_ui_enabled.return_value = False
        mock_recipient_service.get_default_recipients.return_value = []  # No default recipients
        mock_recipient_service.get_enabled_recipients.return_value = []  # No recipients at all
        
        service = RecipientTaskService(
            task_repo=mock_task_repo,
            recipient_service=mock_recipient_service
        )
        
        # Create request object
        request = TaskCreationRequest(
            user_id=12345,
            title="Test Task",
            description="Test Description"
        )
        
        # The service should accept the parameter object
        result = service.create_task_for_recipients(request)
        
        # Should fail with no recipients configured
        assert hasattr(result, 'success')
        assert hasattr(result, 'message')
        assert not result.success
        assert "No recipients configured" in result.message
    
    def test_backwards_compatibility(self):
        """Test that old method signatures still work (if maintaining compatibility)."""
        from services.recipient_task_service import RecipientTaskService
        
        # Mock dependencies
        mock_task_repo = Mock()
        mock_recipient_service = Mock()
        
        service = RecipientTaskService(
            task_repo=mock_task_repo,
            recipient_service=mock_recipient_service
        )
        
        # Should still accept individual parameters for backwards compatibility
        # or have a clear migration path
        try:
            result = service.create_task_for_recipients(
                user_id=12345,
                title="Test Task",
                description="Test Description"
            )
            # If maintaining compatibility, this should work
            assert hasattr(result, 'success')
        except TypeError:
            # If not maintaining compatibility, should have clear error
            pass


class TestParameterObjectValidation:
    """Test parameter object validation and error handling."""
    
    def test_task_creation_request_type_validation(self):
        """Test type validation for TaskCreationRequest."""
        # Should validate types
        with pytest.raises(TypeError, match="user_id must be an integer"):
            TaskCreationRequest(
                user_id="not-an-int",
                title="Test Task",
                description="Test Description"
            )
    
    def test_task_creation_request_immutability(self):
        """Test that parameter objects are immutable."""
        request = TaskCreationRequest(
            user_id=12345,
            title="Test Task",
            description="Test Description"
        )
        
        # Should not be able to modify after creation
        with pytest.raises(AttributeError):
            request.title = "Modified Title"
    
    def test_task_creation_request_equality(self):
        """Test parameter object equality."""
        request1 = TaskCreationRequest(
            user_id=12345,
            title="Test Task",
            description="Test Description"
        )
        
        request2 = TaskCreationRequest(
            user_id=12345,
            title="Test Task",
            description="Test Description"
        )
        
        assert request1 == request2
        
        request3 = TaskCreationRequest(
            user_id=12345,
            title="Different Task",
            description="Test Description"
        )
        
        assert request1 != request3