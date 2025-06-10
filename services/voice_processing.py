"""Voice message processing service."""

from io import BytesIO
from typing import BinaryIO
from aiogram.types import Voice
from aiogram import Bot

from services.openai_service import OpenAIService
from core.exceptions import TranscriptionError
from core.logging import get_logger
from core.interfaces import IOpenAIService, IVoiceProcessingService

logger = get_logger(__name__)

class VoiceProcessingService(IVoiceProcessingService):
    """Service for processing voice messages."""
    
    def __init__(self, openai_service: IOpenAIService):
        self.openai_service = openai_service
    
    async def process_voice_message(self, voice: Voice, bot: Bot) -> str:
        """Process a voice message and return transcribed text.
        
        Args:
            voice: Telegram voice message
            bot: Telegram bot instance
            
        Returns:
            Transcribed text
            
        Raises:
            TranscriptionError: If processing fails
        """
        try:
            # Download voice file
            file = await bot.get_file(voice.file_id)
            downloaded_file = await bot.download_file(file.file_path)
            
            # Prepare audio data
            audio_data = self._prepare_audio_file(downloaded_file)
            
            # Transcribe
            result = await self.openai_service.transcribe_audio(audio_data)
            
            logger.info(f"Successfully processed voice message: {len(result)} characters")
            return result
            
        except Exception as e:
            logger.error(f"Failed to process voice message: {e}")
            raise TranscriptionError(f"Voice processing failed: {e}")
    
    def _prepare_audio_file(self, file_data) -> BinaryIO:
        """Prepare audio file data for transcription.
        
        Args:
            file_data: Downloaded file data
            
        Returns:
            BytesIO object with audio data
        """
        audio_data = BytesIO(file_data.read())
        audio_data.name = "voice_message.ogg"
        return audio_data

 