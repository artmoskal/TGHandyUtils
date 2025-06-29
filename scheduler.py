"""Task scheduler for sending reminders."""

import asyncio
import html
from datetime import datetime, timezone
from dateutil import parser

from core.initialization import services
from core.logging import get_logger
from core.exceptions import DatabaseError

logger = get_logger(__name__)

async def task_scheduler(bot_instance=None):
    """Main scheduler loop that checks for due tasks and sends reminders."""
    config = services.get_config()
    logger.info(f"Task scheduler started with {config.SCHEDULER_INTERVAL}s interval")
    
    # Store bot instance globally for other functions to use
    global bot
    if bot_instance:
        bot = bot_instance
    else:
        from bot import bot
    
    while True:
        try:
            await _check_and_process_due_tasks()
        except Exception as e:
            logger.error(f"Error in task scheduler: {e}")
        
        # Wait before checking again
        await asyncio.sleep(services.get_config().SCHEDULER_INTERVAL)

async def _check_and_process_due_tasks():
    """Check for due tasks and process them."""
    try:
        now = datetime.now(timezone.utc)
        task_service = services.get_clean_recipient_task_service()
        tasks = task_service.task_repo.get_all()
        
        if not tasks:
            return
        
        logger.debug(f"Checking {len(tasks)} tasks for due reminders")
        
        for task in tasks:
            try:
                await _process_task_reminder(task, now)
            except Exception as e:
                logger.error(f"Error processing task {task.id}: {e}")
                
    except DatabaseError as e:
        logger.error(f"Database error in scheduler: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in scheduler: {e}")

async def _process_task_reminder(task, current_time: datetime):
    """Process a single task for reminder.
    
    Args:
        task: TaskDB instance
        current_time: Current UTC datetime
    """
    try:
        # Parse the due time from the database
        due_time = parser.isoparse(task.due_time).astimezone(timezone.utc)
    except Exception as e:
        logger.error(f"Error parsing due_time for task {task.id}: {e}")
        # Delete tasks with invalid due time to prevent repeated errors
        task_service = services.get_clean_recipient_task_service()
        task_service.task_repo.delete(task.id)
        return
    
    # Check if task is due
    if due_time <= current_time:
        try:
            await _send_reminder(task)
            # Delete the task after the reminder is sent
            task_service = services.get_clean_recipient_task_service()
            task_service.task_repo.delete(task.id)
            logger.info(f"Processed and deleted due task {task.id}: {task.task_title}")
        except Exception as e:
            logger.error(f"Error sending reminder for task {task.id}: {e}")

async def _send_reminder(task):
    """Send reminder message for a task.
    
    Args:
        task: TaskDB instance
    """
    # Check user's notification preferences
    try:
        from core.container import container
        recipient_service = container.clean_recipient_service()
        telegram_notifications = recipient_service.are_telegram_notifications_enabled(task.user_id)
    except Exception:
        # Fallback for testing or when DI is not wired - default to enabled
        telegram_notifications = True
    
    if not telegram_notifications:
        logger.info(f"Skipping reminder for user {task.user_id} (notifications disabled): {task.task_title}")
        return
    
    # Escape special characters to prevent markdown parsing issues
    safe_title = html.escape(task.task_title)
    safe_description = html.escape(task.task_description)
    reminder_text = f"⏰ Reminder: {safe_title}\n\n{safe_description}"
    
    try:
        if task.message_id:
            # Reply to the original message if possible
            await bot.send_message(
                chat_id=task.chat_id, 
                text=reminder_text, 
                reply_to_message_id=task.message_id
            )
        else:
            # Send a new message if reply is not possible
            await bot.send_message(chat_id=task.chat_id, text=reminder_text)
        
        logger.info(f"Sent reminder to user {task.user_id}: {task.task_title}")
        
    except Exception as e:
        logger.error(f"Failed to send reminder message: {e}")
        raise