"""Retention policy for finalized observation-bundle groups."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Optional

from ai_workflow_engine.observation_contract import (
    ABANDON_MARKER_NAME,
    COMMIT_MARKER_NAME,
    ObservationBundleMetaV2,
    load_bundle_meta_v2,
)


def prune_observation_bundles(
    base_dir: str | Path,
    retention_limit: int,
    *,
    evict_suspended_after_s: Optional[float] = None,
) -> None:
    """Keep only the newest finalized logical-run groups.

    In-flight suspended groups remain protected unless the caller explicitly configures
    age-based viewer-history eviction. This removes observation files only; it never owns
    wait scheduling or durable resume state.
    """

    base = Path(base_dir)
    if not base.exists():
        return
    keep = max(1, int(retention_limit))
    groups: dict[str, list[Path]] = {}
    for path in base.iterdir():
        if path.is_dir() and _is_finalized_bundle(path):
            groups.setdefault(_bundle_logical_run_id(path), []).append(path)
    prunable: list[tuple[tuple[str, float], list[Path]]] = []
    for paths in groups.values():
        entries = [(_bundle_meta(path), path) for path in paths]
        canonical = [
            (meta, path)
            for meta, path in entries
            if meta.status != "abandoned"
            and not (path / ABANDON_MARKER_NAME).exists()
            and (meta.attempt is None or (path / COMMIT_MARKER_NAME).exists())
        ] or entries
        newest_meta = max(
            (meta for meta, _ in canonical),
            key=lambda meta: (meta.segment_index, meta.timestamp),
        )
        if newest_meta.status == "requires_user_input":
            if evict_suspended_after_s is None:
                continue
            newest_ts = newest_meta.timestamp
            try:
                suspended_at = datetime.fromisoformat(
                    newest_ts.replace("Z", "+00:00")
                )
            except ValueError:
                continue
            age_s = (datetime.now(timezone.utc) - suspended_at).total_seconds()
            if age_s <= evict_suspended_after_s:
                continue
        prunable.append((max(_bundle_sort_key(path) for path in paths), paths))
    if len(prunable) <= keep:
        return
    prunable.sort(key=lambda entry: entry[0], reverse=True)
    for _, paths in prunable[keep:]:
        for path in paths:
            shutil.rmtree(path)


def _bundle_sort_key(path: Path) -> tuple[str, float]:
    meta_path = path / "meta.json"
    if meta_path.exists():
        timestamp = _bundle_meta(path).timestamp
        if timestamp:
            return (timestamp, path.stat().st_mtime)
    return ("", path.stat().st_mtime)


def _is_finalized_bundle(path: Path) -> bool:
    return (path / "meta.json").exists()


def _bundle_meta(path: Path) -> ObservationBundleMetaV2:
    return load_bundle_meta_v2(path)


def _bundle_logical_run_id(path: Path) -> str:
    return _bundle_meta(path).run_id


__all__ = ["prune_observation_bundles"]
