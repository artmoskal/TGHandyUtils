"""Sequenced writing, finalization, and artifact archival for observation bundles."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Iterable, Literal, Optional

from ai_workflow_engine.engine.capabilities import (
    DetailSink,
    JsonlDetailSink,
    JsonlTraceSink,
    TraceSink,
)
from ai_workflow_engine.models import (
    ObservationDetail,
    WorkflowArtifact,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.observability_capture import byte_free
from ai_workflow_engine.observation_contract import (
    ARTIFACT_DIR_NAME,
    ARTIFACT_MANIFEST_NAME,
    BUNDLE_SCHEMA_VERSION,
    DEFAULT_ARTIFACT_MAX_BYTES,
    ObservationBundleMetaV2,
    ObservationSegment,
    assert_plain_identity,
    resolve_child_dir,
)
from ai_workflow_engine.observation_retention import prune_observation_bundles
from ai_workflow_engine.usage_events import JsonlUsageSink, UsageSink
from ai_workflow_engine.workflow import WorkflowDefinition


logger = logging.getLogger(__name__)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ObservationSequence:
    """Run-local monotonic sequence shared by trace/detail/usage bundle writers."""

    value: int = 0

    def next(self) -> int:
        self.value += 1
        return self.value


class SequencedTraceSink:
    """Stamp run id and sequence before writing a trace event."""

    def __init__(self, inner: TraceSink, *, run_id: str, sequence: ObservationSequence) -> None:
        self.inner = inner
        self.run_id = run_id
        self.sequence = sequence

    def record(self, event: WorkflowTraceEvent) -> None:
        updates = {}
        if event.run_id is None:
            updates["run_id"] = self.run_id
        if event.sequence is None:
            updates["sequence"] = self.sequence.next()
        self.inner.record(event.model_copy(update=updates) if updates else event)


class SequencedDetailSink:
    """Stamp run id and sequence before writing an observation detail."""

    def __init__(self, inner: DetailSink, *, run_id: str, sequence: ObservationSequence) -> None:
        self.inner = inner
        self.run_id = run_id
        self.sequence = sequence

    def record(self, detail: ObservationDetail) -> None:
        updates = {}
        if detail.run_id is None:
            updates["run_id"] = self.run_id
        if detail.sequence is None:
            updates["sequence"] = self.sequence.next()
        self.inner.record(detail.model_copy(update=updates) if updates else detail)

    def clear(self) -> None:
        clear = getattr(self.inner, "clear", None)
        if callable(clear):
            clear()


class SequencedUsageSink:
    """Stamp run id and sequence before writing a usage event."""

    def __init__(self, inner: UsageSink, *, run_id: str, sequence: ObservationSequence) -> None:
        self.inner = inner
        self.run_id = run_id
        self.sequence = sequence

    def record(self, event: WorkflowUsageEvent) -> None:
        metadata = dict(event.metadata or {})
        metadata.setdefault("run_id", self.run_id)
        metadata.setdefault("workflow_id", self.run_id)
        updates = {"metadata": metadata}
        if event.run_id is None:
            updates["run_id"] = self.run_id
        if event.sequence is None:
            updates["sequence"] = self.sequence.next()
        self.inner.record(event.model_copy(update=updates))


@dataclass
class ObservationRunBundle:
    """Per-run source bundle used by external viewers."""

    base_dir: Path
    run_id: str
    retention_limit: Optional[int] = None
    artifact_policy: Literal["copy", "off"] = "copy"
    artifact_max_bytes: int = DEFAULT_ARTIFACT_MAX_BYTES
    segment: Optional[ObservationSegment] = None
    evict_suspended_after_s: Optional[float] = None
    correlation_id: Optional[str] = None
    sequence: ObservationSequence = field(default_factory=ObservationSequence)

    def __post_init__(self) -> None:
        assert_plain_identity(str(self.run_id), what="observation run_id")
        from ai_workflow_engine.correlation import validate_optional_correlation

        validate_optional_correlation(self.correlation_id)
        if self.segment is None:
            self.segment = ObservationSegment(
                segment_id=str(self.run_id), segment_index=0, kind="initial"
            )
        self.base_dir = Path(self.base_dir)
        self.path = resolve_child_dir(
            self.base_dir,
            self.segment.segment_id,
            what="observation segment directory",
        )
        self.path.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.path / "trace.jsonl"
        self.detail_path = self.path / "details.jsonl"
        self.usage_path = self.path / "usage.jsonl"
        for target in (self.trace_path, self.detail_path, self.usage_path):
            target.touch(exist_ok=True)
        self.trace_sink = SequencedTraceSink(
            JsonlTraceSink(self.trace_path),
            run_id=self.run_id,
            sequence=self.sequence,
        )
        self.detail_sink = SequencedDetailSink(
            JsonlDetailSink(self.detail_path),
            run_id=self.run_id,
            sequence=self.sequence,
        )
        self.usage_sink = SequencedUsageSink(
            JsonlUsageSink(self.usage_path),
            run_id=self.run_id,
            sequence=self.sequence,
        )

    def finalize(
        self,
        definition: WorkflowDefinition,
        *,
        status: str,
        usage: WorkflowUsageSummary | Iterable[WorkflowUsageEvent] | None = None,
        artifacts: Iterable[WorkflowArtifact] | None = None,
    ) -> None:
        if self.segment is None:
            raise RuntimeError(
                "engine-owned observation lifecycle emits ONLY segmented v2 bundles "
                "(v0.11, manifest row M9) — open the bundle with an ObservationSegment "
                "(open_observation_run_bundle supplies the initial segment automatically)"
            )
        definition_json = definition.model_dump_json()
        (self.path / "definition.json").write_text(definition_json, encoding="utf-8")
        usage_events = _usage_events(usage)
        manifest = self._archive_artifacts(list(artifacts or []))
        (self.path / ARTIFACT_MANIFEST_NAME).write_text(
            json.dumps(manifest, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        meta_model = ObservationBundleMetaV2(
            bundle_schema_version=BUNDLE_SCHEMA_VERSION,
            run_id=str(self.run_id),
            workflow_id=definition.workflow_id,
            status=status,
            timestamp=_utc_timestamp(),
            trace_path=self.trace_path.name,
            detail_path=self.detail_path.name,
            usage_path=self.usage_path.name,
            definition_path="definition.json",
            definition_digest=definition.definition_digest(),
            artifact_manifest_path=ARTIFACT_MANIFEST_NAME,
            artifact_root=ARTIFACT_DIR_NAME,
            artifact_count=len(manifest),
            artifacts_copied=sum(1 for entry in manifest if entry["copied"]),
            trace_count=_line_count(self.trace_path),
            detail_count=_line_count(self.detail_path),
            usage_count=len(usage_events),
            usage_totals_scope="run_cumulative_at_finalize",
            total_tokens=sum(event.total_tokens for event in usage_events),
            metered_usd=_sum_cost(
                event.estimated_usd
                for event in usage_events
                if event.cost_class == "metered"
            ),
            notional_usd=_sum_cost(event.notional_usd for event in usage_events),
            segment_id=self.segment.segment_id,
            segment_index=self.segment.segment_index,
            segment_kind=self.segment.kind,
            attempt=self.segment.attempt,
            correlation_id=self.correlation_id or None,
        )
        meta = json.loads(meta_model.model_dump_json())
        if meta.get("correlation_id") is None:
            meta.pop("correlation_id", None)
        (self.path / "meta.json").write_text(
            json.dumps(meta, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        if self.retention_limit is not None:
            prune_observation_bundles(
                self.base_dir,
                self.retention_limit,
                evict_suspended_after_s=self.evict_suspended_after_s,
            )

    def _archive_artifacts(self, artifacts: list[WorkflowArtifact]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        archived_by_source: dict[str, dict[str, Any]] = {}
        used_names: set[str] = set()
        artifact_dir = self.path / ARTIFACT_DIR_NAME
        for artifact in artifacts:
            metadata = artifact.metadata or {}
            entry: dict[str, Any] = {
                "artifact_id": artifact.artifact_id,
                "source_path": artifact.path,
                "bundle_path": None,
                "copied": False,
                "skip_reason": None,
                "size_bytes": None,
                "sha256": None,
                "kind": artifact.kind,
                "source": artifact.source,
                "owner_node": artifact.owner_node,
                "media_type": metadata.get("media_type"),
                "role": metadata.get("role"),
                "metadata": byte_free(metadata),
            }
            source = Path(artifact.path) if artifact.path else None
            if source is None or not str(artifact.path).strip():
                entry["skip_reason"] = "no_source_path"
            elif not source.is_file():
                entry["skip_reason"] = "source_missing"
            else:
                entry["size_bytes"] = source.stat().st_size
                already = archived_by_source.get(str(source))
                if already is not None:
                    entry["bundle_path"] = already["bundle_path"]
                    entry["copied"] = already["copied"]
                    entry["skip_reason"] = already["skip_reason"]
                    entry["sha256"] = already["sha256"]
                elif self.artifact_policy != "copy":
                    entry["skip_reason"] = "artifact_policy_off"
                elif entry["size_bytes"] > self.artifact_max_bytes:
                    entry["skip_reason"] = "exceeds_artifact_max_bytes"
                else:
                    name = _artifact_filename(artifact.artifact_id, source, used_names)
                    target = artifact_dir / name
                    try:
                        artifact_dir.mkdir(parents=True, exist_ok=True)
                        size, digest = _copy_with_digest(source, target)
                    except OSError as exc:
                        logger.warning(
                            "observation artifact archive failed source=%s error=%s",
                            source,
                            exc,
                        )
                        entry["skip_reason"] = f"copy_failed: {exc}"
                    else:
                        entry["bundle_path"] = f"{ARTIFACT_DIR_NAME}/{name}"
                        entry["copied"] = True
                        entry["size_bytes"] = size
                        entry["sha256"] = digest
                if str(source) not in archived_by_source:
                    archived_by_source[str(source)] = entry
            entries.append(entry)
        return entries


def open_observation_run_bundle(
    base_dir: str | Path,
    run_id: str,
    *,
    retention_limit: Optional[int] = None,
    artifact_policy: Literal["copy", "off"] = "copy",
    artifact_max_bytes: int = DEFAULT_ARTIFACT_MAX_BYTES,
    segment: Optional[ObservationSegment] = None,
    evict_suspended_after_s: Optional[float] = None,
    correlation_id: Optional[str] = None,
) -> ObservationRunBundle:
    """Create one segmented observation source bundle for a logical run."""

    return ObservationRunBundle(
        Path(base_dir),
        run_id,
        retention_limit=retention_limit,
        artifact_policy=artifact_policy,
        artifact_max_bytes=artifact_max_bytes,
        segment=segment,
        evict_suspended_after_s=evict_suspended_after_s,
        correlation_id=correlation_id,
    )


def _segment_meta_fields(segment: ObservationSegment) -> dict:
    return {
        "segment_id": segment.segment_id,
        "segment_index": segment.segment_index,
        "segment_kind": segment.kind,
        "definition_digest": segment.definition_digest,
        "attempt": segment.attempt,
    }


def write_minimal_abandoned_meta(
    path: Path,
    *,
    run_id: str,
    definition: WorkflowDefinition,
    segment_index: int,
    definition_digest: Optional[str],
    attempt: int,
    correlation_id: Optional[str] = None,
) -> None:
    """Write terminal metadata for a crashed, never-finalized attempt directory."""

    definition_json = definition.model_dump_json()
    (path / "definition.json").write_text(definition_json, encoding="utf-8")
    meta_model = ObservationBundleMetaV2(
        bundle_schema_version=BUNDLE_SCHEMA_VERSION,
        run_id=run_id,
        workflow_id=definition.workflow_id,
        status="abandoned",
        timestamp=_utc_timestamp(),
        trace_path="trace.jsonl",
        detail_path="details.jsonl",
        usage_path="usage.jsonl",
        definition_path="definition.json",
        definition_digest=definition.definition_digest(),
        artifact_manifest_path=ARTIFACT_MANIFEST_NAME,
        artifact_root=ARTIFACT_DIR_NAME,
        artifact_count=0,
        artifacts_copied=0,
        trace_count=_line_count(path / "trace.jsonl"),
        detail_count=_line_count(path / "details.jsonl"),
        usage_count=_line_count(path / "usage.jsonl"),
        usage_totals_scope="run_cumulative_at_finalize",
        total_tokens=0,
        metered_usd=None,
        notional_usd=None,
        segment_id=path.name,
        segment_index=segment_index,
        segment_kind="resume",
        attempt=attempt,
        correlation_id=correlation_id or None,
    )
    meta = json.loads(meta_model.model_dump_json())
    if meta.get("correlation_id") is None:
        meta.pop("correlation_id", None)
    (path / "meta.json").write_text(
        json.dumps(meta, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def _usage_events(
    usage: WorkflowUsageSummary | Iterable[WorkflowUsageEvent] | None,
) -> list[WorkflowUsageEvent]:
    if usage is None:
        return []
    if isinstance(usage, WorkflowUsageSummary):
        return list(usage.events)
    return list(usage)


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _sum_cost(values: Iterable[float | None]) -> float | None:
    costs = [float(value) for value in values if value is not None]
    return round(sum(costs), 6) if costs else None


def _artifact_filename(artifact_id: str, source: Path, used: set[str]) -> str:
    prefix = re.sub(r"[^A-Za-z0-9_-]", "", str(artifact_id))[:8] or "artifact"
    name = source.name
    if name in ("", ".", ".."):
        name = "artifact"
    candidate = f"{prefix}-{name}"
    counter = 1
    while candidate in used:
        candidate = f"{prefix}-{counter}-{name}"
        counter += 1
    used.add(candidate)
    return candidate


def _copy_with_digest(source: Path, target: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as fin, target.open("wb") as fout:
        while True:
            chunk = fin.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            fout.write(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


__all__ = [
    "ObservationRunBundle",
    "ObservationSequence",
    "SequencedDetailSink",
    "SequencedTraceSink",
    "SequencedUsageSink",
    "open_observation_run_bundle",
    "write_minimal_abandoned_meta",
]
