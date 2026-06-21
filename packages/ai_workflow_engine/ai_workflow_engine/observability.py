"""Runtime observability projection and static renderers."""

from __future__ import annotations

import html
from pathlib import Path
import json
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, Field

from ai_workflow_engine.models import (
    ObservationDetail,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.workflow import WorkflowDefinition


class ObservationTimelineEntry(BaseModel):
    event_id: str
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


class ObservationGraph(BaseModel):
    workflow_id: str
    run_id: Optional[str] = None
    nodes: dict[str, ObservationNode] = Field(default_factory=dict)
    timeline: list[ObservationTimelineEntry] = Field(default_factory=list)
    details: dict[str, ObservationDetail] = Field(default_factory=dict)
    usage_events: list[WorkflowUsageEvent] = Field(default_factory=list)


def build_observation_graph(
    definition: WorkflowDefinition,
    trace_events: Iterable[WorkflowTraceEvent],
    usage_events: Iterable[WorkflowUsageEvent] | WorkflowUsageSummary = (),
    details: Iterable[ObservationDetail] = (),
) -> ObservationGraph:
    """Project runtime records into a graph view without mutating engine state."""

    graph = ObservationGraph(
        workflow_id=definition.workflow_id,
        nodes={
            node.id: ObservationNode(node_id=node.id, kind=node.kind)
            for node in definition.nodes
        },
    )
    detail_by_id = {detail.detail_id: detail for detail in details}
    graph.details = detail_by_id

    for event in trace_events:
        if graph.run_id is None and event.run_id:
            graph.run_id = event.run_id
        node = graph.nodes.setdefault(
            event.node,
            ObservationNode(node_id=event.node, kind="external"),
        )
        node.attempts = max(node.attempts, event.attempt or 1)
        if event.decision:
            node.decisions.append(event.decision)
        if event.error:
            node.errors.append(event.error)
        node.status = _next_status(node.status, event)
        node.elapsed_ms += int(event.elapsed_ms or 0)
        _extend_unique(node.artifacts, event.artifacts)
        _extend_unique(node.detail_refs, event.detail_refs)
        graph.timeline.append(
            ObservationTimelineEntry(
                event_id=event.event_id,
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
        graph.usage_events.append(event)
        if graph.run_id is None:
            run_id = event.metadata.get("run_id") if event.metadata else None
            if not run_id:
                run_id = event.metadata.get("workflow_id") if event.metadata else None
            graph.run_id = str(run_id) if run_id else None
        node = graph.nodes.setdefault(
            event.node,
            ObservationNode(node_id=event.node, kind="external"),
        )
        _extend_unique(node.usage_event_ids, [event.event_id])
        node.total_tokens += int(event.total_tokens or 0)
        _add_usage_cost(node, event)

    return graph


def render_runtime_timeline(graph: ObservationGraph) -> str:
    """Render a deterministic text timeline suitable for logs or chat handoff."""

    lines = [f"Observation timeline: {graph.workflow_id}"]
    if graph.run_id:
        lines.append(f"run_id: {graph.run_id}")
    for index, item in enumerate(graph.timeline, start=1):
        head = f"{index}. {item.node}"
        if item.phase:
            head += f" [{item.phase}]"
        if item.decision:
            head += f" -> {item.decision}"
        if item.attempt > 1:
            head += f" (attempt {item.attempt})"
        if item.elapsed_ms:
            head += f" {item.elapsed_ms}ms"
        if item.severity != "info":
            head += f" {item.severity}"
        lines.append(head)
        if item.error:
            lines.append(f"   error: {item.error}")
        if item.detail_refs:
            lines.append(f"   details: {', '.join(item.detail_refs)}")
    return "\n".join(lines)


def observation_graph_to_html(
    definition: WorkflowDefinition,
    graph: ObservationGraph,
    *,
    title: Optional[str] = None,
) -> str:
    """Render a self-contained static runtime graph with Mermaid plus timeline/detail panes."""

    from ai_workflow_engine.viz import workflow_to_mermaid

    title_text = title or f"Workflow observation: {definition.workflow_id}"
    mermaid = workflow_to_mermaid(definition, observation_graph=graph)
    node_rows = "\n".join(_node_row(node) for node in graph.nodes.values())
    timeline_rows = "\n".join(_timeline_row(item, graph.details) for item in graph.timeline)
    detail_rows = "\n".join(_detail_row(detail) for detail in graph.details.values()) or (
        '<p class="muted">No detail records captured.</p>'
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title_text)}</title>
<style>
body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2933; }}
h1 {{ font-size: 24px; margin: 0 0 4px; }}
h2 {{ font-size: 18px; margin-top: 28px; }}
.muted {{ color: #66788a; }}
.meta {{ margin-bottom: 18px; }}
.mermaid {{ padding: 16px; border: 1px solid #d9e2ec; background: #f8fafc; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
th, td {{ border: 1px solid #d9e2ec; padding: 8px; text-align: left; vertical-align: top; }}
th {{ background: #f0f4f8; }}
code {{ background: #f0f4f8; padding: 1px 4px; border-radius: 4px; }}
details {{ margin: 4px 0; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
.status-completed {{ color: #166534; font-weight: 600; }}
.status-failed {{ color: #b91c1c; font-weight: 600; }}
.status-running {{ color: #92400e; font-weight: 600; }}
</style>
</head>
<body>
<h1>{html.escape(title_text)}</h1>
<div class="meta muted">workflow_id: <code>{html.escape(graph.workflow_id)}</code>{_run_label(graph)}</div>
<pre class="mermaid">
{html.escape(mermaid)}
</pre>
<h2>Nodes</h2>
<table>
<thead><tr><th>Node</th><th>Kind</th><th>Status</th><th>Attempts</th><th>Usage</th><th>Details</th></tr></thead>
<tbody>{node_rows}</tbody>
</table>
<h2>Timeline</h2>
<table>
<thead><tr><th>#</th><th>Node</th><th>Phase</th><th>Decision</th><th>Severity</th><th>Elapsed</th><th>Details / Error</th></tr></thead>
<tbody>{timeline_rows}</tbody>
</table>
<h2>Details</h2>
{detail_rows}
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{ startOnLoad: true }});
</script>
</body>
</html>
"""


def save_observation_html(
    definition: WorkflowDefinition,
    graph: ObservationGraph,
    path: str | Path,
    *,
    title: Optional[str] = None,
) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(observation_graph_to_html(definition, graph, title=title), encoding="utf-8")
    return str(target)


def _usage_iter(
    usage_events: Iterable[WorkflowUsageEvent] | WorkflowUsageSummary,
) -> Iterable[WorkflowUsageEvent]:
    if isinstance(usage_events, WorkflowUsageSummary):
        return usage_events.events
    return usage_events


def _next_status(current: str, event: WorkflowTraceEvent) -> str:
    if event.error or event.severity == "error":
        return "failed"
    decision = event.decision or ""
    if decision == "start":
        return "running" if current == "not_started" else current
    if decision in {"accepted", "valid", "answered", "provisional"}:
        return "completed"
    if decision in {"failed", "rejected", "denied"}:
        return "failed"
    if decision:
        return current if current != "not_started" else "running"
    return current


def _infer_phase(event: WorkflowTraceEvent) -> Optional[str]:
    if event.error:
        return "error"
    if event.decision == "start":
        return "node:start"
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


def _run_label(graph: ObservationGraph) -> str:
    return f' run_id: <code>{html.escape(graph.run_id)}</code>' if graph.run_id else ""


def _node_row(node: ObservationNode) -> str:
    usage = _usage_label(node)
    return (
        "<tr>"
        f"<td><code>{html.escape(node.node_id)}</code></td>"
        f"<td>{html.escape(node.kind)}</td>"
        f'<td class="status-{html.escape(node.status)}">{html.escape(node.status)}</td>'
        f"<td>{node.attempts}</td>"
        f"<td>{html.escape(usage)}</td>"
        f"<td>{html.escape(', '.join(node.detail_refs) or '-')}</td>"
        "</tr>"
    )


def _usage_label(node: ObservationNode) -> str:
    parts = []
    if node.total_tokens:
        parts.append(f"{node.total_tokens} tokens")
    if node.metered_usd is not None:
        parts.append(f"metered ${node.metered_usd:.4f}")
    if node.notional_usd is not None:
        parts.append(f"notional ${node.notional_usd:.4f}")
    return " / ".join(parts) if parts else "-"


def _timeline_row(item: ObservationTimelineEntry, details: Mapping[str, ObservationDetail]) -> str:
    detail_bits = []
    for ref in item.detail_refs:
        detail = details.get(ref)
        label = f"{ref}:{detail.kind}" if detail else ref
        detail_bits.append(html.escape(label))
    if item.error:
        detail_bits.append(f"error: {html.escape(item.error)}")
    if item.metadata:
        metadata = html.escape(json.dumps(item.metadata, indent=2, sort_keys=True, default=str))
        detail_bits.append(
            f"<details><summary>metadata</summary><pre>{metadata}</pre></details>"
        )
    detail_html = "<br>".join(detail_bits) if detail_bits else "-"
    return (
        "<tr>"
        f"<td>{html.escape(item.event_id[:8])}</td>"
        f"<td><code>{html.escape(item.node)}</code></td>"
        f"<td>{html.escape(item.phase or '-')}</td>"
        f"<td>{html.escape(item.decision or '-')}</td>"
        f"<td>{html.escape(item.severity)}</td>"
        f"<td>{item.elapsed_ms if item.elapsed_ms is not None else '-'}</td>"
        f"<td>{detail_html}</td>"
        "</tr>"
    )


def _detail_row(detail: ObservationDetail) -> str:
    body = detail.text or detail.digest or ""
    if not body and detail.json_value:
        body = str(detail.json_value)
    return (
        "<details>"
        f"<summary><code>{html.escape(detail.detail_id)}</code> {html.escape(detail.kind)} "
        f"({html.escape(detail.redaction_state)})</summary>"
        f"<pre>{html.escape(body)}</pre>"
        "</details>"
    )
