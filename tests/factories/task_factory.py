"""Task factories for testing task creation functionality.

This module provides factories for creating task test objects with realistic
data for testing task creation workflows and screenshot attachment functionality.
"""

import factory
from datetime import datetime, timedelta
from random import randint
from models.task import TaskCreate, TaskDB
from .base import BaseFactory, TestDataMixin, SimpleObject, fake


class SimpleTaskFactory(BaseFactory, TestDataMixin):
    """Factory for simple test task objects with flexible attributes."""
    
    class Meta:
        model = SimpleObject
    
    # Generate realistic task titles
    title = factory.Faker('sentence', nb_words=4)
    
    # Generate realistic descriptions
    description = factory.Faker('text', max_nb_chars=200)
    
    # ISO format due times
    due_time = factory.LazyFunction(
        lambda: (datetime.now() + timedelta(days=randint(1, 30))).isoformat()
    )
    
    # Platform type for error testing
    platform_type = factory.Iterator(['todoist', 'trello'])
    
    # Unique task ID
    id = factory.Sequence(lambda n: n + 1000)


class TaskDBFactory(BaseFactory, TestDataMixin):
    """Factory for TaskDB model.
    
    Creates database task objects with realistic data for testing scheduler,
    repository, and service functionality that works with persisted tasks.
    """
    
    class Meta:
        model = TaskDB
        exclude = ('created_at', 'updated_at')  # TaskDB doesn't have timestamp fields
    
    # Database fields
    id = factory.Sequence(lambda n: n + 1000)
    user_id = factory.LazyFunction(lambda: TestDataMixin.random_user_id())
    chat_id = factory.LazyFunction(lambda: TestDataMixin.random_chat_id())
    message_id = factory.LazyFunction(lambda: TestDataMixin.random_message_id())
    
    # Task content
    title = factory.Faker('sentence', nb_words=4)
    description = factory.Faker('text', max_nb_chars=200)
    due_time = factory.LazyFunction(
        lambda: (datetime.now() + timedelta(days=randint(1, 30))).isoformat()
    )
    
    # Screenshot support
    screenshot_file_id = None


class SimpleTaskDBFactory(TaskDBFactory):
    """Factory for TaskDB with additional priority field for flexible testing."""
    
    class Meta:
        model = TaskDB
        exclude = ('created_at', 'updated_at', 'priority')  # Exclude priority since TaskDB doesn't have it
    
    # Add priority field that's commonly needed in tests but exclude it from model creation
    priority = factory.Iterator(['low', 'medium', 'high', 'urgent'])


class TaskFactory(BaseFactory, TestDataMixin):
    """Factory for TaskCreate model.
    
    Creates task objects with realistic data for testing task creation,
    screenshot attachment, and recipient assignment workflows.
    """
    
    class Meta:
        model = TaskCreate
        exclude = ('id', 'created_at', 'updated_at')  # TaskCreate doesn't have these fields
    
    # Generate realistic task titles
    title = factory.Faker('sentence', nb_words=4)
    
    # Generate realistic descriptions
    description = factory.Faker('text', max_nb_chars=200)
    
    # Generate realistic future due dates
    due_time = factory.LazyFunction(
        lambda: (datetime.now() + timedelta(days=randint(1, 30))).isoformat()
    )


class UrgentTaskFactory(TaskFactory):
    """Factory for urgent tasks with short due dates."""
    
    due_time = factory.LazyFunction(
        lambda: (datetime.now() + timedelta(hours=randint(1, 24))).isoformat()
    )
    title = factory.LazyAttribute(lambda obj: f"URGENT: {fake.sentence(nb_words=3)}")


class LongTermTaskFactory(TaskFactory):
    """Factory for long-term tasks with distant due dates."""
    
    due_time = factory.LazyFunction(
        lambda: (datetime.now() + timedelta(days=randint(30, 365))).isoformat()
    )
    title = factory.LazyAttribute(lambda obj: f"Long-term: {fake.sentence(nb_words=3)}")


class ScreenshotTaskDBFactory(TaskDBFactory):
    """Factory for screenshot tasks using TaskDB model for scheduler tests."""
    
    title = factory.LazyAttribute(lambda obj: f"Task with screenshot: {fake.word()}")
    description = "This task includes a screenshot attachment for testing."
    
    @factory.post_generation
    def screenshot_data(self, create, extracted, **kwargs):
        """Add screenshot data for testing attachment workflows."""
        if extracted:
            # Screenshot data will be added during test execution
            return extracted
        return None


