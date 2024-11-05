
import sqlite3

from bot import logger

# Initialize connection to SQLite database
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
              due_time TEXT,
              todoist_user TEXT)''')  # Added todoist_user to associate Telegram users with Todoist accounts
# Create users table if it doesn't exist
c.execute('''CREATE TABLE IF NOT EXISTS users
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              telegram_user_id INTEGER UNIQUE,
              todoist_user TEXT,
              owner_name TEXT,
              location TEXT)''')
conn.commit()


# Function to get all tasks from the database
def get_tasks():
    c.execute('SELECT id, user_id, chat_id, message_id, task_title, task_description, due_time FROM tasks')
    return c.fetchall()

# Function to delete a task from the database
def delete_task(task_id):
    c.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()

# Function to save a task to the database
# Function to save a task to the database
def save_task(user_id, chat_id, message_id, title, description, due_time):
    try:
        c.execute('''INSERT INTO tasks (user_id, chat_id, message_id, task_title, task_description, due_time)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (user_id, chat_id, message_id, title, description, due_time))
        conn.commit()
        logger.info(f"Task saved for user {user_id}")
    except Exception as e:
        logger.error(f"Database error: {e}")

# Function to get Todoist user associated with Telegram user
def get_todoist_user(telegram_user_id):
    try:
        c.execute('SELECT todoist_user FROM users WHERE telegram_user_id = ?', (telegram_user_id,))
        result = c.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Database error: {e}")
        return None

# Function to save Todoist user for a Telegram user
# Modify save_todoist_user to accept and store location
def save_todoist_user(telegram_user_id, todoist_user, owner_name, location=None):
    try:
        c.execute('''INSERT OR REPLACE INTO users (telegram_user_id, todoist_user, owner_name, location) VALUES (?, ?, ?, ?)''',
                  (telegram_user_id, todoist_user, owner_name, location))
        conn.commit()
        logger.info(f"Todoist user saved for Telegram user {telegram_user_id} with owner {owner_name}")
    except Exception as e:
        logger.error(f"Database error: {e}")

# Retrieve Todoist user, owner, and location information
def get_todoist_user_info(telegram_user_id):
    try:
        c.execute('SELECT todoist_user, owner_name, location FROM users WHERE telegram_user_id = ?', (telegram_user_id,))
        result = c.fetchone()
        return result if result else (None, None, None)
    except Exception as e:
        logger.error(f"Database error: {e}")
        return None, None, None

# Function to save Todoist user for a Telegram user


__all__ = ['get_tasks', 'delete_task', 'save_task', 'get_todoist_user', 'save_todoist_user']
