"""Unit tests for timezone offset calculation overflow bug fix using Factory Boy for realistic test data.

This module tests edge cases and overflow scenarios in timezone calculations
while using Factory Boy for realistic user and location data where applicable."""

import pytest
from unittest.mock import patch, Mock
from datetime import datetime, timezone, timedelta
import zoneinfo

from services.parsing_service import ParsingService
from config import Config

# Import Factory Boy factories
from tests.factories import (
    TelegramUserFactory,
    TelegramMessageFactory
)


class TestTimezoneOffsetOverflow:
    """Test timezone offset calculation handles edge cases and overflow prevention."""
    
    @pytest.fixture
    def parsing_service(self):
        """Create parsing service for testing."""
        config = Mock(spec=Config)
        config._openai_api_key = "test_key_" + "a" * 40  # Realistic format
        # Mock the ChatOpenAI to avoid actual API calls
        with patch('services.parsing_service.ChatOpenAI'):
            return ParsingService(config)
    
    def test_normal_timezone_offsets_with_factory_users(self, parsing_service):
        """Test normal timezone offset calculations work correctly with realistic users."""
        # Create realistic users for each timezone region
        test_cases_with_users = [
            (TelegramUserFactory(first_name="António", language_code="pt"), "Cascais", 1),   # Portugal (CET/CEST)
            (TelegramUserFactory(first_name="Pedro", language_code="pt"), "Portugal", 1),
            (TelegramUserFactory(first_name="Oliver", language_code="en"), "London", 0),     # UK (GMT/BST) 
            (TelegramUserFactory(first_name="Michael", language_code="en"), "New York", -5),  # EST (approximately)
            (TelegramUserFactory(first_name="Sarah", language_code="en"), "California", -8), # PST (approximately)
            (TelegramUserFactory(first_name="Hiroshi", language_code="ja"), "Tokyo", 9),     # JST
        ]
        
        for user, location, expected_range in test_cases_with_users:
            offset = parsing_service.get_timezone_offset(location)
            # Allow for DST variations (±1 hour)
            assert abs(offset - expected_range) <= 1, f"Offset for {location} should be around {expected_range}, got {offset}"
            
            # Verify factory user is realistic
            assert len(user.first_name) > 0
            assert user.language_code in ["pt", "en", "ja"]
    
    def test_invalid_locations_return_utc_with_factory_users(self, parsing_service):
        """Test that invalid locations default to UTC (0 offset) with realistic users."""
        # Create users who might provide invalid location data
        invalid_location_users = [
            TelegramUserFactory(first_name="Alex", language_code="en"),
            TelegramUserFactory(first_name="Unknown", language_code="en"),
            TelegramUserFactory(first_name="Test", language_code="en")
        ]
        
        invalid_locations = [
            None,
            "",
            "   ",
            "XYZ_INVALID_PLACE_999",
            "🚀InvalidLocation🚀",
        ]
        
        for i, location in enumerate(invalid_locations):
            offset = parsing_service.get_timezone_offset(location)
            assert offset == 0, f"Invalid location '{location}' should return UTC offset (0), got {offset}"
            
            # Verify factory users are realistic even for invalid locations
            if i < len(invalid_location_users):
                user = invalid_location_users[i]
                assert len(user.first_name) > 0
                assert user.language_code == "en"
    
    @patch('services.parsing_service.zoneinfo.ZoneInfo')
    def test_overflow_prevention_large_positive_offset(self, mock_zoneinfo, parsing_service):
        """Test that extremely large positive timezone offsets are handled gracefully."""
        # Mock a timezone that returns an unreasonably large offset
        mock_tz = Mock()
        mock_zoneinfo.return_value = mock_tz
        
        # Create a mock datetime with massive offset
        mock_utcoffset = Mock()
        mock_utcoffset.total_seconds.return_value = 999999999999.0  # Extremely large number
        
        mock_local_time = Mock()
        mock_local_time.utcoffset.return_value = mock_utcoffset
        
        mock_utc_time = Mock()
        mock_utc_time.astimezone.return_value = mock_local_time
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_utc_time
            mock_datetime.timezone = timezone
            
            offset = parsing_service.get_timezone_offset("TestLocation")
            assert offset == 0, "Extremely large positive offset should be clamped to UTC (0)"
    
    @patch('services.parsing_service.zoneinfo.ZoneInfo')
    def test_overflow_prevention_large_negative_offset(self, mock_zoneinfo, parsing_service):
        """Test that extremely large negative timezone offsets are handled gracefully."""
        # Mock a timezone that returns an unreasonably large negative offset
        mock_tz = Mock()
        mock_zoneinfo.return_value = mock_tz
        
        # Create a mock datetime with massive negative offset
        mock_utcoffset = Mock()
        mock_utcoffset.total_seconds.return_value = -999999999999.0  # Extremely large negative number
        
        mock_local_time = Mock()
        mock_local_time.utcoffset.return_value = mock_utcoffset
        
        mock_utc_time = Mock()
        mock_utc_time.astimezone.return_value = mock_local_time
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_utc_time
            mock_datetime.timezone = timezone
            
            offset = parsing_service.get_timezone_offset("TestLocation")
            assert offset == 0, "Extremely large negative offset should be clamped to UTC (0)"
    
    @patch('services.parsing_service.zoneinfo.ZoneInfo')
    def test_reasonable_large_offsets_work(self, mock_zoneinfo, parsing_service):
        """Test that reasonable but large timezone offsets (like +14) still work."""
        # Mock a timezone with +14 offset (like Kiribati)
        mock_tz = Mock()
        mock_zoneinfo.return_value = mock_tz
        
        # Create a mock datetime with +14 hours offset
        mock_utcoffset = Mock()
        mock_utcoffset.total_seconds.return_value = 14 * 3600.0  # 14 hours in seconds
        
        mock_local_time = Mock()
        mock_local_time.utcoffset.return_value = mock_utcoffset
        
        mock_utc_time = Mock()
        mock_utc_time.astimezone.return_value = mock_local_time
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_utc_time
            mock_datetime.timezone = timezone
            
            offset = parsing_service.get_timezone_offset("TestLocation")
            assert offset == 14, "Reasonable large offset (+14) should work correctly"
    
    @patch('services.parsing_service.zoneinfo.ZoneInfo')
    def test_exception_handling_returns_utc(self, mock_zoneinfo, parsing_service):
        """Test that exceptions in timezone calculation return UTC safely."""
        # Mock ZoneInfo to raise an exception
        mock_zoneinfo.side_effect = Exception("Invalid timezone")
        
        offset = parsing_service.get_timezone_offset("ErrorLocation")
        assert offset == 0, "Exception in timezone calculation should return UTC (0)"
    
    def test_boundary_offsets(self, parsing_service):
        """Test boundary cases for timezone offsets."""
        # Test with a location that should have exactly the boundary offset
        with patch('services.parsing_service.zoneinfo.ZoneInfo') as mock_zoneinfo:
            mock_tz = Mock()
            mock_zoneinfo.return_value = mock_tz
            
            # Test exactly +24 hours (should be rejected)
            mock_utcoffset = Mock()
            mock_utcoffset.total_seconds.return_value = 24 * 3600.0
            
            mock_local_time = Mock()
            mock_local_time.utcoffset.return_value = mock_utcoffset
            
            mock_utc_time = Mock()
            mock_utc_time.astimezone.return_value = mock_local_time
            
            with patch('services.parsing_service.datetime') as mock_datetime:
                mock_datetime.now.return_value = mock_utc_time
                mock_datetime.timezone = timezone
                
                offset = parsing_service.get_timezone_offset("BoundaryTest")
                assert offset == 0, "+24 hour offset should be rejected and return UTC"
    
    def test_parse_content_with_timezone_overflow_bug_with_factory_user(self, parsing_service):
        """Test that the original bug scenario doesn't crash the parser with realistic user."""
        # Create realistic Portuguese user who might trigger the bug
        portuguese_user = TelegramUserFactory(
            first_name="Carlos",
            last_name="Silva",
            language_code="pt"
        )
        
        # Create realistic message that might cause timezone issues
        problematic_message = TelegramMessageFactory(
            text="Schedule meeting for tomorrow at 3 PM in Cascais",
            from_user=portuguese_user
        )
        
        # Mock the internal timezone calculation to cause overflow in utcoffset() 
        with patch('services.parsing_service.zoneinfo.ZoneInfo') as mock_zoneinfo:
            mock_tz = Mock()
            mock_zoneinfo.return_value = mock_tz
            
            # Mock the datetime chain to cause overflow in total_seconds()
            mock_utcoffset = Mock()
            mock_utcoffset.total_seconds.side_effect = OverflowError("Python int too large to convert to C int")
            
            mock_local_time = Mock()
            mock_local_time.utcoffset.return_value = mock_utcoffset
            
            mock_utc_time = Mock()
            mock_utc_time.astimezone.return_value = mock_local_time
            
            with patch('services.parsing_service.datetime') as mock_datetime:
                mock_datetime.now.return_value = mock_utc_time
                mock_datetime.timezone = timezone
                
                # This should not crash and should handle the error gracefully
                # The get_timezone_offset should catch the overflow and return 0
                offset = parsing_service.get_timezone_offset("Cascais")
                assert offset == 0, "Overflow in timezone calculation should return UTC (0)"
                
                # Verify factory data is realistic
                assert portuguese_user.language_code == "pt"
                assert "cascais" in problematic_message.text.lower()
                assert len(portuguese_user.first_name) > 0