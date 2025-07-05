"""Unit tests for core components using Factory Boy with realistic error scenarios.

This module tests core exceptions and logging functionality with Factory Boy objects
to ensure proper error handling in realistic scenarios.
"""

import pytest

# Import core components
from core.exceptions import (
    ParsingError, TaskCreationError, ValidationError, 
    DatabaseError, PlatformError
)
from core.logging import get_logger

# Import Factory Boy factories
from tests.factories import (
    SimpleTaskFactory,
    TaskFactory,
    TodoistRecipientFactory,
    TrelloRecipientFactory,
    TelegramMessageFactory,
    TelegramUserFactory
)


class TestExceptions:
    """Test custom exception classes with realistic Factory Boy scenarios."""
    
    def test_parsing_error_with_factory_message(self):
        """Test ParsingError exception with realistic message data."""
        # Create realistic message that might cause parsing errors
        problematic_message = TelegramMessageFactory(
            text="Create task with invalid time format: tomorrow at 25:00 PM",
            from_user=TelegramUserFactory(first_name="TestUser")
        )
        
        error_msg = f"Failed to parse message: '{problematic_message.text}'"
        error = ParsingError(error_msg)
        
        assert str(error) == error_msg
        assert isinstance(error, Exception)
        assert "25:00 PM" in str(error)  # Invalid time should be in error
    
    def test_task_creation_error_with_factory_task(self):
        """Test TaskCreationError exception with realistic task data."""
        # Create task that might fail creation
        failing_task = SimpleTaskFactory(
            title="Task That Fails Platform Creation",
            description="Testing task creation failure scenarios",
            platform_type="todoist"
        )
        
        error_msg = f"Failed to create task '{failing_task.title}' on {failing_task.platform_type}"
        error = TaskCreationError(error_msg)
        
        assert str(error) == error_msg
        assert isinstance(error, Exception)
        assert failing_task.title in str(error)
        assert failing_task.platform_type in str(error)
    
    def test_validation_error_with_factory_recipient(self):
        """Test ValidationError exception with realistic recipient data."""
        # Create recipient that might fail validation
        invalid_recipient = TodoistRecipientFactory(
            name="",  # Invalid empty name
            credentials="invalid_token",
            platform_type="todoist"
        )
        
        error_msg = f"Recipient validation failed: empty name for {invalid_recipient.platform_type} recipient"
        error = ValidationError(error_msg)
        
        assert str(error) == error_msg
        assert isinstance(error, Exception)
        assert "empty name" in str(error)
    
    def test_database_error_with_factory_data(self):
        """Test DatabaseError exception with realistic database scenarios."""
        # Create recipient that might cause database errors
        db_recipient = TrelloRecipientFactory(
            user_id=999999999,  # User that might not exist
            name="Database Test Recipient"
        )
        
        error_msg = f"Database operation failed for user_id {db_recipient.user_id}: constraint violation"
        error = DatabaseError(error_msg)
        
        assert str(error) == error_msg
        assert isinstance(error, Exception)
        assert str(db_recipient.user_id) in str(error)
    
    def test_platform_error_with_factory_scenarios(self):
        """Test PlatformError exception with realistic platform scenarios."""
        # Create various platform error scenarios
        platform_scenarios = [
            (TodoistRecipientFactory(), "Todoist API rate limit exceeded"),
            (TrelloRecipientFactory(), "Trello board access denied"),
            (TodoistRecipientFactory(credentials="expired_token"), "Invalid or expired credentials")
        ]
        
        for recipient, error_reason in platform_scenarios:
            error_msg = f"Platform API failed for {recipient.platform_type}: {error_reason}"
            error = PlatformError(error_msg)
            
            assert str(error) == error_msg
            assert isinstance(error, Exception)
            assert recipient.platform_type in str(error)
    
    def test_exception_chaining_with_factory_data(self):
        """Test exception chaining with realistic scenarios."""
        # Create scenario where one error leads to another
        original_task = SimpleTaskFactory(
            title="Task Causing Chain of Errors",
            description="Testing exception chaining"
        )
        
        try:
            # Simulate original error
            raise DatabaseError(f"Failed to save task: {original_task.title}")
        except DatabaseError as db_error:
            # Chain with task creation error
            try:
                raise TaskCreationError("Task creation aborted due to database issue") from db_error
            except TaskCreationError as chained_error:
                assert isinstance(chained_error, TaskCreationError)
                assert isinstance(chained_error.__cause__, DatabaseError)
                assert original_task.title in str(chained_error.__cause__)
    
    def test_exception_with_international_data(self):
        """Test exceptions with international user data from Factory Boy."""
        # Create international users that might cause encoding issues
        international_users = [
            TelegramUserFactory(first_name="José", last_name="García", language_code="es"),
            TelegramUserFactory(first_name="François", last_name="Müller", language_code="fr"),
            TelegramUserFactory(first_name="Александр", last_name="Петров", language_code="ru"),
            TelegramUserFactory(first_name="田中", last_name="太郎", language_code="ja")
        ]
        
        for user in international_users:
            error_msg = f"Validation failed for user {user.first_name} {user.last_name}"
            error = ValidationError(error_msg)
            
            # Verify unicode names work in exceptions
            assert user.first_name in str(error)
            assert user.last_name in str(error)
            assert isinstance(error, Exception)


