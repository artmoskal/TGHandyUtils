"""Unit tests for data models using Factory Boy with comprehensive validation testing.

This module tests Pydantic models with Factory Boy objects to ensure models work correctly
with realistic data and proper validation rules are enforced.
"""

import pytest
from pydantic import ValidationError
from datetime import datetime

# Import models
from models.task import TaskCreate, TaskDB, PlatformTaskData
from models.unified_recipient import UnifiedRecipient, UnifiedRecipientCreate, UnifiedUserPreferences

# Import Factory Boy factories
from tests.factories import (
    TaskFactory,
    SimpleTaskFactory,
    ScreenshotTaskFactory,
    UrgentTaskFactory,
    UnifiedRecipientFactory,
    TodoistRecipientFactory,
    TrelloRecipientFactory,
    PersonalRecipientFactory,
    SharedRecipientFactory,
    TelegramUserFactory,
    PreferencesFactory
)


class TestTaskModels:
    """Test task-related models with Factory Boy integration."""
    
    def test_task_create_valid_with_factory_data(self):
        """Test TaskCreate with valid Factory Boy generated data."""
        # Create realistic task using factory
        factory_task = TaskFactory(
            title="Client Meeting Preparation",
            description="Prepare presentation slides for quarterly review",
            due_time="2024-01-01T12:00:00Z"
        )
        
        # Create TaskCreate model using factory data
        task = TaskCreate(
            title=factory_task.title,
            description=factory_task.description,
            due_time=factory_task.due_time
        )
        
        assert task.title == "Client Meeting Preparation"
        assert task.description == "Prepare presentation slides for quarterly review"
        assert task.due_time == "2024-01-01T12:00:00Z"
        
        # Verify factory data is realistic
        assert len(factory_task.title) > 0
        assert len(factory_task.description) > 0
    
    def test_task_create_with_screenshot_task_factory(self):
        """Test TaskCreate with screenshot task from Factory Boy."""
        # Create screenshot task using factory
        screenshot_task = ScreenshotTaskFactory(
            title="Review UI Screenshot Analysis",
            description="Analyze attached screenshot for design inconsistencies"
        )
        
        task = TaskCreate(
            title=screenshot_task.title,
            description=screenshot_task.description,
            due_time=screenshot_task.due_time
        )
        
        assert "screenshot" in task.title.lower()
        assert "analyze" in task.description.lower()
        assert len(task.title) > 0
        assert len(task.description) > 0
    
    def test_task_create_with_urgent_task_factory(self):
        """Test TaskCreate with urgent task from Factory Boy."""
        # Create urgent task using factory
        urgent_task = SimpleTaskFactory(
            title="URGENT: Fix Production Database Issue",
            description="Critical database performance issue affecting all users",
            priority="urgent"
        )
        
        task = TaskCreate(
            title=urgent_task.title,
            description=urgent_task.description,
            due_time=urgent_task.due_time
        )
        
        assert "URGENT" in task.title
        assert "critical" in task.description.lower()
    
    def test_task_create_empty_title_validation(self):
        """Test TaskCreate validates empty title."""
        with pytest.raises(ValidationError):
            TaskCreate(
                title="",
                description="Test description",
                due_time="2024-01-01T12:00:00Z"
            )
    
    def test_task_create_empty_description_validation_with_factory(self):
        """Test TaskCreate allows empty descriptions with realistic factory data."""
        # Create factory task for reference
        factory_task = TaskFactory(title="Task Without Description")
        
        # Empty description should be allowed
        task = TaskCreate(
            title=factory_task.title,
            description="",
            due_time="2024-01-01T12:00:00Z"
        )
        assert task.description == ""
        
        # None description should be converted to empty string
        task_none = TaskCreate(
            title=factory_task.title,
            description=None,
            due_time="2024-01-01T12:00:00Z"
        )
        assert task_none.description == ""
    
    def test_task_db_model_with_factory_data(self):
        """Test TaskDB model with realistic Factory Boy data."""
        # Create comprehensive task using factory
        factory_task = SimpleTaskFactory(
            id=1,
            user_id=12345,
            chat_id=67890,
            message_id=111,
            title="Database Integration Test Task",
            description="Testing TaskDB model with realistic data",
            due_time="2024-01-01T12:00:00Z",
            platform_task_id="task_db_test_123",
            platform_type="todoist"
        )
        
        # Create TaskDB model using factory data
        task = TaskDB(
            id=factory_task.id,
            user_id=factory_task.user_id,
            chat_id=factory_task.chat_id,
            message_id=factory_task.message_id,
            title=factory_task.title,
            description=factory_task.description,
            due_time=factory_task.due_time,
            platform_task_id=factory_task.platform_task_id,
            platform_type=factory_task.platform_type
        )
        
        assert task.id == 1
        assert task.user_id == 12345
        assert task.title == "Database Integration Test Task"
        assert task.platform_type == "todoist"
        
        # Verify factory data consistency
        assert task.title == factory_task.title
        assert task.description == factory_task.description
        assert task.platform_task_id == factory_task.platform_task_id
    
    def test_platform_task_data_with_factory_variations(self):
        """Test PlatformTaskData model with various Factory Boy scenarios."""
        # Test with different task types
        task_scenarios = [
            TaskFactory(title="Platform Integration Test", description="Testing platform data"),
            ScreenshotTaskFactory(title="Screenshot Platform Task", description="Screenshot platform integration"),
            SimpleTaskFactory(title="URGENT Platform Task", description="Critical platform issue", priority="urgent")
        ]
        
        for factory_task in task_scenarios:
            data = PlatformTaskData(
                title=factory_task.title,
                description=factory_task.description,
                due_time=factory_task.due_time
            )
            
            assert data.title == factory_task.title
            assert data.description == factory_task.description
            assert data.due_time == factory_task.due_time
            
            # Verify factory data is appropriate for platform integration
            assert len(data.title) > 0
            assert isinstance(data.due_time, str)
    
    def test_task_model_validation_with_realistic_edge_cases(self):
        """Test task model validation with realistic edge cases from Factory Boy."""
        # Test very long titles
        long_task = TaskFactory(
            title="Very " * 50 + "Long Task Title That Exceeds Normal Limits",
            description="Testing edge case with extremely long title"
        )
        
        # Should still be valid as there's no length limit in the model
        task = TaskCreate(
            title=long_task.title,
            description=long_task.description,
            due_time="2024-01-01T12:00:00Z"
        )
        assert len(task.title) > 200
        
        # Test special characters in titles
        special_task = TaskFactory(
            title="Task with émojis 🚀 and spëcial chars & symbols!",
            description="Testing unicode and special character handling"
        )
        
        task_special = TaskCreate(
            title=special_task.title,
            description=special_task.description,
            due_time="2024-01-01T12:00:00Z"
        )
        assert "émojis" in task_special.title
        assert "🚀" in task_special.title


