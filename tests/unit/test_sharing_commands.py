"""Tests for sharing command handlers."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
from aiogram.types import Message, User, Chat
from aiogram.fsm.context import FSMContext

from handlers_modular.commands.sharing_commands import (
    handle_requests_command,
    handle_share_command
)
from models.auth_request import AuthRequest


@pytest.fixture
def mock_message():
    """Create mock message."""
    message = Mock(spec=Message)
    message.from_user = Mock(spec=User)
    message.from_user.id = 123456
    message.from_user.username = "testuser"
    message.from_user.first_name = "Test"
    message.from_user.last_name = "User"
    message.chat = Mock(spec=Chat)
    message.chat.id = 123456
    message.reply = AsyncMock()
    message.answer = AsyncMock()
    return message


@pytest.fixture
def mock_state():
    """Create mock FSM state."""
    state = Mock(spec=FSMContext)
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    return state


class TestSharingCommands:
    """Test sharing command handlers."""
    
    @pytest.mark.asyncio
    async def test_handle_requests_command_no_requests(self, mock_message):
        """Test /requests command with no pending requests."""
        with patch('handlers_modular.commands.sharing_commands.logger'):
            with patch('handlers_modular.commands.sharing_commands.container') as mock_container:
                # Setup mocks
                mock_repo = Mock()
                mock_repo.get_auth_requests_by_requester.return_value = []
                mock_repo.get_pending_auth_requests.return_value = []
                
                mock_container.unified_recipient_repository.return_value = mock_repo
                mock_container.sharing_service.return_value = Mock()
                
                # Create mock state
                mock_state = Mock()
                mock_state.clear = AsyncMock()
                
                # Call handler
                await handle_requests_command(mock_message, mock_state)
                
                # Verify response
                mock_message.reply.assert_called_once()
                call_args = mock_message.reply.call_args
                # Check positional args first, then kwargs
                if call_args[0]:
                    assert "No Authentication Requests" in call_args[0][0]
                else:
                    assert "No Authentication Requests" in call_args[1]['text']
                    assert call_args[1]['parse_mode'] == 'Markdown'
    
    @pytest.mark.asyncio
    async def test_handle_requests_command_with_requests(self, mock_message):
        """Test /requests command with pending requests."""
        with patch('handlers_modular.commands.sharing_commands.logger'):
            with patch('handlers_modular.commands.sharing_commands.container') as mock_container:
                # Setup auth requests
                received_req = AuthRequest(
                    id=1,
                    requester_user_id=999,
                    target_user_id=123456,
                    platform_type="todoist",
                    recipient_name="Test Account",
                    status="pending",
                    expires_at=datetime.now() + timedelta(hours=24),
                    completed_recipient_id=None,
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                
                sent_req = AuthRequest(
                    id=2,
                    requester_user_id=123456,
                    target_user_id=888,
                    platform_type="trello",
                    recipient_name="Another Account",
                    status="pending",
                    expires_at=datetime.now() + timedelta(hours=24),
                    completed_recipient_id=None,
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                
                # Setup mocks
                mock_repo = Mock()
                mock_repo.get_auth_requests_by_requester.return_value = [sent_req]
                mock_repo.get_pending_auth_requests.return_value = [received_req]
                
                mock_user_service = Mock()
                mock_user_service.get_user_display_name.side_effect = lambda uid: f"User{uid}"
                
                mock_container.unified_recipient_repository.return_value = mock_repo
                mock_container.sharing_service.return_value = Mock()
                mock_container.user_service.return_value = mock_user_service
                
                # Create mock state
                mock_state = Mock()
                mock_state.clear = AsyncMock()
                
                # Call handler
                await handle_requests_command(mock_message, mock_state)
                
                # Verify response
                mock_message.reply.assert_called_once()
                call_args = mock_message.reply.call_args
                # Check the actual call arguments
                text = call_args[0][0] if call_args[0] else call_args[1]['text']
                assert "Authentication Requests" in text
                assert "Received Requests" in text
                assert "Sent Requests" in text
                assert "Todoist" in text
                assert "Trello" in text
                # Check for reply markup in kwargs
                if call_args[1] and 'reply_markup' in call_args[1]:
                    assert call_args[1]['reply_markup'] is not None
    
    @pytest.mark.asyncio
    async def test_handle_requests_command_error_handling(self, mock_message):
        """Test /requests command error handling."""
        with patch('handlers_modular.commands.sharing_commands.logger'):
            with patch('handlers_modular.commands.sharing_commands.container') as mock_container:
                # Make it raise an error
                mock_container.unified_recipient_repository.side_effect = Exception("Database error")
                
                # Create mock state
                mock_state = Mock()
                mock_state.clear = AsyncMock()
                
                # Call handler
                await handle_requests_command(mock_message, mock_state)
                
                # Should reply with error message
                mock_message.reply.assert_called_once_with("❌ Error loading requests.")
    
    @pytest.mark.asyncio
    async def test_handle_share_command(self, mock_message, mock_state):
        """Test /share command."""
        with patch('handlers_modular.commands.sharing_commands.container') as mock_container:
            # Setup mock user service
            mock_user_service = Mock()
            mock_user_service.track_user = Mock()
            mock_container.user_service.return_value = mock_user_service
            
            # Call handler
            await handle_share_command(mock_message, mock_state)
            
            # Verify user was tracked
            mock_user_service.track_user.assert_called_once_with(mock_message.from_user)
            
            # Verify response
            mock_message.reply.assert_called_once()
            call_args = mock_message.reply.call_args
            text = call_args[0][0] if call_args[0] else call_args[1]['text']
            assert "Account Sharing Options" in text
            # Check for reply markup in kwargs
            if call_args[1] and 'reply_markup' in call_args[1]:
                assert call_args[1]['reply_markup'] is not None
            
            # Verify state was set
            mock_state.set_state.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_commands_registered_on_router(self):
        """Test that commands are registered on the correct router."""
        from bot import router
        
        # Find command handlers
        requests_handler = None
        share_handler = None
        
        for handler in router.message.handlers:
            if handler.filters:
                for f in handler.filters:
                    if hasattr(f.callback, 'commands'):
                        if 'requests' in f.callback.commands:
                            requests_handler = handler
                        elif 'share' in f.callback.commands:
                            share_handler = handler
        
        # Verify handlers are registered
        assert requests_handler is not None, "/requests handler not found on router"
        assert share_handler is not None, "/share handler not found on router"
        
        # Verify they're the correct functions
        assert requests_handler.callback.__name__ == 'handle_requests_command'
        assert share_handler.callback.__name__ == 'handle_share_command'