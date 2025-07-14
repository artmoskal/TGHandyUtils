"""Unit tests for timezone-agnostic LLM parsing."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone, timedelta

from services.parsing_service import ParsingService
from models.unified_recipient import UnifiedUserPreferences, UnifiedUserPreferencesUpdate
from core.exceptions import ParsingError


class TestTimezoneAgnosticParsing:
    """Test that LLM parsing is timezone-agnostic."""
    
    @pytest.fixture
    def mock_config(self):
        """Mock configuration."""
        config = Mock()
        config.OPENAI_API_KEY = "test-key"
        # Mock the ChatOpenAI import to avoid real OpenAI initialization
        return config
    
    @pytest.fixture
    def mock_preferences_repo(self):
        """Mock user preferences repository."""
        return Mock()
    
    @pytest.fixture
    def parsing_service(self, mock_config, mock_preferences_repo):
        """Create parsing service with mocked dependencies."""
        with patch('services.parsing_service.ChatOpenAI') as mock_chat:
            mock_llm = Mock()
            mock_chat.return_value = mock_llm
            service = ParsingService(mock_config, preferences_repo=mock_preferences_repo)
            return service
    
    def test_llm_receives_local_time_only(self, parsing_service, mock_preferences_repo):
        """LLM prompt should only contain local time, no UTC or timezone info."""
        # Setup: Portugal user with UTC+1
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1
        )
        
        # Mock LLM response
        mock_llm_response = Mock()
        mock_llm_response.content = '{"title": "Meeting", "due_time": "2025-07-14T14:00:00", "description": "Meeting at 2pm"}'
        
        # Track what prompt is sent to LLM
        captured_prompt = None
        def capture_prompt(messages):
            nonlocal captured_prompt
            captured_prompt = messages[0].content
            return mock_llm_response
        
        with patch.object(parsing_service.llm, 'invoke', side_effect=capture_prompt):
            # Current UTC time: 2025-07-14 13:00:00
            with patch('services.parsing_service.datetime') as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 7, 14, 13, 0, 0, tzinfo=timezone.utc)
                mock_datetime.timezone = timezone
                mock_datetime.fromisoformat = datetime.fromisoformat
                
                # Call the service
                parsing_service.parse_content_to_task(
                    "meeting at 2pm",
                    owner_name="Test User",
                    location="Portugal"
                )
        
        # Verify the prompt
        assert captured_prompt is not None
        
        # Should contain local time (14:00 Portugal time)
        assert "2025-07-14 14:00:00" in captured_prompt or "Current Time: 2025-07-14 14:00:00" in captured_prompt
        
        # Should NOT contain UTC times or timezone info
        assert "UTC" not in captured_prompt
        assert "13:00:00Z" not in captured_prompt
        assert "timezone" not in captured_prompt.lower()
        assert "offset" not in captured_prompt.lower()
        assert "+1" not in captured_prompt
        assert "UTC+1" not in captured_prompt
    
    def test_llm_output_interpreted_as_local_time(self, parsing_service, mock_preferences_repo):
        """LLM output of '14:00' should be treated as 14:00 local time."""
        # Setup: Portugal user with UTC+1
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal", 
            utc_offset=1
        )
        
        # Mock LLM to return 14:00 (no timezone)
        mock_llm_response = Mock()
        mock_llm_response.content = '{"title": "Meeting", "due_time": "2025-07-14T14:00:00", "description": "Meeting"}'
        
        with patch.object(parsing_service.llm, 'invoke', return_value=mock_llm_response):
            with patch('services.parsing_service.datetime') as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 7, 14, 13, 0, 0, tzinfo=timezone.utc)
                mock_datetime.timezone = timezone
                mock_datetime.fromisoformat = datetime.fromisoformat
                
                result = parsing_service.parse_content_to_task(
                    "meeting at 2pm",
                    owner_name="Test User",
                    location="Portugal"
                )
        
        # Verify: 14:00 Portugal time should become 13:00 UTC
        assert result is not None
        assert result['due_time'] == "2025-07-14T13:00:00Z"
    
    def test_local_to_utc_conversion(self, parsing_service, mock_preferences_repo):
        """Test conversion of LLM's local time output to UTC using offset."""
        test_cases = [
            # (location, utc_offset, local_time, expected_utc)
            ("Portugal", 1, "2025-07-14T14:00:00", "2025-07-14T13:00:00Z"),
            ("New York", -5, "2025-07-14T14:00:00", "2025-07-14T19:00:00Z"),
            ("Tokyo", 9, "2025-07-14T14:00:00", "2025-07-14T05:00:00Z"),
            ("UK", 0, "2025-07-14T14:00:00", "2025-07-14T14:00:00Z"),
        ]
        
        for location, offset, local_time, expected_utc in test_cases:
            # Setup user preferences
            mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
                user_id=123,
                location=location,
                utc_offset=offset
            )
            
            # Mock LLM response
            mock_llm_response = Mock()
            mock_llm_response.content = f'{{"title": "Task", "due_time": "{local_time}", "description": "Test"}}'
            
            with patch.object(parsing_service.llm, 'invoke', return_value=mock_llm_response):
                with patch('services.parsing_service.datetime') as mock_datetime:
                    mock_datetime.now.return_value = datetime(2025, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
                    mock_datetime.timezone = timezone
                    mock_datetime.fromisoformat = datetime.fromisoformat
                    
                    result = parsing_service.parse_content_to_task(
                        "task at 2pm",
                        owner_name="Test User",
                        location=location
                    )
            
            assert result is not None
            assert result['due_time'] == expected_utc, f"Failed for {location}: expected {expected_utc}, got {result['due_time']}"
    
    def test_cached_utc_offset_usage(self, parsing_service, mock_preferences_repo):
        """Test that UTC offset is retrieved from user preferences."""
        # User has cached UTC offset
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1  # Cached value
        )
        
        # Mock LLM response
        mock_llm_response = Mock()
        mock_llm_response.content = '{"title": "Task", "due_time": "2025-07-14T14:00:00", "description": "Test"}'
        
        with patch.object(parsing_service.llm, 'invoke', return_value=mock_llm_response):
            with patch('services.parsing_service.datetime') as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 7, 14, 13, 0, 0, tzinfo=timezone.utc)
                mock_datetime.timezone = timezone
                mock_datetime.fromisoformat = datetime.fromisoformat
                
                # Spy on get_timezone_offset to ensure it's NOT called
                with patch.object(parsing_service, 'get_timezone_offset') as mock_get_offset:
                    result = parsing_service.parse_content_to_task(
                        "task at 2pm",
                        owner_name="Test User",
                        location="Portugal"
                    )
        
        # Should use cached offset, not calculate
        mock_get_offset.assert_not_called()
        assert result['due_time'] == "2025-07-14T13:00:00Z"
    
    def test_missing_offset_fallback(self, parsing_service, mock_preferences_repo):
        """Test fallback when utc_offset is not set."""
        # User has location but no cached offset
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=None  # Not cached
        )
        
        # Mock LLM response
        mock_llm_response = Mock()
        mock_llm_response.content = '{"title": "Task", "due_time": "2025-07-14T14:00:00", "description": "Test"}'
        
        with patch.object(parsing_service.llm, 'invoke', return_value=mock_llm_response):
            with patch('services.parsing_service.datetime') as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 7, 14, 13, 0, 0, tzinfo=timezone.utc)
                mock_datetime.timezone = timezone
                mock_datetime.fromisoformat = datetime.fromisoformat
                
                # Should call get_timezone_offset
                with patch.object(parsing_service, 'get_timezone_offset', return_value=1) as mock_get_offset:
                    result = parsing_service.parse_content_to_task(
                        "task at 2pm",
                        owner_name="Test User",
                        location="Portugal"
                    )
        
        # Should calculate offset from location
        mock_get_offset.assert_called_once_with("Portugal")
        
        # Should update preferences with calculated offset
        mock_preferences_repo.update_preferences.assert_called_once()
        update_call = mock_preferences_repo.update_preferences.call_args
        assert update_call[0][0] == 123  # user_id
        assert update_call[0][1].utc_offset == 1  # calculated offset
        
        assert result['due_time'] == "2025-07-14T13:00:00Z"
    
    def test_offset_calculation_on_location_update(self, parsing_service):
        """Test UTC offset is calculated when location changes."""
        # Test the offset calculation logic
        test_cases = [
            ("Portugal", 1),
            ("New York", -5),
            ("California", -8),
            ("Tokyo", 9),
            ("UK", 0),
            ("Unknown City", 0),  # Default to UTC
        ]
        
        for location, expected_offset in test_cases:
            # Mock the zoneinfo lookup
            with patch('services.parsing_service.zoneinfo.ZoneInfo') as mock_zoneinfo:
                # Mock timezone object
                mock_tz = Mock()
                mock_local_time = Mock()
                mock_local_time.utcoffset.return_value = timedelta(hours=expected_offset)
                mock_tz.return_value = mock_tz
                mock_zoneinfo.return_value = mock_tz
                
                with patch('services.parsing_service.datetime') as mock_datetime:
                    mock_datetime.now.return_value.astimezone.return_value = mock_local_time
                    
                    offset = parsing_service.get_timezone_offset(location)
            
            assert offset == expected_offset, f"Failed for {location}: expected {expected_offset}, got {offset}"
    
    def test_prompt_has_no_timezone_complexity(self, parsing_service):
        """Test that the prompt template has no timezone-related complexity."""
        # Get the prompt template
        prompt_template = parsing_service.prompt_template.template
        
        # Should NOT contain timezone-related terms
        forbidden_terms = [
            "UTC", "timezone", "offset", "UTC+", "UTC-", 
            "convert", "Calculate in UTC", "timezone_name",
            "timezone_offset", "Current UTC:", "TIME EXAMPLES"
        ]
        
        for term in forbidden_terms:
            assert term not in prompt_template, f"Prompt should not contain '{term}'"
        
        # Should contain local time reference
        assert "Current Time:" in prompt_template or "current_local_time" in prompt_template
    
    def test_future_time_safeguard_with_local_time(self, parsing_service, mock_preferences_repo):
        """Test that the 1-minute future safeguard works with local time conversion."""
        # Setup: User in Portugal (UTC+1)
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1
        )
        
        # Current time: 14:00 UTC (15:00 Portugal)
        current_utc = datetime(2025, 7, 14, 14, 0, 0, tzinfo=timezone.utc)
        
        # LLM returns 15:00 local time (which is right now)
        mock_llm_response = Mock()
        mock_llm_response.content = '{"title": "Task", "due_time": "2025-07-14T15:00:00", "description": "Test"}'
        
        with patch.object(parsing_service.llm, 'invoke', return_value=mock_llm_response):
            with patch('services.parsing_service.datetime') as mock_datetime:
                mock_datetime.now.return_value = current_utc
                mock_datetime.timezone = timezone
                mock_datetime.fromisoformat = datetime.fromisoformat
                
                result = parsing_service.parse_content_to_task(
                    "task now",
                    owner_name="Test User",
                    location="Portugal"
                )
        
        # Should push to tomorrow same time
        assert result is not None
        assert result['due_time'] == "2025-07-15T14:00:00Z"  # Tomorrow 15:00 Portugal = 14:00 UTC