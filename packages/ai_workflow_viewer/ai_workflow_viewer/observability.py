"""Runtime observability projection and static renderers."""

from __future__ import annotations

import html
from pathlib import Path
import json
from urllib.parse import quote
import re
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, Field

from ai_workflow_engine.models import (
    ObservationDetail,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.workflow import END, WorkflowDefinition


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
    # A1/A4/A5: external activity (fanout item calls, provenance) has no engine-owned
    # lifecycle, so its outcome is tallied HERE (one owner) — never recomputed per view.
    # Each event is classified into EXACTLY one bucket (_external_outcome), so an
    # accepted-with-warning event never double-counts and typed node_status success is
    # seen. When both buckets are non-empty the status becomes "mixed"; every reader
    # (card, Nodes table, Mermaid, JSON export) consumes the same status + counts.
    outcome_accepted: int = 0
    outcome_partial: int = 0
    outcome_failed: int = 0
    outcome_suspended: int = 0


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
    *,
    run_id: str | None = None,
) -> ObservationGraph:
    """Project runtime records into a graph view without mutating engine state."""

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
            # QRF.6: the original failure event and the terminal record may carry the same
            # text — evidence is deduplicated, never displayed twice.
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

    # A1/A4: external nodes (ids NOT in the declared graph) own their status HERE from the
    # SAME disjoint outcome tally the card shows, so status and tally can never disagree
    # (an accepted-with-warning event reads "completed", not "failed"). Declared nodes keep
    # their engine-owned status untouched; external nodes with only neutral events keep the
    # folded running/not_started value.
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


def render_runtime_timeline(graph: ObservationGraph) -> str:
    """Render a deterministic text timeline suitable for logs or chat handoff."""

    lines = [f"Observation timeline: {graph.workflow_id}"]
    if graph.run_id:
        lines.append(f"run_id: {graph.run_id}")
    for index, item in enumerate(graph.timeline, start=1):
        time_label = _timestamp_label(item.timestamp)
        head = f"{index}. {time_label} {item.node}" if time_label else f"{index}. {item.node}"
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

    from ai_workflow_viewer.viz import workflow_to_mermaid

    title_text = title or f"Workflow observation: {definition.workflow_id}"
    mermaid = workflow_to_mermaid(definition, observation_graph=graph)
    rich_graph = _rich_graph_html(definition, graph)
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
{_observation_base_css()}
{_rich_graph_css()}
</style>
</head>
<body>
<div class="topbar">
<h1>{html.escape(title_text)}</h1>
<div class="meta muted">workflow_id: <code>{html.escape(graph.workflow_id)}</code>{_run_label(graph)}</div>
<nav class="nav" aria-label="Observation sections">
<a href="#investigation">Investigation</a>
<a href="#graph">Mermaid</a>
<a href="#nodes">Nodes</a>
<a href="#timeline">Timeline</a>
<a href="#details">Raw Details</a>
</nav>
</div>
{rich_graph}
<section id="graph">
<details class="export-panel">
<summary>Mermaid Export</summary>
<div class="graph-scroll">
<pre class="mermaid">
{html.escape(mermaid)}
</pre>
</div>
</details>
</section>
<section id="nodes">
<h2>Nodes</h2>
<table>
<thead><tr><th>Node</th><th>Kind</th><th>Status</th><th>Attempts</th><th>Usage</th><th>Details</th></tr></thead>
<tbody>{node_rows}</tbody>
</table>
</section>
<section id="timeline">
<h2>Timeline</h2>
<table>
<thead><tr><th>#</th><th>Time</th><th>Node</th><th>Phase</th><th>Decision</th><th>Severity</th><th>Elapsed</th><th>Details / Error</th></tr></thead>
<tbody>{timeline_rows}</tbody>
</table>
</section>
<section id="details">
<h2>Details</h2>
<div class="toolbar">
<button type="button" data-detail-action="open">Open all details</button>
<button type="button" data-detail-action="close">Close all details</button>
</div>
{detail_rows}
</section>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{ startOnLoad: true }});
</script>
<script>{_rich_graph_js()}</script>
</body>
</html>
"""


# Only inert raster formats are ever rendered inline (exported <img> or served inline
# content) — SVG/HTML are ACTIVE content and must never execute in the viewer context.
INLINE_SAFE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

ARTIFACT_MANIFEST_NAME = "artifacts.json"

# A3: the manifest is UNTRUSTED input — the loader is the ONE place that proves it is a
# list of dict rows. Sentinels distinguish "no manifest" (absent → no section) from
# "manifest present but unreadable/malformed" (loud notice / 404), so no reader ever calls
# ``.get`` on a non-dict.
_MANIFEST_ABSENT = object()
_MANIFEST_UNREADABLE = object()


def load_artifact_manifest(bundle_dir: str | Path) -> Any:
    """Return the manifest as a ``list[dict]``, or a sentinel (``_MANIFEST_ABSENT`` /
    ``_MANIFEST_UNREADABLE``). Shared by the renderer and the served /artifact route so the
    two surfaces cannot disagree on what a valid manifest is."""

    manifest_path = Path(bundle_dir) / ARTIFACT_MANIFEST_NAME
    if not manifest_path.exists():
        return _MANIFEST_ABSENT
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _MANIFEST_UNREADABLE
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return _MANIFEST_UNREADABLE
    return entries


def _encode_artifact_path(rel_path: str) -> str:
    """A7: percent-encode each '/'-segment of a relative artifact path so spaces/#/%/unicode
    round-trip through browsers AND the /artifact route's per-segment ``unquote``. The ONE
    encoder shared by the renderer tail, the served-route href, and the exported-report base."""

    return "/".join(quote(segment, safe="") for segment in rel_path.split("/"))


def _artifact_section_html(bundle_dir: str, href_base: Optional[str] = None) -> str:
    """Q4.2: resolve the bundle's artifact manifest into USER-FACING evidence — image
    previews and clickable bundle-local links, honest skip reasons for uncopied entries.
    ``href_base`` is the ALREADY-ENCODED link prefix from the page's location to the bundle
    directory (relative preferred); default = a percent-encoded file:// URL for local viewing."""

    entries = load_artifact_manifest(bundle_dir)
    if entries is _MANIFEST_ABSENT:
        return ""
    if entries is _MANIFEST_UNREADABLE:
        return '<p class="muted">artifact manifest unreadable</p>'
    if not entries:
        return ""
    # A7: encode the DEFAULT (file://) base too — callers that pass href_base own their
    # own encoding (the served lambda and the export harness both go through
    # _encode_artifact_path); only the local-viewing default is built here.
    base = href_base if href_base is not None else Path(bundle_dir).resolve().as_uri()
    rows = []
    for entry in entries:
        name = str(entry.get("bundle_path") or entry.get("artifact_id") or "artifact")
        label = html.escape(f"{entry.get('role') or entry.get('kind') or 'artifact'} · {name}")
        if entry.get("copied") and entry.get("bundle_path"):
            href = f"{base}/{_encode_artifact_path(str(entry['bundle_path']))}"
            media = str(entry.get("media_type") or "")
            preview = (
                f'<br><a href="{html.escape(href)}"><img src="{html.escape(href)}" '
                f'alt="{label}" style="max-width:320px;max-height:240px;border:1px solid #d9e2ec"></a>'
                if media in INLINE_SAFE_MEDIA_TYPES
                else ""
            )
            rows.append(
                f'<li><a href="{html.escape(href)}">{label}</a> '
                # A2: size_bytes is UNTRUSTED manifest data — escape it like every sibling field
                f'<span class="muted">({html.escape(str(entry.get("size_bytes")))} bytes, '
                f'{html.escape(media or "file")}, owner: '
                f'{html.escape(str(entry.get("owner_node") or "-"))})</span>{preview}</li>'
            )
        else:
            reason = html.escape(str(entry.get("skip_reason") or "not copied"))
            rows.append(f'<li>{label} <span class="muted">— NOT archived: {reason}</span></li>')
    return (
        '<section class="artifacts"><h3>Artifacts</h3><ul>' + "\n".join(rows) + "</ul></section>"
    )


