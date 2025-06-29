"""Unit tests for core components."""

import pytest
from unittest.mock import Mock

from core.exceptions import (
    ParsingError, TaskCreationError, ValidationError, 
    DatabaseError, PlatformError
)
from core.logging import get_logger


class TestExceptions:
    """Test custom exception classes."""
    
    def test_parsing_error(self):
        """Test ParsingError exception."""
        error = ParsingError("Parsing failed")
        assert str(error) == "Parsing failed"
        assert isinstance(error, Exception)
    
    def test_task_creation_error(self):
        """Test TaskCreationError exception."""
        error = TaskCreationError("Task creation failed")
        assert str(error) == "Task creation failed"
        assert isinstance(error, Exception)
    
    def test_validation_error(self):
        """Test ValidationError exception."""
        error = ValidationError("Validation failed")
        assert str(error) == "Validation failed"
        assert isinstance(error, Exception)
    
    def test_database_error(self):
        """Test DatabaseError exception."""
        error = DatabaseError("Database operation failed")
        assert str(error) == "Database operation failed"
        assert isinstance(error, Exception)
    
    def test_platform_error(self):
        """Test PlatformError exception."""
        error = PlatformError("Platform API failed")
        assert str(error) == "Platform API failed"
        assert isinstance(error, Exception)


class TestLogging:
    """Test logging functionality."""
    
    def test_get_logger(self):
        """Test logger creation."""
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