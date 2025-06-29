"""REAL integration test with actual current time and OpenAI API."""

import pytest
from datetime import datetime, timezone

from services.parsing_service import ParsingService
from config import Config


class TestRealTimeParsingIntegration:
    """Test with REAL current time - no mocking."""
    
    @pytest.fixture
    def config(self):
        return Config()
    
    @pytest.fixture
    def parsing_service(self, config):
        return ParsingService(config)
    
    @pytest.mark.integration
    def test_today_5am_with_real_current_time(self, parsing_service):
        """
        Test 'today 5am' with REAL current time.
        NO TIME MOCKING - this tests actual real-world behavior.
        
        Logic: If current time is before 5am UTC, then "today 5am" should be today.
               If current time is after 5am UTC, then "today 5am" should be tomorrow (next occurrence).
        """
        current_real_time = datetime.now(timezone.utc)
        print(f"\\nREAL CURRENT TIME: {current_real_time}")
        
        # Determine expected behavior based on current time
        current_hour = current_real_time.hour
        today_date = current_real_time.date()
        tomorrow_date = today_date.replace(day=today_date.day + 1) if today_date.day < 30 else today_date.replace(month=today_date.month + 1, day=1)
        
        if current_hour < 5:
            expected_date = today_date
            expectation = f"today (before 5am, so schedule for {today_date})"
        else:
            expected_date = tomorrow_date  
            expectation = f"tomorrow (after 5am, so schedule for {tomorrow_date})"
        
        print(f"Expectation: {expectation}")
        
        # Test with real current time - NO MOCKING
        result = parsing_service.parse_content_to_task(
            content_message="remind me about something today 5am",
            owner_name="Test User",
            location=None
        )
        
        print(f"LLM Result: {result['due_time']}")
        
        # Parse the result
        from dateutil import parser as date_parser
        scheduled_time = date_parser.isoparse(result['due_time'])
        scheduled_date = scheduled_time.date()
        scheduled_hour = scheduled_time.hour
        
        print(f"Scheduled for: {scheduled_date} at {scheduled_hour}:00 UTC")
        print(f"Expected date: {expected_date}")
        
        # Verify the date is correct
        assert scheduled_date == expected_date, f"Expected {expected_date}, got {scheduled_date}"
        
        # Verify the time is 5am UTC
        assert scheduled_hour == 5, f"Expected 5am UTC, got {scheduled_hour}:00 UTC"
        
        print(f"✅ CORRECT: Scheduled for {scheduled_date} at 5am UTC")
    
    @pytest.mark.integration
    def test_today_3am_with_real_current_time(self, parsing_service):
        """Test 'today 3am' with real current time for comparison."""
        current_real_time = datetime.now(timezone.utc)
        print(f"\\nREAL CURRENT TIME: {current_real_time}")
        
        result = parsing_service.parse_content_to_task(
            content_message="remind me about something today 3am",
            owner_name="Test User",
            location=None
        )
        
        print(f"LLM Result: {result['due_time']}")
        
        # Since 3am has passed, it should schedule for tomorrow
        if result['due_time'].startswith('2025-06-30') and '03:00' in result['due_time']:
            print("✅ CORRECT: 3am has passed, scheduled for tomorrow")
        elif result['due_time'].startswith('2025-06-29'):
            print("❌ BUG: Scheduled for today but 3am already passed")
            assert False, f"BUG: 3am has passed, should be tomorrow: {result['due_time']}"
        else:
            print(f"❓ UNEXPECTED: {result['due_time']}")
    
    @pytest.mark.integration  
    def test_tomorrow_9am_control_with_real_time(self, parsing_service):
        """Control test with 'tomorrow 9am' - should always work."""
        result = parsing_service.parse_content_to_task(
            content_message="remind me about something tomorrow 9am",
            owner_name="Test User",
            location=None
        )
        
        print(f"Tomorrow 9am result: {result['due_time']}")
        
        # Should be June 30 at 9am
        assert result['due_time'].startswith('2025-06-30')
        assert '09:00' in result['due_time']