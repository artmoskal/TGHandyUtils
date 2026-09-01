"""Incremental persistence and finalization of v4 observation record streams."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
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
    ObservationStreamName,
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
    trace_sha256: str
    detail_sha256: str
    usage_sha256: str
    incomplete_streams: tuple[ObservationStreamName, ...]
    stream_diagnostic: str | None
    total_tokens: int
    metered_usd: float | None
    notional_usd: float | None


@dataclass(frozen=True)
class _StreamScan:
    count: int
    sequences: frozenset[int]
    identities: frozenset[str]
    diagnostic: str | None = None


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
    expected_run_id: str,
    allow_incomplete_streams: bool = False,
) -> SegmentEvidenceSummary:
    """Validate one segment incrementally without materializing its record streams."""

    paths: dict[ObservationStreamName, Path] = {
        "trace": trace_path,
        "detail": detail_path,
        "usage": usage_path,
    }
    counts, incomplete, diagnostics, usage_totals = _scan_segment_streams(
        paths,
        value_store=value_store,
        expected_run_id=expected_run_id,
        allow_incomplete=allow_incomplete_streams,
    )
    digests = {
        name: sha256_file(path) if path.exists() else hashlib.sha256(b"").hexdigest()
        for name, path in paths.items()
    }
    integrity = _provider_integrity(
        paths, incomplete=incomplete, status=status, prefix_counts=counts
    )
    if incomplete:
        diagnostic = "; ".join(diagnostics).strip()[:500]
        integrity = ProviderEvidenceIntegrity(
            integrity="incomplete",
            diagnostic=diagnostic or "one or more compact record streams are incomplete",
        )
    return SegmentEvidenceSummary(
        provider_evidence=integrity,
        trace_count=counts["trace"],
        detail_count=counts["detail"],
        usage_count=counts["usage"],
        trace_sha256=digests["trace"],
        detail_sha256=digests["detail"],
        usage_sha256=digests["usage"],
        incomplete_streams=tuple(incomplete),
        stream_diagnostic="; ".join(diagnostics).strip()[:500] or None,
        total_tokens=usage_totals[0],
        metered_usd=usage_totals[1],
        notional_usd=usage_totals[2],
    )


def _scan_segment_streams(
    paths: dict[ObservationStreamName, Path],
    *,
    value_store: RunValueStore,
    expected_run_id: str,
    allow_incomplete: bool,
) -> tuple[
    dict[ObservationStreamName, int],
    list[ObservationStreamName],
    list[str],
    tuple[int, float | None, float | None],
]:
    models: dict[ObservationStreamName, type[Any]] = {
        "trace": WorkflowTraceEvent,
        "detail": ObservationDetailEnvelope,
        "usage": WorkflowUsageEvent,
    }
    counts: dict[ObservationStreamName, int] = {name: 0 for name in paths}
    incomplete: list[ObservationStreamName] = []
    diagnostics: list[str] = []
    seen_sequences: set[int] = set()
    seen_ids: dict[ObservationStreamName, set[str]] = {name: set() for name in paths}
    validated_bodies: set[str] = set()
    usage_events: list[WorkflowUsageEvent] = []
    for name, model in models.items():
        scan = _scan_stream(
            name,
            paths[name],
            model,
            value_store=value_store,
            expected_run_id=expected_run_id,
            seen_sequences=seen_sequences,
            seen_ids=seen_ids[name],
            validated_bodies=validated_bodies,
            allow_incomplete=allow_incomplete,
            usage_events=usage_events,
        )
        if scan.diagnostic is not None:
            incomplete.append(name)
            diagnostics.append(scan.diagnostic)
            counts[name] = scan.count
            seen_sequences.update(scan.sequences)
            seen_ids[name].update(scan.identities)
            continue
        seen_sequences.update(scan.sequences)
        seen_ids[name].update(scan.identities)
        counts[name] = scan.count
    return (
        counts,
        incomplete,
        diagnostics,
        (
            sum(event.total_tokens for event in usage_events),
            sum_optional_cost(
                event.estimated_usd for event in usage_events if event.cost_class == "metered"
            ),
            sum_optional_cost(event.notional_usd for event in usage_events),
        ),
    )


def _scan_stream(
    name: ObservationStreamName,
    path: Path,
    model: type[Any],
    *,
    value_store: RunValueStore,
    expected_run_id: str,
    seen_sequences: set[int],
    seen_ids: set[str],
    validated_bodies: set[str],
    allow_incomplete: bool,
    usage_events: list[WorkflowUsageEvent],
) -> _StreamScan:
    local_sequences = set(seen_sequences)
    local_ids = set(seen_ids)
    count = 0
    try:
        if not path.exists():
            raise FileNotFoundError(path)
        for record in iter_jsonl_models(path, model):
            _validate_record_identity(
                record,
                stream=name,
                expected_run_id=expected_run_id,
                seen_sequences=local_sequences,
                seen_ids=local_ids,
            )
            if name == "detail" and isinstance(record.body, ReferencedObservationBody) and (
                record.body.sha256 not in validated_bodies
            ):
                value_store.validate(record.body.sha256, record.body.byte_length)
                validated_bodies.add(record.body.sha256)
            count += 1
            if name == "usage":
                usage_events.append(record)
    except (FileNotFoundError, ValueError) as exc:
        if not allow_incomplete:
            raise
        return _StreamScan(
            count=count,
            sequences=frozenset(local_sequences),
            identities=frozenset(local_ids),
            diagnostic=f"{path.name}: {str(exc).strip()[:350]}",
        )
    return _StreamScan(
        count=count,
        sequences=frozenset(local_sequences),
        identities=frozenset(local_ids),
    )


def _provider_integrity(
    paths: dict[ObservationStreamName, Path],
    *,
    incomplete: list[ObservationStreamName],
    status: str,
    prefix_counts: dict[ObservationStreamName, int],
) -> ProviderEvidenceIntegrity:
    validator = ProviderEvidenceValidator()
    # Crash reconciliation may keep one malformed stream verbatim. Only complete siblings enter
    # the provider-link graph; this second bounded pass avoids retaining compact records in memory.
    if "trace" not in incomplete:
        trace_records = iter_jsonl_models(paths["trace"], WorkflowTraceEvent)
    else:
        trace_records = iter_jsonl_prefix_models(
            paths["trace"], WorkflowTraceEvent, prefix_counts["trace"]
        )
    for trace in trace_records:
        validator.record_trace(trace)
    if "detail" not in incomplete:
        detail_records = iter_jsonl_models(paths["detail"], ObservationDetailEnvelope)
    else:
        detail_records = iter_jsonl_prefix_models(
            paths["detail"], ObservationDetailEnvelope, prefix_counts["detail"]
        )
    for detail in detail_records:
            validator.record_detail(detail)
    if "usage" not in incomplete:
        usage_records = iter_jsonl_models(paths["usage"], WorkflowUsageEvent)
    else:
        usage_records = iter_jsonl_prefix_models(
            paths["usage"], WorkflowUsageEvent, prefix_counts["usage"]
        )
    for event in usage_records:
            validator.record_usage(event)
    try:
        validator.validate()
    except ValueError as integrity_error:
        if status == "completed":
            raise
        diagnostic = str(integrity_error).strip()[:500] or "provider evidence is incomplete"
        return ProviderEvidenceIntegrity(
            integrity="incomplete",
            diagnostic=diagnostic,
        )
    return ProviderEvidenceIntegrity(integrity="complete")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError(f"cannot hash observation stream {path.name}: {exc}") from exc
    return digest.hexdigest()


def _validate_record_identity(
    record: Any,
    *,
    stream: ObservationStreamName,
    expected_run_id: str,
    seen_sequences: set[int],
    seen_ids: set[str],
) -> None:
    run_id = getattr(record, "run_id", None)
    if run_id != expected_run_id:
        raise ValueError(
            f"{stream} record belongs to run {run_id!r}, not expected run {expected_run_id!r}"
        )
    sequence = getattr(record, "sequence", None)
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError(f"{stream} record requires a positive observation sequence")
    if sequence in seen_sequences:
        raise ValueError(f"duplicate observation sequence {sequence}")
    seen_sequences.add(sequence)
    identity_name = "detail_id" if stream == "detail" else "event_id"
    identity = str(getattr(record, identity_name, None) or "").strip()
    if not identity:
        raise ValueError(f"{stream} record requires a nonblank {identity_name}")
    if identity in seen_ids:
        raise ValueError(f"duplicate observation {identity_name} {identity!r} in {stream} stream")
    seen_ids.add(identity)


def count_jsonl_records(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as source:
        return sum(1 for line in source if line.strip())


def iter_jsonl_models(path: Path, model: type[Any]) -> Iterable[Any]:
    if not path.exists():
        return
    with path.open("rb") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                yield model.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"invalid {path.name} record at line {line_number}: {exc}"
                ) from exc


def iter_jsonl_prefix_models(path: Path, model: type[Any], count: int) -> Iterable[Any]:
    """Read the validated prefix of an abandoned append-only stream."""
    if count < 0:
        raise ValueError("stream prefix count must be nonnegative")
    if count == 0:
        return
    if not path.exists():
        raise ValueError(f"missing stream {path.name} has nonzero valid prefix")
    yielded = 0
    with path.open("rb") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            if yielded == count:
                break
            try:
                record = model.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"invalid validated-prefix record in {path.name} at line {line_number}: {exc}"
                ) from exc
            yielded += 1
            yield record
    if yielded != count:
        raise ValueError(
            f"{path.name} contains {yielded} valid records, expected prefix of {count}"
        )


__all__ = [
    "PersistedDetailSink",
    "SegmentEvidenceSummary",
    "count_jsonl_records",
    "iter_jsonl_models",
    "iter_jsonl_prefix_models",
    "sha256_file",
    "summarize_segment_evidence",
]
