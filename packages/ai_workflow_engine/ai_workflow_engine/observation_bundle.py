"""Durable source bundles for workflow observability records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
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
from ai_workflow_engine.usage import JsonlUsageSink, UsageSink
from ai_workflow_engine.workflow import WorkflowDefinition

logger = logging.getLogger(__name__)

# Evidence-resolution contract (G1): artifacts referenced by a run (EvidenceRef.uri /
# WorkflowArtifact.path) are archived INSIDE the bundle so they live and die with it —
# bundle retention is the ONLY cleanup policy; there is no second artifact lifecycle.
DEFAULT_ARTIFACT_MAX_BYTES = 25 * 1024 * 1024
# R1 lifecycle markers: append-only files INSIDE a segment directory. meta.json is
# write-once by its owner; these facts are never expressed by rewriting it.
COMMIT_MARKER_NAME = "commit.json"
ABANDON_MARKER_NAME = "abandoned.json"
ARTIFACT_DIR_NAME = "artifacts"
ARTIFACT_MANIFEST_NAME = "artifacts.json"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ObservationSegment:
    """W4: identity of ONE immutable observation segment inside a logical run.

    A suspended-then-resumed run is stored as a GROUP of segments — one per executed
    run-half — under the same logical ``run_id``. ``segment_id`` is the physical storage
    key (directory name); ``segment_index`` is the persisted order within the group
    (0 = initial), allocated from snapshot lineage, never from a directory scan;
    ``parent_segment_id`` names the segment whose suspension this one continues
    (``None`` for the initial segment or for resumes of pre-segment snapshots).
    """

    segment_id: str
    segment_index: int
    kind: Literal["initial", "resume", "wait_terminal"]
    definition_digest: Optional[str] = None
    # R1: durable delivery attempt ordinal (claim ordinal). None for initial and local
    # resumes. Readers select the canonical attempt per logical index from this + the
    # commit marker — physical parent pointers are gone; lineage is (run, contiguous index).
    attempt: Optional[int] = None

    def __post_init__(self) -> None:
        if not str(self.segment_id).strip():
            raise ValueError("observation segment_id must be non-blank")
        if self.kind not in ("initial", "resume", "wait_terminal"):
            raise ValueError(
                f"observation segment kind must be initial|resume|wait_terminal: {self.kind!r}"
            )
        if self.kind == "initial" and (self.segment_index != 0 or self.attempt is not None):
            raise ValueError(
                "initial observation segment must have segment_index=0 and no attempt "
                f"ordinal — got index={self.segment_index}, attempt={self.attempt!r}"
            )
        if self.kind in ("resume", "wait_terminal") and self.segment_index < 1:
            raise ValueError(
                f"{self.kind} observation segment needs segment_index >= 1, got {self.segment_index}"
            )


@dataclass
class ObservationSequence:
    """Run-local monotonic sequence shared by trace/detail/usage bundle writers."""

    value: int = 0

    def next(self) -> int:
        self.value += 1
        return self.value


class SequencedTraceSink:
    """Stamp run id + sequence before writing a trace event."""

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
    """Stamp run id + sequence before writing an observation detail."""

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
    """Stamp run id + sequence before writing a usage event."""

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
    # G1 evidence resolution: "copy" archives run artifacts into the bundle at finalize;
    # "off" still writes an honest manifest (what existed, where) without copying bytes.
    artifact_policy: Literal["copy", "off"] = "copy"
    artifact_max_bytes: int = DEFAULT_ARTIFACT_MAX_BYTES
    # W4: physical/logical split. ``run_id`` stays the LOGICAL run identity (stamped into
    # events + meta); ``segment`` supplies the physical storage key and lineage for one
    # run-half. Without a segment the bundle keeps the pre-W4 shape exactly (dir = run_id,
    # no segment meta) — a group of one.
    segment: Optional[ObservationSegment] = None
    # R4-B opt-in policy threaded from ObservationConfig (None = never evict suspended)
    evict_suspended_after_s: Optional[float] = None
    # W5-C4: caller's stable cross-run correlation, projected into meta.json when set
    correlation_id: Optional[str] = None
    sequence: ObservationSequence = field(default_factory=ObservationSequence)

    def __post_init__(self) -> None:
        # A bundle storage key is a FILESYSTEM NAME, never a path: run ids can arrive from
        # caller-supplied goal metadata, so an unchecked "../escape" or absolute path would
        # write observation files outside the configured bundle root. Reject loudly — the
        # logical run id AND the segment id (when present) both must be plain names.
        for label, value in (
            ("run_id", str(self.run_id)),
            *((("segment_id", str(self.segment.segment_id)),) if self.segment else ()),
        ):
            if (
                not value.strip()
                or value in (".", "..")
                or "/" in value
                or "\\" in value
                or Path(value).is_absolute()
            ):
                raise ValueError(
                    f"observation {label} must be a plain directory name (no separators, "
                    f"no '..', not absolute, not empty): {value!r}"
                )
        self.base_dir = Path(self.base_dir)
        self.path = self.base_dir / (self.segment.segment_id if self.segment else str(self.run_id))
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
        (self.path / "definition.json").write_text(definition.model_dump_json(), encoding="utf-8")
        usage_events = _usage_events(usage)
        manifest = self._archive_artifacts(list(artifacts or []))
        (self.path / ARTIFACT_MANIFEST_NAME).write_text(
            json.dumps(manifest, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        meta = {
            # Versioned contract for dashboards/viewers programming against the bundle.
            # Bump ONLY on breaking layout/field changes (reviewed decision, never drift).
            "bundle_schema_version": 1,
            "run_id": self.run_id,
            "workflow_id": definition.workflow_id,
            "workflow": definition.workflow_id,
            "status": status,
            "timestamp": _utc_timestamp(),
            "trace_path": self.trace_path.name,
            "detail_path": self.detail_path.name,
            "usage_path": self.usage_path.name,
            "definition_path": "definition.json",
            # Evidence resolution (G1): dashboards resolve EvidenceRef.uri / artifact paths
            # by source_path lookup in the manifest, then open bundle_path relative to the
            # bundle directory. Artifacts prune WITH the bundle (single retention policy).
            "artifact_manifest_path": ARTIFACT_MANIFEST_NAME,
            "artifact_root": ARTIFACT_DIR_NAME,
            "artifact_count": len(manifest),
            "artifacts_copied": sum(1 for entry in manifest if entry["copied"]),
            "trace_count": _line_count(self.trace_path),
            "detail_count": _line_count(self.detail_path),
            "usage_count": len(usage_events),
            # W4.4 honesty label: these totals are computed from the SUMMARY the session
            # closed with — cumulative since run start (a resumed segment's summary includes
            # pre-suspension events restored from the snapshot). Per-segment spend lives in
            # this segment's usage.jsonl; group readers aggregate EVENTS, never these totals.
            **({"usage_totals_scope": "run_cumulative_at_finalize"} if self.segment else {}),
            "total_tokens": sum(event.total_tokens for event in usage_events),
            "metered_usd": _sum_cost(
                event.estimated_usd
                for event in usage_events
                if event.cost_class == "metered"
            ),
            "notional_usd": _sum_cost(event.notional_usd for event in usage_events),
        }
        if self.correlation_id:
            meta["correlation_id"] = self.correlation_id
        if self.segment is not None:
            # W4.1 additive segment identity (schema stays v1: pure addition; absence of
            # these fields marks a pre-segment bundle, which readers treat as a group of one).
            meta.update(_segment_meta_fields(self.segment))
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
        """Copy run artifacts into the bundle and return honest manifest entries.

        Every artifact gets an entry; entries that could NOT be archived say why
        (``skip_reason``) instead of silently vanishing — an unresolvable evidence link
        must be diagnosable from the manifest alone. Archive failures never fail the run
        (same contract as observation projection): they are logged + recorded.
        """

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
                    # Same file referenced twice (e.g. fanout aggregation): one copy,
                    # both manifest entries resolve to it.
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
    """Create a per-run observation source bundle (one SEGMENT of a logical run when
    ``segment`` is given; the pre-W4 single-directory shape otherwise)."""

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
    """W4.1/R1 segment identity keys — the ONE source both meta writers share (F12)."""

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
    """R1: give a crashed, never-finalized attempt directory a minimal attributable meta
    (status ``abandoned``) so group retention owns it. Identity keys come from the same
    helper the full finalize uses — the two writers cannot drift on the segment contract."""

    (path / "definition.json").write_text(definition.model_dump_json(), encoding="utf-8")
    segment = ObservationSegment(
        segment_id=path.name,
        segment_index=segment_index,
        kind="resume",
        definition_digest=definition_digest,
        attempt=attempt,
    )
    meta = {
        "bundle_schema_version": 1,
        "run_id": run_id,
        "workflow_id": definition.workflow_id,
        "workflow": definition.workflow_id,
        "status": "abandoned",
        "timestamp": _utc_timestamp(),
        "trace_path": "trace.jsonl",
        "detail_path": "details.jsonl",
        "usage_path": "usage.jsonl",
        "definition_path": "definition.json",
        "trace_count": _line_count(path / "trace.jsonl"),
        "detail_count": _line_count(path / "details.jsonl"),
        "usage_count": _line_count(path / "usage.jsonl"),
        **({"correlation_id": correlation_id} if correlation_id else {}),
        **_segment_meta_fields(segment),
    }
    (path / "meta.json").write_text(json.dumps(meta, sort_keys=True, indent=2), encoding="utf-8")


def prune_observation_bundles(
    base_dir: str | Path,
    retention_limit: int,
    *,
    evict_suspended_after_s: Optional[float] = None,
) -> None:
    """Keep only the newest logical-run observation groups.

    ``0`` means "latest/current only", matching the Anki debug-retention convention.
    W4.5: the unit of retention is the LOGICAL RUN — all segments of one run live and die
    together (a continuation is never retained while its suspension is deleted), and a
    group whose newest segment is still awaiting resume (``requires_user_input``) is
    in-flight and never pruned: deleting it would destroy the history a registered wait
    still needs. Pre-segment bundles are each their own group, so single-run behavior is
    unchanged.
    """

    base = Path(base_dir)
    if not base.exists():
        return
    keep = max(1, int(retention_limit))
    groups: dict[str, list[Path]] = {}
    for path in base.iterdir():
        if path.is_dir() and _is_finalized_bundle(path):
            groups.setdefault(_bundle_logical_run_id(path), []).append(path)
    prunable: list[tuple[tuple[str, float], list[Path]]] = []
    for paths in groups.values():
        entries = [(_bundle_meta(path), path) for path in paths]
        # liveness is judged on CANONICAL history only: initial segments and COMMITTED
        # attempts. Abandoned or merely provisional (finalized-but-uncommitted) attempts
        # must neither outrank a still-suspended segment nor unprotect the group.
        canonical = [
            (meta, path)
            for meta, path in entries
            if str(meta.get("status")) != "abandoned"
            and not (path / ABANDON_MARKER_NAME).exists()
            and (
                meta.get("attempt") is None
                or (path / COMMIT_MARKER_NAME).exists()
            )
        ] or entries
        newest_meta = max(
            (meta for meta, _ in canonical),
            key=lambda meta: (
                int(meta.get("segment_index") or 0),
                str(meta.get("timestamp") or ""),
            ),
        )
        if str(newest_meta.get("status")) == "requires_user_input":
            # R4-B (opt-in): a group suspended longer than the configured cap becomes
            # evictable — VIEWER history only; the wait itself stays resumable in the
            # coordinator. Default (None) = in-flight groups are never evicted.
            if evict_suspended_after_s is None:
                continue
            newest_ts = str(newest_meta.get("timestamp") or "")
            try:
                suspended_at = datetime.fromisoformat(newest_ts.replace("Z", "+00:00"))
            except ValueError:
                continue  # unparseable timestamp: keep protecting rather than guess
            age_s = (datetime.now(timezone.utc) - suspended_at).total_seconds()
            if age_s <= evict_suspended_after_s:
                continue
        prunable.append((max(_bundle_sort_key(path) for path in paths), paths))
    if len(prunable) <= keep:
        return
    prunable.sort(key=lambda entry: entry[0], reverse=True)
    for _, paths in prunable[keep:]:
        for path in paths:
            shutil.rmtree(path)


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


def _bundle_sort_key(path: Path) -> tuple[str, float]:
    meta_path = path / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        timestamp = str(meta.get("timestamp") or "")
        if timestamp:
            return (timestamp, path.stat().st_mtime)
    return ("", path.stat().st_mtime)


def _is_finalized_bundle(path: Path) -> bool:
    return (path / "meta.json").exists()


def _bundle_meta(path: Path) -> dict:
    try:
        return json.loads((path / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _bundle_logical_run_id(path: Path) -> str:
    """Logical run id of a finalized bundle: meta.run_id, else the directory name."""

    return str(_bundle_meta(path).get("run_id") or path.name)


def _artifact_filename(artifact_id: str, source: Path, used: set[str]) -> str:
    """Collision-proof bundle-local file name: sanitized id prefix + source basename."""

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
    """Stream-copy ``source`` to ``target`` returning (size_bytes, sha256) in one read."""

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
