from aiogram.fsm.state import State, StatesGroup

class SharingState(StatesGroup):
    selecting_share_type = State()
    selecting_account_to_share = State()
    waiting_for_grantee_username = State()

class AuthRequestState(StatesGroup):
    selecting_platform = State()
    waiting_for_account_name = State()
    waiting_for_target_username = State()
    waiting_for_oauth = State()
    waiting_for_credentials = State()
    waiting_for_auth_code = State()