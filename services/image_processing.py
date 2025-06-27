"""Image processing service for screenshot analysis and text extraction."""

import io
from typing import Dict, Any, Union, List
from aiogram.types import PhotoSize, Document

from core.exceptions import TranscriptionError
from core.logging import get_logger
from core.interfaces import IImageProcessingService, IOpenAIService

logger = get_logger(__name__)


class ImageProcessingService(IImageProcessingService):
    """Service for processing image messages."""
    
    def __init__(self, openai_service: IOpenAIService):
        self.openai_service = openai_service
    
    async def process_image_message(self, media: Union[List[PhotoSize], Document], bot) -> Dict[str, Any]:
        """Process an image message and return analyzed content.
        
        Args:
            media: List of PhotoSize objects (inline photos) or Document object (attachment)
            bot: Telegram bot instance
            
        Returns:
            Dictionary containing extracted text and summary
            
        Raises:
            TranscriptionError: If image processing fails
        """
        try:
            # Handle both inline photos and document attachments
            if isinstance(media, list):
                # Inline photo (list of PhotoSize)
                largest_photo = max(media, key=lambda p: p.file_size or 0)
                file_id = largest_photo.file_id
                file_name = f"screenshot_{file_id}.jpg"
                logger.debug(f"Processing inline photo: {file_id}")
            else:
                # Document attachment
                file_id = media.file_id
                file_name = media.file_name or f"attachment_{file_id}.jpg"
                logger.debug(f"Processing document attachment: {file_id}, name: {file_name}")
            
            file_info = await bot.get_file(file_id)
            image_data = io.BytesIO()
            await bot.download_file(file_info.file_path, image_data)
            
            image_bytes = image_data.getvalue()
            logger.debug(f"Downloaded image: {len(image_bytes)} bytes")
            
            analysis_result = await self.openai_service.analyze_image(image_bytes)
            
            parsed_result = self._parse_analysis_result(analysis_result)
            
            logger.info(f"Successfully processed image: extracted {len(parsed_result.get('extracted_text', ''))} characters")
            
            return {
                'extracted_text': parsed_result.get('extracted_text', ''),
                'summary': parsed_result.get('summary', ''),
                'raw_analysis': analysis_result,
                'source_type': 'screenshot',
                'image_data': image_bytes,
                'file_name': file_name
            }
            
        except Exception as e:
            logger.error(f"Failed to process image message: {e}")
            raise TranscriptionError(f"Image processing failed: {e}")
    
    def _parse_analysis_result(self, analysis_result: str) -> Dict[str, str]:
        """Parse the structured analysis result from OpenAI.
        
        Args:
            analysis_result: Raw analysis text from OpenAI
            
        Returns:
            Dictionary with extracted_text and summary
        """
        try:
            extracted_text = ""
            summary = ""
            
            if "TEXT EXTRACTED:" in analysis_result:
                parts = analysis_result.split("TEXT EXTRACTED:")
                if len(parts) > 1:
                    text_part = parts[1]
                    if "SUMMARY:" in text_part:
                        text_parts = text_part.split("SUMMARY:")
                        extracted_text = text_parts[0].strip()
                        summary = text_parts[1].strip() if len(text_parts) > 1 else ""
                    else:
                        extracted_text = text_part.strip()
            else:
                extracted_text = analysis_result
            
            return {
                'extracted_text': extracted_text,
                'summary': summary
            }
            
        except Exception as e:
            logger.warning(f"Failed to parse analysis result, using raw: {e}")
            return {
                'extracted_text': analysis_result,
                'summary': ""
            }