"""User preferences factories for testing user configuration functionality."""

import factory
from models.unified_recipient import UnifiedUserPreferences, UnifiedUserPreferencesCreate
from .base import BaseFactory, TestDataMixin


class UserPreferencesFactory(BaseFactory, TestDataMixin):
    """Factory for UnifiedUserPreferences model."""
    
    class Meta:
        model = UnifiedUserPreferences
        exclude = ('id',)  # Exclude id field from BaseFactory
    
    user_id = factory.LazyFunction(lambda: TestDataMixin.random_user_id())
    show_recipient_ui = factory.Faker('boolean', chance_of_getting_true=80)
    telegram_notifications = factory.Faker('boolean', chance_of_getting_true=90)
    owner_name = factory.Faker('first_name')
    location = factory.Faker('city')


class UserPreferencesCreateFactory(BaseFactory, TestDataMixin):
    """Factory for UnifiedUserPreferencesCreate model."""
    
    class Meta:
        model = UnifiedUserPreferencesCreate
        exclude = ('id', 'created_at', 'updated_at')  # UnifiedUserPreferencesCreate doesn't have these fields
    
    show_recipient_ui = factory.Faker('boolean', chance_of_getting_true=80)
    telegram_notifications = factory.Faker('boolean', chance_of_getting_true=90)
    owner_name = factory.Faker('first_name')
    location = factory.Faker('city')