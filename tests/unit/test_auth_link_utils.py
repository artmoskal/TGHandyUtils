"""Unit tests for auth link utilities."""

import pytest
from utils.auth_link_utils import encode_auth_request_data, decode_auth_request_data

pytestmark = pytest.mark.unit


class TestAuthLinkUtils:
    """Test cases for auth link encoding/decoding utilities."""
    
    def test_encode_decode_basic(self):
        """Test basic encoding and decoding of auth request data."""
        test_data = {
            'requester_user_id': 123456,
            'platform_type': 'todoist',
            'recipient_name': 'Work Tasks',
            'requester_name': 'Alice'
        }
        
        # Encode
        encoded = encode_auth_request_data(**test_data)
        assert isinstance(encoded, str)
        assert len(encoded) > 0
        
        # Decode
        decoded = decode_auth_request_data(encoded)
        assert decoded is not None
        assert decoded['requester_user_id'] == test_data['requester_user_id']
        assert decoded['platform_type'] == test_data['platform_type']
        assert decoded['recipient_name'] == test_data['recipient_name']
        assert decoded['requester_name'] == test_data['requester_name']
    
    def test_encode_decode_special_characters(self):
        """Test encoding/decoding with special characters."""
        test_data = {
            'requester_user_id': 999999,
            'platform_type': 'trello',
            'recipient_name': 'My Tasks & Notes!',
            'requester_name': 'User@123'
        }
        
        encoded = encode_auth_request_data(**test_data)
        decoded = decode_auth_request_data(encoded)
        
        assert decoded['recipient_name'] == test_data['recipient_name']
        assert decoded['requester_name'] == test_data['requester_name']
    
    def test_encode_decode_unicode(self):
        """Test encoding/decoding with unicode characters."""
        test_data = {
            'requester_user_id': 555555,
            'platform_type': 'google_calendar',
            'recipient_name': 'Личные задачи',
            'requester_name': 'Пользователь'
        }
        
        encoded = encode_auth_request_data(**test_data)
        decoded = decode_auth_request_data(encoded)
        
        assert decoded['recipient_name'] == test_data['recipient_name']
        assert decoded['requester_name'] == test_data['requester_name']
    
    def test_decode_invalid_data(self):
        """Test decoding invalid data returns None."""
        # Test with invalid base64
        assert decode_auth_request_data('invalid_base64!@#') is None
        
        # Test with valid base64 but invalid JSON
        import base64
        invalid_json = base64.urlsafe_b64encode(b'not json').decode('utf-8')
        assert decode_auth_request_data(invalid_json) is None
    
    def test_decode_empty_string(self):
        """Test decoding empty string returns None."""
        assert decode_auth_request_data('') is None
    
    def test_encode_missing_fields(self):
        """Test encoding with missing required fields raises error."""
        with pytest.raises(TypeError):
            encode_auth_request_data(requester_user_id=123)
    
    def test_bot_link_format(self):
        """Test that encoded data works in bot link format."""
        test_data = {
            'requester_user_id': 123456,
            'platform_type': 'todoist',
            'recipient_name': 'Tasks',
            'requester_name': 'User'
        }
        
        encoded = encode_auth_request_data(**test_data)
        bot_username = "test_bot"
        bot_link = f"https://t.me/{bot_username}?start=auth_{encoded}"
        
        # Verify link format
        assert bot_link.startswith(f"https://t.me/{bot_username}?start=auth_")
        assert len(bot_link) < 256  # Telegram URL length limit