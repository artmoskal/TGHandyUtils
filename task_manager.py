import logging
from db_handler import save_task, get_todoist_user
from bot import bot
from dateutil import parser
from datetime import datetime, timezone
import requests

logger = logging.getLogger(__name__)

# Function to create a task in Todoist
def create_todoist_task(parsed_task, todoist_user_token):
    if not todoist_user_token:
        logger.error("Todoist API token is not set for this user. Cannot create Todoist task.")
        return None

    url = 'https://api.todoist.com/rest/v2/tasks'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {todoist_user_token}'
    }
    data = {
        'content': parsed_task['title'],
        'description': parsed_task['description'],
        'due_datetime': parsed_task['due_time']
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code in [200, 201, 204]:
            task = response.json()
            task_id = task['id']
            logger.debug(f"Created Todoist task with ID: {task_id}")
            return task_id
        else:
            logger.error(f"Todoist API error: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Todoist API error: {e}")
        return None

# Function to save a parsed task asynchronously
async def save_task_async(parsed_task, message, owner_id, initiator_link=None):
    due_time = validate_due_time(parsed_task)
    if due_time is None:
        await message.reply("Invalid due time format or due time is in the past.")
        return

    chat_id = message.chat.id
    message_id = message.message_id
    todoist_user_token = get_todoist_user(owner_id)  # Use the passed owner ID
    if not todoist_user_token:
        await message.reply("Todoist account is not linked. Please link your Todoist account first.")
        return

    title = parsed_task['title']
    description = parsed_task['description']

    # Append the original message link if provided
    if initiator_link:
        description += f"\n\nOriginal message: {initiator_link}"

    try:
        # Save the task to the database
        save_task(owner_id, chat_id, message_id, title, description, due_time.isoformat())
        logger.info(f"Task saved for user {owner_id}")

        # Create the task in Todoist using the user's specific token
        task_id = create_todoist_task(parsed_task, todoist_user_token)
        if task_id:
            await message.reply(f"Task scheduled in Todoist: {title} for {due_time}")
        else:
            await message.reply(f"Task saved locally, but failed to create in Todoist: {title}")

    except Exception as e:
        logger.error(f"Database error: {e}")


# Function to validate the due time of a task
def validate_due_time(parsed_task):
    try:
        due_time = parser.isoparse(parsed_task['due_time']).astimezone(timezone.utc)
        now_utc = datetime.now(timezone.utc)
        if due_time <= now_utc:
            logger.warning("Due time is in the past.")
            return None
        return due_time
    except Exception as e:
        logger.error(f"Error parsing due time: {e}")
        return None