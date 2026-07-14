"""Strict persisted contract and safe paths for observation bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_ARTIFACT_MAX_BYTES = 25 * 1024 * 1024
COMMIT_MARKER_NAME = "commit.json"
ABANDON_MARKER_NAME = "abandoned.json"
ARTIFACT_DIR_NAME = "artifacts"
ARTIFACT_MANIFEST_NAME = "artifacts.json"
BUNDLE_SCHEMA_VERSION = 2

BundleStatus = Literal[
    "accepted",
    "completed",
    "failed",
    "unknown",
    "uncertain",
    "partial",
    "low_confidence",
    "insufficient_evidence",
    "requires_user_input",
    "external_tool_unavailable",
    "cancelled",
    "timeout",
    "abandoned",
]
_BUNDLE_STATUSES = get_args(BundleStatus)


def _looks_like_path(value: str) -> bool:
    return (
        "/" in value
        or "\\" in value
        or value in (".", "..")
        or value.startswith("..")
        or value.startswith("~")
        or (len(value) >= 2 and value[1] == ":")
    )


def assert_plain_identity(value: str, *, what: str) -> str:
    """Require a storage identity to be a plain directory name, never a path."""

    text = str(value)
    if not text.strip() or _looks_like_path(text) or Path(text).is_absolute():
        raise ValueError(
            f"{what} must be a plain name — path syntax is rejected (no separators, '..', "
            f"'~', drive prefixes, absolute paths, or blanks): {text!r}"
        )
    return text


def resolve_child_dir(
    base_dir: str | Path,
    name: str,
    *,
    what: str = "observation bundle directory",
) -> Path:
    """Resolve one real child directory under a trusted configured bundle root."""

    assert_plain_identity(name, what=f"{what} name")
    base = Path(base_dir)
    child = base / name
    if child.is_symlink():
        raise ValueError(
            f"{what} {child} is a symlink — symlinked children of the bundle root are "
            "rejected (history lives in real directories)"
        )
    if child.exists() and not child.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"{what} {child} escapes the configured bundle root {base}")
    return child


@dataclass(frozen=True)
class ObservationSegment:
    """Identity of one immutable segment inside a logical observation run."""

    segment_id: str
    segment_index: int
    kind: Literal["initial", "resume", "wait_terminal"]
    definition_digest: Optional[str] = None
    attempt: Optional[int] = None

    def __post_init__(self) -> None:
        assert_plain_identity(self.segment_id, what="observation segment_id")
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
                f"{self.kind} observation segment needs segment_index >= 1, "
                f"got {self.segment_index}"
            )


class ObservationBundleMetaV2(BaseModel):
    """The CLOSED, versioned meta contract of one observation segment (v0.11, manifest row M9).

    Every engine-written bundle is a segment of a logical run and carries its full identity,
    file layout, counts, cost truth, and the definition digest. Unknown or missing fields fail;
    pre-v2 metas are rejected by :func:`load_bundle_meta_v2` with the historical-tag route (the
    current line has no importer). The v1 duplicate ``workflow`` alias key is gone.
    """

    model_config = ConfigDict(extra="forbid")

    bundle_schema_version: Literal[2]
    run_id: str
    workflow_id: str
    status: BundleStatus
    timestamp: str
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
    attempt: Optional[int] = Field(default=None, ge=1)
    correlation_id: Optional[str] = None

    @model_validator(mode="after")
    def _coherent_segment_identity(self) -> "ObservationBundleMetaV2":
        problems: list[str] = []
        for name in ("run_id", "workflow_id", "segment_id", "timestamp", "definition_digest"):
            if not str(getattr(self, name) or "").strip():
                problems.append(f"{name} is blank")
        for name in ("run_id", "segment_id"):
            try:
                assert_plain_identity(str(getattr(self, name)), what=name)
            except ValueError as exc:
                problems.append(str(exc))
        try:
            parsed = datetime.fromisoformat(str(self.timestamp).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                problems.append(f"timestamp {self.timestamp!r} is not timezone-aware")
        except ValueError:
            problems.append(f"timestamp {self.timestamp!r} is not ISO-8601")
        if self.segment_kind == "initial" and (
            self.segment_index != 0 or self.attempt is not None
        ):
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
    """Read one strict current-schema bundle meta without following child symlinks."""

    if Path(bundle_dir).is_symlink():
        raise ValueError(
            f"observation bundle directory {bundle_dir} is a symlink — symlinked children "
            "of the bundle root are rejected (history lives in real directories)"
        )
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
            f"{BUNDLE_SCHEMA_VERSION}, got {version!r} — read pre-v2 bundles with their "
            "matching historical engine/viewer tag (the current line has no importer)"
        )
    return ObservationBundleMetaV2.model_validate(raw)


__all__ = [
    "ABANDON_MARKER_NAME",
    "ARTIFACT_DIR_NAME",
    "ARTIFACT_MANIFEST_NAME",
    "BUNDLE_SCHEMA_VERSION",
    "BundleStatus",
    "COMMIT_MARKER_NAME",
    "DEFAULT_ARTIFACT_MAX_BYTES",
    "ObservationBundleMetaV2",
    "ObservationSegment",
    "assert_plain_identity",
    "load_bundle_meta_v2",
    "resolve_child_dir",
]
