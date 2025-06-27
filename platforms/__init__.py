from platforms.base import TaskPlatformFactory, AbstractTaskPlatform, register_platform

# Import platform implementations to trigger registration
from platforms.todoist import TodoistPlatform
from platforms.trello import TrelloPlatform

__all__ = ['TaskPlatformFactory', 'AbstractTaskPlatform', 'register_platform']