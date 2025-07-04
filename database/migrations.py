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
            # Add future migrations here
        ]
        
        return [m for m in all_migrations if m[0] not in applied]
    
    def _verify_schema(self, conn: sqlite3.Connection) -> bool:
        """Verify that all required tables exist with correct structure."""
        required_tables = ["tasks", "unified_recipients", "migration_history"]
        
        for table in required_tables:
            if not self._table_exists(conn, table):
                logger.error(f"Required table missing: {table}")
                return False
        
        # Verify critical columns exist
        try:
            # Test basic queries to ensure schema is correct
            conn.execute("SELECT id, title, description, due_time FROM tasks LIMIT 1")
            conn.execute("SELECT id, name, platform_type, credentials FROM unified_recipients LIMIT 1")
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
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        """)
    
    def _create_unified_recipients_table(self, conn: sqlite3.Connection):
        """Create unified recipients table."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unified_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                platform_type TEXT NOT NULL,
                credentials TEXT NOT NULL,
                platform_config TEXT,
                is_personal BOOLEAN DEFAULT 1,
                enabled BOOLEAN DEFAULT 1,
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
        """Initial schema migration."""
        # Tables are already created in _initialize_new_database
        # This is a no-op for existing databases
        pass
    
    def _migration_002_indexes(self, conn: sqlite3.Connection):
        """Add performance indexes."""
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due_time ON tasks(due_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recipients_user_id ON unified_recipients(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recipients_enabled ON unified_recipients(enabled)")


def ensure_database_ready(db_path: str) -> bool:
    """Convenience function to ensure database is ready."""
    migrator = DatabaseMigrator(db_path)
    return migrator.ensure_database_ready()