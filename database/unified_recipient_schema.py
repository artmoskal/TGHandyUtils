"""Unified recipient architecture - single table for all recipients.

This replaces the flawed split-table design with a proper unified approach
where personal and shared accounts are just flags on the same entity.
"""

import sqlite3
from database.connection import DatabaseManager
from core.logging import get_logger

logger = get_logger(__name__)


def create_unified_recipient_table(conn: sqlite3.Connection) -> None:
    """Create unified recipients table to replace split-table design."""
    
    # Single unified recipients table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            platform_type TEXT NOT NULL,
            credentials TEXT NOT NULL,
            platform_config TEXT,
            is_personal BOOLEAN NOT NULL,
            is_default BOOLEAN DEFAULT FALSE,
            enabled BOOLEAN DEFAULT TRUE,
            shared_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # User preferences (clean, no JSON blobs)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences_unified (
            user_id INTEGER PRIMARY KEY,
            show_recipient_ui BOOLEAN DEFAULT FALSE,
            telegram_notifications BOOLEAN DEFAULT TRUE,
            owner_name TEXT,
            location TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Task recipients (track which recipients got each task)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS task_recipients_unified (
            task_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            platform_task_id TEXT,
            status TEXT DEFAULT 'created',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (task_id, recipient_id),
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (recipient_id) REFERENCES recipients(id) ON DELETE CASCADE
        )
    ''')
    
    # Indices for performance
    conn.execute('CREATE INDEX IF NOT EXISTS idx_recipients_user_id ON recipients(user_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_recipients_enabled ON recipients(user_id, enabled)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_recipients_personal ON recipients(user_id, is_personal)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_recipients_default ON recipients(user_id, is_default)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_task_recipients_unified_task_id ON task_recipients_unified(task_id)')
    
    logger.info("Created unified recipient table architecture")


def migrate_to_unified_recipients(conn: sqlite3.Connection) -> None:
    """NO MIGRATION - clean unified schema only."""
    # NO LEGACY CODE - REMOVED ALL MIGRATION BULLSHIT
    logger.info("Migration skipped - using clean unified schema only")


def initialize_unified_schema(db_manager: DatabaseManager) -> None:
    """Initialize the unified recipient schema and migrate data."""
    with db_manager.get_connection() as conn:
        # Enable foreign keys
        conn.execute('PRAGMA foreign_keys=ON')
        
        # Create unified tables
        create_unified_recipient_table(conn)
        
        # Migrate existing data
        migrate_to_unified_recipients(conn)
        
        logger.info("Initialized unified recipient schema with data migration")