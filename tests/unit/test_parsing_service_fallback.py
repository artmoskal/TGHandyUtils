"""Unit tests for parsing service fallback mechanisms and error handling."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone, timedelta

from services.parsing_service import ParsingService
from core.exceptions import ParsingError
from models.unified_recipient import UnifiedUserPreferences


pytestmark = pytest.mark.unit
class TestParsingServiceFallback:
    """Test fallback mechanisms when LLM fails."""
    
    @pytest.fixture(autouse=True)
    def mock_chat_openai(self):
        """Mock ChatOpenAI for all tests in this class."""
        with patch('services.parsing_service.ChatOpenAI') as mock:
            mock.return_value = Mock()
            yield mock
    
    @pytest.fixture
    def mock_config(self):
        config = Mock()
        config.OPENAI_API_KEY = "test-key"
        return config
    
    @pytest.fixture
    def mock_preferences_repo(self):
        return Mock()
    
    @pytest.fixture
    def parsing_service(self, mock_config, mock_preferences_repo):
        service = ParsingService(mock_config, mock_preferences_repo)
        # Mock the LLM after creation
        service.llm = Mock()
        return service
    
    def test_llm_failure_fallback_to_static_patterns(self, parsing_service):
        """Test that static patterns work when LLM fails."""
        # Mock LLM to fail
        parsing_service.llm.invoke.side_effect = Exception("LLM API Error")
        
        # Set up consistent datetime mock for all tests
        with patch('services.parsing_service.datetime') as mock_datetime:
            current_time = datetime(2025, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
            mock_datetime.now.return_value = current_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            # Also need to patch datetime in the actual method
            mock_datetime.strptime = datetime.strptime
            mock_datetime.timedelta = timedelta
            
            # Test Pattern 1: "tomorrow 3pm"
            result = parsing_service.parse_content_to_task(
                content_message="remind me tomorrow 3pm",
                owner_name="Test User",
                location="UTC"
            )
            assert result is not None, "Failed to parse tomorrow pattern"
            assert result['title'] == "remind me tomorrow 3pm"
            assert result['description'] == "remind me tomorrow 3pm"
            due_time = datetime.fromisoformat(result['due_time'].replace('Z', '+00:00'))
            # Tomorrow 3pm from 10am should be ~29 hours
            time_diff_hours = (due_time - current_time).total_seconds() / 3600
            assert 28 < time_diff_hours < 30, f"Expected ~29 hours, got {time_diff_hours}"
            
            # Test Pattern 2: "in 2 hours"
            result = parsing_service.parse_content_to_task(
                content_message="task in 2 hours",
                owner_name="Test User",
                location="UTC"
            )
            assert result is not None, "Failed to parse relative time pattern"
            due_time = datetime.fromisoformat(result['due_time'].replace('Z', '+00:00'))
            time_diff_hours = (due_time - current_time).total_seconds() / 3600
            assert 1.9 < time_diff_hours < 2.1, f"Expected ~2 hours, got {time_diff_hours}"
            
            # Test Pattern 3: "today 5am" (should be tomorrow since current is 10am)
            result = parsing_service.parse_content_to_task(
                content_message="meeting today 5am",
                owner_name="Test User",
                location="UTC"
            )
            assert result is not None, "Failed to parse today pattern"
            
            # Test Pattern 4: "asap"
            result = parsing_service.parse_content_to_task(
                content_message="call asap",
                owner_name="Test User",
                location="UTC"
            )
            assert result is not None, "Failed to parse asap pattern"
            due_time = datetime.fromisoformat(result['due_time'].replace('Z', '+00:00'))
            time_diff_hours = (due_time - current_time).total_seconds() / 3600
            assert 0.9 < time_diff_hours < 1.1, f"Expected ~1 hour for asap, got {time_diff_hours}"
    
    def test_static_fallback_with_timezone_offset(self, parsing_service, mock_preferences_repo):
        """Test static patterns respect user timezone."""
        # Setup user with Portugal timezone (UTC+1)
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1
        )
        
        # Mock LLM failure
        with patch.object(parsing_service.llm, 'invoke', side_effect=Exception("LLM Error")):
            with patch('services.parsing_service.datetime') as mock_datetime:
                # Current UTC: 10:00, Portugal: 11:00
                current_time = datetime(2025, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
                mock_datetime.now.return_value = current_time
                mock_datetime.timezone = timezone
                mock_datetime.fromisoformat = datetime.fromisoformat
                
                result = parsing_service.parse_content_to_task(
                    content_message="meeting today 2pm",
                    owner_name="Test User",
                    user_id=123
                )
                
                assert result is not None
                # 2pm Portugal = 13:00 UTC
                due_time = datetime.fromisoformat(result['due_time'].replace('Z', '+00:00'))
                assert due_time.hour == 13
    
    def test_both_llm_and_static_fallback_fail(self, parsing_service):
        """Test error when both LLM and static patterns fail."""
        # Mock LLM failure
        parsing_service.llm.invoke.side_effect = Exception("LLM Error")
        
        # Use a message that doesn't match any static pattern
        with pytest.raises(ParsingError) as exc_info:
            parsing_service.parse_content_to_task(
                content_message="do something sometime maybe",
                owner_name="Test User"
            )
        
        assert "Both LLM and static parsing failed" in str(exc_info.value)
    
    def test_timezone_calculation_error_handling(self, parsing_service):
        """Test handling of timezone calculation errors."""
        # Test with invalid timezone that causes calculation error
        with patch('services.parsing_service.zoneinfo.ZoneInfo') as mock_zoneinfo:
            mock_zoneinfo.side_effect = Exception("Invalid timezone")
            
            offset = parsing_service.get_timezone_offset("InvalidCity")
            assert offset == 0  # Should default to UTC
    
    def test_timezone_offset_overflow_protection(self, parsing_service):
        """Test protection against timezone offset overflow."""
        # Mock a timezone with extreme offset
        with patch('services.parsing_service.zoneinfo.ZoneInfo') as mock_zoneinfo:
            mock_tz = Mock()
            mock_local_time = Mock()
            
            # Mock an extreme offset (e.g., 48 hours) 
            mock_local_time.utcoffset.return_value.total_seconds.return_value = 48 * 3600
            
            with patch('services.parsing_service.datetime') as mock_datetime:
                mock_now = Mock()
                mock_now.astimezone.return_value = mock_local_time
                mock_datetime.now.return_value = mock_now
                mock_zoneinfo.return_value = mock_tz
                
                offset = parsing_service.get_timezone_offset("ExtremePlace")
                assert offset == 0  # Should default to UTC due to bounds check
    
    def test_invalid_location_handling(self, parsing_service):
        """Test handling of various invalid locations."""
        invalid_locations = [
            None,
            "",
            "   ",  # Whitespace only
            "UnknownRandomCity12345",
            "!!!@@@###",  # Special characters
        ]
        
        for location in invalid_locations:
            offset = parsing_service.get_timezone_offset(location)
            assert offset == 0, f"Location '{location}' should return UTC offset"
    
    def test_static_pattern_edge_cases(self, parsing_service):
        """Test edge cases in static pattern matching."""
        # Mock LLM failure
        parsing_service.llm.invoke.side_effect = Exception("LLM Error")
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            current_time = datetime(2025, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
            mock_datetime.now.return_value = current_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.strptime = datetime.strptime
            
            # Test valid edge case: "today midnight" (00:00)
            result = parsing_service.parse_content_to_task("meeting today midnight", "Test")
            assert result is not None
            due_time = datetime.fromisoformat(result['due_time'].replace('Z', '+00:00'))
            assert due_time.hour == 0
            
            # Test relative time edge case: "in 999 hours" should work
            result = parsing_service.parse_content_to_task("task in 999 hours", "Test")
            assert result is not None
            
            # Test invalid patterns that should fail
            invalid_patterns = [
                "in 0 minutes",  # Zero time
                "Apr 31",  # Invalid date
                "random text",  # No pattern
            ]
            
            for pattern in invalid_patterns:
                try:
                    result = parsing_service.parse_content_to_task(pattern, "Test")
                    # If it succeeds, it should be because of fallback to default
                    assert result is not None
                except ParsingError:
                    # Expected for patterns that don't match
                    pass
    
    def test_cached_offset_with_llm_failure(self, parsing_service, mock_preferences_repo):
        """Test that cached UTC offset is used when LLM fails."""
        # Setup user with cached offset
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="New York",
            utc_offset=-5  # EST
        )
        
        # Mock LLM failure
        parsing_service.llm.invoke.side_effect = Exception("LLM Error")
        
        # Test should work with cached offset
        result = parsing_service.parse_content_to_task(
            content_message="meeting in 2 hours",
            owner_name="Test User",
            user_id=123
        )
        
        # Should work using cached offset
        assert result is not None
        assert "meeting in 2 hours" in result['description']
    
    def test_static_pattern_with_past_time_handling(self, parsing_service):
        """Test how static patterns handle past times."""
        # Mock LLM failure
        parsing_service.llm.invoke.side_effect = Exception("LLM Error")
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            # Current time: 15:00 UTC (3pm)
            current_time = datetime(2025, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
            mock_datetime.now.return_value = current_time
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            mock_datetime.strptime = datetime.strptime
            mock_datetime.timedelta = timedelta
            
            # "at 2pm" when it's already 3pm - should schedule for tomorrow
            result = parsing_service.parse_content_to_task(
                content_message="remind me at 2pm",
                owner_name="Test User"
            )
            
            assert result is not None
            due_time = datetime.fromisoformat(result['due_time'].replace('Z', '+00:00'))
            # Should be tomorrow at 14:00
            assert due_time.day == 16  # Next day
            assert due_time.hour == 14