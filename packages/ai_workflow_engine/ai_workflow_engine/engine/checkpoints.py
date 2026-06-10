"""Checkpoint stores for reusable workflow session progress."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from ai_workflow_engine.models import WorkflowCheckpoint


class CheckpointStore(Protocol):
    """Stores workflow checkpoints outside product domain state."""

    def save(self, checkpoint: WorkflowCheckpoint) -> None:
        ...

    def latest(self, workflow_id: str) -> WorkflowCheckpoint | None:
        ...

    def list(self, workflow_id: str) -> list[WorkflowCheckpoint]:
        ...


class InMemoryCheckpointStore:
    """Checkpoint store for tests and short local runs."""

    def __init__(self) -> None:
        self._items: dict[str, list[WorkflowCheckpoint]] = {}

    def save(self, checkpoint: WorkflowCheckpoint) -> None:
        assert_checkpoint_payload_safe(checkpoint.data)
        self._items.setdefault(checkpoint.workflow_id, []).append(checkpoint)

    def latest(self, workflow_id: str) -> WorkflowCheckpoint | None:
        items = self._items.get(workflow_id, [])
        return items[-1] if items else None

    def list(self, workflow_id: str) -> list[WorkflowCheckpoint]:
        return list(self._items.get(workflow_id, []))


class JsonlCheckpointStore:
    """Append-only JSONL checkpoint store that can reload the latest run state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, checkpoint: WorkflowCheckpoint) -> None:
        assert_checkpoint_payload_safe(checkpoint.data)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(checkpoint.model_dump_json())
            fh.write("\n")

    def latest(self, workflow_id: str) -> WorkflowCheckpoint | None:
        items = self.list(workflow_id)
        return items[-1] if items else None

    def list(self, workflow_id: str) -> list[WorkflowCheckpoint]:
        if not self.path.exists():
            return []
        checkpoints: list[WorkflowCheckpoint] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                checkpoint = WorkflowCheckpoint.model_validate_json(line)
                if checkpoint.workflow_id == workflow_id:
                    checkpoints.append(checkpoint)
        return checkpoints


def assert_checkpoint_payload_safe(value: Any) -> None:
    """Reject raw bytes (and image transport payloads) before checkpoint state is persisted."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("checkpoint data must store refs, not raw bytes")
    # ImageInput is a per-call transport object that may carry base64 image bytes; persistent
    # state must keep byte-free EvidenceRefs instead (privacy invariant: no raw media at rest).
    if value.__class__.__name__ == "ImageInput" and hasattr(value, "fingerprint"):
        raise ValueError(
            "checkpoint data must not contain ImageInput transport payloads; store EvidenceRefs"
        )
    if isinstance(value, dict):
        for item in value.values():
            assert_checkpoint_payload_safe(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            assert_checkpoint_payload_safe(item)
        return


def jsonable_checkpoint_data(value: Any) -> Any:
    """Convert common model outputs into JSON-compatible checkpoint data."""

    assert_checkpoint_payload_safe(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return json.loads(json.dumps(value, default=str))
