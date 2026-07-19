"""Run-local retention of completed capability artifacts.

Graph state is not an artifact ledger: caller cancellation can interrupt a fan-out or child
workflow after one capability returns but before its node update commits. This owner receives
each normalized capability result at the runtime boundary and gives the run session one ordered,
deduplicated source for terminal bundle finalization.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ai_workflow_engine.models import WorkflowArtifact


class ArtifactIdentityConflict(ValueError):
    """One artifact id was reused for different evidence."""


def _artifact_fingerprint(artifact: WorkflowArtifact) -> str:
    # Keep observation machinery out of the simple import tier. Artifact projection is needed
    # only when a capability actually publishes evidence.
    from ai_workflow_engine.observability_capture import byte_free

    return json.dumps(
        byte_free(artifact),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


class RunArtifactJournal:
    """Insertion-ordered, idempotent artifact retention for exactly one run session."""

    def __init__(self, artifacts: Iterable[WorkflowArtifact | dict[str, Any]] = ()) -> None:
        self._artifacts: dict[str, WorkflowArtifact] = {}
        self._fingerprints: dict[str, str] = {}
        self.retain(artifacts)

    def retain(self, artifacts: Iterable[WorkflowArtifact | dict[str, Any]]) -> None:
        """Atomically retain one invocation's artifacts.

        The whole batch is validated before mutation. Repeating an identical artifact id is
        idempotent; presenting different evidence under an existing id fails loudly.
        """

        pending: dict[str, tuple[WorkflowArtifact, str]] = {}
        for raw in artifacts:
            artifact = (
                raw
                if isinstance(raw, WorkflowArtifact)
                else WorkflowArtifact.model_validate(raw)
            )
            artifact = artifact.model_copy(deep=True)
            artifact_id = artifact.artifact_id
            if not artifact_id.strip():
                raise ValueError("artifact_id must be nonblank for run-level retention")
            fingerprint = _artifact_fingerprint(artifact)
            known_fingerprint = self._fingerprints.get(artifact_id)
            if known_fingerprint is None and artifact_id in pending:
                known_fingerprint = pending[artifact_id][1]
            if known_fingerprint is not None:
                if known_fingerprint != fingerprint:
                    raise ArtifactIdentityConflict(
                        f"artifact_id {artifact_id!r} was reused for different evidence"
                    )
                continue
            pending[artifact_id] = (artifact, fingerprint)

        for artifact_id, (artifact, fingerprint) in pending.items():
            self._artifacts[artifact_id] = artifact
            self._fingerprints[artifact_id] = fingerprint

    def snapshot(self) -> list[WorkflowArtifact]:
        """Return a deep, insertion-ordered snapshot safe for bundle finalization."""

        return [artifact.model_copy(deep=True) for artifact in self._artifacts.values()]
