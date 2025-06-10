"""Bot initialization and configuration."""

import os
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties

from core.logging import setup_logging, get_logger

# Setup basic logging
setup_logging()
logger = get_logger(__name__)

# Initialize dispatcher and router without bot for now
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Bot will be initialized later when we have proper config
bot = None

def initialize_bot():
    """Initialize bot with proper configuration."""
    global bot
    if bot is None:
        from core.initialization import services
        config = services.get_config()
        
        if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == 'dummy_token_for_testing':
            # Use environment variable if config doesn't have it
            token = os.getenv('TELEGRAM_BOT_TOKEN')
            if not token:
                raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
        else:
            token = config.TELEGRAM_BOT_TOKEN
            
        bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode='HTML')
        )
        logger.info("Bot initialized successfully")
    
    return bot

# bot.py exposes bot, dp, and router for other modules to use
__all__ = ['bot', 'dp', 'router', 'initialize_bot']