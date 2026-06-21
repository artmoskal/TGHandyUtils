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

from typing import Any

from ai_workflow_engine.engine.capabilities import TraceSink
from ai_workflow_engine.llm_protocol import ChatMessage, LLMRequest, LLMResponse
from ai_workflow_engine.models import WorkflowTraceEvent


class PromptCapturingLLMClient:
    """Wrap any ``LLMCallable`` to record each call's rendered prompt into a trace sink.

    Drop-in: it *is* an ``LLMCallable`` (``async __call__(request) -> LLMResponse``); it records a
    ``WorkflowTraceEvent`` (``decision="llm:prompt"`` by default) then delegates to the inner client
    unchanged. The event's ``metadata`` carries the system/user text and the full message list with
    image **fingerprints only** — never raw image data. The node label and workflow ids are read from
    ``request.metadata`` (the agent/LLM nodes populate ``agent_node`` / ``workflow_id``)::

        client = PromptCapturingLLMClient(real_client, engine.runtime.trace_sink)
        engine.register_capability("ask", build_llm_agent_capability(client, ...))

    The recorded events land in the SAME sink as the rest of the run, so ``format_trace_events`` and
    any viz over the trace see prompts inline with transitions, decisions, and usage.
    """

    def __init__(self, inner: Any, trace_sink: TraceSink, *, decision: str = "llm:prompt") -> None:
        self._inner = inner
        self._trace_sink = trace_sink
        self._decision = decision

    async def __call__(self, request: LLMRequest) -> LLMResponse:
        self._trace_sink.record(self._event(request))
        return await self._inner(request)

    def _event(self, request: LLMRequest) -> WorkflowTraceEvent:
        meta = dict(request.metadata or {})
        node = str(meta.get("agent_node") or meta.get("node") or "llm")
        attempt = int(meta.get("repair_round", 0) or 0) + 1
        return WorkflowTraceEvent(
            node=node,
            attempt=attempt,
            decision=self._decision,
            metadata={
                "workflow_id": meta.get("workflow_id"),
                "workflow_type": meta.get("workflow_type"),
                "system": request.system,
                "user": request.user,
                "messages": [_redact_message(message) for message in request.messages],
                "request_image_fingerprints": [image.fingerprint() for image in request.images],
            },
        )


def _redact_message(message: ChatMessage) -> dict:
    """Project a ``ChatMessage`` to a byte-free dict: role + text + image fingerprints (no raw data)."""

    return {
        "role": message.role,
        "content": message.content,
        "image_fingerprints": [image.fingerprint() for image in message.images],
    }
