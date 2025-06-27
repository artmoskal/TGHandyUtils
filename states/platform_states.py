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

class PartnerManagementState(StatesGroup):
    """States for partner management flow."""
    waiting_partner_name = State()
    waiting_partner_credentials = State()
    waiting_partner_settings = State()
    waiting_partner_edit_name = State()
    waiting_partner_edit_credentials = State()