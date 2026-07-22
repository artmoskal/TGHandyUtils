"""Product-owned identity, evidence, and artifact mapping for Anki media."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from ai_workflow_engine.models import WorkflowArtifact
from models.anki_workflow import GeneratedMedia


def continuity_name(*, user_id: int, style_version: str | None) -> str:
    style = str(style_version or "none")
    source = f"anki|v1|user:{user_id}|style:{style}"
    return "anki-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def image_idempotency_key(*, workflow_id: str, operation_slot: int) -> str:
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        raise ValueError("workflow_id is required for image operation idempotency")
    if type(operation_slot) is not int or operation_slot < 0:
        raise ValueError("operation_slot must be a non-negative integer")
    source = f"anki-image|v1|run:{workflow_id}|slot:{operation_slot}"
    return "anki-image-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def image_evidence_metadata(evidence: Any) -> dict[str, str]:
    if evidence is None:
        return {}
    values = evidence.model_dump(mode="json", exclude_none=True)
    freshness = values.pop("freshness", None)
    metadata = {
        f"generation_{key}": str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in values.items()
    }
    if freshness is not None:
        metadata["generation_freshness"] = json.dumps(freshness, sort_keys=True)
    return metadata


def artifacts_for_node(name: str, update: Mapping[str, Any]) -> list[WorkflowArtifact]:
    if name == "generate_image":
        media = list(update.get("generated_media", []))
    elif name == "generate_voice":
        media = list(update.get("voice_generated_media", []))
    else:
        media = []
    return media_artifacts(media)


def media_artifacts(media: Sequence[GeneratedMedia]) -> list[WorkflowArtifact]:
    return [
        WorkflowArtifact(
            path=item.path,
            kind="media",
            source=item.source,
            owner_node="generate_voice" if item.role == "audio" else "generate_image",
            cleanup_on_failure=item.source == "generated",
            metadata={"role": item.role, **item.metadata},
        )
        for item in media
    ]


__all__ = [
    "artifacts_for_node",
    "continuity_name",
    "image_evidence_metadata",
    "image_idempotency_key",
    "media_artifacts",
]
