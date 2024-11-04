import logging
import asyncio
import sqlite3
from typing import Optional

import requests
import re
import json
import os
from datetime import datetime, timezone

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dateutil import parser
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# Import LangChain and Pydantic
from langchain import OpenAI
from langchain.prompts import PromptTemplate
from langchain.output_parsers import PydanticOutputParser, OutputFixingParser
from langchain.schema import OutputParserException
from langchain.chat_models import ChatOpenAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

# Load environment variables from .env file
load_dotenv()

# Get tokens and API keys from environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TODOIST_API_TOKEN = os.getenv('TODOIST_API_TOKEN')

class TaskMessage(BaseModel):
    action: str
    initiator_message: str
    initiator_link: str

class ReminderState(StatesGroup):
    waiting_for_second_message = State()
    waiting_for_action_message = State()

# Check if all required environment variables are set
if not all([TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, TODOIST_API_TOKEN]):
    raise ValueError("Please set TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, and TODOIST_API_TOKEN in the .env file.")

# Configure logging
LOG_LEVEL = logging.DEBUG  # Set to logging.INFO or logging.ERROR to adjust verbosity
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
dp['aiogd_persistence'] = {}

# Initialize SQLite database
conn = sqlite3.connect('tasks.db', check_same_thread=False)
c = conn.cursor()

# Create tasks table if it doesn't exist
c.execute('''CREATE TABLE IF NOT EXISTS tasks
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER,
              chat_id INTEGER,
              message_id INTEGER,
              task_title TEXT,
              task_description TEXT,
              due_time TEXT)''')
conn.commit()

def get_message_link(message: Message) -> Optional[str]:
    """Constructs a link to the message if possible."""
    if message.chat.username:
        link = f"https://t.me/{message.chat.username}/{message.message_id}"
        logger.debug(f"Constructed message link: {link}")
        return link
    else:
        logger.debug("Message link cannot be constructed (no username).")
        return None

