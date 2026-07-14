"""Typed observation data shared by viewer sources, grouping, and projection."""

from __future__ import annotations

from dataclasses import dataclass

from ai_workflow_engine import (
    ObservationBundleMetaV2,
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
    """One finalized segment of a logical run, including persisted attempt facts."""

    segment_id: str
    segment_index: int
    kind: str
    status: str
    path: str
    data: ObservationRunData
    attempt: int | None = None
    committed: bool = False
    disposition: str = "canonical"


@dataclass(frozen=True)
class ObservationGroupData:
    """One logical run assembled from its ordered immutable segments."""

    run_id: str
    definition: WorkflowDefinition
    segments: list[ObservationSegmentData]
    records: list[ObservationRecord]
    usage_totals: dict
    canonical_usage_totals: dict
    non_canonical_usage_totals: dict
    cumulative_meta_totals: dict
    related_run_id: str | None
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
        return self.segments[-1].status if self.segments else "unknown"


__all__ = [
    "ObservationGroupData",
    "ObservationRecord",
    "ObservationRunData",
    "ObservationSegmentData",
]
