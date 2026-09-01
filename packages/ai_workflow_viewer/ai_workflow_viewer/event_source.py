"""Observation event sources consumed by the standalone viewer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ai_workflow_engine import (
    ObservationReader,
    load_bundle_meta_v4,
)
from ai_workflow_engine.observation_bundle import (
    ABANDON_MARKER_NAME as _ABANDON_MARKER,
    COMMIT_MARKER_NAME as _COMMIT_MARKER,
    assert_plain_identity,
    resolve_child_dir,
)
from ai_workflow_engine.observation_integrity import validate_provider_invocation_links
from ai_workflow_viewer.grouping import (
    assemble_observation_group,
    summarize_observation_groups,
)
from ai_workflow_viewer.observation_data import (
    ObservationGroupData,
    ObservationRecord,
    ObservationRunData,
    ObservationSegmentData,
)


class EventSource(Protocol):
    """Read ordered records for one observation run."""

    def read(self, run_id: str | None = None) -> ObservationRunData:
        """Return one run's definition and ordered source records."""


class FileEventSource:
    """Read a disk-backed observation run bundle."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path)

    def read(self, run_id: str | None = None) -> ObservationRunData:
        run_path = self._run_path(run_id)
        # ONE strict v4 reader; the viewer never guesses a layout or opens referenced bodies.
        reader = ObservationReader(run_path)
        meta = reader.meta
        if run_id is not None and meta.run_id != run_id:
            raise FileNotFoundError(
                f"Observation bundle at {run_path} belongs to run {meta.run_id!r}, "
                f"not {run_id!r} — the meta identity is the truth, never the directory name"
            )
        definition = reader.read_definition()
        trace_records = (
            list(_wrap_records(reader.iter_trace_events(), "trace"))
            if "trace" not in meta.incomplete_streams
            else list(_wrap_records(reader.iter_trace_prefix(), "trace"))
        )
        detail_records = (
            list(_wrap_records(reader.iter_detail_envelopes(), "detail"))
            if "detail" not in meta.incomplete_streams
            else list(_wrap_records(reader.iter_detail_prefix(), "detail"))
        )
        usage_records = (
            list(_wrap_records(reader.iter_usage_events(), "usage"))
            if "usage" not in meta.incomplete_streams
            else list(_wrap_records(reader.iter_usage_prefix(), "usage"))
        )
        reader.validate_record_counts(
            trace_count=len(trace_records),
            detail_count=len(detail_records),
            usage_count=len(usage_records),
        )
        records = [*trace_records, *detail_records, *usage_records]
        records.sort(key=_record_sort_key)
        _assert_sequence_sane(records)
        _validate_provider_evidence(meta, records)
        return ObservationRunData(
            run_id=meta.run_id,
            definition=definition,
            records=records,
            meta=meta,
        )

    def list_runs(self) -> list[dict]:
        entries = [_run_entry_or_corrupt(path) for path in _segment_paths(self.base_path)]
        entries.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return entries

    def read_group(self, logical_run_id: str) -> ObservationGroupData:
        """Read ALL finalized segments of one logical run and merge them honestly (W4.3).

        Single-bundle ``read()`` semantics are untouched — this is the explicit grouped
        surface. Lineage is LOGICAL (R1): the canonical chain is one committed attempt per
        contiguous index — physical parent pointers are gone. Corruption is LOUD: a gap in
        the canonical chain, two committed durable attempts at one ordinal, or definition
        digests that disagree with the actual files all raise instead of rendering a
        half-true merge.
        """

        scan_root = self.base_path
        if _is_run_bundle(scan_root):
            scan_root = ObservationReader(scan_root).run_root
        entries: list[ObservationSegmentData] = []
        abandoned_segment_ids: set[str] = set()
        for path in _segment_paths(scan_root):
            try:
                meta = load_bundle_meta_v4(path)
            except Exception as contract_error:
                if path.is_symlink():
                    raise
                if _raw_logical_run_id(path) == logical_run_id:
                    raise ValueError(
                        f"Observation group {logical_run_id!r} contains corrupt segment "
                        f"{path.name!r}: {contract_error}"
                    ) from contract_error
                continue
            if meta.run_id != logical_run_id:
                continue
            try:
                data = FileEventSource(path).read()  # per-segment sequence sanity runs here
            except Exception as contract_error:
                raise ValueError(
                    f"Observation group {logical_run_id!r} contains corrupt segment "
                    f"{path.name!r}: {contract_error}"
                ) from contract_error
            if meta.status == "abandoned" or (path / _ABANDON_MARKER).exists():
                abandoned_segment_ids.add(meta.segment_id)
            entries.append(
                ObservationSegmentData(
                    segment_id=meta.segment_id,
                    segment_index=meta.segment_index,
                    kind=meta.segment_kind,
                    status=meta.status,
                    path=str(path),
                    data=data,
                    attempt=meta.attempt,
                    # only DURABLE attempts have a separate commit fact (coordinator
                    # terminalization) — attempt-less segments are committed by finalize
                    committed=meta.attempt is None or (path / _COMMIT_MARKER).exists(),
                )
            )
        if not entries:
            raise FileNotFoundError(
                f"No finalized observation segments for logical run {logical_run_id!r} "
                f"under {self.base_path}"
            )
        return assemble_observation_group(
            logical_run_id,
            entries,
            abandoned_segment_ids=abandoned_segment_ids,
        )

    def reader_for_segment(self, run_id: str, segment_id: str) -> ObservationReader:
        """Open one exact segment without scanning or materializing its logical run."""

        assert_plain_identity(run_id, what="observation run id")
        assert_plain_identity(segment_id, what="observation segment id")
        if _is_run_bundle(self.base_path):
            segment_path = self.base_path
        elif (self.base_path / "segments").is_dir():
            segment_path = resolve_child_dir(
                self.base_path / "segments",
                segment_id,
                what="observation segment",
            )
        else:
            run_root = resolve_child_dir(
                self.base_path,
                run_id,
                what="observation logical run",
            )
            segment_path = resolve_child_dir(
                run_root / "segments",
                segment_id,
                what="observation segment",
            )
        if not (segment_path / "meta.json").is_file():
            raise FileNotFoundError(
                f"Observation segment {segment_id!r} for run {run_id!r} does not exist"
            )
        reader = ObservationReader(segment_path)
        if reader.meta.run_id != run_id or reader.meta.segment_id != segment_id:
            raise FileNotFoundError(
                f"Observation segment {segment_id!r} does not belong to run {run_id!r}"
            )
        return reader

    def list_groups(self, *, related_run_id: str | None = None) -> list[dict]:
        """One entry per LOGICAL run: segment count, newest status/timestamp (W4.3)."""

        grouped: dict[str, list[dict]] = {}
        for path in _segment_paths(self.base_path):
            entry = _run_entry_or_corrupt(path)
            attempt = entry.get("attempt")
            entry["_row"] = {
                "id": entry["segment_id"],
                "index": entry["segment_index"],
                "attempt": attempt,
                "committed": attempt is None or (path / _COMMIT_MARKER).exists(),
                "abandoned": entry["status"] == "abandoned"
                or (path / _ABANDON_MARKER).exists(),
                "timestamp": entry["timestamp"],
                "correlation": entry.get("correlation_id"),
                "entry": entry,
            }
            grouped.setdefault(str(entry["run_id"]), []).append(entry)
        return summarize_observation_groups(grouped, related_run_id=related_run_id)

    def _run_path(self, run_id: str | None) -> Path:
        # C2-1/C2GR-1: a caller-supplied run id (including HTTP query/path values) is joined
        # to the configured root through the ENGINE's safe resolver — path syntax is an
        # attack, and a symlinked child directory never becomes a bundle root. The CONFIGURED
        # base itself stays trusted (it may deliberately be a symlink).
        if run_id is not None:
            assert_plain_identity(str(run_id), what="run id")
        if _is_run_bundle(self.base_path):
            if run_id is not None and load_bundle_meta_v4(self.base_path).run_id != run_id:
                raise FileNotFoundError(
                    f"observation segment {self.base_path} does not belong to run {run_id!r}"
                )
            return self.base_path
        if run_id is None:
            logical_ids = {str(entry["run_id"]) for entry in self.list_runs()}
            if len(logical_ids) != 1:
                raise ValueError("Observation source contains multiple runs; pass run_id")
            run_id = logical_ids.pop()
        candidates = [
            path
            for path in _segment_paths(self.base_path)
            if _safe_segment_run_id(path) == run_id
        ]
        if not candidates:
            raise FileNotFoundError(f"No finalized observation segments for run {run_id!r}")
        return max(
            candidates,
            key=lambda path: (
                load_bundle_meta_v4(path).segment_index,
                load_bundle_meta_v4(path).timestamp,
                path.name,
            ),
        )


def _wrap_records(source, kind: str) -> list[ObservationRecord]:
    records = []
    for record in source:
        event_id = _record_identity(kind, record)
        records.append(
            ObservationRecord(
                type=kind,
                sequence=getattr(record, "sequence", None),
                event_id=str(event_id),
                record=record,
            )
        )
    return records


def _record_identity(kind: str, record) -> str:
    if kind == "detail":
        return str(getattr(record, "detail_id", None) or getattr(record, "event_id", None) or "")
    return str(getattr(record, "event_id", None) or "")


def _record_sort_key(record: ObservationRecord) -> tuple[int, str]:
    sequence = record.sequence
    return (sequence if sequence is not None else 10**12, record.event_id)


def _assert_sequence_sane(records: list[ObservationRecord]) -> None:
    seen_sequences = set()
    seen_ids = set()
    for record in records:
        if record.sequence is not None:
            if record.sequence in seen_sequences:
                raise ValueError(f"Duplicate observation sequence: {record.sequence}")
            seen_sequences.add(record.sequence)
        if record.event_id:
            identity = (record.type, record.event_id)
            if identity in seen_ids:
                raise ValueError(f"Duplicate observation event id: {record.event_id}")
            seen_ids.add(identity)


def _is_run_bundle(path: Path) -> bool:
    # C2-2: meta.json is the finalized-bundle recognition fact. A damaged segment that lost
    # other required files is still RECOGNIZED — it lists and then fails loudly on read,
    # never silently disappears from history.
    return (path / "meta.json").exists()


def _segment_paths(base: Path) -> list[Path]:
    if _is_run_bundle(base):
        return [base]
    if not base.exists():
        return []
    if (base / "segments").is_dir():
        roots = [base]
    else:
        roots = []
        for path in base.iterdir():
            if path.is_symlink():
                raise ValueError(
                    f"observation logical-run directory {path} is a symlink — symlinked "
                    "children of the configured root are rejected"
                )
            if path.is_dir():
                roots.append(path)
    segments: list[Path] = []
    for run_root in roots:
        segment_root = run_root / "segments"
        if segment_root.is_symlink():
            raise ValueError(
                f"observation segments directory {segment_root} is a symlink — symlinked "
                "storage owners are rejected"
            )
        if not segment_root.is_dir():
            continue
        for path in segment_root.iterdir():
            if path.is_symlink():
                raise ValueError(
                    f"observation segment directory {path} is a symlink — symlinked "
                    "children are rejected"
                )
            if path.is_dir() and _is_run_bundle(path):
                segments.append(path)
    return sorted(segments)


def _safe_segment_run_id(path: Path) -> str | None:
    try:
        return load_bundle_meta_v4(path).run_id
    except Exception:
        return _raw_logical_run_id(path)


def _run_entry(path: Path) -> dict:
    """Index-row dict built from the strict v4 meta — same key shape the writer
    persists, including absence-not-null for ``correlation_id``."""

    meta = load_bundle_meta_v4(path)
    entry = json.loads(meta.model_dump_json())
    if entry.get("correlation_id") is None:
        entry.pop("correlation_id", None)
    entry["path"] = str(path)
    return entry


def _run_entry_or_corrupt(path: Path) -> dict:
    try:
        return _run_entry(path)
    except Exception as contract_error:
        if path.is_symlink():
            raise
        raw = _raw_meta(path)
        run_id = _safe_raw_identity(raw.get("run_id"), fallback=path.name)
        segment_id = _safe_raw_identity(raw.get("segment_id"), fallback=path.name)
        index = raw.get("segment_index")
        if type(index) is not int or index < 0:
            index = 0
        attempt = raw.get("attempt")
        if type(attempt) is not int or attempt < 1:
            attempt = None
        return {
            "run_id": run_id,
            "workflow_id": str(raw.get("workflow_id") or "<corrupt>")[:200],
            "status": "corrupt",
            "timestamp": str(raw.get("timestamp") or ""),
            "segment_id": segment_id,
            "segment_index": index,
            "segment_kind": str(raw.get("segment_kind") or "initial"),
            "attempt": attempt,
            "corruption": str(contract_error)[:500],
            "path": str(path),
        }


def _raw_meta(path: Path) -> dict:
    try:
        raw = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _safe_raw_identity(value, *, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return assert_plain_identity(value, what="corrupt bundle identity")
    except ValueError:
        return fallback


def _raw_logical_run_id(path: Path) -> str | None:
    raw = _raw_meta(path)
    value = raw.get("run_id")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return assert_plain_identity(value, what="corrupt bundle run id")
    except ValueError:
        return None


def _validate_provider_evidence(meta, records: list[ObservationRecord]) -> None:
    if meta.incomplete_streams:
        if meta.status != "abandoned" or meta.provider_evidence.integrity != "incomplete":
            raise ValueError(
                f"Observation bundle {meta.segment_id!r} has incoherent incomplete-stream truth"
            )
        return
    try:
        validate_provider_invocation_links(
            (item.record for item in records if item.type == "trace"),
            (item.record for item in records if item.type == "detail"),
            (item.record for item in records if item.type == "usage"),
        )
    except Exception:
        if meta.provider_evidence.integrity != "incomplete":
            raise
    else:
        if meta.provider_evidence.integrity != "complete":
            raise ValueError(
                f"Observation bundle {meta.segment_id!r} claims incomplete provider "
                "evidence, but its persisted evidence graph validates completely"
            )
