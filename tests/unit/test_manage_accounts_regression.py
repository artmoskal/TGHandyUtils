"""
Regression test for 'manage accounts click fails' bug using Factory Boy with real data.

This test must fail FIRST to demonstrate the bug, then pass after the bug is fixed.
Enhanced with Factory Boy to test with realistic recipient data scenarios.
"""

import pytest
from unittest.mock import Mock, MagicMock

# Import services
from services.recipient_service import RecipientService
from database.unified_recipient_repository import UnifiedRecipientRepository

# Import Factory Boy factories
from tests.factories import (
    TodoistRecipientFactory,
    TrelloRecipientFactory,
    PersonalRecipientFactory,
    SharedRecipientFactory,
    MultiPlatformRecipientFactory,
    TelegramUserFactory
)


class TestManageAccountsRegression:
    """Test for the specific 'manage accounts click fails' bug with Factory Boy data."""
    
    def setup_method(self):
        """Setup realistic test user for account management."""
        self.test_user = TelegramUserFactory(
            id=447812312,  # Original failing user ID
            first_name="TestUser",
            last_name="AccountManagement"
        )
    
    def test_manage_accounts_callback_requires_get_recipients_by_user_method(self):
        """
        Test that RecipientService has get_recipients_by_user method with realistic data.
        
        This test reproduces the exact error: 
        'RecipientService' object has no attribute 'get_recipients_by_user'
        
        The test should fail initially, then pass after the method is added.
        """
        # Setup with realistic recipients from Factory Boy
        realistic_recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user.id,
                name="Personal Todoist Account",
                is_personal=True,
                enabled=True
            ),
            TrelloRecipientFactory(
                user_id=self.test_user.id,
                name="Team Trello Board", 
                is_personal=False,
                enabled=True
            )
        ]
        
        mock_repository = Mock(spec=UnifiedRecipientRepository)
        mock_repository.get_all_recipients.return_value = realistic_recipients
        
        service = RecipientService(mock_repository)
        
        # This line should work - if it fails, the bug exists
        # The error was: AttributeError: 'RecipientService' object has no attribute 'get_recipients_by_user'
        recipients = service.get_recipients_by_user(self.test_user.id)
        
        # Verify it returns expected type
        assert isinstance(recipients, list)
        
        # Verify it returns realistic recipient data
        assert len(recipients) == 2
        assert any(r.name == "Personal Todoist Account" for r in recipients)
        assert any(r.name == "Team Trello Board" for r in recipients)
        
        # Verify it calls the repository correctly
        mock_repository.get_all_recipients.assert_called_once_with(self.test_user.id)
    
    def test_manage_accounts_callback_integration_simulation_with_factory_data(self):
        """
        Integration test simulating the exact callback flow that was failing with realistic data.
        
        This simulates what happens when user clicks "Manage accounts" button.
        """
        # Setup mock repository with realistic Factory Boy test data
        comprehensive_recipients = MultiPlatformRecipientFactory.create_mixed_scenarios(self.test_user.id)
        
        # Flatten the scenarios into a single list
        all_recipients = []
        for category, recipient_list in comprehensive_recipients.items():
            all_recipients.extend(recipient_list)
        
        mock_repository = Mock(spec=UnifiedRecipientRepository)
        mock_repository.get_all_recipients.return_value = all_recipients
        
        service = RecipientService(mock_repository)
        
        # This is what the callback handler calls internally
        # Error was: show_recipients_callback:97 - Error showing recipients for user 447812312: 
        # 'RecipientService' object has no attribute 'get_recipients_by_user'
        try:
            recipients = service.get_recipients_by_user(self.test_user.id)
            success = True
            error = None
        except AttributeError as e:
            success = False
            error = str(e)
        
        # This test should pass only after the bug is fixed
        assert success, f"Manage accounts callback failed with error: {error}"
        assert len(recipients) >= 0  # Should return empty list or actual recipients
        
        # Verify realistic data is returned
        if len(recipients) > 0:
            # Should have mixed personal and shared recipients
            personal_recipients = [r for r in recipients if r.is_personal]
            shared_recipients = [r for r in recipients if not r.is_personal]
            
            assert len(personal_recipients) > 0, "Should have personal recipients"
            assert len(shared_recipients) > 0, "Should have shared recipients"
            
            # Should have different platform types
            platform_types = {r.platform_type for r in recipients}
            assert len(platform_types) > 1, "Should have multiple platform types"
    
    def test_manage_accounts_empty_user_scenario(self):
        """Test manage accounts behavior with user who has no recipients."""
        # Create user with no recipients
        empty_user = TelegramUserFactory(
            id=999999999,
            first_name="EmptyUser",
            last_name="NoRecipients"
        )
        
        mock_repository = Mock(spec=UnifiedRecipientRepository)
        mock_repository.get_all_recipients.return_value = []
        
        service = RecipientService(mock_repository)
        
        # Should handle empty case gracefully
        recipients = service.get_recipients_by_user(empty_user.id)
        
        assert isinstance(recipients, list)
        assert len(recipients) == 0
        mock_repository.get_all_recipients.assert_called_once_with(empty_user.id)
    
    def test_manage_accounts_with_disabled_recipients(self):
        """Test manage accounts with mix of enabled and disabled recipients."""
        # Create mix of enabled and disabled recipients
        mixed_recipients = [
            TodoistRecipientFactory(
                user_id=self.test_user.id,
                name="Enabled Todoist",
                enabled=True
            ),
            TrelloRecipientFactory(
                user_id=self.test_user.id,
                name="Disabled Trello",
                enabled=False
            ),
            PersonalRecipientFactory(
                user_id=self.test_user.id,
                name="Enabled Personal",
                enabled=True
            ),
            SharedRecipientFactory(
                user_id=self.test_user.id,
                name="Disabled Shared",
                enabled=False
            )
        ]
        
        mock_repository = Mock(spec=UnifiedRecipientRepository)
        mock_repository.get_all_recipients.return_value = mixed_recipients
        
        service = RecipientService(mock_repository)
        
        # Test that method works with mixed enabled/disabled recipients
        recipients = service.get_recipients_by_user(self.test_user.id)
        
        assert isinstance(recipients, list)
        assert len(recipients) == 4  # Should return all recipients (enabled and disabled)
        
        # Verify both enabled and disabled recipients are returned
        enabled_recipients = [r for r in recipients if r.enabled]
        disabled_recipients = [r for r in recipients if not r.enabled]
        
        assert len(enabled_recipients) == 2
        assert len(disabled_recipients) == 2
    
    def test_manage_accounts_regression_with_original_failing_user_id(self):
        """Test specifically with the original failing user ID from the bug report."""
        original_failing_user_id = 447812312  # From the actual error logs
        
        # Create realistic recipients for this specific user ID
        original_user_recipients = [
            TodoistRecipientFactory(
                user_id=original_failing_user_id,
                name="My Work Todoist",
                platform_type="todoist",
                credentials="a" * 40,  # Realistic token length
                is_personal=True,
                enabled=True
            ),
            TrelloRecipientFactory(
                user_id=original_failing_user_id,
                name="Project Management Board",
                platform_type="trello",
                credentials="12345678-1234-1234-1234-123456789012",  # UUID format
                is_personal=False,
                enabled=True
            )
        ]
        
        mock_repository = Mock(spec=UnifiedRecipientRepository)
        mock_repository.get_all_recipients.return_value = original_user_recipients
        
        service = RecipientService(mock_repository)
        
        # This exact call was failing in production
        recipients = service.get_recipients_by_user(original_failing_user_id)
        
        # Verify the fix works for the original failing scenario
        assert isinstance(recipients, list)
        assert len(recipients) == 2
        
        # Verify realistic recipient data
        todoist_recipients = [r for r in recipients if r.platform_type == "todoist"]
        trello_recipients = [r for r in recipients if r.platform_type == "trello"]
        
        assert len(todoist_recipients) == 1
        assert len(trello_recipients) == 1
        
        # Verify realistic credentials
        assert len(todoist_recipients[0].credentials) == 40
        assert "-" in trello_recipients[0].credentials  # UUID format
    
    def test_manage_accounts_method_compatibility_with_existing_methods(self):
        """Test that get_recipients_by_user is compatible with existing service methods."""
        # Create comprehensive recipient set
        comprehensive_recipients = [
            PersonalRecipientFactory(user_id=self.test_user.id, enabled=True),
            SharedRecipientFactory(user_id=self.test_user.id, enabled=True),
            PersonalRecipientFactory(user_id=self.test_user.id, enabled=False),
            SharedRecipientFactory(user_id=self.test_user.id, enabled=False)
        ]
        
        mock_repository = Mock(spec=UnifiedRecipientRepository)
        mock_repository.get_all_recipients.return_value = comprehensive_recipients
        
        service = RecipientService(mock_repository)
        
        # Test that get_recipients_by_user returns same data as get_all_recipients
        recipients_by_user = service.get_recipients_by_user(self.test_user.id)
        
        # Should be equivalent to calling get_all_recipients directly
        assert len(recipients_by_user) == len(comprehensive_recipients)
        
        # Verify method calls the repository correctly
        mock_repository.get_all_recipients.assert_called_with(self.test_user.id)
    
    def test_manage_accounts_error_handling_with_factory_data(self):
        """Test error handling in manage accounts flow with realistic data."""
        # Create scenario that might cause repository errors
        error_user = TelegramUserFactory(
            id=888888888,
            first_name="ErrorProneUser"
        )
        
        mock_repository = Mock(spec=UnifiedRecipientRepository)
        # Simulate repository error
        mock_repository.get_all_recipients.side_effect = Exception("Database connection failed")
        
        service = RecipientService(mock_repository)
        
        # Test that service method handles repository errors appropriately
        with pytest.raises(Exception, match="Database connection failed"):
            service.get_recipients_by_user(error_user.id)
        
        # Verify repository was called before error
        mock_repository.get_all_recipients.assert_called_once_with(error_user.id)