def observation_group_to_html(
    group: Any,
    *,
    title: Optional[str] = None,
    artifact_href_for: Optional[Any] = None,
) -> str:
    """Render ONE logical run assembled from its segments (W4.5) — one truthful lifecycle.

    The merged group events (all stamped with the logical run id) drive the standard
    projection, so a node suspended in segment 0 and completed in segment 1 renders
    completed — never a false still-running state. A segment strip on top shows each
    run-half (index, kind, status) plus honest usage totals: group spend aggregated once
    from segment-local events, with the engine's cumulative label reported separately.
    ``group`` is an :class:`ai_workflow_viewer.event_source.ObservationGroupData`.
    """

    graph = build_observation_graph(
        group.definition,
        group.trace_events,
        group.usage_events,
        group.details,
        run_id=group.run_id,
    )
    base = observation_graph_to_html(
        group.definition,
        graph,
        title=title or f"Workflow observation: {group.definition.workflow_id} (grouped run)",
    )
    cards = "\n".join(
        (
            f'<article class="segment-card status-{html.escape(str(segment.status))}" '
            f'data-segment-index="{segment.segment_index}" data-kind="{html.escape(segment.kind)}">'
            f"<h3>segment {segment.segment_index} · {html.escape(segment.kind)}</h3>"
            f"<p><code>{html.escape(segment.segment_id)}</code></p>"
            f"<p>status: <strong>{html.escape(str(segment.status))}</strong>{_segment_markers(segment)}</p>"
            "</article>"
        )
        for segment in group.segments
    )
    totals = group.usage_totals  # ACTUAL spend incl. non-canonical attempts (W4R.3/R1)
    canonical = group.canonical_usage_totals
    attempts_totals = group.non_canonical_usage_totals
    cumulative = group.cumulative_meta_totals

    def cost_summary(values: dict) -> str:
        metered = values.get("metered_usd")
        notional = values.get("notional_usd")
        return (
            f'metered ${metered if metered is not None else "0"}, '
            f'notional ${notional if notional is not None else "0"}, '
            f'unknown-cost events {values.get("unknown_cost_count", 0)}'
        )

    related = getattr(group, "related_run_id", None)  # the SINGLE validated identity
    related_label = (
        f' · Related-run ID <code>{html.escape(str(related))}</code>' if related else ""
    )
    notes = "\n".join(
        f'<p class="attempt-note muted">⚠ {html.escape(segment.disposition)} attempt at '
        f"segment {segment.segment_index} (<code>{html.escape(segment.segment_id)}</code>) — "
        f"inspectable evidence, not run history</p>"
        for segment in getattr(group, "non_canonical", [])
    )
    artifact_sections = "\n".join(
        section
        for segment in [*group.segments, *getattr(group, "non_canonical", [])]
        if (
            section := _artifact_section_html(
                segment.path,
                artifact_href_for(segment.path) if artifact_href_for else None,
            )
        )
    )
    strip = f"""<section class="segment-strip" id="segments">
<h2>Run segments ({len(group.segments)}) — Run ID <code>{html.escape(group.run_id)}</code>{related_label}</h2>
<div class="segment-cards">{cards}</div>
<p class="usage-totals"><strong>actual spend (all attempts, counted once): {totals.get("usage_count")} events,
{totals.get("total_tokens")} tokens, {cost_summary(totals)}</strong>
· canonical chain: {canonical.get("total_tokens")} tokens, {cost_summary(canonical)}
· non-canonical attempts: {attempts_totals.get("total_tokens")} tokens, {cost_summary(attempts_totals)}
· cumulative-at-finalize (engine label: {html.escape(str(cumulative.get("scope")))}): {cumulative.get("total_tokens")} tokens</p>
{notes}
{artifact_sections}
<style>
.segment-strip {{ margin: 1rem 0; }}
.segment-cards {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
.segment-card {{ border: 1px solid #ccc; border-radius: 6px; padding: 0.5rem 0.75rem; }}
.segment-card.status-completed {{ border-color: #2e7d32; }}
.segment-card.status-failed {{ border-color: #c62828; }}
.segment-card.status-requires_user_input {{ border-color: #ef6c00; }}
</style>
</section>"""
    if "<body>" not in base:
        raise RuntimeError("observation html template lost its <body> anchor — cannot inject segment strip")
    return base.replace("<body>", "<body>\n" + strip, 1)


def _segment_markers(segment: Any) -> str:
    """Resolution evidence for a segment card: timeout route / durable registration."""

    decisions = {
        event.decision
        for event in segment.data.trace_events
        if getattr(event, "decision", None)
    }
    markers = []
    if "wait:timeout_route" in decisions:
        markers.append("⏰ timeout route")
    if "wait:registered" in decisions or "wait:registration_reused" in decisions:
        markers.append("⏸ durable wait registered")
    if "machine:resumed" in decisions:
        markers.append("▶ resumed")
    return (" · " + " · ".join(markers)) if markers else ""


