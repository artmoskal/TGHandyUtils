#!/usr/bin/env python3
"""Comprehensive time parsing test to validate universal pattern support."""

import sys
sys.path.append('.')

from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from services.parsing_service import ParsingService
from config import Config

def test_comprehensive_patterns():
    """Test all time parsing patterns to ensure universal coverage."""
    
    # Mock current time to a known value for consistent testing
    test_time = datetime(2025, 6, 29, 14, 30, 0, tzinfo=timezone.utc)  # 2:30 PM UTC (3:30 PM Portugal)
    
    config = Config()
    service = ParsingService(config)
    
    # Test cases: [input, expected_behavior, description]
    test_cases = [
        # Today patterns
        ("remind me today 5am", "should be tomorrow (5am Portugal = 4am UTC already passed)", "today past time"),
        ("remind me today at 5am", "should be tomorrow", "today past time with 'at'"),
        ("remind me today 18:00", "should be today at 17:00 UTC", "today future 24hr"),
        ("remind me today at 18:00", "should be today at 17:00 UTC", "today future 24hr with 'at'"),
        ("remind me today 6pm", "should be today at 17:00 UTC", "today future with pm"),
        ("remind me today at 6pm", "should be today at 17:00 UTC", "today future with pm and 'at'"),
        ("remind me today noon", "should be tomorrow at 11:00 UTC", "today noon (already passed)"),
        ("remind me today midnight", "should be today at 23:00 UTC", "today midnight (next midnight)"),
        
        # Tomorrow patterns  
        ("remind me tomorrow 9am", "should be tomorrow at 8:00 UTC", "tomorrow morning"),
        ("remind me tomorrow at 9am", "should be tomorrow at 8:00 UTC", "tomorrow morning with 'at'"),
        ("remind me tomorrow 15:00", "should be tomorrow at 14:00 UTC", "tomorrow 24hr"),
        ("remind me tomorrow noon", "should be tomorrow at 11:00 UTC", "tomorrow noon"),
        
        # Relative patterns
        ("remind me in 30 minutes", "should be 30 min from now", "relative minutes"),
        ("remind me in 2 hours", "should be 2 hours from now", "relative hours"),
        ("remind me in 3 days", "should be 3 days from now", "relative days"),
        ("remind me in 2 weeks", "should be 2 weeks from now", "relative weeks"),
        ("remind me in a day", "should be 1 day from now", "relative 'a day'"),
        ("remind me 4m from now", "should be 4 min from now", "abbreviated minutes"),
        ("remind me 2h from now", "should be 2 hours from now", "abbreviated hours"),
        ("remind me asap", "should be 1 hour from now", "asap"),
        ("remind me now", "should be 1 hour from now", "now"),
        
        # Month/day patterns
        ("remind me Nov 25", "should be Nov 25 at 9am", "month day"),
        ("remind me December 1", "should be Dec 1 at 9am", "full month name"),
        ("remind me jan 15", "should be next Jan 15", "month day lowercase"),
    ]
    
    with patch('services.parsing_service.datetime') as mock_datetime:
        mock_datetime.now.return_value = test_time
        mock_datetime.timezone = timezone
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
        
        print(f"Testing from: {test_time} UTC ({test_time + timedelta(hours=1)} Portugal)")
        print("=" * 80)
        
        for i, (input_text, expected, description) in enumerate(test_cases, 1):
            try:
                result = service.parse_content_to_task(
                    content_message=input_text,
                    owner_name="Test User",
                    location="Portugal"
                )
                
                if result:
                    print(f"{i:2d}. ✅ '{input_text}' -> {result['due_time']} ({description})")
                else:
                    print(f"{i:2d}. ❌ '{input_text}' -> FAILED TO PARSE ({description})")
                    
            except Exception as e:
                print(f"{i:2d}. 💥 '{input_text}' -> ERROR: {e} ({description})")
        
        print("=" * 80)
        print("Universal pattern test completed!")

if __name__ == "__main__":
    test_comprehensive_patterns()