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
    # W4R.1: crashed delivery attempts, finalized as typed `abandoned` evidence — never
    # merged into canonical history (their events are the partial prefix of the retry).
    abandoned: list[ObservationSegmentData]

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
        abandoned: list[ObservationSegmentData] = []
        for path in sorted(root.iterdir()) if root.exists() else []:
            if not (path.is_dir() and _is_run_bundle(path)):
                continue
            meta = _load_json(path / "meta.json", default={})
            if str(meta.get("run_id") or path.name) != logical_run_id:
                continue
            data = FileEventSource(path).read()  # per-segment sequence sanity runs here
            segment = ObservationSegmentData(
                segment_id=str(meta.get("segment_id") or path.name),
                segment_index=int(meta.get("segment_index") or 0),
                kind=str(meta.get("segment_kind") or "initial"),
                parent_segment_id=(
                    str(meta["parent_segment_id"]) if meta.get("parent_segment_id") else None
                ),
                status=str(meta.get("status") or "unknown"),
                path=str(path),
                data=data,
            )
            # W4R.1: abandoned crash-attempts are typed evidence, never canonical history.
            (abandoned if segment.status == "abandoned" else segments).append(segment)
        if not segments:
            raise FileNotFoundError(
                f"No finalized observation segments for logical run {logical_run_id!r} under {root}"
            )
        segments.sort(key=lambda segment: segment.segment_index)
        abandoned.sort(key=lambda segment: (segment.segment_index, segment.segment_id))
        group_digest = _assert_group_lineage_sane(logical_run_id, segments, abandoned)
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
            # W4.4: group spend is the sum of CANONICAL segment-local events (each spent
            # exactly once in exactly one segment) — never a sum of per-segment meta
            # totals, which are cumulative-since-run-start on resumed segments. An
            # abandoned attempt's partial spend was real money too, but it is not run
            # history: inspect it via group.abandoned[i].data.usage_events.
            usage_totals=_usage_totals_from_events(usage_events),
            cumulative_meta_totals={
                "scope": newest_meta.get("usage_totals_scope") or "single_bundle",
                "total_tokens": newest_meta.get("total_tokens"),
                "metered_usd": newest_meta.get("metered_usd"),
                "notional_usd": newest_meta.get("notional_usd"),
                "usage_count": newest_meta.get("usage_count"),
            },
            abandoned=abandoned,
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
            canonical = [
                item for item in entries if str(item.get("status")) != "abandoned"
            ] or entries
            newest = max(
                canonical,
                key=lambda item: (
                    int(item.get("segment_index") or 0),
                    str(item.get("timestamp") or ""),
                ),
            )
            groups.append(
                {
                    "run_id": run_id,
                    "segment_count": len(canonical),
                    "abandoned_count": len(entries) - len(canonical),
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
    logical_run_id: str,
    segments: list[ObservationSegmentData],
    abandoned: list[ObservationSegmentData],
) -> str | None:
    """W4R.2: REAL lineage integrity, not metadata agreement. Canonical segments must form
    one contiguous chain — exactly one index-0 ``initial``, indexes 0..N without gaps or
    duplicates, every continuation's parent equal to the immediately preceding segment,
    legal kinds (``wait_terminal`` only as the final segment), unique segment ids — and
    the definition digest is RECOMPUTED from each segment's actual ``definition.json``
    (a forged/stale meta digest cannot bless a changed machine). Pre-segment (v0.8.1)
    bundles remain valid groups of one. Returns the group's canonical digest.
    """

    def _fail(reason: str) -> None:
        raise ValueError(f"Observation group {logical_run_id!r}: {reason}")

    ids = [segment.segment_id for segment in segments]
    if len(set(ids)) != len(ids):
        _fail(f"duplicate segment ids {sorted(ids)} — refusing to merge ambiguous history")
    indexes = [segment.segment_index for segment in segments]
    for first, second in zip(segments, segments[1:]):
        if second.segment_index == first.segment_index:
            _fail(
                f"duplicate segment index {second.segment_index} ({first.segment_id} vs "
                f"{second.segment_id}) — evidence of a re-executed continuation"
            )
    if indexes != list(range(len(segments))):
        _fail(
            f"segment indexes {indexes} are not the contiguous chain "
            f"{list(range(len(segments)))} — history is incomplete (deleted, never "
            "finalized, or recorded without lineage)"
        )
    for position, segment in enumerate(segments):
        if position == 0:
            if segment.kind != "initial":
                _fail(
                    f"segment 0 ({segment.segment_id}) has kind {segment.kind!r} — a group "
                    "starts with exactly one 'initial' segment"
                )
        else:
            if segment.kind not in ("resume", "wait_terminal"):
                _fail(
                    f"segment {segment.segment_index} ({segment.segment_id}) has illegal "
                    f"kind {segment.kind!r}"
                )
            if segment.kind == "wait_terminal" and position != len(segments) - 1:
                _fail(
                    f"wait_terminal segment {segment.segment_id} is not last — nothing can "
                    "continue a terminally failed wait"
                )
            if segment.parent_segment_id != segments[position - 1].segment_id:
                _fail(
                    f"segment {segment.segment_id} names parent "
                    f"{segment.parent_segment_id!r} but the preceding segment is "
                    f"{segments[position - 1].segment_id!r} — the chain must be unbroken"
                )
    # Definition truth: recompute from the actual definition.json of every canonical
    # machine segment. wait_terminal segments are terminal EVIDENCE about the wait — their
    # definition file may legitimately be the changed currently-registered machine, so
    # only their meta claim is held to the group digest.
    recomputed = {
        segment.segment_id: segment.data.definition.definition_digest()
        for segment in segments
        if segment.kind != "wait_terminal"
    }
    distinct = sorted(set(recomputed.values()))
    if len(distinct) > 1:
        _fail(
            f"segments carry DIFFERENT actual workflow definitions (recomputed digests "
            f"{distinct}) — one logical run executes one machine"
        )
    group_digest = distinct[0] if distinct else None
    for segment in segments + abandoned:
        claimed = segment.data.meta.get("definition_digest")
        if claimed and group_digest and str(claimed) != group_digest:
            _fail(
                f"segment {segment.segment_id} meta claims digest {claimed!r} but the "
                f"group's actual definition digest is {group_digest!r} — forged or stale "
                "metadata is rejected"
            )
    return group_digest


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
