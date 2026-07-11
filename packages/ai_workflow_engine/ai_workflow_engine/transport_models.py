"""Transport-model leaf: dependency-free DTOs shared by protocol and worker layers.

`ImageInput` lives BELOW `llm_protocol` and every LLM worker (F1.2): the protocol layer must
not import upward through `vision` (which pulls the LangChain-backed structured node). Nothing
in this module may import from `llm_protocol`, `vision`, or `engine.*`.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Callable, Dict, Literal, Optional

from pydantic import BaseModel, Field

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
