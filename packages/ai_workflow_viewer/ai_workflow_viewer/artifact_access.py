"""Manifest-approved access to archived observation artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ai_workflow_engine.observation_bundle import ARTIFACT_MANIFEST_NAME


_MANIFEST_ABSENT = object()
_MANIFEST_UNREADABLE = object()


@dataclass(frozen=True)
class ApprovedArtifact:
    """One copied manifest row resolved inside its owning segment."""

    entry: dict[str, Any]
    bundle_path: str
    source_path: Path
    media_type: str


def load_artifact_manifest(bundle_dir: str | Path) -> Any:
    """Return a validated row list or a sentinel for absent/unreadable manifests."""

    manifest_path = Path(bundle_dir) / ARTIFACT_MANIFEST_NAME
    if not manifest_path.exists():
        return _MANIFEST_ABSENT
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _MANIFEST_UNREADABLE
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        return _MANIFEST_UNREADABLE
    return entries


def encode_artifact_path(relative_path: str) -> str:
    """Percent-encode each path component without changing manifest identity."""

    return "/".join(quote(component, safe="") for component in relative_path.split("/"))


def resolve_manifest_artifact(
    bundle_dir: str | Path,
    bundle_path: str,
) -> ApprovedArtifact | None:
    """Resolve one copied allow-listed artifact without escaping its segment."""

    entries = load_artifact_manifest(bundle_dir)
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if entry.get("copied") and entry.get("bundle_path") == bundle_path:
            return _resolve_entry(Path(bundle_dir), entry)
    return None


def manifest_artifacts_for_export(bundle_dir: str | Path) -> tuple[ApprovedArtifact, ...]:
    """Return every copied artifact, failing loudly on malformed approved rows."""

    entries = load_artifact_manifest(bundle_dir)
    if entries is _MANIFEST_ABSENT:
        return ()
    if entries is _MANIFEST_UNREADABLE:
        raise ValueError(f"artifact manifest under {str(bundle_dir)!r} is unreadable")
    approved: list[ApprovedArtifact] = []
    for entry in entries:
        if not entry.get("copied"):
            continue
        artifact = _resolve_entry(Path(bundle_dir), entry)
        if artifact is None:
            raise ValueError(
                f"copied artifact {entry.get('artifact_id')!r} is not a valid "
                "manifest-approved segment file"
            )
        approved.append(artifact)
    return tuple(approved)


def _resolve_entry(bundle_dir: Path, entry: dict[str, Any]) -> ApprovedArtifact | None:
    bundle_path = entry.get("bundle_path")
    if not isinstance(bundle_path, str) or not bundle_path.strip():
        return None
    segment_root = bundle_dir.resolve()
    target = (bundle_dir / bundle_path).resolve()
    if not target.is_relative_to(segment_root) or not target.is_file():
        return None
    return ApprovedArtifact(
        entry=entry,
        bundle_path=bundle_path,
        source_path=target,
        media_type=str(entry.get("media_type") or "application/octet-stream"),
    )


__all__ = [
    "ApprovedArtifact",
    "encode_artifact_path",
    "load_artifact_manifest",
    "manifest_artifacts_for_export",
    "resolve_manifest_artifact",
]
