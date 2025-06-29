"""OpenAI service for audio transcription and text processing."""

import base64
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
    
    async def analyze_image(self, image_data: bytes, prompt: str = None) -> str:
        """Analyze image and extract text/content using OpenAI Vision.
        
        Args:
            image_data: Image file data as bytes
            prompt: Optional specific prompt for analysis
            
        Returns:
            Analyzed text content from image
            
        Raises:
            TranscriptionError: If image analysis fails
        """
        try:
            base64_image = base64.b64encode(image_data).decode('utf-8')
            
            analysis_prompt = prompt or (
                "Analyze this image and extract ALL text visible in it. "
                "Then provide a brief summary of what the image shows. "
                "Format your response as:\n"
                "TEXT EXTRACTED:\n[all text found]\n\n"
                "SUMMARY:\n[brief description of the image content]"
            )
            
            response = await self.client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": analysis_prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1000
            )
            
            result = response.choices[0].message.content
            logger.debug(f"Successfully analyzed image: {len(result)} characters")
            return result
            
        except Exception as e:
            logger.error(f"Failed to analyze image: {e}")
            raise TranscriptionError(f"Image analysis failed: {e}")

# Remove global instance - use DI container instead 