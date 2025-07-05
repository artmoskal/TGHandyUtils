"""Integration tests for scheduler with real database schema."""

import pytest
import asyncio
import tempfile
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from database.migrations import ensure_database_ready
from database.connection import DatabaseManager
from database.repositories import TaskRepository
from database.unified_recipient_repository import UnifiedRecipientRepository
from models.task import TaskCreate
from scheduler import _check_and_process_due_tasks, _process_task_reminder, _send_reminder
from core.container import ApplicationContainer


class TestSchedulerDatabaseIntegration:
    """Test scheduler with real database to catch schema mismatches."""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
            db_path = tmp.name
        
        # Ensure database is properly initialized
        success = ensure_database_ready(db_path)
        assert success, "Database initialization failed"
        
        yield db_path
        
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)
    
    @pytest.fixture
    def db_manager(self, temp_db_path):
        """Create database manager with real database."""
        return DatabaseManager(temp_db_path)
    
    @pytest.fixture
    def task_repo(self, db_manager):
        """Create task repository with real database."""
        return TaskRepository(db_manager)
    
    @pytest.fixture
    def recipient_repo(self, db_manager):
        """Create recipient repository with real database."""
        return UnifiedRecipientRepository(db_manager)
    
    @pytest.fixture
    def container(self, temp_db_path):
        """Create a test container with real database."""
        container = ApplicationContainer()
        container.config.override({
            'DATABASE_PATH': temp_db_path,
            'DATABASE_TIMEOUT': 30.0
        })
        container.wire(modules=[__name__])
        yield container
        container.unwire()
    
    def test_database_schema_matches_task_model(self, task_repo):
        """Test that database schema matches what scheduler expects."""
        # Create a test task to verify all columns exist
        from models.unified_recipient import UnifiedRecipientCreate
        
        # First create a recipient (required for foreign key)
        recipient_repo = UnifiedRecipientRepository(task_repo.db_manager)
        recipient_data = UnifiedRecipientCreate(
            name="Test Recipient",
            platform_type="todoist",
            credentials="test_creds"
        )
        recipient_id = recipient_repo.create(
            user_id=12345,
            recipient_data=recipient_data,
            is_personal=True
        )
        
        # Create task with all required fields
        task_data = {
            'user_id': 12345,
            'title': 'Test Task',
            'description': 'Test Description',
            'due_time': '2025-12-25T10:00:00Z',
            'platform_task_id': 'test_123',
            'platform_type': 'todoist',
            'recipient_id': recipient_id,
            'chat_id': 12345,  # Required by scheduler
            'message_id': 67890,  # Required by scheduler
            'status': 'active'
        }
        
        # Insert task
        task_id = task_repo.create(**task_data)
        assert task_id is not None
        
        # Retrieve task and verify all scheduler-expected fields exist
        task = task_repo.get(task_id)
        assert task is not None
        
        # Test all fields that scheduler uses
        assert hasattr(task, 'id')
        assert hasattr(task, 'user_id')
        assert hasattr(task, 'title')  # Scheduler uses task.title
        assert hasattr(task, 'description')  # Scheduler uses task.description
        assert hasattr(task, 'due_time')
        assert hasattr(task, 'chat_id')  # Scheduler uses task.chat_id
        assert hasattr(task, 'message_id')  # Scheduler uses task.message_id
        
        # Verify values
        assert task.title == 'Test Task'
        assert task.description == 'Test Description'
        assert task.user_id == 12345
        assert task.chat_id == 12345
        assert task.message_id == 67890
    
    @pytest.mark.asyncio
    async def test_scheduler_can_read_tasks_from_database(self, task_repo, recipient_repo):
        """Test that scheduler can read tasks without column errors."""
        # Create a recipient first
        from models.unified_recipient import UnifiedRecipientCreate
        recipient_data = UnifiedRecipientCreate(
            name="Test Recipient",
            platform_type="todoist", 
            credentials="test_creds"
        )
        recipient_id = recipient_repo.create(
            user_id=12345,
            recipient_data=recipient_data,
            is_personal=True
        )
        
        # Create a task that's due now
        now = datetime.now(timezone.utc)
        due_time = (now - timedelta(minutes=1)).isoformat()  # 1 minute ago (overdue)
        
        task_data = {
            'user_id': 12345,
            'title': 'Overdue Task',
            'description': 'This task is overdue',
            'due_time': due_time,
            'recipient_id': recipient_id,
            'chat_id': 12345,
            'message_id': 67890,
            'status': 'active'
        }
        
        task_id = task_repo.create(**task_data)
        
        # Mock the container to use our test repositories
        with patch('core.initialization.services') as mock_services:
            mock_task_service = AsyncMock()
            mock_task_service.task_repo = task_repo
            mock_services.get_recipient_task_service.return_value = mock_task_service
            
            # This should not raise any "no such column" errors
            try:
                await _check_and_process_due_tasks()
                # If we get here, no column errors occurred
                success = True
            except Exception as e:
                if "no such column" in str(e):
                    pytest.fail(f"Column mismatch error: {e}")
                else:
                    # Other errors are okay for this test (like missing bot instance)
                    success = True
            
            assert success
    
    @pytest.mark.asyncio
    async def test_process_task_reminder_with_real_task(self, task_repo, recipient_repo):
        """Test processing a reminder with a real task object."""
        # Create recipient
        from models.unified_recipient import UnifiedRecipientCreate
        recipient_data = UnifiedRecipientCreate(
            name="Test Recipient",
            platform_type="todoist",
            credentials="test_creds"
        )
        recipient_id = recipient_repo.create(
            user_id=12345,
            recipient_data=recipient_data,
            is_personal=True
        )
        
        # Create overdue task
        now = datetime.now(timezone.utc)
        due_time = (now - timedelta(hours=1)).isoformat()
        
        task_data = {
            'user_id': 12345,
            'title': 'Test Reminder Task',
            'description': 'Task for reminder testing',
            'due_time': due_time,
            'recipient_id': recipient_id,
            'chat_id': 12345,
            'message_id': 67890,
            'status': 'active'
        }
        
        task_id = task_repo.create(**task_data)
        task = task_repo.get(task_id)
        
        # Mock the notification service and bot
        with patch('core.container.container') as mock_container, \
             patch('scheduler.bot') as mock_bot:
            
            # Mock recipient service to return notifications enabled
            mock_recipient_service = AsyncMock()
            mock_recipient_service.are_telegram_notifications_enabled.return_value = True
            mock_container.recipient_service.return_value = mock_recipient_service
            
            # Mock bot send_message
            mock_bot.send_message = AsyncMock()
            
            # Mock the task service for deletion
            with patch('core.initialization.services') as mock_services:
                mock_task_service = AsyncMock()
                mock_task_service.task_repo = task_repo
                mock_services.get_recipient_task_service.return_value = mock_task_service
                
                # This should work without column errors
                await _process_task_reminder(task, now)
                
                # Verify bot was called with correct data
                mock_bot.send_message.assert_called_once()
                call_args = mock_bot.send_message.call_args
                
                assert call_args[1]['chat_id'] == 12345
                assert 'Test Reminder Task' in call_args[1]['text']
                assert call_args[1]['reply_to_message_id'] == 67890
    
    @pytest.mark.asyncio 
    async def test_send_reminder_accesses_all_expected_fields(self, task_repo, recipient_repo):
        """Test that _send_reminder can access all fields it needs."""
        # Create recipient
        from models.unified_recipient import UnifiedRecipientCreate
        recipient_data = UnifiedRecipientCreate(
            name="Test Recipient",
            platform_type="todoist",
            credentials="test_creds"
        )
        recipient_id = recipient_repo.create(
            user_id=12345,
            recipient_data=recipient_data,
            is_personal=True
        )
        
        # Create task with special characters to test escaping
        task_data = {
            'user_id': 12345,
            'title': 'Task with <HTML> & "quotes"',
            'description': 'Description with <script>alert("xss")</script>',
            'due_time': '2025-12-25T10:00:00Z',
            'recipient_id': recipient_id,
            'chat_id': 12345,
            'message_id': 67890,
            'status': 'active'
        }
        
        task_id = task_repo.create(**task_data)
        task = task_repo.get(task_id)
        
        # Mock dependencies
        with patch('core.container.container') as mock_container, \
             patch('scheduler.bot') as mock_bot:
            
            mock_recipient_service = AsyncMock()
            mock_recipient_service.are_telegram_notifications_enabled.return_value = True
            mock_container.recipient_service.return_value = mock_recipient_service
            
            mock_bot.send_message = AsyncMock()
            
            # This should access task.title, task.description, task.user_id, task.chat_id, task.message_id
            await _send_reminder(task)
            
            # Verify all expected fields were accessed without errors
            mock_bot.send_message.assert_called_once()
            call_args = mock_bot.send_message.call_args
            
            # Verify HTML was escaped
            assert '&lt;HTML&gt;' in call_args[1]['text']
            assert '&lt;script&gt;' in call_args[1]['text']
            
            # Verify all required fields were used
            assert call_args[1]['chat_id'] == 12345
            assert call_args[1]['reply_to_message_id'] == 67890
    
    def test_task_repository_create_and_get_consistency(self, task_repo, recipient_repo):
        """Test that TaskRepository.create and get return consistent field names."""
        # Create recipient
        from models.unified_recipient import UnifiedRecipientCreate
        recipient_data = UnifiedRecipientCreate(
            name="Test Recipient",
            platform_type="todoist",
            credentials="test_creds"
        )
        recipient_id = recipient_repo.create(
            user_id=12345,
            recipient_data=recipient_data,
            is_personal=True
        )
        
        # Test data with all scheduler-required fields
        original_data = {
            'user_id': 12345,
            'title': 'Consistency Test',
            'description': 'Testing field consistency',
            'due_time': '2025-12-25T10:00:00Z',
            'platform_task_id': 'test_123',
            'platform_type': 'todoist',
            'recipient_id': recipient_id,
            'chat_id': 98765,
            'message_id': 54321,
            'status': 'active'
        }
        
        # Create task
        task_id = task_repo.create(**original_data)
        
        # Retrieve task
        retrieved_task = task_repo.get(task_id)
        
        # Verify all fields match what we put in
        assert retrieved_task.user_id == original_data['user_id']
        assert retrieved_task.title == original_data['title']
        assert retrieved_task.description == original_data['description']
        assert retrieved_task.due_time == original_data['due_time']
        assert retrieved_task.chat_id == original_data['chat_id']
        assert retrieved_task.message_id == original_data['message_id']
        assert retrieved_task.status == original_data['status']
    
    def test_database_migration_creates_all_required_columns(self, temp_db_path):
        """Test that database migration creates all columns scheduler expects."""
        # The database should already be created by the fixture
        db_manager = DatabaseManager(temp_db_path)
        
        # Query the schema to verify all columns exist
        with db_manager.get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(tasks)")
            columns = {row[1] for row in cursor.fetchall()}  # row[1] is column name
        
        required_columns = {
            'id', 'user_id', 'title', 'description', 'due_time',
            'platform_task_id', 'platform_type', 'recipient_id',
            'chat_id', 'message_id', 'created_at', 'updated_at', 'status'
        }
        
        missing_columns = required_columns - columns
        assert not missing_columns, f"Missing required columns: {missing_columns}"
        
        # Verify column types are reasonable
        cursor = conn.execute("PRAGMA table_info(tasks)")
        column_info = {row[1]: row[2] for row in cursor.fetchall()}  # name: type
        
        # These should be INTEGER
        assert 'INTEGER' in column_info['id']
        assert 'INTEGER' in column_info['user_id'] 
        assert 'INTEGER' in column_info['chat_id']
        assert 'INTEGER' in column_info['message_id']
        assert 'INTEGER' in column_info['recipient_id']
        
        # These should be TEXT
        assert 'TEXT' in column_info['title']
        assert 'TEXT' in column_info['description']
        assert 'TEXT' in column_info['due_time']