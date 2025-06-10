"""OpenAI service for audio transcription and text processing."""

from typing import BinaryIO
from openai import AsyncOpenAI

from core.exceptions import TranscriptionError
from core.logging import get_logger
from core.interfaces import IOpenAIService

logger = get_logger(__name__)

class OpenAIService(IOpenAIService):
    """Service for OpenAI API operations."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
        
        self.client = AsyncOpenAI(api_key=self.api_key)
    
    async def transcribe_audio(self, audio_data: BinaryIO) -> str:
        """Transcribe audio data using OpenAI Whisper.
        
        Args:
            audio_data: Audio file data
            
        Returns:
            Transcribed text
            
        Raises:
            TranscriptionError: If transcription fails
        """
        try:
            response = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_data
            )
            
            logger.debug(f"Successfully transcribed audio: {len(response.text)} characters")
            return response.text
            
        except Exception as e:
            logger.error(f"Failed to transcribe audio: {e}")
            raise TranscriptionError(f"Audio transcription failed: {e}")

# Remove global instance - use DI container instead 