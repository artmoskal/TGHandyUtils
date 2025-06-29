"""Integration test to reproduce the time parsing bug with real OpenAI API."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from services.parsing_service import ParsingService
from config import Config


class TestTimeParsingBugReproduction:
    """Reproduce the actual time parsing bug with real OpenAI calls."""
    
    @pytest.fixture
    def config(self):
        """Get real config with OpenAI API key."""
        return Config()
    
    @pytest.fixture
    def parsing_service(self, config):
        """Create parsing service with real config."""
        return ParsingService(config)
    
    @pytest.mark.integration
    def test_reproduce_today_3am_vs_5am_bug(self, parsing_service):
        """
        REPRODUCE THE BUG: 'today 3am' works but 'today 5am' doesn't.
        
        This test uses the real OpenAI API to reproduce the exact bug.
        Mock time to be 00:45 UTC (after midnight) to simulate the conditions.
        """
        # Mock current time to 00:45 UTC on June 29, 2025 (after midnight)
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            
            # Test 1: "today 3am" - should work correctly
            result_3am = parsing_service.parse_content_to_task(
                content_message="remind me about something today 3am",
                owner_name="Test User",
                location="UTC"
            )
            
            # Test 2: "today 5am" - reproduces the bug  
            result_5am = parsing_service.parse_content_to_task(
                content_message="remind me about something today 5am",
                owner_name="Test User",
                location="UTC"
            )
            
            print(f"\n=== BUG REPRODUCTION RESULTS ===")
            print(f"Current time: {test_time}")
            print(f"'today 3am' → {result_3am['due_time']}")
            print(f"'today 5am' → {result_5am['due_time']}")
            
            # Analyze the bug
            if "2025-06-29" in result_3am['due_time'] and "2025-06-28" in result_5am['due_time']:
                print(f"✅ BUG REPRODUCED: 3am scheduled for tomorrow, 5am scheduled for yesterday")
                
                # This is the bug - both should be scheduled for tomorrow
                assert False, f"BUG CONFIRMED: 'today 3am' → {result_3am['due_time']}, 'today 5am' → {result_5am['due_time']}"
            
            elif "2025-06-29" in result_3am['due_time'] and "2025-06-29" in result_5am['due_time']:
                print(f"✅ BUG FIXED: Both scheduled for same day (June 29) correctly")
                
            elif "2025-06-30" in result_3am['due_time'] and "2025-06-30" in result_5am['due_time']:
                print(f"✅ BUG FIXED: Both scheduled for next day (June 30) correctly")
                # This is also acceptable - when it's 00:45, "today 3am" could mean "next 3am" (June 30)
                
            else:
                print(f"❓ UNEXPECTED: 3am → {result_3am['due_time']}, 5am → {result_5am['due_time']}")
                assert False, f"Unexpected behavior: 3am → {result_3am['due_time']}, 5am → {result_5am['due_time']}"
    
    @pytest.mark.integration
    def test_control_explicit_tomorrow(self, parsing_service):
        """Control test: 'tomorrow 9am' should always work correctly."""
        test_time = datetime(2025, 6, 29, 0, 45, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            
            result = parsing_service.parse_content_to_task(
                content_message="remind me about something tomorrow 9am",
                owner_name="Test User",
                location="UTC"
            )
            
            print(f"\nControl test - 'tomorrow 9am' → {result['due_time']}")
            assert "2025-06-30" in result['due_time'], f"Expected June 30, got {result['due_time']}"