"""Pure canonical selection for finalized observation segments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


SegmentDisposition = Literal["canonical", "abandoned", "superseded", "provisional"]


@dataclass(frozen=True)
class CanonicalSegmentCandidate:
    segment_id: str
    segment_index: int
    attempt: int | None
    commit_marker: bool
    abandoned: bool
    timestamp: str

    @property
    def committed(self) -> bool:
        return self.attempt is None or self.commit_marker


@dataclass(frozen=True)
class CanonicalSegmentSelection:
    canonical_ids: tuple[str, ...]
    dispositions: dict[str, SegmentDisposition]


def select_canonical_segments(
    logical_run_id: str,
    candidates: Sequence[CanonicalSegmentCandidate],
) -> CanonicalSegmentSelection:
    """Choose one canonical physical segment per logical index."""

    by_index, dispositions = _group_candidates(logical_run_id, candidates)
    canonical: list[CanonicalSegmentCandidate] = []
    for index in sorted(by_index):
        at_index = by_index[index]
        winner = _select_index_winner(logical_run_id, index, at_index)
        if winner is not None:
            canonical.append(winner)
        _record_dispositions(at_index, winner, dispositions)

    return CanonicalSegmentSelection(
        canonical_ids=tuple(item.segment_id for item in canonical),
        dispositions=dispositions,
    )


def _group_candidates(
    logical_run_id: str,
    candidates: Sequence[CanonicalSegmentCandidate],
) -> tuple[
    dict[int, list[CanonicalSegmentCandidate]],
    dict[str, SegmentDisposition],
]:
    by_index: dict[int, list[CanonicalSegmentCandidate]] = {}
    dispositions: dict[str, SegmentDisposition] = {}
    seen_ids: set[str] = set()
    for candidate in candidates:
        if candidate.segment_id in seen_ids:
            raise ValueError(
                f"Observation group {logical_run_id!r}: duplicate segment identity "
                f"{candidate.segment_id!r}"
            )
        seen_ids.add(candidate.segment_id)
        if candidate.commit_marker and candidate.abandoned:
            raise ValueError(
                f"Observation group {logical_run_id!r}: segment "
                f"{candidate.segment_id!r} is both committed and abandoned"
            )
        if candidate.abandoned:
            dispositions[candidate.segment_id] = "abandoned"
            continue
        by_index.setdefault(candidate.segment_index, []).append(candidate)
    return by_index, dispositions


def _select_index_winner(
    logical_run_id: str,
    index: int,
    candidates: Sequence[CanonicalSegmentCandidate],
) -> CanonicalSegmentCandidate | None:
    durable = _committed_durable_attempts(logical_run_id, index, candidates)
    if durable:
        return durable[-1]
    return max(
        (item for item in candidates if item.attempt is None),
        key=lambda item: (item.timestamp, item.segment_id),
        default=None,
    )


def _committed_durable_attempts(
    logical_run_id: str,
    index: int,
    candidates: Sequence[CanonicalSegmentCandidate],
) -> list[CanonicalSegmentCandidate]:
    by_attempt: dict[int, CanonicalSegmentCandidate] = {}
    for item in candidates:
        if item.attempt is None or not item.commit_marker:
            continue
        ordinal = int(item.attempt)
        previous = by_attempt.get(ordinal)
        if previous is not None:
            raise ValueError(
                f"Observation group {logical_run_id!r}: two committed attempts "
                f"share ordinal {ordinal} at segment index {index} "
                f"({previous.segment_id!r} vs {item.segment_id!r})"
            )
        by_attempt[ordinal] = item
    return sorted(
        by_attempt.values(),
        key=lambda item: (int(item.attempt or 0), item.segment_id),
    )


def _record_dispositions(
    candidates: Sequence[CanonicalSegmentCandidate],
    winner: CanonicalSegmentCandidate | None,
    dispositions: dict[str, SegmentDisposition],
) -> None:
    for item in candidates:
        if item is winner:
            dispositions[item.segment_id] = "canonical"
        elif item.committed:
            dispositions[item.segment_id] = "superseded"
        else:
            dispositions[item.segment_id] = "provisional"


__all__ = [
    "CanonicalSegmentCandidate",
    "CanonicalSegmentSelection",
    "SegmentDisposition",
    "select_canonical_segments",
]
