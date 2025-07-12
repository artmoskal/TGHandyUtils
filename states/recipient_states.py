"""Clean recipient states - no legacy code."""

from aiogram.fsm.state import State, StatesGroup


class RecipientState(StatesGroup):
    """States for recipient management flow."""
    
    # Platform configuration
    selecting_platform_type = State()
    waiting_for_credentials = State()
    waiting_for_trello_config = State()
    waiting_for_google_oauth_code = State()
    
    # Shared recipient configuration
    waiting_for_recipient_name = State()
    
    # Task creation with recipient selection
    selecting_recipients = State()
    waiting_for_task = State()
    
    # Settings management
    waiting_for_owner_name = State()
    waiting_for_location = State()