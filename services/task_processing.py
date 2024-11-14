from aiogram.types import CallbackQuery
from db_handler import get_todoist_user_info
from langchain_parser import parse_description_with_langchain

async def create_task_from_text(text: str, user_id: int, callback_query: CallbackQuery):
    todoist_user, owner_name, location = get_todoist_user_info(user_id)
    
    parsed_task = parse_description_with_langchain(
        content_message=text,
        owner_name=owner_name,
        location=location
    )
    
    # Create task logic here
    
    await callback_query.message.edit_text(
        f"✅ Task created from voice message:\n{text}"
    ) 