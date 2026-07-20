"""Small standalone HTTP/SSE viewer for engine observability run bundles."""

from __future__ import annotations

import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, unquote, urlparse

from ai_workflow_viewer.event_source import EventSource, FileEventSource
from ai_workflow_viewer.projection import build_observation_graph
from ai_workflow_viewer.rendering import (
    INLINE_SAFE_MEDIA_TYPES,
    load_artifact_manifest,
    observation_graph_to_html,
    observation_group_to_html,
    observation_index_to_html,
    _encode_artifact_path,
)


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

    def html(self, run_id: Optional[str] = None, *, related_run_id: Optional[str] = None) -> str:
        selected_run_id = run_id or self.run_id
        if selected_run_id is None:
            runs = self.runs(related_run_id=related_run_id)
            if related_run_id is not None or len(runs) != 1:
                # a filter request always shows the (possibly empty) filtered chooser
                return self.index_html(runs, related_run_id=related_run_id)
            selected_run_id = str(runs[0]["run_id"])
        group = self._read_group(selected_run_id)
        if group is not None:
            # R3/F5: the SERVED page renders the whole logical run — suspension, resumes,
            # terminal evidence, and honest cross-attempt economics — not one segment.
            # Q4.2: artifact links route through /artifact/ so archived evidence is
            # clickable over HTTP, not a dead absolute filesystem path.
            run_key = str(selected_run_id)
            return observation_group_to_html(
                group,
                title=self.title,
                artifact_href_for=lambda seg_path: (
                    "/artifact/"
                    + _encode_artifact_path(f"{run_key}/{Path(seg_path).name}")
                ),
            )
        run = self.source.read(selected_run_id)
        graph = build_observation_graph(
            run.definition,
            run.trace_events,
            run.usage_events,
            run.details,
            run_id=run.run_id,
        )
        return observation_graph_to_html(
            run.definition,
            graph,
            title=self.title,
            usage_events=run.usage_events,
        )

    def _read_group(self, run_id: Optional[str]):
        """Grouped read when the source supports it; None falls back to single-bundle.
        A corrupt group stays LOUD — only the absence of the grouped API or of the run
        falls back, never a lineage error."""

        read_group = getattr(self.source, "read_group", None)
        if not callable(read_group) or run_id is None:
            return None
        try:
            return read_group(str(run_id))
        except FileNotFoundError:
            return None

    def index_html(
        self, runs: Optional[list[dict]] = None, *, related_run_id: Optional[str] = None
    ) -> str:
        selected = runs if runs is not None else self.runs(related_run_id=related_run_id)
        return observation_index_to_html(
            selected,
            title=self.title or "Workflow observations",
            related_run_id=related_run_id,
        )

    def runs(self, *, related_run_id: Optional[str] = None) -> list[dict]:
        # R3/F5: one row per LOGICAL run (segments collapse), falling back to the flat
        # per-bundle listing only when the source has no grouped surface.
        list_groups = getattr(self.source, "list_groups", None)
        if callable(list_groups):
            return list_groups(related_run_id=related_run_id)
        list_runs = getattr(self.source, "list_runs", None)
        if not callable(list_runs):
            return []
        return list_runs()

    def event_records(self, run_id: Optional[str] = None) -> list[dict]:
        group = self._read_group(run_id or self.run_id)
        if group is not None:
            return [record.as_event_payload() for record in group.records]
        return [record.as_event_payload() for record in self.source.read(run_id or self.run_id).records]


