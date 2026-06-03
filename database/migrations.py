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

            # Apply remaining migrations (002-010+) on the fresh database
            return self._check_and_migrate()
            
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
                logger.warning("Schema verification failed, attempting repair...")
                if self._repair_schema(conn) and self._verify_schema(conn):
                    logger.info("Schema repair successful")
                else:
                    logger.error("Schema repair failed — database is corrupt")
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
            ("008_add_users_table", "Add users table for username tracking", self._migration_008_users_table),
            ("009_add_user_preferences_unified", "Add user preferences unified table", self._migration_009_add_user_preferences_unified),
            ("010_add_utc_offset", "Add UTC offset to user preferences for timezone handling", self._migration_010_add_utc_offset),
            ("011_add_content_mode", "Add content_mode (reminder/anki/auto) to user preferences", self._migration_011_add_content_mode),
            ("012_add_anki_deck_name", "Add anki_deck_name to user preferences", self._migration_012_add_anki_deck_name),
            # Add future migrations here
        ]
        
        return [m for m in all_migrations if m[0] not in applied]
    
    def _verify_schema(self, conn: sqlite3.Connection) -> bool:
        """Verify that all required tables exist with correct columns.

        This is the last line of defense against schema drift. If a table
        exists but is missing columns, the bot will crash at runtime with
        cryptic 'no such column' errors. Fail loudly here instead.
        """
        expected_schema = {
            'tasks': ['id', 'user_id', 'title', 'description', 'due_time',
                      'platform_task_id', 'platform_type', 'recipient_id',
                      'chat_id', 'message_id', 'created_at', 'updated_at', 'status',
                      'screenshot_file_id'],
            'recipients': ['id', 'user_id', 'name', 'platform_type', 'credentials',
                          'platform_config', 'is_personal', 'is_default', 'enabled',
                          'shared_by', 'created_at', 'updated_at',
                          'owner_name', 'location', 'show_recipient_ui', 'telegram_notifications'],
            'task_recipients': ['id', 'task_id', 'recipient_id', 'platform_task_id',
                               'platform_type', 'created_at', 'status'],
            'user_preferences_unified': ['user_id', 'show_recipient_ui', 'telegram_notifications',
                                        'owner_name', 'location', 'utc_offset', 'content_mode',
                                        'anki_deck_name', 'created_at', 'updated_at'],
            'migration_history': ['id', 'migration_id', 'description', 'applied_at'],
            'users': ['user_id', 'username', 'first_name', 'last_name', 'last_seen', 'created_at'],
        }

        all_ok = True
        for table, expected_columns in expected_schema.items():
            if not self._table_exists(conn, table):
                logger.error(f"Required table missing: {table}")
                all_ok = False
                continue

            actual_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            missing = set(expected_columns) - actual_columns
            if missing:
                logger.error(f"Table '{table}' missing columns: {missing}")
                all_ok = False

        if not all_ok:
            logger.error("Schema verification FAILED - database is corrupt or migrations didn't apply correctly")

        return all_ok

    def _repair_schema(self, conn: sqlite3.Connection) -> bool:
        """Attempt to repair known schema issues from legacy init race conditions.

        This handles databases where migrations were recorded as applied but
        a competing init system (unified_recipient_schema.py) created tables
        with missing columns. We add missing columns with their defaults.
        """
        # Map of table -> column -> (type, default) for columns that may be missing
        # due to the old unified_recipient_schema.py race condition
        repairs = {
            'user_preferences_unified': {
                'utc_offset': ('INTEGER', '0'),
                'content_mode': ('TEXT', "'reminder'"),
                'anki_deck_name': ('TEXT', 'NULL'),
            },
        }

        repaired = False
        for table, columns in repairs.items():
            if not self._table_exists(conn, table):
                continue
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for col, (col_type, default) in columns.items():
                if col not in actual:
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type} DEFAULT {default}")
                        logger.info(f"Repaired: added {col} to {table}")
                        repaired = True
                    except sqlite3.Error as e:
                        logger.error(f"Failed to repair {table}.{col}: {e}")
                        return False

        if repaired:
            conn.commit()
        return True

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
    
    def _migration_008_users_table(self, conn: sqlite3.Connection):
        """Add users table for tracking Telegram user information."""
        # Create users table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT UNIQUE,
                first_name TEXT,
                last_name TEXT,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Add index for username lookups
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        
        logger.info("Created users table for username tracking")
    
    def _migration_009_add_user_preferences_unified(self, conn: sqlite3.Connection):
        """Create user preferences unified table."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences_unified (
                user_id INTEGER PRIMARY KEY,
                show_recipient_ui BOOLEAN DEFAULT 0,
                telegram_notifications BOOLEAN DEFAULT 1,
                owner_name TEXT,
                location TEXT,
                utc_offset INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("Created user_preferences_unified table")
    
    def _migration_010_add_utc_offset(self, conn: sqlite3.Connection):
        """Add UTC offset to user preferences for timezone-aware processing."""
        # Defensively add utc_offset column if missing - handles legacy databases where
        # table was created without this column before migration 009 existed
        try:
            conn.execute("ALTER TABLE user_preferences_unified ADD COLUMN utc_offset INTEGER DEFAULT 0")
            logger.info("Added missing utc_offset column to user_preferences_unified")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                logger.debug("utc_offset column already exists, skipping ALTER TABLE")
            else:
                raise

        # Update existing records with calculated offsets
        
        # Update existing records with UTC offset based on location
        # This mapping matches the existing get_timezone_offset logic
        location_offset_map = {
            'portugal': 1, 'cascais': 1, 'lisbon': 1, 'porto': 1,
            'spain': 1, 'madrid': 1, 'barcelona': 1,
            'france': 1, 'paris': 1,
            'germany': 1, 'berlin': 1,
            'uk': 0, 'united kingdom': 0, 'london': 0,
            'new york': -5, 'est': -5, 'eastern': -5,
            'california': -8, 'pst': -8, 'pacific': -8,
            'tokyo': 9, 'japan': 9,
            'sydney': 10, 'australia': 10,
            'moscow': 3, 'russia': 3,
            'beijing': 8, 'china': 8,
            'india': 5, 'mumbai': 5, 'delhi': 5,
            'dubai': 4, 'uae': 4,
        }
        
        # Get all user preferences with locations
        cursor = conn.execute("SELECT user_id, location FROM user_preferences_unified WHERE location IS NOT NULL")
        updates = []
        
        for user_id, location in cursor.fetchall():
            if location:
                location_lower = location.lower().strip()
                # Find matching offset
                offset = 0
                for key, value in location_offset_map.items():
                    if key in location_lower:
                        offset = value
                        break
                updates.append((offset, user_id))
        
        # Apply updates
        for offset, user_id in updates:
            conn.execute(
                "UPDATE user_preferences_unified SET utc_offset = ? WHERE user_id = ?",
                (offset, user_id)
            )
        
        logger.info(f"Added utc_offset column and updated {len(updates)} user preferences")

    def _migration_011_add_content_mode(self, conn: sqlite3.Connection):
        """Add content_mode column controlling reminder vs anki-card processing."""
        try:
            conn.execute(
                "ALTER TABLE user_preferences_unified ADD COLUMN content_mode TEXT DEFAULT 'reminder'"
            )
            logger.info("Added content_mode column to user_preferences_unified")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                logger.debug("content_mode column already exists, skipping ALTER TABLE")
            else:
                raise

    def _migration_012_add_anki_deck_name(self, conn: sqlite3.Connection):
        """Add anki_deck_name column for the user's configurable Anki deck."""
        try:
            conn.execute("ALTER TABLE user_preferences_unified ADD COLUMN anki_deck_name TEXT")
            logger.info("Added anki_deck_name column to user_preferences_unified")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                logger.debug("anki_deck_name column already exists, skipping ALTER TABLE")
            else:
                raise


def ensure_database_ready(db_path: str) -> bool:
    """Convenience function to ensure database is ready."""
    migrator = DatabaseMigrator(db_path)
    return migrator.ensure_database_ready()