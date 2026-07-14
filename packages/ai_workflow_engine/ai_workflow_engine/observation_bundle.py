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
from typing import Any, Iterable, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
from ai_workflow_engine.usage_events import JsonlUsageSink, UsageSink
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


BUNDLE_SCHEMA_VERSION = 2

# The REAL closed status vocabulary a bundle can be finalized with: every WorkflowResultStatus
# the engine's session close can carry, wait-terminal resolutions written by the reclaim path
# (cancelled/timeout evidence), and the retention-owned "abandoned" marker.
# The ONE closed status vocabulary — a typed Literal so the published JSON schema carries the
# enum itself (C2-2), and a runtime tuple derived from it for message text and membership checks.
BundleStatus = Literal[
    "accepted", "completed", "failed", "unknown", "uncertain", "partial",
    "low_confidence", "insufficient_evidence", "requires_user_input",
    "external_tool_unavailable", "cancelled", "timeout", "abandoned",
]
_BUNDLE_STATUSES = get_args(BundleStatus)


def _looks_like_path(value: str) -> bool:
    """True when an identity string carries path syntax (separators, traversal, drive/root)."""

    return (
        "/" in value
        or "\\" in value
        or value in (".", "..")
        or value.startswith("..")
        or value.startswith("~")
        or (len(value) >= 2 and value[1] == ":")
    )


