"""Main application entry point."""

import asyncio
import faulthandler
import os
import signal
import sys
import time
from bot import dp, initialize_bot
from scheduler import task_scheduler
from composition.initialization import wire_application, unwire_application, services
from core.logging import get_logger

logger = get_logger(__name__)

# Enable faulthandler: send SIGUSR1 to dump all thread stacks (e.g. on freeze)
faulthandler.register(signal.SIGUSR1)
logger.info(f"faulthandler registered on SIGUSR1 — send `kill -SIGUSR1 {os.getpid()}` to dump stacks")

# Initialize dependency injection
wire_application()

# Import handlers module to register all handlers (after DI is wired)
import telegram_handlers  # Phase 2 transition: hybrid modular + monolithic

# Register middleware for user tracking
from handlers_modular.middleware.user_tracking import UserTrackingMiddleware
dp.message.middleware(UserTrackingMiddleware())
dp.callback_query.middleware(UserTrackingMiddleware())

# Initialize bot with proper configuration
bot = initialize_bot()

# Database initialization - migrations.py is the single source of truth
from database.migrations import ensure_database_ready

logger.info("Checking database status...")
config = services.get_config()
if not ensure_database_ready(config.DATABASE_PATH):
    logger.error("Database initialization failed. Exiting.")
    sys.exit(1)
logger.info("Database ready and schema verified")

# Log configuration
config = services.get_config()
logger.info(f"Using default task platform: {config.DEFAULT_TASK_PLATFORM}")
logger.info(f"Scheduler interval: {config.SCHEDULER_INTERVAL} seconds")

# Final handler check
logger.info("=== Final Handler Count ===")
total = 0
for r in [dp] + list(dp.sub_routers):
    count = len(r.message.handlers)
    total += count
    if count > 0:
        logger.info(f"Router {id(r)}: {count} handlers")
logger.info(f"Total handlers before main(): {total}")

async def _watchdog():
    """Detect event loop freezes via drift measurement."""
    interval = 60
    while True:
        t0 = time.monotonic()
        await asyncio.sleep(interval)
        drift = time.monotonic() - t0 - interval
        if drift > 5:
            logger.warning(f"watchdog: event loop was blocked for {drift:.1f}s")
        else:
            logger.debug(f"watchdog: alive, drift={drift:.2f}s")


async def main():
    """Main application function."""
    try:
        # Debug: Check handlers before starting
        logger.info(f"=== Handler Check Before Start ===")
        logger.info(f"Main dispatcher has {len(dp.sub_routers)} sub-routers")
        total_handlers = 0
        for r in [dp] + list(dp.sub_routers):
            handler_count = len(r.message.handlers)
            total_handlers += handler_count
            if handler_count > 0:
                logger.info(f"Router {id(r)}: {handler_count} message handlers")
                # Show command handlers
                for h in r.message.handlers:
                    for f in h.filters:
                        if hasattr(f.callback, 'commands'):
                            logger.info(f"  Command: /{','.join(f.callback.commands)}")
        logger.info(f"Total message handlers: {total_handlers}")

        # Start the task scheduler in the background
        scheduler_task = asyncio.create_task(task_scheduler(bot))
        logger.info("Task scheduler started")

        # Start event-loop watchdog
        watchdog_task = asyncio.create_task(_watchdog())
        logger.info("Event loop watchdog started (60s interval)")

        # Start polling to receive updates from Telegram
        logger.info("Starting bot...")
        await dp.start_polling(bot)
        logger.warning("start_polling() returned — this should not happen in normal operation")

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

        # Cancel watchdog task
        if 'watchdog_task' in locals():
            watchdog_task.cancel()
            try:
                await watchdog_task
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