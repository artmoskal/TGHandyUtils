from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_transcription_keyboard():
    """Get the keyboard for confirming or cancelling transcription."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Confirm", callback_data="transcribe_confirm"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="transcribe_cancel")
        ]
    ])
    return keyboard

def get_platform_selection_keyboard():
    """Get the keyboard for selecting a task management platform."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Todoist", callback_data="platform_todoist"),
            InlineKeyboardButton(text="Trello", callback_data="platform_trello")
        ]
    ])
    return keyboard

def get_trello_board_selection_keyboard(boards):
    """Get a keyboard for selecting a Trello board."""
    buttons = []
    for board in boards:
        buttons.append([InlineKeyboardButton(
            text=board['name'],
            callback_data=f"trello_board_{board['id']}"
        )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_trello_list_selection_keyboard(lists):
    """Get a keyboard for selecting a Trello list."""
    buttons = []
    for list_item in lists:
        buttons.append([InlineKeyboardButton(
            text=list_item['name'],
            callback_data=f"trello_list_{list_item['id']}"
        )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_menu_keyboard():
    """Get the main menu keyboard with key features."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 My Tasks", callback_data="show_tasks"),
            InlineKeyboardButton(text="➕ Quick Task", callback_data="quick_task")
        ],
        [
            InlineKeyboardButton(text="⚙️ Settings", callback_data="show_settings"),
            InlineKeyboardButton(text="❓ Help", callback_data="show_help")
        ]
    ])
    return keyboard

def get_task_action_keyboard(task_id: str):
    """Get action keyboard for a specific task."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Complete", callback_data=f"task_complete_{task_id}"),
            InlineKeyboardButton(text="✏️ Edit", callback_data=f"task_edit_{task_id}")
        ],
        [
            InlineKeyboardButton(text="⏰ Reschedule", callback_data=f"task_reschedule_{task_id}"),
            InlineKeyboardButton(text="🗑️ Delete", callback_data=f"task_delete_{task_id}")
        ],
        [
            InlineKeyboardButton(text="« Back to Tasks", callback_data="show_tasks")
        ]
    ])
    return keyboard

def get_task_list_keyboard(tasks, page=0, page_size=5, user_id=None):
    """Get keyboard for task list with pagination."""
    buttons = []
    start_idx = page * page_size
    end_idx = start_idx + page_size
    
    for task in tasks[start_idx:end_idx]:
        # Format task with due date
        task_title = task.task_title[:25] + "..." if len(task.task_title) > 25 else task.task_title
        
        # Get due date for display in local time
        try:
            if user_id:
                # Get user location for local time display
                from core.initialization import services
                user_info = services.get_task_service().get_user_platform_info(user_id)
                location = user_info.get('location') if user_info else None
                
                # Convert to local time and format compactly
                from datetime import datetime, timezone, timedelta
                from dateutil import parser as date_parser
                
                utc_time = date_parser.isoparse(task.due_time)
                if utc_time.tzinfo is None:
                    utc_time = utc_time.replace(tzinfo=timezone.utc)
                
                offset_hours = services.get_parsing_service().get_timezone_offset(location)
                local_time = utc_time + timedelta(hours=offset_hours)
                due_str = local_time.strftime("%m/%d %H:%M")
            else:
                # Fallback to UTC display
                from dateutil import parser as date_parser
                due_time = date_parser.isoparse(task.due_time)
                due_str = due_time.strftime("%m/%d %H:%M")
        except:
            due_str = "TBD"
        
        buttons.append([InlineKeyboardButton(
            text=f"📌 {task_title} ⏰ {due_str}",
            callback_data=f"task_view_{task.id}"
        )])
    
    # Pagination buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"tasks_page_{page-1}"))
    if end_idx < len(tasks):
        nav_buttons.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"tasks_page_{page+1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    # Add main menu button
    buttons.append([InlineKeyboardButton(text="« Main Menu", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_quick_task_keyboard():
    """Get keyboard for quick task creation."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="☕ Coffee break (15 min)", callback_data="quick_coffee"),
            InlineKeyboardButton(text="📞 Call (1 hour)", callback_data="quick_call")
        ],
        [
            InlineKeyboardButton(text="🛒 Shopping (today)", callback_data="quick_shopping"),
            InlineKeyboardButton(text="💊 Medicine (daily)", callback_data="quick_medicine")
        ],
        [
            InlineKeyboardButton(text="✍️ Custom Task", callback_data="custom_task"),
            InlineKeyboardButton(text="« Back", callback_data="main_menu")
        ]
    ])
    return keyboard

__all__ = [
    'get_transcription_keyboard',
    'get_platform_selection_keyboard',
    'get_trello_board_selection_keyboard',
    'get_trello_list_selection_keyboard',
    'get_main_menu_keyboard',
    'get_task_action_keyboard',
    'get_task_list_keyboard',
    'get_quick_task_keyboard'
]