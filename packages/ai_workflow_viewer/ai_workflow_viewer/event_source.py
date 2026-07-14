"""Observation event sources consumed by the standalone viewer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ai_workflow_engine import (
    ObservationBundleMetaV2,
    ObservationDetail,
    WorkflowDefinition,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    load_bundle_meta_v2,
)
from ai_workflow_engine.observation_bundle import (
    ABANDON_MARKER_NAME as _ABANDON_MARKER,
    COMMIT_MARKER_NAME as _COMMIT_MARKER,
    _looks_like_path,
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
    # M10: the STRICT v2 meta model — every reader consumes typed fields; there is no
    # permissive dict fallback anywhere in the viewer.
    meta: ObservationBundleMetaV2

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
    status: str
    path: str
    data: ObservationRunData
    # R1 attempt lifecycle: durable claim ordinal (None = initial/local), commit-marker
    # presence, and the reader's disposition verdict for this physical directory.
    attempt: int | None = None
    committed: bool = False
    disposition: str = "canonical"  # canonical | superseded | provisional | abandoned


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
    # W4R.3 cost honesty: `usage_totals` is ACTUAL spend — every segment-local usage event
    # counted exactly once, INCLUDING non-canonical attempts (abandoned, superseded, or
    # provisional — a paid call before a crash is real money). The split never makes money
    # disappear: actual = canonical + non_canonical.
    usage_totals: dict
    canonical_usage_totals: dict
    non_canonical_usage_totals: dict
    cumulative_meta_totals: dict
    # the group's SINGLE validated related-run identity (None when uncorrelated)
    related_run_id: str | None
    # R1: physical attempts that are NOT canonical history — each carries its disposition
    # (abandoned = demoted by the reconciler; superseded = committed but outranked by a
    # later committed attempt; provisional = finalized but never committed, i.e. the
    # crashed-between-finalize-and-terminalize window). All inspectable, never merged.
    non_canonical: list[ObservationSegmentData]

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
        # M10: ONE strict loader — pre-v2 or malformed meta fails here naming the
        # historical tag route; the viewer never renders a plausible page from guesses.
        meta = load_bundle_meta_v2(run_path)
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
        return ObservationRunData(
            run_id=meta.run_id,
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
        surface. Lineage is LOGICAL (R1): the canonical chain is one committed attempt per
        contiguous index — physical parent pointers are gone. Corruption is LOUD: a gap in
        the canonical chain, two committed durable attempts at one ordinal, or definition
        digests that disagree with the actual files all raise instead of rendering a
        half-true merge.
        """

        root = self.base_path.parent if _is_run_bundle(self.base_path) else self.base_path
        entries: list[ObservationSegmentData] = []
        for path in sorted(root.iterdir()) if root.exists() else []:
            if not (path.is_dir() and _is_run_bundle(path)):
                continue
            meta = load_bundle_meta_v2(path)
            if meta.run_id != logical_run_id:
                continue
            data = FileEventSource(path).read()  # per-segment sequence sanity runs here
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
        segments, non_canonical = _select_canonical_attempts(logical_run_id, entries)
        group_digest = _assert_group_lineage_sane(logical_run_id, segments, non_canonical)
        merged: list[ObservationRecord] = []
        seen_ids: set[tuple[str, str]] = set()

        def _claim_ids(source: list[ObservationRecord]) -> None:
            for record in source:
                if record.event_id:
                    identity = (record.type, record.event_id)
                    if identity in seen_ids:
                        raise ValueError(
                            f"Duplicate observation event id across segments of "
                            f"{logical_run_id!r}: {record.event_id} — evidence must never "
                            "be double-counted"
                        )
                    seen_ids.add(identity)

        for segment in segments:
            _claim_ids(segment.data.records)  # already sequence-ordered per segment
            merged.extend(segment.data.records)
        for segment in non_canonical:
            _claim_ids(segment.data.records)  # non-canonical money must be unique too
        usage_events = [item.record for item in merged if item.type == "usage"]
        non_canonical_usage = [
            record for segment in non_canonical for record in segment.data.usage_events
        ]
        newest_meta = segments[-1].data.meta
        related_values = {
            segment.data.meta.correlation_id
            for segment in segments
            if segment.data.meta.correlation_id
        }
        return ObservationGroupData(
            run_id=logical_run_id,
            related_run_id=next(iter(related_values), None),  # single by partition check
            definition=segments[0].data.definition,
            segments=segments,
            records=merged,
            # W4.4/W4R.3: totals come from segment-local EVENTS counted once each —
            # never from per-segment meta totals, which are cumulative-since-run-start on
            # resumed segments. Actual spend includes every non-canonical attempt (real
            # money); the split is a drill-down, not a place money hides.
            usage_totals=_usage_totals_from_events(
                usage_events + non_canonical_usage, scope="actual_all_attempts"
            ),
            canonical_usage_totals=_usage_totals_from_events(
                usage_events, scope="canonical_chain"
            ),
            non_canonical_usage_totals=_usage_totals_from_events(
                non_canonical_usage, scope="non_canonical_attempts"
            ),
            cumulative_meta_totals={
                "scope": newest_meta.usage_totals_scope,
                "total_tokens": newest_meta.total_tokens,
                "metered_usd": newest_meta.metered_usd,
                "notional_usd": newest_meta.notional_usd,
                "usage_count": newest_meta.usage_count,
            },
            non_canonical=non_canonical,
        )

    def list_groups(self, *, related_run_id: str | None = None) -> list[dict]:
        """One entry per LOGICAL run: segment count, newest status/timestamp (W4.3)."""

        root = self.base_path.parent if _is_run_bundle(self.base_path) else self.base_path
        if not root.exists():
            return []
        grouped: dict[str, list[dict]] = {}
        for path in root.iterdir():
            if path.is_dir() and _is_run_bundle(path):
                entry = _run_entry(path)
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
        groups = []
        for run_id, entries in grouped.items():
            # R0R3-C2: the chooser derives its counts from the SAME canonical-selection
            # algorithm as the detail page — never a second approximation.
            try:
                canonical_rows, non_rows = _canonical_partition(
                    run_id, [item.pop("_row") for item in entries]
                )
            except ValueError:
                groups.append(
                    {
                        "run_id": run_id,
                        "segment_count": len(entries),
                        "non_canonical_count": 0,
                        "status": "corrupt",
                        "timestamp": max(str(item["timestamp"]) for item in entries),
                        "workflow_id": entries[0]["workflow_id"],
                    }
                )
                continue
            canonical = [row["entry"] for row in canonical_rows]
            newest = max(
                canonical or entries,  # all-provisional group: show raw newest, count 0
                key=lambda item: (item["segment_index"], str(item["timestamp"])),
            )
            groups.append(
                {
                    "run_id": run_id,
                    "segment_count": len(canonical),
                    "non_canonical_count": len(non_rows),
                    "status": newest["status"],
                    "timestamp": max(str(item["timestamp"]) for item in entries),
                    "workflow_id": newest["workflow_id"],
                    # human-facing label: Related-run ID — the group's SINGLE validated
                    # identity (drift already raised in the shared partition above)
                    "related_run_id": next(
                        (
                            row["correlation"]
                            for row in canonical_rows + [r for r, _ in non_rows]
                            if row.get("correlation")
                        ),
                        None,
                    ),
                }
            )
        groups.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        if related_run_id is not None:
            # Related-run FILTER: selects the independent runs of one case — it never
            # merges them; each row keeps its own run_id, history, and storage identity.
            groups = [g for g in groups if g.get("related_run_id") == related_run_id]
        return groups

    def _run_path(self, run_id: str | None) -> Path:
        # C2-1: a caller-supplied run id (including HTTP query/path values) is joined to the
        # configured root — path syntax is an attack, not an identity. One guard, every door.
        if run_id is not None and _looks_like_path(str(run_id)):
            raise ValueError(
                f"run id {run_id!r} contains path syntax — identities are plain names, "
                "never paths"
            )
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


