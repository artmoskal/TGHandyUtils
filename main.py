"""Main application entry point."""

import asyncio
from bot import dp, initialize_bot
from scheduler import task_scheduler
from core.initialization import wire_application, unwire_application, services
from core.logging import get_logger

logger = get_logger(__name__)

# Initialize dependency injection
wire_application()

# Import handlers module to register all handlers (after DI is wired)
import handlers

# Initialize bot with proper configuration
bot = initialize_bot()

# Initialize database schema
from core.container import container
db_manager = container.database_manager()
db_manager.initialize_schema()
logger.info("Database schema initialized")

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