def _observation_base_css() -> str:
    return """
body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2933; background: #ffffff; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin-top: 28px; }
h3 { font-size: 15px; margin: 16px 0 8px; }
a { color: #1d4ed8; }
section { scroll-margin-top: 88px; }
.topbar { position: sticky; top: 0; z-index: 20; padding: 10px 0 12px; background: rgba(255, 255, 255, 0.97); border-bottom: 1px solid #d9e2ec; }
.nav { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.nav a, button { border: 1px solid #bcccdc; border-radius: 6px; background: #f8fafc; color: #1f2933; padding: 6px 10px; font: inherit; text-decoration: none; cursor: pointer; }
.nav a:hover, button:hover { background: #eef2f7; }
button.is-active { background: #dbeafe; border-color: #60a5fa; color: #0f3a7a; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.muted { color: #66788a; }
.meta { margin-bottom: 18px; }
.graph-scroll { max-height: 68vh; padding: 16px; border: 1px solid #d9e2ec; background: #f8fafc; overflow: auto; }
.mermaid { min-width: 960px; margin: 0; }
.export-panel { border: 1px solid #d9e2ec; border-radius: 8px; padding: 10px 12px; background: #ffffff; }
.export-panel > summary { font-weight: 700; cursor: pointer; }
.export-panel .graph-scroll { margin-top: 10px; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; }
th, td { border: 1px solid #d9e2ec; padding: 8px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { background: #f0f4f8; }
code { background: #f0f4f8; padding: 1px 4px; border-radius: 4px; }
details { margin: 4px 0; }
details.observation-detail { border: 1px solid #d9e2ec; border-radius: 6px; padding: 8px 10px; background: #ffffff; }
details.observation-detail + details.observation-detail { margin-top: 8px; }
summary { cursor: pointer; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
dialog.observation-dialog { border: 1px solid #9fb3c8; border-radius: 8px; width: min(1040px, 92vw); max-height: 86vh; padding: 0; box-shadow: 0 20px 45px rgba(15, 23, 42, 0.28); }
dialog.observation-dialog::backdrop { background: rgba(15, 23, 42, 0.35); }
.dialog-header { display: flex; justify-content: space-between; gap: 10px; align-items: center; padding: 12px 14px; border-bottom: 1px solid #d9e2ec; background: #f8fafc; }
.dialog-title { font-weight: 700; overflow-wrap: anywhere; }
.dialog-body { max-height: calc(86vh - 58px); overflow: auto; padding: 14px; }
.detail-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.copy-status { font-size: 12px; color: #166534; }
.status-completed { color: #166534; font-weight: 600; }
.status-failed { color: #b91c1c; font-weight: 600; }
.status-running { color: #92400e; font-weight: 600; }
.status-mixed { color: #78716c; font-weight: 600; }
.json-tree { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 12px; line-height: 1.5; }
.json-tree summary { cursor: pointer; list-style: none; }
.json-tree summary::-webkit-details-marker { display: none; }
.json-tree summary::before { content: "\\25B8 "; color: #6e7781; }
.json-tree details[open] > summary::before { content: "\\25BE "; }
.j-children { margin-left: 12px; border-left: 1px solid #eef1f4; padding-left: 8px; }
.j-item { white-space: pre-wrap; overflow-wrap: anywhere; }
.j-key { color: #0550ae; }
.j-str { color: #0a7d22; }
.j-num { color: #953800; }
.j-bool { color: #8250df; }
.j-null { color: #6e7781; }
.j-bracket, .j-colon, .j-comma { color: #57606a; }
.j-count { color: #6e7781; font-size: 11px; }
"""


def _rich_graph_css() -> str:
    return """
.investigation-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 420px); gap: 16px; align-items: start; }
.graph-workbench { min-width: 0; }
.graph-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0 10px; }
.graph-controls input { min-width: 220px; padding: 7px 9px; border: 1px solid #bcccdc; border-radius: 6px; font: inherit; }
.graph-hint { font-size: 13px; color: #52606d; margin-bottom: 8px; }
.rich-graph-scroll { max-height: 72vh; border: 1px solid #cbd5e1; background: #f8fafc; overflow: auto; position: relative; }
.rich-graph { position: relative; width: var(--canvas-width); height: var(--canvas-height); min-width: 100%; min-height: 520px; }
/* NEVER stretch this layer: its viewBox is the FIXED canvas coordinate space the
   absolutely-positioned node cards live in; CSS 100% sizing on a wider window would
   rescale+center the edges away from the nodes (min-width:100% grows .rich-graph).
   The width/height ATTRIBUTES set in renderEdges keep it identity-mapped. */
.rich-edge-layer { position: absolute; left: 0; top: 0; pointer-events: none; overflow: visible; }
.rich-edge { fill: none; stroke: #7b8794; stroke-width: 1.4; opacity: 0.78; }
.rich-edge.decision { stroke-dasharray: 4 3; }
.rich-edge-label { fill: #334e68; font-size: 11px; paint-order: stroke; stroke: #f8fafc; stroke-width: 3px; stroke-linejoin: round; }
.node-layer { position: absolute; inset: 0; }
.graph-node { position: absolute; box-sizing: border-box; width: 220px; min-height: 100px; border: 1px solid #9fb3c8; border-radius: 8px; background: #ffffff; box-shadow: 0 3px 10px rgba(31, 41, 55, 0.08); padding: 10px; cursor: grab; user-select: none; transition: box-shadow 120ms ease, border-color 120ms ease, opacity 120ms ease; }
.graph-node:active { cursor: grabbing; }
.graph-node:hover, .graph-node.is-selected { border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14), 0 8px 18px rgba(31, 41, 55, 0.12); }
.graph-node.is-hidden { opacity: 0.18; }
.graph-node.run-status-completed { border-left: 5px solid #16a34a; }
.graph-node.run-status-running { border-left: 5px solid #d97706; }
.graph-node.run-status-failed { border-left: 5px solid #dc2626; }
.graph-node.run-status-not_started { border-left: 5px solid #94a3b8; }
.graph-node.run-status-partial { border-left: 5px solid #ca8a04; }
.graph-node.run-status-suspended { border-left: 5px solid #7c3aed; }
.graph-node.run-status-mixed { border-left: 5px solid #78716c; }
.graph-node.kind-branch { background: #f0fdf4; }
.graph-node.kind-terminal { background: #f8fafc; }
.node-topline { display: flex; gap: 6px; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.node-pill { border-radius: 999px; padding: 2px 7px; font-size: 11px; line-height: 1.4; background: #e0f2fe; color: #075985; text-transform: uppercase; letter-spacing: 0.02em; }
.node-status { font-size: 11px; color: #52606d; }
.node-title { font-weight: 700; font-size: 14px; line-height: 1.25; color: #102a43; }
.node-id { margin-top: 3px; font-size: 11px; color: #627d98; overflow-wrap: anywhere; }
.node-description { margin-top: 7px; font-size: 12px; line-height: 1.35; color: #334e68; }
.node-external-note { margin-top: 6px; font-size: 11px; color: #92400e; background: #fef3c7; border-radius: 4px; padding: 2px 6px; display: inline-block; }
.external-legend { color: #92400e; }
.node-metrics { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 5px; font-size: 11px; color: #486581; }
.metric-chip { border: 1px solid #d9e2ec; border-radius: 999px; padding: 1px 6px; background: #f8fafc; }
.rich-graph[data-mode="overview"] .node-description,
.rich-graph[data-mode="overview"] .node-id { display: none; }
.rich-graph[data-mode="overview"] .graph-node { min-height: 76px; }
.rich-graph[data-mode="raw"] .node-id { color: #0f172a; font-weight: 600; }
.inspector { position: sticky; top: 92px; max-height: calc(100vh - 112px); overflow: auto; border: 1px solid #cbd5e1; border-radius: 8px; background: #ffffff; padding: 14px; box-shadow: 0 4px 14px rgba(31, 41, 55, 0.08); }
.inspector h2 { margin-top: 0; }
.inspector-section { border-top: 1px solid #e4e7eb; padding-top: 10px; margin-top: 12px; }
.inspector-grid { display: grid; grid-template-columns: auto 1fr; gap: 6px 10px; font-size: 13px; }
.inspector-grid dt { color: #627d98; }
.inspector-grid dd { margin: 0; overflow-wrap: anywhere; }
.detail-card { border: 1px solid #d9e2ec; border-radius: 7px; padding: 9px; margin: 8px 0; background: #f8fafc; }
.detail-card-header { display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; margin-bottom: 6px; }
.detail-kind { font-weight: 700; color: #102a43; }
.detail-summary { color: #334e68; font-size: 13px; line-height: 1.4; }
.event-list { display: grid; gap: 6px; }
.event-chip { border: 1px solid #d9e2ec; border-radius: 7px; padding: 7px; background: #ffffff; font-size: 12px; }
.event-chip.error { border-color: #fecaca; background: #fff1f2; }
.raw-toggle pre { max-height: 320px; overflow: auto; background: #0f172a; color: #e2e8f0; border-radius: 6px; padding: 10px; }
@media (max-width: 980px) {
  .investigation-layout { grid-template-columns: 1fr; }
  .inspector { position: static; max-height: none; }
}
"""


