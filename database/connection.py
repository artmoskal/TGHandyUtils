"""Database connection management."""

import sqlite3
import threading
import os
from contextlib import contextmanager
from typing import Optional, Generator
from core.exceptions import DatabaseError
from core.logging import get_logger

logger = get_logger(__name__)

class DatabaseManager:
    """Thread-safe database connection manager with connection pooling."""
    
    def __init__(self, database_path: str = "data/db/tasks.db", timeout: int = 30):
        self._local = threading.local()
        self._lock = threading.Lock()
        self._initialized = False
        self.database_path = database_path
        self.timeout = timeout
        
        # Ensure directories exist
        os.makedirs(os.path.dirname(database_path), exist_ok=True)
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a connection for the current thread."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            try:
                self._local.connection = sqlite3.connect(
                    self.database_path,
                    timeout=self.timeout,
                    check_same_thread=False
                )
                self._local.connection.row_factory = sqlite3.Row
                # Enable WAL mode for better concurrency
                self._local.connection.execute('PRAGMA journal_mode=WAL')
                # Disable foreign keys for partner system (using user_id directly)
                self._local.connection.execute('PRAGMA foreign_keys=OFF')
                logger.debug(f"Created new database connection for thread {threading.current_thread().name}")
            except sqlite3.Error as e:
                logger.error(f"Failed to create database connection: {e}")
                raise DatabaseError(f"Failed to connect to database: {e}")
        
        return self._local.connection
    
    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections with automatic transaction handling."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database transaction failed: {e}")
            raise DatabaseError(f"Database operation failed: {e}")
    
    def initialize_schema(self) -> None:
        """Initialize database schema."""
        if self._initialized:
            return
        
        with self._lock:
            if self._initialized:
                return
            
            with self.get_connection() as conn:
                self._create_tables(conn)
                self._migrate_schema(conn)
                self._initialized = True
                logger.info("Database schema initialized")
    
    def _create_tables(self, conn: sqlite3.Connection) -> None:
        """Create database tables if they don't exist."""
        
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
        
        # Create users table (legacy - keep for backward compatibility)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER UNIQUE NOT NULL,
                platform_token TEXT NOT NULL,
                platform_type TEXT NOT NULL DEFAULT 'todoist',
                owner_name TEXT NOT NULL,
                location TEXT,
                platform_settings TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create new partner-based tables
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                partner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                platform TEXT NOT NULL,
                credentials TEXT NOT NULL,
                platform_config TEXT,
                is_self BOOLEAN DEFAULT FALSE,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, partner_id)
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                default_partners TEXT,
                show_sharing_ui BOOLEAN DEFAULT FALSE,
                telegram_notifications BOOLEAN DEFAULT TRUE,
                location TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create indices for better performance
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_due_time ON tasks(due_time)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_user_partners_user_id ON user_partners(user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_user_partners_enabled ON user_partners(user_id, enabled)')
    
    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Handle schema migrations."""
        # Check if migration is needed from old schema
        cursor = conn.execute("PRAGMA table_info(users)")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]
        
        if 'todoist_user' in column_names and 'platform_type' not in column_names:
            logger.info("Migrating users table to support multiple platforms")
            
            # Create temporary table with new schema
            conn.execute('''
                CREATE TABLE users_temp (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER UNIQUE NOT NULL,
                    platform_token TEXT NOT NULL,
                    platform_type TEXT NOT NULL DEFAULT 'todoist',
                    owner_name TEXT NOT NULL,
                    location TEXT,
                    platform_settings TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Copy data from old table
            conn.execute('''
                INSERT INTO users_temp (telegram_user_id, platform_token, platform_type, owner_name, location)
                SELECT telegram_user_id, todoist_user, 'todoist', owner_name, location FROM users
            ''')
            
            # Replace old table
            conn.execute("DROP TABLE users")
            conn.execute("ALTER TABLE users_temp RENAME TO users")
            
            logger.info("Users table migration completed")

# Remove global instance - use DI container instead