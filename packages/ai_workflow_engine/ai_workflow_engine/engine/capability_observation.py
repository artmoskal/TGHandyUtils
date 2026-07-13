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

    def __init__(self, trace_record: Callable[[WorkflowTraceEvent], None], observation: Any) -> None:
        self._record = trace_record
        self._observation = observation

    # ---------------------------------------------------------------- terminal projections

    def start(self, *, name: str, attempt: int, spec: CapabilitySpec, payload: Any) -> None:
        start_event = WorkflowTraceEvent(
            node=name,
            attempt=attempt,
            decision="start",
            phase="tool:request",
        )
        start_event = start_event.model_copy(
            update={"detail_refs": self._tool_payload(spec, payload, event_id=start_event.event_id)}
        )
        self._record(start_event)

    def rejected(
        self, *, name: str, attempt: int, spec: CapabilitySpec, result: CapabilityResult,
        error: str, elapsed_ms: int,
    ) -> None:
        event_id = str(uuid.uuid4())
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
                detail_refs=self._tool_result(spec, result, error=error, event_id=event_id),
            )
        )

    def terminal(
        self, *, name: str, attempt: int, spec: CapabilitySpec, output: CapabilityResult,
        elapsed_ms: int, metadata: dict[str, Any],
    ) -> None:
        event_id = str(uuid.uuid4())
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
                detail_refs=[
                    *self._tool_result(spec, output, error=output.error, event_id=event_id),
                    *self._artifact_previews(spec, output, event_id=event_id),
                ],
            )
        )

    def timeout(
        self, *, name: str, attempt: int, spec: CapabilitySpec, error: str,
        elapsed_ms: int, metadata: dict[str, Any],
    ) -> None:
        event_id = str(uuid.uuid4())
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
                detail_refs=self._tool_error(spec, decision="partial", error=error, event_id=event_id),
            )
        )

    def failed(
        self, *, name: str, attempt: int, spec: CapabilitySpec, error: str, decision: str,
        elapsed_ms: int, metadata: dict[str, Any],
    ) -> None:
        event_id = str(uuid.uuid4())
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
                detail_refs=self._tool_error(spec, decision=decision, error=error, event_id=event_id),
            )
        )

    # ---------------------------------------------------------------- detail linkage (moved verbatim)

    def _tool_payload(self, spec: CapabilitySpec, payload: Any, *, event_id: str | None = None) -> list[str]:
        event_id = event_id or str(uuid.uuid4())
        detail = self._observation.record_detail(
            event_id=event_id,
            kind="tool_payload",
            payload={
                "capability": spec.name,
                "kind": spec.kind,
                "payload": payload,
            },
        )
        return [detail.detail_id] if detail else []

    def _tool_result(
        self, spec: CapabilitySpec, output: CapabilityResult, *, error: str | None = None,
        event_id: str | None = None,
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
        )
        return [detail.detail_id] if detail else []

    def _tool_error(
        self, spec: CapabilitySpec, *, decision: str, error: str, event_id: str | None = None,
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
        )
        return [detail.detail_id] if detail else []

    def _artifact_previews(
        self, spec: CapabilitySpec, output: CapabilityResult, *, event_id: str | None = None,
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
            )
            if detail:
                detail_refs.append(detail.detail_id)
        return detail_refs
