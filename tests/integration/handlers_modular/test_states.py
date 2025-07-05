"""Tests for modular state handlers."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

# Mock the imports before importing handlers
with patch.dict('sys.modules', {
    'bot': Mock(),
    'core.container': Mock(),
    'keyboards.recipient': Mock(),
    'states.recipient_states': Mock(),
    'core.logging': Mock(),
    'models.task': Mock(),
    'core.initialization': Mock()
}):
    # Import after mocking
    from handlers_modular.states import recipient_setup, task_creation, settings_input


class TestRecipientSetupStates:
    """Test recipient setup state handlers."""
    
    @pytest.fixture
    def mock_message(self):
        """Mock Telegram message."""
        message = Mock(spec=Message)
        message.from_user.id = 12345
        message.text = "test_credentials"
        message.reply = AsyncMock()
        return message
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.get_data = AsyncMock()
        state.update_data = AsyncMock()
        state.clear = AsyncMock()
        state.set_state = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_handle_credentials_input_todoist(self, mock_message, mock_state):
        """Test credentials input for Todoist."""
        mock_state.get_data.return_value = {
            'platform_type': 'todoist',
            'mode': 'user_platform'
        }
        
        mock_container = Mock()
        mock_service = Mock()
        mock_service.add_personal_recipient.return_value = 1
        mock_container.recipient_service.return_value = mock_service
        
        with patch('handlers_modular.states.recipient_setup.container', mock_container):
            await recipient_setup.handle_credentials_input(mock_message, mock_state)
            
            mock_service.add_personal_recipient.assert_called_once()
            mock_message.reply.assert_called_once()
            mock_state.clear.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_recipient_name(self, mock_message, mock_state):
        """Test recipient name input."""
        mock_message.text = "Test Recipient"
        
        with patch('handlers_modular.states.recipient_setup.get_platform_selection_keyboard') as mock_keyboard:
            mock_keyboard.return_value = Mock()
            
            await recipient_setup.handle_recipient_name(mock_message, mock_state)
            
            mock_state.update_data.assert_called_once_with(
                recipient_name="Test Recipient", 
                mode="shared_recipient"
            )
            mock_message.reply.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_recipient_name_empty(self, mock_message, mock_state):
        """Test recipient name input with empty name."""
        mock_message.text = ""
        
        await recipient_setup.handle_recipient_name(mock_message, mock_state)
        
        mock_message.reply.assert_called_once()
        call_args = mock_message.reply.call_args[0][0]
        assert "cannot be empty" in call_args


class TestTaskCreationStates:
    """Test task creation state handlers."""
    
    @pytest.fixture
    def mock_message(self):
        """Mock Telegram message."""
        message = Mock(spec=Message)
        message.from_user.id = 12345
        message.text = "Test task description"
        message.reply = AsyncMock()
        return message
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.get_data = AsyncMock()
        state.clear = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_handle_task_creation_success(self, mock_message, mock_state):
        """Test successful task creation."""
        mock_state.get_data.return_value = {"selected_recipients": ["1", "2"]}
        
        # Mock services
        mock_parsing_service = Mock()
        mock_parsing_service.parse_content_to_task.return_value = {
            'title': 'Test Task',
            'description': 'Test task description',
            'due_time': '2025-06-12T11:00:00Z'
        }
        
        mock_recipient_service = Mock()
        mock_prefs = Mock()
        mock_prefs.owner_name = "Test User"
        mock_prefs.location = "Test Location"
        mock_recipient_service.get_user_preferences.return_value = mock_prefs
        mock_recipient_service.get_recipient_by_id.return_value = Mock(enabled=True)
        
        mock_task_service = Mock()
        mock_task_service.create_task_for_recipients.return_value = (True, "Task created", {})
        
        mock_container = Mock()
        mock_container.recipient_service.return_value = mock_recipient_service
        mock_container.recipient_task_service.return_value = mock_task_service
        
        mock_services = Mock()
        mock_services.get_parsing_service.return_value = mock_parsing_service
        
        with patch('handlers_modular.states.task_creation.container', mock_container):
            with patch('handlers_modular.states.task_creation.services', mock_services):
                with patch('handlers_modular.states.task_creation.handle_task_creation_response') as mock_response:
                    await task_creation.handle_task_creation(mock_message, mock_state)
                    
                    mock_state.clear.assert_called_once()
                    mock_response.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_task_creation_empty_description(self, mock_message, mock_state):
        """Test task creation with empty description."""
        mock_message.text = ""
        
        await task_creation.handle_task_creation(mock_message, mock_state)
        
        mock_message.reply.assert_called_once()
        call_args = mock_message.reply.call_args[0][0]
        assert "cannot be empty" in call_args


class TestSettingsInputStates:
    """Test settings input state handlers."""
    
    @pytest.fixture
    def mock_message(self):
        """Mock Telegram message."""
        message = Mock(spec=Message)
        message.from_user.id = 12345
        message.text = "Test User"
        message.reply = AsyncMock()
        return message
    
    @pytest.fixture
    def mock_state(self):
        """Mock FSM state."""
        state = Mock(spec=FSMContext)
        state.clear = AsyncMock()
        return state
    
    @pytest.mark.asyncio
    async def test_handle_owner_name_input_success(self, mock_message, mock_state):
        """Test successful owner name update."""
        mock_container = Mock()
        mock_service = Mock()
        mock_service.update_owner_name.return_value = True
        mock_container.recipient_service.return_value = mock_service
        
        with patch('handlers_modular.states.settings_input.container', mock_container):
            with patch('handlers_modular.states.settings_input.get_back_to_settings_keyboard') as mock_keyboard:
                mock_keyboard.return_value = Mock()
                
                await settings_input.handle_owner_name_input(mock_message, mock_state)
                
                mock_service.update_owner_name.assert_called_once_with(12345, "Test User")
                mock_message.reply.assert_called_once()
                mock_state.clear.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_owner_name_input_empty(self, mock_message, mock_state):
        """Test owner name input with empty name."""
        mock_message.text = ""
        
        await settings_input.handle_owner_name_input(mock_message, mock_state)
        
        mock_message.reply.assert_called_once()
        call_args = mock_message.reply.call_args[0][0]
        assert "cannot be empty" in call_args
    
    @pytest.mark.asyncio
    async def test_handle_location_input_success(self, mock_message, mock_state):
        """Test successful location update."""
        mock_message.text = "Test Location"
        
        mock_container = Mock()
        mock_service = Mock()
        mock_service.update_location.return_value = True
        mock_container.recipient_service.return_value = mock_service
        
        with patch('handlers_modular.states.settings_input.container', mock_container):
            with patch('handlers_modular.states.settings_input.get_back_to_settings_keyboard') as mock_keyboard:
                mock_keyboard.return_value = Mock()
                
                await settings_input.handle_location_input(mock_message, mock_state)
                
                mock_service.update_location.assert_called_once_with(12345, "Test Location")
                mock_message.reply.assert_called_once()
                mock_state.clear.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_location_input_failure(self, mock_message, mock_state):
        """Test location update failure."""
        mock_message.text = "Test Location"
        
        mock_container = Mock()
        mock_service = Mock()
        mock_service.update_location.return_value = False
        mock_container.recipient_service.return_value = mock_service
        
        with patch('handlers_modular.states.settings_input.container', mock_container):
            await settings_input.handle_location_input(mock_message, mock_state)
            
            mock_service.update_location.assert_called_once_with(12345, "Test Location")
            mock_message.reply.assert_called_once()
            call_args = mock_message.reply.call_args[0][0]
            assert "Failed to update" in call_args