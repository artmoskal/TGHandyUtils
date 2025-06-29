"""Integration tests for midnight corner cases - critical scheduling edge cases."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, Mock
from services.parsing_service import ParsingService
from config import Config
from dateutil import parser as date_parser


class TestMidnightCornerCases:
    """Test scheduling behavior around midnight (00:00-01:00)."""
    
    @pytest.fixture
    def parsing_service(self):
        """Create parsing service with real OpenAI API."""
        config = Config()
        if not config.OPENAI_API_KEY or config.OPENAI_API_KEY == "test_key_not_used":
            pytest.skip("OpenAI API key not configured")
        return ParsingService(config)
    
    def test_today_5am_at_midnight_22_minutes(self, parsing_service):
        """
        CRITICAL TEST: Reproduce the exact bug scenario
        Current time: 00:22 UTC (01:22 Portugal)
        User says: "remind me about something today 5am"
        Expected: TODAY at 04:00 UTC (05:00 Portugal) - same day!
        Bug was: Tomorrow at 04:00 UTC - wrong day!
        """
        # Mock current time to be 00:22 UTC
        test_time = datetime(2025, 6, 29, 0, 22, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            # Configure the mock
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            # Parse the task
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something today 5am",
                owner_name="Test User",
                location="Portugal"
            )
            
            assert result is not None
            
            # DEBUG: Print LLM response details
            print(f"\n🔍 LLM RESPONSE DEBUG:")
            print(f"   Input: 'remind me about something today 5am'")
            print(f"   Current UTC: {test_time} (hour: {test_time.hour})")
            print(f"   Current Portugal: {test_time.hour + 1}:22")
            print(f"   LLM returned due_time: {result['due_time']}")
            print(f"   LLM returned title: {result['title']}")
            
            # Parse the scheduled time
            due_time = date_parser.isoparse(result['due_time'])
            print(f"   Parsed due_time: {due_time}")
            print(f"   Expected: 2025-06-29 04:00:00+00:00 (TODAY at 4am UTC)")
            print(f"   Actual date: {due_time.date()}")
            print(f"   Expected date: {test_time.date()}")
            print(f"   Date match: {due_time.date() == test_time.date()}")
            
            print(f"\n[MIDNIGHT TEST] Current UTC: {test_time}")
            print(f"[MIDNIGHT TEST] Scheduled UTC: {due_time}")
            print(f"[MIDNIGHT TEST] Expected: 2025-06-29 04:00 UTC (same day)")
            
            # CRITICAL ASSERTIONS
            # 1. Should be scheduled for TODAY (June 29), not tomorrow
            assert due_time.date() == test_time.date(), \
                f"Should schedule for TODAY ({test_time.date()}), but got {due_time.date()}"
            
            # 2. Should be at 04:00 UTC (5am Portugal time)
            assert due_time.hour == 4, f"Should be at 04:00 UTC, but got {due_time.hour}:00"
            
            # 3. Time difference should be ~3.5 hours (from 00:22 to 04:00)
            time_diff_hours = (due_time - test_time).total_seconds() / 3600
            assert 3 < time_diff_hours < 4, \
                f"Should be ~3.5 hours away, but got {time_diff_hours:.1f} hours"
            
            print(f"[MIDNIGHT TEST] ✅ PASSED! Correctly scheduled for TODAY at 5am")
    
    def test_today_3am_at_midnight_45_minutes(self, parsing_service):
        """
        Test: Current time 00:45, "today 3am" should be TODAY
        """
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me today 3am",
                owner_name="Test User",
                location="Portugal"
            )
            
            assert result is not None
            due_time = date_parser.isoparse(result['due_time'])
            
            # Should be TODAY at 02:00 UTC (3am Portugal)
            assert due_time.date() == test_time.date(), "Should be scheduled for today"
            assert due_time.hour == 2, "Should be at 02:00 UTC (3am Portugal)"
            
            time_diff_hours = (due_time - test_time).total_seconds() / 3600
            assert 1 < time_diff_hours < 2, f"Should be ~1.25 hours away, got {time_diff_hours:.1f}"
            
            print(f"✅ 00:45 → 'today 3am' correctly scheduled for today")
    
    def test_today_1am_at_midnight_30_minutes(self, parsing_service):
        """
        Test: Current time 00:30 UTC, "today 1am" Portugal time.
        1am Portugal = 00:00 UTC, which is BEFORE current time 00:30.
        So this should schedule for TOMORROW.
        """
        test_time = datetime(2025, 6, 29, 0, 30, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me today 1am",
                owner_name="Test User",
                location="Portugal"
            )
            
            assert result is not None
            due_time = date_parser.isoparse(result['due_time'])
            
            # 1am Portugal = 00:00 UTC, which is 30 minutes AGO from 00:30
            # So it should schedule for TOMORROW at 00:00 UTC
            time_diff_hours = (due_time - test_time).total_seconds() / 3600
            
            print(f"\nCurrent: {test_time}")
            print(f"Scheduled: {due_time}")
            print(f"Time difference: {time_diff_hours:.1f} hours")
            
            # Should be ~23.5 hours in the future (tomorrow at 00:00)
            assert 23 < time_diff_hours < 24, "Should be scheduled for tomorrow 1am"
            assert due_time.hour == 0, "Should be at 00:00 UTC (1am Portugal)"
            print("✅ Correctly scheduled for tomorrow since 1am already passed")
    
    def test_today_various_times_at_00_15(self, parsing_service):
        """
        Test multiple "today X" times when current time is 00:15
        """
        test_time = datetime(2025, 6, 29, 0, 15, 0, tzinfo=timezone.utc)
        
        test_cases = [
            ("today 2am", 1, "Should be ~1.75 hours away"),
            ("today 5am", 4, "Should be ~4.75 hours away"),
            ("today 9am", 8, "Should be ~8.75 hours away"),
            ("today noon", 11, "Should be ~11.75 hours away"),
            ("today 6pm", 17, "Should be ~17.75 hours away"),
        ]
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            for test_input, expected_utc_hour, description in test_cases:
                result = parsing_service.parse_content_to_task(
                    content_message=f"remind me {test_input}",
                    owner_name="Test User",
                    location="Portugal"
                )
                
                assert result is not None
                due_time = date_parser.isoparse(result['due_time'])
                
                # All should be scheduled for TODAY
                time_diff_hours = (due_time - test_time).total_seconds() / 3600
                
                print(f"\n00:15 → '{test_input}':")
                print(f"  Scheduled: {due_time}")
                print(f"  Hours away: {time_diff_hours:.1f}")
                print(f"  Expected: {description}")
                
                # Should be in the future
                assert time_diff_hours > 0, f"{test_input} should be in the future"
                
                # Should be today (within 24 hours)
                assert time_diff_hours < 24, f"{test_input} should be today (within 24h)"
    
    def test_edge_case_exactly_midnight(self, parsing_service):
        """
        Test scheduling at exactly 00:00:00
        """
        test_time = datetime(2025, 6, 29, 0, 0, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            # Test "today 5am" at exactly midnight
            result = parsing_service.parse_content_to_task(
                content_message="remind me today 5am",
                owner_name="Test User",
                location="Portugal"
            )
            
            assert result is not None
            due_time = date_parser.isoparse(result['due_time'])
            
            # Should be TODAY at 04:00 UTC (exactly 4 hours from midnight)
            assert due_time.date() == test_time.date(), "Should be today"
            assert due_time.hour == 4, "Should be 04:00 UTC"
            
            time_diff = (due_time - test_time).total_seconds() / 3600
            assert time_diff == 4.0, f"Should be exactly 4 hours, got {time_diff}"
            
            print("✅ Exactly midnight → 'today 5am' scheduled correctly for today")