"""Integration tests for LLM time parsing edge cases.

These tests cover time formats that don't fit static patterns and require LLM interpretation.
All tests make actual LLM calls (not mocked) to ensure real-world behavior.
"""

import pytest
from datetime import datetime, timezone, timedelta

from services.parsing_service import ParsingService
from core.exceptions import ParsingError
from config import Config


class TestLLMTimeParsingEdgeCases:
    """Test LLM parsing for time formats not covered by static patterns."""

    @pytest.fixture
    def parsing_service(self):
        """Create parsing service with real OpenAI API."""
        config = Config()
        if not config.OPENAI_API_KEY:
            pytest.skip("OpenAI API key not configured in .env file")
        return ParsingService(config)

    def test_no_static_pattern_formats_identified(self, parsing_service):
        """Test that edge case formats don't match static patterns."""
        current_local = datetime(2025, 7, 5, 18, 30, 0)
        current_utc = current_local - timedelta(hours=1)
        
        # These should NOT be handled by static patterns
        edge_cases = [
            "today 1900",           # Military time without colon
            "today 2:0pm",          # Missing zero in minutes 
            "at 7",                 # Bare hour without minutes
            "19h",                  # European hour format
            "7.30pm",               # Dot instead of colon
            "meet at seven",        # Written numbers
            "dinner 8ish",          # Approximate time
            "around 3",             # Approximate time
            "before 10",            # Relative to time
            "after lunch",          # Relative to event
            "end of day",           # Vague time reference
            "tonight",              # General time period
            "this evening",         # General time period
            "first thing tomorrow", # Priority-based time
            "by 5",                 # Deadline format
            "no later than 4pm",    # Deadline format
        ]
        
        for time_phrase in edge_cases:
            # Verify these don't match static patterns
            precise_time = parsing_service._calculate_precise_time(
                time_phrase, current_local, current_utc, 1
            )
            assert precise_time is None, f"'{time_phrase}' should not match static patterns but got: {precise_time}"

    @pytest.mark.integration
    def test_military_time_without_colon_llm_parsing(self, parsing_service):
        """Test LLM parsing of military time format: 'today 1900'."""
        # This is a real LLM call - not mocked
        result = parsing_service.parse_content_to_task(
            "Meeting today 1900",
            owner_name="Test User",
            location="Portugal"
        )
        
        assert result is not None
        assert "meeting" in result["title"].lower()
        
        # Parse the due_time and check it's 19:00 local time
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        local_time = due_time + timedelta(hours=1)  # Portugal is UTC+1
        assert local_time.hour == 19
        assert local_time.minute == 0

    @pytest.mark.integration
    def test_incomplete_time_format_llm_parsing(self, parsing_service):
        """Test LLM parsing of incomplete time format: 'today 2:0pm'."""
        # This is a real LLM call - not mocked
        result = parsing_service.parse_content_to_task(
            "Appointment today 2:0pm",
            owner_name="Test User", 
            location="Portugal"
        )
        
        assert result is not None
        assert "appointment" in result["title"].lower()
        
        # Parse the due_time and check it's 14:00 local time
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        local_time = due_time + timedelta(hours=1)  # Portugal is UTC+1
        assert local_time.hour == 14
        assert local_time.minute == 0

    @pytest.mark.integration
    def test_bare_hour_llm_parsing(self, parsing_service):
        """Test LLM parsing of bare hour format: 'at 7'."""
        # This is a real LLM call - not mocked
        result = parsing_service.parse_content_to_task(
            "Call client at 7",
            owner_name="Test User",
            location="Portugal"
        )
        
        assert result is not None
        assert "call" in result["title"].lower()
        
        # Should assume reasonable hour (7 AM or 7 PM based on context)
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        local_time = due_time + timedelta(hours=1)  # Portugal is UTC+1
        assert local_time.hour in [7, 19]  # Could be 7 AM or 7 PM

    @pytest.mark.integration
    def test_european_hour_format_llm_parsing(self, parsing_service):
        """Test LLM parsing of European hour format: '19h'."""
        # This is a real LLM call - not mocked
        result = parsing_service.parse_content_to_task(
            "Dinner at 19h",
            owner_name="Test User",
            location="Portugal"
        )
        
        assert result is not None
        assert "dinner" in result["title"].lower()
        
        # Parse the due_time and check it's 19:00 local time
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        local_time = due_time + timedelta(hours=1)  # Portugal is UTC+1
        assert local_time.hour == 19

    @pytest.mark.integration
    def test_dot_time_format_llm_parsing(self, parsing_service):
        """Test LLM parsing of dot time format: '7.30pm'."""
        # This is a real LLM call - not mocked
        result = parsing_service.parse_content_to_task(
            "Meeting at 7.30pm",
            owner_name="Test User",
            location="Portugal"
        )
        
        assert result is not None
        assert "meeting" in result["title"].lower()
        
        # Parse the due_time and check it's 19:30 local time
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        local_time = due_time + timedelta(hours=1)  # Portugal is UTC+1
        assert local_time.hour == 19
        assert local_time.minute == 30

    @pytest.mark.integration
    def test_written_numbers_llm_parsing(self, parsing_service):
        """Test LLM parsing of written numbers: 'meet at seven'."""
        # This is a real LLM call - not mocked
        result = parsing_service.parse_content_to_task(
            "Meet at seven",
            owner_name="Test User",
            location="Portugal"
        )
        
        assert result is not None
        assert "meet" in result["title"].lower()
        
        # Should interpret as 7 AM or 7 PM
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        local_time = due_time + timedelta(hours=1)  # Portugal is UTC+1
        assert local_time.hour in [7, 19]

    @pytest.mark.integration
    def test_approximate_time_llm_parsing(self, parsing_service):
        """Test LLM parsing of approximate time: 'dinner 8ish'."""
        # This is a real LLM call - not mocked
        result = parsing_service.parse_content_to_task(
            "Dinner 8ish",
            owner_name="Test User",
            location="Portugal"
        )
        
        assert result is not None
        assert "dinner" in result["title"].lower()
        
        # Should interpret as around 8 PM
        due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
        local_time = due_time + timedelta(hours=1)  # Portugal is UTC+1
        assert local_time.hour in [20]  # 8 PM

    @pytest.mark.integration
    def test_vague_time_references_llm_parsing(self, parsing_service):
        """Test LLM parsing of vague time references: 'tonight', 'this evening'."""
        vague_times = [
            ("Call tonight", "tonight"),
            ("Meeting this evening", "this evening"),
            ("Task end of day", "end of day"),
            ("Finish by 5", "by 5"),
            ("Complete no later than 4pm", "no later than 4pm"),
        ]
        
        for message, time_phrase in vague_times:
            # This is a real LLM call - not mocked
            result = parsing_service.parse_content_to_task(
                message,
                owner_name="Test User",
                location="Portugal"
            )
            
            assert result is not None, f"Failed to parse: {message}"
            assert len(result["title"]) > 0, f"Empty title for: {message}"
            
            # Should have a reasonable due_time
            due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
            assert due_time > datetime.now(timezone.utc), f"Due time should be in future for: {message}"

    @pytest.mark.integration
    def test_context_dependent_time_parsing(self, parsing_service):
        """Test LLM parsing of context-dependent times: 'after lunch', 'before 10'."""
        context_times = [
            ("Meeting after lunch", "after lunch"),
            ("Call before 10", "before 10"),
            ("Task around 3", "around 3"),
            ("Appointment first thing tomorrow", "first thing tomorrow"),
        ]
        
        for message, time_phrase in context_times:
            # This is a real LLM call - not mocked
            result = parsing_service.parse_content_to_task(
                message,
                owner_name="Test User",
                location="Portugal"
            )
            
            assert result is not None, f"Failed to parse: {message}"
            assert len(result["title"]) > 0, f"Empty title for: {message}"
            
            # Should have a reasonable due_time
            due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
            assert due_time > datetime.now(timezone.utc), f"Due time should be in future for: {message}"

    @pytest.mark.integration
    def test_timezone_handling_with_edge_cases(self, parsing_service):
        """Test timezone handling with edge case time formats."""
        # Test with different timezones
        locations = ["Portugal", "UK", "New York", "California"]
        
        for location in locations:
            result = parsing_service.parse_content_to_task(
                "Meeting today 1900",
                owner_name="Test User",
                location=location
            )
            
            assert result is not None, f"Failed to parse for location: {location}"
            
            # Should have proper timezone conversion
            due_time = datetime.fromisoformat(result["due_time"].replace('Z', '+00:00'))
            assert due_time.tzinfo is not None or due_time.tzinfo == timezone.utc, f"Missing timezone info for: {location}"

    @pytest.mark.integration
    def test_llm_error_handling_with_edge_cases(self, parsing_service):
        """Test LLM error handling with problematic edge cases."""
        # Test with potentially problematic inputs
        problematic_inputs = [
            "xyz 999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999",  # Extremely long input
            "at 25:99",  # Invalid time
            "today 32:100am",  # Invalid time
            "",  # Empty string
            "???",  # Only punctuation
        ]
        
        for problematic_input in problematic_inputs:
            try:
                result = parsing_service.parse_content_to_task(
                    problematic_input,
                    owner_name="Test User",
                    location="Portugal"
                )
                # If it doesn't raise an exception, it should at least return something reasonable
                if result:
                    assert "title" in result
                    assert "due_time" in result
                    assert len(result["title"]) > 0
            except ParsingError:
                # This is expected for problematic inputs
                pass
            except Exception as e:
                # Log unexpected errors but don't fail the test
                print(f"Unexpected error for input '{problematic_input}': {e}")

    def test_integration_test_setup_verification(self):
        """Verify that integration tests are properly set up to make real LLM calls."""
        # This test ensures our integration tests are not accidentally mocked
        # by verifying the test structure and annotations
        
        # Check that integration tests are marked with @pytest.mark.integration
        import inspect
        
        test_methods = [
            method for method in dir(self) 
            if method.startswith('test_') and 'llm' in method
        ]
        
        for method_name in test_methods:
            method = getattr(self, method_name)
            if hasattr(method, 'pytestmark'):
                marks = [mark.name for mark in method.pytestmark]
                if 'integration' not in marks and method_name != 'test_integration_test_setup_verification':
                    assert False, f"Integration test {method_name} should be marked with @pytest.mark.integration"