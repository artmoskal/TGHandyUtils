"""Tests for modular callback handlers."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

# Mock the imports before importing handlers
with patch.dict('sys.modules', {
    'bot': Mock(),
    'core.container': Mock(),
    'keyboards.recipient': Mock(),
    'states.recipient_states': Mock(),
    'core.logging': Mock()
}):
    # Import after mocking
    from handlers_modular.callbacks.recipient import management
    from handlers_modular.callbacks.task import actions
    from handlers_modular.callbacks.settings import profile, notifications
    from handlers_modular.callbacks.navigation import menus


class TestRecipientCallbacks:
    """Test recipient management callbacks."""
    
    @pytest.fixture
    def mock_callback_query(self):
        """Mock callback query."""
        callback = Mock(spec=CallbackQuery)
        callback.from_user.id = 12345
        callback.data = "add_user_platform"
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        return callback
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.set_state = AsyncMock()
        state.update_data = AsyncMock()
        state.get_data = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_add_user_platform(self, mock_callback_query, mock_state):
        """Test add user platform callback."""
        with patch('handlers_modular.callbacks.recipient.management.get_platform_selection_keyboard') as mock_keyboard:
            mock_keyboard.return_value = Mock()
            
            await management.add_user_platform(mock_callback_query, mock_state)
            
            mock_callback_query.message.edit_text.assert_called_once()
            args = mock_callback_query.message.edit_text.call_args[0]
            assert "Add Your Account" in args[0]
            mock_state.set_state.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_recipient_selection(self, mock_callback_query, mock_state):
        """Test recipient selection callback."""
        mock_callback_query.data = "select_recipient_123"
        mock_state.get_data.return_value = {"selected_recipients": []}
        
        mock_container = Mock()
        mock_service = Mock()
        mock_service.get_enabled_recipients.return_value = []
        mock_container.recipient_service.return_value = mock_service
        
        with patch('handlers_modular.callbacks.recipient.management.container', mock_container):
            with patch('handlers_modular.callbacks.recipient.management.get_recipient_selection_keyboard') as mock_keyboard:
                mock_keyboard.return_value = Mock()
                
                await management.handle_recipient_selection(mock_callback_query, mock_state)
                
                mock_state.update_data.assert_called_once()
                mock_callback_query.message.edit_text.assert_called_once()


class TestTaskCallbacks:
    """Test task action callbacks."""
    
    @pytest.fixture
    def mock_callback_query(self):
        """Mock callback query."""
        callback = Mock(spec=CallbackQuery)
        callback.from_user.id = 12345
        callback.data = "cancel_task"
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        return callback
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.clear = AsyncMock()
        state.get_data = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_cancel_task(self, mock_callback_query, mock_state):
        """Test cancel task callback."""
        await actions.cancel_task(mock_callback_query, mock_state)
        
        mock_state.clear.assert_called_once()
        mock_callback_query.message.edit_text.assert_called_once()
        args = mock_callback_query.message.edit_text.call_args[0]
        assert "cancelled" in args[0].lower()
    
    @pytest.mark.asyncio
    async def test_handle_task_actions_done(self, mock_callback_query, mock_state):
        """Test task actions done callback."""
        await actions.handle_task_actions_done(mock_callback_query)
        
        mock_callback_query.message.edit_reply_markup.assert_called_once()
        mock_callback_query.answer.assert_called_once_with("✅ Done!")


class TestSettingsCallbacks:
    """Test settings callbacks."""
    
    @pytest.fixture
    def mock_callback_query(self):
        """Mock callback query."""
        callback = Mock(spec=CallbackQuery)
        callback.from_user.id = 12345
        callback.data = "profile_settings"
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        return callback
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.set_state = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_profile_settings_callback(self, mock_callback_query, mock_state):
        """Test profile settings callback."""
        with patch('handlers_modular.callbacks.settings.profile.get_profile_settings_keyboard') as mock_keyboard:
            mock_keyboard.return_value = Mock()
            
            await profile.profile_settings_callback(mock_callback_query, mock_state)
            
            mock_callback_query.message.edit_text.assert_called_once()
            args = mock_callback_query.message.edit_text.call_args[0]
            assert "Profile Settings" in args[0]


class TestNavigationCallbacks:
    """Test navigation callbacks."""
    
    @pytest.fixture
    def mock_callback_query(self):
        """Mock callback query."""
        callback = Mock(spec=CallbackQuery)
        callback.from_user.id = 12345
        callback.data = "back_to_menu"
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        return callback
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        return state
    
    @pytest.mark.asyncio
    async def test_back_to_menu(self, mock_callback_query, mock_state):
        """Test back to menu callback."""
        with patch('handlers_modular.callbacks.navigation.menus.get_main_menu_keyboard') as mock_keyboard:
            mock_keyboard.return_value = Mock()
            
            await menus.back_to_menu(mock_callback_query, mock_state)
            
            mock_callback_query.message.edit_text.assert_called_once()
            args = mock_callback_query.message.edit_text.call_args[0]
            assert "Main Menu" in args[0]
    
    @pytest.mark.asyncio
    async def test_show_settings_callback(self, mock_callback_query, mock_state):
        """Test show settings callback."""
        mock_container = Mock()
        mock_service = Mock()
        mock_prefs = Mock()
        mock_prefs.owner_name = "Test User"
        mock_prefs.location = "Test Location"
        mock_prefs.telegram_notifications = True
        mock_prefs.show_recipient_ui = True
        mock_service.get_user_preferences.return_value = mock_prefs
        mock_container.recipient_service.return_value = mock_service
        
        with patch('handlers_modular.callbacks.navigation.menus.container', mock_container):
            with patch('handlers_modular.callbacks.navigation.menus.get_settings_main_keyboard') as mock_keyboard:
                mock_keyboard.return_value = Mock()
                
                await menus.show_settings_callback(mock_callback_query, mock_state)
                
                mock_callback_query.message.edit_text.assert_called_once()
                args = mock_callback_query.message.edit_text.call_args[0]
                assert "Your Settings" in args[0]