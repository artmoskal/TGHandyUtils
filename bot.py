
import logging
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Get tokens and API keys
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Check if the TELEGRAM_BOT_TOKEN is set
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Please set TELEGRAM_BOT_TOKEN in the .env file.")

# Configure logging to help with debugging and monitoring
LOG_LEVEL = logging.DEBUG
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# bot.py exposes bot, dp, and router for other modules to use
__all__ = ['bot', 'dp', 'router']