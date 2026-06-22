"""Small standalone HTTP/SSE viewer for engine observability run bundles."""

from __future__ import annotations

import html
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

from ai_workflow_viewer.event_source import EventSource, FileEventSource
from ai_workflow_viewer.observability import build_observation_graph, observation_graph_to_html


class JsonlObservationViewer:
    """Load an observation run bundle and render the generic observation HTML."""

    def __init__(
        self,
        source: EventSource,
        *,
        title: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        self.source = source
        self.title = title
        self.run_id = run_id

    @classmethod
    def from_run_bundle(
        cls,
        path: str | Path,
        *,
        title: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> "JsonlObservationViewer":
        return cls(FileEventSource(path), title=title, run_id=run_id)

    def html(self, run_id: Optional[str] = None) -> str:
        selected_run_id = run_id or self.run_id
        if selected_run_id is None:
            runs = self.runs()
            if len(runs) != 1:
                return self.index_html(runs)
        run = self.source.read(selected_run_id)
        graph = build_observation_graph(
            run.definition,
            run.trace_events,
            run.usage_events,
            run.details,
            run_id=run.run_id,
        )
        return observation_graph_to_html(run.definition, graph, title=self.title)

    def index_html(self, runs: Optional[list[dict]] = None) -> str:
        rows = "\n".join(_run_row(run) for run in (runs if runs is not None else self.runs()))
        title = self.title or "Workflow observations"
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2933; background: #ffffff; }}
h1 {{ font-size: 24px; margin: 0 0 16px; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; }}
th, td {{ border-bottom: 1px solid #d9e2ec; padding: 8px 10px; text-align: left; vertical-align: top; }}
th {{ color: #52606d; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
code {{ background: #f0f4f8; border-radius: 4px; padding: 1px 4px; }}
.muted {{ color: #627d98; }}
.empty {{ border: 1px solid #d9e2ec; border-radius: 8px; padding: 18px; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
{f'<table><thead><tr><th>Run</th><th>Workflow</th><th>Status</th><th>Timestamp</th><th>Usage</th><th>Open</th></tr></thead><tbody>{rows}</tbody></table>' if rows else '<div class="empty muted">No observation runs found.</div>'}
</body>
</html>
"""

    def runs(self) -> list[dict]:
        list_runs = getattr(self.source, "list_runs", None)
        if not callable(list_runs):
            return []
        return list_runs()

    def event_records(self, run_id: Optional[str] = None) -> list[dict]:
        return [record.as_event_payload() for record in self.source.read(run_id or self.run_id).records]


def serve_viewer(
    viewer: JsonlObservationViewer,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Serve the viewer. Caller owns ``serve_forever`` / shutdown lifecycle."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            run_id = _request_run_id(parsed.path, parse_qs(parsed.query))
            if parsed.path.startswith("/events"):
                _write_sse(self, viewer, run_id=run_id)
                return
            body = viewer.html(run_id=run_id).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def _write_sse(handler: BaseHTTPRequestHandler, viewer: JsonlObservationViewer, *, run_id: Optional[str] = None) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    for record in _poll_records(viewer, seconds=60, run_id=run_id):
        import json

        payload = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
        handler.wfile.write(b"data: " + payload + b"\n\n")
        handler.wfile.flush()


def _poll_records(viewer: JsonlObservationViewer, *, seconds: int, run_id: Optional[str] = None) -> Iterable[dict]:
    seen = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        records = viewer.event_records(run_id=run_id)
        for record in records[seen:]:
            yield record
        seen = len(records)
        time.sleep(0.5)


def _run_row(run: dict) -> str:
    run_id = str(run.get("run_id") or "")
    workflow = str(run.get("workflow_id") or run.get("workflow") or "")
    status = str(run.get("status") or "")
    timestamp = str(run.get("timestamp") or "")
    total_tokens = run.get("total_tokens")
    metered = run.get("metered_usd")
    notional = run.get("notional_usd")
    usage = _usage_label(total_tokens, metered, notional)
    href = f"?run_id={quote(run_id)}"
    return (
        "<tr>"
        f"<td><code>{html.escape(run_id)}</code></td>"
        f"<td>{html.escape(workflow or '-')}</td>"
        f"<td>{html.escape(status or '-')}</td>"
        f"<td>{html.escape(timestamp or '-')}</td>"
        f"<td>{html.escape(usage)}</td>"
        f'<td><a href="{href}">open</a></td>'
        "</tr>"
    )


def _usage_label(total_tokens: object, metered: object, notional: object) -> str:
    parts = []
    if total_tokens:
        parts.append(f"{total_tokens} tokens")
    if metered is not None:
        parts.append(f"metered ${float(metered):.4f}")
    if notional is not None:
        parts.append(f"notional ${float(notional):.4f}")
    return " / ".join(parts) if parts else "-"


def _request_run_id(path: str, query: dict[str, list[str]]) -> Optional[str]:
    query_run_id = (query.get("run_id") or [""])[0]
    if query_run_id:
        return query_run_id
    prefix = "/runs/"
    if path.startswith(prefix):
        return unquote(path[len(prefix):].strip("/"))
    return None
