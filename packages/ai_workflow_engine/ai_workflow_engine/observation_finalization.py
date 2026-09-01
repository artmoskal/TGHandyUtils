"""Incremental persistence and finalization of v4 observation record streams."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ai_workflow_engine.models import (
    ObservationDetail,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.observation_contract import (
    ObservationDetailEnvelope,
    ProviderEvidenceIntegrity,
    ReferencedObservationBody,
)
from ai_workflow_engine.observation_integrity import ProviderEvidenceValidator
from ai_workflow_engine.observation_values import RunValueStore, persist_observation_body


class PersistedDetailSink:
    """Persist logical details as compact v4 envelopes over one run-scoped store."""

    def __init__(self, path: Path, value_store: RunValueStore) -> None:
        self.path = path
        self.value_store = value_store

    def record(self, detail: ObservationDetail) -> None:
        if detail.run_id is None:
            raise ValueError("persisted observation detail requires run_id")
        envelope = ObservationDetailEnvelope(
            **detail.model_dump(exclude={"body", "digest"}),
            body=persist_observation_body(self.value_store, detail.body),
        )
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(envelope.model_dump_json())
            stream.write("\n")

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")


@dataclass(frozen=True)
class SegmentEvidenceSummary:
    provider_evidence: ProviderEvidenceIntegrity
    trace_count: int
    detail_count: int
    usage_count: int


def materialize_usage_events(
    usage: WorkflowUsageSummary | Iterable[WorkflowUsageEvent] | None,
) -> list[WorkflowUsageEvent]:
    if usage is None:
        return []
    if isinstance(usage, WorkflowUsageSummary):
        return list(usage.events)
    return list(usage)


def sum_optional_cost(values: Iterable[float | None]) -> float | None:
    costs = [float(value) for value in values if value is not None]
    return round(sum(costs), 6) if costs else None


def summarize_segment_evidence(
    *,
    status: str,
    trace_path: Path,
    detail_path: Path,
    usage_path: Path,
    value_store: RunValueStore,
) -> SegmentEvidenceSummary:
    """Validate one segment incrementally without materializing its record streams."""

    validator = ProviderEvidenceValidator()
    trace_count = 0
    detail_count = 0
    usage_count = 0
    validated_bodies: set[str] = set()
    for trace in iter_jsonl_models(trace_path, WorkflowTraceEvent):
        validator.record_trace(trace)
        trace_count += 1
    for detail in iter_jsonl_models(detail_path, ObservationDetailEnvelope):
        if isinstance(detail.body, ReferencedObservationBody) and (
            detail.body.sha256 not in validated_bodies
        ):
            value_store.validate(detail.body.sha256, detail.body.byte_length)
            validated_bodies.add(detail.body.sha256)
        validator.record_detail(detail)
        detail_count += 1
    for event in iter_jsonl_models(usage_path, WorkflowUsageEvent):
        validator.record_usage(event)
        usage_count += 1
    try:
        validator.validate()
    except ValueError as integrity_error:
        if status == "completed":
            raise
        diagnostic = str(integrity_error).strip()[:500] or "provider evidence is incomplete"
        integrity = ProviderEvidenceIntegrity(
            integrity="incomplete",
            diagnostic=diagnostic,
        )
    else:
        integrity = ProviderEvidenceIntegrity(integrity="complete")
    return SegmentEvidenceSummary(
        provider_evidence=integrity,
        trace_count=trace_count,
        detail_count=detail_count,
        usage_count=usage_count,
    )


def count_jsonl_records(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as source:
        return sum(1 for line in source if line.strip())


def iter_jsonl_models(path: Path, model: type[Any]) -> Iterable[Any]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                yield model.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"invalid {path.name} record at line {line_number}: {exc}"
                ) from exc


__all__ = [
    "PersistedDetailSink",
    "SegmentEvidenceSummary",
    "count_jsonl_records",
    "iter_jsonl_models",
    "summarize_segment_evidence",
]
