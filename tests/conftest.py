"""Test configuration and fixtures."""

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

from core.interfaces import ITaskRepository, IUserRepository, IParsingService, ITaskService, IConfig
from models.task import TaskCreate
from models.user import UserCreate


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


class MockUserRepository(IUserRepository):
    """Mock user repository for testing."""
    
    def __init__(self):
        self.users = {}
    
    def create_or_update(self, user_data: UserCreate) -> bool:
        self.users[user_data.telegram_user_id] = {
            'telegram_user_id': user_data.telegram_user_id,
            'platform_token': user_data.platform_token,
            'platform_type': user_data.platform_type,
            'owner_name': user_data.owner_name,
            'location': user_data.location,
            'platform_settings': user_data.platform_settings
        }
        return True
    
    def get_by_telegram_id(self, telegram_user_id: int):
        return self.users.get(telegram_user_id)
    
    def get_platform_info(self, telegram_user_id: int):
        user = self.users.get(telegram_user_id)
        if user:
            return {
                'platform_token': user['platform_token'],
                'platform_type': user['platform_type'],
                'owner_name': user['owner_name'],
                'location': user['location'],
                'platform_settings': user['platform_settings']
            }
        return None
    
    def get_platform_token(self, telegram_user_id: int):
        user = self.users.get(telegram_user_id)
        return user['platform_token'] if user else None
    
    def get_platform_type(self, telegram_user_id: int):
        user = self.users.get(telegram_user_id)
        return user['platform_type'] if user else 'todoist'
    
    def delete(self, telegram_user_id: int) -> bool:
        if telegram_user_id in self.users:
            del self.users[telegram_user_id]
            return True
        return False


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
    """Test dependency injection container."""
    
    # Configuration
    config = providers.Singleton(MockConfig)
    
    # Repositories  
    task_repository = providers.Factory(MockTaskRepository)
    user_repository = providers.Factory(MockUserRepository)
    
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
def mock_user_repository():
    """Mock user repository fixture."""
    return MockUserRepository()


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
def sample_user_data():
    """Sample user data fixture."""
    return {
        'telegram_user_id': 12345,
        'platform_token': 'test_token',
        'platform_type': 'todoist',
        'owner_name': 'Test User',
        'location': 'Portugal',
        'platform_settings': None
    }


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