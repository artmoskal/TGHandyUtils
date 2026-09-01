"""Static rendering of already-projected workflow observation truth."""

from __future__ import annotations

import html
from pathlib import Path
import json
import math
from urllib.parse import quote
import re
from typing import Any, Mapping, Optional

from ai_workflow_engine.models import WorkflowUsageEvent
from ai_workflow_engine.workflow import END, WorkflowDefinition
from ai_workflow_viewer.assets import load_asset_text
from ai_workflow_viewer.detail_presentation import (
    DetailHrefFor,
    ObservationDisplayDetail,
    detail_row,
    detail_view,
)
from ai_workflow_viewer.projection import (
    ObservationGraph,
    ObservationNode,
    ObservationTimelineEntry,
    build_observation_graph,
)
from ai_workflow_viewer.view_models import seal_observation_view_data


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
    usage_events: Optional[list[WorkflowUsageEvent]] = None,
    detail_href_for: DetailHrefFor | None = None,
) -> str:
    """Render a self-contained static runtime graph with Mermaid plus timeline/detail panes."""

    from ai_workflow_viewer.viz import workflow_to_mermaid

    title_text = title or f"Workflow observation: {definition.workflow_id}"
    mermaid = workflow_to_mermaid(definition, observation_graph=graph)
    rich_graph = _rich_graph_html(definition, graph, detail_href_for=detail_href_for)
    node_rows = "\n".join(_node_row(node) for node in graph.nodes.values())
    timeline_rows = "\n".join(_timeline_row(item, graph.details) for item in graph.timeline)
    detail_rows = "\n".join(
        detail_row(detail, detail_href_for) for detail in graph.details.values()
    ) or (
        '<p class="muted">No detail records captured.</p>'
    )
    economics = _usage_pricing_html(usage_events or [])
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
<a href="#economics">Economics</a>
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
{economics}
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


def _usage_pricing_html(events: list[WorkflowUsageEvent]) -> str:
    rows: list[str] = []
    for event in events:
        usage = event.normalized_usage
        pricing = event.notional_pricing
        if event.cost_class == "subscription_notional":
            if pricing is None or not pricing.known:
                amount = "unknown"
                source = (
                    _pricing_label(pricing.unknown_reason)
                    if pricing is not None
                    else _pricing_label(event.usage_error or "pricing_not_recorded")
                )
            else:
                amount = f"${_format_precise_usd(pricing.amount_usd)} notional"
                source = (
                    f"{_pricing_label(pricing.source)} · {pricing.catalog_version}"
                    + (
                        f" · {pricing.rate.rate_version}"
                        if pricing.rate is not None
                        else ""
                    )
                )
        elif event.estimated_usd is None:
            amount = "unknown"
            source = "metered estimate not recorded"
        else:
            amount = f"${_format_precise_usd(event.estimated_usd)} metered"
            source = "metered provider/rate estimate"

        quantities = "not recorded"
        if usage is not None:
            quantities = (
                f"{usage.uncached_input_tokens} uncached input · "
                f"{usage.cache_read_input_tokens} cached input · "
                f"{usage.cache_creation_input_tokens} cache-created input · "
                f"{usage.output_tokens} output "
                f"({usage.reasoning_output_tokens} reasoning output)"
            )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(event.invocation_id or 'not recorded'))}</td>"
            f"<td>{html.escape(event.node)}</td>"
            f"<td>{html.escape(event.provider)}</td>"
            f"<td>{html.escape(event.model or 'not recorded')}</td>"
            f"<td>{html.escape('completed' if event.success else 'incomplete')}</td>"
            f"<td>{html.escape(f'{event.elapsed_ms} ms' if event.elapsed_ms is not None else 'not recorded')}</td>"
            f"<td>{html.escape(quantities)}</td>"
            f"<td>{html.escape(amount)}</td>"
            f"<td>{html.escape(str(source))}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or (
        '<tr><td colspan="9" class="muted">No usage events recorded.</td></tr>'
    )
    return f"""<section id="economics">
<h2>Economics</h2>
<table>
<thead><tr><th>Invocation</th><th>Node</th><th>Provider</th><th>Model</th><th>Outcome</th><th>Elapsed</th><th>Token basis</th><th>Amount</th><th>Source</th></tr></thead>
<tbody>{body}</tbody>
</table>
</section>"""


def _pricing_label(value: str) -> str:
    return value.replace("_", " ")


def _format_precise_usd(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:.9f}".rstrip("0").rstrip(".")


# Only inert raster formats are ever rendered inline (exported <img> or served inline
# content) — SVG/HTML are ACTIVE content and must never execute in the viewer context.
INLINE_SAFE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

# C2-1: the manifest name is the ENGINE's fixed-layout fact (a Literal field of the v2 meta) —
# one owner, no viewer-side duplicate that could drift.
from ai_workflow_engine.observation_bundle import ARTIFACT_MANIFEST_NAME  # noqa: E402

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
    detail_href_for: DetailHrefFor | None = None,
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
        usage_events=group.usage_events,
        detail_href_for=detail_href_for,
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


