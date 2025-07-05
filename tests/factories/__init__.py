"""Factory Boy infrastructure for TGHandyUtils test suite.

This module provides comprehensive factory infrastructure for creating test objects
with real database integration, replacing the previous mock-based testing approach.

Architecture:
- BaseFactory: Common configuration and database integration
- Domain Factories: UnifiedRecipient, Task, Preferences, Platform configs
- Integration Factories: Complex scenario and workflow factories

Usage:
    from tests.factories import UnifiedRecipientFactory, SharedRecipientFactory
    
    # Create personal recipient
    personal = UnifiedRecipientFactory(is_personal=True)
    
    # Create shared recipient (for testing shared account bug fixes)
    shared = SharedRecipientFactory(is_personal=False)
"""

# Import all factories for easy access
from .base import BaseFactory
from .recipient_factory import (
    UnifiedRecipientFactory,
    SharedRecipientFactory, 
    PersonalRecipientFactory,
    TodoistRecipientFactory,
    TrelloRecipientFactory,
    TodoistSharedRecipientFactory,
    TrelloSharedRecipientFactory,
    DisabledRecipientFactory,
    MultiPlatformRecipientFactory
)
from .task_factory import (
    SimpleTaskFactory,
    SimpleTaskDBFactory,
    TaskFactory,
    TaskDBFactory,
    ScreenshotTaskFactory,
    ScreenshotTaskDBFactory,
    UrgentTaskFactory,
    LongTermTaskFactory,
    TaskBatchFactory
)
from .preferences_factory import UserPreferencesFactory
# Add alias for consistency
PreferencesFactory = UserPreferencesFactory
from .platform_factory import (
    PlatformConfigFactory,
    TodoistConfigFactory,
    TrelloConfigFactory
)
from .message_factory import (
    TelegramUserFactory,
    TelegramMessageFactory,
    CallbackQueryFactory
)

__all__ = [
    'BaseFactory',
    'UnifiedRecipientFactory',
    'SharedRecipientFactory',
    'PersonalRecipientFactory',
    'TodoistRecipientFactory', 
    'TrelloRecipientFactory',
    'TodoistSharedRecipientFactory',
    'TrelloSharedRecipientFactory',
    'DisabledRecipientFactory',
    'MultiPlatformRecipientFactory',
    'SimpleTaskFactory',
    'SimpleTaskDBFactory',
    'TaskFactory',
    'TaskDBFactory',
    'ScreenshotTaskFactory',
    'ScreenshotTaskDBFactory',
    'UrgentTaskFactory',
    'LongTermTaskFactory',
    'TaskBatchFactory',
    'UserPreferencesFactory',
    'PreferencesFactory',
    'PlatformConfigFactory',
    'TodoistConfigFactory',
    'TrelloConfigFactory',
    'TelegramUserFactory',
    'TelegramMessageFactory',
    'CallbackQueryFactory'
]