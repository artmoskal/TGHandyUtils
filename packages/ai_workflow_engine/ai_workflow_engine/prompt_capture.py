"""Opt-in runtime prompt observability.

The engine renders prompts inside its LLM nodes but, by design, keeps the rendered prompt text out
of the trace (the trace carries decisions + usage, not message bodies). When you want to *see the
exact prompt sent* — debugging a weak model, auditing what an agent was told, or building a runtime
view — wrap your LLM client with :class:`PromptCapturingLLMClient`. It records each call's rendered
prompt into the same trace sink as the rest of the workflow, **byte-free**: images are reduced to
fingerprints, so raw media never enters the trace.

This is the runtime counterpart to :func:`ai_workflow_engine.viz.render_prompt_manifest` (the static
"what prompts will this workflow use" view). Because the engine's LLM seam is the ``LLMCallable``
protocol, decorating it is the idiomatic, additive way to add this — no engine-core change, and it
composes with any client (LangChain-backed, plain-callable, console).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ai_workflow_engine.engine.capabilities import DetailSink, TraceSink
from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest, LLMResponse
from ai_workflow_engine.models import ObservationDetail, PrivacyLevel, WorkflowTraceEvent


class PromptCapturingLLMClient:
    """Wrap any ``LLMCallable`` to record each call's rendered prompt into a trace sink.

    Drop-in: it *is* an ``LLMCallable`` (``async __call__(request) -> LLMResponse``); it records a
    ``WorkflowTraceEvent`` (``decision="llm:prompt"`` by default) plus a linked
    ``ObservationDetail`` then delegates to the inner client unchanged. The trace event carries only
    a digest/counts; the detail sink carries the optional prompt body with image **fingerprints only**
    — never raw image data. The node label and workflow ids are read from ``request.metadata`` (the
    agent/LLM nodes populate ``agent_node`` / ``workflow_id``)::

        client = PromptCapturingLLMClient(
            real_client,
            engine.runtime.trace_sink,
            detail_sink=engine.detail_sink,
        )
        engine.register_capability("ask", build_llm_agent_capability(client, ...))

    The trace event and detail record must land in shared run sinks so graph drill-down can resolve
    ``detail_refs``. A private detail sink is intentionally not created.
    """

    def __init__(
        self,
        inner: Any,
        trace_sink: TraceSink,
        *,
        detail_sink: DetailSink,
        decision: str = "llm:prompt",
        capture_text: bool = False,
        privacy: PrivacyLevel = "internal",
    ) -> None:
        self._inner = inner
        self._trace_sink = trace_sink
        self.detail_sink = detail_sink
        self._decision = decision
        self._capture_text = capture_text
        self._privacy = privacy

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        event, detail = self._event_and_detail(request)
        self.detail_sink.record(detail)
        self._trace_sink.record(event)
        return await self._inner(request)

    def _event_and_detail(self, request: LLMRequest) -> tuple[WorkflowTraceEvent, ObservationDetail]:
        meta = dict(request.metadata or {})
        node = str(meta.get("agent_node") or meta.get("node") or "llm")
        attempt = int(meta.get("repair_round", 0) or 0) + 1
        prompt_payload = _prompt_payload(request)
        prompt_digest = _digest(prompt_payload)
        event = WorkflowTraceEvent(
            node=node,
            attempt=attempt,
            decision=self._decision,
            phase="llm:request",
            metadata={
                "workflow_id": meta.get("workflow_id"),
                "workflow_type": meta.get("workflow_type"),
                "prompt_digest": prompt_digest,
                "message_count": len(request.messages),
                "request_image_count": len(request.images),
            },
        )
        detail = ObservationDetail(
            event_id=event.event_id,
            kind="rendered_prompt",
            privacy=self._privacy,
            redaction_state="none" if self._capture_text else "digest_only",
            content_type="text/plain" if self._capture_text else "application/json",
            text=_render_prompt_text(prompt_payload) if self._capture_text else None,
            json_value=prompt_payload if self._capture_text else None,
            digest=prompt_digest,
        )
        event = event.model_copy(update={"detail_refs": [detail.detail_id]})
        return event, detail


def _prompt_payload(request: LLMRequest) -> dict:
    return {
        "system": request.system,
        "user": request.user,
        "messages": [_redact_message(message) for message in request.messages],
        "request_image_fingerprints": [image.fingerprint() for image in request.images],
    }


def _digest(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _render_prompt_text(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=True)


def _redact_message(message: ChatMessage) -> dict:
    """Project a ``ChatMessage`` to a byte-free dict: role + text + image fingerprints (no raw data)."""

    return {
        "role": message.role,
        "content": message.content,
        "image_fingerprints": [image.fingerprint() for image in message.images],
    }
