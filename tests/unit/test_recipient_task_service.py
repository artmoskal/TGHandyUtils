"""Unit tests for recipient task service - clean system only."""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from services.recipient_task_service import RecipientTaskService
from models.task import TaskCreate
from models.recipient import Recipient
from core.exceptions import TaskCreationError, ValidationError


class TestRecipientTaskService:
    """Test cases for RecipientTaskService."""
    
    @pytest.fixture
    def mock_task_repo(self):
        """Mock task repository."""
        mock = Mock()
        mock.create.return_value = 123
        mock.update_platform_id.return_value = True
        return mock
    
    @pytest.fixture
    def mock_recipient_service(self):
        """Mock recipient service."""
        mock = Mock()
        mock.get_default_recipients.return_value = [
            Recipient(
                id="platform_1",
                name="My Todoist",
                platform_type="todoist",
                type="user_platform",
                enabled=True
            )
        ]
        mock.get_recipients_by_ids.return_value = [
            Recipient(
                id="platform_1",
                name="My Todoist",
                platform_type="todoist",
                type="user_platform",
                enabled=True
            )
        ]
        mock.get_recipient_credentials.return_value = "token123"
        mock.get_recipient_config.return_value = None
        return mock
    
    @pytest.fixture
    def task_service(self, mock_task_repo, mock_recipient_service):
        """Create task service with mocked dependencies."""
        return RecipientTaskService(
            task_repo=mock_task_repo,
            recipient_service=mock_recipient_service
        )
    
    @pytest.fixture
    def valid_task_data(self):
        """Valid task data for testing."""
        return TaskCreate(
            title="Test Task",
            description="Test description",
            due_time="2024-01-01T12:00:00Z"
        )
    
    @pytest.mark.asyncio
    async def test_create_task_success(self, task_service, valid_task_data, mock_task_repo, mock_recipient_service):
        """Test successful task creation."""
        with patch('services.recipient_task_service.TaskPlatformFactory') as mock_factory:
            # Mock platform
            mock_platform = Mock()
            mock_platform.create_task.return_value = "task_123"
            mock_factory.get_platform.return_value = mock_platform
            
            success, feedback, actions = await task_service.create_task(
                user_id=12345,
                chat_id=67890,
                message_id=111,
                task_data=valid_task_data
            )
            
            assert success is True
            assert "My Todoist" in feedback
            mock_task_repo.create.assert_called_once()
            mock_platform.create_task.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_task_with_specific_recipients(self, task_service, valid_task_data, mock_recipient_service):
        """Test task creation with specific recipients."""
        with patch('services.recipient_task_service.TaskPlatformFactory') as mock_factory:
            mock_platform = Mock()
            mock_platform.create_task.return_value = "task_123"
            mock_factory.get_platform.return_value = mock_platform
            
            success, feedback, actions = await task_service.create_task(
                user_id=12345,
                chat_id=67890,
                message_id=111,
                task_data=valid_task_data,
                recipient_ids=["platform_1"]
            )
            
            assert success is True
            mock_recipient_service.get_recipients_by_ids.assert_called_once_with(12345, ["platform_1"])
    
    @pytest.mark.asyncio
    async def test_create_task_no_recipients(self, task_service, valid_task_data, mock_recipient_service):
        """Test task creation fails when no recipients."""
        mock_recipient_service.get_default_recipients.return_value = []
        
        with pytest.raises(TaskCreationError, match="No recipients configured"):
            await task_service.create_task(
                user_id=12345,
                chat_id=67890,
                message_id=111,
                task_data=valid_task_data
            )
    
    @pytest.mark.asyncio
    async def test_create_task_platform_failure(self, task_service, valid_task_data):
        """Test task creation when platform fails."""
        with patch('services.recipient_task_service.TaskPlatformFactory') as mock_factory:
            mock_platform = Mock()
            mock_platform.create_task.return_value = None  # Platform failure
            mock_factory.get_platform.return_value = mock_platform
            
            success, feedback, actions = await task_service.create_task(
                user_id=12345,
                chat_id=67890,
                message_id=111,
                task_data=valid_task_data
            )
            
            assert success is False
            assert feedback is None
    
    def test_validate_task_data_valid(self, task_service, valid_task_data):
        """Test task data validation with valid data."""
        # Should not raise exception
        task_service._validate_task_data(valid_task_data)
    
    def test_validate_task_data_empty_title(self, task_service):
        """Test task data validation with empty title."""
        # Since TaskCreate already validates, test that we can't create invalid TaskCreate
        from pydantic import ValidationError as PydanticValidationError
        
        with pytest.raises(PydanticValidationError):
            TaskCreate(
                title="",
                description="Test",
                due_time="2024-01-01T12:00:00Z"
            )
    
    def test_validate_task_data_no_due_time(self, task_service):
        """Test task data validation without due time."""
        task_data = TaskCreate(
            title="Test",
            description="Test",
            due_time=""
        )
        
        with pytest.raises(ValidationError, match="Task due time is required"):
            task_service._validate_task_data(task_data)
    
    def test_validate_task_data_invalid_due_time(self, task_service):
        """Test task data validation with invalid due time format."""
        task_data = TaskCreate(
            title="Test",
            description="Test",
            due_time="invalid_date"
        )
        
        with pytest.raises(ValidationError, match="Invalid due time format"):
            task_service._validate_task_data(task_data)
    
    def test_generate_task_url_todoist(self, task_service):
        """Test URL generation for Todoist."""
        recipient = Recipient(
            id="platform_1",
            name="My Todoist",
            platform_type="todoist",
            type="user_platform",
            enabled=True
        )
        
        url = task_service._generate_task_url("123456", recipient)
        assert url == "https://todoist.com/showTask?id=123456"
    
    def test_generate_task_url_trello(self, task_service):
        """Test URL generation for Trello."""
        recipient = Recipient(
            id="shared_1",
            name="Team Trello",
            platform_type="trello",
            type="shared_recipient",
            enabled=True
        )
        
        url = task_service._generate_task_url("abcd1234", recipient)
        assert url == "https://trello.com/c/abcd1234"
    
    def test_generate_task_url_unknown_platform(self, task_service):
        """Test URL generation for unknown platform."""
        recipient = Recipient(
            id="platform_1",
            name="Unknown Platform",
            platform_type="unknown",
            type="user_platform",
            enabled=True
        )
        
        url = task_service._generate_task_url("123", recipient)
        assert url is None