"""Settings input state handlers."""

from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot import router
from states.recipient_states import RecipientState
from keyboards.recipient import get_back_to_settings_keyboard
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)


@router.message(RecipientState.waiting_for_owner_name)
async def handle_owner_name_input(message: Message, state: FSMContext):
    """Handle owner name input."""
    user_id = message.from_user.id
    logger.info(f"Handle owner name input triggered for user {user_id}, message: '{message.text}'")
    owner_name = message.text.strip()
    
    if not owner_name:
        await message.reply("❌ Name cannot be empty. Please enter your name:", disable_web_page_preview=True)
        return
    
    try:
        recipient_service = container.recipient_service()
        logger.info(f"Attempting to update owner name for user {user_id} to '{owner_name}'")
        success = recipient_service.update_owner_name(user_id, owner_name)
        logger.info(f"Update owner name result: {success}")
        
        if success:
            await state.clear()
            keyboard = get_back_to_settings_keyboard()
            await message.reply(
                f"✅ *Name Updated*\n\n"
                f"Your name has been set to: {owner_name}",
                reply_markup=keyboard,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        else:
            await message.reply("❌ Failed to update name. Please try again.", disable_web_page_preview=True)
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Failed to update owner name for user {user_id}: {e}")
        await message.reply("❌ Error updating name. Please try again.", disable_web_page_preview=True)
        await state.clear()


@router.message(RecipientState.waiting_for_location)
async def handle_location_input(message: Message, state: FSMContext):
    """Handle location input."""
    user_id = message.from_user.id
    location = message.text.strip()
    
    if not location:
        await message.reply("❌ Location cannot be empty. Please enter your location:", disable_web_page_preview=True)
        return
    
    try:
        recipient_service = container.recipient_service()
        success = recipient_service.update_location(user_id, location)
        
        if success:
            await state.clear()
            keyboard = get_back_to_settings_keyboard()
            await message.reply(
                f"✅ *Location Updated*\n\n"
                f"Your location has been set to: {location}\n\n"
                f"This will be used for timezone handling in task scheduling.",
                reply_markup=keyboard,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        else:
            await message.reply("❌ Failed to update location. Please try again.", disable_web_page_preview=True)
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Failed to update location for user {user_id}: {e}")
        await message.reply("❌ Error updating location. Please try again.", disable_web_page_preview=True)
        await state.clear()