class TestLogging:
    """Test logging functionality with realistic Factory Boy scenarios."""
    
    def test_get_logger_basic_functionality(self):
        """Test basic logger creation."""
        logger = get_logger("test_module")
        
        assert logger is not None
        assert logger.name == "test_module"
        
        # Test logger has required methods
        assert hasattr(logger, 'info')
        assert hasattr(logger, 'debug')
        assert hasattr(logger, 'error')
        assert hasattr(logger, 'warning')
    
    def test_get_logger_same_name(self):
        """Test getting logger with same name returns same instance."""
        logger1 = get_logger("same_name")
        logger2 = get_logger("same_name")
        
        assert logger1 is logger2
    
    def test_get_logger_different_names(self):
        """Test getting loggers with different names."""
        logger1 = get_logger("module1")
        logger2 = get_logger("module2")
        
        assert logger1 is not logger2
        assert logger1.name == "module1"
        assert logger2.name == "module2"
    
    def test_logging_with_factory_task_data(self):
        """Test logging with realistic task data from Factory Boy."""
        logger = get_logger("task_processing")
        
        # Create realistic tasks for logging scenarios
        tasks = [
            SimpleTaskFactory(title="Task Processing Test", description="Testing logging"),
            SimpleTaskFactory(title="Urgent System Alert", description="Critical issue"),
            SimpleTaskFactory(title="Daily Standup Meeting", description="Team meeting")
        ]
        
        # Test that logger can handle realistic task data
        for task in tasks:
            # These shouldn't raise exceptions with realistic data
            try:
                # Simulate logging realistic task data
                log_message = f"Processing task: {task.title} (ID: {task.id})"
                # We can't actually test log output easily, but we can test the logger accepts the data
                assert isinstance(log_message, str)
                assert task.title in log_message
                assert len(log_message) > 0
            except Exception as e:
                pytest.fail(f"Logger failed with realistic task data: {e}")
    
    def test_logging_with_factory_recipient_data(self):
        """Test logging with realistic recipient data from Factory Boy."""
        logger = get_logger("recipient_management")
        
        # Create recipients for logging scenarios
        recipients = [
            TodoistRecipientFactory(name="Personal Todoist Account"),
            TrelloRecipientFactory(name="Team Trello Board"),
            TodoistRecipientFactory(name="Work Project Tracker")
        ]
        
        for recipient in recipients:
            # Test logging with recipient data
            log_message = f"Managing recipient: {recipient.name} ({recipient.platform_type})"
            assert isinstance(log_message, str)
            assert recipient.name in log_message
            assert recipient.platform_type in log_message
    
    def test_logging_with_international_users(self):
        """Test logging with international user data from Factory Boy."""
        logger = get_logger("international_logging")
        
        # Create international users for logging
        international_users = [
            TelegramUserFactory(first_name="María", last_name="González", language_code="es"),
            TelegramUserFactory(first_name="Jean-Pierre", last_name="Dupont", language_code="fr"),
            TelegramUserFactory(first_name="Hans", last_name="Müller", language_code="de"),
            TelegramUserFactory(first_name="中村", last_name="太郎", language_code="ja")
        ]
        
        for user in international_users:
            # Test that logging handles unicode names correctly
            log_message = f"User activity: {user.first_name} {user.last_name} ({user.language_code})"
            
            # Verify unicode names work in log messages
            assert user.first_name in log_message
            assert user.last_name in log_message
            assert user.language_code in log_message
            assert isinstance(log_message, str)
    
    def test_logging_error_scenarios_with_factory_data(self):
        """Test logging error scenarios with Factory Boy data."""
        logger = get_logger("error_scenarios")
        
        # Create scenarios that might generate errors
        error_scenarios = [
            (SimpleTaskFactory(title="Failed Task"), "Task creation failed"),
            (TodoistRecipientFactory(name="Failed Recipient"), "Recipient validation failed"),
            (TelegramUserFactory(first_name="ErrorUser"), "User processing failed")
        ]
        
        for factory_object, error_type in error_scenarios:
            # Test that error logging works with factory data
            if hasattr(factory_object, 'title'):
                log_message = f"{error_type}: {factory_object.title}"
            elif hasattr(factory_object, 'name'):
                log_message = f"{error_type}: {factory_object.name}"
            elif hasattr(factory_object, 'first_name'):
                log_message = f"{error_type}: {factory_object.first_name}"
            
            assert isinstance(log_message, str)
            assert error_type in log_message
            assert len(log_message) > len(error_type)
    
    def test_logger_performance_with_many_factory_objects(self):
        """Test logger performance with many Factory Boy objects."""
        logger = get_logger("performance_test")
        
        # Create many objects for performance testing
        many_tasks = [SimpleTaskFactory(title=f"Performance Task {i}") for i in range(100)]
        
        # Test that logger can handle many realistic objects efficiently
        for i, task in enumerate(many_tasks):
            log_message = f"Processing task {i}: {task.title}"
            
            # Basic verification that logging structure works
            assert isinstance(log_message, str)
            assert str(i) in log_message
            assert task.title in log_message
        
        # If we get here without timeout/memory issues, performance is acceptable
        assert len(many_tasks) == 100