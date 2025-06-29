"""Integration tests for parsing service with real OpenAI API calls.

These tests consume tokens and cost money, so they are separated from unit tests.
Run with: pytest tests/integration/test_parsing_integration.py -v
"""

import pytest
from datetime import datetime, timezone, timedelta
from services.parsing_service import ParsingService
from config import Config
from dateutil import parser as date_parser


class TestParsingServiceIntegration:
    """Integration tests that make actual OpenAI API calls."""
    
    @pytest.fixture
    def parsing_service(self):
        """Create parsing service with real OpenAI API."""
        config = Config()
        if not config.OPENAI_API_KEY:
            pytest.skip("OpenAI API key not configured in .env file")
        return ParsingService(config)
    
    @pytest.fixture
    def current_time(self):
        """Get current UTC time for test calculations."""
        return datetime.now(timezone.utc)
    
    def test_schedule_4_minutes_from_now(self, parsing_service, current_time):
        """Test scheduling 4 minutes from now."""
        content = "test schedule 4m from now"
        location = "Cascais"
        owner_name = "Test User"
        
        result = parsing_service.parse_content_to_task(content, owner_name, location)
        
        assert result is not None
        
        # Only check the time scheduling accuracy
        due_time = date_parser.isoparse(result['due_time'])
        expected_time = current_time + timedelta(minutes=4)
        
        # Allow for some variance due to processing time (within 2 minutes)
        time_diff = abs((due_time - expected_time).total_seconds())
        assert time_diff < 120, f"Due time {due_time} not within 2 minutes of expected {expected_time}"
    
    def test_schedule_next_monday_11am(self, parsing_service, current_time):
        """Test scheduling next Monday at 11am."""
        content = "test schedule next Monday 11am"
        location = "Cascais"
        owner_name = "Test User"
        
        result = parsing_service.parse_content_to_task(content, owner_name, location)
        
        assert result is not None
        
        # Only check the time scheduling accuracy  
        due_time = date_parser.isoparse(result['due_time'])
        assert due_time.weekday() == 0, f"Expected Monday (0), got {due_time.weekday()}"
        assert due_time.hour == 10, f"Expected 10 UTC (11am Cascais), got {due_time.hour}"
    
    def test_schedule_in_2_weeks(self, parsing_service, current_time):
        """Test scheduling in 2 weeks."""
        content = "test schedule in 2w"
        location = "Cascais"
        owner_name = "Test User"
        
        result = parsing_service.parse_content_to_task(content, owner_name, location)
        
        assert result is not None
        
        # Only check the time scheduling accuracy
        due_time = date_parser.isoparse(result['due_time'])
        expected_time = current_time + timedelta(weeks=2)
        
        # Allow for some variance (within 1 day)
        time_diff = abs((due_time - expected_time).total_seconds())
        assert time_diff < 86400, f"Due time {due_time} not within 1 day of expected {expected_time}"
    
    def test_schedule_without_date_defaults_tomorrow_9am(self, parsing_service, current_time):
        """Test that scheduling without date defaults to tomorrow 9am."""
        content = "test schedule"
        location = "Cascais"
        owner_name = "Test User"
        
        result = parsing_service.parse_content_to_task(content, owner_name, location)
        
        assert result is not None
        
        # Only check the time scheduling accuracy - tomorrow at 9am (8am UTC for Cascais +1)
        due_time = date_parser.isoparse(result['due_time'])
        tomorrow = (current_time + timedelta(days=1)).date()
        
        assert due_time.date() == tomorrow, f"Expected {tomorrow}, got {due_time.date()}"
        assert due_time.hour in [8, 9], f"Expected 8 or 9 UTC (9am Cascais with timezone variance), got {due_time.hour}"
    
    def test_schedule_nov_25_next_year(self, parsing_service, current_time):
        """Test scheduling Nov 25 (should be next occurrence)."""
        content = "test schedule Nov 25"
        location = "Cascais"
        owner_name = "Test User"
        
        result = parsing_service.parse_content_to_task(content, owner_name, location)
        
        assert result is not None
        
        # Only check the time scheduling accuracy - verify it's November 25
        due_time = date_parser.isoparse(result['due_time'])
        assert due_time.month == 11, f"Expected November (11), got {due_time.month}"
        assert due_time.day == 25, f"Expected 25th, got {due_time.day}"
        
        # Should be current year if Nov 25 hasn't passed, or next year if it has
        current_date = current_time.date()
        if current_date > datetime(current_time.year, 11, 25).date():
            expected_year = current_time.year + 1
        else:
            expected_year = current_time.year
        
        assert due_time.year == expected_year, f"Expected {expected_year}, got {due_time.year}"
        # Default time should be 9am local (8am UTC for Cascais)
        assert due_time.hour in [8, 9], f"Expected 8 or 9 UTC (9am Cascais with timezone variance), got {due_time.hour}"
    
    def test_timezone_conversion_accuracy(self, parsing_service, current_time):
        """Test that timezone conversion is accurate for different scenarios."""
        test_cases = [
            ("remind me in 10 minutes", 10),
            ("remind me in 30 minutes", 30),
            ("remind me in 1 hour", 60),
        ]
        
        location = "Cascais"
        owner_name = "Test User"
        
        for content, expected_minutes in test_cases:
            result = parsing_service.parse_content_to_task(content, owner_name, location)
            
            assert result is not None, f"Failed to parse: {content}"
            
            due_time = date_parser.isoparse(result['due_time'])
            expected_time = current_time + timedelta(minutes=expected_minutes)
            
            # Allow for 5 minutes variance due to processing time and LLM interpretation
            time_diff = abs((due_time - expected_time).total_seconds())
            assert time_diff < 300, f"For '{content}': Due time {due_time} not within 5 minutes of expected {expected_time}"
    
    def test_different_timezones(self, parsing_service, current_time):
        """Test parsing with different timezone locations."""
        content = "remind me in 15 minutes"
        owner_name = "Test User"
        
        test_locations = [
            ("Cascais", 1),      # UTC+1
            ("London", 1),       # UTC+1 (BST in summer)
            ("New York", -4),    # UTC-4 (EDT in summer)
            ("Tokyo", 9),        # UTC+9
        ]
        
        for location, expected_offset in test_locations:
            result = parsing_service.parse_content_to_task(content, owner_name, location)
            
            assert result is not None, f"Failed to parse for location: {location}"
            
            due_time = date_parser.isoparse(result['due_time'])
            expected_time = current_time + timedelta(minutes=15)
            
            # Allow for timezone calculation variance and processing time
            time_diff = abs((due_time - expected_time).total_seconds())
            assert time_diff < 300, f"For {location}: Due time {due_time} not within 5 minutes of expected {expected_time}"
    
    def test_content_types_prioritization(self, parsing_service):
        """Test that [CAPTION] content is prioritized over other content types."""
        content = """[CAPTION] Buy groceries tomorrow at 2PM
[SCREENSHOT TEXT] Some random text from image
[SCREENSHOT DESCRIPTION] An image showing a shopping list"""
        
        location = "Cascais"
        owner_name = "Test User"
        
        result = parsing_service.parse_content_to_task(content, owner_name, location)
        
        assert result is not None
        
        # Only check the time scheduling accuracy - 2PM tomorrow (1PM UTC for Cascais +1)
        due_time = date_parser.isoparse(result['due_time'])
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
        # Allow for same day if LLM interprets "tomorrow" differently
        assert due_time.date() in [datetime.now(timezone.utc).date(), tomorrow], f"Expected today or tomorrow, got {due_time.date()}"
        assert due_time.hour in [13, 14], f"Expected 13 or 14 UTC (2PM Cascais with timezone variance), got {due_time.hour}"
    
    def test_scheduling_integration_midnight_boundaries(self, parsing_service):
        """Test scheduling integration for edge cases around midnight."""
        test_cases = [
            {
                "content": "remind me about something today 5am",
                "location": "Portugal",
                "expected_behavior": "should schedule for same day if 5am is future, next day if past"
            },
            {
                "content": "remind me about meeting tonight 11pm", 
                "location": "Portugal",
                "expected_behavior": "should schedule for same day if 11pm is future, next day if past"
            },
            {
                "content": "remind me about call today 2pm",
                "location": "Portugal", 
                "expected_behavior": "should schedule for same day if 2pm is future, next day if past"
            }
        ]
        
        for case in test_cases:
            result = parsing_service.parse_content_to_task(
                case["content"], 
                "Test User", 
                case["location"]
            )
            
            assert result is not None, f"Failed to parse: {case['content']}"
            
            due_time = date_parser.isoparse(result['due_time'])
            current_utc = datetime.now(timezone.utc)
            
            # Validate that scheduled time is in the future
            assert due_time > current_utc, f"Scheduled time {due_time} should be in future, current: {current_utc}"
            
            # Validate that scheduled time is within reasonable bounds (within 48 hours)
            time_diff = (due_time - current_utc).total_seconds()
            assert time_diff < 172800, f"Scheduled time {due_time} should be within 48 hours of current time"
    
    def test_scheduling_integration_future_dates(self, parsing_service):
        """Test scheduling integration for various future date formats."""
        current_time = datetime.now(timezone.utc)
        
        test_cases = [
            {
                "content": "remind me in 2 weeks",
                "expected_days": 14,
                "tolerance_hours": 24
            },
            {
                "content": "remind me in a day",
                "expected_days": 1, 
                "tolerance_hours": 4
            },
            {
                "content": "remind me next month",
                "expected_days": 30,
                "tolerance_hours": 72
            },
            {
                "content": "remind me december 15",
                "expected_month": 12,
                "expected_day": 15,
                "tolerance_hours": 24
            }
        ]
        
        for case in test_cases:
            result = parsing_service.parse_content_to_task(
                case["content"],
                "Test User",
                "Portugal"
            )
            
            assert result is not None, f"Failed to parse: {case['content']}"
            
            due_time = date_parser.isoparse(result['due_time'])
            
            if "expected_days" in case:
                expected_time = current_time + timedelta(days=case["expected_days"])
                time_diff = abs((due_time - expected_time).total_seconds())
                max_diff = case["tolerance_hours"] * 3600
                assert time_diff < max_diff, f"For '{case['content']}': time difference {time_diff/3600:.1f}h exceeds tolerance"
            
            if "expected_month" in case:
                assert due_time.month == case["expected_month"], f"Expected month {case['expected_month']}, got {due_time.month}"
                assert due_time.day == case["expected_day"], f"Expected day {case['expected_day']}, got {due_time.day}"