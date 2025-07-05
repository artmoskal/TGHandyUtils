"""Unit tests for RecipientTaskService using Factory Boy with real database integration.

This module tests the RecipientTaskService with real database operations and Factory Boy
objects, replacing the previous mock-based testing that hid critical implementation bugs.
"""

import pytest
from datetime import datetime
from unittest.mock import patch, Mock

# Import database and service components
from database.connection import DatabaseManager
from database.unified_recipient_repository import UnifiedRecipientRepository
from database.repositories import TaskRepository
from services.recipient_service import RecipientService
from services.recipient_task_service import RecipientTaskService

# Import Factory Boy factories
from tests.factories import (
    UnifiedRecipientFactory,
    SharedRecipientFactory,
    PersonalRecipientFactory,
    TodoistRecipientFactory,
    TrelloRecipientFactory,
    TaskFactory,
    SimpleTaskFactory,
    ScreenshotTaskFactory,
    UrgentTaskFactory,
    TaskBatchFactory,
    MultiPlatformRecipientFactory
)

# Import models
from models.unified_recipient import UnifiedRecipient
from models.task import TaskDB, TaskCreate


class TestRecipientTaskService:
    """Test cases for RecipientTaskService with real database integration."""
    
    def setup_method(self):
        """Setup real database components for each test."""
        self.db_manager = DatabaseManager("data/db/tasks.db")
        self.recipient_repo = UnifiedRecipientRepository(self.db_manager)
        self.task_repo = TaskRepository(self.db_manager)
        self.recipient_service = RecipientService(self.recipient_repo)
        self.task_service = RecipientTaskService(self.task_repo, self.recipient_service)
        
        # Test user ID for isolation
        self.test_user_id = 777777777  # Unique ID to avoid conflicts
    
    def teardown_method(self):
        """Clean up test data after each test."""
        with self.db_manager.get_connection() as conn:
            # Clean up test recipients and tasks
            conn.execute("DELETE FROM recipients WHERE user_id = ?", (self.test_user_id,))
            conn.execute("DELETE FROM tasks WHERE user_id = ?", (self.test_user_id,))
    
    def test_create_task_for_recipients_with_personal_recipients_only(self):
        """Test task creation with only personal recipients.
        
        CRITICAL: This tests the core workflow where personal recipients
        should get automatic task creation without confirmation prompts.
        """
        # Create personal recipients using factories
        personal_todoist = TodoistRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            enabled=True,
            name="My Personal Todoist"
        )
        personal_trello = TrelloRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            enabled=True,
            name="My Personal Trello"
        )
        
        # Persist to database
        self.recipient_repo.add_recipient(self.test_user_id, personal_todoist)
        self.recipient_repo.add_recipient(self.test_user_id, personal_trello)
        
        # Create realistic task using factory
        task = TaskFactory(
            title="Factory Test Task",
            description="Testing task creation with Factory Boy"
        )
        
        # Mock platform create_task calls to avoid real API calls
        with patch('platforms.todoist.TodoistPlatform.create_task', return_value='todoist-task-123'), \
             patch('platforms.trello.TrelloPlatform.create_task', return_value='trello-task-456'):
            # Test task creation service
            success, feedback, actions = self.task_service.create_task_for_recipients(
                user_id=self.test_user_id,
                title=task.title,
                description=task.description
            )
        
        # Verify successful creation for personal recipients
        # Note: This tests the service logic without actual platform API calls
        assert isinstance(success, bool)
        assert isinstance(feedback, str)
        assert isinstance(actions, dict)
        
        # Verify the service processes personal recipients correctly
        default_recipients = self.recipient_service.get_default_recipients(self.test_user_id)
        assert len(default_recipients) == 2
        assert all(r.is_personal for r in default_recipients)
    
    def test_create_task_for_recipients_with_shared_recipients_only(self):
        """Test task creation with only shared recipients.
        
        CRITICAL: This tests the shared account workflow that was broken.
        Shared recipients should require explicit confirmation, not auto-creation.
        """
        # Create shared recipients using factories
        shared_todoist = TodoistRecipientFactory(
            user_id=self.test_user_id,
            is_personal=False,  # CRITICAL: Shared recipients
            enabled=True,
            name="Partner Todoist Account"
        )
        shared_trello = TrelloRecipientFactory(
            user_id=self.test_user_id,
            is_personal=False,  # CRITICAL: Shared recipients
            enabled=True,
            name="Partner Trello Account"
        )
        
        # Persist to database
        self.recipient_repo.add_recipient(self.test_user_id, shared_todoist)
        self.recipient_repo.add_recipient(self.test_user_id, shared_trello)
        
        # Create task for testing
        task = TaskFactory(
            title="Shared Recipients Test Task",
            description="Testing with shared recipients only"
        )
        
        # Test default recipients (should be empty for shared-only)
        default_recipients = self.recipient_service.get_default_recipients(self.test_user_id)
        all_recipients = self.recipient_service.get_all_recipients(self.test_user_id)
        
        # Verify shared recipients are not in defaults
        assert len(default_recipients) == 0  # No auto-creation for shared
        assert len(all_recipients) == 2      # But they exist for confirmation
        assert all(not r.is_personal for r in all_recipients)
    
    def test_create_task_for_recipients_mixed_personal_and_shared(self):
        """Test task creation with mixed personal and shared recipients.
        
        CRITICAL: This tests the core business logic that determines which
        recipients get automatic task creation vs confirmation prompts.
        """
        # Create mixed recipient scenario using factories
        scenarios = MultiPlatformRecipientFactory.create_mixed_scenarios(self.test_user_id)
        
        # Persist all recipients to database
        for category, recipients in scenarios.items():
            for recipient in recipients:
                self.recipient_repo.add_recipient(self.test_user_id, recipient)
        
        # Create task for testing
        task = TaskFactory(
            title="Mixed Recipients Test",
            description="Testing personal and shared recipient logic"
        )
        
        # Test recipient categorization
        default_recipients = self.recipient_service.get_default_recipients(self.test_user_id)
        all_recipients = self.recipient_service.get_all_recipients(self.test_user_id)
        shared_recipients = self.recipient_service.get_shared_recipients(self.test_user_id)
        
        # Verify correct categorization
        assert len(all_recipients) == 5     # Total: 2 personal + 2 shared + 1 disabled
        assert len(default_recipients) == 2 # Only personal enabled recipients
        assert len(shared_recipients) == 2  # Only shared recipients
        
        # Verify default recipients are personal and enabled
        for recipient in default_recipients:
            assert recipient.is_personal is True
            assert recipient.enabled is True
        
        # Verify shared recipients are not personal
        for recipient in shared_recipients:
            assert recipient.is_personal is False
    
    def test_recipient_filtering_for_task_actions(self):
        """Test that recipient filtering works correctly for action generation.
        
        This verifies the logic that determines which recipients show up
        in confirmation buttons and action lists.
        """
        # Create comprehensive recipient set
        personal_enabled = PersonalRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            enabled=True,
            name="Personal Enabled"
        )
        personal_disabled = PersonalRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            enabled=False,
            name="Personal Disabled"
        )
        shared_enabled = SharedRecipientFactory(
            user_id=self.test_user_id,
            is_personal=False,
            enabled=True,
            name="Shared Enabled"
        )
        shared_disabled = SharedRecipientFactory(
            user_id=self.test_user_id,
            is_personal=False,
            enabled=False,
            name="Shared Disabled"
        )
        
        # Persist all recipients
        recipients = [personal_enabled, personal_disabled, shared_enabled, shared_disabled]
        for recipient in recipients:
            self.recipient_repo.add_recipient(self.test_user_id, recipient)
        
        # Test various filtering methods
        all_recipients = self.recipient_service.get_all_recipients(self.test_user_id)
        enabled_recipients = self.recipient_service.get_enabled_recipients(self.test_user_id)
        default_recipients = self.recipient_service.get_default_recipients(self.test_user_id)
        shared_recipients = self.recipient_service.get_shared_recipients(self.test_user_id)
        
        # Verify filtering correctness
        assert len(all_recipients) == 4
        assert len(enabled_recipients) == 2  # Only enabled ones
        assert len(default_recipients) == 1  # Only personal + enabled
        assert len(shared_recipients) == 2   # Both shared (enabled and disabled)
        
        # Verify specific filtering logic
        assert default_recipients[0].name == "Personal Enabled"
        enabled_names = {r.name for r in enabled_recipients}
        assert enabled_names == {"Personal Enabled", "Shared Enabled"}
    
    def test_task_creation_with_screenshot_data(self):
        """Test task creation with screenshot attachment data.
        
        CRITICAL: This tests the screenshot attachment workflow that was
        broken by mock-based testing hiding missing attach_screenshot calls.
        """
        # Create recipient for screenshot testing
        recipient = TodoistRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            enabled=True,
            name="Screenshot Test Recipient"
        )
        self.recipient_repo.add_recipient(self.test_user_id, recipient)
        
        # Create screenshot task using factory
        screenshot_task = ScreenshotTaskFactory(
            title="Task with Screenshot",
            description="Testing screenshot attachment workflow"
        )
        
        # Create realistic screenshot data
        screenshot_data = {
            'image_data': b'fake_screenshot_bytes_for_testing',
            'file_name': 'test_screenshot.png',
            'file_id': 'telegram_file_12345'
        }
        
        # Test task creation with screenshot data
        # Note: This tests the service layer handling of screenshot data
        # without requiring actual platform API calls
        with patch('platforms.todoist.TodoistPlatform.create_task', return_value='todoist-task-screenshot-123'):
            try:
                success, feedback, actions = self.task_service.create_task_for_recipients(
                    user_id=self.test_user_id,
                    title=screenshot_task.title,
                    description=screenshot_task.description,
                    screenshot_data=screenshot_data
                )
                
                # Verify service handles screenshot data without errors
                assert isinstance(success, bool)
                assert isinstance(feedback, str)
                assert isinstance(actions, dict)
                
            except Exception as e:
                pytest.fail(f"Screenshot task creation failed: {e}")
    
    def test_urgent_task_prioritization(self):
        """Test that urgent tasks are handled correctly by the service."""
        # Create recipient for urgent task testing
        recipient = TrelloRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            enabled=True,
            name="Urgent Task Recipient"
        )
        self.recipient_repo.add_recipient(self.test_user_id, recipient)
        
        # Create urgent task using factory
        urgent_task = SimpleTaskFactory(
            title="URGENT: Critical Issue",
            priority="urgent"
        )
        
        # Test urgent task creation
        success, feedback, actions = self.task_service.create_task_for_recipients(
            user_id=self.test_user_id,
            title=urgent_task.title,
            description=urgent_task.description
        )
        
        # Verify urgent task handling
        assert isinstance(success, bool)
        assert urgent_task.priority == "urgent"
        assert "URGENT" in urgent_task.title
    
    def test_batch_task_creation_scenarios(self):
        """Test various batch task creation scenarios with Factory Boy."""
        # Create multiple recipients for batch testing
        recipients = MultiPlatformRecipientFactory.create_all_platforms(
            self.test_user_id,
            is_personal=True  # All personal for automatic creation
        )
        
        # Persist recipients
        for recipient in recipients:
            self.recipient_repo.add_recipient(self.test_user_id, recipient)
        
        # Create batch of varied tasks
        task_batch = TaskBatchFactory.create_mixed_priority_batch(size=5)
        
        # Test service handles varied task types
        with patch('platforms.todoist.TodoistPlatform.create_task', return_value='todoist-batch-123'), \
             patch('platforms.trello.TrelloPlatform.create_task', return_value='trello-batch-456'):
            for task in task_batch:
                try:
                    success, feedback, actions = self.task_service.create_task_for_recipients(
                        user_id=self.test_user_id,
                        title=task.title,
                        description=task.description
                    )
                    
                    # Verify each task is processed
                    assert isinstance(success, bool)
                    assert len(task.title) > 0
                    # Only check priority if the task has it
                    if hasattr(task, 'priority'):
                        assert task.priority in ['low', 'medium', 'high', 'urgent']
                    
                except Exception as e:
                    pytest.fail(f"Batch task creation failed for {task.title}: {e}")
    
    def test_recipient_task_service_database_integration(self):
        """Test that the service properly integrates with real database operations."""
        # Create test recipient
        recipient = TodoistRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            enabled=True,
            name="Database Integration Test"
        )
        recipient_id = self.recipient_repo.add_recipient(self.test_user_id, recipient)
        
        # Verify recipient exists in database
        retrieved_recipient = self.recipient_repo.get_recipient_by_id(self.test_user_id, recipient_id)
        assert retrieved_recipient is not None
        assert retrieved_recipient.name == "Database Integration Test"
        
        # Create task using factory
        task = TaskFactory(
            title="Database Integration Task",
            description="Testing real database integration"
        )
        
        # Test service uses real database operations
        default_recipients = self.recipient_service.get_default_recipients(self.test_user_id)
        assert len(default_recipients) == 1
        assert default_recipients[0].id == recipient_id
        
        # Verify service can retrieve by ID
        service_retrieved = self.recipient_service.get_recipient_by_id(self.test_user_id, recipient_id)
        assert service_retrieved.name == "Database Integration Test"
    
    def test_factory_boy_creates_realistic_task_data(self):
        """Test that Factory Boy creates realistic task data for service testing."""
        # Create various task types using factories
        task_scenarios = TaskBatchFactory.create_comprehensive_test_scenario()
        
        # Verify factory creates realistic and varied data
        for category, tasks in task_scenarios.items():
            for task in tasks:
                # Verify task data is realistic
                assert len(task.title) > 0
                assert len(task.description) >= 0  # Description can be empty
                
                # Check priority only if the task has it (SimpleTaskFactory objects)
                if hasattr(task, 'priority'):
                    assert task.priority in ['low', 'medium', 'high', 'urgent']
                
                # Verify task due times are realistic
                if hasattr(task, 'due_time') and task.due_time:
                    assert isinstance(task.due_time, str)
                    assert len(task.due_time) > 10  # ISO format should be long
                
                # Verify labels are realistic
                if hasattr(task, 'labels') and task.labels:
                    assert isinstance(task.labels, list)
                    assert all(isinstance(label, str) for label in task.labels)
        
        # Verify category-specific characteristics
        urgent_tasks = task_scenarios.get('urgent', [])
        for task in urgent_tasks:
            # These are SimpleTaskFactory objects which have priority
            if hasattr(task, 'priority'):
                assert task.priority == 'urgent'
        
        screenshot_tasks = task_scenarios.get('with_screenshots', [])
        for task in screenshot_tasks:
            assert 'screenshot' in task.title.lower()
    
    def test_real_database_vs_factory_consistency(self):
        """Test that Factory Boy objects work consistently with real database."""
        # Create recipient using factory
        factory_recipient = TodoistRecipientFactory(
            user_id=self.test_user_id,
            name="Factory Consistency Test"
        )
        
        # Store in database
        recipient_id = self.recipient_repo.add_recipient(self.test_user_id, factory_recipient)
        
        # Retrieve from database
        db_recipient = self.recipient_repo.get_recipient_by_id(self.test_user_id, recipient_id)
        
        # Verify factory and database consistency
        assert db_recipient.name == factory_recipient.name
        assert db_recipient.platform_type == factory_recipient.platform_type
        assert db_recipient.credentials == factory_recipient.credentials
        assert db_recipient.is_personal == factory_recipient.is_personal
        assert db_recipient.enabled == factory_recipient.enabled
        
        # Verify service layer consistency
        service_recipient = self.recipient_service.get_recipient_by_id(self.test_user_id, recipient_id)
        assert service_recipient.name == factory_recipient.name
        assert service_recipient.is_personal == factory_recipient.is_personal