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


def workflow_to_mermaid(definition: WorkflowDefinition, result: Any = None) -> str:
    """Render the state machine; with ``result``, overlay the executed path."""

    lines = ["flowchart TD"]
    statuses: dict[str, str] = {}
    branch_taken: dict[str, str] = {}
    if result is not None:
        for record in getattr(result, "node_results", []):
            statuses[record.node_id] = record.status
            if record.branch_label:
                branch_taken[record.node_id] = record.branch_label

    for node in definition.nodes:
        left, right = _SHAPES.get(node.kind, ("[", "]"))
        label = node.id if node.kind == "step" else f"{node.id}\n«{node.kind}»"
        lines.append(f'    {_mid(node.id)}{left}"{label}"{right}')
    lines.append(f'    {_mid(END)}(("END"))')

    for edge in definition.edges:
        src, dst = _mid(edge.source), _mid(edge.target)
        if edge.conditional and edge.label:
            taken = branch_taken.get(edge.source) == edge.label
            label = f"{edge.label} ✓" if taken else edge.label
            lines.append(f"    {src} -->|{label}| {dst}")
        else:
            lines.append(f"    {src} --> {dst}")

    for node in definition.nodes:
        if node.kind == "evaluate" and node.on_reject is not None:
            kind = getattr(node.on_reject, "kind", "")
            if kind in ("retrace", "replan"):
                target = getattr(node.on_reject, "target", None)
                if target:
                    lines.append(f"    {_mid(node.id)} -.->|{kind}| {_mid(target)}")
            elif kind == "retry":
                cap = node.target_capability or "previous"
                lines.append(f'    {_mid(node.id)} -.->|retry ≤{getattr(node.on_reject, "max_attempts", 1)}| {_mid(node.id)}')
            elif kind == "fallback":
                fb = getattr(node.on_reject, "capability", None) or node.fallback_capability
                if fb:
                    fb_id = _mid(f"fb_{node.id}")
                    lines.append(f'    {fb_id}(["{fb}"]):::fallback')
                    lines.append(f"    {_mid(node.id)} -.->|fallback| {fb_id}")

    lines.append("    classDef ok fill:#c8e6c9,stroke:#2e7d32;")
    lines.append("    classDef fail fill:#ffcdd2,stroke:#c62828;")
    lines.append("    classDef part fill:#fff9c4,stroke:#f9a825;")
    lines.append("    classDef fallback fill:#eeeeee,stroke:#9e9e9e,stroke-dasharray: 3 3;")
    for node_id, status in statuses.items():
        cls = "ok" if status == "accepted" else ("part" if status == "partial" else "fail")
        lines.append(f"    class {_mid(node_id)} {cls};")
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


def workflow_to_html(definition: WorkflowDefinition, result: Any = None,
                     title: Optional[str] = None) -> str:
    return _HTML_TEMPLATE.format(
        title=title or f"Workflow: {definition.workflow_id}",
        mermaid=workflow_to_mermaid(definition, result).replace("\\n", "\n"),
    )


def save_workflow_html(definition: WorkflowDefinition, path: str,
                       result: Any = None, title: Optional[str] = None) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(workflow_to_html(definition, result, title), encoding="utf-8")
    return str(target)