def _canonical_partition(
    logical_run_id: str, rows: list[dict]
) -> tuple[list[dict], list[tuple[dict, str]]]:
    """R1/R0R3-C2: THE canonical-selection algorithm — one implementation shared by
    ``read_group`` (detail) and ``list_groups`` (chooser), so the two surfaces can never
    disagree about what counts as history.

    A row is ``{"id", "index", "attempt", "committed", "abandoned", "timestamp",
    "correlation", ...}``.
    Dispositions: ``abandoned`` (demoted by the reconciler), ``superseded`` (committed but
    outranked — higher committed ordinal, or newer committed local attempt), ``provisional``
    (finalized, never committed). Two committed durable attempts sharing one ordinal are
    impossible under single-claimant CAS — corruption, raises. One run also has ONE
    related-run identity: segments that disagree — including present-vs-absent drift —
    are corruption, never a silent first/newest pick."""

    # EVERY disposition participates in identity validation — an abandoned attempt's
    # spend counts in actual economics, so its identity must match the group's too.
    present = sorted({row.get("correlation") for row in rows if row.get("correlation")})
    if len(present) > 1:
        raise ValueError(
            f"Observation group {logical_run_id!r}: segments claim DIFFERENT related-run "
            f"ids {present} — one run has one related-run identity"
        )
    if present and any(row.get("correlation") is None for row in rows):
        raise ValueError(
            f"Observation group {logical_run_id!r}: related-run id {present[0]!r} is "
            "present on some segments and absent on others — identity drift is "
            "corruption, not a display choice"
        )

    canonical: list[dict] = []
    non_canonical: list[tuple[dict, str]] = []
    by_index: dict[int, list[dict]] = {}
    for row in rows:
        if row["abandoned"]:
            non_canonical.append((row, "abandoned"))
            continue
        by_index.setdefault(int(row["index"]), []).append(row)
    for index in sorted(by_index):
        candidates = by_index[index]
        committed = [row for row in candidates if row["committed"]]
        durable = sorted(
            (row for row in committed if row["attempt"] is not None),
            key=lambda row: row["attempt"],
        )
        if durable:
            if len(durable) >= 2 and durable[-1]["attempt"] == durable[-2]["attempt"]:
                raise ValueError(
                    f"Observation group {logical_run_id!r}: two COMMITTED attempts share "
                    f"ordinal {durable[-1]['attempt']} at segment index {index} "
                    f"({durable[-2]['id']} vs {durable[-1]['id']}) — impossible "
                    "under single-claimant CAS; refusing corrupt history"
                )
            winner = durable[-1]
        elif committed:
            winner = sorted(
                committed, key=lambda row: (str(row["timestamp"] or ""), row["id"])
            )[-1]
        else:
            winner = None
        for row in candidates:
            if row is winner:
                canonical.append(row)
            elif row["committed"]:
                non_canonical.append((row, "superseded"))
            else:
                non_canonical.append((row, "provisional"))
    return canonical, non_canonical


