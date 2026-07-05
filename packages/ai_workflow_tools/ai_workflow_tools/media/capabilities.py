"""Thin engine-capability wrappers for the media generators (G-T1).

The generators themselves stay plain provider adapters; these wrappers make them
registerable + described like every other tool: a ``CapabilitySpec`` with a real
description, dict→request coercion, and the generated file surfaced as a
``WorkflowArtifact`` so bundles archive it. Provider errors propagate — the engine
records a loud failure; nothing downgrades silently.
"""

from __future__ import annotations

from typing import Any, Callable

from ai_workflow_engine.models import CapabilityContext, CapabilityResult, CapabilitySpec, WorkflowArtifact


class MediaGenerationCapability:
    """One bounded ``generator.generate(request)`` call as an engine capability."""

    def __init__(
        self,
        generator: Any,
        *,
        name: str,
        description: str,
        request_factory: Callable[[dict[str, Any]], Any],
        metered: bool = True,
        side_effects: tuple[str, ...] = ("external_call",),
    ) -> None:
        self.generator = generator
        self.request_factory = request_factory
        self.spec = CapabilitySpec(
            name=name,
            kind="tool",
            description=description,
            side_effects=list(side_effects),
            metered=metered,
        )

    async def __call__(self, _context: CapabilityContext, request: Any) -> CapabilityResult:
        if isinstance(request, dict):
            request = self.request_factory(request)
        generated = await self.generator.generate(request)
        path = getattr(generated, "path", None)
        artifacts = (
            [
                WorkflowArtifact(
                    path=str(path),
                    kind="media",
                    source=getattr(generated, "provider", "") or self.spec.name,
                    owner_node=self.spec.name,
                    metadata={"basename": getattr(generated, "basename", "")},
                )
            ]
            if path
            else []
        )
        return CapabilityResult(status="accepted", output=generated, artifacts=artifacts)


def build_image_generation_capability(
    generator: Any = None,
    *,
    config: Any = None,
    name: str = "image_generation",
    metered: bool = True,
) -> MediaGenerationCapability:
    """Image generation as a described capability. Pass ``generator`` directly, or
    ``config`` to build the configured provider via ``create_image_generator``."""

    from ai_workflow_tools.media.image_generation import create_image_generator
    from ai_workflow_tools.media.image_models import ImageGenerationRequest

    if generator is None:
        if config is None:
            raise ValueError("build_image_generation_capability needs generator= or config=")
        generator = create_image_generator(config)
    return MediaGenerationCapability(
        generator,
        name=name,
        description=(
            "Generate one image artifact from a prompt (+ optional reference images) via the "
            "configured provider (OpenAI / Gemini / ChatGPT-browser / comparison)."
        ),
        request_factory=lambda payload: ImageGenerationRequest.model_validate(payload),
        metered=metered,
    )


def build_voice_generation_capability(
    generator: Any = None,
    *,
    config: Any = None,
    name: str = "voice_generation",
    metered: bool = True,
) -> MediaGenerationCapability:
    """Voice (TTS) generation as a described capability. Pass ``generator`` directly, or
    ``config`` to build the configured provider via ``create_voice_generator``."""

    from ai_workflow_tools.media.voice_generation import VoiceGenerationRequest, create_voice_generator

    if generator is None:
        if config is None:
            raise ValueError("build_voice_generation_capability needs generator= or config=")
        generator = create_voice_generator(config)
    return MediaGenerationCapability(
        generator,
        name=name,
        description=(
            "Synthesize one speech-audio artifact from text via the configured TTS provider "
            "(ElevenLabs today); voice identity comes from config, not the graph."
        ),
        request_factory=lambda payload: VoiceGenerationRequest(**payload),
        metered=metered,
    )


__all__ = [
    "MediaGenerationCapability",
    "build_image_generation_capability",
    "build_voice_generation_capability",
]