def observation_index_to_html(
    runs: list[dict],
    *,
    title: str = "Workflow observations",
    related_run_id: Optional[str] = None,
) -> str:
    """Render the run chooser from already-decoded group summaries."""

    rows = "\n".join(
        _index_run_row(run, related_filter=related_run_id) for run in runs
    )
    filter_banner = (
        f'<p class="muted">Filtered by Related-run ID '
        f"<code>{html.escape(related_run_id)}</code> — runs stay separate "
        f'(<a href="?">clear filter</a>)</p>'
        if related_run_id is not None
        else ""
    )
    empty_message = (
        f"No runs for Related-run ID <code>{html.escape(related_run_id)}</code>."
        if related_run_id is not None
        else "No observation runs found."
    )
    table = (
        '<table><thead><tr><th>Run ID</th><th>Related-run ID</th><th>Workflow</th>'
        '<th>Status</th><th>Timestamp</th><th>Usage</th><th>Open</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
        if rows
        else f'<div class="empty muted">{empty_message}</div>'
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
{load_asset_text("index.css")}</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
{filter_banner}
{table}
</body>
</html>
"""


def _index_run_row(run: dict, *, related_filter: Optional[str] = None) -> str:
    run_id = str(run.get("run_id") or "")
    workflow = str(run.get("workflow_id") or "")
    status = str(run.get("status") or "")
    timestamp = str(run.get("timestamp") or "")
    total_tokens = run.get("total_tokens")
    metered = run.get("metered_usd")
    notional = run.get("notional_usd")
    usage = _index_usage_label(total_tokens, metered, notional)
    href = f"?run_id={quote(run_id)}"
    if related_filter is not None:
        href += f"&related_run_id={quote(related_filter)}"
    related = run.get("related_run_id")
    related_cell = (
        f'<a href="?related_run_id={quote(str(related))}"><code>'
        f"{html.escape(str(related))}</code></a>"
        if related
        else "-"
    )
    return (
        "<tr>"
        f"<td><code>{html.escape(run_id)}</code></td>"
        f"<td>{related_cell}</td>"
        f"<td>{html.escape(workflow or '-')}</td>"
        f"<td>{html.escape(status or '-')}</td>"
        f"<td>{html.escape(timestamp or '-')}</td>"
        f"<td>{html.escape(usage)}</td>"
        f'<td><a href="{href}">open</a></td>'
        "</tr>"
    )


def _index_usage_label(total_tokens: object, metered: object, notional: object) -> str:
    parts = []
    if total_tokens:
        parts.append(f"{total_tokens} tokens")
    if metered is not None:
        parts.append(f"metered ${float(metered):.4f}")
    if notional is not None:
        parts.append(f"notional ${float(notional):.4f}")
    return " / ".join(parts) if parts else "-"


def _segment_markers(segment: Any) -> str:
    """Resolution evidence for a segment card: timeout route / durable registration.

    NOT status inference (M11 keeps this): each marker shows a decision the CURRENT engine
    records — ``wait:timeout_route`` (nodes/human.py), ``wait:registered``/``_reused``
    (executor wait registration), ``machine:resumed`` (executor resume) — and the card's
    status itself comes from the typed v2 meta, never from these strings."""

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
    return "\n" + load_asset_text("observation-base.css")


def _rich_graph_css() -> str:
    return "\n" + load_asset_text("rich-graph.css")


def _rich_graph_html(
    definition: WorkflowDefinition,
    graph: ObservationGraph,
    *,
    detail_href_for: DetailHrefFor | None = None,
) -> str:
    data = _observation_view_data(definition, graph, detail_href_for=detail_href_for)
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


def _observation_view_data(
    definition: WorkflowDefinition,
    graph: ObservationGraph,
    *,
    detail_href_for: DetailHrefFor | None = None,
) -> dict[str, Any]:
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
            detail_view(graph.details[ref], detail_href_for)
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
    return seal_observation_view_data({
        "workflow_id": graph.workflow_id,
        "run_id": graph.run_id,
        "canvas": canvas,
        "nodes": nodes,
        "transitions": transitions,
    })


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
    if window is not None:
        soft = _nonnegative_finite_number(window.get("soft_timeout_s"))
        hard = _nonnegative_finite_number(window.get("hard_timeout_s"))
        parts = [
            *(["soft " + f"{soft:g}s"] if soft is not None else []),
            *(["hard " + f"{hard:g}s"] if hard is not None else []),
        ]
        label = f"window: {' / '.join(parts)}" if parts else ""
        clamps = window.get("clamps")
        clamps = (
            [item for item in clamps if isinstance(item, str) and item.strip()]
            if isinstance(clamps, list)
            else []
        )
        if label and clamps:
            label += f" (clamped: {', '.join(str(c) for c in clamps)})"
        enforcement = window.get("enforcement")
        if label and isinstance(enforcement, str) and enforcement.strip():
            label += f" [{enforcement}]"
        if label:
            metrics.append(label)
    if process_bound is not None:
        process_parts: list[str] = []
        for key, name in (
            ("work_timeout_s", "work"),
            ("kill_grace_s", "cleanup"),
            ("settle_reserve_s", "settle"),
        ):
            value = _nonnegative_finite_number(process_bound.get(key))
            if value is not None:
                process_parts.append(f"{name} {value:g}s")
        label = f"process: {' / '.join(process_parts)}" if process_parts else ""
        headroom = _nonnegative_finite_number(
            process_bound.get("cleanup_headroom_s")
        )
        if label and headroom is not None and headroom > 0:
            label += f" (reserved: {headroom:g}s)"
        elif headroom is not None and headroom > 0:
            label = f"process: reserved {headroom:g}s"
        source = process_bound.get("source")
        if label and isinstance(source, str) and source.strip():
            label += f" [{source}]"
        if label:
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


def _nonnegative_finite_number(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


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


def _css_token(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "_", value or "unknown")


def _json_script(value: Any) -> str:
    return (
        json.dumps(value, sort_keys=True, default=str)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _rich_graph_js() -> str:
    return "\n" + load_asset_text("rich-graph.js")


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


def _timeline_row(
    item: ObservationTimelineEntry,
    details: Mapping[str, ObservationDisplayDetail],
) -> str:
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