class TestUnifiedRecipientModels:
    """Test unified recipient models with Factory Boy integration."""
    
    def test_unified_recipient_model_with_factory_data(self):
        """Test UnifiedRecipient model with realistic Factory Boy data."""
        # Create comprehensive recipient using factory
        factory_recipient = TodoistRecipientFactory(
            id=1,
            user_id=12345,
            name="My Personal Todoist - Work Projects",
            platform_type="todoist",
            credentials="a" * 40,  # Realistic Todoist token length
            platform_config={'project_id': '2147483647', 'section_id': '1234567890'},
            is_personal=True,
            enabled=True
        )
        
        # Create model using factory data
        recipient = UnifiedRecipient(
            id=factory_recipient.id,
            user_id=factory_recipient.user_id,
            name=factory_recipient.name,
            platform_type=factory_recipient.platform_type,
            credentials=factory_recipient.credentials,
            platform_config=factory_recipient.platform_config,
            is_personal=factory_recipient.is_personal,
            enabled=factory_recipient.enabled
        )
        
        assert recipient.id == 1
        assert recipient.user_id == 12345
        assert recipient.name == "My Personal Todoist - Work Projects"
        assert recipient.platform_type == "todoist"
        assert recipient.is_personal is True
        assert recipient.enabled is True
        assert len(recipient.credentials) == 40  # Realistic length
        assert recipient.platform_config['project_id'] == '2147483647'
    
    def test_unified_recipient_with_trello_factory(self):
        """Test UnifiedRecipient model with Trello factory data."""
        # Create Trello recipient using factory
        trello_recipient = TrelloRecipientFactory(
            name="Team Trello Board - Product Development",
            platform_type="trello",
            credentials="12345678-1234-1234-1234-123456789012",  # UUID format
            platform_config={
                'board_id': '5f8b2c3d4e5a6b7c8d9e0f12',
                'list_id': '6f9c3d4e5f6a7b8c9d0e1f23'
            },
            is_personal=False,
            enabled=True
        )
        
        recipient = UnifiedRecipient(
            id=1,
            user_id=12345,
            name=trello_recipient.name,
            platform_type=trello_recipient.platform_type,
            credentials=trello_recipient.credentials,
            platform_config=trello_recipient.platform_config,
            is_personal=trello_recipient.is_personal,
            enabled=trello_recipient.enabled
        )
        
        assert recipient.platform_type == "trello"
        assert recipient.is_personal is False  # Shared/team board
        assert "-" in recipient.credentials  # UUID format
        assert recipient.platform_config['board_id'] == '5f8b2c3d4e5a6b7c8d9e0f12'
        assert recipient.platform_config['list_id'] == '6f9c3d4e5f6a7b8c9d0e1f23'
    
    def test_unified_recipient_create_with_factory_variations(self):
        """Test UnifiedRecipientCreate model with various Factory Boy scenarios."""
        # Test personal recipient creation
        personal_factory = PersonalRecipientFactory(
            name="My Personal Workspace",
            platform_type="todoist",
            credentials="personal_token_123",
            is_personal=True
        )
        
        create_data = UnifiedRecipientCreate(
            name=personal_factory.name,
            platform_type=personal_factory.platform_type,
            credentials=personal_factory.credentials,
            is_personal=personal_factory.is_personal
        )
        
        assert create_data.name == "My Personal Workspace"
        assert create_data.platform_type == "todoist"
        assert create_data.is_personal is True
        
        # Test shared recipient creation
        shared_factory = SharedRecipientFactory(
            name="Shared Team Account",
            platform_type="trello",
            credentials="shared_token_456",
            is_personal=False
        )
        
        shared_create = UnifiedRecipientCreate(
            name=shared_factory.name,
            platform_type=shared_factory.platform_type,
            credentials=shared_factory.credentials,
            is_personal=shared_factory.is_personal
        )
        
        assert shared_create.is_personal is False
        assert "shared" in shared_create.name.lower()
    
    def test_unified_user_preferences_model_with_factory_user(self):
        """Test UnifiedUserPreferences model with realistic Factory Boy user data."""
        # Create realistic user using factory
        factory_user = TelegramUserFactory(
            id=12345,
            first_name="Maria",
            last_name="Santos",
            username="maria_santos_pt",
            language_code="pt"
        )
        
        # Create preferences using factory
        factory_prefs = PreferencesFactory(
            user_id=factory_user.id,
            show_recipient_ui=True,
            telegram_notifications=True,
            owner_name=f"{factory_user.first_name} {factory_user.last_name}",
            location="Portugal"
        )
        
        prefs = UnifiedUserPreferences(
            user_id=factory_prefs.user_id,
            show_recipient_ui=factory_prefs.show_recipient_ui,
            telegram_notifications=factory_prefs.telegram_notifications,
            owner_name=factory_prefs.owner_name,
            location=factory_prefs.location
        )
        
        assert prefs.user_id == 12345
        assert prefs.show_recipient_ui is True
        assert prefs.telegram_notifications is True
        assert prefs.owner_name == "Maria Santos"
        assert prefs.location == "Portugal"
        
        # Verify factory data matches user characteristics
        assert factory_user.language_code == "pt"
        assert "maria" in factory_user.username.lower()
    
    def test_recipient_model_validation_edge_cases(self):
        """Test recipient model validation with edge cases from Factory Boy."""
        # Test minimum valid recipient
        minimal_recipient = UnifiedRecipientFactory(
            name="A",  # Single character name
            platform_type="todoist",
            credentials="x",  # Minimal credentials
            is_personal=True,
            enabled=True
        )
        
        recipient = UnifiedRecipient(
            id=1,
            user_id=1,
            name=minimal_recipient.name,
            platform_type=minimal_recipient.platform_type,
            credentials=minimal_recipient.credentials,
            platform_config=None,
            is_personal=minimal_recipient.is_personal,
            enabled=minimal_recipient.enabled
        )
        
        assert recipient.name == "A"
        assert recipient.credentials == "x"
        assert recipient.platform_config is None
        
        # Test recipient with complex platform config
        complex_recipient = TrelloRecipientFactory(
            platform_config={
                'board_id': 'complex_board_123',
                'list_id': 'complex_list_456', 
                'member_ids': ['member1', 'member2', 'member3'],
                'labels': ['urgent', 'bug', 'feature'],
                'webhook_url': 'https://example.com/webhook',
                'custom_fields': {'priority': 'high', 'category': 'development'}
            }
        )
        
        complex_model = UnifiedRecipient(
            id=2,
            user_id=2,
            name=complex_recipient.name,
            platform_type=complex_recipient.platform_type,
            credentials=complex_recipient.credentials,
            platform_config=complex_recipient.platform_config,
            is_personal=complex_recipient.is_personal,
            enabled=complex_recipient.enabled
        )
        
        assert len(complex_model.platform_config) > 5
        assert complex_model.platform_config['member_ids'] == ['member1', 'member2', 'member3']
        assert complex_model.platform_config['custom_fields']['priority'] == 'high'
    
    def test_model_consistency_between_factory_and_pydantic(self):
        """Test consistency between Factory Boy objects and Pydantic model validation."""
        # Create batch of recipients using different factories
        factory_recipients = [
            TodoistRecipientFactory(),
            TrelloRecipientFactory(),
            PersonalRecipientFactory(),
            SharedRecipientFactory()
        ]
        
        for factory_recipient in factory_recipients:
            # Test that Factory Boy data is valid for Pydantic models
            try:
                recipient_model = UnifiedRecipient(
                    id=1,
                    user_id=factory_recipient.user_id,
                    name=factory_recipient.name,
                    platform_type=factory_recipient.platform_type,
                    credentials=factory_recipient.credentials,
                    platform_config=factory_recipient.platform_config,
                    is_personal=factory_recipient.is_personal,
                    enabled=factory_recipient.enabled
                )
                
                # Verify model creation succeeded
                assert recipient_model.name == factory_recipient.name
                assert recipient_model.platform_type == factory_recipient.platform_type
                assert recipient_model.is_personal == factory_recipient.is_personal
                
            except ValidationError as e:
                pytest.fail(f"Factory Boy data failed Pydantic validation: {e}")
    
    def test_preferences_model_with_international_users(self):
        """Test preferences model with international users from Factory Boy."""
        # Create international users
        international_users = [
            TelegramUserFactory(first_name="João", last_name="Silva", language_code="pt"),
            TelegramUserFactory(first_name="Pierre", last_name="Dubois", language_code="fr"),
            TelegramUserFactory(first_name="Hans", last_name="Mueller", language_code="de"),
            TelegramUserFactory(first_name="Hiroshi", last_name="Tanaka", language_code="ja")
        ]
        
        locations = ["Portugal", "France", "Germany", "Japan"]
        
        for user, location in zip(international_users, locations):
            prefs = UnifiedUserPreferences(
                user_id=user.id,
                show_recipient_ui=True,
                telegram_notifications=True,
                owner_name=f"{user.first_name} {user.last_name}",
                location=location
            )
            
            # Verify preferences work with international data
            assert len(prefs.owner_name) > 0
            assert prefs.location in ["Portugal", "France", "Germany", "Japan"]
            assert user.language_code in ["pt", "fr", "de", "ja"]
            
            # Verify unicode names work correctly
            if user.language_code == "pt":
                assert "ão" in prefs.owner_name  # Portuguese characters
            elif user.language_code == "fr":
                assert len(prefs.owner_name.split()) == 2  # First and last name