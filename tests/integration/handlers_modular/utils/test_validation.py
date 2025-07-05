"""Tests for validation utilities."""

import pytest
from unittest.mock import Mock
from aiogram.types import Message

from handlers_modular.utils.validation import InputValidator, StateValidator


class TestInputValidator:
    """Test input validation utilities."""
    
    def test_validate_non_empty_valid(self):
        """Test validation with valid non-empty text."""
        is_valid, error = InputValidator.validate_non_empty("Test text", "Name")
        
        assert is_valid is True
        assert error is None
    
    def test_validate_non_empty_empty_string(self):
        """Test validation with empty string."""
        is_valid, error = InputValidator.validate_non_empty("", "Name")
        
        assert is_valid is False
        assert "Name cannot be empty" in error
    
    def test_validate_non_empty_whitespace_only(self):
        """Test validation with whitespace-only string."""
        is_valid, error = InputValidator.validate_non_empty("   ", "Name")
        
        assert is_valid is False
        assert "Name cannot be empty" in error
    
    def test_validate_text_message_valid(self):
        """Test message validation with valid text."""
        mock_message = Mock(spec=Message)
        mock_message.text = "Valid message text"
        
        is_valid, result = InputValidator.validate_text_message(mock_message)
        
        assert is_valid is True
        assert result == "Valid message text"
    
    def test_validate_text_message_no_text(self):
        """Test message validation with no text."""
        mock_message = Mock(spec=Message)
        mock_message.text = None
        
        is_valid, error = InputValidator.validate_text_message(mock_message)
        
        assert is_valid is False
        assert "must contain text" in error
    
    def test_validate_text_message_empty_text(self):
        """Test message validation with empty text."""
        mock_message = Mock(spec=Message)
        mock_message.text = "   "
        
        is_valid, error = InputValidator.validate_text_message(mock_message)
        
        assert is_valid is False
        assert "cannot be empty" in error
    
    def test_validate_user_input_valid(self):
        """Test comprehensive user input validation - valid case."""
        mock_message = Mock(spec=Message)
        mock_message.text = "  Valid input  "
        
        is_valid, error, result = InputValidator.validate_user_input(mock_message, "Name")
        
        assert is_valid is True
        assert error is None
        assert result == "Valid input"  # Should be stripped
    
    def test_validate_user_input_invalid(self):
        """Test comprehensive user input validation - invalid case."""
        mock_message = Mock(spec=Message)
        mock_message.text = None
        
        is_valid, error, result = InputValidator.validate_user_input(mock_message, "Name")
        
        assert is_valid is False
        assert "must contain text" in error
        assert result is None
    
    def test_validate_callback_data_valid(self):
        """Test callback data validation - valid case."""
        is_valid, result = InputValidator.validate_callback_data("select_recipient_123", "select_recipient_")
        
        assert is_valid is True
        assert result == "123"
    
    def test_validate_callback_data_invalid_prefix(self):
        """Test callback data validation - invalid prefix."""
        is_valid, error = InputValidator.validate_callback_data("wrong_prefix_123", "select_recipient_")
        
        assert is_valid is False
        assert "Invalid callback data format" in error
    
    def test_validate_recipient_selection_valid(self):
        """Test recipient selection validation - valid case."""
        is_valid, error = InputValidator.validate_recipient_selection(["1", "2", "3"])
        
        assert is_valid is True
        assert error is None
    
    def test_validate_recipient_selection_empty(self):
        """Test recipient selection validation - empty selection."""
        is_valid, error = InputValidator.validate_recipient_selection([])
        
        assert is_valid is False
        assert "select at least one recipient" in error


class TestStateValidator:
    """Test state validation utilities."""
    
    def test_validate_state_data_valid(self):
        """Test state data validation - valid case."""
        state_data = {"platform_type": "todoist", "mode": "user_platform"}
        required_keys = ["platform_type", "mode"]
        
        is_valid, error = StateValidator.validate_state_data(state_data, required_keys)
        
        assert is_valid is True
        assert error is None
    
    def test_validate_state_data_missing_keys(self):
        """Test state data validation - missing keys."""
        state_data = {"platform_type": "todoist"}
        required_keys = ["platform_type", "mode"]
        
        is_valid, error = StateValidator.validate_state_data(state_data, required_keys)
        
        assert is_valid is False
        assert "Missing required state data: mode" in error
    
    def test_validate_state_data_no_data(self):
        """Test state data validation - no data."""
        is_valid, error = StateValidator.validate_state_data(None, ["key"])
        
        assert is_valid is False
        assert "No state data found" in error
    
    def test_validate_credentials_state_valid(self):
        """Test credentials state validation - valid case."""
        state_data = {"platform_type": "todoist", "mode": "user_platform"}
        
        is_valid, error = StateValidator.validate_credentials_state(state_data)
        
        assert is_valid is True
        assert error is None
    
    def test_validate_task_creation_state_valid(self):
        """Test task creation state validation - valid case."""
        state_data = {"selected_recipients": ["1", "2"]}
        
        is_valid, error = StateValidator.validate_task_creation_state(state_data)
        
        assert is_valid is True
        assert error is None
    
    def test_validate_task_creation_state_missing_recipients(self):
        """Test task creation state validation - missing recipients."""
        state_data = {}
        
        is_valid, error = StateValidator.validate_task_creation_state(state_data)
        
        assert is_valid is False
        assert "Missing selected recipients" in error
    
    def test_validate_trello_setup_state_valid(self):
        """Test Trello setup state validation - valid case."""
        state_data = {"credentials": "api_key:token", "board_id": "board123"}
        
        is_valid, error = StateValidator.validate_trello_setup_state(state_data)
        
        assert is_valid is True
        assert error is None