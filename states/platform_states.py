"""FSM states for platform configuration."""

from aiogram.fsm.state import StatesGroup, State

class TaskPlatformState(StatesGroup):
    """States for task platform configuration flow."""
    selecting_platform = State()
    waiting_for_api_key = State()
    waiting_for_location = State()
    
    # Trello-specific states
    waiting_for_board_selection = State()
    waiting_for_list_selection = State()

class DropUserDataState(StatesGroup):
    """States for user data deletion flow."""
    waiting_for_confirmation = State()