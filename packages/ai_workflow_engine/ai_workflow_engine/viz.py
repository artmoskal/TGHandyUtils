"""Workflow visualization: WorkflowDefinition (+ optional run result) -> Mermaid / HTML.

Zero new dependencies: output is Mermaid text (renderable anywhere) or a self-contained HTML page
that loads Mermaid from a CDN at view time. With a ``WorkflowRunResult``, the diagram becomes an
execution view: visited nodes are colored by status and the taken branch labels are check-marked.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from ai_workflow_engine.workflow import END, WorkflowDefinition

_SHAPES = {
    "step": ("[", "]"),
    "branch": ("{", "}"),
    "evaluate": ("[/", "/]"),
    "fanout": ("[[", "]]"),
    "planner": ("[(", ")]"),
    "human": ("((", "))"),
    "subworkflow": ("[\\", "\\]"),
}


def _mid(node_id: str) -> str:
    return "n_" + re.sub(r"[^0-9A-Za-z_]", "_", node_id)


def workflow_to_mermaid(
    definition: WorkflowDefinition,
    result: Any = None,
    *,
    observation_graph: Any = None,
) -> str:
    """Render the state machine; with ``result``, overlay the executed path."""

    lines = ["flowchart TD"]
    statuses: dict[str, str] = {}
    branch_taken: dict[str, str] = {}
    observed_nodes = getattr(observation_graph, "nodes", {}) if observation_graph is not None else {}
    suspended: Optional[str] = None
    if result is not None:
        for record in getattr(result, "node_results", []):
            statuses[record.node_id] = record.status
            if record.branch_label:
                branch_taken[record.node_id] = record.branch_label
        snap = getattr(result, "snapshot", None)
        if snap is not None:
            suspended = getattr(snap, "suspended_node", None)
    for node_id, observed in observed_nodes.items():
        statuses[node_id] = getattr(observed, "status", statuses.get(node_id, "not_started"))

    declared_node_ids = {node.id for node in definition.nodes}
    for node in definition.nodes:
        left, right = _SHAPES.get(node.kind, ("[", "]"))
        label = _node_display_label(node)
        if node.kind != "step":
            label = f"{label}\n«{node.kind}»"
        observed = observed_nodes.get(node.id) if hasattr(observed_nodes, "get") else None
        if observed is not None:
            usage = _observed_usage_label(observed)
            if usage:
                label = f"{label}\n{usage}"
        lines.append(f'    {_mid(node.id)}{left}"{label}"{right}')
    for node_id, observed in observed_nodes.items():
        if node_id in declared_node_ids:
            continue
        kind = getattr(observed, "kind", "external")
        label = f"{node_id}\n«{kind}»"
        usage = _observed_usage_label(observed)
        if usage:
            label = f"{label}\n{usage}"
        lines.append(f'    {_mid(node_id)}["{label}"]')
    lines.append(f'    {_mid(END)}(("END"))')

    for t in definition.transitions:
        src, dst = _mid(t.source), _mid(t.target)
        if t.policy == "decision":
            bound = f" \u27f2\u2264{t.max_traversals}" if t.max_traversals else ""
            taken = branch_taken.get(t.source) == t.label
            text = f"{t.label}{bound}" + (" \u2713" if taken else "")
            lines.append(f"    {src} -->|{text}| {dst}")
            if t.on_exhausted != "fail":
                esc = next(
                    (s for s in definition.transitions
                     if s.source == t.source and s.label == t.on_exhausted and s.policy == "decision"),
                    None,
                )
                if esc is not None:
                    lines.append(
                        f"    {src} -.->|{t.label} exhausted \u21d2 {t.on_exhausted}| {_mid(esc.target)}"
                    )
        elif t.policy == "on_accept":
            lines.append(f"    {src} -->|accept| {dst}")
        elif t.policy == "on_reject":
            bound = f" \u2264{t.max_traversals}" if t.max_traversals else ""
            lines.append(f"    {src} -.->|{t.label}{bound}| {dst}")
        else:
            # the evaluate node's sequential continuation is already drawn as its accept route
            if definition.node(t.source).kind == "evaluate":
                continue
            lines.append(f"    {src} --> {dst}")

    for node in definition.nodes:
        if node.kind != "evaluate":
            continue
        fb = (
            getattr(node.on_reject, "capability", None)
            if getattr(node.on_reject, "kind", "") == "fallback"
            else None
        ) or node.fallback_capability
        if fb:
            fb_id = _mid(f"fb_{node.id}")
            lines.append(f'    {fb_id}(["{fb}"]):::fallback')
            lines.append(f"    {_mid(node.id)} -.->|fallback| {fb_id}")

    lines.append("    classDef ok fill:#c8e6c9,stroke:#2e7d32;")
    lines.append("    classDef fail fill:#ffcdd2,stroke:#c62828;")
    lines.append("    classDef part fill:#fff9c4,stroke:#f9a825;")
    lines.append("    classDef fallback fill:#eeeeee,stroke:#9e9e9e,stroke-dasharray: 3 3;")
    lines.append("    classDef susp fill:#bbdefb,stroke:#1565c0,stroke-width:3px;")
    for node_id, status in statuses.items():
        if node_id == suspended:
            continue  # the suspension marker below wins
        cls = _status_class(status)
        lines.append(f"    class {_mid(node_id)} {cls};")
    if suspended:
        lines.append(f"    class {_mid(suspended)} susp;")
    return "\n".join(lines)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body>
<h2>{title}</h2>
<pre class="mermaid">
{mermaid}
</pre>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{ startOnLoad: true }});
</script>
</body></html>
"""


