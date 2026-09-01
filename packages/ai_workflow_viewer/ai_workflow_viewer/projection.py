"""Pure projection of persisted engine records into typed observation graph data."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from ai_workflow_engine.models import (
    ObservationDetail,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.observation_contract import ObservationDetailEnvelope
from ai_workflow_engine.workflow import WorkflowDefinition


class ObservationTimelineEntry(BaseModel):
    event_id: str
    timestamp: str = ""
    node: str
    attempt: int = 1
    phase: Optional[str] = None
    severity: str = "info"
    decision: Optional[str] = None
    error: Optional[str] = None
    elapsed_ms: Optional[int] = None
    artifacts: list[str] = Field(default_factory=list)
    detail_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservationNode(BaseModel):
    node_id: str
    kind: str = "external"
    status: str = "not_started"
    attempts: int = 0
    decisions: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    detail_refs: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0
    usage_event_ids: list[str] = Field(default_factory=list)
    total_tokens: int = 0
    metered_usd: Optional[float] = None
    notional_usd: Optional[float] = None
    outcome_accepted: int = 0
    outcome_partial: int = 0
    outcome_failed: int = 0
    outcome_suspended: int = 0


ObservationGraphDetail = ObservationDetail | ObservationDetailEnvelope


class ObservationGraph(BaseModel):
    workflow_id: str
    run_id: Optional[str] = None
    nodes: dict[str, ObservationNode] = Field(default_factory=dict)
    timeline: list[ObservationTimelineEntry] = Field(default_factory=list)
    details: dict[str, ObservationGraphDetail] = Field(default_factory=dict)
    usage_events: list[WorkflowUsageEvent] = Field(default_factory=list)


def build_observation_graph(
    definition: WorkflowDefinition,
    trace_events: Iterable[WorkflowTraceEvent],
    usage_events: Iterable[WorkflowUsageEvent] | WorkflowUsageSummary = (),
    details: Iterable[ObservationGraphDetail] = (),
    *,
    run_id: str | None = None,
) -> ObservationGraph:
    """Project runtime records into graph data without mutating engine state."""

    graph = ObservationGraph(
        workflow_id=definition.workflow_id,
        run_id=run_id,
        nodes={
            node.id: ObservationNode(node_id=node.id, kind=node.kind)
            for node in definition.nodes
        },
    )
    detail_by_id = {detail.detail_id: detail for detail in details}
    referenced_detail_ids: list[str] = []

    for event in trace_events:
        if run_id is not None and event.run_id != run_id:
            continue
        if graph.run_id is None and event.run_id:
            graph.run_id = event.run_id
        node = graph.nodes.setdefault(
            event.node,
            ObservationNode(node_id=event.node, kind="external"),
        )
        node.attempts = max(node.attempts, event.attempt or 1)
        outcome = _external_outcome(event)
        if outcome == "accepted":
            node.outcome_accepted += 1
        elif outcome == "partial":
            node.outcome_partial += 1
        elif outcome == "failed":
            node.outcome_failed += 1
        elif outcome == "suspended":
            node.outcome_suspended += 1
        if event.decision:
            node.decisions.append(event.decision)
        if event.error and event.error not in node.errors:
            node.errors.append(event.error)
        node.status = _next_status(node.status, event)
        node.elapsed_ms += int(event.elapsed_ms or 0)
        _extend_unique(node.artifacts, event.artifacts)
        _extend_unique(node.detail_refs, event.detail_refs)
        _extend_unique(referenced_detail_ids, event.detail_refs)
        graph.timeline.append(
            ObservationTimelineEntry(
                event_id=event.event_id,
                timestamp=event.timestamp,
                node=event.node,
                attempt=event.attempt,
                phase=event.phase or _infer_phase(event),
                severity=event.severity,
                decision=event.decision,
                error=event.error,
                elapsed_ms=event.elapsed_ms,
                artifacts=list(event.artifacts),
                detail_refs=list(event.detail_refs),
                metadata=dict(event.metadata or {}),
            )
        )

    for event in _usage_iter(usage_events):
        if run_id is not None:
            event_run_id = _usage_run_id(event)
            if event_run_id is not None and event_run_id != run_id:
                continue
        graph.usage_events.append(event)
        if graph.run_id is None:
            usage_run_id = _usage_run_id(event)
            graph.run_id = str(usage_run_id) if usage_run_id else None
        node = graph.nodes.setdefault(
            event.node,
            ObservationNode(node_id=event.node, kind="external"),
        )
        _extend_unique(node.usage_event_ids, [event.event_id])
        node.total_tokens += int(event.total_tokens or 0)
        _add_usage_cost(node, event)

    graph.details = {
        detail_id: detail
        for detail_id in referenced_detail_ids
        if (detail := detail_by_id.get(detail_id)) is not None
        and (run_id is None or detail.run_id in (None, run_id))
    }

    declared = {node.id for node in definition.nodes}
    for node in graph.nodes.values():
        if node.node_id in declared:
            continue
        if node.outcome_accepted and node.outcome_failed:
            node.status = "mixed"
        elif node.outcome_failed:
            node.status = "failed"
        elif node.outcome_suspended:
            node.status = "suspended"
        elif node.outcome_partial:
            node.status = "partial"
        elif node.outcome_accepted:
            node.status = "completed"
    return graph


def _usage_iter(
    usage_events: Iterable[WorkflowUsageEvent] | WorkflowUsageSummary,
) -> Iterable[WorkflowUsageEvent]:
    if isinstance(usage_events, WorkflowUsageSummary):
        return usage_events.events
    return usage_events


def _usage_run_id(event: WorkflowUsageEvent) -> str | None:
    if event.run_id:
        return str(event.run_id)
    if not event.metadata:
        return None
    run_id = event.metadata.get("run_id") or event.metadata.get("workflow_id")
    return str(run_id) if run_id else None


def _external_outcome(event: WorkflowTraceEvent) -> Optional[str]:
    terminal = getattr(event, "node_status", None)
    if terminal in {"accepted", "completed"}:
        return "accepted"
    if terminal in {"failed", "rejected"}:
        return "failed"
    if terminal == "partial":
        return "partial"
    if terminal == "requires_user_input":
        return "suspended"
    if terminal is not None:
        return None
    decision = event.decision or ""
    if decision == "accepted":
        return "accepted"
    if decision in {"failed", "rejected"}:
        return "failed"
    if decision == "partial":
        return "partial"
    if event.error or event.severity == "error":
        return "failed"
    return None


def _next_status(current: str, event: WorkflowTraceEvent) -> str:
    terminal = getattr(event, "node_status", None)
    if terminal is not None:
        if terminal in {"accepted", "completed"}:
            return "completed"
        if terminal in {"failed", "rejected"}:
            return "failed"
        if terminal == "partial":
            return "partial"
        if terminal == "requires_user_input":
            return "suspended"
        return current
    if event.error or event.severity == "error":
        return "failed"
    if event.decision:
        return "running" if current == "not_started" else current
    return current


def _infer_phase(event: WorkflowTraceEvent) -> Optional[str]:
    if event.error:
        return "error"
    if event.decision:
        return "node:decision"
    return None


def _usage_cost(event: WorkflowUsageEvent) -> Optional[float]:
    if event.cost_class == "metered" and event.estimated_usd is not None:
        return float(event.estimated_usd)
    if event.cost_class == "subscription_notional" and event.notional_usd is not None:
        return float(event.notional_usd)
    return None


def _add_usage_cost(node: ObservationNode, event: WorkflowUsageEvent) -> None:
    cost = _usage_cost(event)
    if cost is None:
        return
    if event.cost_class == "metered":
        node.metered_usd = round((node.metered_usd or 0.0) + cost, 6)
        return
    node.notional_usd = round((node.notional_usd or 0.0) + cost, 6)


def _extend_unique(target: list[str], values: Iterable[str]) -> None:
    seen = set(target)
    for value in values:
        if value not in seen:
            target.append(value)
            seen.add(value)


__all__ = [
    "ObservationGraph",
    "ObservationNode",
    "ObservationTimelineEntry",
    "build_observation_graph",
]
