"""Tests for user tracking middleware."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from aiogram.types import Message, CallbackQuery, User

from handlers_modular.middleware.user_tracking import UserTrackingMiddleware


pytestmark = pytest.mark.unit
class TestUserTrackingMiddleware:
    """Test cases for user tracking middleware."""
    
    @pytest.fixture
    def middleware(self):
        """Create middleware instance."""
        return UserTrackingMiddleware()
    
    @pytest.fixture
    def mock_message(self):
        """Create mock message with user."""
        message = Mock(spec=Message)
        user = Mock(spec=User)
        user.id = 123456
        user.username = "testuser"
        user.first_name = "Test"
        user.last_name = "User"
        message.from_user = user
        return message
    
    @pytest.fixture
    def mock_callback_query(self):
        """Create mock callback query with user."""
        query = Mock(spec=CallbackQuery)
        user = Mock(spec=User)
        user.id = 789012
        user.username = "callbackuser"
        user.first_name = "Callback"
        user.last_name = "User"
        query.from_user = user
        return query
    
    @pytest.fixture
    def mock_handler(self):
        """Create mock handler."""
        handler = AsyncMock()
        handler.return_value = "handler_result"
        return handler
    
    @pytest.mark.asyncio
    async def test_middleware_tracks_message_user(self, middleware, mock_message, mock_handler):
        """Test middleware tracks user from message."""
        with patch('handlers_modular.middleware.user_tracking.container') as mock_container:
            # Setup mock user service
            mock_user_service = Mock()
            mock_container.user_service.return_value = mock_user_service
            
            # Call middleware
            result = await middleware(mock_handler, mock_message, {})
            
            # Verify user was tracked
            mock_user_service.track_user.assert_called_once_with(mock_message.from_user)
            
            # Verify handler was called
            mock_handler.assert_called_once_with(mock_message, {})
            assert result == "handler_result"
    
    @pytest.mark.asyncio
    async def test_middleware_tracks_callback_user(self, middleware, mock_callback_query, mock_handler):
        """Test middleware tracks user from callback query."""
        with patch('handlers_modular.middleware.user_tracking.container') as mock_container:
            # Setup mock user service
            mock_user_service = Mock()
            mock_container.user_service.return_value = mock_user_service
            
            # Call middleware
            result = await middleware(mock_handler, mock_callback_query, {})
            
            # Verify user was tracked
            mock_user_service.track_user.assert_called_once_with(mock_callback_query.from_user)
            
            # Verify handler was called
            mock_handler.assert_called_once_with(mock_callback_query, {})
            assert result == "handler_result"
    
    @pytest.mark.asyncio
    async def test_middleware_handles_no_user(self, middleware, mock_handler):
        """Test middleware handles events without user."""
        # Create event without from_user
        event = Mock()
        event.from_user = None
        
        with patch('handlers_modular.middleware.user_tracking.container') as mock_container:
            mock_user_service = Mock()
            mock_container.user_service.return_value = mock_user_service
            
            # Call middleware
            result = await middleware(mock_handler, event, {})
            
            # Verify user was NOT tracked
            mock_user_service.track_user.assert_not_called()
            
            # Verify handler was still called
            mock_handler.assert_called_once_with(event, {})
            assert result == "handler_result"
    
    @pytest.mark.asyncio
    async def test_middleware_handles_tracking_error(self, middleware, mock_message, mock_handler):
        """Test middleware handles errors in user tracking gracefully."""
        with patch('handlers_modular.middleware.user_tracking.container') as mock_container:
            # Setup mock user service that raises error
            mock_user_service = Mock()
            mock_user_service.track_user.side_effect = Exception("Database error")
            mock_container.user_service.return_value = mock_user_service
            
            # Call middleware - should not raise
            result = await middleware(mock_handler, mock_message, {})
            
            # Verify handler was still called despite tracking error
            mock_handler.assert_called_once_with(mock_message, {})
            assert result == "handler_result"
    
    @pytest.mark.asyncio 
    async def test_middleware_with_custom_event(self, middleware, mock_handler):
        """Test middleware with custom event type that has from_user."""
        # Create custom event with from_user
        custom_event = Mock()
        user = Mock(spec=User)
        user.id = 555555
        user.username = "customevent"
        custom_event.from_user = user
        
        with patch('handlers_modular.middleware.user_tracking.container') as mock_container:
            mock_user_service = Mock()
            mock_container.user_service.return_value = mock_user_service
            
            # Call middleware
            result = await middleware(mock_handler, custom_event, {})
            
            # Verify user was tracked
            mock_user_service.track_user.assert_called_once_with(user)
            
            # Verify handler was called
            mock_handler.assert_called_once_with(custom_event, {})
            assert result == "handler_result"