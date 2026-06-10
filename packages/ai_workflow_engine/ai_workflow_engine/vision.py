"""Vision/multimodal structured LLM support.

``ImageInput`` is the per-call *transport payload* for images: it may carry base64 bytes for the
one LLM call, is never persisted, and only its :meth:`ImageInput.fingerprint` may appear in logs,
traces, or usage metadata. Persistent workflow state keeps byte-free ``EvidenceRef``s; the
:meth:`ImageInput.from_evidence` bridge loads bytes only at the call boundary (layering confirmed
with the GoPro consumer, 2026-06-10).

``StructuredVisionLLMNode`` extends ``StructuredLLMNode`` with image attachments: same Pydantic
parse, ``pre_parse`` cleaners, repair rounds (images are re-sent on repair — a text-only repair of
a vision answer is meaningless), metering, and budget integration. Provider-agnostic: messages use
the ``image_url`` data-URL content-part format accepted by OpenAI-style and Ollama-style chat
models alike; plain-callable backends receive the images on the ``LLMRequest`` instead.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from ai_workflow_engine.engine.llm_node import MessageFactory, StructuredLLMNode
from ai_workflow_engine.models import EvidenceRef


class ImageInput(BaseModel):
    """One image attached to one LLM call. Transport-only — never store in workflow state."""

    source: Literal["path", "base64", "url"]
    data: str = Field(repr=False)
    media_type: str = "image/jpeg"
    role: str = ""
    captured_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> Dict[str, Any]:
        """Trace-safe identity: hash/length/kind — never the payload."""

        return {
            "sha12": hashlib.sha256(self.data.encode("utf-8", errors="ignore")).hexdigest()[:12],
            "length": len(self.data),
            "source": self.source,
            "media_type": self.media_type,
            "role": self.role,
        }

    def __repr__(self) -> str:  # pragma: no cover - repr formatting
        return f"ImageInput({self.fingerprint()})"

    __str__ = __repr__

    @classmethod
    def from_path(cls, path: str, *, role: str = "", media_type: Optional[str] = None) -> "ImageInput":
        guessed = media_type or mimetypes.guess_type(path)[0] or "image/jpeg"
        return cls(source="path", data=path, media_type=guessed, role=role)

    @classmethod
    def from_evidence(
        cls,
        ref: EvidenceRef,
        loader: Callable[[EvidenceRef], bytes],
        *,
        media_type: Optional[str] = None,
    ) -> "ImageInput":
        """Bridge from a persistent byte-free EvidenceRef: bytes are loaded only here, at the
        call boundary, and exist only inside this transport object."""

        raw = loader(ref)
        return cls(
            source="base64",
            data=base64.b64encode(raw).decode("ascii"),
            media_type=media_type or ref.media_type or "image/jpeg",
            role=ref.role,
            metadata={"evidence_ref_id": ref.ref_id},
        )

    def as_content_part(self) -> Dict[str, Any]:
        """Render as a chat content part (``image_url`` data-URL form)."""

        if self.source == "url":
            return {"type": "image_url", "image_url": {"url": self.data}}
        if self.source == "base64":
            payload = self.data
        else:  # path — read at call time; missing file fails loudly
            raw = Path(self.data).read_bytes()
            payload = base64.b64encode(raw).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{self.media_type};base64,{payload}"}}


class StructuredVisionLLMNode(StructuredLLMNode):
    """Structured LLM node that attaches images to the (and every repair) call."""

    async def run(  # type: ignore[override]
        self,
        values: Dict[str, Any],
        *,
        images: Sequence[ImageInput] = (),
        content_hash_input: str = "",
        message_factory: Optional[MessageFactory] = None,
    ) -> Any:
        image_list: List[ImageInput] = list(images)

        def with_images(messages, attempt):
            if message_factory:
                messages = list(message_factory(messages, attempt))
            if not image_list:
                return messages
            return self._attach_images(messages, image_list)

        return await super().run(
            values,
            content_hash_input=content_hash_input,
            message_factory=with_images,
            usage_metadata={
                "input_images": len(image_list),
                "image_fingerprints": [image.fingerprint() for image in image_list],
            },
            # Plain-callable clients receive the images on the LLMRequest instead of message parts.
            images=image_list,
        )

    @staticmethod
    def _attach_images(messages, images: Sequence[ImageInput]):
        """Append image content parts to the last human message (works for repair messages too)."""

        from langchain_core.messages import HumanMessage

        if not messages:
            return messages
        updated = list(messages)
        last = updated[-1]
        text = last.content if isinstance(last.content, str) else last.content
        parts: List[Any]
        if isinstance(text, str):
            parts = [{"type": "text", "text": text}]
        else:
            parts = list(text)
        parts.extend(image.as_content_part() for image in images)
        updated[-1] = HumanMessage(content=parts)
        return updated
