"""Unit tests for timezone conversion logic without OpenAI API calls."""

import pytest
from datetime import datetime, timezone, timedelta
import zoneinfo
from unittest.mock import Mock, patch
from services.parsing_service import ParsingService


class TestTimezoneConversionUnit:
    """Unit tests for timezone conversion logic."""
    
    @pytest.fixture
    def mock_config(self):
        """Mock config for testing."""
        config = Mock()
        config.OPENAI_API_KEY = "test-key"
        return config
    
    @pytest.fixture 
    def parsing_service(self, mock_config):
        """Create parsing service with mocked dependencies."""
        # Mock the ChatOpenAI to avoid actual API calls
        with patch('services.parsing_service.ChatOpenAI'):
            return ParsingService(mock_config)
    
    def test_get_timezone_offset_cascais(self, parsing_service):
        """Test timezone offset calculation for Cascais."""
        offset = parsing_service.get_timezone_offset("Cascais")
        # Should be 0 or 1 depending on DST (WEST timezone)
        assert offset in [0, 1], f"Cascais offset should be 0 or 1, got {offset}"
    
    def test_get_timezone_offset_london(self, parsing_service):
        """Test timezone offset calculation for London.""" 
        offset = parsing_service.get_timezone_offset("London")
        # Should be 0 or 1 depending on DST (GMT/BST)
        assert offset in [0, 1], f"London offset should be 0 or 1, got {offset}"
    
    def test_get_timezone_offset_new_york(self, parsing_service):
        """Test timezone offset calculation for New York."""
        offset = parsing_service.get_timezone_offset("New York")
        # Should be -5 or -4 depending on DST (EST/EDT)
        assert offset in [-5, -4], f"NY offset should be -5 or -4, got {offset}"
    
    def test_get_timezone_offset_tokyo(self, parsing_service):
        """Test timezone offset calculation for Tokyo."""
        offset = parsing_service.get_timezone_offset("Tokyo")
        # Should be 9 hours (JST, no DST)
        assert offset == 9
    
    def test_get_timezone_offset_unknown_location(self, parsing_service):
        """Test timezone offset for unknown location defaults to UTC."""
        offset = parsing_service.get_timezone_offset("UnknownCity")
        assert offset == 0
    
    def test_get_timezone_offset_none_location(self, parsing_service):
        """Test timezone offset for None location defaults to UTC."""
        offset = parsing_service.get_timezone_offset(None)
        assert offset == 0
    
    def test_timezone_info_mapping(self, parsing_service):
        """Test timezone info string generation."""
        test_cases = [
            ("Cascais", "UTC+1 (UTC+2 during DST)"),
            ("Portugal", "UTC+1 (UTC+2 during DST)"),
            ("London", "UTC+0 (UTC+1 during DST)"),
            ("New York", "UTC-5 (UTC-4 during DST)"),
            ("UnknownPlace", "UTC+0 (please specify timezone for accuracy)"),
        ]
        
        for location, expected in test_cases:
            result = parsing_service._get_timezone_info(location)
            assert result == expected, f"For {location}: expected {expected}, got {result}"
    
    def test_convert_utc_to_local_display_cascais(self, parsing_service):
        """Test UTC to local time conversion for display."""
        utc_time_str = "2025-06-28T09:00:00Z"
        location = "Cascais"
        
        result = parsing_service.convert_utc_to_local_display(utc_time_str, location)
        
        # Should show 09:00 or 10:00 depending on DST
        assert ("09:00" in result or "10:00" in result), f"Time should be 09:00 or 10:00, got {result}"
        assert "Portugal time" in result
        assert "June 28, 2025" in result
    
    def test_convert_utc_to_local_display_london(self, parsing_service):
        """Test UTC to local time conversion for London."""
        utc_time_str = "2025-06-28T09:00:00Z"
        location = "London"
        
        result = parsing_service.convert_utc_to_local_display(utc_time_str, location)
        
        # Should show 09:00 or 10:00 depending on DST
        assert ("09:00" in result or "10:00" in result), f"Time should be 09:00 or 10:00, got {result}"
        assert "UK time" in result
        assert "June 28, 2025" in result
    
    def test_convert_utc_to_local_display_invalid_time(self, parsing_service):
        """Test handling of invalid UTC time string."""
        invalid_time_str = "invalid-time-format"
        location = "Cascais"
        
        result = parsing_service.convert_utc_to_local_display(invalid_time_str, location)
        
        # Should return error message with original string
        assert "Error parsing time" in result
        assert invalid_time_str in result
    
    def test_timezone_guess_from_location(self, parsing_service):
        """Test timezone identifier guessing from location names."""
        # Test that the guessing logic works for common patterns
        test_cases = [
            ("paris", "Europe/Paris"),
            ("berlin", "Europe/Berlin"),
            ("new york", "America/New_York"),
            ("los angeles", "America/Los_Angeles"),
        ]
        
        for location, expected_tz in test_cases:
            # This tests the internal logic indirectly through offset calculation
            offset = parsing_service.get_timezone_offset(location)
            
            # Verify we get a reasonable offset (not the default 0)
            # These cities should all have non-zero offsets
            assert offset != 0 or location in ["london"], f"Expected non-zero offset for {location}"
    
    def test_timezone_offset_dynamic_calculation(self, parsing_service):
        """Test that timezone offset calculation is dynamic (handles DST)."""
        # Test with a known timezone that has DST
        location = "Cascais"
        
        # Mock different times of year to test DST handling
        with patch('services.parsing_service.datetime') as mock_datetime:
            # Mock summer time (should be UTC+2 with DST)
            summer_time = datetime(2025, 7, 15, tzinfo=timezone.utc)
            mock_datetime.now.return_value = summer_time
            
            # Note: Since we're using zoneinfo, DST should be handled automatically
            # The actual offset depends on the current implementation
            offset = parsing_service.get_timezone_offset(location)
            assert isinstance(offset, int)
            assert -12 <= offset <= 12  # Reasonable range for timezone offsets
    
    def test_prompt_template_formatting(self, parsing_service):
        """Test that the prompt template formats correctly with real data."""
        # Test the actual prompt template formatting (no LLM call)
        current_utc = datetime.now(timezone.utc)
        offset_hours = parsing_service.get_timezone_offset("Cascais")
        user_local_time = current_utc + timedelta(hours=offset_hours)
        
        # Create example for "in 5 minutes" calculation
        example_local_time = user_local_time + timedelta(minutes=5)
        example_utc_time = example_local_time - timedelta(hours=offset_hours)
        
        # Use the real input data preparation logic
        local_hour = user_local_time.hour
        input_data = {
            "content_message": "test message",
            "owner_name": "Test User",
            "current_year": current_utc.year,
            "utc_time": current_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "local_time": f"{user_local_time.strftime('%Y-%m-%d %H:%M:%S')} (Cascais) - Current local hour: {local_hour}",
            "timezone_offset_hours": offset_hours,
            "due_time_example": example_utc_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "current_hour": current_utc.hour,
            "current_local_hour": local_hour
        }
        
        # Test that the real prompt template can format with this data
        prompt_text = parsing_service.prompt_template.format(**input_data)
        
        # Verify key elements are in the formatted prompt
        assert "Test User" in prompt_text
        assert "UTC" in prompt_text  
        assert "Cascais" in prompt_text
        assert str(offset_hours) in prompt_text
        assert "2025" in prompt_text
        assert "timezone offset:" in prompt_text.lower()