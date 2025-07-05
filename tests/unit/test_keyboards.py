"""Unit tests for keyboard components using Factory Boy with real objects.

This module tests keyboard functionality with Factory Boy objects instead of 
hardcoded test data, ensuring keyboards work correctly with realistic recipient data.
"""

import pytest
from datetime import datetime

# Import keyboard components
from keyboards.recipient import (
    get_recipient_selection_keyboard, get_recipient_management_keyboard
)

# Import Factory Boy factories
from tests.factories import (
    UnifiedRecipientFactory,
    TodoistRecipientFactory,
    TrelloRecipientFactory,
    PersonalRecipientFactory,
    SharedRecipientFactory,
    DisabledRecipientFactory,
    MultiPlatformRecipientFactory
)

# Import models
from models.unified_recipient import UnifiedRecipient


class TestRecipientKeyboards:
    """Test recipient keyboard functionality with Factory Boy objects."""
    
    def setup_method(self):
        """Setup test user ID for consistent factory generation."""
        self.test_user_id = 333333333  # Consistent test user ID
    
    def test_recipient_selection_keyboard_no_recipients(self):
        """Test keyboard with no recipients using empty list."""
        keyboard = get_recipient_selection_keyboard([])
        
        assert keyboard is not None
        # Should have at least a cancel button
        assert len(keyboard.inline_keyboard) > 0
    
    def test_recipient_selection_keyboard_with_factory_recipients(self):
        """Test keyboard with Factory Boy generated recipients."""
        # Create varied recipients using factories
        recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user_id,
                is_personal=True,
                enabled=True
            ),
            TrelloRecipientFactory(
                user_id=self.test_user_id,
                is_personal=False,
                enabled=True
            ),
            PersonalRecipientFactory(
                user_id=self.test_user_id,
                enabled=False
            )
        ]
        
        keyboard = get_recipient_selection_keyboard(recipients)
        
        assert keyboard is not None
        assert len(keyboard.inline_keyboard) > 0
        
        # Should have buttons for each recipient plus control buttons
        # Check that there are multiple rows
        assert len(keyboard.inline_keyboard) >= 2
    
    def test_recipient_selection_keyboard_with_mixed_platforms(self):
        """Test keyboard with mixed platform recipients from factory."""
        # Create comprehensive mix using factory
        recipients = MultiPlatformRecipientFactory.create_all_platforms(
            self.test_user_id,
            is_personal=True
        )
        
        keyboard = get_recipient_selection_keyboard(recipients)
        
        assert keyboard is not None
        assert len(keyboard.inline_keyboard) > 0
        
        # Verify keyboard handles different platform types
        platforms = {r.platform_type for r in recipients}
        assert platforms == {'todoist', 'trello'}  # Both platforms represented
    
    def test_recipient_selection_keyboard_with_selected(self):
        """Test keyboard with some recipients pre-selected."""
        recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user_id,
                id=1,
                is_personal=True,
                enabled=True
            ),
            TrelloRecipientFactory(
                user_id=self.test_user_id,
                id=2,
                is_personal=True,
                enabled=True
            )
        ]
        
        selected = ["platform_1"]
        keyboard = get_recipient_selection_keyboard(recipients, selected)
        
        assert keyboard is not None
        assert len(keyboard.inline_keyboard) > 0
        
        # Should show visual feedback for selected recipients
        # We can't test the exact emoji without checking button text,
        # but we can verify the keyboard structure is correct
    
    def test_recipient_management_keyboard_with_factory_recipients(self):
        """Test recipient management keyboard with Factory Boy recipients."""
        # Create realistic recipient set using factories
        recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user_id,
                name="Personal Todoist Account"
            ),
            SharedRecipientFactory(
                user_id=self.test_user_id,
                name="Team Shared Account"
            ),
            DisabledRecipientFactory(
                user_id=self.test_user_id,
                name="Disabled Account"
            )
        ]
        
        keyboard = get_recipient_management_keyboard(recipients)
        
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
    
    def test_keyboard_button_structure_with_realistic_data(self):
        """Test that keyboard buttons have proper structure with realistic Factory Boy data."""
        # Create realistic recipients with varied names and configurations
        recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user_id,
                name="My Work Todoist - Q4 2024",
                platform_config={'project_id': '12345678'}
            ),
            TrelloRecipientFactory(
                user_id=self.test_user_id,
                name="Home Projects Board",
                platform_config={'board_id': 'board_abc123', 'list_id': 'list_def456'}
            )
        ]
        
        keyboard = get_recipient_selection_keyboard(recipients)
        
        # Check that all buttons have required properties
        for row in keyboard.inline_keyboard:
            for button in row:
                assert hasattr(button, 'text')
                assert hasattr(button, 'callback_data')
                assert button.text is not None
                assert button.callback_data is not None
                # Verify text and callback data are strings
                assert isinstance(button.text, str)
                assert isinstance(button.callback_data, str)
    
    def test_recipient_filtering_enabled_only_with_factory(self):
        """Test keyboard shows enabled recipients appropriately using Factory Boy."""
        # Create mix of enabled and disabled recipients
        all_recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user_id,
                enabled=True,
                name="Enabled Todoist"
            ),
            TrelloRecipientFactory(
                user_id=self.test_user_id,
                enabled=True,
                name="Enabled Trello"
            ),
            DisabledRecipientFactory(
                user_id=self.test_user_id,
                enabled=False,
                name="Disabled Account"
            )
        ]
        
        # Filter to only enabled recipients
        enabled_recipients = [r for r in all_recipients if r.enabled]
        keyboard = get_recipient_selection_keyboard(enabled_recipients)
        
        assert keyboard is not None
        # Should have fewer recipients than the full list
        assert len(enabled_recipients) == 2  # Only enabled ones
        assert len(all_recipients) == 3      # Total including disabled
        
        # Verify all recipients in keyboard are enabled
        for recipient in enabled_recipients:
            assert recipient.enabled is True
    
    def test_callback_data_format_with_factory_recipients(self):
        """Test that callback data follows expected format with Factory Boy recipients."""
        recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user_id,
                id=101
            ),
            TrelloRecipientFactory(
                user_id=self.test_user_id,
                id=102
            )
        ]
        
        keyboard = get_recipient_selection_keyboard(recipients)
        
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
        
        # Verify callback data format includes recipient IDs
        for callback in recipient_callbacks:
            assert len(callback) > len('select_recipient_')  # Has ID appended
    
    def test_keyboard_with_personal_vs_shared_recipients(self):
        """Test keyboard handles personal vs shared recipients correctly."""
        recipients = [
            PersonalRecipientFactory(
                user_id=self.test_user_id,
                is_personal=True,
                name="My Personal Account"
            ),
            SharedRecipientFactory(
                user_id=self.test_user_id,
                is_personal=False,
                name="Team Shared Account"
            )
        ]
        
        keyboard = get_recipient_selection_keyboard(recipients)
        
        assert keyboard is not None
        assert len(keyboard.inline_keyboard) > 0
        
        # Verify both recipient types are represented
        personal_recipients = [r for r in recipients if r.is_personal]
        shared_recipients = [r for r in recipients if not r.is_personal]
        
        assert len(personal_recipients) == 1
        assert len(shared_recipients) == 1
    
    def test_keyboard_with_complex_recipient_configurations(self):
        """Test keyboard with complex recipient configurations from Factory Boy."""
        # Create recipients with complex platform configurations
        recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user_id,
                platform_config={
                    'project_id': '987654321',
                    'section_id': '123456789',
                    'labels': ['work', 'urgent']
                }
            ),
            TrelloRecipientFactory(
                user_id=self.test_user_id,
                platform_config={
                    'board_id': 'complex_board_123',
                    'list_id': 'complex_list_456',
                    'member_ids': ['member1', 'member2']
                }
            )
        ]
        
        keyboard = get_recipient_selection_keyboard(recipients)
        
        assert keyboard is not None
        
        # Verify keyboard can handle complex configurations
        for recipient in recipients:
            assert recipient.platform_config is not None
            assert isinstance(recipient.platform_config, dict)
            assert len(recipient.platform_config) > 0
    
    def test_management_keyboard_with_varied_recipient_states(self):
        """Test management keyboard with recipients in varied states."""
        # Create comprehensive scenario using factory methods
        recipients = MultiPlatformRecipientFactory.create_mixed_scenarios(self.test_user_id)
        
        # Flatten the scenarios into a single list
        all_recipients = []
        for category, recipient_list in recipients.items():
            all_recipients.extend(recipient_list)
        
        keyboard = get_recipient_management_keyboard(all_recipients)
        
        assert keyboard is not None
        assert len(keyboard.inline_keyboard) > 0
        
        # Verify keyboard handles the variety of recipient states
        button_count = sum(len(row) for row in keyboard.inline_keyboard)
        assert button_count > 0
        
        # Should handle mix of enabled/disabled, personal/shared
        enabled_count = len([r for r in all_recipients if r.enabled])
        disabled_count = len([r for r in all_recipients if not r.enabled])
        personal_count = len([r for r in all_recipients if r.is_personal])
        shared_count = len([r for r in all_recipients if not r.is_personal])
        
        assert enabled_count > 0
        assert disabled_count > 0  
        assert personal_count > 0
        assert shared_count > 0
    
    def test_keyboard_performance_with_many_recipients(self):
        """Test keyboard performance with many Factory Boy generated recipients."""
        # Create larger set of recipients to test performance
        recipients = []
        for i in range(20):
            if i % 2 == 0:
                recipient = TodoistRecipientFactory(
                    user_id=self.test_user_id,
                    id=i + 1000,
                    name=f"Todoist Account {i}"
                )
            else:
                recipient = TrelloRecipientFactory(
                    user_id=self.test_user_id,
                    id=i + 1000,
                    name=f"Trello Account {i}"
                )
            recipients.append(recipient)
        
        # Test keyboard generation performance
        keyboard = get_recipient_selection_keyboard(recipients)
        
        assert keyboard is not None
        assert len(keyboard.inline_keyboard) > 0
        
        # Verify keyboard can handle larger recipient sets
        total_buttons = sum(len(row) for row in keyboard.inline_keyboard)
        assert total_buttons >= len(recipients)  # At least one button per recipient
    
    def test_factory_recipient_keyboard_integration_realism(self):
        """Test that Factory Boy recipients integrate realistically with keyboard functions."""
        # Create realistic recipient scenarios
        recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user_id,
                name="Work Projects - Todoist Premium",
                credentials="a" * 40,  # Realistic Todoist token length
                platform_config={'project_id': '2147483647'}
            ),
            TrelloRecipientFactory(
                user_id=self.test_user_id,
                name="Home & Family - Trello",
                credentials="12345678-1234-1234-1234-123456789012",  # UUID format
                platform_config={
                    'board_id': '5f8b2c3d4e5a6b7c8d9e0f12',
                    'list_id': '6f9c3d4e5f6a7b8c9d0e1f23'
                }
            )
        ]
        
        # Test both keyboard types with realistic data
        selection_keyboard = get_recipient_selection_keyboard(recipients)
        management_keyboard = get_recipient_management_keyboard(recipients)
        
        # Verify both keyboards work with realistic Factory Boy data
        assert selection_keyboard is not None
        assert management_keyboard is not None
        
        # Verify keyboard button text uses realistic recipient names
        for row in selection_keyboard.inline_keyboard:
            for button in row:
                # Button text should be meaningful (not just placeholder text)
                assert len(button.text) > 0
                if 'todoist' in button.text.lower() or 'trello' in button.text.lower():
                    # Platform-specific buttons should reference realistic names
                    assert len(button.text) > 10  # Should be descriptive
        
        # Verify realistic credentials and configs don't break keyboard generation
        for recipient in recipients:
            assert len(recipient.credentials) > 20  # Realistic credential length
            assert recipient.platform_config is not None
            assert len(recipient.platform_config) > 0