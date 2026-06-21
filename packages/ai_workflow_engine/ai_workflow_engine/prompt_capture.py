"""Opt-in runtime LLM observability for external/BYO clients.

Engine-owned workers such as ``StructuredLLMNode`` and ``LLMAgentPlanner`` emit prompt/response
observations themselves. Use :class:`PromptCapturingLLMClient` only for LLM calls that happen outside
those workers, for example inside a custom capability that owns its own client. It records each call's
rendered prompt and response into the same trace sink as the rest of the workflow, **byte-free**:
images are reduced to fingerprints, so raw media never enters the trace.

This is the runtime counterpart to :func:`ai_workflow_engine.viz.render_prompt_manifest` (the static
"what prompts will this workflow use" view). Because the engine's LLM seam is the ``LLMCallable``
protocol, decorating external clients is the idiomatic, additive way to add this. Do not wrap a client
that is passed into an engine-owned LLM worker; those requests are marked and the wrapper skips them to
avoid duplicate prompt/response events.
"""

from __future__ import annotations

from typing import Any

from ai_workflow_engine.engine.capabilities import DetailSink, TraceSink
from ai_workflow_engine.llm_protocol import LLMRequest, LLMResponse
from ai_workflow_engine.models import PrivacyLevel
from ai_workflow_engine.observability_capture import (
    is_engine_worker_observed_request,
    llm_request_payload,
    llm_response_payload,
    record_observation,
)


class PromptCapturingLLMClient:
    """Wrap external ``LLMCallable`` calls to record prompt/response into a trace sink.

    Drop-in: it *is* an ``LLMCallable`` (``async __call__(request) -> LLMResponse``); it records
    request and response ``WorkflowTraceEvent`` records plus linked ``ObservationDetail`` records, then
    returns the inner response unchanged. The trace events carry only digests/counts; the detail sink
    carries optional prompt/response bodies with image **fingerprints only** — never raw image data. The
    node label and workflow ids are read from ``request.metadata``. Use this for custom/BYO LLM calls,
    not clients passed into engine-owned LLM workers such as ``StructuredLLMNode`` or
    ``LLMAgentPlanner``::

        engine = WorkflowEngineBuilder().with_detail_sink(InMemoryDetailSink()).build()
        client = PromptCapturingLLMClient(
            real_client,
            engine.runtime.trace_sink,
            detail_sink=engine.detail_sink,
        )

    The trace event and detail record must land in shared run sinks so graph drill-down can resolve
    ``detail_refs``. A private detail sink is intentionally not created. If an engine-owned worker has
    already marked the request as observed, this wrapper delegates without recording.
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
        if is_engine_worker_observed_request(request):
            return await self._inner(request)
        self._record_request(request)
        try:
            response = await self._inner(request)
        except Exception as exc:
            self._record_response_error(request, exc)
            raise
        self._record_response(request, response)
        return response

    def _record_request(self, request: LLMRequest) -> None:
        meta = dict(request.metadata or {})
        record_observation(
            self._trace_sink,
            self.detail_sink,
            node=_node(meta),
            attempt=_attempt(meta),
            decision=self._decision,
            phase="llm:request",
            kind="rendered_prompt",
            payload=llm_request_payload(request),
            metadata={
                "workflow_id": meta.get("workflow_id"),
                "workflow_type": meta.get("workflow_type"),
                "message_count": len(request.messages),
                "request_image_count": len(request.images),
            },
            capture_text=self._capture_text,
            privacy=self._privacy,
            digest_metadata_key="prompt_digest",
        )

    def _record_response(self, request: LLMRequest, response: LLMResponse) -> None:
        meta = dict(request.metadata or {})
        record_observation(
            self._trace_sink,
            self.detail_sink,
            node=_node(meta),
            attempt=_attempt(meta),
            decision="llm:response",
            phase="llm:response",
            kind="llm_response",
            payload=llm_response_payload(response),
            metadata={
                "workflow_id": meta.get("workflow_id"),
                "workflow_type": meta.get("workflow_type"),
                "model": response.model,
                "stop_reason": response.stop_reason,
                "tool_call_count": len(response.tool_calls),
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "total_tokens": response.total_tokens,
                "cost_class": response.cost_class,
            },
            capture_text=self._capture_text,
            privacy=self._privacy,
            digest_metadata_key="response_digest",
        )

    def _record_response_error(self, request: LLMRequest, exc: Exception) -> None:
        meta = dict(request.metadata or {})
        error = str(exc) or exc.__class__.__name__
        record_observation(
            self._trace_sink,
            self.detail_sink,
            node=_node(meta),
            attempt=_attempt(meta),
            decision="llm:response",
            phase="llm:response",
            kind="llm_response",
            payload={"error_type": exc.__class__.__name__, "error": error},
            severity="error",
            error=error,
            metadata={
                "workflow_id": meta.get("workflow_id"),
                "workflow_type": meta.get("workflow_type"),
                "error_type": exc.__class__.__name__,
            },
            capture_text=self._capture_text,
            privacy=self._privacy,
            digest_metadata_key="response_digest",
        )

def _node(meta: dict[str, Any]) -> str:
    return str(meta.get("agent_node") or meta.get("node") or "llm")


def _attempt(meta: dict[str, Any]) -> int:
    return int(meta.get("repair_round", 0) or 0) + 1
