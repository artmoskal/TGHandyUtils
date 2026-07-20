"""One owner for capability observation projection (v0.11 iteration 1, Phase I1.1).

``CapabilityRuntime.invoke`` used to construct every start/terminal trace event and its linked
payload/result/error/artifact details inline, so one new observation invariant had to be
remembered across success, rejection, timeout, containment-failure, and output-failure surfaces —
the exact sibling-surface fragility this refactor removes.

This projector is that single owner. It receives ALREADY-DECIDED facts (name, attempt, spec,
the terminal ``CapabilityResult`` or the error/decision, elapsed, and control-derived metadata)
and only serializes/links/records them. **It makes no control decision** — it never chooses a
status, branch, timeout, or return value. Dependency-light on purpose: it imports the trace/detail
models and the observation capture, nothing from the runtime facade, budget, executor, process
control, nodes, bundle writer, or viewer.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from ai_workflow_engine.models import (
    CapabilityResult,
    CapabilitySpec,
    WorkflowTraceEvent,
)


class CapabilityObservationProjector:
    """Projects one capability invocation's observation facts to trace + details.

    ``trace_record`` records a ``WorkflowTraceEvent``; ``observation`` is the ``ObservationCapture``
    that owns detail recording (and honors the capture mode). Both are supplied by the runtime.
    """

    def __init__(
        self,
        trace_record_provider: Callable[[], Callable[[WorkflowTraceEvent], None]],
        observation_provider: Callable[[], Any],
    ) -> None:
        # Resolve the runtime's CURRENT collaborators at EACH record, matching v0.10.1's call-time
        # ``self.trace_sink.record(...)`` / ``self.observation.record_detail(...)``. A consumer may
        # replace ``runtime.trace_sink`` or ``runtime.observation`` AFTER construction (the shipped
        # GoPro pilot tees the trace sink this way), and those replacements must receive this
        # invocation's events/details. Capturing the bound method or the instance here would freeze
        # the originals — a silent, supported-behavior observation regression.
        self._trace_record_provider = trace_record_provider
        self._observation_provider = observation_provider

    def _record(self, event: WorkflowTraceEvent) -> None:
        self._trace_record_provider()(event)

    @property
    def _observation(self) -> Any:
        return self._observation_provider()

    # ---------------------------------------------------------------- terminal projections

    def start(self, *, name: str, attempt: int, spec: CapabilitySpec, payload: Any) -> None:
        invocation_id = _provider_invocation_id(payload)
        start_event = WorkflowTraceEvent(
            node=name,
            attempt=attempt,
            decision="start",
            phase="tool:request",
            invocation_id=invocation_id,
        )
        detail_refs = self._tool_payload(
            spec,
            payload,
            event_id=start_event.event_id,
            invocation_id=invocation_id,
        )
        start_event = start_event.model_copy(
            update={
                "detail_refs": detail_refs,
                "detail_capture": self._detail_capture(detail_refs),
            }
        )
        self._record(start_event)

    def rejected(
        self, *, name: str, attempt: int, spec: CapabilitySpec, result: CapabilityResult,
        error: str, elapsed_ms: int,
    ) -> None:
        event_id = str(uuid.uuid4())
        invocation_id = _provider_invocation_id(result)
        detail_refs = self._tool_result(
            spec,
            result,
            error=error,
            event_id=event_id,
            invocation_id=invocation_id,
        )
        self._record(
            WorkflowTraceEvent(
                node=name,
                attempt=attempt,
                decision="rejected",
                error=error,
                elapsed_ms=elapsed_ms,
                metadata=result.metadata,
                phase="tool:result",
                severity="error",
                event_id=event_id,
                detail_refs=detail_refs,
                invocation_id=invocation_id,
                detail_capture=self._detail_capture(detail_refs),
            )
        )

    def terminal(
        self, *, name: str, attempt: int, spec: CapabilitySpec, output: CapabilityResult,
        elapsed_ms: int, metadata: dict[str, Any],
    ) -> None:
        event_id = str(uuid.uuid4())
        invocation_id = _provider_invocation_id(output, metadata)
        detail_refs = [
            *self._tool_result(
                spec,
                output,
                error=output.error,
                event_id=event_id,
                invocation_id=invocation_id,
            ),
            *self._artifact_previews(
                spec,
                output,
                event_id=event_id,
                invocation_id=invocation_id,
            ),
        ]
        self._record(
            WorkflowTraceEvent(
                node=name,
                attempt=attempt,
                decision=output.status,
                artifacts=[artifact.artifact_id for artifact in output.artifacts],
                elapsed_ms=elapsed_ms,
                phase="tool:result",
                severity="error" if output.status in {"failed", "rejected"} or output.error else "info",
                event_id=event_id,
                metadata=metadata,
                detail_refs=detail_refs,
                invocation_id=invocation_id,
                detail_capture=self._detail_capture(detail_refs),
            )
        )

    def timeout(
        self, *, name: str, attempt: int, spec: CapabilitySpec, error: str,
        elapsed_ms: int, metadata: dict[str, Any],
    ) -> None:
        event_id = str(uuid.uuid4())
        invocation_id = _provider_invocation_id(metadata)
        detail_refs = self._tool_error(
            spec,
            decision="partial",
            error=error,
            event_id=event_id,
            invocation_id=invocation_id,
        )
        self._record(
            WorkflowTraceEvent(
                node=name,
                attempt=attempt,
                decision="partial",
                error=error,
                elapsed_ms=elapsed_ms,
                phase="tool:result",
                severity="error",
                event_id=event_id,
                metadata=metadata,
                detail_refs=detail_refs,
                invocation_id=invocation_id,
                detail_capture=self._detail_capture(detail_refs),
            )
        )

    def failed(
        self, *, name: str, attempt: int, spec: CapabilitySpec, error: str, decision: str,
        elapsed_ms: int, metadata: dict[str, Any],
    ) -> None:
        event_id = str(uuid.uuid4())
        invocation_id = _provider_invocation_id(metadata)
        detail_refs = self._tool_error(
            spec,
            decision=decision,
            error=error,
            event_id=event_id,
            invocation_id=invocation_id,
        )
        self._record(
            WorkflowTraceEvent(
                node=name,
                attempt=attempt,
                decision=decision,
                error=error,
                elapsed_ms=elapsed_ms,
                phase="tool:result",
                severity="error",
                event_id=event_id,
                metadata=metadata,
                detail_refs=detail_refs,
                invocation_id=invocation_id,
                detail_capture=self._detail_capture(detail_refs),
            )
        )

    # ---------------------------------------------------------------- detail linkage (moved verbatim)

    def _tool_payload(
        self,
        spec: CapabilitySpec,
        payload: Any,
        *,
        event_id: str | None = None,
        invocation_id: str | None = None,
    ) -> list[str]:
        event_id = event_id or str(uuid.uuid4())
        detail = self._observation.record_detail(
            event_id=event_id,
            kind="tool_payload",
            payload={
                "capability": spec.name,
                "kind": spec.kind,
                "payload": payload,
            },
            invocation_id=invocation_id,
        )
        return [detail.detail_id] if detail else []

    def _tool_result(
        self, spec: CapabilitySpec, output: CapabilityResult, *, error: str | None = None,
        event_id: str | None = None, invocation_id: str | None = None,
    ) -> list[str]:
        event_id = event_id or str(uuid.uuid4())
        artifact_refs = [
            {
                "artifact_id": artifact.artifact_id,
                "kind": artifact.kind,
                "owner_node": artifact.owner_node or spec.name,
                "source": artifact.source,
            }
            for artifact in output.artifacts
        ]
        detail = self._observation.record_detail(
            event_id=event_id,
            kind="tool_result",
            payload={
                "capability": spec.name,
                "kind": spec.kind,
                "status": output.status,
                "output": output.output,
                "error": output.error,
                "artifact_refs": artifact_refs,
                "metadata": output.metadata,
            },
            invocation_id=invocation_id,
        )
        return [detail.detail_id] if detail else []

    def _tool_error(
        self,
        spec: CapabilitySpec,
        *,
        decision: str,
        error: str,
        event_id: str | None = None,
        invocation_id: str | None = None,
    ) -> list[str]:
        event_id = event_id or str(uuid.uuid4())
        detail = self._observation.record_detail(
            event_id=event_id,
            kind="tool_result",
            payload={
                "capability": spec.name,
                "kind": spec.kind,
                "status": decision,
                "error": error,
            },
            invocation_id=invocation_id,
        )
        return [detail.detail_id] if detail else []

    def _artifact_previews(
        self, spec: CapabilitySpec, output: CapabilityResult, *, event_id: str | None = None,
        invocation_id: str | None = None,
    ) -> list[str]:
        event_id = event_id or str(uuid.uuid4())
        detail_refs: list[str] = []
        for artifact in output.artifacts:
            detail = self._observation.record_detail(
                event_id=event_id,
                kind="artifact_preview",
                payload={
                    "capability": spec.name,
                    "artifact": artifact,
                },
                invocation_id=invocation_id,
            )
            if detail:
                detail_refs.append(detail.detail_id)
        return detail_refs

    def _detail_capture(self, detail_refs: list[str]) -> str:
        if detail_refs:
            return "captured"
        return getattr(self._observation, "unavailable_reason", None) or "projection_failed"


def _provider_invocation_id(*values: Any) -> str | None:
    """Read one provider id through nested capability/result shapes."""

    for value in values:
        if value is None:
            continue
        direct = getattr(value, "invocation_id", None)
        if direct:
            return str(direct)
        if isinstance(value, dict):
            direct = value.get("invocation_id")
            if direct:
                return str(direct)
        nested = getattr(value, "output", None)
        if nested is not None and nested is not value:
            found = _provider_invocation_id(nested)
            if found:
                return found
        metadata = getattr(value, "metadata", None)
        if isinstance(metadata, dict) and metadata.get("invocation_id"):
            return str(metadata["invocation_id"])
    return None
