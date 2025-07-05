"""UnifiedRecipient factories for testing recipient functionality.

This module provides factories for creating recipient test objects with proper
is_personal field handling to test the shared account creation bug fixes.
"""

import factory
from datetime import datetime
from models.unified_recipient import UnifiedRecipient, UnifiedRecipientCreate
from .base import BaseFactory, TestDataMixin, fake


class UnifiedRecipientFactory(BaseFactory, TestDataMixin):
    """Factory for UnifiedRecipient model.
    
    Creates recipient objects with realistic data for testing.
    Supports both personal (is_personal=True) and shared (is_personal=False) recipients.
    """
    
    class Meta:
        model = UnifiedRecipient
    
    # Use sequence to avoid ID conflicts with real data
    id = factory.Sequence(lambda n: n + 1000)
    
    # Generate realistic user IDs
    user_id = factory.LazyFunction(lambda: TestDataMixin.random_user_id())
    
    # Generate realistic recipient names
    name = factory.Faker('company')
    
    # Rotate through supported platforms
    platform_type = factory.Iterator(['todoist', 'trello'])
    
    # Generate platform-appropriate credentials
    credentials = factory.LazyAttribute(
        lambda obj: TestDataMixin.random_platform_credentials(obj.platform_type)
    )
    
    # Generate platform-appropriate configuration
    platform_config = factory.LazyAttribute(
        lambda obj: TestDataMixin.random_platform_config(obj.platform_type)
    )
    
    # Default to personal recipient
    is_personal = True
    
    # Default to enabled
    enabled = True
    
    # Realistic timestamps
    created_at = factory.LazyFunction(datetime.utcnow)
    updated_at = factory.LazyFunction(datetime.utcnow)


class SharedRecipientFactory(UnifiedRecipientFactory):
    """Factory for shared recipients - CRITICAL for testing shared account bug fixes.
    
    This factory creates recipients with is_personal=False and appropriate naming
    to test the shared account creation workflow that was previously broken.
    """
    
    # CRITICAL: This must be False to test shared account functionality
    is_personal = False
    
    # Use descriptive naming for shared recipients
    name = factory.LazyAttribute(lambda obj: f"{fake.company()} (Shared)")


class PersonalRecipientFactory(UnifiedRecipientFactory):
    """Factory for personal recipients with explicit is_personal=True.
    
    Used for testing personal account creation and ensuring proper differentiation
    from shared accounts.
    """
    
    # Explicit personal recipient
    is_personal = True
    
    # Use "My" prefix for personal recipients
    name = factory.LazyAttribute(lambda obj: f"My {obj.platform_type.title()}")


class TodoistRecipientFactory(UnifiedRecipientFactory):
    """Factory for Todoist-specific recipients."""
    
    platform_type = 'todoist'
    credentials = factory.Faker('password', length=40)  # Todoist token length
    platform_config = factory.LazyFunction(lambda: {
        'project_id': TestDataMixin.random_user_id(),
        'section_id': TestDataMixin.random_user_id()
    })


class TodoistSharedRecipientFactory(SharedRecipientFactory):
    """Factory for shared Todoist recipients - tests shared account bug fix."""
    
    platform_type = 'todoist'
    credentials = factory.Faker('password', length=40)
    platform_config = factory.LazyFunction(lambda: {
        'project_id': TestDataMixin.random_user_id(),
        'section_id': TestDataMixin.random_user_id()
    })


class TrelloRecipientFactory(UnifiedRecipientFactory):
    """Factory for Trello-specific recipients."""
    
    platform_type = 'trello'
    credentials = factory.Faker('uuid4')  # Trello API key format
    platform_config = factory.LazyFunction(lambda: {
        'board_id': fake.uuid4(),
        'list_id': fake.uuid4()
    })


class TrelloSharedRecipientFactory(SharedRecipientFactory):
    """Factory for shared Trello recipients - tests the specific bug we fixed.
    
    This factory creates Trello shared recipients to test the bug fix in
    handlers_modular/callbacks/recipient/management.py where Trello list
    selection was always creating personal recipients.
    """
    
    platform_type = 'trello'
    credentials = factory.Faker('uuid4')
    platform_config = factory.LazyFunction(lambda: {
        'board_id': fake.uuid4(),
        'list_id': fake.uuid4()
    })


class DisabledRecipientFactory(UnifiedRecipientFactory):
    """Factory for disabled recipients to test filtering logic."""
    
    enabled = False
    name = factory.LazyAttribute(lambda obj: f"Disabled {fake.company()}")


class UnifiedRecipientCreateFactory(BaseFactory, TestDataMixin):
    """Factory for UnifiedRecipientCreate model (used in service layer).
    
    Creates recipient creation objects for testing service methods directly.
    """
    
    class Meta:
        model = UnifiedRecipientCreate
    
    name = factory.Faker('company')
    platform_type = factory.Iterator(['todoist', 'trello'])
    credentials = factory.LazyAttribute(
        lambda obj: TestDataMixin.random_platform_credentials(obj.platform_type)
    )
    platform_config = factory.LazyAttribute(
        lambda obj: TestDataMixin.random_platform_config(obj.platform_type)
    )
    is_personal = True  # Default to personal
    enabled = True


class SharedRecipientCreateFactory(UnifiedRecipientCreateFactory):
    """Factory for creating shared recipient creation objects."""
    
    is_personal = False
    name = factory.LazyAttribute(lambda obj: f"{fake.company()} (Shared)")


# Convenience factories for common test scenarios
class MultiPlatformRecipientFactory:
    """Factory collection for creating recipients across multiple platforms."""
    
    @staticmethod
    def create_all_platforms(user_id: int, is_personal: bool = True) -> list:
        """Create recipients for all supported platforms.
        
        Args:
            user_id: The user ID to create recipients for
            is_personal: Whether to create personal or shared recipients
            
        Returns:
            List of created recipient objects
        """
        recipients = []
        
        if is_personal:
            recipients.append(TodoistRecipientFactory(user_id=user_id, is_personal=True))
            recipients.append(TrelloRecipientFactory(user_id=user_id, is_personal=True))
        else:
            recipients.append(TodoistSharedRecipientFactory(user_id=user_id, is_personal=False))
            recipients.append(TrelloSharedRecipientFactory(user_id=user_id, is_personal=False))
        
        return recipients
    
    @staticmethod
    def create_mixed_scenarios(user_id: int) -> dict:
        """Create comprehensive test scenario with mixed recipient types.
        
        Args:
            user_id: The user ID to create recipients for
            
        Returns:
            Dictionary with categorized recipients
        """
        return {
            'personal_enabled': [
                TodoistRecipientFactory(user_id=user_id, is_personal=True, enabled=True),
                TrelloRecipientFactory(user_id=user_id, is_personal=True, enabled=True)
            ],
            'shared_enabled': [
                TodoistSharedRecipientFactory(user_id=user_id, is_personal=False, enabled=True),
                TrelloSharedRecipientFactory(user_id=user_id, is_personal=False, enabled=True)
            ],
            'disabled': [
                DisabledRecipientFactory(user_id=user_id, is_personal=True, enabled=False)
            ]
        }