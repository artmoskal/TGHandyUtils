"""Unit tests for data models."""

import pytest
from pydantic import ValidationError
from datetime import datetime

from models.task import TaskCreate, TaskDB, PlatformTaskData
from models.unified_recipient import UnifiedRecipient, UnifiedRecipientCreate, UnifiedUserPreferences


class TestTaskModels:
    """Test task-related models."""
    
    def test_task_create_valid(self):
        """Test TaskCreate with valid data."""
        task = TaskCreate(
            title="Test Task",
            description="Test description",
            due_time="2024-01-01T12:00:00Z"
        )
        
        assert task.title == "Test Task"
        assert task.description == "Test description"
        assert task.due_time == "2024-01-01T12:00:00Z"
    
    def test_task_create_empty_title_validation(self):
        """Test TaskCreate validates empty title."""
        with pytest.raises(ValidationError):
            TaskCreate(
                title="",
                description="Test description",
                due_time="2024-01-01T12:00:00Z"
            )
    
    def test_task_create_empty_description_validation(self):
        """Test TaskCreate validates empty description."""
        with pytest.raises(ValidationError):
            TaskCreate(
                title="Test Task",
                description="",
                due_time="2024-01-01T12:00:00Z"
            )
    
    def test_task_db_model(self):
        """Test TaskDB model."""
        task = TaskDB(
            id=1,
            user_id=12345,
            chat_id=67890,
            message_id=111,
            task_title="Test Task",
            task_description="Test description",
            due_time="2024-01-01T12:00:00Z",
            platform_task_id="task_123",
            platform_type="todoist"
        )
        
        assert task.id == 1
        assert task.user_id == 12345
        assert task.task_title == "Test Task"
    
    def test_platform_task_data(self):
        """Test PlatformTaskData model."""
        data = PlatformTaskData(
            title="Platform Task",
            description="Platform description",
            due_time="2024-01-01T12:00:00Z"
        )
        
        assert data.title == "Platform Task"
        assert data.description == "Platform description"


class TestUnifiedRecipientModels:
    """Test unified recipient models."""
    
    def test_unified_recipient_model(self):
        """Test UnifiedRecipient model."""
        recipient = UnifiedRecipient(
            id=1,
            user_id=12345,
            name="My Todoist",
            platform_type="todoist",
            credentials="token123",
            platform_config=None,
            is_personal=True,
            is_default=True,
            enabled=True
        )
        
        assert recipient.id == 1
        assert recipient.user_id == 12345
        assert recipient.name == "My Todoist"
        assert recipient.platform_type == "todoist"
        assert recipient.is_personal is True
        assert recipient.is_default is True
        assert recipient.enabled is True
    
    def test_unified_recipient_create(self):
        """Test UnifiedRecipientCreate model."""
        create_data = UnifiedRecipientCreate(
            name="New Todoist",
            platform_type="todoist",
            credentials="token123",
            is_personal=True,
            is_default=False
        )
        
        assert create_data.name == "New Todoist"
        assert create_data.platform_type == "todoist"
        assert create_data.credentials == "token123"
        assert create_data.is_personal is True
        assert create_data.is_default is False
    
    def test_unified_user_preferences_model(self):
        """Test UnifiedUserPreferences model."""
        prefs = UnifiedUserPreferences(
            user_id=12345,
            show_recipient_ui=True,
            telegram_notifications=True,
            owner_name="John Doe",
            location="Portugal"
        )
        
        assert prefs.user_id == 12345
        assert prefs.show_recipient_ui is True
        assert prefs.telegram_notifications is True
        assert prefs.owner_name == "John Doe"
        assert prefs.location == "Portugal"