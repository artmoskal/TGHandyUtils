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
