"""Integration tests for time parsing edge cases with REAL LLM calls."""

import pytest
from unittest.mock import patch
from datetime import datetime, timezone
from dateutil import parser as date_parser

from services.parsing_service import ParsingService
from config import Config


class TestTimeParsingEdgeCases:
    """Test time parsing behavior with REAL OpenAI API calls - no LLM mocking."""
    
    @pytest.fixture
    def parsing_service(self):
        """Create parsing service with real config."""
        config = Config()
        if not config.OPENAI_API_KEY or config.OPENAI_API_KEY == "test_key_not_used":
            pytest.skip("OpenAI API key not configured")
        return ParsingService(config)
    
    @pytest.mark.integration
    def test_today_3am_at_midnight_45_portugal_time(self, parsing_service):
        """Test 'today 3am' when current time is 00:45 in Portugal (UTC+1)."""
        # Mock current time to 00:45 UTC on June 29, 2025 (01:45 Portugal time)
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today 3am",
                owner_name="Test User",
                location="Portugal"  # UTC+1
            )
        
        # Should schedule for today at 3am Portugal time = 2am UTC
        assert result is not None
        assert result["due_time"] == "2025-06-29T02:00:00Z"
    
    @pytest.mark.integration
    def test_today_5am_at_midnight_45_portugal_time(self, parsing_service):
        """Test 'today 5am' when current time is 00:45 UTC (01:45 Portugal time)."""
        # Mock current time to 00:45 UTC on June 29, 2025 (01:45 Portugal time)
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today 5am",
                owner_name="Test User", 
                location="Portugal"  # UTC+1
            )
        
        # Should schedule for today at 5am Portugal time = 4am UTC (since 5am Portugal hasn't passed yet)
        assert result is not None
        print(f"Generated due_time: {result['due_time']}")
        assert result["due_time"] == "2025-06-29T04:00:00Z"
    
    @pytest.mark.integration
    def test_tomorrow_9am_portugal_time(self, parsing_service):
        """Test 'tomorrow 9am' in Portugal timezone."""
        # Mock current time to 00:45 UTC on June 29, 2025
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something tomorrow 9am",
                owner_name="Test User",
                location="Portugal"
            )
        
        # Should schedule for June 30 at 9am Portugal time = 8am UTC
        assert result is not None
        assert result["due_time"] == "2025-06-30T08:00:00Z"
    
    @pytest.mark.integration
    def test_asap_relative_time_utc(self, parsing_service):
        """Test 'asap' relative time parsing in UTC."""
        # Mock current time to 00:45 UTC on June 29, 2025
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something asap",
                owner_name="Test User",
                location="UTC"
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
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something in 30 minutes",
                owner_name="Test User",
                location="UTC"
            )
        
        # Should schedule 30 minutes from now (01:15 UTC)
        assert result is not None
        assert result["due_time"] == "2025-06-29T01:15:00Z"
    
    @pytest.mark.integration
    def test_today_midnight_portugal_afternoon(self, parsing_service):
        """Test 'today midnight' when it's afternoon in Portugal."""
        # Mock current time to 15:30 UTC on June 29, 2025 (16:30 Portugal time - afternoon)
        test_time = datetime(2025, 6, 29, 15, 30, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today midnight",
                owner_name="Test User",
                location="Portugal"
            )
        
        # Should schedule for next midnight Portugal time = 23:00 UTC June 29
        assert result is not None
        assert result["due_time"] == "2025-06-29T23:00:00Z"
    
    @pytest.mark.integration
    def test_today_noon_portugal_morning(self, parsing_service):
        """Test 'today noon' when it's morning in Portugal."""
        # Mock current time to 08:30 UTC on June 29, 2025 (09:30 Portugal time - morning)
        test_time = datetime(2025, 6, 29, 8, 30, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today noon",
                owner_name="Test User",
                location="Portugal"
            )
        
        # Should schedule for today at noon Portugal time = 11:00 UTC
        assert result is not None
        assert result["due_time"] == "2025-06-29T11:00:00Z"
    
    @pytest.mark.integration
    def test_in_2_hours_portugal_time(self, parsing_service):
        """Test 'in 2 hours' relative time with Portugal timezone."""
        # Mock current time to 10:30 UTC on June 29, 2025 (11:30 Portugal time)
        test_time = datetime(2025, 6, 29, 10, 30, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something in 2 hours",
                owner_name="Test User",
                location="Portugal"
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
            ("remind me today at 18:00", "2025-06-29T17:00:00Z", "24-hour with at"),
            ("remind me today 18:00", "2025-06-29T17:00:00Z", "24-hour without at"),
            ("remind me today at 6pm", "2025-06-29T17:00:00Z", "pm with at"),
            ("remind me today 6pm", "2025-06-29T17:00:00Z", "pm without at"),
        ]
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            for test_input, expected_time, description in test_cases:
                result = parsing_service.parse_content_to_task(
                    content_message=test_input,
                    owner_name="Test User",
                    location="Portugal"
                )
                
                assert result is not None, f"Failed to parse: {test_input} ({description})"
                assert result["due_time"] == expected_time, f"Wrong time for {test_input}: got {result['due_time']}, expected {expected_time}"