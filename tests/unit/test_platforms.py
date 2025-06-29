"""Unit tests for platform integrations."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from platforms.base import AbstractTaskPlatform
from platforms import TaskPlatformFactory
from platforms.todoist import TodoistPlatform
from platforms.trello import TrelloPlatform
from models.task import PlatformTaskData
from core.exceptions import PlatformError


class TestTaskPlatformFactory:
    """Test platform factory."""
    
    def test_get_todoist_platform(self):
        """Test getting Todoist platform."""
        platform = TaskPlatformFactory.get_platform("todoist", "token123")
        
        assert isinstance(platform, TodoistPlatform)
        assert platform.api_token == "token123"
    
    def test_get_trello_platform(self):
        """Test getting Trello platform."""
        platform = TaskPlatformFactory.get_platform("trello", "key123:token456")
        
        assert isinstance(platform, TrelloPlatform)
        assert platform.api_key == "key123"
        assert platform.token == "token456"
    
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


class TestTodoistPlatform:
    """Test Todoist platform integration."""
    
    @pytest.fixture
    def todoist_platform(self):
        """Create Todoist platform instance."""
        return TodoistPlatform("test_token")
    
    @pytest.fixture
    def sample_task_data(self):
        """Sample task data for testing."""
        return PlatformTaskData(
            title="Test Task",
            description="Test description",
            due_time="2024-01-01T12:00:00Z"
        )
    
    def test_todoist_platform_creation(self, todoist_platform):
        """Test Todoist platform can be created."""
        assert todoist_platform.api_token == "test_token"
        assert hasattr(todoist_platform, 'base_url')
        assert hasattr(todoist_platform, 'headers')
    
    @patch('requests.post')
    def test_create_task_success(self, mock_post, todoist_platform, sample_task_data):
        """Test successful task creation."""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "task_123"}
        mock_post.return_value = mock_response
        
        # Convert Pydantic model to dict for platform
        task_dict = sample_task_data.model_dump()
        task_id = todoist_platform.create_task(task_dict)
        
        assert task_id == "task_123"
        mock_post.assert_called_once()
    
    @patch('requests.post')
    def test_create_task_api_error(self, mock_post, todoist_platform, sample_task_data):
        """Test task creation with API error."""
        # Mock error response
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_post.return_value = mock_response
        
        # Convert Pydantic model to dict for platform
        task_dict = sample_task_data.model_dump()
        task_id = todoist_platform.create_task(task_dict)
        
        assert task_id is None
    
    @patch('requests.post')
    def test_create_task_network_error(self, mock_post, todoist_platform, sample_task_data):
        """Test task creation with network error."""
        # Mock network exception
        mock_post.side_effect = requests.RequestException("Network error")
        
        # Convert Pydantic model to dict for platform
        task_dict = sample_task_data.model_dump()
        task_id = todoist_platform.create_task(task_dict)
        
        assert task_id is None
    
    def test_validate_credentials_format(self, todoist_platform):
        """Test credential validation."""
        # Should not raise exception for valid token format
        assert todoist_platform.api_token == "test_token"
    
    def test_headers_configuration(self, todoist_platform):
        """Test that headers are properly configured."""
        assert 'Authorization' in todoist_platform.headers
        assert 'Content-Type' in todoist_platform.headers
        assert todoist_platform.headers['Authorization'] == "Bearer test_token"


class TestTrelloPlatform:
    """Test Trello platform integration."""
    
    @pytest.fixture
    def trello_platform(self):
        """Create Trello platform instance."""
        return TrelloPlatform("test_key:test_token")
    
    @pytest.fixture
    def sample_task_data(self):
        """Sample task data for testing."""
        return PlatformTaskData(
            title="Trello Task",
            description="Trello description",
            due_time="2024-01-01T12:00:00Z",
            list_id="list456"
        )
    
    def test_trello_platform_creation(self, trello_platform):
        """Test Trello platform can be created."""
        assert trello_platform.api_key == "test_key"
        assert trello_platform.token == "test_token"
        # Board and list IDs are set via configuration, not initialization
        assert hasattr(trello_platform, 'default_board_id')
        assert hasattr(trello_platform, 'default_list_id')
    
    def test_trello_credentials_parsing(self):
        """Test Trello credentials parsing."""
        platform = TrelloPlatform("key123:token456")
        
        assert platform.api_key == "key123"
        assert platform.token == "token456"
    
    def test_trello_invalid_credentials(self):
        """Test Trello with invalid credentials format."""
        platform = TrelloPlatform("invalid_format")
        # Should create platform but with empty credentials
        assert platform.api_key == ""
        assert platform.token == ""
    
    @patch('requests.post')
    def test_create_card_success(self, mock_post, trello_platform, sample_task_data):
        """Test successful card creation."""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "card_123", "shortUrl": "https://trello.com/c/card_123"}
        mock_post.return_value = mock_response
        
        task_id = trello_platform.create_task(sample_task_data.model_dump())
        
        assert task_id == "card_123"
        mock_post.assert_called_once()
    
    @patch('requests.post')
    def test_create_card_api_error(self, mock_post, trello_platform, sample_task_data):
        """Test card creation with API error."""
        # Mock error response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_post.return_value = mock_response
        
        task_id = trello_platform.create_task(sample_task_data.model_dump())
        
        assert task_id is None
    
    def test_trello_base_url(self, trello_platform):
        """Test Trello base URL configuration."""
        assert trello_platform.base_url == "https://api.trello.com/1"


class TestBasePlatform:
    """Test base platform functionality."""
    
    def test_base_platform_abstract(self):
        """Test that base platform cannot be instantiated directly."""
        # AbstractTaskPlatform is abstract and should not be instantiated
        with pytest.raises(TypeError):
            AbstractTaskPlatform("token")
    
    def test_platform_registry(self):
        """Test platform registry functionality."""
        # Test that platforms are registered
        supported = TaskPlatformFactory.get_registered_platforms()
        assert len(supported) > 0
        assert "todoist" in supported
        assert "trello" in supported