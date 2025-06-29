"""Integration tests to validate timezone scheduling fixes."""

import pytest
from datetime import datetime, timezone, timedelta
from services.parsing_service import ParsingService
from config import Config
from dateutil import parser as date_parser


class TestSchedulingValidation:
    """Validate that timezone scheduling works correctly."""
    
    @pytest.fixture
    def parsing_service(self):
        """Create parsing service with real OpenAI API."""
        config = Config()
        if not config.OPENAI_API_KEY or config.OPENAI_API_KEY == "test_key_not_used":
            pytest.skip("OpenAI API key not configured")
        return ParsingService(config)
    
    def test_today_5am_portugal_midnight_scenario(self, parsing_service):
        """
        Test the exact bug scenario:
        - Current time: 00:22 UTC (01:22 Portugal)
        - User says: "remind me about something today 5am"
        - Expected: Today at 04:00 UTC (05:00 Portugal)
        - Bug was: Tomorrow at 04:00 UTC
        """
        # Parse the task
        result = parsing_service.parse_content_to_task(
            content_message="remind me about something today 5am",
            owner_name="Test User",
            location="Portugal"
        )
        
        assert result is not None
        
        # Get the scheduled time
        due_time = date_parser.isoparse(result['due_time'])
        current_utc = datetime.now(timezone.utc)
        
        print(f"\nCurrent UTC: {current_utc}")
        print(f"Scheduled UTC: {due_time}")
        print(f"Time difference: {(due_time - current_utc).total_seconds() / 3600:.1f} hours")
        
        # Validate the time is in the future
        assert due_time > current_utc, "Scheduled time must be in the future"
        
        # Validate it's within 24 hours (same day scheduling)
        time_diff_hours = (due_time - current_utc).total_seconds() / 3600
        assert time_diff_hours < 24, f"Should be scheduled for today, but is {time_diff_hours:.1f} hours away"
        
        # If current time is before 5am Portugal (4am UTC), should be today
        portugal_offset = parsing_service.get_timezone_offset("Portugal")
        current_portugal = current_utc + timedelta(hours=portugal_offset)
        
        if current_portugal.hour < 5:
            # Should be scheduled for today
            assert due_time.date() == current_utc.date() or due_time.date() == (current_utc + timedelta(days=1)).date()
            print("✅ Correctly scheduled for today (or tomorrow if near midnight boundary)")
        else:
            # Should be scheduled for tomorrow
            assert due_time.date() == (current_utc + timedelta(days=1)).date()
            print("✅ Correctly scheduled for tomorrow (5am already passed)")
    
    def test_various_today_times(self, parsing_service):
        """Test various 'today [time]' scenarios to ensure consistent behavior."""
        test_cases = [
            "remind me today 3am",
            "remind me today 9am", 
            "remind me today 2pm",
            "remind me today 6pm",
            "remind me today 11pm"
        ]
        
        current_utc = datetime.now(timezone.utc)
        
        for test_case in test_cases:
            result = parsing_service.parse_content_to_task(
                content_message=test_case,
                owner_name="Test User", 
                location="Portugal"
            )
            
            assert result is not None
            due_time = date_parser.isoparse(result['due_time'])
            
            # All should be in the future
            assert due_time > current_utc, f"{test_case}: Should be in future"
            
            # All should be within 48 hours
            hours_diff = (due_time - current_utc).total_seconds() / 3600
            assert hours_diff < 48, f"{test_case}: Should be within 48 hours"
            
            print(f"✅ {test_case}: Scheduled {hours_diff:.1f} hours from now")
    
    def test_future_date_accuracy(self, parsing_service):
        """Test that future date scheduling is accurate."""
        test_cases = [
            ("remind me in exactly 2 weeks", 14, 1),
            ("remind me in 30 days", 30, 1),
            ("remind me next friday", None, None),  # Variable based on current day
        ]
        
        current_utc = datetime.now(timezone.utc)
        
        for test_input, expected_days, tolerance_days in test_cases:
            result = parsing_service.parse_content_to_task(
                content_message=test_input,
                owner_name="Test User",
                location="Portugal"
            )
            
            assert result is not None
            due_time = date_parser.isoparse(result['due_time'])
            
            if expected_days:
                actual_days = (due_time.date() - current_utc.date()).days
                assert abs(actual_days - expected_days) <= tolerance_days, \
                    f"{test_input}: Expected ~{expected_days} days, got {actual_days}"
                print(f"✅ {test_input}: Scheduled {actual_days} days from now")
            else:
                # Just validate it's in the future
                assert due_time > current_utc
                days_away = (due_time.date() - current_utc.date()).days
                print(f"✅ {test_input}: Scheduled {days_away} days from now")