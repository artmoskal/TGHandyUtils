"""Bounded static export for one already-validated logical observation group."""

from __future__ import annotations

import hashlib
import html
from pathlib import Path
from typing import Any

from ai_workflow_engine import ObservationReader
from ai_workflow_viewer.detail_delivery import PREVIEW_BYTES
from ai_workflow_viewer.rendering import observation_group_to_html


def export_observation_group(group: Any, target_dir: str | Path) -> Path:
    """Write a compact index, bounded detail pages, and exact byte sidecars."""

    target = Path(target_dir)
    if target.is_symlink() or (target.exists() and any(target.iterdir())):
        raise ValueError("static observation export target must be a new or empty real directory")
    target.mkdir(parents=True, exist_ok=True)
    details_dir = target / "details"
    details_dir.mkdir()
    links: dict[str, dict[str, str]] = {}
    occurrence = 0

    for segment in group.segments:
        reader = ObservationReader(segment.path)
        for envelope in segment.data.details:
            occurrence += 1
            detail = reader.get_detail(
                envelope.detail_id,
                invocation_id=envelope.invocation_id,
            )
            body_name = f"body-{detail.body.sha256}.bin"
            body_path = details_dir / body_name
            preview = _write_exact_body(reader, detail, body_path)
            page_name = f"detail-{occurrence:06d}-{detail.body.sha256[:12]}.html"
            page_path = details_dir / page_name
            page_path.write_text(
                _detail_page(detail, preview=preview, body_name=body_name),
                encoding="utf-8",
            )
            links[detail.detail_id] = {
                "page": f"details/{page_name}",
                "download": f"details/{body_name}",
            }

    index_path = target / "index.html"
    index_path.write_text(
        observation_group_to_html(
            group,
            detail_href_for=lambda detail: links.get(detail.detail_id, {}),
        ),
        encoding="utf-8",
    )
    return index_path


def _write_exact_body(reader: ObservationReader, detail, path: Path) -> bytes:
    if path.exists():
        with path.open("rb") as source:
            return source.read(PREVIEW_BYTES)
    temp = path.with_suffix(".tmp")
    digest = hashlib.sha256()
    byte_length = 0
    preview = bytearray()
    try:
        with temp.open("xb") as target:
            for chunk in reader.iter_body_bytes(detail):
                digest.update(chunk)
                byte_length += len(chunk)
                if len(preview) < PREVIEW_BYTES:
                    preview.extend(chunk[: PREVIEW_BYTES - len(preview)])
                target.write(chunk)
        if digest.hexdigest() != detail.body.sha256 or byte_length != detail.body.byte_length:
            raise ValueError(
                f"static export body {detail.detail_id!r} disagrees with its envelope"
            )
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return bytes(preview)


def _detail_page(detail, *, preview: bytes, body_name: str) -> str:
    text = preview.decode("utf-8", errors="replace")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Observation detail {html.escape(detail.detail_id)}</title>
<style>body{{font-family:system-ui;margin:24px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}code{{background:#f0f4f8;padding:2px 4px}}</style>
</head><body>
<h1>Observation detail <code>{html.escape(detail.detail_id)}</code></h1>
<p>Kind: {html.escape(detail.kind)} · Content type: {html.escape(detail.content_type)} · SHA-256: <code>{detail.body.sha256}</code></p>
<p>Preview limited to {len(preview)} of {detail.body.byte_length} canonical bytes. <a href="{body_name}" download>Download exact bytes</a>.</p>
<pre>{html.escape(text)}</pre>
</body></html>"""


__all__ = ["export_observation_group"]
