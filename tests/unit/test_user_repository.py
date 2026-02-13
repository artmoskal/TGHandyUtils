"""Tests for UserRepository."""

import pytest
import tempfile
import os
from datetime import datetime, timedelta
from database.user.user_repository import UserRepository
from database.connection import DatabaseManager
from models.user import User
from tests.factories.user_factory import UserFactory
from core.exceptions import DatabaseError


pytestmark = pytest.mark.unit
class TestUserRepository:
    """Test cases for UserRepository."""
    
    @pytest.fixture
    def test_db_manager(self):
        """Create a test database manager with in-memory database."""
        # Create temporary database file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as temp_file:
            db_path = temp_file.name
        
        # Initialize database with schema
        db_manager = DatabaseManager(db_path)
        
        # Create users table
        with db_manager.get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE,
                    first_name TEXT,
                    last_name TEXT,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        yield db_manager
        
        # Cleanup
        os.unlink(db_path)
    
    def test_upsert_user_new(self, test_db_manager):
        """Test inserting new user."""
        repo = UserRepository(test_db_manager)
        
        # Insert new user
        repo.upsert_user(
            user_id=123456,
            username="testuser",
            first_name="Test",
            last_name="User"
        )
        
        # Verify user was inserted
        user = repo.find_by_id(123456)
        assert user is not None
        assert user.user_id == 123456
        assert user.username == "testuser"
        assert user.first_name == "Test"
        assert user.last_name == "User"
    
    def test_upsert_user_update(self, test_db_manager):
        """Test updating existing user."""
        repo = UserRepository(test_db_manager)
        
        # Insert initial user
        repo.upsert_user(123456, "oldusername", "Old", "Name")
        
        # Update with new information
        repo.upsert_user(123456, "newusername", "New", "Name")
        
        # Verify user was updated
        user = repo.find_by_id(123456)
        assert user.username == "newusername"
        assert user.first_name == "New"
        assert user.last_name == "Name"
    
    def test_upsert_user_partial_update(self, test_db_manager):
        """Test partial update keeps existing data."""
        repo = UserRepository(test_db_manager)
        
        # Insert complete user
        repo.upsert_user(123456, "username", "First", "Last")
        
        # Update with only username (other fields None)
        repo.upsert_user(123456, "newusername", None, None)
        
        # Verify only username changed
        user = repo.find_by_id(123456)
        assert user.username == "newusername"
        assert user.first_name == "First"  # Kept original
        assert user.last_name == "Last"    # Kept original
    
    def test_find_by_username_variations(self, test_db_manager):
        """Test username lookup with @, case variations."""
        repo = UserRepository(test_db_manager)
        
        # Insert user
        repo.upsert_user(123456, "TestUser", "Test", "User")
        
        # Test various username formats
        assert repo.find_by_username("TestUser") is not None
        assert repo.find_by_username("testuser") is not None  # Case insensitive
        assert repo.find_by_username("@TestUser") is not None  # With @
        assert repo.find_by_username("@testuser") is not None  # Both
        assert repo.find_by_username("  @TestUser  ") is not None  # With spaces
    
    def test_find_by_username_not_found(self, test_db_manager):
        """Test username lookup for non-existent user."""
        repo = UserRepository(test_db_manager)
        
        assert repo.find_by_username("nonexistent") is None
        assert repo.find_by_username("") is None
        assert repo.find_by_username("@") is None
    
    def test_find_by_id_exists(self, test_db_manager):
        """Test finding user by ID."""
        repo = UserRepository(test_db_manager)
        
        # Insert user
        repo.upsert_user(123456, "testuser", "Test", "User")
        
        # Find by ID
        user = repo.find_by_id(123456)
        assert user is not None
        assert user.user_id == 123456
        assert user.username == "testuser"
    
    def test_find_by_id_not_found(self, test_db_manager):
        """Test finding non-existent user by ID."""
        repo = UserRepository(test_db_manager)
        
        assert repo.find_by_id(999999) is None
    
    def test_update_last_seen(self, test_db_manager):
        """Test updating last seen timestamp."""
        repo = UserRepository(test_db_manager)
        
        # Insert user
        repo.upsert_user(123456, "testuser", "Test", "User")
        
        # Get initial last_seen
        user1 = repo.find_by_id(123456)
        initial_last_seen = user1.last_seen
        
        # Wait a bit and update last seen
        import time
        time.sleep(1)  # Increase sleep to ensure timestamp difference
        repo.update_last_seen(123456)
        
        # Verify last_seen was updated
        user2 = repo.find_by_id(123456)
        assert user2.last_seen > initial_last_seen
    
    def test_get_all_users(self, test_db_manager):
        """Test getting all users."""
        repo = UserRepository(test_db_manager)
        
        # Insert multiple users with delays to ensure different timestamps
        import time
        repo.upsert_user(111111, "user1", "User", "One")
        time.sleep(1)  # SQLite datetime precision requires full seconds
        repo.upsert_user(222222, "user2", "User", "Two")
        time.sleep(1)
        repo.upsert_user(333333, "user3", "User", "Three")
        
        # Get all users
        users = repo.get_all_users()
        assert len(users) == 3
        
        # Verify they're ordered by last_seen DESC
        assert users[0].user_id == 333333  # Most recent
        assert users[1].user_id == 222222
        assert users[2].user_id == 111111  # Oldest
    
    def test_user_display_name(self):
        """Test User display_name property."""
        # With first name
        user = UserFactory(first_name="John", username="johndoe")
        assert user.display_name == "John"
        
        # Without first name but with username
        user = UserFactory(first_name=None, username="johndoe")
        assert user.display_name == "@johndoe"
        
        # Without both
        user = UserFactory(first_name=None, username=None, user_id=123456)
        assert user.display_name == "User123456"
    
    def test_user_full_name(self):
        """Test User full_name property."""
        # With both names
        user = UserFactory(first_name="John", last_name="Doe")
        assert user.full_name == "John Doe"
        
        # Only first name
        user = UserFactory(first_name="John", last_name=None)
        assert user.full_name == "John"
        
        # Only last name
        user = UserFactory(first_name=None, last_name="Doe")
        assert user.full_name == "Doe"
        
        # Neither name - falls back to display_name
        user = UserFactory(first_name=None, last_name=None, username="johndoe")
        assert user.full_name == "@johndoe"