def _artifact_response(
    viewer: JsonlObservationViewer, path: str
) -> Optional[tuple[bytes, str]]:
    """Resolve ``/artifact/<run_id>/<segment_dir>/<bundle_path...>`` to file bytes.

    Only files LISTED as copied in that segment's ``artifacts.json`` manifest are
    served, and only after proving the resolved target stays inside the segment
    directory — the manifest is the allow-list, never the raw filesystem.
    Returns ``None`` (→ 404) for anything else.
    """

    parts = path.split("/", 3)  # ["", "artifact", <run>, "<seg>/<bundle_path...>"]
    if len(parts) < 4 or "/" not in parts[3]:
        return None
    run_id = unquote(parts[2])
    segment_name, raw_bundle_path = parts[3].split("/", 1)
    segment_name = unquote(segment_name)
    # hrefs percent-encode each segment (spaces/#/%/unicode survive browsers); decode
    # BEFORE comparing against the raw manifest value
    bundle_path = "/".join(unquote(part) for part in raw_bundle_path.split("/"))
    try:
        group = viewer._read_group(run_id)
    except FileNotFoundError:
        # A6: ONLY absence is a 404 — group-lineage corruption stays LOUD (it raises here,
        # matching the HTML route for the same run) instead of masquerading as "not found".
        return None
    if group is None:
        return None
    for segment in [*group.segments, *getattr(group, "non_canonical", [])]:
        seg_dir = Path(segment.path)
        if seg_dir.name != segment_name:
            continue
        # A3: the shared loader is the ONE manifest contract — non-list/non-dict shapes
        # return the unreadable sentinel (→ 404) instead of raising AttributeError here.
        entries = load_artifact_manifest(seg_dir)
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if entry.get("copied") and entry.get("bundle_path") == bundle_path:
                target = (seg_dir / bundle_path).resolve()
                if not target.is_relative_to(seg_dir.resolve()) or not target.is_file():
                    return None
                return target.read_bytes(), str(
                    entry.get("media_type") or "application/octet-stream"
                )
        return None
    return None


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
            query = parse_qs(parsed.query)
            run_id = _request_run_id(parsed.path, query)
            related = (query.get("related_run_id") or [None])[0]
            if parsed.path.startswith("/events"):
                # C2-3: the stream commits 200 only AFTER the strict read succeeds — a
                # pre-v2/corrupt/missing run answers with the loud plain error, never a
                # success status followed by a dropped connection.
                try:
                    initial = viewer.event_records(run_id=run_id)
                except FileNotFoundError as exc:
                    _write_plain_error(self, 404, exc)
                    return
                except ValueError as exc:
                    _write_plain_error(self, 500, exc)
                    return
                _write_sse(self, viewer, run_id=run_id, initial=initial)
                return
            if parsed.path.startswith("/artifact/"):
                # C2-3: artifact resolution shares the strict error door — absence is 404,
                # contract violations (pre-v2, corrupt lineage, malformed meta) are the loud
                # 500 with the raising contract's message.
                try:
                    resolved = _artifact_response(viewer, parsed.path)
                except FileNotFoundError as exc:
                    _write_plain_error(self, 404, exc)
                    return
                except ValueError as exc:
                    _write_plain_error(self, 500, exc)
                    return
                if resolved is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data, media_type = resolved
                self.send_response(200)
                if media_type in INLINE_SAFE_MEDIA_TYPES:
                    # inert raster formats may render inline
                    self.send_header("Content-Type", media_type)
                    self.send_header("Content-Disposition", "inline")
                else:
                    # ACTIVE/unknown types (HTML, SVG, ...) must never execute in the
                    # viewer origin: force download as opaque bytes
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", "attachment")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "sandbox")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            try:
                body = viewer.html(run_id=run_id, related_run_id=related).encode("utf-8")
            except FileNotFoundError as exc:
                _write_plain_error(self, 404, exc)
                return
            except ValueError as exc:
                # M10 loud door: an unsupported (pre-v2) or malformed bundle, or corrupt
                # group lineage, answers with the loader's message — which names the
                # matching historical tag route — never a plausible partial page.
                _write_plain_error(self, 500, exc)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def _write_plain_error(handler: BaseHTTPRequestHandler, status: int, exc: Exception) -> None:
    """The loud non-page: status + the raising contract's own message as plain text."""

    body = str(exc).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_sse(
    handler: BaseHTTPRequestHandler,
    viewer: JsonlObservationViewer,
    *,
    run_id: Optional[str] = None,
    initial: Optional[list[dict]] = None,
) -> None:
    # ``initial`` is the PREFLIGHTED strict read (C2-3) — success is only claimed after it.
    records = initial if initial is not None else viewer.event_records(run_id=run_id)
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    import json

    for record in records:
        payload = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
        handler.wfile.write(b"data: " + payload + b"\n\n")
        handler.wfile.flush()
    for record in _poll_records(viewer, seconds=60, run_id=run_id, already_seen=len(records)):
        payload = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
        handler.wfile.write(b"data: " + payload + b"\n\n")
        handler.wfile.flush()


def _poll_records(
    viewer: JsonlObservationViewer,
    *,
    seconds: int,
    run_id: Optional[str] = None,
    already_seen: int = 0,
) -> Iterable[dict]:
    seen = already_seen
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        records = viewer.event_records(run_id=run_id)
        for record in records[seen:]:
            yield record
        seen = len(records)
        time.sleep(0.5)


def _request_run_id(path: str, query: dict[str, list[str]]) -> Optional[str]:
    query_run_id = (query.get("run_id") or [""])[0]
    if query_run_id:
        return query_run_id
    prefix = "/runs/"
    if path.startswith(prefix):
        return unquote(path[len(prefix):].strip("/"))
    return None
