
import asyncio
from datetime import datetime, timezone
from db_handler import get_tasks, delete_task
from bot import bot
from dateutil import parser
import logging

logger = logging.getLogger(__name__)

# Scheduler that periodically checks tasks and sends reminders if due
async def task_scheduler():
    while True:
        now = datetime.now(timezone.utc)

        tasks = get_tasks()  # Retrieve tasks from the database

        for task in tasks:
            task_id_db, user_id, chat_id, message_id, task_title, task_description, due_time_str = task
            try:
                # Parse the due time from the database
                due_time = parser.isoparse(due_time_str).astimezone(timezone.utc)
            except Exception as e:
                logger.error(f"Error parsing due_time from database: {e}")
                # Delete tasks with invalid due time to prevent repeated errors
                delete_task(task_id_db)
                continue

            if due_time <= now:
                try:
                    # Send reminder message to the user
                    reminder_text = f"⏰ Reminder: {task_title}\n\n{task_description}"
                    if message_id:
                        # Reply to the original message if possible
                        await bot.send_message(chat_id=chat_id, text=reminder_text, reply_to_message_id=message_id)
                    else:
                        # Send a new message if reply is not possible
                        await bot.send_message(chat_id=chat_id, text=reminder_text)
                    logger.info(f"Sent reminder to user {user_id}: {task_title}")
                except Exception as e:
                    logger.error(f"Error sending message: {e}")

                # Delete the task after the reminder is sent
                delete_task(task_id_db)

        # Wait before checking again
        await asyncio.sleep(20)