def _rich_graph_html(definition: WorkflowDefinition, graph: ObservationGraph) -> str:
    data = _observation_view_data(definition, graph)
    nodes_html = "\n".join(_rich_node_card(node) for node in data["nodes"])
    external_legend = (
        '<div class="graph-hint external-legend">EXTERNAL cards are recorded activity outside '
        "the declared workflow graph (e.g. fanout item calls, authored-flow provenance); the "
        "viewer never invents edges for them.</div>\n"
        if any(node["kind"] == "external" for node in data["nodes"])
        else ""
    )
    canvas = data["canvas"]
    return f"""
<section id="investigation">
<h2>Investigation Graph</h2>
<div class="investigation-layout">
<div class="graph-workbench">
<div class="graph-controls" aria-label="Graph controls">
<button type="button" class="mode-button" data-mode="overview">Overview</button>
<button type="button" class="mode-button is-active" data-mode="investigate">Investigate</button>
<button type="button" class="mode-button" data-mode="raw">Raw</button>
<input id="node-search" type="search" aria-label="Filter nodes or details">
<button type="button" id="reset-layout">Reset layout</button>
</div>
<div class="graph-hint">Click a node to inspect it. Drag nodes to rearrange the canvas. Raw payloads stay available in the inspector and Raw Details section.</div>
{external_legend}
<div class="rich-graph-scroll">
<div id="rich-graph" class="rich-graph" data-mode="investigate" style="--canvas-width: {canvas['width']}px; --canvas-height: {canvas['height']}px;">
<svg id="rich-edges" class="rich-edge-layer" aria-hidden="true"></svg>
<div class="node-layer">{nodes_html}</div>
</div>
</div>
</div>
<aside class="inspector" id="node-inspector" aria-live="polite">
<h2>Node Inspector</h2>
<p class="muted">Select a node to see decisions, cost, details, and raw evidence without scrolling through the full JSON list.</p>
</aside>
</div>
<script type="application/json" id="observation-data">{_json_script(data)}</script>
</section>
"""


def _rich_node_card(node: Mapping[str, Any]) -> str:
    metrics = "".join(
        f'<span class="metric-chip">{html.escape(part)}</span>'
        for part in node["metrics"]
    )
    return (
        f'<article class="graph-node run-status-{html.escape(node["status"])} '
        f'kind-{html.escape(node["kind"])}" tabindex="0" '
        f'data-node-id="{html.escape(node["id"], quote=True)}" '
        f'style="left: {node["x"]}px; top: {node["y"]}px;">'
        '<div class="node-topline">'
        f'<span class="node-pill">{html.escape(node["kind_label"])}</span>'
        f'<span class="node-status">{html.escape(node["status"])}</span>'
        '</div>'
        f'<div class="node-title">{html.escape(node["label"])}</div>'
        f'<div class="node-id">{html.escape(node["id"])}</div>'
        f'<div class="node-description">{html.escape(node["description"])}</div>'
        + (
            '<div class="node-external-note">outside declared graph — no wiring recorded</div>'
            if node["kind"] == "external"
            else ""
        )
        + f'<div class="node-metrics">{metrics}</div>'
        '</article>'
    )


def _observation_view_data(definition: WorkflowDefinition, graph: ObservationGraph) -> dict[str, Any]:
    definition_nodes = {node.id: node for node in definition.nodes}
    node_ids = _ordered_node_ids(definition, graph)
    positions, canvas = _layout_positions(definition, node_ids)
    timeline_by_node: dict[str, list[ObservationTimelineEntry]] = {}
    for item in graph.timeline:
        timeline_by_node.setdefault(item.node, []).append(item)

    nodes = []
    for node_id in node_ids:
        observed = graph.nodes.get(node_id)
        defined = definition_nodes.get(node_id)
        details = [
            _detail_view(graph.details[ref])
            for ref in (observed.detail_refs if observed else [])
            if ref in graph.details
        ]
        nodes.append(
            _node_view(
                node_id=node_id,
                defined=defined,
                observed=observed,
                details=details,
                events=timeline_by_node.get(node_id, []),
                position=positions[node_id],
            )
        )

    transitions = [
        {
            "source": transition.source,
            "target": transition.target,
            "label": transition.label or "",
            "policy": transition.policy,
            "description": transition.description or "",
        }
        for transition in definition.transitions
        if transition.source in positions and transition.target in positions
    ]
    return {
        "workflow_id": graph.workflow_id,
        "run_id": graph.run_id,
        "canvas": canvas,
        "nodes": nodes,
        "transitions": transitions,
    }


