"""Unit tests for task scheduler."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timezone, timedelta

from scheduler import _process_task_reminder, _send_reminder
from models.task import TaskDB


class TestTaskScheduler:
    """Test task scheduler functionality."""
    
    @pytest.fixture
    def sample_task(self):
        """Sample task for testing."""
        return TaskDB(
            id=1,
            user_id=12345,
            chat_id=67890,
            message_id=111,
            task_title="Test Reminder",
            task_description="Test reminder description",
            due_time="2024-01-01T12:00:00Z",
            platform_task_id="task_123",
            platform_type="todoist"
        )
    
    @pytest.fixture
    def overdue_task(self):
        """Task that is overdue."""
        past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        return TaskDB(
            id=2,
            user_id=12345,
            chat_id=67890,
            message_id=112,
            task_title="Overdue Task",
            task_description="This task is overdue",
            due_time=past_time.isoformat(),
            platform_task_id="task_456",
            platform_type="todoist"
        )
    
    @pytest.fixture
    def future_task(self):
        """Task that is not yet due."""
        future_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        return TaskDB(
            id=3,
            user_id=12345,
            chat_id=67890,
            message_id=113,
            task_title="Future Task",
            task_description="This task is not yet due",
            due_time=future_time.isoformat(),
            platform_task_id="task_789",
            platform_type="todoist"
        )
    
    @pytest.mark.asyncio
    async def test_send_reminder_with_notifications_enabled(self, sample_task):
        """Test sending reminder when notifications are enabled."""
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            
            await _send_reminder(sample_task)
            
            # Should send message
            mock_bot.send_message.assert_called_once()
            call_args = mock_bot.send_message.call_args
            assert call_args[1]['chat_id'] == 67890
            assert "Test Reminder" in call_args[1]['text']
    
    @pytest.mark.asyncio
    async def test_send_reminder_html_escaping(self, sample_task):
        """Test that HTML characters are properly escaped in reminders."""
        # Create task with HTML characters
        sample_task.task_title = "Task with <script> & quotes"
        sample_task.task_description = "Description with <b>HTML</b> & ampersands"
        
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            
            await _send_reminder(sample_task)
            
            # Should escape HTML characters
            mock_bot.send_message.assert_called_once()
            call_args = mock_bot.send_message.call_args
            message_text = call_args[1]['text']
            
            # Check that HTML is escaped
            assert "&lt;script&gt;" in message_text
            assert "&amp;" in message_text
            assert "<script>" not in message_text
    
    @pytest.mark.asyncio
    async def test_process_task_reminder_overdue_task(self, overdue_task):
        """Test processing an overdue task."""
        current_time = datetime.now(timezone.utc)
        
        with patch('scheduler._send_reminder') as mock_send:
            mock_send.return_value = None
            with patch('scheduler.services') as mock_services:
                mock_task_service = Mock()
                mock_task_service.task_repo.delete.return_value = True
                mock_services.get_clean_recipient_task_service.return_value = mock_task_service
                
                await _process_task_reminder(overdue_task, current_time)
                
                # Should send reminder and delete task
                mock_send.assert_called_once_with(overdue_task)
                mock_task_service.task_repo.delete.assert_called_once_with(overdue_task.id)
    
    @pytest.mark.asyncio
    async def test_process_task_reminder_future_task(self, future_task):
        """Test processing a task that is not yet due."""
        current_time = datetime.now(timezone.utc)
        
        with patch('scheduler._send_reminder') as mock_send:
            with patch('scheduler.services') as mock_services:
                mock_task_service = Mock()
                mock_services.get_clean_recipient_task_service.return_value = mock_task_service
                
                await _process_task_reminder(future_task, current_time)
                
                # Should not send reminder or delete task
                mock_send.assert_not_called()
                mock_task_service.task_repo.delete.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_process_task_reminder_invalid_due_time(self, sample_task):
        """Test processing task with invalid due time format."""
        sample_task.due_time = "invalid-date-format"
        current_time = datetime.now(timezone.utc)
        
        with patch('scheduler.services') as mock_services:
            mock_task_service = Mock()
            mock_task_service.task_repo.delete.return_value = True
            mock_services.get_clean_recipient_task_service.return_value = mock_task_service
            
            await _process_task_reminder(sample_task, current_time)
            
            # Should delete task with invalid due time
            mock_task_service.task_repo.delete.assert_called_once_with(sample_task.id)
    
    @pytest.mark.asyncio 
    async def test_send_reminder_error_handling(self, sample_task):
        """Test error handling in send_reminder."""
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock(side_effect=Exception("Bot error"))
            
            # Should raise exception after logging it
            with pytest.raises(Exception, match="Bot error"):
                await _send_reminder(sample_task)
    
    def test_task_db_model_attributes(self, sample_task):
        """Test that TaskDB model has all required attributes."""
        assert hasattr(sample_task, 'id')
        assert hasattr(sample_task, 'user_id') 
        assert hasattr(sample_task, 'chat_id')
        assert hasattr(sample_task, 'message_id')
        assert hasattr(sample_task, 'task_title')
        assert hasattr(sample_task, 'task_description')
        assert hasattr(sample_task, 'due_time')
        assert hasattr(sample_task, 'platform_task_id')
        assert hasattr(sample_task, 'platform_type')
    
    def test_task_scheduler_time_parsing(self):
        """Test that scheduler can parse various time formats."""
        from dateutil import parser
        
        # Test ISO format
        iso_time = "2024-01-01T12:00:00Z"
        parsed = parser.parse(iso_time)
        assert parsed.year == 2024
        assert parsed.month == 1
        assert parsed.day == 1
        
        # Test with timezone
        tz_time = "2024-01-01T12:00:00+00:00"
        parsed_tz = parser.parse(tz_time)
        assert parsed_tz.tzinfo is not None