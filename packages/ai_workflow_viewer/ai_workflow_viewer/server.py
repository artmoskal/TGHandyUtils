"""Small standalone HTTP/SSE viewer for engine observability JSONL files."""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Optional

from ai_workflow_engine import (
    ObservationDetail,
    WorkflowDefinition,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    build_observation_graph,
    observation_graph_to_html,
)


class JsonlObservationViewer:
    """Load trace/usage/detail JSONL and render the generic observation HTML."""

    def __init__(
        self,
        definition: WorkflowDefinition,
        trace_path: str | Path,
        *,
        usage_path: str | Path | None = None,
        detail_path: str | Path | None = None,
        title: Optional[str] = None,
    ) -> None:
        self.definition = definition
        self.trace_path = Path(trace_path)
        self.usage_path = Path(usage_path) if usage_path is not None else None
        self.detail_path = Path(detail_path) if detail_path is not None else None
        self.title = title

    def trace_events(self) -> list[WorkflowTraceEvent]:
        return _load_jsonl(self.trace_path, WorkflowTraceEvent)

    def usage_events(self) -> list[WorkflowUsageEvent]:
        return _load_jsonl(self.usage_path, WorkflowUsageEvent) if self.usage_path else []

    def details(self) -> list[ObservationDetail]:
        return _load_jsonl(self.detail_path, ObservationDetail) if self.detail_path else []

    def html(self) -> str:
        graph = build_observation_graph(
            self.definition,
            self.trace_events(),
            self.usage_events(),
            self.details(),
        )
        return observation_graph_to_html(self.definition, graph, title=self.title)

    def event_records(self) -> list[dict]:
        records: list[dict] = []
        records.extend({"type": "trace", "record": event.model_dump()} for event in self.trace_events())
        records.extend({"type": "usage", "record": event.model_dump()} for event in self.usage_events())
        records.extend(
            {"type": "detail", "record": detail.model_dump(by_alias=True)}
            for detail in self.details()
        )
        return records


def serve_viewer(
    viewer: JsonlObservationViewer,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Serve the viewer. Caller owns ``serve_forever`` / shutdown lifecycle."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path.startswith("/events"):
                _write_sse(self, viewer)
                return
            body = viewer.html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def _load_jsonl(path: Path | None, model: type) -> list:
    if path is None or not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(model.model_validate_json(line))
    return records


def _write_sse(handler: BaseHTTPRequestHandler, viewer: JsonlObservationViewer) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    for record in _poll_records(viewer, seconds=60):
        payload = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
        handler.wfile.write(b"data: " + payload + b"\n\n")
        handler.wfile.flush()


def _poll_records(viewer: JsonlObservationViewer, *, seconds: int) -> Iterable[dict]:
    seen = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        records = viewer.event_records()
        for record in records[seen:]:
            yield record
        seen = len(records)
        time.sleep(0.5)
