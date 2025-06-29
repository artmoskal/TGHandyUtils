"""Tests for new functionality: owner_name, location, and new commands."""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock

from models.recipient import UserPreferencesV2, UserPreferencesV2Create, UserPreferencesV2Update
from services.recipient_service import RecipientService
from database.recipient_repositories import UserPreferencesV2Repository
from keyboards.recipient import (
    get_main_menu_keyboard, 
    get_settings_main_keyboard,
    get_notification_settings_keyboard,
    get_delete_confirmation_keyboard,
    get_back_to_settings_keyboard
)


class TestUserPreferencesV2Models:
    """Test the updated UserPreferencesV2 models with new fields."""
    
    def test_user_preferences_v2_with_new_fields(self):
        """Test UserPreferencesV2 with owner_name and location."""
        prefs = UserPreferencesV2(
            telegram_user_id=123,
            default_recipients=["platform_1"],
            show_recipient_ui=True,
            telegram_notifications=True,
            owner_name="John Doe",
            location="Portugal"
        )
        
        assert prefs.telegram_user_id == 123
        assert prefs.owner_name == "John Doe"
        assert prefs.location == "Portugal"
    
    def test_user_preferences_v2_create_with_new_fields(self):
        """Test UserPreferencesV2Create with owner_name and location."""
        prefs = UserPreferencesV2Create(
            owner_name="Jane Smith",
            location="New York"
        )
        
        assert prefs.owner_name == "Jane Smith"
        assert prefs.location == "New York"
        assert prefs.show_recipient_ui == False  # default
        assert prefs.telegram_notifications == True  # default
    
    def test_user_preferences_v2_update_with_new_fields(self):
        """Test UserPreferencesV2Update with owner_name and location."""
        updates = UserPreferencesV2Update(
            owner_name="Updated Name",
            location="Updated Location"
        )
        
        assert updates.owner_name == "Updated Name"
        assert updates.location == "Updated Location"
        assert updates.show_recipient_ui is None  # not set
        assert updates.telegram_notifications is None  # not set


