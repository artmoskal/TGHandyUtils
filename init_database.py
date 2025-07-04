#!/usr/bin/env python3
"""Initialize database with all required tables."""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from database.connection import DatabaseManager
from database.repositories import TaskRepository
from database.unified_recipient_repository import UnifiedRecipientRepository


def init_database():
    """Initialize all database tables."""
    print("Initializing database...")
    
    # Ensure data directories exist
    os.makedirs("data/db", exist_ok=True)
    os.makedirs("data/logs", exist_ok=True)
    os.makedirs("data/temp_cache", exist_ok=True)
    
    # Initialize database
    db_path = os.getenv("DATABASE_PATH", "data/db/tasks.db")
    db_manager = DatabaseManager(db_path)
    
    # Initialize repositories (this creates tables)
    print("Creating tasks table...")
    task_repo = TaskRepository(db_manager)
    
    print("Creating unified_recipients table...")
    recipient_repo = UnifiedRecipientRepository(db_manager)
    
    print("Database initialization complete!")
    print(f"Database location: {db_path}")


if __name__ == "__main__":
    init_database()