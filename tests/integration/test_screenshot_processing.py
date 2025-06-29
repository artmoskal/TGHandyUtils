"""Simplified screenshot processing tests with minimal mocking."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from aiogram.types import Message, PhotoSize, User, Chat

from handlers import process_thread_with_photos
from models.task import TaskCreate


class TestScreenshotProcessingSimple:
    """Simplified screenshot processing tests."""
    
    @pytest.fixture
    def mock_message(self):
        """Create a mock Telegram message."""
        user = User(id=123456, is_bot=False, first_name="Test", username="testuser")
        chat = Chat(id=789, type="private")
        
        message = Mock(spec=Message)
        message.from_user = user
        message.chat = chat
        message.reply = AsyncMock()
        
        return message
    
    @pytest.fixture
    def sample_screenshot_data(self):
        """Sample screenshot analysis data."""
        return {
            "extracted_text": "TODO: Fix bug in calculation function",
            "summary": "Code screenshot showing a TODO comment about fixing a bug",
            "file_url": "https://api.telegram.org/file/bot123/screenshot.jpg"
        }
    
    @pytest.mark.asyncio
    async def test_process_thread_with_screenshot_basic(self, mock_message, sample_screenshot_data):
        """Test basic screenshot processing without complex dependencies."""
        # Thread content with screenshot
        thread_content = [
            ("User", "look at this screenshot", sample_screenshot_data)
        ]
        
        # Mock both services completely at the module level
        with patch('handlers.container') as mock_container, \
             patch('core.initialization.services.get_parsing_service') as mock_parsing_service:
            
            # Setup parsing service
            mock_parsing = Mock()
            mock_parsing.parse_content_to_task.return_value = {
                "title": "Fix calculation bug",
                "description": "Fix bug in calculation function based on screenshot TODO",
                "due_time": "2025-06-29T09:00:00Z"
            }
            mock_parsing_service.return_value = mock_parsing
            
            # Setup task service
            mock_task_svc = Mock()
            mock_task_svc.create_task_for_recipients.return_value = (
                True, 
                "✅ Task created successfully!", 
                {"add_actions": [], "remove_actions": []}
            )
            mock_container.clean_recipient_task_service.return_value = mock_task_svc
            
            # Process the thread
            await process_thread_with_photos(
                message=mock_message,
                thread_content=thread_content,
                owner_name="Test User",
                location="UTC",
                owner_id=123456
            )
            
            # Verify parsing service was called
            mock_parsing.parse_content_to_task.assert_called_once()
            call_args = mock_parsing.parse_content_to_task.call_args
            assert "User: look at this screenshot" in call_args[0][0]
            
            # Verify task service was called
            mock_task_svc.create_task_for_recipients.assert_called_once()
            task_call_args = mock_task_svc.create_task_for_recipients.call_args
            
            # Check screenshot data was passed
            assert task_call_args.kwargs['screenshot_data'] == sample_screenshot_data
            
            # Verify reply was called
            mock_message.reply.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_process_thread_parsing_failure(self, mock_message, sample_screenshot_data):
        """Test fallback when parsing fails."""
        thread_content = [
            ("User", "urgent task", sample_screenshot_data)
        ]
        
        with patch('handlers.container') as mock_container, \
             patch('core.initialization.services.get_parsing_service') as mock_parsing_service:
            
            # Setup parsing service to fail
            mock_parsing = Mock()
            mock_parsing.parse_content_to_task.return_value = None
            mock_parsing_service.return_value = mock_parsing
            
            # No need to mock datetime for this test - just check basic behavior
            
            # Setup task service
            mock_task_svc = Mock()
            mock_task_svc.create_task_for_recipients.return_value = (True, "✅ Task created!", {})
            mock_container.clean_recipient_task_service.return_value = mock_task_svc
            
            # Process the thread
            await process_thread_with_photos(
                message=mock_message,
                thread_content=thread_content,
                owner_name="Test User",
                location="UTC",
                owner_id=123456
            )
            
            # Verify fallback task was created
            mock_task_svc.create_task_for_recipients.assert_called_once()
            task_call_args = mock_task_svc.create_task_for_recipients.call_args
            
            # Check that task service was called (fallback behavior working)
            assert task_call_args.kwargs['title'] == "User: urgent task"
            assert task_call_args.kwargs['description'] == "User: urgent task"
            # Due time should be set to a reasonable fallback - check it's a valid ISO string
            assert "T" in task_call_args.kwargs['due_time']  # Basic ISO format check
    
    @pytest.mark.asyncio 
    async def test_process_thread_error_handling(self, mock_message):
        """Test error handling."""
        thread_content = [
            ("User", "test message")
        ]
        
        # Force an exception
        with patch('core.initialization.services.get_parsing_service', side_effect=Exception("Service error")):
            
            # Should not raise exception, should handle gracefully
            await process_thread_with_photos(
                message=mock_message,
                thread_content=thread_content,
                owner_name="Test User",
                location="UTC", 
                owner_id=123456
            )
            
            # Should reply with error message
            mock_message.reply.assert_called_once_with(
                "❌ Error creating task from messages. Please try again."
            )