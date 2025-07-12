"""Tests for string deduplication and constants - Test-First Development."""

import pytest
from unittest.mock import Mock, patch

# These imports will fail until we create the constants
from helpers.error_messages import ErrorMessages
from helpers.constants import HttpConstants


class TestErrorMessages:
    """Test centralized error messages."""
    
    def test_error_messages_class_exists(self):
        """Test ErrorMessages class exists and has expected structure."""
        # This will fail until we create ErrorMessages
        assert hasattr(ErrorMessages, 'RECIPIENT_NOT_FOUND')
        assert hasattr(ErrorMessages, 'TASK_CREATION_FAILED')
        assert hasattr(ErrorMessages, 'PLATFORM_CONNECTION_FAILED')
    
    def test_recipient_not_found_message(self):
        """Test recipient not found message is consistent."""
        # This will fail until we define the constant
        expected = "❌ Recipient not found"
        assert ErrorMessages.RECIPIENT_NOT_FOUND == expected
    
    def test_task_creation_failed_message(self):
        """Test task creation failed message is consistent."""
        # This will fail until we define the constant
        expected = "❌ Failed to create task in database."
        assert ErrorMessages.TASK_CREATION_FAILED == expected
    
    def test_platform_connection_failed_format(self):
        """Test platform connection error supports formatting."""
        # This will fail until we define the constant with format placeholder
        expected = "❌ Could not connect to {platform}"
        assert ErrorMessages.PLATFORM_CONNECTION_FAILED == expected
        
        # Test formatting works
        formatted = ErrorMessages.PLATFORM_CONNECTION_FAILED.format(platform="Todoist")
        assert formatted == "❌ Could not connect to Todoist"
    
    def test_no_recipients_configured_message(self):
        """Test no recipients message is consistent."""
        # This will fail until we define the constant
        expected = "❌ No recipients configured. Please add accounts first."
        assert ErrorMessages.NO_RECIPIENTS_CONFIGURED == expected


class TestHttpConstants:
    """Test HTTP timeout and retry constants."""
    
    def test_http_constants_class_exists(self):
        """Test HttpConstants class exists and has expected structure."""
        # This will fail until we create HttpConstants
        assert hasattr(HttpConstants, 'HTTP_TIMEOUT')
        assert hasattr(HttpConstants, 'MAX_RETRIES')
        assert hasattr(HttpConstants, 'BACKOFF_FACTOR')
    
    def test_http_timeout_value(self):
        """Test HTTP timeout constant value."""
        # This will fail until we define the constant
        assert HttpConstants.HTTP_TIMEOUT == 30
        assert isinstance(HttpConstants.HTTP_TIMEOUT, int)
    
    def test_max_retries_value(self):
        """Test max retries constant value."""
        # This will fail until we define the constant
        assert HttpConstants.MAX_RETRIES == 3
        assert isinstance(HttpConstants.MAX_RETRIES, int)
    
    def test_backoff_factor_value(self):
        """Test backoff factor constant value."""
        # This will fail until we define the constant
        assert HttpConstants.BACKOFF_FACTOR == 2.0
        assert isinstance(HttpConstants.BACKOFF_FACTOR, float)


class TestStringDeduplicationIntegration:
    """Test that services can use the new constants."""
    
    def test_recipient_task_service_uses_error_constants(self):
        """Test RecipientTaskService uses error message constants."""
        # Check that ErrorMessages is imported in the service file
        import services.recipient_task_service as service_module
        import inspect
        
        # Get the source code of the module
        source = inspect.getsource(service_module)
        
        # Verify that ErrorMessages is imported and used
        assert 'from helpers.error_messages import ErrorMessages' in source
        assert 'ErrorMessages.RECIPIENT_NOT_FOUND' in source or 'ErrorMessages.TASK_CREATION_FAILED' in source
    
    def test_platform_classes_use_http_constants(self):
        """Test platform classes use HTTP constants."""
        # Check that HttpConstants is imported in platform files
        import platforms.todoist as todoist_module
        import platforms.trello as trello_module
        import inspect
        
        # Get the source code of the modules
        todoist_source = inspect.getsource(todoist_module)
        trello_source = inspect.getsource(trello_module)
        
        # Verify that HttpConstants is imported and used
        assert 'from helpers.constants import HttpConstants' in todoist_source
        assert 'from helpers.constants import HttpConstants' in trello_source
        assert 'HttpConstants.HTTP_TIMEOUT' in todoist_source
        assert 'HttpConstants.MAX_RETRIES' in todoist_source


class TestConstantsUsageValidation:
    """Test that constants eliminate magic numbers and strings."""
    
    def test_no_hardcoded_timeout_in_platforms(self):
        """Test that platform files don't have hardcoded timeout=30."""
        # This test checks that we've properly replaced magic numbers
        # It will pass initially but helps validate our refactoring
        
        # Read platform files to check for hardcoded values
        import glob
        import re
        
        platform_files = glob.glob('platforms/*.py')
        hardcoded_timeouts = []
        
        for file_path in platform_files:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                    # Look for hardcoded timeout=30 patterns
                    if re.search(r'timeout\s*=\s*30', content):
                        hardcoded_timeouts.append(file_path)
            except FileNotFoundError:
                pass  # Skip if file doesn't exist in test environment
        
        # After refactoring, this should be empty
        # Initially this will fail, showing what needs to be fixed
        if hardcoded_timeouts:
            pytest.fail(f"Found hardcoded timeouts in: {hardcoded_timeouts}")
    
    def test_error_message_consistency(self):
        """Test that error messages are used consistently."""
        # This test helps ensure we've replaced all duplicate strings
        # It will initially pass but validates our refactoring
        
        # Check for duplicate error patterns (simplified version)
        import glob
        import re
        
        service_files = glob.glob('services/*.py')
        duplicate_patterns = []
        
        # Pattern for "❌ [text] not found" messages
        not_found_pattern = r'❌[^"]*not found'
        
        for file_path in service_files:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                    matches = re.findall(not_found_pattern, content, re.IGNORECASE)
                    if len(matches) > 1:  # Multiple similar patterns in same file
                        duplicate_patterns.append((file_path, matches))
            except FileNotFoundError:
                pass  # Skip if file doesn't exist in test environment
        
        # After refactoring, files should use constants instead of duplicates
        if duplicate_patterns:
            pytest.fail(f"Found potential duplicate error patterns: {duplicate_patterns}")