def _node_view(
    *,
    node_id: str,
    defined: Any,
    observed: ObservationNode | None,
    details: list[dict[str, Any]],
    events: list[ObservationTimelineEntry],
    position: dict[str, int],
) -> dict[str, Any]:
    status = observed.status if observed is not None else ("completed" if node_id == END else "not_started")
    kind = (defined.kind if defined is not None else ("terminal" if node_id == END else (observed.kind if observed else "external")))
    elapsed_ms = observed.elapsed_ms if observed is not None else 0
    metrics = []
    external_counts = None
    # A1: status is graph-layer truth — build_observation_graph already set "mixed" for
    # external nodes with both outcomes, so the view NEVER recomputes it and the card, the
    # Nodes table, Mermaid, and the JSON export can never disagree.
    if observed is not None:
        outcome_parts = [
            f"{count} {label}"
            for count, label in (
                (observed.outcome_accepted, "accepted"),
                (observed.outcome_partial, "partial"),
                (observed.outcome_failed, "failed"),
                (observed.outcome_suspended, "suspended"),
            )
            if count
        ]
        if len(outcome_parts) > 1:
            external_counts = " \u00b7 ".join(outcome_parts)
    if observed is not None and observed.attempts:
        metrics.append(f"{observed.attempts} attempt{'s' if observed.attempts != 1 else ''}")
    if elapsed_ms:
        metrics.append(f"{elapsed_ms} ms")
    if observed is not None and observed.total_tokens:
        metrics.append(f"{observed.total_tokens} tokens")
    if observed is not None and observed.metered_usd is not None:
        metrics.append(f"metered ${observed.metered_usd:.4f}")
    if observed is not None and observed.notional_usd is not None:
        metrics.append(f"notional ${observed.notional_usd:.4f}")
    if details:
        metrics.append(f"{len(details)} detail{'s' if len(details) != 1 else ''}")
    if external_counts:
        metrics.append(external_counts)
    # v0.10: surface the engine's execution window, timeout reason, and retrace round/target
    # from PERSISTED trace metadata — never inferred; absent records say "not recorded".
    metrics.extend(_runtime_bound_metrics(events))
    if not metrics:
        metrics.append("no runtime data")

    decisions = observed.decisions if observed is not None else []
    errors = observed.errors if observed is not None else []
    return {
        "id": node_id,
        "label": _display_label(defined, node_id),
        "description": _node_description(defined, node_id, kind),
        "kind": _css_token(kind),
        "kind_label": _kind_label(kind),
        "status": _css_token(status),
        "raw_status": status,
        "attempts": observed.attempts if observed is not None else 0,
        "elapsed_ms": elapsed_ms,
        "usage": _usage_label(observed) if observed is not None else "-",
        "metrics": metrics,
        "decisions": decisions,
        "errors": errors,
        "details": details,
        "events": [_event_view(event) for event in events],
        "x": position["x"],
        "y": position["y"],
        "width": position["width"],
        "height": position["height"],
        "search": " ".join(
            [
                node_id,
                _display_label(defined, node_id),
                _node_description(defined, node_id, kind),
                " ".join(decisions),
                " ".join(detail["summary"] for detail in details),
            ]
        ).lower(),
    }


def _runtime_bound_metrics(events: list[ObservationTimelineEntry]) -> list[str]:
    """Human-readable execution-window / timeout / retrace metrics from a node's recorded
    events. Reads ONLY persisted engine truth (trace metadata); it never guesses. When a
    timeout was recorded but the window was not (an older bundle), it says "not recorded"
    rather than inventing durations."""

    window: dict[str, Any] | None = None
    process_bound: dict[str, Any] | None = None
    process_io: dict[str, Any] | None = None
    timeout_reason: str | None = None
    retrace: tuple[Any, Any] | None = None
    for event in events:
        metadata = event.metadata or {}
        candidate = metadata.get("execution_window")
        if isinstance(candidate, dict):
            window = candidate  # last recorded wins
        process_candidate = metadata.get("process_execution_bound")
        if isinstance(process_candidate, dict):
            process_bound = process_candidate
        io_candidate = metadata.get("process_io")
        if isinstance(io_candidate, dict):
            process_io = io_candidate
        if metadata.get("timeout_reason"):
            timeout_reason = str(metadata["timeout_reason"])
        if metadata.get("retrace_target"):
            retrace = (metadata.get("retrace"), metadata["retrace_target"])

    metrics: list[str] = []
    if window is not None and window.get("hard_timeout_s") is not None:
        soft = window.get("soft_timeout_s")
        hard = window.get("hard_timeout_s")
        label = f"window: soft {float(soft):g}s / hard {float(hard):g}s"
        clamps = window.get("clamps") or []
        if clamps:
            label += f" (clamped: {', '.join(str(c) for c in clamps)})"
        enforcement = window.get("enforcement")
        if enforcement:
            label += f" [{enforcement}]"
        metrics.append(label)
    if process_bound is not None and process_bound.get("work_timeout_s") is not None:
        work = float(process_bound["work_timeout_s"])
        grace = float(process_bound.get("kill_grace_s") or 0.0)
        label = f"process: work {work:g}s / cleanup {grace:g}s"
        settle = float(process_bound.get("settle_reserve_s") or 0.0)
        if settle:
            label += f" / settle {settle:g}s"
        headroom = float(process_bound.get("cleanup_headroom_s") or 0.0)
        if headroom:
            label += f" (reserved: {headroom:g}s)"
        source = process_bound.get("source")
        if source:
            label += f" [{source}]"
        metrics.append(label)
    if process_io is not None:
        # v0.10.1: a CONCISE settlement summary — byte totals, truncation, and result-file
        # state — so operators diagnose without the viewer copying the flood into the page.
        io_label = _process_io_summary(process_io)
        if io_label:
            metrics.append(io_label)
    if timeout_reason is not None:
        if window is None:
            metrics.append("window: not recorded")
        metrics.append(f"timeout: {timeout_reason}")
    if retrace is not None:
        round_no, target = retrace
        prefix = f"retrace round {round_no}" if round_no is not None else "retrace"
        metrics.append(f"{prefix} → {target}")
    return metrics


def _format_bytes(value: Any) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "?"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


def _process_io_summary(process_io: dict[str, Any]) -> str | None:
    """One concise line summarizing bounded-capture truth — never the captured bytes."""

    parts: list[str] = []
    for stream in ("stdout", "stderr"):
        entry = process_io.get(stream)
        if isinstance(entry, dict) and entry.get("total_bytes"):
            piece = f"{stream} {_format_bytes(entry.get('total_bytes'))}"
            if entry.get("truncated"):
                piece += " (truncated)"
            parts.append(piece)
    capture = f"capture: {' / '.join(parts)}" if parts else None
    result = process_io.get("result_file")
    result_label = None
    if isinstance(result, dict):
        status = result.get("status")
        if status and status not in ("ok", "missing"):
            kind = result.get("kind")
            result_label = f"result file: {status}" + (f" ({kind})" if kind else "")
    joined = " · ".join(p for p in (capture, result_label) if p)
    return joined or None


def _event_view(event: ObservationTimelineEntry) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp,
        "timestamp_label": _timestamp_label(event.timestamp),
        "phase": event.phase or "",
        "decision": event.decision or "",
        "severity": event.severity,
        "elapsed_ms": event.elapsed_ms,
        "error": event.error or "",
        "detail_refs": list(event.detail_refs),
    }


# v0.10.1: a hard ceiling on how much of any one detail body the PAGE renders. The full
# (already engine-bounded) record stays in the bundle and remains extractable; the viewer must
# not echo a multi-megabyte captured log into the HTML.
_DETAIL_BODY_DISPLAY_LIMIT = 64 * 1024


def _bounded_detail_body(body: str) -> str:
    if len(body) <= _DETAIL_BODY_DISPLAY_LIMIT:
        return body
    head = _DETAIL_BODY_DISPLAY_LIMIT // 2
    tail = _DETAIL_BODY_DISPLAY_LIMIT - head
    dropped = len(body) - head - tail
    return (
        f"{body[:head]}\n"
        f"...[{dropped} chars truncated in this view — full record in the bundle]...\n"
        f"{body[-tail:]}"
    )


