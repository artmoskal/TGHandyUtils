"""Reusable image generation request/result models."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


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
    continuity_key: Optional[str] = Field(default=None, min_length=1, max_length=80)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=160)


class ImageFreshnessEvidence(BaseModel):
    """Closed privacy-safe proof that a reuse result belongs to this request turn."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    schema_version: Literal[4] = Field(alias="schema")
    strategy: Literal["resultFreshnessFenceV3"]
    source: Literal["captured", "restored"]
    state: Literal["result_observed"]
    anchor_owned: Literal[True]
    result_owned: Literal[True]
    result_scope_count: StrictInt = Field(ge=1, le=8)
    candidate_count: StrictInt = Field(ge=1, le=1000)
    reason: None


class ImageGenerationEvidence(BaseModel):
    """Provider-safe generation facts retained with the local artifact."""

    model_config = ConfigDict(extra="forbid")

    conversation_mode: Literal["reuse", "fresh"]
    reference_images_used: StrictInt = Field(ge=0)
    generation: Optional[StrictInt] = Field(default=None, ge=1)
    reused: Optional[StrictBool] = None
    rotated: Optional[StrictBool] = None
    freshness: Optional[ImageFreshnessEvidence] = None

    @model_validator(mode="after")
    def _seal_mode_shape(self):
        reuse_values = (self.generation, self.reused, self.rotated, self.freshness)
        if self.conversation_mode == "reuse" and any(value is None for value in reuse_values):
            raise ValueError("reuse evidence requires generation, reused, rotated, and freshness")
        if self.conversation_mode == "fresh" and any(value is not None for value in reuse_values):
            raise ValueError("fresh evidence cannot contain reuse-only fields")
        return self


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
    generation_evidence: Optional[ImageGenerationEvidence] = None
    estimated_usd: Optional[float] = None
    request_id: Optional[str] = None
