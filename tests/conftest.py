"""Test configuration and fixtures - clean recipient system only."""

import pytest
import tempfile
import sqlite3
import os
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock
from contextlib import contextmanager
from dependency_injector import containers, providers

# Add the project root to Python path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.interfaces import ITaskRepository, IParsingService, IConfig
from core.recipient_interfaces import IRecipientService, IUserPlatformRepository, ISharedRecipientRepository, IUserPreferencesV2Repository
from models.task import TaskCreate
from models.recipient import (
    UserPlatform, UserPlatformCreate, SharedRecipient, SharedRecipientCreate,
    Recipient, UserPreferencesV2
)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


class MockConfig(IConfig):
    """Mock configuration for testing."""
    
    def __init__(self):
        self._telegram_bot_token = "test_bot_token"
        self._openai_api_key = "test_openai_key"
        self._database_path = ":memory:"
        self._database_timeout = 30
        self._default_task_platform = "todoist"
        self._supported_platforms = ["todoist", "trello"]
    
    @property
    def TELEGRAM_BOT_TOKEN(self) -> str:
        return self._telegram_bot_token
    
    @property
    def OPENAI_API_KEY(self) -> str:
        return self._openai_api_key
    
    @property
    def DATABASE_PATH(self) -> str:
        return self._database_path
    
    @property
    def DATABASE_TIMEOUT(self) -> int:
        return self._database_timeout
    
    @property
    def DEFAULT_TASK_PLATFORM(self) -> str:
        return self._default_task_platform
    
    @property
    def SUPPORTED_PLATFORMS(self) -> list:
        return self._supported_platforms


class MockTaskRepository(ITaskRepository):
    """Mock task repository for testing."""
    
    def __init__(self):
        self.tasks = {}
        self.next_id = 1
    
    def create(self, user_id: int, chat_id: int, message_id: int, 
               task_data: TaskCreate, platform_task_id: str = None, 
               platform_type: str = 'todoist') -> int:
        task_id = self.next_id
        self.next_id += 1
        self.tasks[task_id] = {
            'id': task_id,
            'user_id': user_id,
            'chat_id': chat_id,
            'message_id': message_id,
            'title': task_data.title,
            'description': task_data.description,
            'due_time': task_data.due_time,
            'platform_task_id': platform_task_id,
            'platform_type': platform_type
        }
        return task_id
    
    def get_all(self):
        return list(self.tasks.values())
    
    def get_by_user(self, user_id: int):
        return [task for task in self.tasks.values() if task['user_id'] == user_id]
    
    def delete(self, task_id: int) -> bool:
        if task_id in self.tasks:
            del self.tasks[task_id]
            return True
        return False
    
    def update_platform_id(self, task_id: int, platform_task_id: str, platform_type: str) -> bool:
        if task_id in self.tasks:
            self.tasks[task_id]['platform_task_id'] = platform_task_id
            self.tasks[task_id]['platform_type'] = platform_type
            return True
        return False


# Legacy MockUserRepository removed - using recipient system only


class MockParsingService(IParsingService):
    """Mock parsing service for testing."""
    
    def parse_content_to_task(self, content_message: str, owner_name: str = None, 
                             location: str = None):
        return {
            'title': f"Parsed: {content_message[:20]}",
            'description': content_message,
            'due_time': '2025-06-12T11:00:00Z'
        }
    
    def get_timezone_offset(self, location: str) -> int:
        if not location:
            return 0
        location_map = {
            'portugal': 1,
            'cascais': 1,
            'uk': 0,
            'london': 0,
            'new york': -5,
            'california': -8
        }
        return location_map.get(location.lower(), 0)
    
    def convert_utc_to_local_display(self, utc_time_str: str, location: str) -> str:
        return f"Local time display for {utc_time_str} in {location}"


class TestContainer(containers.DeclarativeContainer):
    """Test dependency injection container - clean recipient system only."""
    
    # Configuration
    config = providers.Singleton(MockConfig)
    
    # Repositories  
    task_repository = providers.Factory(MockTaskRepository)
    
    # Services
    parsing_service = providers.Factory(MockParsingService)


@pytest.fixture
def test_container():
    """Test container fixture."""
    container = TestContainer()
    return container


@pytest.fixture
def mock_config():
    """Mock configuration fixture."""
    return MockConfig()


@pytest.fixture
def mock_task_repository():
    """Mock task repository fixture."""
    return MockTaskRepository()


@pytest.fixture
def mock_parsing_service():
    """Mock parsing service fixture."""
    return MockParsingService()


@pytest.fixture
def sample_task_data():
    """Sample task data fixture."""
    return TaskCreate(
        title="Test Task",
        description="Test Description",
        due_time="2025-06-12T11:00:00Z"
    )


@pytest.fixture
def sample_user_platform():
    """Sample user platform for testing."""
    return UserPlatform(
        id=1,
        telegram_user_id=12345,
        platform_type="todoist",
        credentials="token123",
        platform_config=None,
        enabled=True
    )


@pytest.fixture
def sample_shared_recipient():
    """Sample shared recipient for testing."""
    return SharedRecipient(
        id=1,
        telegram_user_id=12345,
        name="Team Trello",
        platform_type="trello",
        credentials="key123:token456",
        platform_config={"board_id": "board123", "list_id": "list456"},
        enabled=True
    )


@pytest.fixture
def sample_recipient():
    """Sample unified recipient for testing."""
    return Recipient(
        id="platform_1",
        name="My Todoist",
        platform_type="todoist",
        type="user_platform",
        enabled=True
    )


@pytest.fixture
def mock_telegram_message():
    """Mock Telegram message fixture."""
    message = Mock()
    message.from_user.id = 12345
    message.from_user.full_name = "Test User"
    message.chat.id = 67890
    message.message_id = 111
    message.text = "Test message"
    message.reply = AsyncMock()
    return message