def _detail_view(detail: ObservationDetail) -> dict[str, Any]:
    body = _bounded_detail_body(_detail_body(detail))
    return {
        "id": detail.detail_id,
        "kind": detail.kind,
        "privacy": detail.privacy,
        "redaction_state": detail.redaction_state,
        "content_type": detail.content_type,
        "digest": detail.digest,
        "summary": _detail_summary(detail, body),
        "body": body,
        "anchor": _dom_id("detail", detail.detail_id),
    }


def _ordered_node_ids(definition: WorkflowDefinition, graph: ObservationGraph) -> list[str]:
    ordered = [node.id for node in definition.nodes]
    if any(transition.target == END for transition in definition.transitions):
        ordered.append(END)
    for node_id in graph.nodes:
        if node_id not in ordered:
            ordered.append(node_id)
    return ordered


def _layout_positions(
    definition: WorkflowDefinition,
    node_ids: list[str],
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    outgoing: dict[str, list[str]] = {}
    for transition in definition.transitions:
        outgoing.setdefault(transition.source, []).append(transition.target)
    entry = definition.entry or (node_ids[0] if node_ids else "")
    levels: dict[str, int] = {entry: 0} if entry else {}
    queue = [entry] if entry else []
    while queue:
        source = queue.pop(0)
        for target in outgoing.get(source, []):
            if target not in node_ids or target in levels:
                continue
            levels[target] = levels[source] + 1
            queue.append(target)

    next_level = (max(levels.values()) + 1) if levels else 0
    for node_id in node_ids:
        if node_id not in levels:
            levels[node_id] = next_level
            next_level += 1

    layers: dict[int, list[str]] = {}
    for node_id in node_ids:
        layers.setdefault(levels[node_id], []).append(node_id)

    card_width = 220
    card_height = 106
    x_gap = 292
    y_gap = 136
    margin = 32
    max_rows = max((len(items) for items in layers.values()), default=1)
    positions: dict[str, dict[str, int]] = {}
    for level, items in layers.items():
        for row, node_id in enumerate(items):
            positions[node_id] = {
                "x": margin + level * x_gap,
                "y": margin + row * y_gap,
                "width": card_width,
                "height": card_height,
            }
    canvas = {
        "width": max(960, margin * 2 + (max(layers) + 1 if layers else 1) * x_gap + card_width),
        "height": max(560, margin * 2 + max_rows * y_gap + card_height),
    }
    return positions, canvas


def _display_label(defined: Any, node_id: str) -> str:
    if node_id == END:
        return "End"
    if defined is not None and getattr(defined, "title", ""):
        return defined.title
    if defined is not None and defined.description:
        first_sentence = re.split(r"(?<=[.!?])\s+", defined.description.strip(), maxsplit=1)[0]
        if 4 <= len(first_sentence) <= 44:
            return first_sentence.rstrip(".")
    return " ".join(part.capitalize() for part in re.split(r"[_\-\s]+", node_id) if part)


def _node_description(defined: Any, node_id: str, kind: str) -> str:
    if node_id == END:
        return "Workflow completed."
    if defined is not None and defined.description:
        return defined.description
    capability = getattr(defined, "capability", None) or getattr(defined, "decider", None) or node_id
    if kind == "branch":
        return f"Chooses the next workflow path using {capability}."
    if kind == "step":
        return f"Runs capability {capability}."
    return f"{_kind_label(kind)} node."


def _kind_label(kind: str) -> str:
    return {
        "step": "step",
        "branch": "branch",
        "evaluate": "evaluate",
        "fanout": "fanout",
        "planner": "planner",
        "human": "human",
        "subworkflow": "subflow",
        "terminal": "end",
    }.get(kind, kind or "node")


def _detail_body(detail: ObservationDetail) -> str:
    if detail.json_value is not None:
        return json.dumps(detail.json_value, indent=2, sort_keys=True, default=str)
    if detail.text:
        return detail.text
    if detail.digest:
        return f"digest: {detail.digest}"
    if detail.artifact_id:
        return f"artifact: {detail.artifact_id}"
    return ""


def _detail_summary(detail: ObservationDetail, body: str) -> str:
    if detail.redaction_state == "digest_only":
        digest = f" digest={detail.digest[:16]}" if detail.digest else ""
        return f"Digest-only {detail.kind}.{digest}"
    if detail.json_value is not None:
        highlights = _json_highlights(detail.json_value)
        if highlights:
            return "; ".join(highlights)
    return _truncate(_single_line(body), 420)


_INTERESTING_DETAIL_KEYS = (
    "validation_error",
    "error",
    "decision",
    "status",
    "card_kind",
    "count",
    "strategy",
    "image_role",
    "rationale",
    "accepted",
    "repair_strategy",
    "issues",
    "guidance",
    "fallback_used",
    "fallback_reason",
    "cleaned_content",
    "source_content",
    "question",
    "answer",
    "text",
)


def _json_highlights(value: Any) -> list[str]:
    highlights: list[str] = []

    def visit(item: Any, depth: int = 0) -> None:
        if len(highlights) >= 8 or depth > 3:
            return
        if isinstance(item, dict):
            for key in _INTERESTING_DETAIL_KEYS:
                if key in item and len(highlights) < 8:
                    rendered = _compact_value(item[key])
                    if rendered:
                        highlights.append(f"{key}: {rendered}")
            for child in item.values():
                if len(highlights) >= 8:
                    break
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item[:4]:
                if len(highlights) >= 8:
                    break
                visit(child, depth + 1)

    visit(value)
    if highlights:
        return _dedupe(highlights)
    if isinstance(value, dict):
        keys = ", ".join(list(value)[:8])
        return [f"keys: {keys}"] if keys else []
    if isinstance(value, list):
        return [f"{len(value)} item{'s' if len(value) != 1 else ''}"]
    return []


def _compact_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _truncate(_single_line(value), 130)
    if isinstance(value, list):
        primitive = [item for item in value if not isinstance(item, (dict, list))]
        if primitive:
            return _truncate(", ".join(_single_line(str(item)) for item in primitive[:4]), 130)
        return f"{len(value)} item{'s' if len(value) != 1 else ''}"
    if isinstance(value, dict):
        for key in _INTERESTING_DETAIL_KEYS:
            if key in value:
                rendered = _compact_value(value[key])
                if rendered:
                    return rendered
        return f"keys {', '.join(list(value)[:4])}"
    return _truncate(_single_line(str(value)), 130)


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def _css_token(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "_", value or "unknown")


def _dom_id(prefix: str, value: str) -> str:
    return f"obs-{prefix}-{_css_token(value)}"


def _json_script(value: Any) -> str:
    return (
        json.dumps(value, sort_keys=True, default=str)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _rich_graph_js() -> str:
    return r"""
(function () {
  const dataEl = document.getElementById("observation-data");
  if (!dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const graph = document.getElementById("rich-graph");
  const edgeLayer = document.getElementById("rich-edges");
  const inspector = document.getElementById("node-inspector");
  const search = document.getElementById("node-search");
  const nodes = new Map(data.nodes.map((node) => [node.id, node]));
  const nodeEls = new Map();
  const positions = new Map();
  const initialPositions = new Map(
    data.nodes.map((node) => [node.id, { x: node.x, y: node.y }])
  );

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function setNodePosition(id, x, y) {
    const node = nodes.get(id);
    const el = nodeEls.get(id);
    if (!node || !el) return;
    node.x = Math.max(0, x);
    node.y = Math.max(0, y);
    positions.set(id, { x: node.x, y: node.y, width: node.width, height: node.height });
    el.style.left = `${node.x}px`;
    el.style.top = `${node.y}px`;
  }

  function isHidden(id) {
    const el = nodeEls.get(id);
    return Boolean(el && el.classList.contains("is-hidden"));
  }

  function renderEdges() {
    edgeLayer.setAttribute("viewBox", `0 0 ${data.canvas.width} ${data.canvas.height}`);
    edgeLayer.setAttribute("width", data.canvas.width);
    edgeLayer.setAttribute("height", data.canvas.height);
    edgeLayer.innerHTML = '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 z" fill="#7b8794"></path></marker></defs>';
    for (const edge of data.transitions) {
      if (isHidden(edge.source) || isHidden(edge.target)) continue;
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) continue;
      const sx = source.x + source.width;
      const sy = source.y + source.height / 2;
      const tx = target.x;
      const ty = target.y + target.height / 2;
      const bend = Math.max(70, Math.abs(tx - sx) / 2);
      const d = `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.setAttribute("class", `rich-edge ${edge.policy === "decision" ? "decision" : ""}`);
      path.setAttribute("marker-end", "url(#arrow)");
      edgeLayer.appendChild(path);
      if (edge.label || edge.description) {
        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("x", (sx + tx) / 2);
        label.setAttribute("y", (sy + ty) / 2 - 6);
        label.setAttribute("class", "rich-edge-label");
        label.textContent = edge.label || edge.description;
        edgeLayer.appendChild(label);
      }
    }
  }

  function jsonNode(value) {
    if (value === null) return '<span class="j-null">null</span>';
    const t = typeof value;
    if (t === "number") return `<span class="j-num">${value}</span>`;
    if (t === "boolean") return `<span class="j-bool">${value}</span>`;
    if (t === "string") return `<span class="j-str">${escapeHtml(JSON.stringify(value))}</span>`;
    if (Array.isArray(value)) {
      if (!value.length) return '<span class="j-bracket">[]</span>';
      const items = value.map((v, i) => `<div class="j-item">${jsonNode(v)}${i < value.length - 1 ? '<span class="j-comma">,</span>' : ''}</div>`).join("");
      return `<details open><summary><span class="j-bracket">[</span> <span class="j-count">${value.length}</span></summary><div class="j-children">${items}</div><span class="j-bracket">]</span></details>`;
    }
    if (t === "object") {
      const keys = Object.keys(value);
      if (!keys.length) return '<span class="j-bracket">{}</span>';
      const items = keys.map((k, i) => `<div class="j-item"><span class="j-key">${escapeHtml(JSON.stringify(k))}</span><span class="j-colon">: </span>${jsonNode(value[k])}${i < keys.length - 1 ? '<span class="j-comma">,</span>' : ''}</div>`).join("");
      return `<details open><summary><span class="j-bracket">{</span> <span class="j-count">${keys.length}</span></summary><div class="j-children">${items}</div><span class="j-bracket">}</span></details>`;
    }
    return escapeHtml(String(value));
  }

  function jsonTreeHtml(text) {
    if (!text) return "";
    try { return `<div class="json-tree">${jsonNode(JSON.parse(text))}</div>`; }
    catch (e) { return `<pre>${escapeHtml(text)}</pre>`; }
  }

  function detailHtml(detail) {
    const rawLink = detail.anchor
      ? `<a href="#${escapeHtml(detail.anchor)}" data-open-detail="${escapeHtml(detail.anchor)}">open in raw list</a>`
      : "";
    return `
      <div class="detail-card">
        <div class="detail-card-header">
          <span class="detail-kind">${escapeHtml(detail.kind)}</span>
          <span class="muted">${escapeHtml(detail.redaction_state)} · ${escapeHtml(detail.privacy)}</span>
          ${rawLink}
        </div>
        <div class="detail-summary">${escapeHtml(detail.summary || "(no summary)")}</div>
        ${detail.digest ? `<div class="detail-digest">digest ${escapeHtml(detail.digest)}</div>` : ""}
        <details class="raw-toggle">
          <summary>Raw ${escapeHtml(detail.content_type || "detail")}</summary>
          ${jsonTreeHtml(detail.body || "")}
        </details>
      </div>`;
  }

  function eventHtml(event) {
    const bits = [
      event.timestamp_label || event.timestamp || "",
      event.phase || "event",
      event.decision ? `→ ${event.decision}` : "",
      event.elapsed_ms == null ? "" : `${event.elapsed_ms} ms`,
      event.detail_refs && event.detail_refs.length ? `${event.detail_refs.length} detail refs` : "",
    ].filter(Boolean).join(" · ");
    return `<div class="event-chip ${event.error ? "error" : ""}">
      <strong>${escapeHtml(bits || event.event_id.slice(0, 8))}</strong>
      ${event.error ? `<div>error: ${escapeHtml(event.error)}</div>` : ""}
    </div>`;
  }

  function selectNode(id) {
    const node = nodes.get(id);
    if (!node) return;
    for (const el of nodeEls.values()) el.classList.remove("is-selected");
    const selectedEl = nodeEls.get(id);
    if (selectedEl) selectedEl.classList.add("is-selected");
    const decisions = node.decisions.length ? node.decisions.join(", ") : "-";
    const errors = node.errors.length ? node.errors.map(escapeHtml).join("<br>") : "-";
    const details = node.details.length
      ? node.details.map(detailHtml).join("")
      : '<p class="muted">No detail records captured for this node.</p>';
    const events = node.events.length
      ? node.events.map(eventHtml).join("")
      : '<p class="muted">No timeline events captured for this node.</p>';
    inspector.innerHTML = `
      <h2>${escapeHtml(node.label)}</h2>
      <p class="muted"><code>${escapeHtml(node.id)}</code></p>
      <p>${escapeHtml(node.description)}</p>
      <dl class="inspector-grid">
        <dt>Status</dt><dd>${escapeHtml(node.raw_status)}</dd>
        <dt>Kind</dt><dd>${escapeHtml(node.kind_label)}</dd>
        <dt>Attempts</dt><dd>${escapeHtml(node.attempts)}</dd>
        <dt>Elapsed</dt><dd>${node.elapsed_ms ? `${escapeHtml(node.elapsed_ms)} ms` : "-"}</dd>
        <dt>Usage</dt><dd>${escapeHtml(node.usage)}</dd>
        <dt>Decisions</dt><dd>${escapeHtml(decisions)}</dd>
        <dt>Errors</dt><dd>${errors}</dd>
      </dl>
      <div class="inspector-section">
        <h3>Significant Details</h3>
        ${details}
      </div>
      <div class="inspector-section">
        <h3>Timeline For Node</h3>
        <div class="event-list">${events}</div>
      </div>`;
    inspector.querySelectorAll("[data-open-detail]").forEach((link) => {
      link.addEventListener("click", (event) => {
        const target = document.getElementById(link.dataset.openDetail);
        if (target && target.tagName.toLowerCase() === "details") target.open = true;
      });
    });
  }

  function applySearch() {
    const query = (search.value || "").trim().toLowerCase();
    for (const node of data.nodes) {
      const el = nodeEls.get(node.id);
      if (!el) continue;
      el.classList.toggle("is-hidden", Boolean(query && !node.search.includes(query)));
    }
    renderEdges();
  }

  for (const node of data.nodes) {
    const el = document.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`);
    if (!el) continue;
    nodeEls.set(node.id, el);
    positions.set(node.id, { x: node.x, y: node.y, width: node.width, height: node.height });
    el.addEventListener("click", () => selectNode(node.id));
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(node.id);
      }
    });

    let drag = null;
    el.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      drag = { startX: event.clientX, startY: event.clientY, x: node.x, y: node.y };
      el.setPointerCapture(event.pointerId);
    });
    el.addEventListener("pointermove", (event) => {
      if (!drag) return;
      setNodePosition(node.id, drag.x + event.clientX - drag.startX, drag.y + event.clientY - drag.startY);
      renderEdges();
    });
    el.addEventListener("pointerup", () => { drag = null; });
    el.addEventListener("pointercancel", () => { drag = null; });
  }

  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      graph.dataset.mode = button.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
    });
  });

  document.getElementById("reset-layout")?.addEventListener("click", () => {
    for (const node of data.nodes) {
      const initial = initialPositions.get(node.id);
      if (initial) setNodePosition(node.id, initial.x, initial.y);
    }
    applySearch();
  });

  search?.addEventListener("input", applySearch);

  document.querySelectorAll("[data-detail-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const shouldOpen = button.dataset.detailAction === "open";
      document.querySelectorAll("details.observation-detail").forEach((item) => {
        item.open = shouldOpen;
      });
    });
  });

  document.querySelectorAll("[data-open-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.openDialog);
      if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    });
  });

  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = button.closest("dialog");
      if (dialog) dialog.close();
    });
  });

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.textContent || "");
        const status = button.parentElement?.querySelector(".copy-status");
        if (status) {
          status.textContent = "copied";
          window.setTimeout(() => { status.textContent = ""; }, 1200);
        }
      } catch (_error) {
        window.prompt("Copy detail payload", target.textContent || "");
      }
    });
  });

  renderEdges();
  const firstObserved = data.nodes.find((node) => node.raw_status !== "not_started") || data.nodes[0];
  if (firstObserved) selectNode(firstObserved.id);
})();
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


def _usage_run_id(event: WorkflowUsageEvent) -> str | None:
    if event.run_id:
        return str(event.run_id)
    if not event.metadata:
        return None
    run_id = event.metadata.get("run_id") or event.metadata.get("workflow_id")
    return str(run_id) if run_id else None


def _external_outcome(event: WorkflowTraceEvent) -> Optional[str]:
    """Classify ONE external-activity event into exactly one outcome bucket, or None.

    Precedence matches ``_next_status`` (typed ``node_status`` is authoritative, then the
    decision, then error/severity), so the tally can never disagree with the folded status
    and an accepted-with-warning event is counted once as ``accepted`` (A4), while a
    typed-``node_status`` success with no decision is still seen (A5). Neutral events
    (start/request) return None and count toward neither bucket.
    """

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
    if decision in {"accepted", "valid", "answered", "provisional"}:
        return "accepted"
    if decision in {"failed", "rejected", "denied"}:
        return "failed"
    if decision == "partial":
        return "partial"
    if event.error or event.severity == "error":
        return "failed"
    return None


def _next_status(current: str, event: WorkflowTraceEvent) -> str:
    # QRF.5: the TYPED terminal field is authoritative and is read FIRST — a partial node
    # carrying its children's failure reason stays partial; error text is evidence, not a
    # status override, whenever the engine stated how the node ended.
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
    decision = event.decision or ""
    if decision == "flow:authored":
        # LEGACY BUNDLES ONLY: pre-typed-field events carried no node_status; new engine
        # events set node_status="completed" and never reach this branch.
        return "completed"
    if decision == "start":
        return "running" if current == "not_started" else current
    if decision in {"accepted", "valid", "answered", "provisional"}:
        return "completed"
    if decision in {"failed", "rejected", "denied"}:
        return "failed"
    if decision == "partial":
        return "partial"
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


def _timestamp_label(timestamp: str) -> str:
    if not timestamp:
        return ""
    if len(timestamp) >= 19 and timestamp[10] == "T":
        suffix = "Z" if timestamp.endswith("Z") else ""
        return f"{timestamp[11:19]}{suffix}"
    return timestamp


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
        f'<td title="{html.escape(item.timestamp)}">{html.escape(_timestamp_label(item.timestamp) or "-")}</td>'
        f"<td><code>{html.escape(item.node)}</code></td>"
        f"<td>{html.escape(item.phase or '-')}</td>"
        f"<td>{html.escape(item.decision or '-')}</td>"
        f"<td>{html.escape(item.severity)}</td>"
        f"<td>{item.elapsed_ms if item.elapsed_ms is not None else '-'}</td>"
        f"<td>{detail_html}</td>"
        "</tr>"
    )


def _detail_row(detail: ObservationDetail) -> str:
    body = _bounded_detail_body(_detail_body(detail))
    digest = f" digest={detail.digest}" if detail.digest else ""
    detail_dom_id = _dom_id("detail", detail.detail_id)
    dialog_id = _dom_id("detail_dialog", detail.detail_id)
    raw_id = _dom_id("detail_raw", detail.detail_id)
    return (
        f'<details class="observation-detail" id="{html.escape(detail_dom_id, quote=True)}">'
        f"<summary><code>{html.escape(detail.detail_id)}</code> {html.escape(detail.kind)} "
        f"({html.escape(detail.redaction_state)}){html.escape(digest)}</summary>"
        '<div class="detail-actions">'
        f'<button type="button" data-open-dialog="{html.escape(dialog_id, quote=True)}">Expand</button>'
        f'<button type="button" data-copy-target="{html.escape(raw_id, quote=True)}">Copy raw</button>'
        '<span class="copy-status" aria-live="polite"></span>'
        "</div>"
        f"<pre>{html.escape(body)}</pre>"
        f'<dialog class="observation-dialog" id="{html.escape(dialog_id, quote=True)}">'
        '<div class="dialog-header">'
        f'<div class="dialog-title">{html.escape(detail.kind)} · <code>{html.escape(detail.detail_id)}</code></div>'
        '<button type="button" data-close-dialog>Close</button>'
        "</div>"
        '<div class="dialog-body">'
        f'<pre id="{html.escape(raw_id, quote=True)}">{html.escape(body)}</pre>'
        "</div>"
        "</dialog>"
        "</details>"
    )
