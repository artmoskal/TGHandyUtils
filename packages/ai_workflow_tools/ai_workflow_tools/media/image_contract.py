"""Strict, provider-neutral validation for generated image response envelopes."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

from ai_workflow_tools.media.image_models import (
    ImageGenerationEvidence,
    ImageGenerationRequest,
)


_MIME_BY_FORMAT = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}
_DATA_URL_RE = re.compile(r"^data:([^;,]+);base64,(.+)$")


class ImageEnvelopeError(ValueError):
    """The provider response does not prove a valid requested image artifact."""


def validate_browser_image_response(
    data: Any,
    *,
    request: ImageGenerationRequest,
    conversation_mode: str,
    expected_reference_count: int,
) -> tuple[bytes, ImageGenerationEvidence]:
    """Validate bytes and return only the privacy-safe response evidence."""

    response = _completed_response(data, conversation_mode)
    image_bytes = _decode_image(response, request.output_format)
    reference_count = _reference_count(response, expected_reference_count)
    evidence_values = _evidence_values(
        response,
        request=request,
        conversation_mode=conversation_mode,
        reference_count=reference_count,
    )
    try:
        evidence = ImageGenerationEvidence.model_validate(evidence_values)
    except Exception as exc:
        raise ImageEnvelopeError(f"image freshness/evidence is invalid: {exc}") from exc
    return image_bytes, evidence


def _completed_response(data: Any, conversation_mode: str) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("status") != "completed":
        status = data.get("status") if isinstance(data, dict) else None
        raise ImageEnvelopeError(f"image response is not completed (status={status!r})")
    actual_mode = data.get("conversation_mode")
    if actual_mode != conversation_mode:
        raise ImageEnvelopeError(
            f"conversation mode mismatch: requested {conversation_mode!r}, got {actual_mode!r}"
        )
    return data


def _decode_image(data: dict[str, Any], output_format: str) -> bytes:
    data_url = data.get("image_data_url")
    if not isinstance(data_url, str):
        raise ImageEnvelopeError("image response is missing a data URL")
    match = _DATA_URL_RE.fullmatch(data_url)
    if match is None:
        raise ImageEnvelopeError("image data URL must contain strict base64 media data")
    data_url_mime, encoded = match.groups()

    expected_mime = _MIME_BY_FORMAT[output_format]
    declared_mime = data.get("mime")
    if declared_mime != expected_mime or data_url_mime != expected_mime:
        raise ImageEnvelopeError(
            "image MIME/format mismatch: "
            f"requested={expected_mime!r}, declared={declared_mime!r}, data_url={data_url_mime!r}"
        )

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageEnvelopeError("image data URL contains invalid base64") from exc
    _validate_magic(image_bytes, output_format)

    declared_bytes = data.get("bytes")
    if type(declared_bytes) is not int or declared_bytes < 1:
        raise ImageEnvelopeError("image byte count must be a positive integer")
    if declared_bytes != len(image_bytes):
        raise ImageEnvelopeError(
            f"image byte count mismatch: declared {declared_bytes}, decoded {len(image_bytes)}"
        )
    return image_bytes


def _reference_count(data: dict[str, Any], expected: int) -> int:
    reference_count = data.get("reference_images_used")
    if type(reference_count) is not int or reference_count != expected:
        raise ImageEnvelopeError(
            "reference image count mismatch: "
            f"expected {expected}, provider reported {reference_count!r}"
        )
    return reference_count


def _evidence_values(
    data: dict[str, Any],
    *,
    request: ImageGenerationRequest,
    conversation_mode: str,
    reference_count: int,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "conversation_mode": conversation_mode,
        "reference_images_used": reference_count,
    }
    if conversation_mode == "reuse":
        if data.get("conversation_name") != request.continuity_key:
            raise ImageEnvelopeError("reuse response conversation identity does not match the request")
        values.update(
            generation=data.get("generation"),
            reused=data.get("reused"),
            rotated=data.get("rotated"),
            freshness=data.get("freshness"),
        )
    else:
        forbidden = ("generation", "reused", "rotated", "freshness", "conversation_name")
        if any(name in data for name in forbidden):
            raise ImageEnvelopeError("fresh response contains reuse-only evidence")
    return values


def safe_generation_metadata(evidence: ImageGenerationEvidence) -> dict[str, Any]:
    """Serialize the closed evidence DTO; provider URLs and identities cannot enter it."""

    return evidence.model_dump(mode="json", exclude_none=True)


def _validate_magic(data: bytes, output_format: str) -> None:
    if output_format == "png":
        valid = len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR"
    elif output_format == "jpeg":
        valid = len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    else:
        valid = (
            len(data) >= 16
            and data.startswith(b"RIFF")
            and data[8:12] == b"WEBP"
            and int.from_bytes(data[4:8], "little") + 8 == len(data)
        )
    if not valid:
        raise ImageEnvelopeError(f"image bytes do not have a valid {output_format} signature")


__all__ = [
    "ImageEnvelopeError",
    "safe_generation_metadata",
    "validate_browser_image_response",
]
