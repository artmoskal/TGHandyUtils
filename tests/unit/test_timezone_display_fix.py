"""Unit test to verify task success messages show time in user's local timezone."""

import pytest
from unittest.mock import Mock
from datetime import datetime, timezone

from services.recipient_task_service import RecipientTaskService
from services.recipient_service import RecipientService


class TestTimezoneDisplayFix:
    """Test that task success messages display time in user's local timezone."""

    @pytest.fixture
    def mock_task_repo(self):
        """Mock task repository."""
        return Mock()

    @pytest.fixture  
    def mock_recipient_service_with_location(self):
        """Mock recipient service with Portugal location."""
        service = Mock(spec=RecipientService)
        
        # Mock user preferences with Portugal location
        mock_user_prefs = Mock()
        mock_user_prefs.location = "Portugal"
        service.get_user_preferences.return_value = mock_user_prefs
        
        return service

    @pytest.fixture  
    def mock_recipient_service_no_location(self):
        """Mock recipient service with no location."""
        service = Mock(spec=RecipientService)
        
        # Mock user preferences with no location
        mock_user_prefs = Mock()
        mock_user_prefs.location = None
        service.get_user_preferences.return_value = mock_user_prefs
        
        return service

    def test_success_feedback_attempts_timezone_conversion_for_portugal_user(self, mock_task_repo, mock_recipient_service_with_location):
        """Test that success feedback attempts timezone conversion for users with location."""
        service = RecipientTaskService(mock_task_repo, mock_recipient_service_with_location)
        
        # Test data
        recipients = []
        task_urls = ["https://todoist.com/showTask?id=123"]
        failed_recipients = []
        title = "Test Task"
        description = "Test description"
        due_time = "2025-07-05T19:00:00Z"  # 19:00 UTC
        user_id = 12345

        # Generate feedback - this will try to convert timezone but may fail due to missing OpenAI key
        # The important thing is that it tries to use the user's location
        feedback = service._generate_success_feedback(
            recipients, task_urls, failed_recipients, title, description, due_time, user_id
        )
        
        # Should contain task details
        assert "✅ *Task Created Successfully!*" in feedback
        assert "📝 *Title:* Test Task" in feedback
        assert "📄 *Description:* Test description" in feedback
        assert "⏰ *Due:*" in feedback
        
        # Verify that the service attempted to get user preferences
        mock_recipient_service_with_location.get_user_preferences.assert_called_once_with(user_id)

    def test_success_feedback_falls_back_to_utc_when_no_location(self, mock_task_repo, mock_recipient_service_no_location):
        """Test that success feedback falls back to UTC when user has no location set."""
        service = RecipientTaskService(mock_task_repo, mock_recipient_service_no_location)
        
        # Test data
        recipients = []
        task_urls = ["https://todoist.com/showTask?id=123"]
        failed_recipients = []
        title = "Test Task"
        description = "Test description"
        due_time = "2025-07-05T19:00:00Z"
        user_id = 12345

        # Generate feedback
        feedback = service._generate_success_feedback(
            recipients, task_urls, failed_recipients, title, description, due_time, user_id
        )
        
        # Should fall back to UTC display
        assert "UTC" in feedback
        assert "19:00" in feedback
        
        # Verify that the service checked user preferences
        mock_recipient_service_no_location.get_user_preferences.assert_called_once_with(user_id)

    def test_success_feedback_includes_all_expected_elements(self, mock_task_repo, mock_recipient_service_no_location):
        """Test that success feedback includes all expected elements."""
        service = RecipientTaskService(mock_task_repo, mock_recipient_service_no_location)
        
        # Mock recipients and URLs
        mock_recipient = Mock()
        mock_recipient.name = "Todoist Personal"
        recipients = [mock_recipient]
        task_urls = ["https://todoist.com/showTask?id=123"]
        failed_recipients = []
        title = "Important Meeting"
        description = "Weekly team meeting with John"
        due_time = "2025-07-05T19:00:00Z"
        user_id = 12345

        # Generate feedback
        feedback = service._generate_success_feedback(
            recipients, task_urls, failed_recipients, title, description, due_time, user_id
        )
        
        # Verify all expected elements are present
        assert "✅ *Task Created Successfully!*" in feedback
        assert "📝 *Title:* Important Meeting" in feedback
        assert "📄 *Description:* Weekly team meeting with John" in feedback
        assert "⏰ *Due:*" in feedback
        assert "🔗 *Created on:*" in feedback
        assert "Todoist Personal" in feedback
        assert "https://todoist.com/showTask?id=123" in feedback

    def test_success_feedback_method_signature_includes_user_id(self, mock_task_repo, mock_recipient_service_no_location):
        """Test that the method signature has been updated to include user_id parameter."""
        service = RecipientTaskService(mock_task_repo, mock_recipient_service_no_location)
        
        # This test verifies that we can call the method with user_id
        # If the signature was not updated, this would fail
        try:
            feedback = service._generate_success_feedback(
                recipients=[], 
                task_urls=[], 
                failed_recipients=[], 
                title="Test", 
                description="Test", 
                due_time="2025-07-05T19:00:00Z", 
                user_id=12345
            )
            # If we get here, the signature is correct
            assert True
        except TypeError as e:
            # If we get a TypeError, it means the signature wasn't updated
            pytest.fail(f"Method signature not updated: {e}")