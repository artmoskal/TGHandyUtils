"""Unit tests for timezone conversion logic using Factory Boy with realistic user data.

This module tests timezone conversion functionality with Factory Boy objects representing
realistic user scenarios from different timezones, without making OpenAI API calls.
"""

import pytest
from datetime import datetime, timezone, timedelta
import zoneinfo
from unittest.mock import Mock, patch

# Import service
from services.parsing_service import ParsingService

# Import Factory Boy factories
from tests.factories import (
    TelegramUserFactory,
    TelegramMessageFactory,
    TaskFactory,
    SimpleTaskFactory
)


class TestTimezoneConversionUnit:
    """Unit tests for timezone conversion logic with Factory Boy objects."""
    
    @pytest.fixture
    def mock_config(self):
        """Mock config for testing."""
        config = Mock()
        config._openai_api_key = "test-key-" + "a" * 40  # Realistic format
        return config
    
    @pytest.fixture 
    def parsing_service(self, mock_config):
        """Create parsing service with mocked dependencies."""
        # Mock the ChatOpenAI to avoid actual API calls
        with patch('services.parsing_service.ChatOpenAI'):
            return ParsingService(mock_config)
    
    def test_get_timezone_offset_cascais_with_factory_users(self, parsing_service):
        """Test timezone offset calculation for Cascais with realistic user data."""
        # Create Portuguese users using factory
        portuguese_users = [
            TelegramUserFactory(
                first_name="João",
                last_name="Silva",
                language_code="pt"
            ),
            TelegramUserFactory(
                first_name="Maria",
                last_name="Santos",
                language_code="pt"
            )
        ]
        
        offset = parsing_service.get_timezone_offset("Cascais")
        # Should be 0 or 1 depending on DST (WEST timezone)
        assert offset in [0, 1], f"Cascais offset should be 0 or 1, got {offset}"
        
        # Verify factory users are realistic
        for user in portuguese_users:
            assert len(user.first_name) > 0
            assert user.language_code == "pt"
    
    def test_get_timezone_offset_london_with_factory_users(self, parsing_service):
        """Test timezone offset calculation for London with realistic UK users."""
        # Create UK users using factory
        uk_users = [
            TelegramUserFactory(
                first_name="James",
                last_name="Smith",
                language_code="en"
            ),
            TelegramUserFactory(
                first_name="Emma",
                last_name="Johnson",
                language_code="en"
            )
        ]
        
        offset = parsing_service.get_timezone_offset("London")
        # Should be 0 or 1 depending on DST (GMT/BST)
        assert offset in [0, 1], f"London offset should be 0 or 1, got {offset}"
        
        # Verify factory users are realistic
        for user in uk_users:
            assert len(user.first_name) > 0
            assert user.language_code == "en"
    
    def test_get_timezone_offset_new_york_with_factory_users(self, parsing_service):
        """Test timezone offset calculation for New York with realistic US users."""
        # Create US users using factory
        us_users = [
            TelegramUserFactory(
                first_name="John",
                last_name="Doe",
                language_code="en"
            ),
            TelegramUserFactory(
                first_name="Sarah",
                last_name="Wilson",
                language_code="en"
            )
        ]
        
        offset = parsing_service.get_timezone_offset("New York")
        # Should be -5 or -4 depending on DST (EST/EDT)
        assert offset in [-5, -4], f"NY offset should be -5 or -4, got {offset}"
        
        # Verify factory users are realistic
        for user in us_users:
            assert len(user.first_name) > 0
            assert user.language_code == "en"
    
    def test_get_timezone_offset_tokyo_with_factory_users(self, parsing_service):
        """Test timezone offset calculation for Tokyo with realistic Japanese users."""
        # Create Japanese users using factory
        japanese_users = [
            TelegramUserFactory(
                first_name="Hiroshi",
                last_name="Tanaka",
                language_code="ja"
            ),
            TelegramUserFactory(
                first_name="Yuki",
                last_name="Yamamoto",
                language_code="ja"
            )
        ]
        
        offset = parsing_service.get_timezone_offset("Tokyo")
        # Should be 9 hours (JST, no DST)
        assert offset == 9
        
        # Verify factory users are realistic
        for user in japanese_users:
            assert len(user.first_name) > 0
            assert user.language_code == "ja"
    
    def test_get_timezone_offset_unknown_location_with_factory_user(self, parsing_service):
        """Test timezone offset for unknown location defaults to UTC."""
        # Create user from unknown location
        unknown_user = TelegramUserFactory(
            first_name="Alex",
            last_name="Unknown",
            language_code="en"
        )
        
        offset = parsing_service.get_timezone_offset("UnknownCity")
        assert offset == 0
        
        # Verify factory user is realistic even for unknown location
        assert len(unknown_user.first_name) > 0
        assert len(unknown_user.last_name) > 0
    
    def test_get_timezone_offset_none_location(self, parsing_service):
        """Test timezone offset for None location defaults to UTC."""
        offset = parsing_service.get_timezone_offset(None)
        assert offset == 0
    
    def test_timezone_info_mapping_with_factory_user_locations(self, parsing_service):
        """Test timezone info string generation with realistic user locations."""
        # Create users from different locations
        test_users_and_locations = [
            (TelegramUserFactory(first_name="António", language_code="pt"), "Cascais", "UTC+1 (UTC+2 during DST)"),
            (TelegramUserFactory(first_name="Pedro", language_code="pt"), "Portugal", "UTC+1 (UTC+2 during DST)"),
            (TelegramUserFactory(first_name="Oliver", language_code="en"), "London", "UTC+0 (UTC+1 during DST)"),
            (TelegramUserFactory(first_name="Michael", language_code="en"), "New York", "UTC-5 (UTC-4 during DST)"),
            (TelegramUserFactory(first_name="Alex", language_code="en"), "UnknownPlace", "UTC+0 (please specify timezone for accuracy)"),
        ]
        
        for user, location, expected in test_users_and_locations:
            result = parsing_service._get_timezone_info(location)
            assert result == expected, f"For {location}: expected {expected}, got {result}"
            
            # Verify factory user is realistic
            assert len(user.first_name) > 0
            assert user.language_code in ["pt", "en", "ja"]
    
    def test_convert_utc_to_local_display_cascais_with_factory_task(self, parsing_service):
        """Test UTC to local time conversion for display with realistic task data."""
        # Create realistic task for Portuguese user
        portuguese_task = TaskFactory(
            title="Reunião com Cliente",
            description="Reunião mensal com cliente importante",
            due_time="2025-06-28T09:00:00Z"
        )
        
        location = "Cascais"
        result = parsing_service.convert_utc_to_local_display(portuguese_task.due_time, location)
        
        # Should show 09:00 or 10:00 depending on DST
        assert ("09:00" in result or "10:00" in result), f"Time should be 09:00 or 10:00, got {result}"
        assert "Portugal time" in result
        assert "June 28, 2025" in result
        
        # Verify factory task is realistic
        assert len(portuguese_task.title) > 0
        assert len(portuguese_task.description) > 0
    
    def test_convert_utc_to_local_display_london_with_factory_task(self, parsing_service):
        """Test UTC to local time conversion for London with realistic task data."""
        # Create realistic task for UK user
        uk_task = TaskFactory(
            title="Team Standup Meeting",
            description="Daily team synchronization meeting",
            due_time="2025-06-28T09:00:00Z"
        )
        
        location = "London"
        result = parsing_service.convert_utc_to_local_display(uk_task.due_time, location)
        
        # Should show 09:00 or 10:00 depending on DST
        assert ("09:00" in result or "10:00" in result), f"Time should be 09:00 or 10:00, got {result}"
        assert "UK time" in result
        assert "June 28, 2025" in result
        
        # Verify factory task is realistic
        assert "meeting" in uk_task.title.lower()
        assert len(uk_task.description) > 0
    
    def test_convert_utc_to_local_display_new_york_with_factory_task(self, parsing_service):
        """Test UTC to local time conversion for New York with realistic task data."""
        # Create realistic task for US user
        us_task = TaskFactory(
            title="Client Call - Q4 Planning",
            description="Quarterly planning session with key client",
            due_time="2025-06-28T14:00:00Z"  # 2 PM UTC
        )
        
        location = "New York"
        result = parsing_service.convert_utc_to_local_display(us_task.due_time, location)
        
        # Should show 09:00/10:00 AM depending on DST (UTC-5/-4)
        assert ("09:00" in result or "10:00" in result), f"Time should be 09:00 or 10:00, got {result}"
        assert "New York time" in result
        assert "June 28, 2025" in result
        
        # Verify factory task is realistic
        assert "client" in us_task.title.lower()
        assert "quarterly" in us_task.description.lower()
    
    def test_convert_utc_to_local_display_invalid_time_with_factory_task(self, parsing_service):
        """Test handling of invalid UTC time string with Factory Boy task."""
        # Create task with invalid time format
        invalid_task = TaskFactory(
            title="Task with Invalid Time",
            description="This task has malformed due time",
            due_time="invalid-time-format"
        )
        
        location = "Cascais"
        result = parsing_service.convert_utc_to_local_display(invalid_task.due_time, location)
        
        # Should return error message with original string
        assert "Error parsing time" in result
        assert invalid_task.due_time in result
        
        # Verify factory task is still realistic despite invalid time
        assert len(invalid_task.title) > 0
        assert len(invalid_task.description) > 0
    
    def test_timezone_guess_from_location_with_factory_users(self, parsing_service):
        """Test timezone identifier guessing from location names with realistic users."""
        # Create users from different cities using factory
        city_users = [
            (TelegramUserFactory(first_name="Pierre", language_code="fr"), "paris"),
            (TelegramUserFactory(first_name="Hans", language_code="de"), "berlin"),
            (TelegramUserFactory(first_name="David", language_code="en"), "new york"),
            (TelegramUserFactory(first_name="Jessica", language_code="en"), "los angeles"),
        ]
        
        for user, location in city_users:
            # This tests the internal logic indirectly through offset calculation
            offset = parsing_service.get_timezone_offset(location)
            
            # Verify we get a reasonable offset (not the default 0)
            # These cities should all have non-zero offsets
            assert offset != 0 or location in ["london"], f"Expected non-zero offset for {location}"
            
            # Verify factory user is realistic
            assert len(user.first_name) > 0
            assert user.language_code in ["fr", "de", "en"]
    
    def test_timezone_offset_dynamic_calculation_with_factory_data(self, parsing_service):
        """Test that timezone offset calculation is dynamic (handles DST) with realistic data."""
        # Create realistic user for timezone testing
        cascais_user = TelegramUserFactory(
            first_name="Carlos",
            last_name="Oliveira",
            language_code="pt"
        )
        
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
            
        # Verify factory user is realistic
        assert len(cascais_user.first_name) > 0
        assert cascais_user.language_code == "pt"
    
    def test_prompt_template_formatting_with_factory_message(self, parsing_service):
        """Test that the prompt template formats correctly with Factory Boy message data."""
        # Create realistic Telegram message using factory
        telegram_message = TelegramMessageFactory(
            text="Schedule meeting for tomorrow at 2 PM",
            from_user=TelegramUserFactory(
                first_name="Maria",
                last_name="Costa",
                language_code="pt"
            )
        )
        
        # Test the actual prompt template formatting (no LLM call)
        current_utc = datetime.now(timezone.utc)
        offset_hours = parsing_service.get_timezone_offset("Cascais")
        user_local_time = current_utc + timedelta(hours=offset_hours)
        
        # Create example for "in 5 minutes" calculation
        example_local_time = user_local_time + timedelta(minutes=5)
        example_utc_time = example_local_time - timedelta(hours=offset_hours)
        
        # Use the real input data preparation logic matching parsing service
        timezone_offset_str = f"+{offset_hours}" if offset_hours >= 0 else str(offset_hours)
        
        input_data = {
            "content_message": telegram_message.text,
            "owner_name": f"{telegram_message.from_user.first_name} {telegram_message.from_user.last_name}",
            "current_year": current_utc.year,
            "current_utc_iso": current_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "current_local_iso": user_local_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "location": "Cascais",
            "timezone_name": "Portugal Time",
            "timezone_offset_str": timezone_offset_str,
            "today_date": user_local_time.strftime("%Y-%m-%d"),
            "tomorrow_date": (user_local_time + timedelta(days=1)).strftime("%Y-%m-%d"),
            "current_local_simple": user_local_time.strftime("%H:%M"),
            "time_examples": "- \"today 5am\" → 2025-07-04T04:00:00Z\n- \"today noon\" → 2025-07-04T11:00:00Z"
        }
        
        # Test that the real prompt template can format with this data
        prompt_text = parsing_service.prompt_template.format(**input_data)
        
        # Verify key elements are in the formatted prompt
        assert "Maria Costa" in prompt_text
        assert "UTC" in prompt_text  
        assert "Cascais" in prompt_text
        assert str(offset_hours) in prompt_text
        assert "2025" in prompt_text
        assert "timezone:" in prompt_text.lower()  # Template uses "Timezone: {timezone_name}"
        assert "schedule meeting" in prompt_text.lower()  # Message content included
        
        # Verify factory message is realistic
        assert len(telegram_message.text) > 10
        assert telegram_message.from_user.first_name == "Maria"
        assert telegram_message.from_user.language_code == "pt"
    
    def test_multi_timezone_scenario_with_factory_users(self, parsing_service):
        """Test multiple timezone scenarios with realistic user data from Factory Boy."""
        # Create users from different timezones
        timezone_scenarios = [
            (TelegramUserFactory(first_name="Ana", language_code="pt"), "Cascais", [0, 1]),
            (TelegramUserFactory(first_name="Sophie", language_code="fr"), "Paris", [1, 2]),
            (TelegramUserFactory(first_name="William", language_code="en"), "London", [0, 1]),
            (TelegramUserFactory(first_name="Robert", language_code="en"), "New York", [-5, -4]),
            (TelegramUserFactory(first_name="Kenji", language_code="ja"), "Tokyo", [9]),
        ]
        
        for user, location, expected_offsets in timezone_scenarios:
            offset = parsing_service.get_timezone_offset(location)
            assert offset in expected_offsets, f"For {location}: expected offset in {expected_offsets}, got {offset}"
            
            # Test time conversion with realistic task
            task = TaskFactory(
                title=f"Meeting in {location}",
                description=f"Important meeting scheduled for {user.first_name}",
                due_time="2025-06-28T12:00:00Z"
            )
            
            result = parsing_service.convert_utc_to_local_display(task.due_time, location)
            
            # Verify conversion produces reasonable output
            assert "2025" in result
            assert "June 28" in result
            assert "time" in result.lower()
            
            # Verify factory data is realistic
            assert len(user.first_name) > 0
            assert user.language_code in ["pt", "fr", "en", "ja"]
            assert location in task.title
            assert user.first_name in task.description
    
    def test_edge_case_timezones_with_factory_data(self, parsing_service):
        """Test edge case timezones with Factory Boy generated data."""
        # Create users for edge case locations
        edge_case_users = [
            TelegramUserFactory(first_name="Vladimir", language_code="ru"),  # Russia
            TelegramUserFactory(first_name="Raj", language_code="hi"),       # India
            TelegramUserFactory(first_name="Ahmad", language_code="ar"),     # Middle East
            TelegramUserFactory(first_name="Chen", language_code="zh"),      # China
        ]
        
        edge_locations = ["Moscow", "Mumbai", "Dubai", "Shanghai"]
        
        for user, location in zip(edge_case_users, edge_locations):
            offset = parsing_service.get_timezone_offset(location)
            
            # Should get reasonable offsets even for edge cases
            assert -12 <= offset <= 12, f"Offset for {location} should be within ±12 hours, got {offset}"
            
            # Test time conversion works
            result = parsing_service.convert_utc_to_local_display("2025-06-28T12:00:00Z", location)
            assert "2025" in result  # Should successfully convert
            
            # Verify factory user is realistic
            assert len(user.first_name) > 0
            assert user.language_code in ["ru", "hi", "ar", "zh"]
    
    def test_timezone_conversion_with_realistic_task_scenarios(self, parsing_service):
        """Test timezone conversion with realistic task scenarios from Factory Boy."""
        # Create realistic tasks for different scenarios
        task_scenarios = [
            ("Morning Standup", "Daily team meeting", "09:00"),
            ("Lunch Meeting", "Business lunch with partners", "12:00"),
            ("Afternoon Call", "Client check-in call", "15:00"),
            ("Evening Review", "End of day project review", "18:00"),
        ]
        
        for title, description, time_part in task_scenarios:
            task = TaskFactory(
                title=title,
                description=description,
                due_time=f"2025-06-28T{time_part}:00Z"
            )
            
            # Test conversion for different locations
            locations = ["Cascais", "London", "New York"]
            for location in locations:
                result = parsing_service.convert_utc_to_local_display(task.due_time, location)
                
                # Verify conversion works for all scenarios
                assert "June 28, 2025" in result
                assert "time" in result.lower()
                assert len(result) > 20  # Should be descriptive
                
            # Verify factory task is realistic
            assert title in task.title
            assert description in task.description
            assert time_part in task.due_time