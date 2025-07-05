"""Platform configuration factories for testing platform integration."""

import factory
from .base import BaseFactory, TestDataMixin, SimpleObject, fake


class PlatformConfigFactory(BaseFactory, TestDataMixin):
    """Factory for platform configuration dictionaries."""
    
    class Meta:
        model = SimpleObject
    
    @factory.lazy_attribute
    def platform_config(self):
        """Generate platform-specific configuration."""
        return {
            'api_version': 'v2',
            'rate_limit': 450,
            'timeout': 30
        }


class TodoistConfigFactory(PlatformConfigFactory):
    """Factory for Todoist platform configuration."""
    
    platform_type = 'todoist'
    api_version = 'v2'
    rate_limit = 450  # Todoist API limit
    
    @factory.lazy_attribute
    def platform_config(self):
        return {
            'api_token': fake.password(length=40),
            'project_id': TestDataMixin.random_user_id(),
            'section_id': TestDataMixin.random_user_id(),
            'sync_enabled': True,
            'webhook_url': fake.url()
        }


class TrelloConfigFactory(PlatformConfigFactory):
    """Factory for Trello platform configuration."""
    
    platform_type = 'trello'
    
    @factory.lazy_attribute
    def platform_config(self):
        return {
            'api_key': fake.uuid4(),
            'token': fake.uuid4(),
            'board_id': fake.uuid4(),
            'list_id': fake.uuid4(),
            'webhook_enabled': True
        }