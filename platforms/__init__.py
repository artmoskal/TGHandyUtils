from platforms.base import TaskPlatformFactory, AbstractTaskPlatform, register_platform

# Import platform implementations to trigger registration
from platforms.todoist import TodoistPlatform
from platforms.trello import TrelloPlatform
from platforms.google_calendar import GoogleCalendarPlatform

__all__ = ['TaskPlatformFactory', 'AbstractTaskPlatform', 'register_platform']