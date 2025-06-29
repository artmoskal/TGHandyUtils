#!/usr/bin/env python3
"""
Comprehensive integration tests for the recipient system.
Tests the entire flow from database to services to handlers.
"""

import sys
import os
import tempfile
import asyncio
from pathlib import Path

# Add the project root to Python path
sys.path.insert(0, str(Path(__file__).parent))

from database.connection import DatabaseManager
from database.recipient_schema import create_recipient_tables
from database.recipient_repositories import (
    UserPlatformRepository, SharedRecipientRepository, UserPreferencesV2Repository
)
from services.recipient_service import RecipientService
from services.recipient_task_service import RecipientTaskService
from database.repositories import TaskRepository
from models.recipient import UserPlatformCreate, SharedRecipientCreate, UserPreferencesV2Create
from models.task import TaskCreate
from core.logging import get_logger

logger = get_logger(__name__)


class IntegrationTestSuite:
    """Comprehensive integration test suite."""
    
    def __init__(self):
        self.db_path = None
        self.db_manager = None
        self.repositories = {}
        self.services = {}
    
    def setup(self):
        """Set up test environment with real database."""
        print("🔧 Setting up integration test environment...")
        
        # Create temporary database
        self.db_path = tempfile.mktemp(suffix='.db')
        self.db_manager = DatabaseManager(self.db_path)
        
        # Initialize schema
        with self.db_manager.get_connection() as conn:
            create_recipient_tables(conn)
            
            # Create tasks table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    task_title TEXT NOT NULL,
                    task_description TEXT NOT NULL,
                    due_time TEXT NOT NULL,
                    platform_task_id TEXT,
                    platform_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        # Create repositories
        self.repositories = {
            'platform': UserPlatformRepository(self.db_manager),
            'shared': SharedRecipientRepository(self.db_manager),
            'preferences': UserPreferencesV2Repository(self.db_manager),
            'task': TaskRepository(self.db_manager)
        }
        
        # Create services
        self.services = {
            'recipient': RecipientService(
                self.repositories['platform'],
                self.repositories['shared'],
                self.repositories['preferences']
            ),
            'task': RecipientTaskService(
                self.repositories['task'],
                None  # Will be set after recipient service is created
            )
        }
        
        # Wire task service with recipient service
        self.services['task'].recipient_service = self.services['recipient']
        
        print("✅ Integration test environment ready")
    
    def teardown(self):
        """Clean up test environment."""
        if self.db_path and os.path.exists(self.db_path):
            os.remove(self.db_path)
        print("🧹 Test environment cleaned up")
    
    def test_user_platform_lifecycle(self):
        """Test complete user platform lifecycle."""
        print("\n🧪 Testing user platform lifecycle...")
        
        user_id = 12345
        
        # 1. Add user platform
        platform_create = UserPlatformCreate(
            platform_type="todoist",
            credentials="test_token_123",
            enabled=True
        )
        
        platform_id = self.repositories['platform'].add_platform(user_id, platform_create)
        assert platform_id is not None, "Platform creation failed"
        print(f"   ✅ Created platform with ID: {platform_id}")
        
        # 2. Retrieve user platforms
        platforms = self.repositories['platform'].get_user_platforms(user_id)
        assert len(platforms) == 1, f"Expected 1 platform, got {len(platforms)}"
        assert platforms[0].platform_type == "todoist", f"Wrong platform type: {platforms[0].platform_type}"
        print("   ✅ Platform retrieval works")
        
        # 3. Test via service
        recipients = self.services['recipient'].get_all_recipients(user_id)
        assert len(recipients) == 1, f"Expected 1 recipient, got {len(recipients)}"
        assert recipients[0].name == "My Todoist", f"Wrong name: {recipients[0].name}"
        assert recipients[0].type == "user_platform", f"Wrong type: {recipients[0].type}"
        print("   ✅ Service integration works")
        
        # 4. Test enabling/disabling
        recipient_id = recipients[0].id
        result = self.services['recipient'].toggle_recipient_enabled(user_id, recipient_id)
        assert result is True, "Toggle failed"
        
        updated_recipients = self.services['recipient'].get_all_recipients(user_id)
        assert updated_recipients[0].enabled is False, "Toggle didn't work"
        print("   ✅ Enable/disable works")
        
        return platform_id
    
    def test_shared_recipient_lifecycle(self):
        """Test complete shared recipient lifecycle."""
        print("\n🧪 Testing shared recipient lifecycle...")
        
        user_id = 12345
        
        # 1. Add shared recipient
        shared_create = SharedRecipientCreate(
            name="Team Trello",
            platform_type="trello",
            credentials="key123:token456",
            platform_config={"board_id": "board123", "list_id": "list456"},
            enabled=True
        )
        
        shared_id = self.repositories['shared'].add_recipient(user_id, shared_create)
        assert shared_id is not None, "Shared recipient creation failed"
        print(f"   ✅ Created shared recipient with ID: {shared_id}")
        
        # 2. Retrieve shared recipients
        shared_recipients = self.repositories['shared'].get_shared_recipients(user_id)
        assert len(shared_recipients) == 1, f"Expected 1 shared recipient, got {len(shared_recipients)}"
        assert shared_recipients[0].name == "Team Trello", f"Wrong name: {shared_recipients[0].name}"
        print("   ✅ Shared recipient retrieval works")
        
        # 3. Test via service
        all_recipients = self.services['recipient'].get_all_recipients(user_id)
        shared_from_service = [r for r in all_recipients if r.type == "shared_recipient"]
        assert len(shared_from_service) == 1, f"Expected 1 shared recipient from service, got {len(shared_from_service)}"
        print("   ✅ Service integration works")
        
        return shared_id
    
    def test_user_preferences_lifecycle(self):
        """Test user preferences lifecycle."""
        print("\n🧪 Testing user preferences lifecycle...")
        
        user_id = 12345
        
        # 1. Create preferences
        prefs_create = UserPreferencesV2Create(
            default_recipients=["platform_1", "shared_1"],
            show_recipient_ui=True
        )
        
        result = self.repositories['preferences'].create_preferences(user_id, prefs_create)
        assert result is True, "Preferences creation failed"
        print("   ✅ Preferences created")
        
        # 2. Retrieve preferences
        prefs = self.repositories['preferences'].get_preferences(user_id)
        assert prefs is not None, "Preferences retrieval failed"
        assert prefs.show_recipient_ui is True, "Wrong UI setting"
        assert len(prefs.default_recipients) == 2, f"Wrong default recipients count: {len(prefs.default_recipients)}"
        print("   ✅ Preferences retrieval works")
        
        # 3. Test via service
        ui_enabled = self.services['recipient'].is_recipient_ui_enabled(user_id)
        assert ui_enabled is True, "UI setting not working via service"
        print("   ✅ Service integration works")
        
        # 4. Update preferences
        result = self.services['recipient'].enable_recipient_ui(user_id, False)
        assert result is True, "UI toggle failed"
        
        updated_enabled = self.services['recipient'].is_recipient_ui_enabled(user_id)
        assert updated_enabled is False, "UI toggle didn't persist"
        print("   ✅ Preferences update works")
    
    def test_task_creation_flow(self):
        """Test complete task creation flow."""
        print("\n🧪 Testing task creation flow...")
        
        user_id = 12345
        
        # Ensure we have recipients
        recipients = self.services['recipient'].get_enabled_recipients(user_id)
        if not recipients:
            print("   ⚠️  No enabled recipients for task creation test")
            return
        
        # Create task data
        task_data = TaskCreate(
            title="Integration Test Task",
            description="This is a test task created during integration testing",
            due_time="2024-12-25T10:00:00Z"
        )
        
        # Test task repository directly
        task_id = self.repositories['task'].create(
            user_id=user_id,
            chat_id=67890,
            message_id=111,
            task_data=task_data,
            platform_type="todoist"
        )
        
        assert task_id is not None, "Task creation in database failed"
        print(f"   ✅ Task created in database with ID: {task_id}")
        
        # Retrieve task
        user_tasks = self.repositories['task'].get_by_user(user_id)
        assert len(user_tasks) == 1, f"Expected 1 task, got {len(user_tasks)}"
        assert user_tasks[0].task_title == "Integration Test Task", f"Wrong title: {user_tasks[0].task_title}"
        print("   ✅ Task retrieval works")
        
        # Test task service validation
        try:
            self.services['task']._validate_task_data(task_data)
            print("   ✅ Task validation works")
        except Exception as e:
            raise AssertionError(f"Task validation failed: {e}")
    
    def test_credential_and_config_retrieval(self):
        """Test credential and configuration retrieval."""
        print("\n🧪 Testing credential and config retrieval...")
        
        user_id = 12345
        
        # Get all recipients
        recipients = self.services['recipient'].get_all_recipients(user_id)
        
        for recipient in recipients:
            # Test credential retrieval
            credentials = self.services['recipient'].get_recipient_credentials(user_id, recipient.id)
            assert credentials is not None, f"No credentials for {recipient.id}"
            print(f"   ✅ Credentials retrieved for {recipient.name}")
            
            # Test config retrieval
            config = self.services['recipient'].get_recipient_config(user_id, recipient.id)
            # Config can be None for some platforms, that's ok
            print(f"   ✅ Config retrieved for {recipient.name}: {config is not None}")
    
    def test_recipient_removal(self):
        """Test recipient removal functionality."""
        print("\n🧪 Testing recipient removal...")
        
        user_id = 12345
        
        # Get current recipients
        recipients_before = self.services['recipient'].get_all_recipients(user_id)
        initial_count = len(recipients_before)
        
        if initial_count == 0:
            print("   ⚠️  No recipients to remove")
            return
        
        # Remove first recipient
        recipient_to_remove = recipients_before[0]
        result = self.services['recipient'].remove_recipient(user_id, recipient_to_remove.id)
        assert result is True, f"Failed to remove recipient {recipient_to_remove.id}"
        
        # Check that it's gone
        recipients_after = self.services['recipient'].get_all_recipients(user_id)
        assert len(recipients_after) == initial_count - 1, f"Expected {initial_count - 1} recipients, got {len(recipients_after)}"
        
        removed_ids = [r.id for r in recipients_after]
        assert recipient_to_remove.id not in removed_ids, "Recipient not actually removed"
        
        print(f"   ✅ Removed recipient {recipient_to_remove.name}")
    
    def test_data_consistency(self):
        """Test data consistency across the system."""
        print("\n🧪 Testing data consistency...")
        
        user_id = 12345
        
        # Add some test data
        platform_create = UserPlatformCreate(
            platform_type="todoist",
            credentials="consistency_test_token",
            enabled=True
        )
        platform_id = self.repositories['platform'].add_platform(user_id, platform_create)
        
        # Test that the same data appears consistently across all access methods
        
        # Via repository
        platforms_repo = self.repositories['platform'].get_user_platforms(user_id)
        repo_platform = next((p for p in platforms_repo if p.id == platform_id), None)
        assert repo_platform is not None, "Platform not found via repository"
        
        # Via service
        recipients_service = self.services['recipient'].get_all_recipients(user_id)
        service_recipient = next((r for r in recipients_service if r.id == f"platform_{platform_id}"), None)
        assert service_recipient is not None, "Recipient not found via service"
        
        # Check consistency
        assert repo_platform.platform_type == service_recipient.platform_type, "Platform type inconsistent"
        assert repo_platform.enabled == service_recipient.enabled, "Enabled status inconsistent"
        
        print("   ✅ Data consistency verified")
    
    def run_all_tests(self):
        """Run all integration tests."""
        print("🚀 Starting comprehensive integration tests...")
        
        try:
            self.setup()
            
            # Run tests in order (some depend on previous tests)
            self.test_user_platform_lifecycle()
            self.test_shared_recipient_lifecycle()
            self.test_user_preferences_lifecycle()
            self.test_task_creation_flow()
            self.test_credential_and_config_retrieval()
            self.test_data_consistency()
            self.test_recipient_removal()
            
            print("\n🎉 ALL INTEGRATION TESTS PASSED!")
            return True
            
        except Exception as e:
            print(f"\n❌ Integration test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
            
        finally:
            self.teardown()


def main():
    """Main test runner."""
    test_suite = IntegrationTestSuite()
    success = test_suite.run_all_tests()
    
    if success:
        print("\n✅ Integration test suite completed successfully")
        sys.exit(0)
    else:
        print("\n❌ Integration test suite failed")
        sys.exit(1)


if __name__ == "__main__":
    main()