def _select_canonical_attempts(
    logical_run_id: str, entries: list[ObservationSegmentData]
) -> tuple[list[ObservationSegmentData], list[ObservationSegmentData]]:
    """Adapter over :func:`_canonical_partition` for full segment data (detail surface)."""

    from dataclasses import replace

    rows = [
        {
            "id": entry.segment_id,
            "index": entry.segment_index,
            "attempt": entry.attempt,
            "committed": entry.committed,
            "abandoned": entry.status == "abandoned"
            or (Path(entry.path) / _ABANDON_MARKER).exists(),
            "timestamp": entry.data.meta.timestamp,
            "correlation": entry.data.meta.correlation_id,
            "entry": entry,
        }
        for entry in entries
    ]
    canonical_rows, non_rows = _canonical_partition(logical_run_id, rows)
    canonical = sorted(
        (row["entry"] for row in canonical_rows), key=lambda entry: entry.segment_index
    )
    non_canonical = sorted(
        (replace(row["entry"], disposition=disposition) for row, disposition in non_rows),
        key=lambda entry: (entry.segment_index, entry.segment_id),
    )
    if not canonical:
        raise ValueError(
            f"Observation group {logical_run_id!r} has no canonical history — every "
            "attempt is abandoned or provisional (uncommitted)"
        )
    return canonical, non_canonical