class ScreenshotTaskFactory(TaskFactory):
    """Factory for tasks with screenshot attachments.
    
    This factory creates tasks specifically for testing the screenshot
    attachment workflow that was broken by mock-based testing.
    """
    
    title = factory.LazyAttribute(lambda obj: f"Task with screenshot: {fake.word()}")
    description = "This task includes a screenshot attachment for testing."
    
    @factory.post_generation
    def screenshot_data(self, create, extracted, **kwargs):
        """Add screenshot data for testing attachment workflows."""
        if extracted:
            # Screenshot data will be added during test execution
            return extracted
        return None


class NoDescriptionTaskFactory(TaskFactory):
    """Factory for tasks without descriptions."""
    
    description = ""


class MinimalTaskFactory(TaskFactory):
    """Factory for minimal tasks with only required fields."""
    
    title = factory.Faker('sentence', nb_words=2)
    description = ""


class TodoistTaskFactory(TaskFactory):
    """Factory for Todoist-specific tasks."""
    
    # Todoist supports markdown in descriptions
    description = factory.LazyAttribute(
        lambda obj: f"**{fake.sentence()}**\n\n{fake.text(max_nb_chars=100)}"
    )


class TrelloTaskFactory(TaskFactory):
    """Factory for Trello-specific tasks."""
    
    # Trello descriptions are plain text
    description = factory.Faker('text', max_nb_chars=150)


class TaskWithAttachmentFactory(TaskFactory):
    """Factory for tasks that require file attachments.
    
    Used for testing file upload and attachment workflows.
    """
    
    title = factory.LazyAttribute(lambda obj: f"Task with files: {fake.word()}")
    description = "This task includes file attachments for testing."


class RecurringTaskFactory(TaskFactory):
    """Factory for recurring/scheduled tasks."""
    
    title = factory.LazyAttribute(lambda obj: f"Daily: {fake.sentence(nb_words=2)}")
    
    # Set due time to regular intervals
    due_time = factory.LazyFunction(
        lambda: (datetime.now().replace(hour=9, minute=0, second=0, microsecond=0) + 
                timedelta(days=1)).isoformat()
    )


class ErrorTaskFactory(TaskFactory):
    """Factory for tasks that should cause validation errors.
    
    Used for testing error handling in task creation workflows.
    """
    
    # Intentionally invalid data for testing error scenarios
    title = ""  # Empty title should cause validation error
    due_time = "invalid-date-format"  # Invalid date format


class TaskBatchFactory:
    """Factory collection for creating batches of related tasks."""
    
    @staticmethod
    def create_mixed_priority_batch(size: int = 10) -> list:
        """Create a batch of tasks with mixed priorities.
        
        Args:
            size: Number of tasks to create
            
        Returns:
            List of task objects with varied priorities
        """
        tasks = []
        priorities = ['low', 'medium', 'high', 'urgent']
        
        for i in range(size):
            priority = priorities[i % len(priorities)]
            if priority == 'urgent':
                tasks.append(UrgentTaskFactory())
            elif priority in ['low', 'medium']:
                tasks.append(LongTermTaskFactory())
            else:
                tasks.append(TaskFactory())
        
        return tasks
    
    @staticmethod
    def create_platform_specific_batch(platform_type: str, size: int = 5) -> list:
        """Create platform-specific task batch.
        
        Args:
            platform_type: 'todoist' or 'trello'
            size: Number of tasks to create
            
        Returns:
            List of platform-specific task objects
        """
        if platform_type == 'todoist':
            return TodoistTaskFactory.create_batch(size)
        elif platform_type == 'trello':
            return TrelloTaskFactory.create_batch(size)
        else:
            return TaskFactory.create_batch(size)
    
    @staticmethod
    def create_comprehensive_test_scenario() -> dict:
        """Create comprehensive task test scenario.
        
        Returns:
            Dictionary with categorized task objects for testing
        """
        return {
            'urgent': [SimpleTaskFactory(priority='urgent') for _ in range(3)],
            'normal': TaskFactory.create_batch(5),
            'long_term': [SimpleTaskFactory(priority='low') for _ in range(3)],
            'with_screenshots': ScreenshotTaskFactory.create_batch(2),
            'minimal': MinimalTaskFactory.create_batch(2),
            'recurring': RecurringTaskFactory.create_batch(2),
            'todoist_specific': TodoistTaskFactory.create_batch(3),
            'trello_specific': TrelloTaskFactory.create_batch(3)
        }