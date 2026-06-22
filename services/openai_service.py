"""OpenAI service for audio transcription and text processing."""

import base64
import time
from typing import BinaryIO

from core.exceptions import TranscriptionError
from core.logging import get_logger
from core.interfaces import IOpenAIService
from ai_workflow_engine.models import WorkflowUsageEvent
from services.openai_cache import openai_prompt_cache_kwargs
from services.llm_factory import create_openai_client
from ai_workflow_engine.usage import check_budget_before_call, estimate_cost_usd, record_usage_event

logger = get_logger(__name__)


def _usage_details(value) -> dict[str, int]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        return {}
    details = {}
    for key, raw in value.items():
        if isinstance(raw, (int, float)):
            details[str(key)] = int(raw)
    return details


class OpenAIService(IOpenAIService):
    """Service for OpenAI API operations."""
    
    def __init__(self, api_key: str, config=None):
        self.api_key = api_key
        self.config = config
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
        
        self.client = create_openai_client(self.config, api_key=self.api_key)
    
    async def transcribe_audio(self, audio_data: BinaryIO) -> str:
        """Transcribe audio data using OpenAI Whisper.
        
        Args:
            audio_data: Audio file data
            
        Returns:
            Transcribed text
            
        Raises:
            TranscriptionError: If transcription fails
        """
        start = time.monotonic()
        try:
            response = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_data
            )
            if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
                record_usage_event(
                    WorkflowUsageEvent(
                        operation="tool",
                        node="audio_transcription",
                        model="whisper-1",
                        request_id=getattr(response, "_request_id", None),
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                        metadata={"provider_endpoint": "audio.transcriptions.create"},
                    )
                )
            
            logger.debug(f"Successfully transcribed audio: {len(response.text)} characters")
            return response.text
            
        except Exception as e:
            if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
                record_usage_event(
                    WorkflowUsageEvent(
                        operation="tool",
                        node="audio_transcription",
                        model="whisper-1",
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                        success=False,
                        error=str(e)[:500],
                        metadata={"provider_endpoint": "audio.transcriptions.create"},
                    )
                )
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
        if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
            check_budget_before_call("chat", "image_analyzer")
        start = time.monotonic()
        try:
            base64_image = base64.b64encode(image_data).decode('utf-8')
            
            analysis_prompt = prompt or (
                "Analyze this image and extract ALL text visible in it. "
                "Then provide a brief summary of what the image shows. "
                "Format your response as:\n"
                "TEXT EXTRACTED:\n[all text found]\n\n"
                "SUMMARY:\n[brief description of the image content]"
            )
            model = getattr(self.config, "OPENAI_VISION_MODEL", None) or getattr(
                self.config,
                "ANKI_CARD_MODEL",
                "gpt-5.4-mini",
            )
            
            kwargs = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": analysis_prompt,
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Analyze the attached image."
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
                "max_completion_tokens": 1000,
            }
            kwargs.update(openai_prompt_cache_kwargs(self.config, model=model, node="image_analyzer"))

            response = await self.client.chat.completions.create(**kwargs)
            if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
                usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
                input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
                input_details = _usage_details(
                    usage.get("prompt_tokens_details") or usage.get("input_token_details")
                )
                output_details = _usage_details(
                    usage.get("completion_tokens_details") or usage.get("output_token_details")
                )
                record_usage_event(
                    WorkflowUsageEvent(
                        operation="chat",
                        node="image_analyzer",
                        model=getattr(response, "model", model),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        input_token_details=input_details,
                        output_token_details=output_details,
                        estimated_usd=estimate_cost_usd(
                            getattr(response, "model", model),
                            "chat",
                            input_tokens,
                            output_tokens,
                            input_details,
                            output_details,
                            config=self.config,
                        ),
                        request_id=getattr(response, "_request_id", None),
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                        metadata={"provider_endpoint": "chat.completions.create"},
                    )
                )
            
            result = response.choices[0].message.content
            logger.debug(f"Successfully analyzed image: {len(result)} characters")
            return result
            
        except Exception as e:
            if getattr(self.config, "WORKFLOW_USAGE_TRACKING_ENABLED", True):
                record_usage_event(
                    WorkflowUsageEvent(
                        operation="chat",
                        node="image_analyzer",
                        model=getattr(self.config, "OPENAI_VISION_MODEL", None)
                        or getattr(self.config, "ANKI_CARD_MODEL", "gpt-5.4-mini"),
                        elapsed_ms=int((time.monotonic() - start) * 1000),
                        success=False,
                        error=str(e)[:500],
                        metadata={"provider_endpoint": "chat.completions.create"},
                    )
                )
            logger.error(f"Failed to analyze image: {e}")
            raise TranscriptionError(f"Image analysis failed: {e}")

# Remove global instance - use DI container instead
