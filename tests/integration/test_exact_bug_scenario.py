"""Test the exact bug scenario reported by user: time 23:39 UTC."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from services.parsing_service import ParsingService
from config import Config


class TestExactBugScenario:
    """Test the exact scenario reported: current time 23:39 UTC on June 28."""
    
    @pytest.fixture
    def config(self):
        return Config()
    
    @pytest.fixture
    def parsing_service(self, config):
        return ParsingService(config)
    
    @pytest.mark.integration
    def test_exact_user_reported_scenario(self, parsing_service):
        """
        Test the EXACT scenario user reported:
        - Current time: June 28, 2025 at 23:39 UTC
        - "today 3am" worked (should schedule for June 29 at 02:00 UTC)  
        - "today 5am" failed (scheduled for June 28 at 05:00 UTC - YESTERDAY)
        """
        # Exact time when user reported the bug
        test_time = datetime(2025, 6, 28, 23, 39, 0, tzinfo=timezone.utc)
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = test_time
            mock_datetime.timezone = timezone
            
            # Test the exact cases user reported
            result_3am = parsing_service.parse_content_to_task(
                content_message="remind me about something today 3am",
                owner_name="Artem Moskalenko",
                location=None  # User didn't specify location
            )
            
            result_5am = parsing_service.parse_content_to_task(
                content_message="remind me about something today 5am", 
                owner_name="Artem Moskalenko",
                location=None
            )
            
            print(f"\n=== EXACT BUG SCENARIO TEST ===")
            print(f"Current time: {test_time} (June 28, 23:39 UTC)")
            print(f"'today 3am' → {result_3am['due_time']}")
            print(f"'today 5am' → {result_5am['due_time']}")
            
            # Check if this reproduces the exact bug from the database
            # Task 122: "today 3am" → "2025-06-29T02:00:00Z" (correct)
            # Task 123: "today 5am" → "2025-06-28T05:00:00Z" (bug - yesterday!)
            
            if ("2025-06-29" in result_3am['due_time'] and 
                "2025-06-28" in result_5am['due_time'] and
                "05:00" in result_5am['due_time']):
                
                print("🐛 BUG REPRODUCED: 'today 5am' scheduled for yesterday!")
                assert False, f"BUG CONFIRMED: 3am → {result_3am['due_time']}, 5am → {result_5am['due_time']}"
                
            elif ("2025-06-29" in result_3am['due_time'] and 
                  "2025-06-29" in result_5am['due_time']):
                
                print("✅ BUG FIXED: Both correctly scheduled for tomorrow")
                # This means the current prompt fixes the issue
                
            else:
                print(f"❓ UNEXPECTED BEHAVIOR")
                print(f"Expected either: Bug reproduced OR both scheduled for June 29")
                print(f"Got: 3am → {result_3am['due_time']}, 5am → {result_5am['due_time']}")
                assert False, f"Unexpected: 3am → {result_3am['due_time']}, 5am → {result_5am['due_time']}"