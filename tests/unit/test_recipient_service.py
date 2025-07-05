"""Unit tests for recipient service using Factory Boy with real database integration.

This module tests the RecipientService with real database operations and Factory Boy
objects, replacing the previous mock-based testing that hid implementation bugs.
"""

import pytest

# Import database and service components
from database.connection import DatabaseManager
from database.unified_recipient_repository import UnifiedRecipientRepository
from services.recipient_service import RecipientService

# Import Factory Boy factories
from tests.factories import (
    UnifiedRecipientFactory,
    SharedRecipientFactory,
    PersonalRecipientFactory,
    TodoistRecipientFactory,
    TrelloRecipientFactory,
    TodoistSharedRecipientFactory,
    TrelloSharedRecipientFactory,
    DisabledRecipientFactory,
    MultiPlatformRecipientFactory
)

# Import models
from models.unified_recipient import UnifiedRecipient


class TestRecipientService:
    """Test cases for RecipientService with real database integration."""
    
    def setup_method(self):
        """Setup real database components for each test."""
        self.db_manager = DatabaseManager("data/db/tasks.db")
        self.recipient_repo = UnifiedRecipientRepository(self.db_manager)
        self.recipient_service = RecipientService(self.recipient_repo)
        
        # Test user ID for isolation
        self.test_user_id = 888888888  # Unique ID to avoid conflicts
    
    def teardown_method(self):
        """Clean up test data after each test."""
        with self.db_manager.get_connection() as conn:
            # Clean up test recipients
            conn.execute("DELETE FROM recipients WHERE user_id = ?", (self.test_user_id,))
    
    def test_get_all_recipients_returns_all_types(self):
        """Test getting all recipients returns both personal and shared."""
        # Create mixed recipients using factories
        personal_todoist = TodoistRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            name="My Personal Todoist"
        )
        shared_trello = TrelloSharedRecipientFactory(
            user_id=self.test_user_id,
            is_personal=False,
            name="Shared Team Trello"
        )
        
        # Persist to database
        self.recipient_repo.add_recipient(self.test_user_id, personal_todoist)
        self.recipient_repo.add_recipient(self.test_user_id, shared_trello)
        
        # Test real service logic
        recipients = self.recipient_service.get_all_recipients(self.test_user_id)
        
        # Verify both recipients returned
        assert len(recipients) == 2
        names = {r.name for r in recipients}
        assert "My Personal Todoist" in names
        assert "Shared Team Trello" in names
        
        # Verify both personal and shared types present
        is_personal_values = {r.is_personal for r in recipients}
        assert is_personal_values == {True, False}
    
    def test_get_enabled_recipients_filters_correctly(self):
        """Test getting enabled recipients filters out disabled ones."""
        # Create enabled and disabled recipients
        enabled_recipient = TodoistRecipientFactory(
            user_id=self.test_user_id,
            enabled=True,
            name="Enabled Recipient"
        )
        disabled_recipient = DisabledRecipientFactory(
            user_id=self.test_user_id,
            enabled=False,
            name="Disabled Recipient"
        )
        
        # Persist to database
        self.recipient_repo.add_recipient(self.test_user_id, enabled_recipient)
        self.recipient_repo.add_recipient(self.test_user_id, disabled_recipient)
        
        # Test filtering logic
        enabled_recipients = self.recipient_service.get_enabled_recipients(self.test_user_id)
        all_recipients = self.recipient_service.get_all_recipients(self.test_user_id)
        
        # Verify filtering works correctly
        assert len(all_recipients) == 2
        assert len(enabled_recipients) == 1
        assert enabled_recipients[0].name == "Enabled Recipient"
        assert enabled_recipients[0].enabled is True
    
    def test_get_personal_recipients_filters_by_is_personal_true(self):
        """Test getting personal recipients filters by is_personal=True."""
        # Create personal and shared recipients
        personal_recipient = PersonalRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            name="Personal Account"
        )
        shared_recipient = SharedRecipientFactory(
            user_id=self.test_user_id,
            is_personal=False,
            name="Shared Account"
        )
        
        # Persist to database
        self.recipient_repo.add_recipient(self.test_user_id, personal_recipient)
        self.recipient_repo.add_recipient(self.test_user_id, shared_recipient)
        
        # Test personal filtering
        personal_recipients = self.recipient_service.get_personal_recipients(self.test_user_id)
        
        # Verify only personal recipients returned
        assert len(personal_recipients) == 1
        assert personal_recipients[0].is_personal is True
        assert personal_recipients[0].name == "Personal Account"
    
    def test_get_shared_recipients_filters_by_is_personal_false(self):
        """Test getting shared recipients filters by is_personal=False.
        
        CRITICAL: This tests the shared account filtering that was at the
        center of the shared account creation bug.
        """
        # Create personal and shared recipients
        personal_recipient = PersonalRecipientFactory(
            user_id=self.test_user_id,
            is_personal=True,
            name="Personal Account"
        )
        shared_recipient = SharedRecipientFactory(
            user_id=self.test_user_id,
            is_personal=False,
            name="Shared Account"
        )
        
        # Persist to database
        self.recipient_repo.add_recipient(self.test_user_id, personal_recipient)
        self.recipient_repo.add_recipient(self.test_user_id, shared_recipient)
        
        # Test shared filtering
        shared_recipients = self.recipient_service.get_shared_recipients(self.test_user_id)
        
        # Verify only shared recipients returned
        assert len(shared_recipients) == 1
        assert shared_recipients[0].is_personal is False
        assert shared_recipients[0].name == "Shared Account"
    
    def test_get_default_recipients_only_returns_personal_enabled(self):
        """Test default recipients only returns personal and enabled recipients.
        
        CRITICAL: This tests the core logic determining which recipients
        get automatic task creation vs manual confirmation.
        """
        # Create comprehensive test scenario
        scenarios = MultiPlatformRecipientFactory.create_mixed_scenarios(self.test_user_id)
        
        # Persist all recipients
        for category, recipients in scenarios.items():
            for recipient in recipients:
                self.recipient_repo.add_recipient(self.test_user_id, recipient)
        
        # Test default recipient logic
        default_recipients = self.recipient_service.get_default_recipients(self.test_user_id)
        all_recipients = self.recipient_service.get_all_recipients(self.test_user_id)
        
        # Verify only personal AND enabled recipients in defaults
        assert len(all_recipients) == 5  # 2 personal + 2 shared + 1 disabled
        assert len(default_recipients) == 2  # Only personal enabled
        
        for recipient in default_recipients:
            assert recipient.is_personal is True
            assert recipient.enabled is True
    
    def test_get_default_recipients_empty_when_no_personal(self):
        """Test default recipients returns empty when only shared recipients exist."""
        # Create only shared recipients
        shared_todoist = TodoistSharedRecipientFactory(
            user_id=self.test_user_id,
            is_personal=False
        )
        shared_trello = TrelloSharedRecipientFactory(
            user_id=self.test_user_id,
            is_personal=False
        )
        
        # Persist to database
        self.recipient_repo.add_recipient(self.test_user_id, shared_todoist)
        self.recipient_repo.add_recipient(self.test_user_id, shared_trello)
        
        # Test default recipients with shared-only user
        default_recipients = self.recipient_service.get_default_recipients(self.test_user_id)
        all_recipients = self.recipient_service.get_all_recipients(self.test_user_id)
        
        # Should have no defaults for shared-only user
        assert len(all_recipients) == 2
        assert len(default_recipients) == 0
    
    def test_get_recipient_by_id_returns_correct_recipient(self):
        """Test getting specific recipient by ID."""
        # Create test recipient
        test_recipient = TodoistRecipientFactory(
            user_id=self.test_user_id,
            name="Test Specific Recipient"
        )
        
        # Persist and get ID
        recipient_id = self.recipient_repo.add_recipient(self.test_user_id, test_recipient)
        
        # Test retrieval by ID
        retrieved_recipient = self.recipient_service.get_recipient_by_id(self.test_user_id, recipient_id)
        
        # Verify correct recipient returned
        assert retrieved_recipient is not None
        assert retrieved_recipient.id == recipient_id
        assert retrieved_recipient.name == "Test Specific Recipient"
        assert retrieved_recipient.user_id == self.test_user_id
    
    def test_get_recipient_by_id_returns_none_for_nonexistent(self):
        """Test getting non-existent recipient returns None."""
        # Test with non-existent ID
        retrieved_recipient = self.recipient_service.get_recipient_by_id(self.test_user_id, 99999)
        
        # Should return None for non-existent recipient
        assert retrieved_recipient is None
    
    def test_add_personal_recipient_creates_with_is_personal_true(self):
        """Test adding personal recipient creates with is_personal=True."""
        # Add personal recipient through service
        recipient_id = self.recipient_service.add_personal_recipient(
            user_id=self.test_user_id,
            name="Service Created Personal",
            platform_type="todoist",
            credentials="test_personal_token"
        )
        
        # Verify creation via database query
        created_recipient = self.recipient_repo.get_recipient_by_id(self.test_user_id, recipient_id)
        
        # Verify personal recipient properties
        assert created_recipient is not None
        assert created_recipient.is_personal is True
        assert created_recipient.enabled is True
        assert created_recipient.name == "Service Created Personal"
        assert created_recipient.platform_type == "todoist"
        
        # Verify it appears in defaults
        default_recipients = self.recipient_service.get_default_recipients(self.test_user_id)
        assert len(default_recipients) == 1
        assert default_recipients[0].id == recipient_id
    
    def test_add_shared_recipient_creates_with_is_personal_false(self):
        """Test adding shared recipient creates with is_personal=False.
        
        CRITICAL: This tests the shared account creation bug fix.
        The service must properly create shared recipients with is_personal=False.
        """
        # Add shared recipient through service
        recipient_id = self.recipient_service.add_shared_recipient(
            user_id=self.test_user_id,
            name="Service Created Shared",
            platform_type="trello",
            credentials="test_shared_token",
            shared_by_info="friend@example.com"
        )
        
        # Verify creation via database query
        created_recipient = self.recipient_repo.get_recipient_by_id(self.test_user_id, recipient_id)
        
        # CRITICAL: Verify shared recipient properties
        assert created_recipient is not None
        assert created_recipient.is_personal is False  # THIS WAS THE BUG
        assert created_recipient.enabled is True
        assert created_recipient.name == "Service Created Shared"
        assert created_recipient.platform_type == "trello"
        
        # Verify shared_by info in platform_config
        assert created_recipient.platform_config is not None
        assert created_recipient.platform_config.get('shared_by') == "friend@example.com"
        
        # Verify it does NOT appear in defaults
        default_recipients = self.recipient_service.get_default_recipients(self.test_user_id)
        assert len(default_recipients) == 0
        
        # But appears in shared recipients
        shared_recipients = self.recipient_service.get_shared_recipients(self.test_user_id)
        assert len(shared_recipients) == 1
        assert shared_recipients[0].id == recipient_id
    
    def test_add_shared_recipient_with_platform_config_merges_shared_by(self):
        """Test shared recipient creation merges shared_by into existing platform_config."""
        # Add shared recipient with existing platform config
        recipient_id = self.recipient_service.add_shared_recipient(
            user_id=self.test_user_id,
            name="Shared With Config",
            platform_type="trello",
            credentials="test_token",
            platform_config={"board_id": "existing_board"},
            shared_by_info="collaborator@example.com"
        )
        
        # Verify platform config merge
        created_recipient = self.recipient_repo.get_recipient_by_id(self.test_user_id, recipient_id)
        
        assert created_recipient.platform_config['board_id'] == "existing_board"
        assert created_recipient.platform_config['shared_by'] == "collaborator@example.com"
    
    def test_platform_type_diversity_with_factory_boy(self):
        """Test that Factory Boy creates diverse platform types correctly."""
        # Create recipients for all supported platforms
        platform_recipients = MultiPlatformRecipientFactory.create_all_platforms(
            self.test_user_id, 
            is_personal=True
        )
        
        # Persist all recipients
        recipient_ids = []
        for recipient in platform_recipients:
            recipient_id = self.recipient_repo.add_recipient(self.test_user_id, recipient)
            recipient_ids.append(recipient_id)
        
        # Verify platform diversity
        all_recipients = self.recipient_service.get_all_recipients(self.test_user_id)
        platforms = {r.platform_type for r in all_recipients}
        
        assert platforms == {'todoist', 'trello'}
        
        # Verify platform-specific configurations
        for recipient in all_recipients:
            if recipient.platform_type == 'todoist':
                # Todoist should have realistic token
                assert len(recipient.credentials) == 40
            elif recipient.platform_type == 'trello':
                # Trello should have UUID-like credentials
                assert '-' in recipient.credentials  # UUID format
    
    def test_realistic_factory_data_integration(self):
        """Test that Factory Boy creates realistic data that integrates properly."""
        # Create batch of varied recipients
        recipients = [
            TodoistRecipientFactory(user_id=self.test_user_id),
            TrelloRecipientFactory(user_id=self.test_user_id),
            SharedRecipientFactory(user_id=self.test_user_id),
            DisabledRecipientFactory(user_id=self.test_user_id)
        ]
        
        # Persist and verify integration
        for recipient in recipients:
            recipient_id = self.recipient_repo.add_recipient(self.test_user_id, recipient)
            
            # Test service integration with factory-created data
            retrieved = self.recipient_service.get_recipient_by_id(self.test_user_id, recipient_id)
            
            # Verify factory data is realistic and valid
            assert len(retrieved.name) > 0
            assert len(retrieved.credentials) > 10
            assert retrieved.platform_type in ['todoist', 'trello']
            assert isinstance(retrieved.is_personal, bool)
            assert isinstance(retrieved.enabled, bool)
            
            # Test service filtering works with factory data
            if retrieved.is_personal and retrieved.enabled:
                defaults = self.recipient_service.get_default_recipients(self.test_user_id)
                assert any(r.id == recipient_id for r in defaults)
            
            if not retrieved.is_personal:
                shared = self.recipient_service.get_shared_recipients(self.test_user_id)
                assert any(r.id == recipient_id for r in shared)