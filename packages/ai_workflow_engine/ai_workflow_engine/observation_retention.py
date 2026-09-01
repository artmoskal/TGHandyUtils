"""Retention policy for finalized observation-bundle groups."""

from __future__ import annotations

from pathlib import Path
import shutil

from ai_workflow_engine.observation_contract import (
    ABANDON_MARKER_NAME,
    COMMIT_MARKER_NAME,
    ObservationBundleMetaV4,
    load_bundle_meta_v4,
)


def prune_observation_bundles(
    base_dir: str | Path,
    retention_limit: int,
) -> None:
    """Keep only the newest finalized logical-run groups.

    In-flight suspended groups are never retention candidates: deleting their run-scoped
    store would make a later resume either fail or create an incomplete observation group.
    """

    base = Path(base_dir)
    if not base.exists():
        return
    keep = max(1, int(retention_limit))
    groups = filter(None, (_load_finalized_group(path) for path in base.iterdir()))
    prunable = [entry for group in groups if (entry := _prunable_entry(*group)) is not None]
    if len(prunable) <= keep:
        return
    prunable.sort(key=lambda entry: entry[0], reverse=True)
    for _, run_path in prunable[keep:]:
        shutil.rmtree(run_path)


def _load_finalized_group(
    run_path: Path,
) -> tuple[Path, list[tuple[Path, ObservationBundleMetaV4]]] | None:
    if not run_path.is_dir() or run_path.is_symlink():
        return None
    segments_root = run_path / "segments"
    if not segments_root.is_dir() or segments_root.is_symlink():
        return None
    stored: list[tuple[Path, ObservationBundleMetaV4]] = []
    try:
        for segment_path in segments_root.iterdir():
            if segment_path.is_dir() and _is_finalized_bundle(segment_path):
                meta = _bundle_meta(segment_path)
                if meta.run_id != run_path.name:
                    raise ValueError("observation run directory identity disagrees with meta")
                stored.append((segment_path, meta))
    except Exception:
        # Corrupt or foreign-version evidence is preserved, never treated as prunable.
        return None
    return (run_path, stored) if stored else None


def _prunable_entry(
    run_path: Path,
    stored: list[tuple[Path, ObservationBundleMetaV4]],
) -> tuple[tuple[str, float], Path] | None:
    canonical = [entry for entry in stored if _is_canonical_attempt(*entry)] or stored
    newest_meta = max(
        (meta for _path, meta in canonical),
        key=lambda meta: (meta.segment_index, meta.timestamp),
    )
    if newest_meta.status == "requires_user_input":
        return None
    return (max(_bundle_sort_key(path) for path, _meta in stored), run_path)


def _is_canonical_attempt(path: Path, meta: ObservationBundleMetaV4) -> bool:
    return (
        meta.status != "abandoned"
        and not (path / ABANDON_MARKER_NAME).exists()
        and (meta.attempt is None or (path / COMMIT_MARKER_NAME).exists())
    )


def _bundle_sort_key(path: Path) -> tuple[str, float]:
    meta_path = path / "meta.json"
    if meta_path.exists():
        timestamp = _bundle_meta(path).timestamp
        if timestamp:
            return (timestamp, path.stat().st_mtime)
    return ("", path.stat().st_mtime)


def _is_finalized_bundle(path: Path) -> bool:
    return (path / "meta.json").exists()


def _bundle_meta(path: Path) -> ObservationBundleMetaV4:
    return load_bundle_meta_v4(path)


__all__ = ["prune_observation_bundles"]
