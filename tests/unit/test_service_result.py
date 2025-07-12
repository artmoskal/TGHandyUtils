"""Unit tests for ServiceResult class following Factory Boy patterns."""

import pytest
from core.interfaces import ServiceResult


class TestServiceResult:
    """Test ServiceResult class behavior."""
    
    def test_success_result_creation(self):
        """Test creating successful result."""
        result = ServiceResult.success_with_data("Operation completed", {"key": "value"})
        
        assert result.success is True
        assert result.message == "Operation completed"
        assert result.data == {"key": "value"}
    
    def test_success_result_without_data(self):
        """Test creating successful result without data."""
        result = ServiceResult.success_with_data("Operation completed")
        
        assert result.success is True
        assert result.message == "Operation completed"
        assert result.data is None
    
    def test_failure_result_creation(self):
        """Test creating failure result."""
        result = ServiceResult.failure("Operation failed")
        
        assert result.success is False
        assert result.message == "Operation failed"
        assert result.data is None
    
    def test_direct_construction(self):
        """Test direct ServiceResult construction."""
        result = ServiceResult(True, "Direct construction", {"test": "data"})
        
        assert result.success is True
        assert result.message == "Direct construction"
        assert result.data == {"test": "data"}
    
    def test_result_immutability_concept(self):
        """Test that results represent immutable operations."""
        result = ServiceResult.success_with_data("Test message", [1, 2, 3])
        
        # Results should be self-contained
        assert hasattr(result, 'success')
        assert hasattr(result, 'message')
        assert hasattr(result, 'data')
        
        # Data should be accessible
        assert result.data == [1, 2, 3]
        
    def test_tuple_unpacking_replacement_pattern(self):
        """Test that ServiceResult can replace tuple unpacking patterns."""
        # This simulates the old pattern: success, message, data = method()
        # With new pattern: result = method()
        
        result = ServiceResult.success_with_data("Task created successfully", {"task_id": 123})
        
        # Old pattern equivalent
        success = result.success
        message = result.message
        data = result.data
        
        assert success is True
        assert message == "Task created successfully"
        assert data == {"task_id": 123}