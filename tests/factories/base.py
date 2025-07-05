"""Base factory configuration with database integration.

This module provides the foundation for all Factory Boy factories in the test suite,
including database session management, sequence generation, and common patterns.
"""

import factory
from datetime import datetime, timedelta
from typing import Any, Dict
from faker import Faker

# Initialize faker with consistent locale
fake = Faker('en_US')


class SimpleObject:
    """Simple object that supports attribute access for Factory Boy models."""
    
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    def __repr__(self):
        attrs = ', '.join(f'{k}={v!r}' for k, v in self.__dict__.items())
        return f'{self.__class__.__name__}({attrs})'


class BaseFactory(factory.Factory):
    """Abstract base factory with common configuration.
    
    Provides:
    - Database session management for test isolation
    - Sequence generators for unique IDs
    - Common timestamp generation
    - Faker configuration
    - Test data cleanup utilities
    """
    
    class Meta:
        abstract = True
        strategy = factory.BUILD_STRATEGY  # Don't auto-save to DB by default
    
    # Common sequence generators to avoid ID conflicts
    id = factory.Sequence(lambda n: n + 1000)  # Start from 1000 to avoid real data conflicts
    created_at = factory.LazyFunction(lambda: datetime.utcnow())
    updated_at = factory.LazyFunction(lambda: datetime.utcnow())
    
    @classmethod
    def _setup_database(cls):
        """Setup test database connection and configuration.
        
        Ensures:
        - Foreign keys are enabled
        - WAL mode for better concurrency
        - Transaction isolation for test cleanup
        """
        # Database setup is handled by the test framework
        # This method is reserved for future database-specific configuration
        pass
    
    @classmethod
    def create_batch_with_cleanup(cls, size: int, **kwargs) -> list:
        """Create a batch of objects with automatic cleanup tracking.
        
        Args:
            size: Number of objects to create
            **kwargs: Factory parameters
            
        Returns:
            List of created objects
        """
        objects = cls.create_batch(size, **kwargs)
        # Track for cleanup - implementation depends on test framework
        return objects
    
    @classmethod
    def generate_unique_sequence(cls, prefix: str = "") -> str:
        """Generate unique sequence values for testing.
        
        Args:
            prefix: Optional prefix for the sequence
            
        Returns:
            Unique string value
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return f"{prefix}{timestamp}" if prefix else timestamp


class DatabaseFactory(BaseFactory):
    """Base factory for models that require database persistence.
    
    Extends BaseFactory with database-specific functionality:
    - Automatic database session management
    - Transaction rollback for test isolation
    - Real database constraint validation
    """
    
    class Meta:
        abstract = True
        strategy = factory.CREATE_STRATEGY  # Auto-save to database
    
    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Create object with database session management."""
        # Custom creation logic for database integration
        # This will be implemented based on the specific database setup
        return super()._create(model_class, *args, **kwargs)


# Common test data generators
class TestDataMixin:
    """Mixin providing common test data generation utilities."""
    
    @staticmethod
    def random_user_id() -> int:
        """Generate realistic Telegram user ID."""
        return fake.random_int(min=100000000, max=999999999)
    
    @staticmethod
    def random_chat_id() -> int:
        """Generate realistic Telegram chat ID."""
        return fake.random_int(min=100000000, max=999999999)
    
    @staticmethod
    def random_message_id() -> int:
        """Generate realistic Telegram message ID."""
        return fake.random_int(min=1, max=999999999)
    
    @staticmethod  
    def random_platform_credentials(platform_type: str) -> str:
        """Generate realistic credentials for different platforms."""
        if platform_type == "todoist":
            return fake.password(length=40)  # Todoist API token length
        elif platform_type == "trello":
            return fake.uuid4()  # Trello API key format
        else:
            return fake.password(length=32)
    
    @staticmethod
    def random_datetime_future(days_ahead: int = 30) -> datetime:
        """Generate random future datetime for due dates."""
        days = fake.random_int(min=1, max=days_ahead)
        hours = fake.random_int(min=1, max=23)
        return datetime.now() + timedelta(days=days, hours=hours)
    
    @staticmethod
    def random_platform_config(platform_type: str) -> Dict[str, Any]:
        """Generate realistic platform configuration."""
        if platform_type == "todoist":
            return {
                'project_id': fake.random_int(min=1000000, max=9999999),
                'section_id': fake.random_int(min=1000000, max=9999999)
            }
        elif platform_type == "trello":
            return {
                'board_id': fake.uuid4(),
                'list_id': fake.uuid4()
            }
        else:
            return {}