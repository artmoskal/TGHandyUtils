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
from ai_workflow_engine.budget import check_images_per_call


from ai_workflow_engine.transport_models import ImageInput  # noqa: F401 — public compat re-export (home moved to the transport leaf, F1.2)


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
        check_images_per_call(len(image_list), self.name)

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
        text = last.content
        parts: List[Any]
        if isinstance(text, str):
            parts = [{"type": "text", "text": text}]
        else:
            parts = list(text)
        parts.extend(image.as_content_part() for image in images)
        updated[-1] = HumanMessage(content=parts)
        return updated
