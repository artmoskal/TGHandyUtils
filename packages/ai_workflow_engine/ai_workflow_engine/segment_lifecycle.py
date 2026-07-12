"""R1: THE single owner of observation-segment identity and attempt lifecycle.

Three identities were previously conflated — machine position (replay state), logical
segment (run + contiguous index), physical attempt (one execution try of a logical
segment). This module owns the physical side and its lifecycle:

- **Key derivation.** Every physical directory name is minted here: initial = the run id;
  durable attempt K of logical index i = ``<run>--s{i:03d}`` (K=1) / ``…-r{K}`` (K>1);
  local resume = ``<run>--s{i:03d}-<uuid8>``; terminal evidence = ``<run>--s{i:03d}-wfail``.
  Machine snapshots and wait records carry ONLY the logical index.
- **Append-only lifecycle markers.** ``commit.json`` promotes a finalized DURABLE
  attempt to canonical at the engine-owned moment AFTER the coordinator terminalizes —
  the only place where disk and coordinator can disagree. Attempt-less segments (initial,
  local resume, wait_terminal) have no second commit fact: finalize IS their commit.
  ``abandoned.json`` demotes a dead prior attempt — even one that finalized but never
  committed. ``meta.json`` is write-once by its owner: a
  zombie claimant finishing late can rewrite meta, but it can never un-commit or
  un-abandon — the markers stand, so readers derive truth locally with no coordinator
  access and no rewrite races.
- **Terminal evidence.** A wait that fails with NO continuation gets a ``wait_terminal``
  segment built ONLY from facts persisted at registration (origin index + definition
  bytes); a wait that failed THROUGH a continuation (``resume_failed``) has its evidence
  already — the failing attempt — and only its commit marker is ensured.

Hard boundaries: no executor import, no wait-module import at module level (records are
duck-read), no background task/timer. Callers: the engine facade (builder) only.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from ai_workflow_engine.observation_bundle import (
    ABANDON_MARKER_NAME,
    COMMIT_MARKER_NAME,
    ObservationSegment,
    open_observation_run_bundle,
    write_minimal_abandoned_meta,
)

logger = logging.getLogger(__name__)

__all__ = [
    "attempt_segment_id",
    "commit_attempt",
    "ensure_terminal_evidence",
    "initial_segment",
    "local_segment_id",
    "reconcile_prior_attempts",
    "terminal_segment_id",
]


def attempt_segment_id(run_id: str, segment_index: int, attempt: int) -> str:
    """Deterministic physical key of durable attempt K at a logical index."""

    stem = f"{run_id}--s{segment_index:03d}"
    return stem if attempt <= 1 else f"{stem}-r{attempt}"


def local_segment_id(run_id: str, segment_index: int) -> str:
    """Physical key for an in-process (local) resume — unique per invocation."""

    return f"{run_id}--s{segment_index:03d}-{uuid.uuid4().hex[:8]}"


def terminal_segment_id(run_id: str, segment_index: int) -> str:
    return f"{run_id}--s{segment_index:03d}-wfail"


def initial_segment(run_id: str, definition_digest: str) -> ObservationSegment:
    return ObservationSegment(
        segment_id=run_id,
        segment_index=0,
        kind="initial",
        definition_digest=definition_digest,
    )


def commit_attempt(
    base_dir: str | Path,
    segment_id: str,
    *,
    wait_id: Optional[str] = None,
    event_id: Optional[str] = None,
    attempt: Optional[int] = None,
    resolution: Optional[str] = None,
) -> bool:
    """Promote a finalized attempt to CANONICAL (append-only ``commit.json``).

    Durable path: called by the engine facade after the coordinator terminalized the
    wait — an attempt without this marker is provisional, exactly the crashed-between-
    finalize-and-terminalize window. Local path: called right after a successful resume.
    Best-effort by contract (the typed outcome reports it); returns True when the marker
    exists afterwards.
    """

    path = Path(base_dir) / segment_id
    marker = path / COMMIT_MARKER_NAME
    try:
        if marker.exists():
            return True
        if not path.is_dir():
            logger.error(
                "cannot commit attempt segment %s — directory missing under %s",
                segment_id,
                base_dir,
            )
            return False
        marker.write_text(
            json.dumps(
                {
                    "wait_id": wait_id,
                    "event_id": event_id,
                    "attempt": attempt,
                    "resolution": resolution,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return True
    except OSError:
        logger.exception("failed to write commit marker for %s", segment_id)
        return False


def reconcile_prior_attempts(
    base_dir: str | Path,
    *,
    run_id: str,
    segment_index: int,
    definition: Any,
    definition_digest: Optional[str],
    upto_attempt: int,
) -> list[str]:
    """Demote every PRIOR attempt of this logical segment to ``abandoned`` (append-only).

    Runs at the next engine-owned moment for the wait (a later delivery attempt, or the
    terminal-evidence write). Covers BOTH crash shapes: an attempt that died before
    finalizing (no meta.json — a minimal one is written so retention can attribute the
    directory) and an attempt that FINALIZED but never received its commit marker (the
    coordinator never terminalized for it, or a later claim exists — either way it is not
    canonical history). Committed attempts are never touched; the CURRENT attempt
    (``upto_attempt``) is never touched — the active claimant owns it.
    """

    base = Path(base_dir)
    reconciled: list[str] = []
    for attempt in range(1, max(1, int(upto_attempt))):
        name = attempt_segment_id(run_id, segment_index, attempt)
        path = base / name
        if not path.is_dir():
            continue
        if (path / COMMIT_MARKER_NAME).exists():
            continue  # canonical history is immutable
        try:
            if not (path / "meta.json").exists():
                write_minimal_abandoned_meta(
                    path,
                    run_id=run_id,
                    definition=definition,
                    segment_index=segment_index,
                    definition_digest=definition_digest,
                    attempt=attempt,
                )
            marker = path / ABANDON_MARKER_NAME
            if not marker.exists():
                marker.write_text(
                    json.dumps({"attempt": attempt, "superseded_by_attempt": int(upto_attempt)}),
                    encoding="utf-8",
                )
            reconciled.append(name)
        except OSError:
            logger.exception("failed to reconcile abandoned attempt %s", name)
    return reconciled


async def ensure_terminal_evidence(observation: Any, runtime: Any, wait_id: str) -> str:
    """W3R.3b/R1: converge on-disk observation with coordinator truth for a FAILED wait.

    Built ONLY from facts persisted at registration (logical origin index on the record,
    canonical definition bytes in the coordinator). Routing by persisted ``failure_kind``:

    - ``resume_failed`` — a continuation attempt IS the evidence; ensure its commit marker
      (repairs the crashed-between-fail-and-marker window) and write nothing else. Writing
      a ``wait_terminal`` here would collide with the attempt's logical index (F1).
    - every other kind (digest_mismatch / snapshot_missing / attempts_exhausted /
      cancelled) — no continuation executed for the failing claim: ensure the
      ``wait_terminal`` segment exists (idempotent: an existing finalized segment is
      validated, a partial one is rebuilt), and demote crashed prior attempts.

    Returns the typed status the delivery outcome carries: recorded | failed | skipped.
    """

    if observation is None or not getattr(observation, "enabled", False):
        return "skipped"
    try:
        from ai_workflow_engine.models import WorkflowTraceEvent  # call-time (F1.1)
        from ai_workflow_engine.workflow import WorkflowDefinition

        record = await runtime.coordinator.get(wait_id)
        if record is None or record.status not in ("failed", "cancelled", "completed"):
            return "skipped"
        if record.origin_segment_index is None:
            # registered without observation: no on-disk group exists that could misreport
            return "skipped"
        segment_index = int(record.origin_segment_index) + 1
        if record.status == "completed" or record.failure_kind == "resume_failed":
            # R0R3-C1: a wait that terminalized THROUGH a continuation (completed, or
            # failed inside the resumed run) has its evidence already — the executed
            # attempt. Converge by ensuring ITS commit marker from persisted wait facts,
            # so a transient marker-write failure is repaired by any later redelivery.
            attempt = int(record.resume_attempts or 1)
            committed = commit_attempt(
                observation.bundle_dir,
                attempt_segment_id(str(record.run_id), segment_index, attempt),
                wait_id=wait_id,
                attempt=attempt,
                resolution=str(record.status if record.status == "completed" else "failed"),
            )
            return "recorded" if committed else "failed"
        definition_json = await runtime.coordinator.load_definition(wait_id)
        if not definition_json:
            logger.error(
                "wait %s failed (%s) but the coordinator returned no registered definition "
                "bytes — adapter integrity failure; terminal observation cannot be written",
                wait_id,
                record.failure_kind,
            )
            return "failed"
        registered = WorkflowDefinition.model_validate_json(definition_json)
        segment_id = terminal_segment_id(str(record.run_id), segment_index)
        segment_path = Path(observation.bundle_dir) / segment_id
        meta_path = segment_path / "meta.json"
        if meta_path.exists():
            # at-least-once redelivery after the evidence write: validate, never append
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            existing_definition = WorkflowDefinition.model_validate_json(
                (segment_path / "definition.json").read_text(encoding="utf-8")
            )
            expected = {
                "run_id": str(record.run_id),
                "segment_id": segment_id,
                "segment_index": segment_index,
                "segment_kind": "wait_terminal",
                "definition_digest": record.definition_digest,
            }
            mismatches = [name for name, value in expected.items() if meta.get(name) != value]
            if existing_definition.definition_digest() != record.definition_digest:
                mismatches.append("definition.json")
            if mismatches:
                raise RuntimeError(
                    f"existing terminal observation segment {segment_id!r} has DIFFERENT "
                    f"{', '.join(mismatches)} — refusing to call corrupt evidence recorded"
                )
            return "recorded"
        if segment_path.exists():
            # crash mid-write left JSONL without meta — the terminal segment is fully
            # reconstructible from registered facts, so rebuild it cleanly
            shutil.rmtree(segment_path)
        reconcile_prior_attempts(
            observation.bundle_dir,
            run_id=str(record.run_id),
            segment_index=segment_index,
            definition=registered,
            definition_digest=record.definition_digest,
            upto_attempt=int(record.resume_attempts or 0) + 1,
        )
        bundle = open_observation_run_bundle(
            observation.bundle_dir,
            str(record.run_id),
            retention_limit=observation.retention_limit,
            artifact_policy=observation.artifacts,
            artifact_max_bytes=observation.artifact_max_bytes,
            evict_suspended_after_s=observation.evict_suspended_after_s,
            segment=ObservationSegment(
                segment_id=segment_id,
                segment_index=segment_index,
                kind="wait_terminal",
                definition_digest=record.definition_digest,
            ),
        )
        bundle.trace_sink.record(
            WorkflowTraceEvent(
                node=record.suspended_node,
                decision="wait:failed",
                node_status="failed",
                phase="node:result",
                error=(record.failure_detail or record.failure_kind or "wait failed")[:500],
                run_id=str(record.run_id),
                metadata={
                    "wait_id": wait_id,
                    "failure_kind": record.failure_kind or "unknown",
                    "resume_attempts": record.resume_attempts,
                },
            )
        )
        bundle.finalize(registered, status=str(record.status))
        return "recorded"
    except Exception:
        logger.exception(
            "failed to write terminal observation segment for wait %s — the typed delivery "
            "outcome carries terminal_observation=failed; the observation group may still "
            "show the run as suspended",
            wait_id,
        )
        return "failed"
