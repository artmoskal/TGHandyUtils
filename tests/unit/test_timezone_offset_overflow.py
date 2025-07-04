"""Unit tests for timezone offset calculation overflow bug fix."""

import pytest
from unittest.mock import patch, Mock
from datetime import datetime, timezone, timedelta
import zoneinfo

from services.parsing_service import ParsingService
from config import Config


class TestTimezoneOffsetOverflow:
    """Test timezone offset calculation handles edge cases and overflow prevention."""
    
    @pytest.fixture
    def parsing_service(self):
        """Create parsing service for testing."""
        config = Mock(spec=Config)
        config.OPENAI_API_KEY = "test_key_not_used"
        return ParsingService(config)
    
    def test_normal_timezone_offsets(self, parsing_service):
        """Test normal timezone offset calculations work correctly."""
        # Test common locations
        test_cases = [
            ("Cascais", 1),  # Portugal (CET/CEST)
            ("Portugal", 1),
            ("London", 0),   # UK (GMT/BST) 
            ("New York", -5), # EST (approximately)
            ("California", -8), # PST (approximately)
            ("Tokyo", 9),    # JST
        ]
        
        for location, expected_range in test_cases:
            offset = parsing_service.get_timezone_offset(location)
            # Allow for DST variations (±1 hour)
            assert abs(offset - expected_range) <= 1, f"Offset for {location} should be around {expected_range}, got {offset}"
    
    def test_invalid_locations_return_utc(self, parsing_service):
        """Test that invalid locations default to UTC (0 offset)."""
        invalid_locations = [
            None,
            "",
            "   ",
            "XYZ_INVALID_PLACE_999",
            "🚀InvalidLocation🚀",
        ]
        
        for location in invalid_locations:
            offset = parsing_service.get_timezone_offset(location)
            assert offset == 0, f"Invalid location '{location}' should return UTC offset (0), got {offset}"
    
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
    
    def test_parse_content_with_timezone_overflow_bug(self, parsing_service):
        """Test that the original bug scenario doesn't crash the parser."""
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