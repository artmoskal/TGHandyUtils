"""Main application entry point."""

import asyncio
import sys
from bot import dp, initialize_bot
from scheduler import task_scheduler
from core.initialization import wire_application, unwire_application, services
from core.logging import get_logger

logger = get_logger(__name__)

# Initialize dependency injection
wire_application()

# Import handlers module to register all handlers (after DI is wired)
import telegram_handlers  # Phase 2 transition: hybrid modular + monolithic

# Initialize bot with proper configuration
bot = initialize_bot()

# Database initialization with migration support
from core.container import container
from database.migrations import ensure_database_ready
from database.unified_recipient_schema import initialize_unified_schema

logger.info("Checking database status...")
config = services.get_config()
if not ensure_database_ready(config.DATABASE_PATH):
    logger.error("Database initialization failed. Exiting.")
    sys.exit(1)

# Legacy schema initialization (for compatibility)
db_manager = container.database_manager()
try:
    initialize_unified_schema(db_manager)
    logger.info("Database ready and schema verified")
except Exception as e:
    logger.warning(f"Legacy schema initialization warning: {e}")
    # Continue anyway since the new migration system should handle this

# Log configuration
config = services.get_config()
logger.info(f"Using default task platform: {config.DEFAULT_TASK_PLATFORM}")
logger.info(f"Scheduler interval: {config.SCHEDULER_INTERVAL} seconds")

async def main():
    """Main application function."""
    try:
        # Start the task scheduler in the background
        scheduler_task = asyncio.create_task(task_scheduler(bot))
        logger.info("Task scheduler started")

        # Start polling to receive updates from Telegram
        logger.info("Starting bot...")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"Error in main application: {e}")
        raise
    finally:
        # Cancel scheduler task
        if 'scheduler_task' in locals():
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
        
        # Unwire dependency injection
        unwire_application()
        
        # Close the bot session cleanly when stopping
        await bot.session.close()
        logger.info("Bot stopped.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        raise