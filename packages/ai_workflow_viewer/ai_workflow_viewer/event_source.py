"""Observation event sources consumed by the standalone viewer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ai_workflow_engine import (
    ObservationDetail,
    WorkflowDefinition,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    load_bundle_meta_v3,
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
        # ONE strict loader — pre-v3 or malformed meta fails here naming the
        # historical tag route; the viewer never renders a plausible page from guesses.
        meta = load_bundle_meta_v3(run_path)
        if run_id is not None and meta.run_id != run_id:
            raise FileNotFoundError(
                f"Observation bundle at {run_path} belongs to run {meta.run_id!r}, "
                f"not {run_id!r} — the meta identity is the truth, never the directory name"
            )
        definition_path = _contained_file(run_path, meta.definition_path)
        if not definition_path.exists():
            raise FileNotFoundError(f"Observation bundle is missing workflow definition: {definition_path}")
        definition = WorkflowDefinition.model_validate_json(definition_path.read_text(encoding="utf-8"))
        # C2-2: the digest is verified on EVERY read surface, not only the grouped one — the
        # same segment must never render through one door and refuse through another.
        actual_digest = definition.definition_digest()
        if actual_digest != meta.definition_digest:
            raise ValueError(
                f"Observation bundle at {run_path}: meta claims digest "
                f"{meta.definition_digest!r} but definition.json recomputes {actual_digest!r} "
                "— forged or stale metadata is rejected"
            )
        records = [
            *_load_records(_contained_file(run_path, meta.trace_path), "trace", WorkflowTraceEvent),
            *_load_records(_contained_file(run_path, meta.detail_path), "detail", ObservationDetail),
            *_load_records(_contained_file(run_path, meta.usage_path), "usage", WorkflowUsageEvent),
        ]
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
        base = self.base_path
        if _is_run_bundle(base):
            return [_run_entry_or_corrupt(base)]
        if not base.exists():
            return []
        entries = [
            _run_entry_or_corrupt(path)
            for path in base.iterdir()
            if path.is_dir() and _is_run_bundle(path)
        ]
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

        root = self.base_path.parent if _is_run_bundle(self.base_path) else self.base_path
        entries: list[ObservationSegmentData] = []
        abandoned_segment_ids: set[str] = set()
        for path in sorted(root.iterdir()) if root.exists() else []:
            if not (path.is_dir() and _is_run_bundle(path)):
                continue
            try:
                meta = load_bundle_meta_v3(path)
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
            data = FileEventSource(path).read()  # per-segment sequence sanity runs here
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
                f"No finalized observation segments for logical run {logical_run_id!r} under {root}"
            )
        return assemble_observation_group(
            logical_run_id,
            entries,
            abandoned_segment_ids=abandoned_segment_ids,
        )

    def list_groups(self, *, related_run_id: str | None = None) -> list[dict]:
        """One entry per LOGICAL run: segment count, newest status/timestamp (W4.3)."""

        root = self.base_path.parent if _is_run_bundle(self.base_path) else self.base_path
        if not root.exists():
            return []
        grouped: dict[str, list[dict]] = {}
        for path in root.iterdir():
            if path.is_dir() and _is_run_bundle(path):
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
            if run_id is not None and run_id != self.base_path.name:
                candidate = resolve_child_dir(
                    self.base_path.parent, run_id, what="observation bundle directory"
                )
                if candidate.exists():
                    return candidate
            return self.base_path
        if run_id is None:
            runs = self.list_runs()
            if len(runs) != 1:
                raise ValueError("Observation source contains multiple runs; pass run_id")
            run_id = str(runs[0]["run_id"])
        return resolve_child_dir(self.base_path, run_id, what="observation bundle directory")


def _load_records(path: Path, kind: str, model: type) -> list[ObservationRecord]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = model.model_validate_json(line)
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


def _contained_file(run_path: Path, name: str) -> Path:
    """C2-1: resolve one fixed-layout bundle file and require it to stay inside the bundle
    directory — a symlink escaping the bundle is rejected, never followed."""

    root = run_path.resolve()
    candidate = root / name
    if not candidate.resolve().is_relative_to(root):
        raise ValueError(
            f"bundle file {name!r} in {run_path} escapes its bundle directory — "
            "symlinked evidence is rejected"
        )
    return candidate


def _run_entry(path: Path) -> dict:
    """Index-row dict built from the STRICT v3 meta — same key shape the writer
    persists, including absence-not-null for ``correlation_id``."""

    meta = load_bundle_meta_v3(path)
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