def workflow_to_html(
    definition: WorkflowDefinition,
    result: Any = None,
    title: Optional[str] = None,
    *,
    observation_graph: Any = None,
) -> str:
    return _HTML_TEMPLATE.format(
        title=title or f"Workflow: {definition.workflow_id}",
        mermaid=workflow_to_mermaid(
            definition,
            result,
            observation_graph=observation_graph,
        ).replace("\\n", "\n"),
    )


def save_workflow_html(definition: WorkflowDefinition, path: str,
                       result: Any = None, title: Optional[str] = None,
                       *, observation_graph: Any = None) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        workflow_to_html(definition, result, title, observation_graph=observation_graph),
        encoding="utf-8",
    )
    return str(target)


def render_prompt_manifest(definition: WorkflowDefinition, registry: Any) -> str:
    """Static "structure + prompts" view of a *built* workflow: every node, the capability it
    resolves to, and the prompt template(s) that capability carries.

    Complements the other structure renderers — ``workflow_to_mermaid`` (graph shape) and
    ``render_machine_card`` (per-state transition semantics) — by surfacing the one thing they omit:
    the prompts. Read-only introspection over the registered handlers; no execution, no LLM calls, no
    raw bytes. Pass the engine's registry::

        print(render_prompt_manifest(workflow, engine.registry))

    For each node it reports the resolved ``effective_capability`` and, when the registered handler
    exposes them, its prompt templates: a ``StructuredLLMNode``'s ``prompt`` / ``static_prompt`` /
    ``dynamic_prompt`` and an agent capability's planner ``system_prompt``. Deterministic, tool, or
    dynamically-rendered capabilities are reported as having no static prompt. Engine-owned LLM workers
    emit runtime prompt/response observations themselves; use
    ``ai_workflow_engine.prompt_capture.PromptCapturingLLMClient`` only for external/BYO LLM calls that
    run outside those workers.
    """

    lines = [f"# Prompt manifest: {definition.workflow_id}", ""]
    for node in definition.nodes:
        capability = node.effective_capability()
        lines.append(f"## {node.id}  (kind={node.kind}, capability={capability or '—'})")
        handler = None
        if capability is not None:
            try:
                _spec, handler = registry.get(capability)
            except KeyError:
                lines.append("  (capability not registered)")
                lines.append("")
                continue
        prompts = _extract_prompts(handler)
        if not prompts:
            lines.append("  (no static prompt — deterministic / tool / dynamic capability)")
        for label, text in prompts:
            lines.append(f"  [{label}]")
            lines.extend(f"    {line}" for line in text.splitlines())
        lines.append("")
    return "\n".join(lines)


def _extract_prompts(handler: Any) -> list:
    """Best-effort, duck-typed prompt extraction from a registered capability handler.

    Returns ``[(label, template_text), ...]`` for the prompt-bearing shapes the engine ships
    (``StructuredLLMNode`` and agent capabilities) and ``[]`` for handlers with no static prompt.
    Never raises — unknown handler shapes simply yield nothing.
    """

    if handler is None:
        return []
    prompts = []
    for attr, label in (("static_prompt", "static"), ("dynamic_prompt", "dynamic"), ("prompt", "prompt")):
        template = getattr(getattr(handler, attr, None), "template", None)
        if isinstance(template, str) and template:
            prompts.append((label, template))
    system_prompt = getattr(getattr(handler, "planner", None), "system_prompt", None)
    if isinstance(system_prompt, str) and system_prompt:
        prompts.append(("agent_system", system_prompt))
    return prompts


def _status_class(status: str) -> str:
    if status in {"accepted", "completed", "valid", "answered", "provisional"}:
        return "ok"
    if status in {"partial", "running", "not_started"}:
        return "part"
    return "fail"


def _node_display_label(node: Any) -> str:
    title = getattr(node, "title", "") or node.id
    if title == node.id:
        return node.id
    return f"{title}\n{node.id}"


def _observed_usage_label(observed: Any) -> str:
    tokens = int(getattr(observed, "total_tokens", 0) or 0)
    metered = getattr(observed, "metered_usd", None)
    notional = getattr(observed, "notional_usd", None)
    if not tokens and metered is None and notional is None:
        return ""
    parts = []
    if tokens:
        parts.append(f"{tokens} tok")
    if metered is not None:
        parts.append(f"metered ${float(metered):.4f}")
    if notional is not None:
        parts.append(f"notional ${float(notional):.4f}")
    return " / ".join(parts)
