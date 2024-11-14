from aiogram.types import Voice
from aiogram import Bot
from .openai_service import transcribe_audio

async def process_voice_message(voice: Voice, bot: Bot) -> str:
    file = await bot.get_file(voice.file_id)
    downloaded_file = await bot.download_file(file.file_path)
    
    audio_data = prepare_audio_file(downloaded_file)
    return await transcribe_audio(audio_data)

def prepare_audio_file(file_data):
    from io import BytesIO
    audio_data = BytesIO(file_data.read())
    audio_data.name = "voice_message.ogg"
    return audio_data 