import os
import logging
from typing import Optional, List
from dotenv import load_dotenv
from core.interfaces import IConfig

load_dotenv()

class Config(IConfig):
    """Centralized configuration management."""
    
    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = os.getenv('TELEGRAM_BOT_TOKEN', '')
    
    # OpenAI Configuration
    OPENAI_API_KEY: str = os.getenv('OPENAI_API_KEY', '')
    
    # Database Configuration
    DATABASE_PATH: str = os.getenv('DATABASE_PATH', 'data/db/tasks.db')
    DATABASE_TIMEOUT: int = int(os.getenv('DATABASE_TIMEOUT', '30'))
    
    # Platform Configuration
    DEFAULT_TASK_PLATFORM: str = os.getenv('DEFAULT_TASK_PLATFORM', 'todoist')
    SUPPORTED_PLATFORMS: List[str] = ['todoist', 'trello']
    
    # Scheduler Configuration
    SCHEDULER_INTERVAL: int = int(os.getenv('SCHEDULER_INTERVAL', '20'))
    
    # Threading Configuration
    THREAD_TIMEOUT: float = float(os.getenv('THREAD_TIMEOUT', '1.0'))
    
    # Logging Configuration
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'DEBUG')
    LOG_FORMAT: str = os.getenv('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    LOG_FILE: str = os.getenv('LOG_FILE', 'data/logs/bot.log')
    
    @classmethod
    def validate(cls) -> None:
        """Validate configuration and raise errors for missing required values."""
        if not cls.TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        
        if not cls.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required")
        
        if cls.DEFAULT_TASK_PLATFORM not in cls.SUPPORTED_PLATFORMS:
            raise ValueError(f"DEFAULT_TASK_PLATFORM must be one of {cls.SUPPORTED_PLATFORMS}")
    
    @classmethod
    def get_log_level(cls) -> int:
        """Convert string log level to logging constant."""
        return getattr(logging, cls.LOG_LEVEL.upper(), logging.DEBUG)
    
    @classmethod
    def ensure_directories(cls) -> None:
        """Ensure required directories exist."""
        os.makedirs(os.path.dirname(cls.DATABASE_PATH), exist_ok=True)
        os.makedirs(os.path.dirname(cls.LOG_FILE), exist_ok=True)

# Remove global instance - use DI container instead