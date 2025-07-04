"""Profile settings callback handlers."""

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from bot import router
from states.recipient_states import RecipientState
from keyboards.recipient import get_profile_settings_keyboard
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)


@router.callback_query(lambda c: c.data == "profile_settings")
async def profile_settings_callback(callback_query: CallbackQuery, state: FSMContext):
    """Show profile settings menu."""
    keyboard = get_profile_settings_keyboard()
    
    await callback_query.message.edit_text(
        "👤 *Profile Settings*\n\n"
        "Manage your personal information:",
        reply_markup=keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await callback_query.answer()


@router.callback_query(lambda c: c.data == "update_owner_name")
async def update_owner_name_callback(callback_query: CallbackQuery, state: FSMContext):
    """Start owner name update process."""
    logger.info(f"Update owner name callback triggered for user {callback_query.from_user.id}")
    
    cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_settings")]
    ])
    
    await callback_query.message.edit_text(
        "👤 *Update Your Name*\n\n"
        "Enter your name (this helps with personalized task creation):",
        reply_markup=cancel_keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await state.set_state(RecipientState.waiting_for_owner_name)
    logger.info(f"Set state to waiting_for_owner_name for user {callback_query.from_user.id}")
    await callback_query.answer()


@router.callback_query(lambda c: c.data == "update_location")
async def update_location_callback(callback_query: CallbackQuery, state: FSMContext):
    """Start location update process."""
    cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_settings")]
    ])
    
    await callback_query.message.edit_text(
        "🌍 *Update Your Location*\n\n"
        "Enter your location (for timezone handling):\n"
        "Examples: Portugal, Cascais, New York, California, UK",
        reply_markup=cancel_keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await state.set_state(RecipientState.waiting_for_location)
    await callback_query.answer()