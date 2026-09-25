"""Shared sequencing and write/seal exclusion for one observation segment."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class ObservationSequence:
    """Run-local monotonic sequence and lifecycle shared by all three stream writers."""

    value: int = 0
    claimed: set[int] = field(default_factory=set, repr=False)
    sealed: bool = False
    lock: Any = field(default_factory=RLock, repr=False)

    def claim(self, supplied: int | None) -> int:
        if self.sealed:
            raise RuntimeError("observation segment is sealed; late write refused")
        if supplied is None:
            candidate = self.value + 1
            while candidate in self.claimed:
                candidate += 1
        else:
            if not isinstance(supplied, int) or isinstance(supplied, bool) or supplied < 1:
                raise ValueError("observation sequence must be a positive integer")
            candidate = supplied
        if candidate in self.claimed:
            raise ValueError(f"duplicate observation sequence {candidate}")
        self.claimed.add(candidate)
        self.value = max(self.value, candidate)
        return candidate


def owned_run_updates(record: Any, *, run_id: str, sequence: ObservationSequence) -> dict:
    supplied_run_id = getattr(record, "run_id", None)
    if supplied_run_id not in (None, run_id):
        raise ValueError(
            f"observation record belongs to foreign run {supplied_run_id!r}, not {run_id!r}"
        )
    return {
        "run_id": run_id,
        "sequence": sequence.claim(getattr(record, "sequence", None)),
    }
