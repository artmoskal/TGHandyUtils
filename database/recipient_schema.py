"""Clean recipient-based database schema.

This is a completely new system, separate from legacy tables.
No migration logic, no backward compatibility.
"""

import sqlite3
from database.connection import DatabaseManager
from core.logging import get_logger

logger = get_logger(__name__)


def create_recipient_tables(conn: sqlite3.Connection) -> None:
    """Create clean recipient-based tables."""
    
    # User platforms (platforms owned by the user)
    # Use telegram_user_id directly - no need for separate users table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_platforms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL,
            platform_type TEXT NOT NULL,
            credentials TEXT NOT NULL,
            platform_config TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(telegram_user_id, platform_type)
        )
    ''')
    
    # Shared recipients (platforms shared by others)
    # Use telegram_user_id directly
    conn.execute('''
        CREATE TABLE IF NOT EXISTS shared_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            platform_type TEXT NOT NULL,
            credentials TEXT NOT NULL,
            platform_config TEXT,
            shared_by TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # User preferences (clean, no JSON blobs)
    # Use telegram_user_id directly
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences_v2 (
            telegram_user_id INTEGER PRIMARY KEY,
            default_recipients TEXT,
            show_recipient_ui BOOLEAN DEFAULT FALSE,
            telegram_notifications BOOLEAN DEFAULT TRUE,
            owner_name TEXT,
            location TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Add new columns if they don't exist (migration)
    try:
        conn.execute('ALTER TABLE user_preferences_v2 ADD COLUMN owner_name TEXT')
        logger.info("Added owner_name column to user_preferences_v2")
    except sqlite3.OperationalError:
        pass  # Column already exists
        
    try:
        conn.execute('ALTER TABLE user_preferences_v2 ADD COLUMN location TEXT')
        logger.info("Added location column to user_preferences_v2")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Task recipients (track which recipients got each task)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS task_recipients (
            task_id INTEGER NOT NULL,
            recipient_type TEXT NOT NULL,
            recipient_id INTEGER NOT NULL,
            platform_task_id TEXT,
            status TEXT DEFAULT 'created',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (task_id, recipient_type, recipient_id),
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        )
    ''')
    
    # Indices for performance
    conn.execute('CREATE INDEX IF NOT EXISTS idx_user_platforms_telegram_user_id ON user_platforms(telegram_user_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_user_platforms_enabled ON user_platforms(telegram_user_id, enabled)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_shared_recipients_telegram_user_id ON shared_recipients(telegram_user_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_shared_recipients_enabled ON shared_recipients(telegram_user_id, enabled)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_task_recipients_task_id ON task_recipients(task_id)')
    
    logger.info("Created clean recipient-based database schema")


def initialize_recipient_schema(db_manager: DatabaseManager) -> None:
    """Initialize the clean recipient schema."""
    with db_manager.get_connection() as conn:
        # Enable foreign keys for clean schema
        conn.execute('PRAGMA foreign_keys=ON')
        create_recipient_tables(conn)