"""Pure canonical lifecycle grouping for observation segments."""

from __future__ import annotations

from dataclasses import replace
from typing import AbstractSet

from ai_workflow_engine import WorkflowUsageEvent

from ai_workflow_viewer.observation_data import (
    ObservationGroupData,
    ObservationRecord,
    ObservationSegmentData,
)


def assemble_observation_group(
    logical_run_id: str,
    entries: list[ObservationSegmentData],
    *,
    abandoned_segment_ids: AbstractSet[str],
) -> ObservationGroupData:
    """Select canonical history and aggregate one logical run without reading storage."""

    segments, non_canonical = _select_canonical_attempts(
        logical_run_id,
        entries,
        abandoned_segment_ids=abandoned_segment_ids,
    )
    _assert_group_lineage_sane(logical_run_id, segments, non_canonical)
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
        _claim_ids(segment.data.records)
        merged.extend(segment.data.records)
    for segment in non_canonical:
        _claim_ids(segment.data.records)

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
        related_run_id=next(iter(related_values), None),
        definition=segments[0].data.definition,
        segments=segments,
        records=merged,
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


def summarize_observation_groups(
    grouped: dict[str, list[dict]],
    *,
    related_run_id: str | None = None,
) -> list[dict]:
    """Project storage rows into one chooser entry per logical run."""

    groups: list[dict] = []
    for run_id, entries in grouped.items():
        if any(item.get("status") == "corrupt" for item in entries):
            groups.append(
                {
                    "run_id": run_id,
                    "segment_count": len(entries),
                    "non_canonical_count": 0,
                    "status": "corrupt",
                    "timestamp": max(str(item.get("timestamp") or "") for item in entries),
                    "workflow_id": entries[0].get("workflow_id", "<corrupt>"),
                }
            )
            continue
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
            canonical or entries,
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
                "related_run_id": next(
                    (
                        row["correlation"]
                        for row in canonical_rows + [row for row, _ in non_rows]
                        if row.get("correlation")
                    ),
                    None,
                ),
            }
        )
    groups.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    if related_run_id is not None:
        groups = [group for group in groups if group.get("related_run_id") == related_run_id]
    return groups


def _canonical_partition(
    logical_run_id: str, rows: list[dict]
) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Select one canonical attempt per logical segment index."""

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
    logical_run_id: str,
    entries: list[ObservationSegmentData],
    *,
    abandoned_segment_ids: AbstractSet[str],
) -> tuple[list[ObservationSegmentData], list[ObservationSegmentData]]:
    rows = [
        {
            "id": entry.segment_id,
            "index": entry.segment_index,
            "attempt": entry.attempt,
            "committed": entry.committed,
            "abandoned": entry.status == "abandoned"
            or entry.segment_id in abandoned_segment_ids,
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
        claimed = segment.data.meta.definition_digest
        if group_digest and claimed != group_digest:
            _fail(
                f"segment {segment.segment_id} meta claims digest {claimed!r} but the "
                f"group's actual definition digest is {group_digest!r} — forged or stale "
                "metadata is rejected"
            )
    return group_digest


def _usage_totals_from_events(events: list[WorkflowUsageEvent], *, scope: str) -> dict:
    metered = [
        float(event.estimated_usd)
        for event in events
        if event.cost_class == "metered" and event.estimated_usd is not None
    ]
    notional = [
        float(event.notional_usd) for event in events if event.notional_usd is not None
    ]
    unknown_cost_count = sum(1 for event in events if _event_cost_is_unknown(event))
    return {
        "scope": scope,
        "usage_count": len(events),
        "total_tokens": sum(event.total_tokens for event in events),
        "metered_usd": round(sum(metered), 6) if metered else None,
        "notional_usd": round(sum(notional), 6) if notional else None,
        "unknown_cost_count": unknown_cost_count,
    }


def _event_cost_is_unknown(event: WorkflowUsageEvent) -> bool:
    if event.cost_class == "subscription_notional":
        return event.notional_pricing is None or not event.notional_pricing.known
    return event.estimated_usd is None


__all__ = ["assemble_observation_group", "summarize_observation_groups"]
