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
                # Enable foreign keys for clean recipient system
                self._local.connection.execute('PRAGMA foreign_keys=ON')
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
                self._initialize_recipient_schema(conn)
                self._initialized = True
                logger.info("Database schema initialized")
    
    def _initialize_recipient_schema(self, conn) -> None:
        """Initialize clean recipient schema."""
        from database.recipient_schema import create_recipient_tables
        create_recipient_tables(conn)
    
    def _create_tables(self, conn: sqlite3.Connection) -> None:
        """Create clean database tables for recipient system only."""
        
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
        
        # Create indices for better performance
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_due_time ON tasks(due_time)')
    
    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Clean recipient system - no legacy migrations needed."""
        pass

# Remove global instance - use DI container instead