from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def get_transcription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, correct", callback_data="transcribe_confirm"),
            InlineKeyboardButton(text="❌ No, retry", callback_data="transcribe_retry")
        ]
    ]) 