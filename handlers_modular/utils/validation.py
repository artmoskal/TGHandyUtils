"""Input validation utilities for handlers."""

from typing import Optional, Tuple
from aiogram.types import Message
from core.interfaces import ServiceResult


class InputValidator:
    """Common input validation functions."""
    
    @staticmethod
    def validate_non_empty(text: str, field_name: str) -> ServiceResult:
        """Validate that text is not empty."""
        if not text or not text.strip():
            return ServiceResult.failure(f"{field_name} cannot be empty")
        return ServiceResult.success_with_data("Valid", text.strip())
    
    @staticmethod
    def validate_text_message(message: Message) -> ServiceResult:
        """Validate that message contains text."""
        if not message.text:
            return ServiceResult.failure("Message must contain text")
        
        text = message.text.strip()
        if not text:
            return ServiceResult.failure("Message cannot be empty")
        
        return ServiceResult.success_with_data("Valid message", text)
    
    @staticmethod
    def validate_user_input(message: Message, field_name: str) -> ServiceResult:
        """Comprehensive validation for user text input."""
        # Check if message has text
        result = InputValidator.validate_text_message(message)
        if not result.success:
            return result
        
        # Check if text is not empty after stripping
        text = result.data
        result = InputValidator.validate_non_empty(text, field_name)
        if not result.success:
            return result
        
        return ServiceResult.success_with_data("Valid input", text.strip())
    
    @staticmethod
    def validate_callback_data(callback_data: str, expected_prefix: str) -> ServiceResult:
        """Validate callback data format."""
        if not callback_data:
            return ServiceResult.failure("No callback data provided")
        
        if not callback_data.startswith(expected_prefix):
            return ServiceResult.failure(f"Invalid callback data format, expected prefix: {expected_prefix}")
        
        return ServiceResult.success_with_data("Valid callback data", callback_data.replace(expected_prefix, ""))
    
    @staticmethod
    def validate_recipient_selection(selected_recipients: list) -> ServiceResult:
        """Validate recipient selection."""
        if not selected_recipients:
            return ServiceResult.failure("Please select at least one recipient")
        
        if not isinstance(selected_recipients, list):
            return ServiceResult.failure("Invalid recipient selection format")
        
        return ServiceResult.success_with_data("Valid selection", selected_recipients)


class StateValidator:
    """State-specific validation functions."""
    
    @staticmethod
    def validate_state_data(state_data: dict, required_keys: list) -> ServiceResult:
        """Validate that state data contains required keys."""
        if not state_data:
            return ServiceResult.failure("No state data found")
        
        missing_keys = [key for key in required_keys if key not in state_data]
        if missing_keys:
            return ServiceResult.failure(f"Missing required state data: {', '.join(missing_keys)}")
        
        return ServiceResult.success_with_data("Valid state data", state_data)
    
    @staticmethod
    def validate_credentials_state(state_data: dict) -> ServiceResult:
        """Validate credentials input state data."""
        required_keys = ['platform_type', 'mode']
        return StateValidator.validate_state_data(state_data, required_keys)
    
    @staticmethod
    def validate_task_creation_state(state_data: dict) -> ServiceResult:
        """Validate task creation state data."""
        # selected_recipients is optional, can be empty list
        if 'selected_recipients' not in state_data:
            return ServiceResult.failure("Missing selected recipients in state")
        
        return ServiceResult.success_with_data("Valid task creation state", state_data)
    
    @staticmethod
    def validate_trello_setup_state(state_data: dict) -> ServiceResult:
        """Validate Trello setup state data."""
        required_keys = ['credentials', 'board_id']
        return StateValidator.validate_state_data(state_data, required_keys)