"""Unit tests for task scheduler using Factory Boy with realistic task data.

This module tests the scheduler functionality with Factory Boy objects instead of 
hardcoded test data, ensuring realistic scheduling scenarios are tested.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timezone, timedelta

# Import scheduler functions
from scheduler import _process_task_reminder, _send_reminder

# Import Factory Boy factories
from tests.factories import (
    TaskFactory,
    TaskDBFactory,
    SimpleTaskFactory,
    SimpleTaskDBFactory,
    ScreenshotTaskFactory,
    ScreenshotTaskDBFactory,
    UrgentTaskFactory,
    TelegramMessageFactory,
    TelegramUserFactory
)

# Import models
from models.task import TaskDB


class TestTaskScheduler:
    """Test task scheduler functionality with Factory Boy objects."""
    
    def setup_method(self):
        """Setup test data for scheduler tests."""
        self.test_user_id = 111111111
        self.test_chat_id = 222222222
        self.test_message_id = 333333333
    
    def test_sample_task_creation_with_factory(self):
        """Test creating sample tasks using Factory Boy."""
        # Create realistic task using factory
        task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Team Meeting Reminder",
            description="Weekly team sync meeting",
            due_time="2024-01-01T12:00:00Z",
            platform_task_id="task_meeting_123",
            platform_type="todoist"
        )
        
        # Verify factory creates realistic task
        assert task.title == "Team Meeting Reminder"
        assert task.description == "Weekly team sync meeting"
        assert task.due_time == "2024-01-01T12:00:00Z"
        assert task.platform_type == "todoist"
    
    def test_overdue_task_creation_with_factory(self):
        """Test creating overdue tasks using Factory Boy."""
        # Create overdue task
        past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        overdue_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Overdue Project Deadline",
            description="Submit project proposal",
            due_time=past_time.isoformat(),
            platform_task_id="task_overdue_456",
            platform_type="trello"
        )
        
        # Verify overdue task properties
        assert "overdue" in overdue_task.title.lower()
        assert len(overdue_task.description) > 0
        assert overdue_task.platform_type == "trello"
        
        # Verify task is actually overdue
        task_due_time = datetime.fromisoformat(overdue_task.due_time.replace('Z', '+00:00'))
        current_time = datetime.now(timezone.utc)
        assert task_due_time < current_time
    
    def test_future_task_creation_with_factory(self):
        """Test creating future tasks using Factory Boy."""
        # Create future task
        future_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        future_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Upcoming Client Call",
            description="Quarterly review with client",
            due_time=future_time.isoformat(),
            platform_task_id="task_future_789",
            platform_type="todoist"
        )
        
        # Verify future task properties
        assert "upcoming" in future_task.title.lower() or "call" in future_task.title.lower()
        assert len(future_task.description) > 0
        
        # Verify task is actually in the future
        task_due_time = datetime.fromisoformat(future_task.due_time.replace('Z', '+00:00'))
        current_time = datetime.now(timezone.utc)
        assert task_due_time > current_time
    
    @pytest.mark.asyncio
    async def test_send_reminder_with_factory_task(self):
        """Test sending reminder with Factory Boy generated task."""
        # Create realistic task using factory
        reminder_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Doctor Appointment Reminder",
            description="Annual health checkup with Dr. Smith",
            due_time="2024-01-01T12:00:00Z"
        )
        
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            
            await _send_reminder(reminder_task)
            
            # Should send message
            mock_bot.send_message.assert_called_once()
            call_args = mock_bot.send_message.call_args
            assert call_args[1]['chat_id'] == self.test_chat_id
            assert "Doctor Appointment Reminder" in call_args[1]['text']
            assert "Dr. Smith" in call_args[1]['text']
    
    @pytest.mark.asyncio
    async def test_send_reminder_with_screenshot_task(self):
        """Test sending reminder for screenshot task."""
        # Create screenshot task using factory
        screenshot_task = ScreenshotTaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Review UI Screenshot Analysis",
            description="Check attached screenshot for design issues"
        )
        
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            
            await _send_reminder(screenshot_task)
            
            # Should send message with screenshot context
            mock_bot.send_message.assert_called_once()
            call_args = mock_bot.send_message.call_args
            assert call_args[1]['chat_id'] == self.test_chat_id
            assert "screenshot" in call_args[1]['text'].lower()
    
    @pytest.mark.asyncio
    async def test_send_reminder_with_urgent_task(self):
        """Test sending reminder for urgent task."""
        # Create urgent task using factory
        urgent_task = SimpleTaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="URGENT: Fix Production Bug",
            description="Critical server issue affecting all users",
            priority="urgent"
        )
        
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            
            await _send_reminder(urgent_task)
            
            # Should send message with urgent context
            mock_bot.send_message.assert_called_once()
            call_args = mock_bot.send_message.call_args
            assert call_args[1]['chat_id'] == self.test_chat_id
            assert "URGENT" in call_args[1]['text']
            assert "critical" in call_args[1]['text'].lower()
    
    @pytest.mark.asyncio
    async def test_send_reminder_html_escaping_with_factory(self):
        """Test that HTML characters are properly escaped in reminders with Factory Boy data."""
        # Create task with HTML characters using factory
        html_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Task with <script> & special chars",
            description="Description with <b>HTML</b> & ampersands"
        )
        
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            
            await _send_reminder(html_task)
            
            # Should escape HTML characters
            mock_bot.send_message.assert_called_once()
            call_args = mock_bot.send_message.call_args
            message_text = call_args[1]['text']
            
            # Check that HTML is escaped
            assert "&lt;script&gt;" in message_text
            assert "&amp;" in message_text
            assert "<script>" not in message_text
            assert "&lt;b&gt;HTML&lt;/b&gt;" in message_text
    
    @pytest.mark.asyncio
    async def test_process_task_reminder_overdue_factory_task(self):
        """Test processing an overdue task created with Factory Boy."""
        # Create overdue task using factory
        past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        overdue_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Overdue Report Submission",
            description="Submit monthly performance report",
            due_time=past_time.isoformat(),
            platform_task_id="task_overdue_report",
            platform_type="todoist"
        )
        
        current_time = datetime.now(timezone.utc)
        
        with patch('scheduler._send_reminder') as mock_send:
            mock_send.return_value = None
            with patch('scheduler.services') as mock_services:
                mock_task_service = Mock()
                mock_task_service.task_repo.delete.return_value = True
                mock_services.get_recipient_task_service.return_value = mock_task_service
                
                await _process_task_reminder(overdue_task, current_time)
                
                # Should send reminder and delete task
                mock_send.assert_called_once_with(overdue_task)
                mock_task_service.task_repo.delete.assert_called_once_with(overdue_task.id)
    
    @pytest.mark.asyncio
    async def test_process_task_reminder_future_factory_task(self):
        """Test processing a future task created with Factory Boy."""
        # Create future task using factory
        future_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        future_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Upcoming Team Standup",
            description="Daily team synchronization meeting",
            due_time=future_time.isoformat(),
            platform_task_id="task_future_standup",
            platform_type="trello"
        )
        
        current_time = datetime.now(timezone.utc)
        
        with patch('scheduler._send_reminder') as mock_send:
            with patch('scheduler.services') as mock_services:
                mock_task_service = Mock()
                mock_services.get_recipient_task_service.return_value = mock_task_service
                
                await _process_task_reminder(future_task, current_time)
                
                # Should not send reminder or delete task
                mock_send.assert_not_called()
                mock_task_service.task_repo.delete.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_process_task_reminder_invalid_due_time_factory_task(self):
        """Test processing task with invalid due time format from Factory Boy."""
        # Create task with invalid due time using factory
        invalid_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Task with Invalid Time",
            description="This task has malformed due time",
            due_time="invalid-date-format",  # Invalid format
            platform_task_id="task_invalid_time",
            platform_type="todoist"
        )
        
        current_time = datetime.now(timezone.utc)
        
        with patch('scheduler.services') as mock_services:
            mock_task_service = Mock()
            mock_task_service.task_repo.delete.return_value = True
            mock_services.get_recipient_task_service.return_value = mock_task_service
            
            await _process_task_reminder(invalid_task, current_time)
            
            # Should delete task with invalid due time
            mock_task_service.task_repo.delete.assert_called_once_with(invalid_task.id)
    
    @pytest.mark.asyncio 
    async def test_send_reminder_error_handling_with_factory(self):
        """Test error handling in send_reminder with Factory Boy task."""
        # Create realistic task for error testing
        error_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id,
            title="Error Test Task",
            description="Task for testing error scenarios"
        )
        
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock(side_effect=Exception("Bot error"))
            
            # Should raise exception after logging it
            with pytest.raises(Exception, match="Bot error"):
                await _send_reminder(error_task)
    
    def test_task_db_model_attributes_with_factory(self):
        """Test that TaskDB model has all required attributes using Factory Boy."""
        # Create task using factory
        factory_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            message_id=self.test_message_id
        )
        
        # Test that TaskDB model has all required attributes
        assert hasattr(factory_task, 'id')
        assert hasattr(factory_task, 'user_id') 
        assert hasattr(factory_task, 'chat_id')
        assert hasattr(factory_task, 'message_id')
        assert hasattr(factory_task, 'title')
        assert hasattr(factory_task, 'description')
        assert hasattr(factory_task, 'due_time')
        assert hasattr(factory_task, 'platform_task_id')
        assert hasattr(factory_task, 'platform_type')
        
        # Verify factory sets realistic values
        assert factory_task.user_id == self.test_user_id
        assert factory_task.chat_id == self.test_chat_id
        assert factory_task.message_id == self.test_message_id
        assert len(factory_task.title) > 0
        assert len(factory_task.description) >= 0  # Can be empty
        assert factory_task.platform_type in ['todoist', 'trello']
    
    def test_task_scheduler_time_parsing_with_factory_tasks(self):
        """Test that scheduler can parse various time formats from Factory Boy tasks."""
        from dateutil import parser
        
        # Create tasks with various time formats using factory
        iso_task = TaskDBFactory(due_time="2024-01-01T12:00:00Z")
        tz_task = TaskDBFactory(due_time="2024-01-01T12:00:00+00:00")
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        future_task = TaskDBFactory(due_time=future_time)
        
        # Test ISO format parsing
        parsed_iso = parser.parse(iso_task.due_time)
        assert parsed_iso.year == 2024
        assert parsed_iso.month == 1
        assert parsed_iso.day == 1
        
        # Test timezone format parsing
        parsed_tz = parser.parse(tz_task.due_time)
        assert parsed_tz.tzinfo is not None
        
        # Test factory-generated future time parsing
        parsed_future = parser.parse(future_task.due_time)
        assert parsed_future > datetime.now(timezone.utc)
    
    @pytest.mark.asyncio
    async def test_scheduler_with_multiple_task_types(self):
        """Test scheduler handles multiple task types from Factory Boy correctly."""
        # Create various task types using factories
        regular_task = TaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            title="Regular Meeting Task"
        )
        screenshot_task = ScreenshotTaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            title="Screenshot Review Task"
        )
        urgent_task = SimpleTaskDBFactory(
            user_id=self.test_user_id,
            chat_id=self.test_chat_id,
            title="URGENT: Fix Critical Bug"
        )
        
        tasks = [regular_task, screenshot_task, urgent_task]
        
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            
            # Test each task type
            for task in tasks:
                await _send_reminder(task)
            
            # Verify all tasks generated reminders
            assert mock_bot.send_message.call_count == 3
            
            # Verify task type-specific content
            call_args_list = mock_bot.send_message.call_args_list
            messages = [call_args[1]['text'] for call_args in call_args_list]
            
            assert any("regular meeting" in msg.lower() for msg in messages)
            assert any("screenshot" in msg.lower() for msg in messages)
            assert any("urgent" in msg.lower() for msg in messages)
    
    def test_factory_task_realism_for_scheduler(self):
        """Test that Factory Boy creates realistic tasks suitable for scheduler testing."""
        # Create batch of tasks using factories
        tasks = []
        for i in range(10):
            if i % 3 == 0:
                task = TaskDBFactory(title=f"Meeting Task {i}")
            elif i % 3 == 1:
                task = ScreenshotTaskDBFactory(title=f"Screenshot Task {i}")
            else:
                task = SimpleTaskDBFactory(title=f"Urgent Task {i}")
            tasks.append(task)
        
        # Verify task variety and realism
        titles = [task.title for task in tasks]
        descriptions = [task.description for task in tasks]
        due_times = [task.due_time for task in tasks]
        
        # Should have variety in titles
        assert len(set(titles)) == len(titles)  # All different
        
        # Should have realistic descriptions
        for desc in descriptions:
            assert isinstance(desc, str)
        
        # Should have proper due time formats
        for due_time in due_times:
            try:
                from dateutil import parser
                parsed = parser.parse(due_time)
                assert parsed is not None
            except Exception:
                pytest.fail(f"Invalid due time format: {due_time}")
        
        # Should have proper platform types
        platform_types = {task.platform_type for task in tasks}
        assert platform_types.issubset({'todoist', 'trello'})
        
        # Should have realistic user/chat/message IDs
        for task in tasks:
            assert task.user_id > 0
            assert task.chat_id > 0
            assert task.message_id > 0
    
    @pytest.mark.asyncio
    async def test_scheduler_performance_with_many_factory_tasks(self):
        """Test scheduler performance with many Factory Boy generated tasks."""
        # Create larger batch of tasks for performance testing
        tasks = []
        base_time = datetime.now(timezone.utc)
        
        for i in range(50):
            # Create mix of overdue and future tasks
            if i % 2 == 0:
                due_time = (base_time - timedelta(minutes=i)).isoformat()
            else:
                due_time = (base_time + timedelta(minutes=i)).isoformat()
            
            task = TaskDBFactory(
                user_id=self.test_user_id + i,
                chat_id=self.test_chat_id + i,
                message_id=self.test_message_id + i,
                title=f"Performance Test Task {i}",
                due_time=due_time
            )
            tasks.append(task)
        
        # Test scheduler processing performance
        with patch('scheduler.bot', create=True) as mock_bot:
            mock_bot.send_message = AsyncMock()
            with patch('scheduler.services') as mock_services:
                mock_task_service = Mock()
                mock_task_service.task_repo.delete.return_value = True
                mock_services.get_recipient_task_service.return_value = mock_task_service
                
                current_time = datetime.now(timezone.utc)
                
                # Process all tasks
                for task in tasks:
                    await _process_task_reminder(task, current_time)
                
                # Verify scheduler handled all tasks
                # Should have sent reminders for overdue tasks (roughly half)
                reminder_count = mock_bot.send_message.call_count
                delete_count = mock_task_service.task_repo.delete.call_count
                
                assert reminder_count > 0  # Some reminders sent
                assert delete_count > 0    # Some tasks deleted
                assert reminder_count <= len(tasks)  # Not more than total tasks