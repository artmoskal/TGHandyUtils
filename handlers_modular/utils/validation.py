"""Input validation utilities for handlers."""

from typing import Optional, Tuple
from aiogram.types import Message


class InputValidator:
    """Common input validation functions."""
    
    @staticmethod
    def validate_non_empty(text: str, field_name: str) -> Tuple[bool, Optional[str]]:
        """Validate that text is not empty."""
        if not text or not text.strip():
            return False, f"{field_name} cannot be empty"
        return True, None
    
    @staticmethod
    def validate_text_message(message: Message) -> Tuple[bool, Optional[str]]:
        """Validate that message contains text."""
        if not message.text:
            return False, "Message must contain text"
        
        text = message.text.strip()
        if not text:
            return False, "Message cannot be empty"
        
        return True, text
    
    @staticmethod
    def validate_user_input(message: Message, field_name: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Comprehensive validation for user text input."""
        # Check if message has text
        is_valid, text_or_error = InputValidator.validate_text_message(message)
        if not is_valid:
            return False, text_or_error, None
        
        # Check if text is not empty after stripping
        is_valid, error = InputValidator.validate_non_empty(text_or_error, field_name)
        if not is_valid:
            return False, error, None
        
        return True, None, text_or_error.strip()
    
    @staticmethod
    def validate_callback_data(callback_data: str, expected_prefix: str) -> Tuple[bool, Optional[str]]:
        """Validate callback data format."""
        if not callback_data:
            return False, "No callback data provided"
        
        if not callback_data.startswith(expected_prefix):
            return False, f"Invalid callback data format, expected prefix: {expected_prefix}"
        
        return True, callback_data.replace(expected_prefix, "")
    
    @staticmethod
    def validate_recipient_selection(selected_recipients: list) -> Tuple[bool, Optional[str]]:
        """Validate recipient selection."""
        if not selected_recipients:
            return False, "Please select at least one recipient"
        
        if not isinstance(selected_recipients, list):
            return False, "Invalid recipient selection format"
        
        return True, None


class StateValidator:
    """State-specific validation functions."""
    
    @staticmethod
    def validate_state_data(state_data: dict, required_keys: list) -> Tuple[bool, Optional[str]]:
        """Validate that state data contains required keys."""
        if not state_data:
            return False, "No state data found"
        
        missing_keys = [key for key in required_keys if key not in state_data]
        if missing_keys:
            return False, f"Missing required state data: {', '.join(missing_keys)}"
        
        return True, None
    
    @staticmethod
    def validate_credentials_state(state_data: dict) -> Tuple[bool, Optional[str]]:
        """Validate credentials input state data."""
        required_keys = ['platform_type', 'mode']
        return StateValidator.validate_state_data(state_data, required_keys)
    
    @staticmethod
    def validate_task_creation_state(state_data: dict) -> Tuple[bool, Optional[str]]:
        """Validate task creation state data."""
        # selected_recipients is optional, can be empty list
        if 'selected_recipients' not in state_data:
            return False, "Missing selected recipients in state"
        
        return True, None
    
    @staticmethod
    def validate_trello_setup_state(state_data: dict) -> Tuple[bool, Optional[str]]:
        """Validate Trello setup state data."""
        required_keys = ['credentials', 'board_id']
        return StateValidator.validate_state_data(state_data, required_keys)