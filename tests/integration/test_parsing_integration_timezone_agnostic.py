"""Integration tests for timezone-agnostic LLM parsing with real API calls."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, Mock

from services.parsing_service import ParsingService
from models.unified_recipient import UnifiedUserPreferences
from config import Config
from dateutil import parser as date_parser


class TestTimezoneAgnosticIntegration:
    """Integration tests with real LLM calls for timezone-agnostic parsing."""
    
    @pytest.fixture
    def mock_preferences_repo(self):
        """Mock user preferences repository."""
        return Mock()
    
    @pytest.fixture
    def parsing_service(self, mock_preferences_repo):
        """Create parsing service with real OpenAI API."""
        config = Config()
        if not config.OPENAI_API_KEY:
            pytest.skip("OpenAI API key not configured")
        
        service = ParsingService(config)
        service.preferences_repo = mock_preferences_repo
        return service
    
    @pytest.mark.integration
    def test_portugal_2pm_becomes_13_utc(self, parsing_service, mock_preferences_repo):
        """User in Portugal (+1) says '2pm' → LLM outputs 14:00 → converts to 13:00 UTC."""
        # Setup: Portugal user with cached UTC offset
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1
        )
        
        # Mock current time to a known value
        with patch('services.parsing_service.datetime') as mock_datetime:
            # Current UTC: 2025-07-14 10:00:00 (11:00 Portugal time)
            mock_datetime.now.return_value = datetime(2025, 7, 14, 10, 0, 0, tzinfo=timezone.utc)
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            # Real LLM call
            result = parsing_service.parse_content_to_task(
                "meeting today 2pm",
                owner_name="Test User",
                location="Portugal"
            )
        
        assert result is not None
        assert "meeting" in result["title"].lower()
        
        # 2pm Portugal time = 13:00 UTC
        assert result["due_time"] == "2025-07-14T13:00:00Z"
    
    @pytest.mark.integration
    def test_new_york_2pm_becomes_19_utc(self, parsing_service, mock_preferences_repo):
        """User in New York (-5) says '2pm' → LLM outputs 14:00 → converts to 19:00 UTC."""
        # Setup: New York user with cached UTC offset
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="New York",
            utc_offset=-5
        )
        
        # Mock current time
        with patch('services.parsing_service.datetime') as mock_datetime:
            # Current UTC: 2025-07-14 15:00:00 (10:00 New York time)
            mock_datetime.now.return_value = datetime(2025, 7, 14, 15, 0, 0, tzinfo=timezone.utc)
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            # Real LLM call
            result = parsing_service.parse_content_to_task(
                "meeting today 2pm",
                owner_name="Test User",
                location="New York"
            )
        
        assert result is not None
        assert "meeting" in result["title"].lower()
        
        # 2pm New York time = 19:00 UTC (during standard time)
        assert result["due_time"] == "2025-07-14T19:00:00Z"
    
    @pytest.mark.integration
    def test_incomplete_time_fixed(self, parsing_service, mock_preferences_repo):
        """Test that '2:0pm' now works correctly with timezone-agnostic approach."""
        # Setup: Portugal user
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1
        )
        
        # Mock current time
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 7, 14, 10, 0, 0, tzinfo=timezone.utc)
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            # Real LLM call with problematic format
            result = parsing_service.parse_content_to_task(
                "Appointment today 2:0pm",
                owner_name="Test User",
                location="Portugal"
            )
        
        assert result is not None
        assert "appointment" in result["title"].lower()
        
        # Should correctly parse as 2:00pm = 14:00 local = 13:00 UTC
        assert result["due_time"] == "2025-07-14T13:00:00Z"
    
    @pytest.mark.integration
    def test_multilanguage_without_timezone(self, parsing_service, mock_preferences_repo):
        """Test multilanguage support without timezone confusion."""
        # Setup: Portugal user
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1
        )
        
        test_cases = [
            ("reunião hoje às 14h", "Portuguese"),
            ("cita hoy a las 14h", "Spanish"),
            ("зустріч сьогодні о 14:00", "Ukrainian"),
            ("meeting today at 2pm", "English"),
            ("réunion aujourd'hui à 14h", "French"),
            ("Besprechung heute um 14 Uhr", "German"),
            ("riunione oggi alle 14", "Italian"),
        ]
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 7, 14, 10, 0, 0, tzinfo=timezone.utc)
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            for message, language in test_cases:
                result = parsing_service.parse_content_to_task(
                    message,
                    owner_name="Test User",
                    location="Portugal"
                )
                
                assert result is not None, f"Failed to parse {language}: {message}"
                
                # All should parse to 14:00 local = 13:00 UTC
                due_time = date_parser.isoparse(result["due_time"])
                assert due_time.hour == 13, f"{language} parsing failed: expected 13:00 UTC, got {result['due_time']}"
    
    @pytest.mark.integration
    def test_relative_times_across_timezones(self, parsing_service, mock_preferences_repo):
        """Test relative time expressions work correctly across timezones."""
        test_cases = [
            ("Portugal", 1),
            ("Tokyo", 9),
            ("New York", -5),
            ("UK", 0),
        ]
        
        for location, offset in test_cases:
            mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
                user_id=123,
                location=location,
                utc_offset=offset
            )
            
            with patch('services.parsing_service.datetime') as mock_datetime:
                # Current UTC: 2025-07-14 12:00:00
                current_utc = datetime(2025, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
                mock_datetime.now.return_value = current_utc
                mock_datetime.timezone = timezone
                mock_datetime.fromisoformat = datetime.fromisoformat
                
                # Test "in 2 hours"
                result = parsing_service.parse_content_to_task(
                    "remind me in 2 hours",
                    owner_name="Test User",
                    location=location
                )
            
            assert result is not None, f"Failed for {location}"
            
            # Should be 2 hours from current UTC time
            expected_utc = current_utc + timedelta(hours=2)
            assert result["due_time"] == expected_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), \
                f"Failed for {location}: expected {expected_utc}, got {result['due_time']}"
    
    @pytest.mark.integration
    def test_vague_times_interpretation(self, parsing_service, mock_preferences_repo):
        """Test vague time references are interpreted correctly."""
        # Setup: Portugal user
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1
        )
        
        vague_times = [
            "tonight",
            "this evening",
            "end of day",
            "tomorrow morning",
            "after lunch",
        ]
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            # Current time: 14:00 UTC (15:00 Portugal)
            mock_datetime.now.return_value = datetime(2025, 7, 14, 14, 0, 0, tzinfo=timezone.utc)
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            for time_phrase in vague_times:
                result = parsing_service.parse_content_to_task(
                    f"task {time_phrase}",
                    owner_name="Test User",
                    location="Portugal"
                )
                
                assert result is not None, f"Failed to parse: {time_phrase}"
                
                # Should have a reasonable future time
                due_time = date_parser.isoparse(result["due_time"])
                current_utc = datetime(2025, 7, 14, 14, 0, 0, tzinfo=timezone.utc)
                assert due_time > current_utc, f"{time_phrase} should be in the future"
                
                # Should be within 48 hours
                time_diff = (due_time - current_utc).total_seconds()
                assert time_diff < 172800, f"{time_phrase} should be within 48 hours"
    
    @pytest.mark.integration
    def test_edge_case_formats(self, parsing_service, mock_preferences_repo):
        """Test various edge case time formats."""
        # Setup: Portugal user
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1
        )
        
        edge_cases = [
            ("today 1900", 19, 0),      # Military time
            ("today 7.30pm", 19, 30),   # Dot separator
            ("today at seven", 19, 0),  # Written number (assume evening)
            ("dinner 8ish", 20, 0),     # Approximate time
            ("meet at 19h", 19, 0),     # European format
        ]
        
        with patch('services.parsing_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 7, 14, 10, 0, 0, tzinfo=timezone.utc)
            mock_datetime.timezone = timezone
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            for message, expected_hour, expected_minute in edge_cases:
                result = parsing_service.parse_content_to_task(
                    message,
                    owner_name="Test User",
                    location="Portugal"
                )
                
                assert result is not None, f"Failed to parse: {message}"
                
                # Parse the time
                due_time = date_parser.isoparse(result["due_time"])
                local_time = due_time + timedelta(hours=1)  # Convert to Portugal time
                
                assert local_time.hour == expected_hour, \
                    f"'{message}' expected {expected_hour}:00 local, got {local_time.hour}:{local_time.minute:02d}"
                assert local_time.minute == expected_minute, \
                    f"'{message}' expected minute {expected_minute}, got {local_time.minute}"
    
    @pytest.mark.integration
    def test_no_timezone_in_prompt(self, parsing_service, mock_preferences_repo):
        """Verify that the actual prompt sent to LLM has no timezone info."""
        # Setup
        mock_preferences_repo.get_preferences.return_value = UnifiedUserPreferences(
            user_id=123,
            location="Portugal",
            utc_offset=1
        )
        
        # Capture the actual prompt
        original_invoke = parsing_service.llm.invoke
        captured_prompt = None
        
        def capture_invoke(messages):
            nonlocal captured_prompt
            captured_prompt = messages[0].content
            return original_invoke(messages)
        
        with patch.object(parsing_service.llm, 'invoke', side_effect=capture_invoke):
            with patch('services.parsing_service.datetime') as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 7, 14, 13, 0, 0, tzinfo=timezone.utc)
                mock_datetime.timezone = timezone
                mock_datetime.fromisoformat = datetime.fromisoformat
                
                parsing_service.parse_content_to_task(
                    "meeting at 3pm",
                    owner_name="Test User",
                    location="Portugal"
                )
        
        # Verify prompt content
        assert captured_prompt is not None
        
        # Should NOT contain timezone info
        forbidden_terms = ["UTC", "timezone", "offset", "+1", "UTC+1", "13:00:00Z"]
        for term in forbidden_terms:
            assert term not in captured_prompt, f"Prompt should not contain '{term}'"
        
        # Should contain local time (14:00 Portugal time)
        assert "14:00" in captured_prompt or "2025-07-14 14:00:00" in captured_prompt