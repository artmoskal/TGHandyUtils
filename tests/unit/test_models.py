"""Unit tests for data models."""

import pytest
from pydantic import ValidationError
from datetime import datetime

from models.task import TaskCreate, TaskDB, PlatformTaskData
from models.recipient import (
    UserPlatform, UserPlatformCreate, UserPlatformUpdate,
    SharedRecipient, SharedRecipientCreate, SharedRecipientUpdate,
    Recipient, UserPreferencesV2, UserPreferencesV2Create, UserPreferencesV2Update
)


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


class TestRecipientModels:
    """Test recipient-related models."""
    
    def test_user_platform_model(self):
        """Test UserPlatform model."""
        platform = UserPlatform(
            id=1,
            telegram_user_id=12345,
            platform_type="todoist",
            credentials="token123",
            platform_config=None,
            enabled=True
        )
        
        assert platform.id == 1
        assert platform.telegram_user_id == 12345
        assert platform.platform_type == "todoist"
        assert platform.enabled is True
    
    def test_user_platform_create(self):
        """Test UserPlatformCreate model."""
        create_data = UserPlatformCreate(
            platform_type="todoist",
            credentials="token123",
            enabled=True
        )
        
        assert create_data.platform_type == "todoist"
        assert create_data.credentials == "token123"
        assert create_data.enabled is True
    
    def test_user_platform_update(self):
        """Test UserPlatformUpdate model."""
        update_data = UserPlatformUpdate(
            credentials="new_token",
            enabled=False
        )
        
        assert update_data.credentials == "new_token"
        assert update_data.enabled is False
    
    def test_shared_recipient_model(self):
        """Test SharedRecipient model."""
        recipient = SharedRecipient(
            id=1,
            telegram_user_id=12345,
            name="Team Trello",
            platform_type="trello",
            credentials="key:token",
            platform_config={"board_id": "board123"},
            enabled=True
        )
        
        assert recipient.id == 1
        assert recipient.name == "Team Trello"
        assert recipient.platform_type == "trello"
        assert recipient.platform_config == {"board_id": "board123"}
    
    def test_shared_recipient_create(self):
        """Test SharedRecipientCreate model."""
        create_data = SharedRecipientCreate(
            name="New Team",
            platform_type="trello",
            credentials="key:token",
            enabled=True
        )
        
        assert create_data.name == "New Team"
        assert create_data.platform_type == "trello"
    
    def test_recipient_unified_model(self):
        """Test unified Recipient model."""
        recipient = Recipient(
            id="platform_1",
            name="My Todoist",
            platform_type="todoist",
            type="user_platform",
            enabled=True
        )
        
        assert recipient.id == "platform_1"
        assert recipient.name == "My Todoist"
        assert recipient.type == "user_platform"
    
    def test_user_preferences_model(self):
        """Test UserPreferencesV2 model."""
        prefs = UserPreferencesV2(
            telegram_user_id=12345,
            default_recipients=["platform_1", "shared_1"],
            show_recipient_ui=True
        )
        
        assert prefs.telegram_user_id == 12345
        assert len(prefs.default_recipients) == 2
        assert prefs.show_recipient_ui is True
    
    def test_user_preferences_create(self):
        """Test UserPreferencesV2Create model."""
        create_data = UserPreferencesV2Create(
            default_recipients=["platform_1"],
            show_recipient_ui=False
        )
        
        assert len(create_data.default_recipients) == 1
        assert create_data.show_recipient_ui is False
    
    def test_user_preferences_update(self):
        """Test UserPreferencesV2Update model."""
        update_data = UserPreferencesV2Update(
            default_recipients=["shared_1", "platform_2"],
            show_recipient_ui=True
        )
        
        assert len(update_data.default_recipients) == 2
        assert update_data.show_recipient_ui is True
    
    def test_recipient_model_creation(self):
        """Test Recipient model can be created with valid data."""
        recipient = Recipient(
            id="platform_1",
            name="Test Platform",
            platform_type="todoist",
            type="user_platform",
            enabled=True
        )
        
        assert recipient.id == "platform_1"
        assert recipient.name == "Test Platform"
        assert recipient.type == "user_platform"