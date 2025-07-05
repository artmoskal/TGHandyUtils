"""Tests for modular command handlers."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

# Mock the imports before importing handlers
with patch.dict('sys.modules', {
    'bot': Mock(),
    'core.container': Mock(),
    'keyboards.recipient': Mock(),
    'states.recipient_states': Mock()
}):
    # Import after mocking
    from handlers_modular.commands import main_commands, task_commands, settings_commands, menu_commands


class TestMainCommands:
    """Test main command handlers."""
    
    @pytest.fixture
    def mock_message(self):
        """Mock Telegram message."""
        message = Mock(spec=Message)
        message.from_user.id = 12345
        message.from_user.full_name = "Test User"
        message.reply = AsyncMock()
        return message
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.clear = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_cmd_start(self, mock_message, mock_state):
        """Test /start command."""
        with patch('handlers_modular.commands.main_commands.get_main_menu_keyboard') as mock_keyboard:
            mock_keyboard.return_value = Mock()
            
            await main_commands.cmd_start(mock_message, mock_state)
            
            mock_message.reply.assert_called_once()
            call_args = mock_message.reply.call_args[0][0]
            assert "Welcome" in call_args
            assert "TGHandyUtils" in call_args
    
    @pytest.mark.asyncio
    async def test_show_recipient_management(self, mock_message, mock_state):
        """Test recipient management command."""
        mock_container = Mock()
        mock_service = Mock()
        mock_service.get_recipients_by_user.return_value = []
        mock_container.recipient_service.return_value = mock_service
        
        with patch('handlers_modular.commands.main_commands.container', mock_container):
            with patch('handlers_modular.commands.main_commands.get_recipient_management_keyboard') as mock_keyboard:
                mock_keyboard.return_value = Mock()
                
                await main_commands.show_recipient_management(mock_message, mock_state)
                
                mock_message.reply.assert_called_once()
                mock_service.get_recipients_by_user.assert_called_once_with(12345)


class TestTaskCommands:
    """Test task command handlers."""
    
    @pytest.fixture
    def mock_message(self):
        """Mock Telegram message."""
        message = Mock(spec=Message)
        message.from_user.id = 12345
        message.reply = AsyncMock()
        return message
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.set_state = AsyncMock()
        state.update_data = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_create_task_with_recipients_no_recipients(self, mock_message, mock_state):
        """Test task creation when no recipients are configured."""
        mock_container = Mock()
        mock_service = Mock()
        mock_service.get_enabled_recipients.return_value = []
        mock_container.recipient_service.return_value = mock_service
        
        with patch('handlers_modular.commands.task_commands.container', mock_container):
            await task_commands.create_task_with_recipients(mock_message, mock_state)
            
            mock_message.reply.assert_called_once()
            call_args = mock_message.reply.call_args[0][0]
            assert "No Recipients Available" in call_args


class TestSettingsCommands:
    """Test settings command handlers."""
    
    @pytest.fixture
    def mock_message(self):
        """Mock Telegram message."""
        message = Mock(spec=Message)
        message.from_user.id = 12345
        message.reply = AsyncMock()
        return message
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.clear = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_show_settings(self, mock_message, mock_state):
        """Test settings display command."""
        mock_container = Mock()
        mock_service = Mock()
        mock_prefs = Mock()
        mock_prefs.owner_name = "Test User"
        mock_prefs.location = "Test Location"
        mock_prefs.telegram_notifications = True
        mock_prefs.show_recipient_ui = True
        mock_service.get_user_preferences.return_value = mock_prefs
        mock_container.recipient_service.return_value = mock_service
        
        with patch('handlers_modular.commands.settings_commands.container', mock_container):
            with patch('handlers_modular.commands.settings_commands.get_settings_main_keyboard') as mock_keyboard:
                mock_keyboard.return_value = Mock()
                
                await settings_commands.show_settings(mock_message, mock_state)
                
                mock_message.reply.assert_called_once()
                call_args = mock_message.reply.call_args[0][0]
                assert "Your Settings" in call_args
                assert "Test User" in call_args


class TestMenuCommands:
    """Test menu command handlers."""
    
    @pytest.fixture
    def mock_message(self):
        """Mock Telegram message."""
        message = Mock(spec=Message)
        message.reply = AsyncMock()
        return message
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.clear = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_show_main_menu(self, mock_message, mock_state):
        """Test main menu display."""
        with patch('handlers_modular.commands.menu_commands.get_main_menu_keyboard') as mock_keyboard:
            mock_keyboard.return_value = Mock()
            
            await menu_commands.show_main_menu(mock_message, mock_state)
            
            mock_message.reply.assert_called_once()
            call_args = mock_message.reply.call_args[0][0]
            assert "Main Menu" in call_args
    
    @pytest.mark.asyncio
    async def test_cancel_command(self, mock_message, mock_state):
        """Test cancel command."""
        await menu_commands.cancel_command(mock_message, mock_state)
        
        mock_state.clear.assert_called_once()
        mock_message.reply.assert_called_once()
        call_args = mock_message.reply.call_args[0][0]
        assert "Cancelled" in call_args