class TestRecipientServiceNewMethods:
    """Test new methods in RecipientService."""
    
    @pytest.fixture
    def mock_prefs_repo(self):
        return Mock(spec=UserPreferencesV2Repository)
    
    @pytest.fixture
    def recipient_service(self, mock_prefs_repo):
        platform_repo = Mock()
        shared_repo = Mock()
        service = RecipientService(platform_repo, shared_repo, mock_prefs_repo)
        return service
    
    def test_update_owner_name_existing_preferences(self, recipient_service, mock_prefs_repo):
        """Test updating owner name for user with existing preferences."""
        # Mock existing preferences
        existing_prefs = UserPreferencesV2(
            telegram_user_id=123,
            default_recipients=[],
            show_recipient_ui=False,
            telegram_notifications=True
        )
        mock_prefs_repo.get_preferences.return_value = existing_prefs
        mock_prefs_repo.update_preferences.return_value = True
        
        result = recipient_service.update_owner_name(123, "John Doe")
        
        assert result == True
        mock_prefs_repo.get_preferences.assert_called_once_with(123)
        mock_prefs_repo.update_preferences.assert_called_once()
        
        # Check the update call
        args, kwargs = mock_prefs_repo.update_preferences.call_args
        assert args[0] == 123
        assert args[1].owner_name == "John Doe"
    
    def test_update_owner_name_no_existing_preferences(self, recipient_service, mock_prefs_repo):
        """Test updating owner name for user with no existing preferences."""
        mock_prefs_repo.get_preferences.return_value = None
        mock_prefs_repo.create_preferences.return_value = True
        
        result = recipient_service.update_owner_name(123, "Jane Smith")
        
        assert result == True
        mock_prefs_repo.get_preferences.assert_called_once_with(123)
        mock_prefs_repo.create_preferences.assert_called_once()
        
        # Check the create call
        args, kwargs = mock_prefs_repo.create_preferences.call_args
        assert args[0] == 123
        assert args[1].owner_name == "Jane Smith"
    
    def test_update_location_existing_preferences(self, recipient_service, mock_prefs_repo):
        """Test updating location for user with existing preferences."""
        existing_prefs = UserPreferencesV2(
            telegram_user_id=456,
            default_recipients=[],
            show_recipient_ui=False,
            telegram_notifications=True
        )
        mock_prefs_repo.get_preferences.return_value = existing_prefs
        mock_prefs_repo.update_preferences.return_value = True
        
        result = recipient_service.update_location(456, "Portugal")
        
        assert result == True
        mock_prefs_repo.get_preferences.assert_called_once_with(456)
        mock_prefs_repo.update_preferences.assert_called_once()
        
        # Check the update call
        args, kwargs = mock_prefs_repo.update_preferences.call_args
        assert args[0] == 456
        assert args[1].location == "Portugal"
    
    def test_update_location_no_existing_preferences(self, recipient_service, mock_prefs_repo):
        """Test updating location for user with no existing preferences."""
        mock_prefs_repo.get_preferences.return_value = None
        mock_prefs_repo.create_preferences.return_value = True
        
        result = recipient_service.update_location(456, "California")
        
        assert result == True
        mock_prefs_repo.get_preferences.assert_called_once_with(456)
        mock_prefs_repo.create_preferences.assert_called_once()
        
        # Check the create call
        args, kwargs = mock_prefs_repo.create_preferences.call_args
        assert args[0] == 456
        assert args[1].location == "California"
    
    def test_get_user_preferences(self, recipient_service, mock_prefs_repo):
        """Test get_user_preferences convenience method."""
        prefs = UserPreferencesV2(
            telegram_user_id=789,
            default_recipients=[],
            show_recipient_ui=True,
            telegram_notifications=False,
            owner_name="Test User",
            location="UK"
        )
        mock_prefs_repo.get_preferences.return_value = prefs
        
        result = recipient_service.get_user_preferences(789)
        
        assert result == prefs
        assert result.owner_name == "Test User"
        assert result.location == "UK"
        mock_prefs_repo.get_preferences.assert_called_once_with(789)
    
    def test_delete_all_user_data(self, recipient_service):
        """Test delete_all_user_data method."""
        # Mock platform and shared repositories
        recipient_service.platform_repo.get_user_platforms.return_value = []
        recipient_service.platform_repo.delete_platform.return_value = True
        recipient_service.shared_repo.get_shared_recipients.return_value = []
        recipient_service.shared_repo.delete_recipient.return_value = True
        
        # Mock preferences repo with database manager
        mock_db_manager = Mock()
        mock_conn = Mock()
        mock_context_manager = Mock()
        mock_context_manager.__enter__ = Mock(return_value=mock_conn)
        mock_context_manager.__exit__ = Mock(return_value=None)
        mock_db_manager.get_connection.return_value = mock_context_manager
        
        recipient_service.prefs_repo.db_manager = mock_db_manager
        recipient_service.prefs_repo.get_preferences.return_value = UserPreferencesV2(
            telegram_user_id=999,
            default_recipients=[],
            show_recipient_ui=False,
            telegram_notifications=True
        )
        
        result = recipient_service.delete_all_user_data(999)
        
        assert result == True
        recipient_service.platform_repo.get_user_platforms.assert_called_once_with(999)
        recipient_service.shared_repo.get_shared_recipients.assert_called_once_with(999)
        recipient_service.prefs_repo.get_preferences.assert_called_once_with(999)
        mock_conn.execute.assert_called_once()


