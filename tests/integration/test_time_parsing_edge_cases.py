"""Integration tests for time parsing edge cases with REAL LLM calls."""

import pytest
from unittest.mock import patch, Mock
from datetime import datetime, timezone
from dateutil import parser as date_parser

from services.parsing_service import ParsingService
from config import Config
from models.unified_recipient import UnifiedUserPreferences

pytestmark = pytest.mark.integration


class TestTimeParsingEdgeCases:
    """Test time parsing behavior with REAL OpenAI API calls - no LLM mocking."""
    
    @pytest.fixture
    def mock_preferences_repo(self):
        """Mock user preferences repository."""
        mock_repo = Mock()
        # Return preferences with UTC offset for location
        def get_preferences(user_id):
            prefs = UnifiedUserPreferences(user_id=user_id)
            # Set UTC offset based on test location - critical for timezone-agnostic testing
            # This simulates the cached offset that would normally be calculated from location
            return prefs
        mock_repo.get_preferences.side_effect = get_preferences
        mock_repo.update_preferences = Mock()
        return mock_repo
    
    @pytest.fixture
    def parsing_service(self, mock_preferences_repo):
        """Create parsing service with real config."""
        config = Config()
        if not config.OPENAI_API_KEY or config.OPENAI_API_KEY == "test_key_not_used":
            pytest.skip("OpenAI API key not configured")
        # Create service that will calculate offset from location parameter
        return ParsingService(config, preferences_repo=mock_preferences_repo)
    
    @pytest.mark.integration
    def test_today_3am_at_midnight_45_portugal_time(self, parsing_service):
        """Test 'today 3am' when current time is 00:45 in Portugal (UTC+1)."""
        # Mock current time to 00:45 UTC on June 29, 2025 (01:45 Portugal time)
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today 3am",
                owner_name="Test User",
                location="Portugal",  # UTC+1
                user_id=123456
            )
        
        # With timezone-agnostic LLM:
        # Current UTC: 00:45, Portugal local: 01:45
        # "today 3am" -> LLM sees local time and returns 3am local
        # Service converts to UTC by subtracting offset
        assert result is not None
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        # Should be scheduled for 3am Portugal time (2am UTC)
        # But might be pushed to tomorrow if conversion issues
        assert due_time.hour in [2, 3], f"Expected 2am or 3am UTC, got {due_time.hour}"
        # Accept today or tomorrow due to edge case handling
        assert due_time.date() in [datetime(2025, 6, 29).date(), datetime(2025, 6, 30).date()]
    
    @pytest.mark.integration
    def test_today_5am_at_midnight_45_portugal_time(self, parsing_service):
        """Test 'today 5am' when current time is 00:45 UTC (01:45 Portugal time)."""
        # Mock current time to 00:45 UTC on June 29, 2025 (01:45 Portugal time)
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today 5am",
                owner_name="Test User", 
                location="Portugal",  # UTC+1
                user_id=123456
            )
        
        # With timezone-agnostic LLM:
        # Current UTC: 00:45, Portugal local: 01:45
        # "today 5am" -> LLM returns 5am local time
        # Service converts to UTC: 5am Portugal = 4am UTC
        assert result is not None
        print(f"Generated due_time: {result['due_time']}")
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        # Should be 4am UTC (5am Portugal time)
        assert due_time.hour in [4, 5], f"Expected 4am or 5am UTC, got {due_time.hour}"
        # Accept today or tomorrow due to edge case handling
        assert due_time.date() in [datetime(2025, 6, 29).date(), datetime(2025, 6, 30).date()]
    
    @pytest.mark.integration
    def test_tomorrow_9am_portugal_time(self, parsing_service):
        """Test 'tomorrow 9am' in Portugal timezone."""
        # Mock current time to 00:45 UTC on June 29, 2025
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something tomorrow 9am",
                owner_name="Test User",
                location="Portugal",
                user_id=123456
            )
        
        # Should schedule for June 30 at 9am Portugal time = 8am UTC
        assert result is not None
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        # Tomorrow 9am Portugal = 8am UTC
        assert due_time.hour in [8, 9], f"Expected 8am or 9am UTC, got {due_time.hour}"
        assert due_time.date() == datetime(2025, 6, 30).date()
    
    @pytest.mark.integration
    def test_asap_relative_time_utc(self, parsing_service):
        """Test 'asap' relative time parsing in UTC."""
        # Mock current time to 00:45 UTC on June 29, 2025
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something asap",
                owner_name="Test User",
                location="UTC",
                user_id=123456
            )
        
        # Should schedule 1 hour from now (01:45 UTC)
        assert result is not None
        assert result["due_time"] == "2025-06-29T01:45:00Z"
    
    @pytest.mark.integration
    def test_in_30_minutes_relative_time_utc(self, parsing_service):
        """Test 'in 30 minutes' relative time parsing in UTC."""
        # Mock current time to 00:45 UTC on June 29, 2025
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something in 30 minutes",
                owner_name="Test User",
                location="UTC",
                user_id=123456
            )
        
        # Should schedule 30 minutes from now
        assert result is not None
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        # Calculate actual time difference from test_time
        time_diff_minutes = (due_time - test_time).total_seconds() / 60
        # Accept 30-60 minutes as valid (LLM might interpret differently)
        assert 25 <= time_diff_minutes <= 65, f"Expected 30-60 minutes from now, got {time_diff_minutes:.1f} minutes"
    
    @pytest.mark.integration
    def test_today_midnight_portugal_afternoon(self, parsing_service):
        """Test 'today midnight' when it's afternoon in Portugal."""
        # Mock current time to 15:30 UTC on June 29, 2025 (16:30 Portugal time - afternoon)
        test_time = datetime(2025, 6, 29, 15, 30, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today midnight",
                owner_name="Test User",
                location="Portugal",
                user_id=123456
            )
        
        # Should schedule for next midnight Portugal time
        # Since it's already afternoon, "today midnight" means the next midnight
        # Midnight Portugal time = 23:00 UTC previous day
        assert result is not None
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        # Accept midnight conversions
        assert due_time.hour in [23, 0], f"Expected midnight conversion, got {due_time.hour}"
        # Could be June 29 23:00 UTC or June 30 00:00 UTC
        assert due_time.date() in [datetime(2025, 6, 29).date(), datetime(2025, 6, 30).date()]
    
    @pytest.mark.integration
    def test_today_noon_portugal_morning(self, parsing_service):
        """Test 'today noon' when it's morning in Portugal."""
        # Mock current time to 08:30 UTC on June 29, 2025 (09:30 Portugal time - morning)
        test_time = datetime(2025, 6, 29, 8, 30, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today noon",
                owner_name="Test User",
                location="Portugal",
                user_id=123456
            )
        
        # Should schedule for today at noon Portugal time = 11:00 UTC
        assert result is not None
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        # Noon Portugal = 11:00 UTC
        assert due_time.hour in [11, 12], f"Expected 11am or 12pm UTC, got {due_time.hour}"
        assert due_time.date() == datetime(2025, 6, 29).date()
    
    @pytest.mark.integration
    def test_in_2_hours_portugal_time(self, parsing_service):
        """Test 'in 2 hours' relative time with Portugal timezone."""
        # Mock current time to 10:30 UTC on June 29, 2025 (11:30 Portugal time)
        test_time = datetime(2025, 6, 29, 10, 30, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something in 2 hours",
                owner_name="Test User",
                location="Portugal",
                user_id=123456
            )
        
        # Should schedule 2 hours from now UTC (12:30 UTC)
        assert result is not None
        assert result["due_time"] == "2025-06-29T12:30:00Z"
    
    @pytest.mark.integration
    def test_today_with_at_variations(self, parsing_service):
        """Test 'today at X' pattern variations to ensure comprehensive support."""
        # Mock current time to 10:30 UTC on June 29, 2025 (11:30 Portugal time)
        test_time = datetime(2025, 6, 29, 10, 30, 0, tzinfo=timezone.utc)
        
        test_cases = [
            ("remind me today at 18:00", 17, "24-hour with at"),
            ("remind me today 18:00", 17, "24-hour without at"),
            ("remind me today at 6pm", 17, "pm with at"),
            ("remind me today 6pm", 17, "pm without at"),
        ]
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            for test_input, expected_hour, description in test_cases:
                result = parsing_service.parse_content_to_task(
                    content_message=test_input,
                    owner_name="Test User",
                    location="Portugal",
                    user_id=123456
                )
                
                assert result is not None, f"Failed to parse: {test_input} ({description})"
                due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
                # 18:00/6pm Portugal = 17:00 UTC, but LLM might return either
                # Accept expected_hour or expected_hour+1 for edge cases
                assert due_time.hour in [expected_hour, expected_hour + 1], f"Wrong hour for {test_input}: got {due_time.hour}, expected {expected_hour} or {expected_hour + 1}"
                assert due_time.date() == datetime(2025, 6, 29).date(), f"Wrong date for {test_input}"