import pytest
import sqlite3
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

from services.oauth_state_manager import OAuthStateManager

@pytest.fixture
def mock_db_manager():
    """Create a mock database manager."""
    db_manager = Mock()
    conn = Mock(spec=sqlite3.Connection)
    cursor = Mock()
    conn.execute.return_value = cursor
    conn.commit = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=None)
    db_manager.get_connection.return_value = conn
    return db_manager

def test_create_pending_request(mock_db_manager):
    """Test creating a pending OAuth request."""
    manager = OAuthStateManager(mock_db_manager)
    
    state = manager.create_pending_request(123)
    
    # Verify state format
    assert state.startswith("123_")
    assert len(state.split("_")) == 2
    assert len(state.split("_")[1]) == 8  # UUID part
    
    # Verify database calls
    conn = mock_db_manager.get_connection.return_value
    assert conn.execute.call_count == 2  # DELETE + INSERT
    conn.commit.assert_called_once()

def test_complete_oauth_request_valid(mock_db_manager):
    """Test completing OAuth request with valid state."""
    manager = OAuthStateManager(mock_db_manager)
    
    # Mock fetchone to return a valid user
    cursor = Mock()
    cursor.fetchone.return_value = (123,)  # Valid user_id
    conn = mock_db_manager.get_connection.return_value
    conn.execute.return_value = cursor
    
    user_id = manager.complete_oauth_request("123_abcd1234", "auth_code")
    
    assert user_id == 123
    assert conn.execute.call_count == 2  # SELECT + UPDATE
    conn.commit.assert_called_once()

def test_complete_oauth_request_invalid_state(mock_db_manager):
    """Test completing OAuth request with invalid state format."""
    manager = OAuthStateManager(mock_db_manager)
    
    user_id = manager.complete_oauth_request("invalid_state_format", "auth_code")
    
    assert user_id is None

def test_complete_oauth_request_expired(mock_db_manager):
    """Test completing OAuth request with expired state."""
    manager = OAuthStateManager(mock_db_manager)
    
    # Mock fetchone to return None (expired/invalid)
    cursor = Mock()
    cursor.fetchone.return_value = None
    conn = mock_db_manager.get_connection.return_value
    conn.execute.return_value = cursor
    
    user_id = manager.complete_oauth_request("123_abcd1234", "auth_code")
    
    assert user_id is None

def test_get_oauth_code(mock_db_manager):
    """Test retrieving OAuth code."""
    manager = OAuthStateManager(mock_db_manager)
    
    # Mock fetchone to return code and state
    cursor = Mock()
    cursor.fetchone.return_value = ("auth_code_123", "123_abcd1234")
    conn = mock_db_manager.get_connection.return_value
    conn.execute.return_value = cursor
    
    code = manager.get_oauth_code(123)
    
    assert code == "auth_code_123"
    assert conn.execute.call_count == 2  # SELECT + DELETE
    conn.commit.assert_called_once()

def test_get_oauth_code_not_found(mock_db_manager):
    """Test retrieving OAuth code when none exists."""
    manager = OAuthStateManager(mock_db_manager)
    
    # Mock fetchone to return None
    cursor = Mock()
    cursor.fetchone.return_value = None
    conn = mock_db_manager.get_connection.return_value
    conn.execute.return_value = cursor
    
    code = manager.get_oauth_code(123)
    
    assert code is None