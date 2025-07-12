"""Database migration and initialization system."""

import os
import sqlite3
from pathlib import Path
from typing import List, Tuple
from core.logging import get_logger

logger = get_logger(__name__)


class DatabaseMigrator:
    """Handle database initialization and migrations."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db_dir = Path(db_path).parent
        
    def ensure_database_ready(self) -> bool:
        """Ensure database exists and is up to date."""
        try:
            # Ensure data directories exist
            self._ensure_directories()
            
            # Check if database exists
            if not os.path.exists(self.db_path):
                logger.info("Database not found, creating new database...")
                return self._initialize_new_database()
            else:
                logger.info("Database found, checking schema...")
                return self._check_and_migrate()
                
        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            return False
    
    def _ensure_directories(self):
        """Create necessary directories."""
        directories = [
            self.db_dir,
            self.db_dir.parent / "logs",
            self.db_dir.parent / "temp_cache"
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {directory}")
    
    def _initialize_new_database(self) -> bool:
        """Create a new database with all tables."""
        logger.info("Initializing new database...")
        
        try:
            # Create database file
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            
            # Create all tables
            self._create_tasks_table(conn)
            self._create_unified_recipients_table(conn)
            self._create_migration_history_table(conn)
            
            # Record initial migration
            self._record_migration(conn, "001_initial_schema", "Initial database schema")
            
            conn.commit()
            conn.close()
            
            logger.info(f"New database created successfully: {self.db_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create new database: {e}")
            return False
    
    def _check_and_migrate(self) -> bool:
        """Check database schema and apply migrations if needed."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            
            # Check if migration history table exists
            if not self._table_exists(conn, "migration_history"):
                logger.info("Migration history table missing, creating...")
                self._create_migration_history_table(conn)
                # If no migration history, assume it's an older database
                self._record_migration(conn, "000_legacy", "Legacy database detected")
            
            # Get current schema version
            current_migrations = self._get_applied_migrations(conn)
            logger.info(f"Applied migrations: {len(current_migrations)}")
            
            # Apply pending migrations
            pending = self._get_pending_migrations(current_migrations)
            if pending:
                logger.info(f"Applying {len(pending)} pending migrations...")
                for migration_id, description, sql_func in pending:
                    try:
                        sql_func(conn)
                        self._record_migration(conn, migration_id, description)
                        logger.info(f"Applied migration: {migration_id} - {description}")
                    except Exception as e:
                        logger.error(f"Migration {migration_id} failed: {e}")
                        conn.rollback()
                        conn.close()
                        return False
            else:
                logger.info("Database schema is up to date")
            
            # Verify critical tables exist
            if not self._verify_schema(conn):
                logger.error("Database schema verification failed")
                conn.close()
                return False
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            logger.error(f"Database migration check failed: {e}")
            return False
    
    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        """Check if a table exists."""
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cursor.fetchone() is not None
    
    def _get_applied_migrations(self, conn: sqlite3.Connection) -> List[str]:
        """Get list of applied migration IDs."""
        try:
            cursor = conn.execute("SELECT migration_id FROM migration_history ORDER BY applied_at")
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            # Migration history table doesn't exist
            return []
    
    def _get_pending_migrations(self, applied: List[str]) -> List[Tuple[str, str, callable]]:
        """Get list of pending migrations."""
        all_migrations = [
            ("001_initial_schema", "Initial database schema", self._migration_001_initial),
            ("002_add_indexes", "Add performance indexes", self._migration_002_indexes),
            ("003_add_screenshot_field", "Add screenshot_file_id to tasks table", self._migration_003_screenshot_field),
            ("004_google_calendar_oauth", "Add Google Calendar OAuth and sharing tables", self._migration_004_google_oauth),
            ("005_fix_oauth_foreign_keys", "Fix foreign key constraints in OAuth tables", self._migration_005_fix_oauth_fks),
            ("006_fix_default_recipients", "Fix default recipient logic and data", self._migration_006_fix_defaults),
            ("007_add_task_recipients", "Add multi-platform task tracking table", self._migration_007_task_recipients),
            # Add future migrations here
        ]
        
        return [m for m in all_migrations if m[0] not in applied]
    
    def _verify_schema(self, conn: sqlite3.Connection) -> bool:
        """Verify that all required tables exist with correct structure."""
        required_tables = ["tasks", "recipients", "migration_history", "task_recipients"]
        
        for table in required_tables:
            if not self._table_exists(conn, table):
                logger.error(f"Required table missing: {table}")
                return False
        
        # Verify critical columns exist
        try:
            # Test basic queries to ensure schema is correct
            conn.execute("SELECT id, title, description, due_time FROM tasks LIMIT 1")
            conn.execute("SELECT id, name, platform_type, credentials FROM recipients LIMIT 1")
            conn.execute("SELECT id, task_id, recipient_id, platform_task_id FROM task_recipients LIMIT 1")
            return True
        except sqlite3.OperationalError as e:
            logger.error(f"Schema verification failed: {e}")
            return False
    
    def _record_migration(self, conn: sqlite3.Connection, migration_id: str, description: str):
        """Record a migration in the history table."""
        conn.execute(
            "INSERT INTO migration_history (migration_id, description, applied_at) VALUES (?, ?, datetime('now'))",
            (migration_id, description)
        )
    
    # Table Creation Methods
    def _create_migration_history_table(self, conn: sqlite3.Connection):
        """Create migration history tracking table."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS migration_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_id TEXT UNIQUE NOT NULL,
                description TEXT NOT NULL,
                applied_at DATETIME NOT NULL
            )
        """)
    
    def _create_tasks_table(self, conn: sqlite3.Connection):
        """Create tasks table."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                due_time TEXT,
                platform_task_id TEXT,
                platform_type TEXT,
                recipient_id INTEGER,
                chat_id INTEGER,
                message_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        """)
    
    def _create_unified_recipients_table(self, conn: sqlite3.Connection):
        """Create unified recipients table."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                platform_type TEXT NOT NULL,
                credentials TEXT NOT NULL,
                platform_config TEXT,
                is_personal BOOLEAN DEFAULT 1,
                is_default BOOLEAN DEFAULT 0,
                enabled BOOLEAN DEFAULT 1,
                shared_by TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                owner_name TEXT,
                location TEXT,
                show_recipient_ui BOOLEAN DEFAULT 0,
                telegram_notifications BOOLEAN DEFAULT 1
            )
        """)
    
    # Migration Methods
    def _migration_001_initial(self, conn: sqlite3.Connection):
        """Initial schema migration - create core tables."""
        self._create_tasks_table(conn)
        self._create_unified_recipients_table(conn)
    
    def _migration_002_indexes(self, conn: sqlite3.Connection):
        """Add performance indexes."""
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due_time ON tasks(due_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recipients_user_id ON recipients(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recipients_enabled ON recipients(enabled)")
    
    def _migration_003_screenshot_field(self, conn: sqlite3.Connection):
        """Add screenshot_file_id column to tasks table."""
        conn.execute("ALTER TABLE tasks ADD COLUMN screenshot_file_id TEXT")

    def _migration_004_google_oauth(self, conn: sqlite3.Connection):
        """Add Google Calendar OAuth and sharing tables."""
        # OAuth states table (no foreign key constraint - user_id is just an integer)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                user_id INTEGER NOT NULL,
                state TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                oauth_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, state)
            )
        """)
        
        # Auth requests table (no foreign key constraints - user_ids are just integers)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_user_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                platform_type TEXT NOT NULL,
                recipient_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                expires_at TIMESTAMP NOT NULL,
                completed_recipient_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Shared authorizations table (only foreign key to recipients table)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shared_authorizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                grantee_user_id INTEGER NOT NULL,
                owner_recipient_id INTEGER NOT NULL,
                permission_level TEXT NOT NULL DEFAULT 'use',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_recipient_id) REFERENCES recipients(id) ON DELETE CASCADE
            )
        """)

    def _migration_005_fix_oauth_fks(self, conn: sqlite3.Connection):
        """Fix foreign key constraints in OAuth tables by recreating them."""
        # Drop existing tables with foreign key constraints
        conn.execute("DROP TABLE IF EXISTS oauth_states")
        conn.execute("DROP TABLE IF EXISTS auth_requests") 
        conn.execute("DROP TABLE IF EXISTS shared_authorizations")
        
        # Recreate tables without invalid foreign key constraints
        # OAuth states table (no foreign key constraint - user_id is just an integer)
        conn.execute("""
            CREATE TABLE oauth_states (
                user_id INTEGER NOT NULL,
                state TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                oauth_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, state)
            )
        """)
        
        # Auth requests table (no foreign key constraints - user_ids are just integers)
        conn.execute("""
            CREATE TABLE auth_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_user_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                platform_type TEXT NOT NULL,
                recipient_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                expires_at TIMESTAMP NOT NULL,
                completed_recipient_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Shared authorizations table (only foreign key to recipients table)
        conn.execute("""
            CREATE TABLE shared_authorizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                grantee_user_id INTEGER NOT NULL,
                owner_recipient_id INTEGER NOT NULL,
                permission_level TEXT NOT NULL DEFAULT 'use',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_recipient_id) REFERENCES recipients(id) ON DELETE CASCADE
            )
        """)

    def _migration_006_fix_defaults(self, conn: sqlite3.Connection):
        """Fix default recipient logic and migrate existing data."""
        # First, set all existing personal recipients to is_default = 0
        conn.execute("UPDATE recipients SET is_default = 0 WHERE is_personal = 1")
        
        # For each user, set their first personal recipient as default
        conn.execute("""
            UPDATE recipients 
            SET is_default = 1 
            WHERE id IN (
                SELECT MIN(id) 
                FROM recipients 
                WHERE is_personal = 1 AND enabled = 1 
                GROUP BY user_id
            )
        """)
        
        # Ensure all shared recipients are not default
        conn.execute("UPDATE recipients SET is_default = 0 WHERE is_personal = 0")

    def _migration_007_task_recipients(self, conn: sqlite3.Connection):
        """Add multi-platform task tracking table."""
        # Create task_recipients table for many-to-many tracking
        conn.execute("""
            CREATE TABLE task_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                platform_task_id TEXT NOT NULL,
                platform_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active',
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (recipient_id) REFERENCES recipients(id) ON DELETE CASCADE,
                UNIQUE(task_id, recipient_id)
            )
        """)
        
        # Add indexes for performance
        conn.execute("CREATE INDEX idx_task_recipients_task_id ON task_recipients(task_id)")
        conn.execute("CREATE INDEX idx_task_recipients_recipient_id ON task_recipients(recipient_id)")
        conn.execute("CREATE INDEX idx_task_recipients_status ON task_recipients(status)")
        
        # Migrate existing data from tasks table to task_recipients
        # Only migrate tasks that have both recipient_id and platform_task_id
        conn.execute("""
            INSERT INTO task_recipients (task_id, recipient_id, platform_task_id, platform_type, created_at)
            SELECT 
                id,
                recipient_id,
                platform_task_id,
                COALESCE(platform_type, 'todoist'),
                COALESCE(created_at, datetime('now'))
            FROM tasks 
            WHERE recipient_id IS NOT NULL AND platform_task_id IS NOT NULL
        """)
        
        logger.info("Created task_recipients table and migrated existing data")


def ensure_database_ready(db_path: str) -> bool:
    """Convenience function to ensure database is ready."""
    migrator = DatabaseMigrator(db_path)
    return migrator.ensure_database_ready()