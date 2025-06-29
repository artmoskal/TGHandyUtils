"""Basic integration test to verify environment setup."""

import pytest
import os
from services.parsing_service import ParsingService
from config import Config


class TestBasicIntegration:
    """Simple integration tests to verify API connectivity."""
    
    @pytest.fixture
    def config(self):
        """Get config with real API key."""
        config = Config()
        print(f"\n[DEBUG] OPENAI_API_KEY present: {bool(config.OPENAI_API_KEY)}")
        print(f"[DEBUG] Key starts with: {config.OPENAI_API_KEY[:10] if config.OPENAI_API_KEY else 'None'}...")
        return config
    
    @pytest.fixture
    def parsing_service(self, config):
        """Create parsing service instance."""
        if not config.OPENAI_API_KEY or config.OPENAI_API_KEY == "test_key_not_used":
            pytest.skip("OpenAI API key not configured")
        return ParsingService(config)
    
    def test_environment_setup(self, config):
        """Test that environment is properly configured."""
        # Skip this test if running in test environment without real key
        if config.OPENAI_API_KEY == "test_key_not_used":
            pytest.skip("Running with test API key, skipping real API tests")
        
        assert config.OPENAI_API_KEY, "OpenAI API key must be set"
        assert config.OPENAI_API_KEY.startswith("sk-"), "API key should start with 'sk-'"
    
    def test_simple_parsing(self, parsing_service):
        """Test basic parsing functionality."""
        # Very simple test case
        result = parsing_service.parse_content_to_task(
            content_message="remind me to test in 5 minutes",
            owner_name="Test User",
            location="Portugal"
        )
        
        assert result is not None, "Parsing should return a result"
        assert "title" in result, "Result should have a title"
        assert "due_time" in result, "Result should have a due_time"
        assert "description" in result, "Result should have a description"
        
        print(f"\n[SUCCESS] Basic parsing worked!")
        print(f"Title: {result['title']}")
        print(f"Due time: {result['due_time']}")
    
    def test_timezone_offset_calculation(self, parsing_service):
        """Test timezone offset calculation without API calls."""
        locations_and_offsets = [
            ("Portugal", [0, 1]),  # UTC+0 or UTC+1 depending on DST
            ("London", [0, 1]),    # UTC+0 or UTC+1 depending on DST
            ("New York", [-5, -4]), # UTC-5 or UTC-4 depending on DST
            ("Tokyo", [9]),        # UTC+9 (no DST)
        ]
        
        for location, expected_offsets in locations_and_offsets:
            offset = parsing_service.get_timezone_offset(location)
            assert offset in expected_offsets, f"{location} offset {offset} not in expected {expected_offsets}"
            print(f"[OK] {location}: UTC{offset:+d}")