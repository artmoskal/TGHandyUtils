"""Reusable image generation request/result models."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ImageGenerationRequest(BaseModel):
    """A provider-neutral request for one generated image artifact."""

    prompt: str
    output_dir: str
    output_basename: Optional[str] = None
    model: str = "gpt-image-2"
    size: str = "auto"
    quality: str = "auto"
    output_format: Literal["png", "jpeg", "webp"] = "png"
    reference_image_paths: List[str] = Field(default_factory=list)
    style_reference_version: Optional[str] = None
    workflow_id: Optional[str] = None


class GeneratedImage(BaseModel):
    """A generated image artifact stored on local disk."""

    path: str
    basename: str
    provider: str = ""
    model: str
    size: str
    quality: str
    output_format: Literal["png", "jpeg", "webp"]
    reference_image_count: int = 0
    style_reference_version: Optional[str] = None
    usage_metadata: Dict[str, Any] = Field(default_factory=dict)
    estimated_usd: Optional[float] = None
    request_id: Optional[str] = None
