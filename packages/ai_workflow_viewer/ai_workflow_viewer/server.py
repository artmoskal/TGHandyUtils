"""Small standalone HTTP/SSE viewer for engine observability run bundles."""

from __future__ import annotations

import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Mapping, Optional
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from ai_workflow_viewer.artifact_access import (
    encode_artifact_path as _encode_artifact_path,
    read_verified_artifact,
    resolve_manifest_artifact,
)
from ai_workflow_viewer.detail_delivery import prepare_detail_delivery
from ai_workflow_viewer.event_source import EventSource, FileEventSource
from ai_workflow_viewer.projection import build_observation_graph
from ai_workflow_viewer.rendering import (
    INLINE_SAFE_MEDIA_TYPES,
    observation_graph_to_html,
    observation_group_to_html,
    observation_index_to_html,
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
                detail_href_for=_served_detail_hrefs(group),
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
        artifact = resolve_manifest_artifact(seg_dir, bundle_path)
        if artifact is None:
            return None
        return read_verified_artifact(artifact), artifact.media_type
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
                # pre-v3/corrupt/missing run answers with the loud plain error, never a
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
            if parsed.path.startswith("/detail/"):
                _write_detail_response(
                    self,
                    viewer.source,
                    path=parsed.path,
                    query=query,
                )
                return
            if parsed.path.startswith("/artifact/"):
                # C2-3: artifact resolution shares the strict error door — absence is 404,
                # contract violations (pre-v3, corrupt lineage, malformed meta) are the loud
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
                # M10 loud door: an unsupported (pre-v3) or malformed bundle, or corrupt
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


def _write_detail_response(
    handler: BaseHTTPRequestHandler,
    source: object,
    *,
    path: str,
    query: Mapping[str, list[str]],
) -> None:
    """Resolve, validate, and write one bounded detail response."""

    try:
        detail_run_id, segment_id, detail_id = _detail_route(path)
        delivery = prepare_detail_delivery(
            source,
            run_id=detail_run_id,
            segment_id=segment_id,
            detail_id=detail_id,
            invocation_id=(query.get("invocation_id") or [None])[0],
            mode=(query.get("mode") or ["preview"])[0],
        )
    except FileNotFoundError as exc:
        _write_plain_error(handler, 404, exc)
        return
    except ValueError as exc:
        _write_plain_error(handler, 500, exc)
        return

    handler.send_response(200)
    handler.send_header("Content-Type", delivery.content_type)
    handler.send_header("Content-Disposition", delivery.content_disposition)
    handler.send_header("Content-Security-Policy", "sandbox")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Observation-SHA256", delivery.sha256)
    handler.send_header("X-Observation-Byte-Length", str(delivery.body_length))
    handler.send_header(
        "X-Observation-Truncated", "true" if delivery.truncated else "false"
    )
    handler.send_header("Content-Length", str(delivery.content_length))
    handler.end_headers()
    for chunk in delivery.chunks:
        handler.wfile.write(chunk)


def _detail_route(path: str) -> tuple[str, str, str]:
    parts = path.split("/")
    if len(parts) != 5 or parts[:2] != ["", "detail"] or not all(parts[2:]):
        raise ValueError(
            "observation detail route must be /detail/<run>/<segment>/<detail>"
        )
    return tuple(unquote(part) for part in parts[2:])


def _served_detail_hrefs(group):
    locations = {
        detail.detail_id: (segment.segment_id, detail.invocation_id)
        for segment in group.segments
        for detail in segment.data.details
    }

    def hrefs(detail) -> dict[str, str]:
        location = locations.get(detail.detail_id)
        if location is None:
            return {}
        segment_id, invocation_id = location
        path = "/detail/{}/{}/{}".format(
            quote(str(group.run_id), safe=""),
            quote(str(segment_id), safe=""),
            quote(str(detail.detail_id), safe=""),
        )
        identity = {"invocation_id": invocation_id} if invocation_id is not None else {}
        return {
            mode: f"{path}?{urlencode({**identity, 'mode': mode})}"
            for mode in ("preview", "download")
        }

    return hrefs


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
