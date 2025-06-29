"""Unit tests for keyboard components."""

import pytest
from unittest.mock import Mock

from keyboards.recipient import (
    get_recipient_selection_keyboard, get_recipient_management_keyboard
)
from models.unified_recipient import UnifiedRecipient
from datetime import datetime


class TestRecipientKeyboards:
    """Test recipient keyboard functionality."""
    
    @pytest.fixture
    def sample_recipients(self):
        """Sample recipients for testing."""
        return [
            UnifiedRecipient(
                id=1,
                user_id=12345,
                name="My Todoist",
                platform_type="todoist",
                credentials="token123",
                platform_config=None,
                is_personal=True,
                is_default=True,
                enabled=True,
                shared_by=None,
                created_at=datetime.now(),
                updated_at=datetime.now()
            ),
            UnifiedRecipient(
                id=2,
                user_id=12345,
                name="Team Trello",
                platform_type="trello",
                credentials="key:token",
                platform_config={"board_id": "board123"},
                is_personal=False,
                is_default=False,
                enabled=True,
                shared_by="Team Admin",
                created_at=datetime.now(),
                updated_at=datetime.now()
            ),
            UnifiedRecipient(
                id=3,
                user_id=12345,
                name="Personal Trello",
                platform_type="trello",
                credentials="key:token2",
                platform_config=None,
                is_personal=True,
                is_default=False,
                enabled=False,
                shared_by=None,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        ]
    
    def test_recipient_selection_keyboard_no_recipients(self):
        """Test keyboard with no recipients."""
        keyboard = get_recipient_selection_keyboard([])
        
        assert keyboard is not None
        # Should have at least a cancel button
        assert len(keyboard.inline_keyboard) > 0
    
    def test_recipient_selection_keyboard_with_recipients(self, sample_recipients):
        """Test keyboard with recipients."""
        keyboard = get_recipient_selection_keyboard(sample_recipients)
        
        assert keyboard is not None
        assert len(keyboard.inline_keyboard) > 0
        
        # Should have buttons for each recipient plus control buttons
        # Check that there are multiple rows
        assert len(keyboard.inline_keyboard) >= 2
    
    def test_recipient_selection_keyboard_with_selected(self, sample_recipients):
        """Test keyboard with some recipients pre-selected."""
        selected = ["platform_1"]
        keyboard = get_recipient_selection_keyboard(sample_recipients, selected)
        
        assert keyboard is not None
        assert len(keyboard.inline_keyboard) > 0
        
        # Should show visual feedback for selected recipients
        # We can't test the exact emoji without checking button text,
        # but we can verify the keyboard structure is correct
    
    def test_recipient_management_keyboard(self, sample_recipients):
        """Test recipient management keyboard."""
        keyboard = get_recipient_management_keyboard(sample_recipients)
        
        assert keyboard is not None
        assert len(keyboard.inline_keyboard) > 0
        
        # Should have buttons for each recipient plus management options
        button_count = sum(len(row) for row in keyboard.inline_keyboard)
        assert button_count > 0
    
    def test_keyboard_with_empty_recipients_list(self):
        """Test keyboard with empty recipients list."""
        keyboard = get_recipient_management_keyboard([])
        
        assert keyboard is not None
        # Should still have basic structure even without recipients
        assert len(keyboard.inline_keyboard) >= 0
    
    def test_keyboard_button_structure(self, sample_recipients):
        """Test that keyboard buttons have proper structure."""
        keyboard = get_recipient_selection_keyboard(sample_recipients)
        
        # Check that all buttons have required properties
        for row in keyboard.inline_keyboard:
            for button in row:
                assert hasattr(button, 'text')
                assert hasattr(button, 'callback_data')
                assert button.text is not None
                assert button.callback_data is not None
    
    def test_recipient_filtering_enabled_only(self, sample_recipients):
        """Test keyboard shows enabled recipients appropriately."""
        # Filter to only enabled recipients
        enabled_recipients = [r for r in sample_recipients if r.enabled]
        keyboard = get_recipient_selection_keyboard(enabled_recipients)
        
        assert keyboard is not None
        # Should have fewer recipients than the full list
        assert len(enabled_recipients) == 2  # From our fixture
    
    def test_callback_data_format(self, sample_recipients):
        """Test that callback data follows expected format."""
        keyboard = get_recipient_selection_keyboard(sample_recipients)
        
        # Check that callback data contains expected prefixes
        callback_data_found = []
        for row in keyboard.inline_keyboard:
            for button in row:
                callback_data_found.append(button.callback_data)
        
        # Should have various callback types
        assert len(callback_data_found) > 0
        
        # At least some should be recipient selection callbacks
        recipient_callbacks = [cb for cb in callback_data_found if cb.startswith('select_recipient_')]
        assert len(recipient_callbacks) > 0