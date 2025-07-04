"""Simple unit test to verify scheduler can access task fields."""

import pytest
import tempfile
import os
from database.migrations import ensure_database_ready
from database.connection import DatabaseManager
from database.repositories import TaskRepository
from database.unified_recipient_repository import UnifiedRecipientRepository
from models.task import TaskCreate
from models.unified_recipient import UnifiedRecipientCreate


class TestSimpleSchedulerCompatibility:
    """Simple test to check if scheduler field access works."""
    
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
    
    def test_scheduler_field_access_works(self, temp_db_path):
        """Test that we can create and access a task with scheduler-expected fields."""
        # Setup repositories
        db_manager = DatabaseManager(temp_db_path)
        task_repo = TaskRepository(db_manager)
        recipient_repo = UnifiedRecipientRepository(db_manager)
        
        # Create recipient
        recipient_data = UnifiedRecipientCreate(
            name="Test Recipient",
            platform_type="todoist",
            credentials="test_creds"
        )
        recipient_id = recipient_repo.add_recipient(
            user_id=12345,
            recipient=recipient_data
        )
        
        # Create task
        task_data = TaskCreate(
            title='Test Task',
            description='Test Description',
            due_time='2025-12-25T10:00:00Z'
        )
        
        task_id = task_repo.create(
            user_id=12345,
            chat_id=12345,
            message_id=67890,
            task_data=task_data
        )
        
        # Get task back
        task = task_repo.get_by_id(task_id)
        assert task is not None
        
        # Test ALL field access patterns that scheduler.py uses
        # These should NOT raise AttributeError
        
        # From scheduler.py line 67
        assert hasattr(task, 'due_time')
        _ = task.due_time
        
        # From scheduler.py line 82 and 102 and 122
        assert hasattr(task, 'id')
        assert hasattr(task, 'title')
        assert hasattr(task, 'user_id')
        _ = task.id
        _ = task.title
        _ = task.user_id
        
        # From scheduler.py line 96
        assert hasattr(task, 'user_id')
        _ = task.user_id
        
        # From scheduler.py line 106-107
        assert hasattr(task, 'title')
        assert hasattr(task, 'description')
        _ = task.title
        _ = task.description
        
        # From scheduler.py line 114, 116, 120
        assert hasattr(task, 'chat_id')
        assert hasattr(task, 'message_id')
        _ = task.chat_id
        _ = task.message_id
        
        # Verify actual values
        assert task.title == 'Test Task'
        assert task.description == 'Test Description'
        assert task.user_id == 12345
        assert task.chat_id == 12345
        assert task.message_id == 67890
        assert task.due_time == '2025-12-25T10:00:00Z'
        
        print("✅ All scheduler field access patterns work correctly!")
    
    def test_get_all_returns_compatible_objects(self, temp_db_path):
        """Test that get_all() returns objects compatible with scheduler."""
        # Setup
        db_manager = DatabaseManager(temp_db_path)
        task_repo = TaskRepository(db_manager)
        recipient_repo = UnifiedRecipientRepository(db_manager)
        
        # Create recipient
        recipient_data = UnifiedRecipientCreate(
            name="Test Recipient",
            platform_type="todoist",
            credentials="test_creds"
        )
        recipient_id = recipient_repo.add_recipient(
            user_id=12345,
            recipient=recipient_data
        )
        
        # Create task
        task_data = TaskCreate(
            title='Scheduler Test Task',
            description='For testing get_all',
            due_time='2025-12-25T10:00:00Z'
        )
        
        task_id = task_repo.create(
            user_id=12345,
            chat_id=98765,
            message_id=54321,
            task_data=task_data
        )
        
        # This is what scheduler.py does on line 40
        all_tasks = task_repo.get_all()
        
        # Should have at least our task
        assert len(all_tasks) >= 1
        
        # Find our task
        our_task = None
        for task in all_tasks:
            if task.id == task_id:
                our_task = task
                break
        
        assert our_task is not None
        
        # Test scheduler field access on task from get_all()
        _ = our_task.id
        _ = our_task.user_id
        _ = our_task.title
        _ = our_task.description
        _ = our_task.due_time
        _ = our_task.chat_id
        _ = our_task.message_id
        
        print("✅ get_all() returns scheduler-compatible objects!")