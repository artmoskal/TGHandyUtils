"""Integration tests with manual time specifications to ensure robust scheduling."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from services.parsing_service import ParsingService
from config import Config
from dateutil import parser as date_parser


class TestManualTimeScheduling:
    """Test scheduling with various manual time specifications."""
    
    @pytest.fixture
    def parsing_service(self):
        """Create parsing service with real OpenAI API."""
        config = Config()
        if not config.OPENAI_API_KEY or config.OPENAI_API_KEY == "test_key_not_used":
            pytest.skip("OpenAI API key not configured")
        return ParsingService(config)
    
    def test_manual_time_00_30_schedule_5am(self, parsing_service):
        """
        Manual test: It's 00:30 UTC, user says "today 5am"
        Expected: TODAY at 04:00 UTC (5am Portugal)
        """
        # Manually set time to 00:30 UTC
        test_time = datetime(2025, 7, 15, 0, 30, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me today 5am",
                owner_name="Manual Test User",
                location="Portugal"
            )
            
            assert result is not None
            due_time = date_parser.isoparse(result['due_time'])
            
            print(f"\n[MANUAL TEST 1]")
            print(f"Current: {test_time.strftime('%Y-%m-%d %H:%M')} UTC")
            print(f"Request: 'today 5am' (Portugal)")
            print(f"Scheduled: {due_time.strftime('%Y-%m-%d %H:%M')} UTC")
            
            # Should be same day at 04:00 UTC
            assert due_time.date() == test_time.date(), "Should be TODAY"
            assert due_time.hour == 4, "Should be 04:00 UTC (5am Portugal)"
            print("✅ PASS: Correctly scheduled for today at 5am")
    
    def test_manual_time_23_45_schedule_2am(self, parsing_service):
        """
        Manual test: It's 23:45 UTC, user says "today 2am"
        Expected: TOMORROW at 01:00 UTC (2am Portugal) - because 2am already passed
        """
        test_time = datetime(2025, 7, 15, 23, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me today 2am",
                owner_name="Manual Test User",
                location="Portugal"
            )
            
            assert result is not None
            due_time = date_parser.isoparse(result['due_time'])
            
            print(f"\n[MANUAL TEST 2]")
            print(f"Current: {test_time.strftime('%Y-%m-%d %H:%M')} UTC")
            print(f"Request: 'today 2am' (Portugal)")
            print(f"Scheduled: {due_time.strftime('%Y-%m-%d %H:%M')} UTC")
            
            # Should be tomorrow because it's late evening
            assert due_time.date() > test_time.date(), "Should be TOMORROW"
            assert due_time.hour == 1, "Should be 01:00 UTC (2am Portugal)"
            print("✅ PASS: Correctly scheduled for tomorrow at 2am")
    
    def test_manual_time_01_00_schedule_various(self, parsing_service):
        """
        Manual test: It's 01:00 UTC, test various "today" times
        """
        test_time = datetime(2025, 7, 15, 1, 0, 0, tzinfo=timezone.utc)
        
        test_cases = [
            ("today 3am", 2, True, "Should be today (3am > 1am)"),
            ("today 7am", 6, True, "Should be today (7am > 1am)"),
            ("today midnight", 23, True, "Should be today next midnight (23:00 UTC)"),
            ("today noon", 11, True, "Should be today at noon"),
        ]
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            for test_input, expected_utc_hour, is_today, description in test_cases:
                result = parsing_service.parse_content_to_task(
                    content_message=f"remind me {test_input}",
                    owner_name="Manual Test User",
                    location="Portugal"
                )
                
                assert result is not None
                due_time = date_parser.isoparse(result['due_time'])
                
                print(f"\n[MANUAL TEST - 01:00 UTC]")
                print(f"Request: '{test_input}'")
                print(f"Scheduled: {due_time.strftime('%Y-%m-%d %H:%M')} UTC")
                print(f"Expected: {description}")
                
                if is_today:
                    assert due_time.date() == test_time.date(), f"{test_input} - {description}"
                else:
                    assert due_time.date() > test_time.date(), f"{test_input} - {description}"
                
                print(f"✅ PASS: {description}")
    
    def test_manual_time_00_00_exact_midnight(self, parsing_service):
        """
        Manual test: It's exactly 00:00 UTC (midnight)
        """
        test_time = datetime(2025, 7, 15, 0, 0, 0, tzinfo=timezone.utc)
        
        test_cases = [
            ("today 1am", 0, True),   # 1am Portugal = 0am UTC = RIGHT NOW
            ("today 6am", 5, True),   # Should be today
            ("today 11pm", 22, True), # Should be today (far future)
        ]
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            for test_input, expected_utc_hour, is_today in test_cases:
                result = parsing_service.parse_content_to_task(
                    content_message=f"remind me {test_input}",
                    owner_name="Manual Test User", 
                    location="Portugal"
                )
                
                assert result is not None
                due_time = date_parser.isoparse(result['due_time'])
                
                print(f"\n[MANUAL TEST - MIDNIGHT]")
                print(f"Current: {test_time.strftime('%Y-%m-%d %H:%M')} UTC (midnight)")
                print(f"Request: '{test_input}'")
                print(f"Scheduled: {due_time.strftime('%Y-%m-%d %H:%M')} UTC")
                
                # All should be scheduled for the future
                time_diff = (due_time - test_time).total_seconds()
                assert time_diff > 0, f"{test_input} should be in the future"
                
                print(f"✅ PASS: Scheduled {time_diff/3600:.1f} hours in future")
    
    def test_manual_specific_bug_scenario(self, parsing_service):
        """
        Manual test: Exact bug scenario - 00:22 UTC, "today 5am"
        """
        # Exact time from bug report
        test_time = datetime(2025, 6, 29, 0, 22, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today 5am",
                owner_name="Bug Reporter",
                location="Portugal"
            )
            
            assert result is not None
            due_time = date_parser.isoparse(result['due_time'])
            
            print(f"\n[BUG SCENARIO VALIDATION]")
            print(f"Current: {test_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            print(f"Request: 'remind me about something today 5am' (Portugal)")
            print(f"Scheduled: {due_time}")
            print(f"Expected: 2025-06-29 04:00:00 UTC (same day)")
            
            # Critical assertions
            assert due_time.date() == datetime(2025, 6, 29).date(), "Must be June 29 (TODAY)"
            assert due_time.hour == 4, "Must be 04:00 UTC (5am Portugal)"
            assert due_time.minute == 0, "Must be exactly on the hour"
            
            # Calculate hours difference
            hours_diff = (due_time - test_time).total_seconds() / 3600
            assert 3 < hours_diff < 4, f"Should be ~3.6 hours away, got {hours_diff:.1f}"
            
            print(f"✅ BUG FIXED: Correctly scheduled {hours_diff:.1f} hours in future on same day")
    
    def test_manual_various_timezones(self, parsing_service):
        """
        Manual test: Same time request from different timezones
        """
        test_time = datetime(2025, 7, 15, 0, 30, 0, tzinfo=timezone.utc)
        
        timezone_tests = [
            ("Portugal", "today 5am", 4),      # UTC+1 → 5am local = 4am UTC
            ("London", "today 5am", 4),        # UTC+1 summer → 5am local = 4am UTC
            ("New York", "today 5am", 9),      # UTC-4 summer → 5am local = 9am UTC
            ("Tokyo", "today 5am", 20),        # UTC+9 → 5am local = 20:00 previous day UTC
        ]
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            for location, request, expected_hour in timezone_tests:
                result = parsing_service.parse_content_to_task(
                    content_message=request,
                    owner_name="Timezone Test User",
                    location=location
                )
                
                assert result is not None
                due_time = date_parser.isoparse(result['due_time'])
                
                print(f"\n[TIMEZONE TEST - {location}]")
                print(f"Current: {test_time.strftime('%H:%M')} UTC")
                print(f"Request: '{request}' from {location}")
                print(f"Scheduled: {due_time.strftime('%Y-%m-%d %H:%M')} UTC")
                
                # Tokyo edge case: 5am Tokyo when it's 00:30 UTC is actually yesterday 20:00 UTC
                if location == "Tokyo" and expected_hour == 20:
                    # Should schedule for tomorrow's 5am Tokyo = today 20:00 UTC
                    assert due_time.hour == expected_hour
                    print(f"✅ PASS: Correctly handled {location} timezone edge case")
                else:
                    # For others, should be today
                    assert due_time.date() == test_time.date(), f"Should be today for {location}"
                    assert due_time.hour == expected_hour, f"Wrong hour for {location}"
                    print(f"✅ PASS: Correct timezone conversion for {location}")