def parse_description_with_langchain(action_message, content_message=None):
    # Define the expected output schema using Pydantic
    class Task(BaseModel):
        title: str = Field(description="The title of the task.")
        due_time: str = Field(description="The due time in UTC ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ).")
        description: str = Field(description="The description or details of the task.")

    # Create an output parser using the Task schema
    parser_lc = PydanticOutputParser(pydantic_object=Task)

    # Define the prompt template with format instructions
    if content_message:
        prompt_template = """
You are an assistant that creates a task from the given messages.

The task should include a 'title', a 'due_time' in UTC ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ), and a 'description'.

Given all data try to determine most appropriate and informative title unless it's explicitly specified.

Use the 'action_message' to determine the action and due time.

Use the 'content_message' to extract relevant information for the task title and description.

If the due time is not specified, set it to 9 AM tomorrow.

Messages:
Action Message: {action_message}
Content Message: {content_message}
Current UTC time: {cur_time}

{format_instructions}
"""
    else:
        prompt_template = """
You are an assistant that creates a task from the given message.

The task should include a 'title', a 'due_time' in UTC ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ), and a 'description'.

Given all data try to determine most appropriate and informative title unless it's explicitly specified.

Use the 'action_message' to determine the title, due time, and description.

If the due time is not specified, set it to the time specified in the message, or default to 9 AM tomorrow.

Message:
Action Message: {action_message}
Current UTC time: {cur_time}

{format_instructions}
"""

    format_instructions = parser_lc.get_format_instructions()

    input_variables = ["action_message", "cur_time"]
    if content_message:
        input_variables.append("content_message")

    prompt = PromptTemplate(
        template=prompt_template,
        input_variables=input_variables,
        partial_variables={"format_instructions": format_instructions}
    )

    # Initialize the OpenAI LLM with LangChain
    llm = ChatOpenAI(
        model="gpt-4",
        temperature=0,
        max_tokens=None,
        timeout=None,
        max_retries=2,
        openai_api_key=OPENAI_API_KEY
    )
    _input_kwargs = {
        "action_message": action_message,
        "cur_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    }
    if content_message:
        _input_kwargs["content_message"] = content_message

    _input = prompt.format(**_input_kwargs)
    logger.debug(f"LLM Input: {_input}")

    try:
        output = llm([HumanMessage(content=_input)])

        logger.debug(f"LLM Output: {output.content}")
        parsed_task = parser_lc.parse(output.content)
        logger.debug(f"Parsed task: {parsed_task}")
        return parsed_task.dict()
    except OutputParserException as e:
        logger.error(f"Parsing error: {e}")
        new_parser = OutputFixingParser.from_llm(
            parser=parser_lc,
            llm=llm
        )
        fixed_output = new_parser.parse(output.content)
        logger.debug(f"Fixed output: {fixed_output}")
        return fixed_output.dict()
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return None

def create_todoist_task(parsed_task):
    url = 'https://api.todoist.com/rest/v2/tasks'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {TODOIST_API_TOKEN}'
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

@router.message()
async def handle_message(message: Message, state: FSMContext):
    """Handle messages and process tasks accordingly."""
    current_state = await state.get_state()
    logger.debug(f"Current state: {current_state}")

    if current_state == ReminderState.waiting_for_second_message:
        # Handle the follow-up message
        user_data = await state.get_data()
        first_message_text = user_data.get("first_message_text")
        first_message = user_data.get("first_message")
        logger.debug(f"First message text: {first_message_text}")

        if message.forward_date:
            # The message is a forwarded message
            logger.debug("Received forwarded message as second message.")
            initiator_link = get_message_link(message)
            logger.debug(f"Initiator link: {initiator_link}")
            combined_task = TaskMessage(
                action=first_message_text,
                initiator_message=message.text,
                initiator_link=initiator_link  # Construct the link if possible
            )
            logger.debug("Processing forwarded message as combined task.")
            # Process combined task
            await process_combined_task(combined_task, message)
        else:
            # Combine both messages into a TaskMessage
            logger.debug("Received second message.")
            initiator_link = get_message_link(first_message)
            logger.debug(f"Initiator link: {initiator_link}")
            combined_task = TaskMessage(
                action=first_message_text,
                initiator_message=message.text,
                initiator_link=initiator_link  # Construct the link if possible
            )
            # Process combined task
            await process_combined_task(combined_task, first_message)
        await state.clear()

    elif current_state == ReminderState.waiting_for_action_message:
        # Handle the action message after a forwarded message
        logger.debug("Waiting for action message after forwarded message.")
        user_data = await state.get_data()
        forwarded_message_text = user_data.get("forwarded_message_text")
        forwarded_message = user_data.get("forwarded_message")
        initiator_link = get_message_link(forwarded_message)
        logger.debug(f"Initiator link: {initiator_link}")

        # Combine the action message and the forwarded message
        combined_task = TaskMessage(
            action=message.text,
            initiator_message=forwarded_message_text,
            initiator_link=initiator_link  # Construct the link if possible
        )
        logger.debug("Processing forwarded message with action message as combined task.")
        # Process combined task
        await process_combined_task(combined_task, message)
        await state.clear()

    else:
        # Handle the initial message
        if message.forward_date:
            # Message is a forwarded message
            logger.info(f"Received forwarded message from {message.from_user.id}: {message.text}")
            # Save forwarded message and set state
            await state.update_data(forwarded_message_text=message.text, forwarded_message=message)
            await state.set_state(ReminderState.waiting_for_action_message)
            await message.reply("Please provide instructions for this forwarded message.")
        else:
            # Save initial message and set state
            user = message.from_user
            logger.info(f"Received initial message from {user.id}: {message.text}")
            await state.update_data(first_message_text=message.text, first_message=message)
            await state.set_state(ReminderState.waiting_for_second_message)

            # Wait briefly to see if a follow-up arrives
            await asyncio.sleep(1)

            # Check if still in waiting state (no follow-up received)
            if (await state.get_state()) == ReminderState.waiting_for_second_message:
                # No follow-up; treat the initial message as a standalone task
                await process_task(message.text, message)
                await state.clear()

async def process_task(description: str, message: Message):
    parsed_task = parse_description_with_langchain(description)
    if parsed_task:
        await save_task(parsed_task, message)

async def process_combined_task(task: TaskMessage, message: Message):
    parsed_task = parse_description_with_langchain(task.action, task.initiator_message)
    if parsed_task:
        await save_task(parsed_task, message, task.initiator_link)

async def save_task(parsed_task, message, initiator_link=None):
    due_time = validate_due_time(parsed_task)
    if due_time is None:
        await message.reply("Invalid due time format or due time is in the past.")
        return

    task_id = create_todoist_task(parsed_task)
    if not task_id:
        await message.reply("Failed to create task in Todoist.")
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    message_id = message.message_id
    title = parsed_task['title']
    description = parsed_task['description']

    if initiator_link:
        description += f"\n\nOriginal message: {initiator_link}"

    try:
        c.execute('''INSERT INTO tasks (user_id, chat_id, message_id, task_title, task_description, due_time)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (user_id, chat_id, message_id, title, description, due_time.isoformat()))
        conn.commit()
        logger.info(f"Task saved for user {user_id}")
        if initiator_link:
            await message.reply(f"Task scheduled: {title}. Origin: {initiator_link}")
        else:
            await message.reply(f"Task scheduled: {title}")
    except Exception as e:
        logger.error(f"Database error: {e}")

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

async def task_scheduler():
    while True:
        now = datetime.now(timezone.utc)
        c.execute('''SELECT id, user_id, chat_id, message_id, task_title, task_description, due_time FROM tasks''')
        tasks = c.fetchall()

        for task in tasks:
            task_id_db, user_id, chat_id, message_id, task_title, task_description, due_time_str = task
            try:
                due_time = parser.isoparse(due_time_str).astimezone(timezone.utc)
            except Exception as e:
                logger.error(f"Error parsing due_time from database: {e}")
                # Optionally delete the faulty task
                c.execute('DELETE FROM tasks WHERE id = ?', (task_id_db,))
                conn.commit()
                continue

            if due_time <= now:
                # Send message to user
                try:
                    reminder_text = f"⏰ Reminder: {task_title}\n\n{task_description}"
                    if message_id:
                        # Reference the original message
                        await bot.send_message(chat_id=chat_id, text=reminder_text, reply_to_message_id=message_id)
                    else:
                        # Send direct message
                        await bot.send_message(chat_id=chat_id, text=reminder_text)
                    logger.info(f"Sent reminder to user {user_id}: {task_title}")
                except Exception as e:
                    logger.error(f"Error sending message: {e}")

                # Delete the task from database
                c.execute('DELETE FROM tasks WHERE id = ?', (task_id_db,))
                conn.commit()

        # Sleep for a while
        await asyncio.sleep(60)

async def main():
    # Start the scheduler
    asyncio.create_task(task_scheduler())

    # Start polling
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        conn.close()
        logger.info("Bot stopped.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
