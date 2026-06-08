"""Provider-neutral voice generation helpers for workflow artifacts."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ai_workflow_engine.models import WorkflowUsageEvent
from ai_workflow_engine.usage import record_usage_event


class VoiceGenerationError(RuntimeError):
    """Raised when a voice provider cannot synthesize an artifact."""


@dataclass(frozen=True)
class VoiceGenerationRequest:
    text: str
    output_dir: str
    output_basename: str
    voice_id: str = ""
    model: str = "eleven_multilingual_v2"
    output_format: str = "mp3_44100_128"
    provider: str = "elevenlabs"
    workflow_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedVoiceAudio:
    path: str
    basename: str
    provider: str
    model: str
    voice_id: str
    output_format: str
    request_id: Optional[str] = None
    character_count: int = 0
    usage_metadata: dict[str, Any] = field(default_factory=dict)


def create_voice_generator(config: Any):
    provider = str(getattr(config, "WORKFLOW_VOICE_PROVIDER", "elevenlabs") or "elevenlabs").strip().lower()
    if provider in {"elevenlabs", "11labs", "eleven"}:
        return ElevenLabsVoiceGenerator(config)
    raise VoiceGenerationError(f"Unsupported WORKFLOW_VOICE_PROVIDER={provider!r}; expected elevenlabs")


class ElevenLabsVoiceGenerator:
    """ElevenLabs text-to-speech artifact generator.

    Uses direct HTTP to avoid adding a provider SDK dependency. Voice identity is configured
    separately from request text so the graph can stay provider-neutral.
    """

    def __init__(self, config: Any):
        self.config = config

    async def generate(self, request: VoiceGenerationRequest) -> GeneratedVoiceAudio:
        api_key = str(getattr(self.config, "ELEVENLABS_API_KEY", "") or "").strip()
        voice_id = (request.voice_id or str(getattr(self.config, "WORKFLOW_ELEVENLABS_VOICE_ID", "") or "")).strip()
        if not api_key:
            raise VoiceGenerationError("ELEVENLABS_API_KEY is required for voice generation")
        if not voice_id:
            raise VoiceGenerationError("WORKFLOW_ELEVENLABS_VOICE_ID is required for voice generation")
        text = (request.text or "").strip()
        if not text:
            raise VoiceGenerationError("Voice generation text is empty")

        os.makedirs(request.output_dir, exist_ok=True)
        output_path = os.path.join(request.output_dir, request.output_basename)
        start = time.monotonic()
        try:
            response_headers = await asyncio.to_thread(
                self._synthesize,
                api_key,
                voice_id,
                text,
                request.model,
                request.output_format,
                output_path,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            request_id = response_headers.get("x-request-id") or response_headers.get("request-id")
            record_usage_event(
                WorkflowUsageEvent(
                    provider="elevenlabs",
                    operation="tool",
                    node="generate_voice",
                    model=request.model,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    input_token_details={"characters": len(text)},
                    estimated_usd=self._estimated_cost(len(text)),
                    request_id=request_id,
                    elapsed_ms=elapsed_ms,
                    metadata={
                        "voice_id": voice_id,
                        "output_format": request.output_format,
                        "character_count": len(text),
                        **request.metadata,
                    },
                )
            )
            return GeneratedVoiceAudio(
                path=output_path,
                basename=request.output_basename,
                provider="elevenlabs",
                model=request.model,
                voice_id=voice_id,
                output_format=request.output_format,
                request_id=request_id,
                character_count=len(text),
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            record_usage_event(
                WorkflowUsageEvent(
                    provider="elevenlabs",
                    operation="tool",
                    node="generate_voice",
                    model=request.model,
                    input_token_details={"characters": len(text)},
                    estimated_usd=self._estimated_cost(len(text)),
                    elapsed_ms=elapsed_ms,
                    success=False,
                    error=str(exc)[:500],
                    metadata={
                        "voice_id": voice_id,
                        "output_format": request.output_format,
                        "character_count": len(text),
                        **request.metadata,
                    },
                )
            )
            raise

    def _synthesize(
        self,
        api_key: str,
        voice_id: str,
        text: str,
        model: str,
        output_format: str,
        output_path: str,
    ) -> dict[str, str]:
        import requests

        timeout = int(getattr(self.config, "WORKFLOW_ELEVENLABS_TIMEOUT_SECONDS", 60) or 60)
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        response = requests.post(
            url,
            params={"output_format": output_format},
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": model,
            },
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise VoiceGenerationError(
                f"ElevenLabs TTS failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        if not response.content:
            raise VoiceGenerationError("ElevenLabs TTS returned empty audio")
        with open(output_path, "wb") as fh:
            fh.write(response.content)
        return dict(response.headers)

    def _estimated_cost(self, character_count: int) -> Optional[float]:
        try:
            per_1k = float(getattr(self.config, "WORKFLOW_ELEVENLABS_USD_PER_1K_CHARS", 0) or 0)
        except (TypeError, ValueError):
            return None
        if per_1k <= 0:
            return None
        return round(character_count * per_1k / 1000, 6)
