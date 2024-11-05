
import asyncio
from bot import dp, bot, logger
from scheduler import task_scheduler
from handlers import handle_message, receive_todoist_key, receive_location  # Import handlers to register them

async def main():
    # Start the task scheduler in the background
    asyncio.create_task(task_scheduler())

    # Start polling to receive updates from Telegram
    try:
        await dp.start_polling(bot)
    finally:
        # Close the bot session cleanly when stopping
        await bot.session.close()
        logger.info("Bot stopped.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")