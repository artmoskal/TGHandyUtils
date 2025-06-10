"""Unit tests for task service using dependency injection."""

import pytest
from unittest.mock import Mock, patch, AsyncMock

from services.task_service import TaskService
from models.task import TaskCreate
from models.user import UserCreate
from core.exceptions import TaskCreationError, ValidationError


class TestTaskService:
    """Test cases for TaskService with dependency injection."""
    
    @pytest.fixture
    def task_service(self, mock_task_repository, mock_user_repository):
        """Create a task service instance with mocked dependencies."""
        return TaskService(task_repo=mock_task_repository, user_repo=mock_user_repository)
    
    def test_initialization(self, mock_task_repository, mock_user_repository):
        """Test task service initializes correctly with dependencies."""
        service = TaskService(task_repo=mock_task_repository, user_repo=mock_user_repository)
        assert service.task_repo is not None
        assert service.user_repo is not None
    
    def test_save_user_platform_success(self, task_service):
        """Test saving platform info for user."""
        user_id = 12345
        api_token = "test_token"
        platform_type = "todoist"
        owner_name = "Test User"
        location = "Portugal"
        
        result = task_service.save_user_platform(
            user_id, api_token, platform_type, owner_name, location
        )
        
        assert result is True
        
        # Verify user was saved correctly
        saved_user = task_service.user_repo.get_platform_info(user_id)
        assert saved_user['platform_token'] == api_token
        assert saved_user['platform_type'] == platform_type
        assert saved_user['owner_name'] == owner_name
        assert saved_user['location'] == location
    
    def test_save_user_platform_with_trello_settings(self, task_service):
        """Test saving Trello platform with board/list settings."""
        user_id = 12345
        platform_settings = {'board_id': 'board123', 'list_id': 'list456'}
        
        result = task_service.save_user_platform(
            user_id, "trello_token", "trello", "Test User", "Portugal", platform_settings
        )
        
        assert result is True
        
        # Check that platform_settings were saved correctly
        saved_user = task_service.user_repo.get_platform_info(user_id)
        assert saved_user['platform_settings'] == platform_settings
    
    def test_get_user_platform_info(self, task_service):
        """Test retrieving user platform information."""
        user_id = 12345
        
        # First save a user
        task_service.save_user_platform(
            user_id, "test_token", "todoist", "Test User", "Portugal"
        )
        
        result = task_service.get_user_platform_info(user_id)
        
        assert result is not None
        assert result['platform_token'] == "test_token"
        assert result['platform_type'] == "todoist"
        assert result['owner_name'] == "Test User"
        assert result['location'] == "Portugal"
    
    def test_get_platform_token(self, task_service):
        """Test retrieving user platform token."""
        user_id = 12345
        expected_token = "test_token_123"
        
        # Save user first
        task_service.save_user_platform(
            user_id, expected_token, "todoist", "Test User", "Portugal"
        )
        
        result = task_service.get_platform_token(user_id)
        
        assert result == expected_token
    
    def test_get_platform_type(self, task_service):
        """Test retrieving user platform type."""
        user_id = 12345
        expected_type = "trello"
        
        # Save user first
        task_service.save_user_platform(
            user_id, "token", expected_type, "Test User", "Portugal"
        )
        
        result = task_service.get_platform_type(user_id)
        
        assert result == expected_type
    
    @pytest.mark.asyncio
    async def test_create_task_success(self, task_service, sample_task_data):
        """Test successful task creation."""
        user_id = 12345
        chat_id = 67890
        message_id = 111
        
        # Setup user first
        task_service.save_user_platform(
            user_id, "test_token", "todoist", "Test User", "Portugal"
        )
        
        with patch.object(task_service, '_create_platform_task', return_value=("platform_task_123", None)):
            result = await task_service.create_task(
                user_id, chat_id, message_id, sample_task_data
            )
        
        assert result is True
        
        # Verify task was created in repository
        tasks = task_service.task_repo.get_by_user(user_id)
        assert len(tasks) == 1
        assert tasks[0]['title'] == sample_task_data.title
        assert tasks[0]['platform_task_id'] == "platform_task_123"
    
    @pytest.mark.asyncio
    async def test_create_task_no_user_info(self, task_service, sample_task_data):
        """Test task creation fails with no user info."""
        user_id = 12345
        
        with pytest.raises(TaskCreationError, match="User platform not configured"):
            await task_service.create_task(user_id, 67890, 111, sample_task_data)
    
    @pytest.mark.asyncio
    async def test_create_task_platform_failure(self, task_service, sample_task_data):
        """Test task creation with platform failure."""
        user_id = 12345
        
        # Setup user first
        task_service.save_user_platform(
            user_id, "test_token", "todoist", "Test User", "Portugal"
        )
        
        # Mock platform task creation failure
        with patch.object(task_service, '_create_platform_task', return_value=(None, "Platform error")):
            result = await task_service.create_task(
                user_id, 67890, 111, sample_task_data
            )
        
        # Should return False (platform failed but local succeeded)
        assert result is False
        
        # But task should still be saved locally
        tasks = task_service.task_repo.get_by_user(user_id)
        assert len(tasks) == 1
    
    def test_create_platform_task_todoist(self, task_service):
        """Test creating task on Todoist platform."""
        user_info = {
            'platform_type': 'todoist',
            'platform_token': 'todoist_token',
            'platform_settings': None
        }
        
        task_data = TaskCreate(
            title="Test Task",
            description="Test Description",
            due_time="2025-06-12T11:00:00Z"
        )
        
        with patch('services.task_service.TaskPlatformFactory') as mock_factory:
            mock_platform = Mock()
            mock_platform.create_task.return_value = "task_123"
            mock_factory.get_platform.return_value = mock_platform
            
            task_id, error = task_service._create_platform_task(task_data, user_info)
        
        assert task_id == "task_123"
        assert error is None
        mock_factory.get_platform.assert_called_once_with('todoist', 'todoist_token')
        mock_platform.create_task.assert_called_once()
    
    def test_create_platform_task_no_token(self, task_service):
        """Test platform task creation with no token."""
        user_info = {
            'platform_type': 'todoist',
            'platform_token': None,
            'platform_settings': None
        }
        
        task_data = TaskCreate(
            title="Test Task",
            description="Test Description",
            due_time="2025-06-12T11:00:00Z"
        )
        
        task_id, error = task_service._create_platform_task(task_data, user_info)
        
        assert task_id is None
        assert "Platform token not found" in error
    
    def test_delete_user_data(self, task_service):
        """Test deleting all user data."""
        user_id = 12345
        
        # First create some user data
        task_service.save_user_platform(
            user_id, "token", "todoist", "Test User", "Portugal"
        )
        
        result = task_service.delete_user_data(user_id)
        
        assert result is True
        
        # Verify user was deleted
        user_info = task_service.get_user_platform_info(user_id)
        assert user_info is None
    
    def test_save_user_platform_validation_error(self, task_service):
        """Test validation error in save_user_platform."""
        # Test with invalid platform type (this should be caught by Pydantic validation)
        with pytest.raises(ValidationError):
            task_service.save_user_platform(
                12345, "token", "invalid_platform", "User", "Location"
            )
    
    @pytest.mark.parametrize("platform_type", ['todoist', 'trello'])
    def test_platform_types_supported(self, task_service, platform_type):
        """Test that both platform types are supported."""
        result = task_service.save_user_platform(
            12345, "test_token", platform_type, "Test User", "Location"
        )
        
        assert result is True
        
        # Verify the platform type was saved correctly
        user_info = task_service.get_user_platform_info(12345)
        assert user_info['platform_type'] == platform_type