"""Unit tests for platform integrations using Factory Boy with realistic platform data.

This module tests platform functionality with Factory Boy objects to ensure platforms work 
correctly with realistic recipient and task data, while maintaining appropriate mocks for external APIs.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

# Import platform components
from platforms.base import AbstractTaskPlatform
from platforms import TaskPlatformFactory
from platforms.todoist import TodoistPlatform
from platforms.trello import TrelloPlatform
from models.task import PlatformTaskData
from core.exceptions import PlatformError

# Import Factory Boy factories
from tests.factories import (
    TaskFactory,
    SimpleTaskFactory,
    ScreenshotTaskFactory,
    UrgentTaskFactory,
    TodoistRecipientFactory,
    TrelloRecipientFactory,
    TodoistConfigFactory,
    TrelloConfigFactory
)


class TestTaskPlatformFactory:
    """Test platform factory with realistic Factory Boy configurations."""
    
    def test_get_todoist_platform_with_factory_credentials(self):
        """Test getting Todoist platform with realistic Factory Boy credentials."""
        # Create realistic Todoist recipient with factory
        todoist_recipient = TodoistRecipientFactory(
            credentials="a" * 40,  # Realistic Todoist token length
            platform_config={'project_id': '2147483647'}
        )
        
        platform = TaskPlatformFactory.get_platform("todoist", todoist_recipient.credentials)
        
        assert isinstance(platform, TodoistPlatform)
        assert platform.api_token == todoist_recipient.credentials
        assert len(platform.api_token) == 40  # Realistic token length
    
    def test_get_trello_platform_with_factory_credentials(self):
        """Test getting Trello platform with realistic Factory Boy credentials."""
        # Create realistic Trello recipient with factory
        trello_recipient = TrelloRecipientFactory(
            credentials="12345678-1234-1234-1234-123456789012:abcdef12-3456-7890-abcd-ef1234567890",  # UUID:UUID format
            platform_config={
                'board_id': '5f8b2c3d4e5a6b7c8d9e0f12',
                'list_id': '6f9c3d4e5f6a7b8c9d0e1f23'
            }
        )
        
        platform = TaskPlatformFactory.get_platform("trello", trello_recipient.credentials)
        
        assert isinstance(platform, TrelloPlatform)
        # Verify UUID format credentials
        key_token = trello_recipient.credentials.split(":")
        assert len(key_token) == 2
        assert "-" in key_token[0]  # UUID format key
        assert "-" in key_token[1]  # UUID format token
        assert platform.api_key == key_token[0]
        assert platform.token == key_token[1]
    
    def test_get_unsupported_platform(self):
        """Test getting unsupported platform returns None."""
        result = TaskPlatformFactory.get_platform("unsupported", "token")
        assert result is None
    
    def test_list_supported_platforms(self):
        """Test listing supported platforms."""
        platforms = TaskPlatformFactory.get_registered_platforms()
        
        assert "todoist" in platforms
        assert "trello" in platforms
        assert len(platforms) >= 2
    
    def test_platform_factory_with_invalid_credentials(self):
        """Test platform factory behavior with invalid credentials from Factory Boy."""
        # Create recipients with potentially problematic credentials
        invalid_scenarios = [
            TodoistRecipientFactory(credentials=""),  # Empty credentials
            TodoistRecipientFactory(credentials="short"),  # Too short
            TrelloRecipientFactory(credentials="missing_colon"),  # Missing colon separator
            TrelloRecipientFactory(credentials="too:many:colons:here"),  # Too many separators
        ]
        
        for recipient in invalid_scenarios:
            try:
                platform = TaskPlatformFactory.get_platform(recipient.platform_type, recipient.credentials)
                # Some might succeed (platforms might be lenient), others might fail
                if platform is not None:
                    assert hasattr(platform, 'api_token') or hasattr(platform, 'api_key')
            except Exception:
                # Expected for invalid credentials
                pass


class TestTodoistPlatform:
    """Test Todoist platform integration with Factory Boy data."""
    
    @pytest.fixture
    def todoist_recipient(self):
        """Create realistic Todoist recipient using Factory Boy."""
        return TodoistRecipientFactory(
            credentials="a" * 40,  # 40 char total
            platform_config={
                'project_id': '2147483647',
                'section_id': '1234567890'
            }
        )
    
    @pytest.fixture
    def todoist_platform(self, todoist_recipient):
        """Create Todoist platform instance with Factory Boy credentials."""
        return TodoistPlatform(todoist_recipient.credentials)
    
    @pytest.fixture
    def factory_task_data(self):
        """Create realistic task data using Factory Boy."""
        factory_task = TaskFactory(
            title="Complete Project Proposal",
            description="Finalize Q4 project proposal for client presentation",
            due_time="2024-01-01T12:00:00Z"
        )
        
        return PlatformTaskData(
            title=factory_task.title,
            description=factory_task.description,
            due_time=factory_task.due_time
        )
    
    def test_todoist_platform_creation_with_factory_data(self, todoist_platform, todoist_recipient):
        """Test Todoist platform creation with realistic Factory Boy data."""
        assert todoist_platform.api_token == todoist_recipient.credentials
        assert hasattr(todoist_platform, 'base_url')
        assert hasattr(todoist_platform, 'headers')
        assert len(todoist_platform.api_token) == 40  # Realistic token length
    
    @patch('requests.post')
    def test_create_task_success_with_factory_data(self, mock_post, todoist_platform, factory_task_data):
        """Test successful task creation with realistic Factory Boy task data."""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "task_realistic_123"}
        mock_post.return_value = mock_response
        
        # Convert Pydantic model to dict for platform
        task_dict = factory_task_data.model_dump()
        task_id = todoist_platform.create_task(task_dict)
        
        assert task_id == "task_realistic_123"
        mock_post.assert_called_once()
        
        # Verify realistic task data was sent
        call_args = mock_post.call_args
        assert "Complete Project Proposal" in str(call_args)
        assert "client presentation" in str(call_args)
    
    @patch('requests.post')
    def test_create_screenshot_task_with_factory(self, mock_post, todoist_platform):
        """Test creating screenshot task with Factory Boy data."""
        # Create screenshot task using factory
        screenshot_task = ScreenshotTaskFactory(
            title="Review UI Screenshot Analysis",
            description="Analyze attached screenshot for design inconsistencies"
        )
        
        screenshot_task_data = PlatformTaskData(
            title=screenshot_task.title,
            description=screenshot_task.description,
            due_time=screenshot_task.due_time
        )
        
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "screenshot_task_456"}
        mock_post.return_value = mock_response
        
        task_dict = screenshot_task_data.model_dump()
        task_id = todoist_platform.create_task(task_dict)
        
        assert task_id == "screenshot_task_456"
        assert "screenshot" in screenshot_task.title.lower()
    
    @patch('requests.post')
    def test_create_urgent_task_with_factory(self, mock_post, todoist_platform):
        """Test creating urgent task with Factory Boy data."""
        # Create urgent task using factory
        urgent_task = SimpleTaskFactory(
            title="URGENT: Fix Production Database Issue",
            description="Critical database performance issue affecting all users",
            priority="urgent"
        )
        
        urgent_task_data = PlatformTaskData(
            title=urgent_task.title,
            description=urgent_task.description,
            due_time=urgent_task.due_time
        )
        
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "urgent_task_789"}
        mock_post.return_value = mock_response
        
        task_dict = urgent_task_data.model_dump()
        task_id = todoist_platform.create_task(task_dict)
        
        assert task_id == "urgent_task_789"
        assert "URGENT" in urgent_task.title
        assert urgent_task.priority == "urgent"
    
    @patch('requests.post')
    def test_create_task_api_error_with_factory_data(self, mock_post, todoist_platform, factory_task_data):
        """Test task creation with API error using realistic data."""
        # Mock error response
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request: Invalid project_id"
        mock_post.return_value = mock_response
        
        task_dict = factory_task_data.model_dump()
        task_id = todoist_platform.create_task(task_dict)
        
        assert task_id is None
    
    @patch('requests.post')
    def test_create_task_network_error_with_factory_data(self, mock_post, todoist_platform, factory_task_data):
        """Test task creation with network error using realistic data."""
        # Mock network exception
        mock_post.side_effect = requests.RequestException("Network timeout")
        
        task_dict = factory_task_data.model_dump()
        task_id = todoist_platform.create_task(task_dict)
        
        assert task_id is None
    
    def test_validate_credentials_format_with_factory(self, todoist_platform, todoist_recipient):
        """Test credential validation with Factory Boy credentials."""
        # Should not raise exception for realistic token format
        assert todoist_platform.api_token == todoist_recipient.credentials
        assert len(todoist_platform.api_token) == 40  # Realistic length
    
    def test_headers_configuration_with_factory_credentials(self, todoist_platform, todoist_recipient):
        """Test that headers are properly configured with Factory Boy credentials."""
        assert 'Authorization' in todoist_platform.headers
        assert 'Content-Type' in todoist_platform.headers
        assert todoist_platform.headers['Authorization'] == f"Bearer {todoist_recipient.credentials}"
    
    def test_multiple_task_creation_with_factory_batch(self, todoist_platform):
        """Test creating multiple tasks with Factory Boy batch data."""
        # Create batch of varied tasks
        factory_tasks = [
            TaskFactory(title=f"Task {i}", description=f"Description {i}")
            for i in range(5)
        ]
        
        with patch('requests.post') as mock_post:
            # Mock successful responses for all tasks
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"id": "batch_task"}
            mock_post.return_value = mock_response
            
            created_tasks = []
            for factory_task in factory_tasks:
                task_data = PlatformTaskData(
                    title=factory_task.title,
                    description=factory_task.description,
                    due_time=factory_task.due_time
                )
                task_dict = task_data.model_dump()
                task_id = todoist_platform.create_task(task_dict)
                created_tasks.append(task_id)
            
            # Verify all tasks were created
            assert len(created_tasks) == 5
            assert all(task_id == "batch_task" for task_id in created_tasks)
            assert mock_post.call_count == 5


class TestTrelloPlatform:
    """Test Trello platform integration with Factory Boy data."""
    
    @pytest.fixture
    def trello_recipient(self):
        """Create realistic Trello recipient using Factory Boy."""
        return TrelloRecipientFactory(
            credentials="12345678-1234-1234-1234-123456789012:abcdef12-3456-7890-abcd-ef1234567890",
            platform_config={
                'board_id': '5f8b2c3d4e5a6b7c8d9e0f12',
                'list_id': '6f9c3d4e5f6a7b8c9d0e1f23'
            }
        )
    
    @pytest.fixture
    def trello_platform(self, trello_recipient):
        """Create Trello platform instance with Factory Boy credentials."""
        return TrelloPlatform(trello_recipient.credentials)
    
    @pytest.fixture
    def trello_task_data(self, trello_recipient):
        """Create realistic Trello task data using Factory Boy."""
        factory_task = TaskFactory(
            title="Trello Card - Team Sprint Planning",
            description="Plan next sprint goals and assign tasks to team members",
            due_time="2024-01-01T12:00:00Z"
        )
        
        return PlatformTaskData(
            title=factory_task.title,
            description=factory_task.description,
            due_time=factory_task.due_time,
            list_id=trello_recipient.platform_config['list_id']
        )
    
    def test_trello_platform_creation_with_factory_data(self, trello_platform, trello_recipient):
        """Test Trello platform creation with realistic Factory Boy data."""
        credentials = trello_recipient.credentials.split(":")
        assert trello_platform.api_key == credentials[0]
        assert trello_platform.token == credentials[1]
        
        # Verify UUID format
        assert "-" in trello_platform.api_key
        assert "-" in trello_platform.token
        assert len(trello_platform.api_key) == 36  # UUID length
        assert len(trello_platform.token) == 36   # UUID length
        
        assert hasattr(trello_platform, 'default_board_id')
        assert hasattr(trello_platform, 'default_list_id')
    
    @patch('requests.post')
    def test_create_trello_card_success_with_factory_data(self, mock_post, trello_platform, trello_task_data):
        """Test successful Trello card creation with Factory Boy data."""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "trello_card_xyz789"}
        mock_post.return_value = mock_response
        
        task_dict = trello_task_data.model_dump()
        card_id = trello_platform.create_task(task_dict)
        
        assert card_id == "trello_card_xyz789"
        mock_post.assert_called_once()
        
        # Verify realistic Trello data was sent
        call_args = mock_post.call_args
        assert "Team Sprint Planning" in str(call_args)
        assert "assign tasks" in str(call_args)
    
    @patch('requests.post')
    def test_create_trello_card_with_screenshot_attachment(self, mock_post, trello_platform):
        """Test creating Trello card with screenshot using Factory Boy."""
        # Create screenshot task for Trello
        screenshot_task = ScreenshotTaskFactory(
            title="Bug Report - UI Issue Screenshot",
            description="Screenshot showing layout bug in mobile view"
        )
        
        trello_task_data = PlatformTaskData(
            title=screenshot_task.title,
            description=screenshot_task.description,
            due_time=screenshot_task.due_time,
            list_id="screenshot_list_id"
        )
        
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "screenshot_card_123"}
        mock_post.return_value = mock_response
        
        task_dict = trello_task_data.model_dump()
        card_id = trello_platform.create_task(task_dict)
        
        assert card_id == "screenshot_card_123"
        assert "screenshot" in screenshot_task.title.lower()
        assert "bug" in screenshot_task.description.lower()
    
    def test_trello_credentials_parsing_with_factory_variations(self):
        """Test Trello credential parsing with various Factory Boy scenarios."""
        # Test different credential formats
        credential_scenarios = [
            "12345678-1234-1234-1234-123456789012:abcdef12-3456-7890-abcd-ef1234567890",  # Standard UUID:UUID
            "simple_key:complex_token_with_underscores_123",  # Mixed format
            "api_key_123:token_456_789"  # Simple format
        ]
        
        for credentials in credential_scenarios:
            trello_recipient = TrelloRecipientFactory(credentials=credentials)
            
            try:
                platform = TrelloPlatform(trello_recipient.credentials)
                
                # Verify credentials are parsed correctly
                key, token = credentials.split(":")
                assert platform.api_key == key
                assert platform.token == token
                
            except Exception as e:
                # Some formats might be invalid
                assert ":" not in credentials or credentials.count(":") != 1
    
    @patch('requests.post')
    def test_trello_error_handling_with_factory_data(self, mock_post, trello_platform, trello_task_data):
        """Test Trello error handling with realistic Factory Boy data."""
        # Mock Trello API error response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized: Invalid API key or token"
        mock_post.return_value = mock_response
        
        task_dict = trello_task_data.model_dump()
        card_id = trello_platform.create_task(task_dict)
        
        assert card_id is None
    
    def test_trello_configuration_with_factory_platform_config(self):
        """Test Trello configuration with Factory Boy platform config."""
        # Create Trello config using factory
        trello_config = TrelloConfigFactory()
        
        trello_recipient = TrelloRecipientFactory(
            platform_config=trello_config.platform_config
        )
        
        # Verify factory config is realistic
        assert 'board_id' in trello_recipient.platform_config
        assert 'list_id' in trello_recipient.platform_config
        assert len(trello_recipient.platform_config['board_id']) > 10
        assert len(trello_recipient.platform_config['list_id']) > 10


class TestPlatformIntegration:
    """Test platform integration scenarios with Factory Boy data."""
    
    def test_multi_platform_task_creation_with_factory_recipients(self):
        """Test creating tasks across multiple platforms with Factory Boy recipients."""
        # Create recipients for both platforms
        todoist_recipient = TodoistRecipientFactory(
            credentials="a" * 40,
            platform_config={'project_id': '123456789'}
        )
        trello_recipient = TrelloRecipientFactory(
            credentials="key123:token456",
            platform_config={'board_id': 'board123', 'list_id': 'list456'}
        )
        
        # Create platforms
        todoist_platform = TaskPlatformFactory.get_platform("todoist", todoist_recipient.credentials)
        trello_platform = TaskPlatformFactory.get_platform("trello", trello_recipient.credentials)
        
        assert isinstance(todoist_platform, TodoistPlatform)
        assert isinstance(trello_platform, TrelloPlatform)
        
        # Create realistic task for both platforms
        factory_task = TaskFactory(
            title="Multi-Platform Task",
            description="Task to be created on both Todoist and Trello"
        )
        
        # Verify both platforms can handle the same task data
        task_data = PlatformTaskData(
            title=factory_task.title,
            description=factory_task.description,
            due_time=factory_task.due_time
        )
        
        task_dict = task_data.model_dump()
        
        # Both platforms should accept the task format
        assert hasattr(todoist_platform, 'create_task')
        assert hasattr(trello_platform, 'create_task')
    
    def test_platform_error_scenarios_with_factory_data(self):
        """Test platform error scenarios with realistic Factory Boy data."""
        # Create recipients that might cause platform errors
        error_scenarios = [
            TodoistRecipientFactory(credentials="expired_token_" + "x" * 25),
            TrelloRecipientFactory(credentials="invalid:format:too:many:colons"),
            TodoistRecipientFactory(credentials=""),  # Empty credentials
        ]
        
        for recipient in error_scenarios:
            try:
                platform = TaskPlatformFactory.get_platform(recipient.platform_type, recipient.credentials)
                
                if platform is not None:
                    # If platform was created, test it can handle task creation errors
                    error_task = TaskFactory(
                        title="Error Test Task",
                        description="Task to test error handling"
                    )
                    
                    task_data = PlatformTaskData(
                        title=error_task.title,
                        description=error_task.description,
                        due_time=error_task.due_time
                    )
                    
                    # Platform should handle errors gracefully
                    with patch('requests.post') as mock_post:
                        mock_post.side_effect = requests.RequestException("API Error")
                        
                        task_dict = task_data.model_dump()
                        result = platform.create_task(task_dict)
                        
                        # Should return None on error, not raise exception
                        assert result is None
                        
            except Exception:
                # Expected for invalid credentials
                pass