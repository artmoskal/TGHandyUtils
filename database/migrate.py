import sqlite3
import os
from pathlib import Path
from core.logging import get_logger

logger = get_logger(__name__)

class MigrationManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.connection = None

    def migrate(self):
        """Run all migrations from scratch."""
        try:
            # Ensure database directory exists
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            self.connection = sqlite3.connect(self.db_path)
            self.connection.execute("PRAGMA foreign_keys = ON")
            
            # Read and execute schema
            schema_path = Path(__file__).parent / "schema.sql"
            with open(schema_path, 'r') as f:
                schema_sql = f.read()
            
            self.connection.executescript(schema_sql)
            self.connection.commit()
            
            logger.info("Database schema created successfully")
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            if self.connection:
                self.connection.rollback()
            raise
        finally:
            if self.connection:
                self.connection.close()

if __name__ == "__main__":
    import sys
    db_path = os.getenv("DATABASE_PATH", "data/tghandyutils.db")
    manager = MigrationManager(db_path)
    manager.migrate()