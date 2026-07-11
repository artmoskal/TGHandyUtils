"""Observation event sources consumed by the standalone viewer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ai_workflow_engine import (
    ObservationDetail,
    WorkflowDefinition,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
)


@dataclass(frozen=True)
class ObservationRecord:
    """One ordered source record from a run bundle."""

    type: str
    sequence: int | None
    event_id: str
    record: WorkflowTraceEvent | ObservationDetail | WorkflowUsageEvent

    def as_event_payload(self) -> dict:
        return {"type": self.type, "record": self.record.model_dump(by_alias=True)}


@dataclass(frozen=True)
class ObservationRunData:
    """Typed records needed to render one observation run."""

    run_id: str
    definition: WorkflowDefinition
    records: list[ObservationRecord]
    meta: dict

    @property
    def trace_events(self) -> list[WorkflowTraceEvent]:
        return [item.record for item in self.records if item.type == "trace"]

    @property
    def details(self) -> list[ObservationDetail]:
        return [item.record for item in self.records if item.type == "detail"]

    @property
    def usage_events(self) -> list[WorkflowUsageEvent]:
        return [item.record for item in self.records if item.type == "usage"]


@dataclass(frozen=True)
class ObservationSegmentData:
    """One finalized segment of a logical run (W4): identity plus its own run data."""

    segment_id: str
    segment_index: int
    kind: str
    parent_segment_id: str | None
    status: str
    path: str
    data: ObservationRunData


@dataclass(frozen=True)
class ObservationGroupData:
    """One LOGICAL run assembled from its ordered immutable segments (W4.3).

    ``records`` merge in ``(segment_index, per-segment sequence)`` order; sequence sanity
    holds PER SEGMENT (each segment restarts at 1), while event ids must be unique across
    the whole group. ``usage_totals`` is aggregated ONCE from segment-local usage events;
    ``cumulative_meta_totals`` carries the newest segment's meta totals, which the engine
    labels ``run_cumulative_at_finalize`` — the two must never be summed together.
    """

    run_id: str
    definition: WorkflowDefinition
    segments: list[ObservationSegmentData]
    records: list[ObservationRecord]
    usage_totals: dict
    cumulative_meta_totals: dict
    lineage_notes: list[str]

    @property
    def trace_events(self) -> list[WorkflowTraceEvent]:
        return [item.record for item in self.records if item.type == "trace"]

    @property
    def details(self) -> list[ObservationDetail]:
        return [item.record for item in self.records if item.type == "detail"]

    @property
    def usage_events(self) -> list[WorkflowUsageEvent]:
        return [item.record for item in self.records if item.type == "usage"]

    @property
    def status(self) -> str:
        """The group's current truth: the newest segment's finalized status."""

        return self.segments[-1].status if self.segments else "unknown"


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
        meta = _load_json(run_path / "meta.json", default={})
        definition_path = run_path / str(meta.get("definition_path") or "definition.json")
        if not definition_path.exists():
            raise FileNotFoundError(f"Observation bundle is missing workflow definition: {definition_path}")
        definition = WorkflowDefinition.model_validate_json(definition_path.read_text(encoding="utf-8"))
        selected_run_id = run_id or str(meta.get("run_id") or run_path.name)
        records = [
            *_load_records(run_path / str(meta.get("trace_path") or "trace.jsonl"), "trace", WorkflowTraceEvent),
            *_load_records(run_path / str(meta.get("detail_path") or "details.jsonl"), "detail", ObservationDetail),
            *_load_records(run_path / str(meta.get("usage_path") or "usage.jsonl"), "usage", WorkflowUsageEvent),
        ]
        records.sort(key=_record_sort_key)
        _assert_sequence_sane(records)
        return ObservationRunData(
            run_id=selected_run_id,
            definition=definition,
            records=records,
            meta=meta,
        )

    def list_runs(self) -> list[dict]:
        base = self.base_path
        if _is_run_bundle(base):
            return [_run_entry(base)]
        if not base.exists():
            return []
        entries = [_run_entry(path) for path in base.iterdir() if path.is_dir() and _is_run_bundle(path)]
        entries.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return entries

    def read_group(self, logical_run_id: str) -> ObservationGroupData:
        """Read ALL finalized segments of one logical run and merge them honestly (W4.3).

        Single-bundle ``read()`` semantics are untouched — this is the explicit grouped
        surface. Pre-segment (v0.8.1) bundles are groups of one. Corrupt lineage is LOUD:
        duplicate segment index, a named parent that is absent, or segments whose
        definition digests disagree all raise instead of rendering a half-true merge.
        """

        root = self.base_path.parent if _is_run_bundle(self.base_path) else self.base_path
        segments: list[ObservationSegmentData] = []
        for path in sorted(root.iterdir()) if root.exists() else []:
            if not (path.is_dir() and _is_run_bundle(path)):
                continue
            meta = _load_json(path / "meta.json", default={})
            if str(meta.get("run_id") or path.name) != logical_run_id:
                continue
            data = FileEventSource(path).read()  # per-segment sequence sanity runs here
            segments.append(
                ObservationSegmentData(
                    segment_id=str(meta.get("segment_id") or path.name),
                    segment_index=int(meta.get("segment_index") or 0),
                    kind=str(meta.get("segment_kind") or "initial"),
                    parent_segment_id=(
                        str(meta["parent_segment_id"])
                        if meta.get("parent_segment_id")
                        else None
                    ),
                    status=str(meta.get("status") or "unknown"),
                    path=str(path),
                    data=data,
                )
            )
        if not segments:
            raise FileNotFoundError(
                f"No finalized observation segments for logical run {logical_run_id!r} under {root}"
            )
        segments.sort(key=lambda segment: segment.segment_index)
        _assert_group_lineage_sane(logical_run_id, segments)
        lineage_notes = [
            f"segment {segment.segment_index} ({segment.segment_id}) has no recorded parent "
            "lineage (pre-segment snapshot or pre-W4 suspension)"
            for segment in segments
            if segment.segment_index > 0 and segment.parent_segment_id is None
        ]
        merged: list[ObservationRecord] = []
        seen_ids: set[tuple[str, str]] = set()
        for segment in segments:
            for record in segment.data.records:  # already sequence-ordered per segment
                if record.event_id:
                    identity = (record.type, record.event_id)
                    if identity in seen_ids:
                        raise ValueError(
                            f"Duplicate observation event id across segments of "
                            f"{logical_run_id!r}: {record.event_id} — evidence must never "
                            "be double-counted"
                        )
                    seen_ids.add(identity)
                merged.append(record)
        usage_events = [item.record for item in merged if item.type == "usage"]
        newest_meta = segments[-1].data.meta
        return ObservationGroupData(
            run_id=logical_run_id,
            definition=segments[0].data.definition,
            segments=segments,
            records=merged,
            # W4.4: group spend is the sum of SEGMENT-LOCAL events (each spent exactly once
            # in exactly one segment) — never a sum of per-segment meta totals, which are
            # cumulative-since-run-start on resumed segments.
            usage_totals=_usage_totals_from_events(usage_events),
            cumulative_meta_totals={
                "scope": newest_meta.get("usage_totals_scope") or "single_bundle",
                "total_tokens": newest_meta.get("total_tokens"),
                "metered_usd": newest_meta.get("metered_usd"),
                "notional_usd": newest_meta.get("notional_usd"),
                "usage_count": newest_meta.get("usage_count"),
            },
            lineage_notes=lineage_notes,
        )

    def list_groups(self) -> list[dict]:
        """One entry per LOGICAL run: segment count, newest status/timestamp (W4.3)."""

        root = self.base_path.parent if _is_run_bundle(self.base_path) else self.base_path
        if not root.exists():
            return []
        grouped: dict[str, list[dict]] = {}
        for path in root.iterdir():
            if path.is_dir() and _is_run_bundle(path):
                entry = _run_entry(path)
                grouped.setdefault(str(entry["run_id"]), []).append(entry)
        groups = []
        for run_id, entries in grouped.items():
            newest = max(
                entries,
                key=lambda item: (
                    int(item.get("segment_index") or 0),
                    str(item.get("timestamp") or ""),
                ),
            )
            groups.append(
                {
                    "run_id": run_id,
                    "segment_count": len(entries),
                    "status": newest.get("status"),
                    "timestamp": max(str(item.get("timestamp") or "") for item in entries),
                    "workflow_id": newest.get("workflow_id"),
                }
            )
        groups.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return groups

    def _run_path(self, run_id: str | None) -> Path:
        if _is_run_bundle(self.base_path):
            if run_id is not None and run_id != self.base_path.name:
                candidate = self.base_path.parent / run_id
                if candidate.exists():
                    return candidate
            return self.base_path
        if run_id is None:
            runs = self.list_runs()
            if len(runs) != 1:
                raise ValueError("Observation source contains multiple runs; pass run_id")
            run_id = str(runs[0]["run_id"])
        return self.base_path / run_id


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


def _assert_group_lineage_sane(
    logical_run_id: str, segments: list[ObservationSegmentData]
) -> None:
    """Loud lineage integrity (W4.3): duplicate index, absent named parent, digest split."""

    seen_indexes: dict[int, str] = {}
    known_segment_ids = {segment.segment_id for segment in segments}
    digests = {
        str(segment.data.meta.get("definition_digest"))
        for segment in segments
        if segment.data.meta.get("definition_digest")
    }
    if len(digests) > 1:
        raise ValueError(
            f"Observation group {logical_run_id!r} mixes definition digests {sorted(digests)} — "
            "segments of one logical run must share one workflow definition"
        )
    for segment in segments:
        if segment.segment_index in seen_indexes:
            raise ValueError(
                f"Duplicate segment index {segment.segment_index} in observation group "
                f"{logical_run_id!r} ({seen_indexes[segment.segment_index]} vs "
                f"{segment.segment_id}) — evidence of a re-executed continuation; refusing "
                "to merge ambiguous history"
            )
        seen_indexes[segment.segment_index] = segment.segment_id
        if segment.parent_segment_id and segment.parent_segment_id not in known_segment_ids:
            raise ValueError(
                f"Segment {segment.segment_id} of observation group {logical_run_id!r} names "
                f"parent {segment.parent_segment_id!r} which is not present — its suspension "
                "history was deleted or never finalized"
            )


def _usage_totals_from_events(events: list[WorkflowUsageEvent]) -> dict:
    """Aggregate segment-local usage events exactly once (same rules as bundle meta)."""

    metered = [
        float(event.estimated_usd)
        for event in events
        if event.cost_class == "metered" and event.estimated_usd is not None
    ]
    notional = [
        float(event.notional_usd) for event in events if event.notional_usd is not None
    ]
    return {
        "scope": "group_events",
        "usage_count": len(events),
        "total_tokens": sum(event.total_tokens for event in events),
        "metered_usd": round(sum(metered), 6) if metered else None,
        "notional_usd": round(sum(notional), 6) if notional else None,
    }


def _is_run_bundle(path: Path) -> bool:
    return (path / "meta.json").exists() and (path / "definition.json").exists()


def _run_entry(path: Path) -> dict:
    meta = _load_json(path / "meta.json", default={})
    return {
        **meta,
        "run_id": str(meta.get("run_id") or path.name),
        "path": str(path),
    }


def _load_json(path: Path, *, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    return json.loads(path.read_text(encoding="utf-8"))