class TestNewKeyboards:
    """Test the new keyboard functions."""
    
    def test_get_main_menu_keyboard(self):
        """Test main menu keyboard creation."""
        keyboard = get_main_menu_keyboard()
        
        assert keyboard.inline_keyboard is not None
        assert len(keyboard.inline_keyboard) == 3
        
        # Check button texts and callbacks
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        texts = [btn.text for btn in buttons]
        callbacks = [btn.callback_data for btn in buttons]
        
        assert "📱 My Accounts" in texts
        assert "📝 Create Task" in texts
        assert "⚙️ Settings" in texts
        assert "show_recipients" in callbacks
        assert "create_task" in callbacks
        assert "show_settings" in callbacks
    
    def test_get_settings_main_keyboard(self):
        """Test settings main keyboard creation."""
        keyboard = get_settings_main_keyboard()
        
        assert keyboard.inline_keyboard is not None
        assert len(keyboard.inline_keyboard) == 5
        
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        texts = [btn.text for btn in buttons]
        callbacks = [btn.callback_data for btn in buttons]
        
        assert "👤 Profile Settings" in texts
        assert "📱 Manage Accounts" in texts
        assert "🔔 Notifications" in texts
        assert "🗑️ Delete All Data" in texts
        assert "« Back to Menu" in texts
        
        assert "profile_settings" in callbacks
        assert "show_recipients" in callbacks
        assert "notification_settings" in callbacks
        assert "confirm_delete_data" in callbacks
        assert "back_to_menu" in callbacks
    
    def test_get_notification_settings_keyboard(self):
        """Test notification settings keyboard creation."""
        keyboard = get_notification_settings_keyboard()
        
        assert keyboard.inline_keyboard is not None
        assert len(keyboard.inline_keyboard) == 3
        
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        callbacks = [btn.callback_data for btn in buttons]
        
        assert "toggle_telegram_notifications" in callbacks
        assert "toggle_recipient_ui" in callbacks
        assert "back_to_settings" in callbacks
    
    def test_get_delete_confirmation_keyboard(self):
        """Test delete confirmation keyboard creation."""
        keyboard = get_delete_confirmation_keyboard()
        
        assert keyboard.inline_keyboard is not None
        assert len(keyboard.inline_keyboard) == 1
        assert len(keyboard.inline_keyboard[0]) == 2
        
        buttons = keyboard.inline_keyboard[0]
        assert "🗑️ DELETE ALL DATA" in buttons[0].text
        assert "❌ Cancel" in buttons[1].text
        assert buttons[0].callback_data == "delete_all_data_confirmed"
        assert buttons[1].callback_data == "back_to_settings"
    
    def test_get_back_to_settings_keyboard(self):
        """Test back to settings keyboard creation."""
        keyboard = get_back_to_settings_keyboard()
        
        assert keyboard.inline_keyboard is not None
        assert len(keyboard.inline_keyboard) == 1
        assert len(keyboard.inline_keyboard[0]) == 1
        
        button = keyboard.inline_keyboard[0][0]
        assert "« Back to Settings" in button.text
        assert button.callback_data == "back_to_settings"


class TestParsingServiceEnhancement:
    """Test parsing service with enhanced owner_name prompt."""
    
    @pytest.fixture
    def mock_config(self):
        config = Mock()
        config.OPENAI_API_KEY = "test_key"
        return config
    
    def test_prompt_template_includes_owner_name_instructions(self, mock_config):
        """Test that the prompt template includes owner_name instructions."""
        from services.parsing_service import ParsingService
        
        service = ParsingService(mock_config)
        template = service.prompt_template.template
        
        # Check that owner_name instructions are included
        assert "When creating the task title and description, consider that this task belongs to {owner_name}" in template
        assert "Personal context (e.g., \"call mom\" vs \"call John's mom\")" in template
        assert "Personalized task descriptions" in template
        assert "Resolving ambiguous references in the conversation" in template
        
        # Check that owner_name is in input variables
        assert "owner_name" in service.prompt_template.input_variables
    
    @patch('services.parsing_service.ChatOpenAI')
    @patch('services.parsing_service.PydanticOutputParser')
    def test_parse_content_to_task_uses_owner_name(self, mock_parser_class, mock_llm_class, mock_config):
        """Test that parse_content_to_task uses owner_name in the prompt."""
        from services.parsing_service import ParsingService
        
        # Mock the LLM and parser
        mock_llm = Mock()
        mock_llm_class.return_value = mock_llm
        mock_parser = Mock()
        mock_parser_class.return_value = mock_parser
        
        # Mock LLM response - it should return a single message, not a list
        mock_message = Mock()
        mock_message.content = '{"title": "Test Task", "description": "Test", "due_time": "2025-06-28T10:00:00Z"}'
        mock_llm.return_value = mock_message  # Single message, not list
        
        # Mock parser response
        mock_task = Mock()
        mock_task.dict.return_value = {
            "title": "Test Task",
            "description": "Test",
            "due_time": "2025-06-28T10:00:00Z"
        }
        mock_parser.parse.return_value = mock_task
        
        service = ParsingService(mock_config)
        
        result = service.parse_content_to_task(
            "Test message",
            owner_name="John Doe",
            location="Portugal"
        )
        
        # Verify that the LLM was called
        mock_llm.assert_called_once()
        
        # Get the prompt that was sent to the LLM
        call_args = mock_llm.call_args[0][0]
        prompt_content = call_args[0].content
        
        # Check that owner_name was included in the prompt
        assert "John Doe" in prompt_content
        assert "Test message" in prompt_content
        assert "Portugal" in prompt_content
        
        # Check that the result is correct
        assert result["title"] == "Test Task"