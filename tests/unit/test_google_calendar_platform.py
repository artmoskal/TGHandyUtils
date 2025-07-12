import json
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

from platforms.google_calendar import GoogleCalendarPlatform
from core.exceptions import PlatformError, OAuthError

@pytest.fixture
def mock_credentials():
    return json.dumps({
        'token': 'test_token',
        'refresh_token': 'test_refresh_token',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'test_client_id',
        'client_secret': 'test_client_secret',
        'scopes': ['https://www.googleapis.com/auth/calendar'],
        'expiry': (datetime.utcnow() + timedelta(hours=1)).isoformat()
    })

@patch('platforms.google_calendar.build')
@patch('platforms.google_calendar.Credentials')
def test_google_calendar_create_task_success(mock_credentials_class, mock_build, mock_credentials):
    """Test successful Google Calendar task creation."""
    # Setup mocks
    mock_creds_instance = Mock()
    mock_creds_instance.expired = False
    mock_credentials_class.return_value = mock_creds_instance
    
    mock_service = Mock()
    mock_events = Mock()
    mock_insert = Mock()
    mock_execute = Mock()
    
    mock_execute.return_value = {'id': 'test_event_id'}
    mock_insert.return_value.execute = mock_execute
    mock_events.return_value.insert = mock_insert
    mock_service.events = mock_events
    mock_build.return_value = mock_service
    
    platform = GoogleCalendarPlatform(mock_credentials)
    
    task_data = {
        'title': 'Test Calendar Event',
        'description': 'Test Description',
        'due_time': '2024-01-01T10:00:00+00:00',
        'timezone': 'UTC'
    }
    
    result = platform.create_task(task_data)
    
    assert result == 'test_event_id'
    mock_insert.assert_called_once()
    
    # Verify event structure
    call_args = mock_insert.call_args
    assert call_args[1]['calendarId'] == 'primary'
    
    event = call_args[1]['body']
    assert event['summary'] == 'Test Calendar Event'
    assert event['description'] == 'Test Description'
    assert 'start' in event
    assert 'end' in event

@patch('platforms.google_calendar.build')
@patch('platforms.google_calendar.Credentials')
def test_google_calendar_token_refresh(mock_credentials_class, mock_build, mock_credentials):
    """Test automatic token refresh."""
    # Setup expired credentials
    mock_creds_instance = Mock()
    mock_creds_instance.expired = True
    mock_creds_instance.refresh_token = 'refresh_token'
    mock_credentials_class.return_value = mock_creds_instance
    
    mock_service = Mock()
    mock_build.return_value = mock_service
    
    platform = GoogleCalendarPlatform(mock_credentials)
    
    # Verify refresh was called
    mock_creds_instance.refresh.assert_called_once()
    assert platform._credentials_refreshed is True

@patch('platforms.google_calendar.build')
@patch('platforms.google_calendar.Credentials')
def test_google_calendar_invalid_credentials(mock_credentials_class, mock_build):
    """Test handling of invalid credentials."""
    mock_credentials_class.side_effect = Exception("Invalid credentials")
    
    with pytest.raises(PlatformError) as exc_info:
        GoogleCalendarPlatform('{"invalid": "json"}')
    
    assert "Invalid Google Calendar credentials" in str(exc_info.value)

def test_google_calendar_is_configured_static():
    """Test static configuration check."""
    # Valid configuration
    valid_config = {
        'credentials': json.dumps({
            'token': 'test_token',
            'refresh_token': 'refresh_token',
            'client_id': 'client_id',
            'client_secret': 'client_secret'
        })
    }
    
    assert GoogleCalendarPlatform.is_configured_static(valid_config) is True
    
    # Invalid configuration
    invalid_config = {'credentials': '{"incomplete": "config"}'}
    assert GoogleCalendarPlatform.is_configured_static(invalid_config) is False
    
    # Missing credentials
    empty_config = {}
    assert GoogleCalendarPlatform.is_configured_static(empty_config) is False