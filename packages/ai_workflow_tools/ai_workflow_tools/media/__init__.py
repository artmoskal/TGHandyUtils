"""Media generation pack (L2): image + voice generation behind lazy provider imports.

Moved from the engine core 2026-06-13 (register item closed): L0 stays orchestration-only, and
the once-planned "re-export shims in the engine" were rejected as an L0<-L2 inversion — consumers
import from here, shim-free. Provider SDKs (openai, requests) load lazily; install the
``[media]`` extra to actually generate.
"""

from ai_workflow_tools.media.capabilities import (
    MediaGenerationCapability,
    build_image_generation_capability,
    build_voice_generation_capability,
)
from ai_workflow_tools.media.image_generation import OpenAIImageGenerator, create_image_generator
from ai_workflow_tools.media.image_models import GeneratedImage, ImageGenerationRequest
from ai_workflow_tools.media.voice_generation import (
    GeneratedVoiceAudio,
    VoiceGenerationRequest,
    create_voice_generator,
)

__all__ = [
    "GeneratedImage",
    "MediaGenerationCapability",
    "build_image_generation_capability",
    "build_voice_generation_capability",
    "GeneratedVoiceAudio",
    "ImageGenerationRequest",
    "OpenAIImageGenerator",
    "VoiceGenerationRequest",
    "create_image_generator",
    "create_voice_generator",
]