class ObservationBundleMetaV2(BaseModel):
    """The CLOSED, versioned meta contract of one observation segment (v0.11, manifest row M9).

    Every engine-written bundle is a segment of a logical run and carries its full identity,
    file layout, counts, cost truth, and the definition digest. Unknown or missing fields fail;
    pre-v2 metas are rejected by :func:`load_bundle_meta_v2` with the historical-tag route (the
    current line has no importer). The v1 duplicate ``workflow`` alias key is gone."""

    model_config = ConfigDict(extra="forbid")

    bundle_schema_version: Literal[2]
    run_id: str
    workflow_id: str
    status: BundleStatus
    timestamp: str
    # C2-1: the file layout is FIXED — these are facts of the contract, not knobs. Literal
    # fields publish the truth in the schema and leave no traversal surface to validate.
    trace_path: Literal["trace.jsonl"]
    detail_path: Literal["details.jsonl"]
    usage_path: Literal["usage.jsonl"]
    definition_path: Literal["definition.json"]
    definition_digest: str
    artifact_manifest_path: Literal["artifacts.json"]
    artifact_root: Literal["artifacts"]
    artifact_count: int = Field(ge=0)
    artifacts_copied: int = Field(ge=0)
    trace_count: int = Field(ge=0)
    detail_count: int = Field(ge=0)
    usage_count: int = Field(ge=0)
    usage_totals_scope: Literal["run_cumulative_at_finalize"]
    total_tokens: int = Field(ge=0)
    metered_usd: Optional[float] = Field(default=None, allow_inf_nan=False)
    notional_usd: Optional[float] = Field(default=None, allow_inf_nan=False)
    segment_id: str
    segment_index: int = Field(ge=0)
    segment_kind: Literal["initial", "resume", "wait_terminal"]
    # durable claim ordinals are 1-based; None = initial/local segment
    attempt: Optional[int] = Field(default=None, ge=1)
    correlation_id: Optional[str] = None

    @model_validator(mode="after")
    def _coherent_segment_identity(self) -> "ObservationBundleMetaV2":
        problems: list[str] = []
        for name in ("run_id", "workflow_id", "segment_id", "timestamp", "definition_digest"):
            if not str(getattr(self, name) or "").strip():
                problems.append(f"{name} is blank")
        # C2-1: run/segment identities are PLAIN names — they become directory names and are
        # joined to configured roots by readers, so any path syntax is an attack, not an id.
        for name in ("run_id", "segment_id"):
            value = str(getattr(self, name))
            if _looks_like_path(value):
                problems.append(
                    f"{name} {value!r} contains path syntax — identities are plain names, "
                    "never paths"
                )
        # C2-2: the timestamp is the retention/ordering truth — it must parse as tz-aware
        # ISO-8601 (the writer emits UTC with a Z suffix), never a lexicographic guess.
        try:
            parsed = datetime.fromisoformat(str(self.timestamp).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                problems.append(f"timestamp {self.timestamp!r} is not timezone-aware")
        except ValueError:
            problems.append(f"timestamp {self.timestamp!r} is not ISO-8601")
        if self.segment_kind == "initial" and (self.segment_index != 0 or self.attempt is not None):
            problems.append(
                f"initial segment must have segment_index=0 and no attempt — got "
                f"index={self.segment_index}, attempt={self.attempt!r}"
            )
        if self.segment_kind in ("resume", "wait_terminal") and self.segment_index < 1:
            problems.append(f"{self.segment_kind} segment needs segment_index >= 1")
        if problems:
            raise ValueError("invalid observation-bundle meta: " + "; ".join(problems))
        return self


def load_bundle_meta_v2(bundle_dir: Path) -> ObservationBundleMetaV2:
    """The ONE meta reader for engine-owned lifecycle (retention/reclaim). Pre-v2 or malformed
    metas fail loudly with the historical route — never a permissive ``meta.get`` fallback.
    C2-1: the read is containment-checked — a ``meta.json`` symlink escaping the bundle
    directory is rejected, never followed."""

    root = Path(bundle_dir).resolve()
    meta_path = root / "meta.json"
    if not meta_path.resolve().is_relative_to(root):
        raise ValueError(
            f"observation bundle meta at {meta_path} escapes its bundle directory — "
            "symlinked metadata is rejected"
        )
    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    version = raw.get("bundle_schema_version") if isinstance(raw, dict) else None
    if version != BUNDLE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported observation-bundle schema in {Path(bundle_dir).name!r}: expected "
            f"{BUNDLE_SCHEMA_VERSION}, got {version!r} — read pre-v2 bundles with their matching "
            f"historical engine/viewer tag (the current line has no importer)"
        )
    return ObservationBundleMetaV2.model_validate(raw)


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
    # run-half. v0.11 (M9): a bundle is ALWAYS a segment — when omitted, __post_init__
    # creates the explicit initial segment of a group-of-one (dir = run_id, index 0).
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
        from ai_workflow_engine.correlation import validate_optional_correlation

        # the EXPORTED bundle boundary enforces the same schema rule as every model —
        # a blank related-run id must not enter meta through the public open_… door
        validate_optional_correlation(self.correlation_id)
        # v0.11 (M9): every bundle is a SEGMENT. A caller that supplies none gets the explicit
        # INITIAL segment of a group-of-one — created AFTER the plain-name guard above so a
        # malicious/blank run id keeps its original loud path error, not a segment error.
        if self.segment is None:
            self.segment = ObservationSegment(
                segment_id=str(self.run_id), segment_index=0, kind="initial"
            )
        self.base_dir = Path(self.base_dir)
        self.path = self.base_dir / self.segment.segment_id
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
            # the ONE digest authority (W2B/W3 waits use the same method) — no second rule
            definition_digest=definition.definition_digest(),
            # Evidence resolution (G1): dashboards resolve EvidenceRef.uri / artifact paths
            # by source_path lookup in the manifest, then open bundle_path relative to the
            # bundle directory. Artifacts prune WITH the bundle (single retention policy).
            artifact_manifest_path=ARTIFACT_MANIFEST_NAME,
            artifact_root=ARTIFACT_DIR_NAME,
            artifact_count=len(manifest),
            artifacts_copied=sum(1 for entry in manifest if entry["copied"]),
            trace_count=_line_count(self.trace_path),
            detail_count=_line_count(self.detail_path),
            usage_count=len(usage_events),
            # W4.4 honesty label: totals come from the SUMMARY the session closed with —
            # cumulative since run start. Per-segment spend lives in this segment's
            # usage.jsonl; group readers aggregate EVENTS, never these totals.
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
            # absence-not-null contract: an unset related-run id never appears in meta
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
    """Create a per-run observation source bundle — always ONE SEGMENT of a logical run
    (v0.11, manifest row M9). When no segment is supplied, the run is a group of one and its
    INITIAL segment is created here explicitly (dir = run_id, index 0) — the pre-W4
    unsegmented shape no longer exists in the engine-owned lifecycle."""

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
            if meta.status != "abandoned"
            and not (path / ABANDON_MARKER_NAME).exists()
            and (
                meta.attempt is None
                or (path / COMMIT_MARKER_NAME).exists()
            )
        ] or entries
        newest_meta = max(
            (meta for meta, _ in canonical),
            key=lambda meta: (
                meta.segment_index,
                meta.timestamp,
            ),
        )
        if newest_meta.status == "requires_user_input":
            # R4-B (opt-in): a group suspended longer than the configured cap becomes
            # evictable — VIEWER history only; the wait itself stays resumable in the
            # coordinator. Default (None) = in-flight groups are never evicted.
            if evict_suspended_after_s is None:
                continue
            newest_ts = newest_meta.timestamp
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
        timestamp = _bundle_meta(path).timestamp  # typed v2; loud on pre-v2 (M9)
        if timestamp:
            return (timestamp, path.stat().st_mtime)
    return ("", path.stat().st_mtime)


def _is_finalized_bundle(path: Path) -> bool:
    return (path / "meta.json").exists()


def _bundle_meta(path: Path) -> ObservationBundleMetaV2:
    """Typed meta of a finalized bundle — the engine-owned lifecycle reads ONLY v2 (M9).
    A pre-v2 or malformed meta in the retention tree fails LOUDLY (historical bundles are
    inspected with their historical tag, never half-read by the current engine)."""

    return load_bundle_meta_v2(path)


def _bundle_logical_run_id(path: Path) -> str:
    """Logical run id of a finalized bundle (typed meta; loud on pre-v2)."""

    return _bundle_meta(path).run_id


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