def _assert_group_lineage_sane(
    logical_run_id: str,
    segments: list[ObservationSegmentData],
    non_canonical: list[ObservationSegmentData],
) -> str | None:
    """R1: lineage is LOGICAL — one committed attempt per contiguous index, legal kinds,
    and definition digests RECOMPUTED from each actual ``definition.json`` (a forged or
    stale meta digest cannot bless a changed machine). Physical parent pointers left the
    contract; the gap check subsumes the old absent-parent check. Returns the group's
    canonical digest."""

    def _fail(reason: str) -> None:
        raise ValueError(f"Observation group {logical_run_id!r}: {reason}")

    ids = [segment.segment_id for segment in segments]
    if len(set(ids)) != len(ids):
        _fail(f"duplicate segment ids {sorted(ids)} — refusing to merge ambiguous history")
    indexes = [segment.segment_index for segment in segments]
    if indexes != list(range(len(segments))):
        _fail(
            f"canonical segment indexes {indexes} are not the contiguous chain "
            f"{list(range(len(segments)))} — history is incomplete (deleted, never "
            "finalized, or never committed)"
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
    # Definition truth: recompute from the actual definition.json of EVERY directory in
    # the group — canonical and non-canonical alike (wait_terminal files are the
    # REGISTERED bytes, so they recompute like any other segment).
    recomputed = {
        segment.segment_id: segment.data.definition.definition_digest()
        for segment in [*segments, *non_canonical]
    }
    distinct = sorted(set(recomputed.values()))
    if len(distinct) > 1:
        _fail(
            f"segments carry DIFFERENT actual workflow definitions (recomputed digests "
            f"{distinct}) — one logical run executes one machine"
        )
    group_digest = distinct[0] if distinct else None
    for segment in [*segments, *non_canonical]:
        claimed = segment.data.meta.definition_digest  # REQUIRED in v2 — never blank
        if group_digest and claimed != group_digest:
            _fail(
                f"segment {segment.segment_id} meta claims digest {claimed!r} but the "
                f"group's actual definition digest is {group_digest!r} — forged or stale "
                "metadata is rejected"
            )
    return group_digest


def _usage_totals_from_events(events: list[WorkflowUsageEvent], *, scope: str) -> dict:
    """Aggregate segment-local usage events exactly once (same rules as bundle meta)."""

    metered = [
        float(event.estimated_usd)
        for event in events
        if event.cost_class == "metered" and event.estimated_usd is not None
    ]
    notional = [
        float(event.notional_usd) for event in events if event.notional_usd is not None
    ]
    unknown_cost_count = sum(
        1 for event in events if (event.metadata or {}).get("cost_known") is False
    )
    return {
        "scope": scope,
        "usage_count": len(events),
        "total_tokens": sum(event.total_tokens for event in events),
        "metered_usd": round(sum(metered), 6) if metered else None,
        "notional_usd": round(sum(notional), 6) if notional else None,
        "unknown_cost_count": unknown_cost_count,
    }


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
    """Index-row dict built from the STRICT v2 meta (M10) — same key shape the writer
    persists, including absence-not-null for ``correlation_id``."""

    meta = load_bundle_meta_v2(path)
    entry = json.loads(meta.model_dump_json())
    if entry.get("correlation_id") is None:
        entry.pop("correlation_id", None)
    entry["path"] = str(path)
    return entry
