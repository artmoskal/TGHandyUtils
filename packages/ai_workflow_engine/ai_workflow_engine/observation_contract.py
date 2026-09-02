"""Strict persisted contract and safe paths for observation bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal, Optional, Union, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_ARTIFACT_MAX_BYTES = 25 * 1024 * 1024
COMMIT_MARKER_NAME = "commit.json"
ABANDON_MARKER_NAME = "abandoned.json"
ARTIFACT_DIR_NAME = "artifacts"
ARTIFACT_MANIFEST_NAME = "artifacts.json"
BUNDLE_SCHEMA_VERSION = 4
INLINE_BODY_MAX_BYTES = 4_096

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
ObservationStreamName = Literal["trace", "detail", "usage"]
ObservationDetailKind = Literal[
    "rendered_prompt",
    "llm_response",
    "tool_payload",
    "tool_result",
    "artifact_preview",
    "planner_output",
    "memory_projection",
]


def canonical_json_chunks(value: Any) -> Iterator[bytes]:
    """Yield the persisted canonical JSON representation."""

    encoder = json.JSONEncoder(
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    for chunk in encoder.iterencode(value):
        yield chunk.encode("utf-8")


def canonical_json_bytes(value: Any) -> bytes:
    return b"".join(canonical_json_chunks(value))


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


class ProviderEvidenceIntegrity(BaseModel):
    """Persisted truth about cross-record provider evidence in one finalized segment."""

    model_config = ConfigDict(extra="forbid")

    integrity: Literal["complete", "incomplete"]
    diagnostic: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _coherent_integrity(self) -> "ProviderEvidenceIntegrity":
        if self.integrity == "complete" and self.diagnostic is not None:
            raise ValueError("complete provider evidence must not carry a diagnostic")
        if self.integrity == "incomplete" and not str(self.diagnostic or "").strip():
            raise ValueError("incomplete provider evidence requires a bounded diagnostic")
        return self


class InlineJsonObservationBody(BaseModel):
    """Canonical JSON stored directly in a compact detail envelope."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["inline_json"]
    value: Any
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0, le=INLINE_BODY_MAX_BYTES)

    @model_validator(mode="after")
    def _canonical_identity(self) -> "InlineJsonObservationBody":
        raw = canonical_json_bytes(self.value)
        _assert_body_identity(raw, self.sha256, self.byte_length)
        return self


class InlineTextObservationBody(BaseModel):
    """Exact UTF-8 text stored directly in a compact detail envelope."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["inline_text"]
    value: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0, le=INLINE_BODY_MAX_BYTES)

    @model_validator(mode="after")
    def _canonical_identity(self) -> "InlineTextObservationBody":
        raw = self.value.encode("utf-8")
        _assert_body_identity(raw, self.sha256, self.byte_length)
        return self


class ReferencedObservationBody(BaseModel):
    """Run-scoped immutable canonical body selected by SHA-256."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["body_ref"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(gt=INLINE_BODY_MAX_BYTES)
    codec: Literal["gzip"]


PersistedObservationBody = Annotated[
    Union[
        InlineJsonObservationBody,
        InlineTextObservationBody,
        ReferencedObservationBody,
    ],
    Field(discriminator="kind"),
]


class ObservationDetailEnvelope(BaseModel):
    """Closed persisted v4 detail occurrence; body truth is inline or run-scoped."""

    model_config = ConfigDict(extra="forbid")

    detail_id: str
    event_id: str
    run_id: str
    sequence: Optional[int] = Field(default=None, ge=1)
    invocation_id: Optional[str] = None
    kind: ObservationDetailKind
    content_type: str
    body: PersistedObservationBody
    artifact_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _closed_identity(self) -> "ObservationDetailEnvelope":
        for name in ("detail_id", "event_id", "run_id", "content_type"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"observation detail envelope {name} must be nonblank")
        assert_plain_identity(self.run_id, what="observation detail run_id")
        return self


class ObservationBundleMetaV4(BaseModel):
    """Closed latest-only metadata for one v4 observation segment."""

    model_config = ConfigDict(extra="forbid")

    bundle_schema_version: Literal[4]
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
    artifact_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_root: Literal["artifacts"]
    value_store_layout: Literal["run-scoped-sha256-gzip-v1"]
    inline_body_max_bytes: Literal[4096]
    artifact_count: int = Field(ge=0)
    artifacts_copied: int = Field(ge=0)
    trace_count: int = Field(ge=0)
    detail_count: int = Field(ge=0)
    usage_count: int = Field(ge=0)
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    detail_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    usage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    incomplete_streams: list[ObservationStreamName] = Field(max_length=3)
    stream_diagnostic: Optional[str] = Field(max_length=500)
    usage_totals_scope: Literal["run_cumulative_at_finalize"]
    total_tokens: int = Field(ge=0)
    metered_usd: Optional[float] = Field(default=None, allow_inf_nan=False)
    notional_usd: Optional[float] = Field(default=None, allow_inf_nan=False)
    segment_id: str
    segment_index: int = Field(ge=0)
    segment_kind: Literal["initial", "resume", "wait_terminal"]
    attempt: Optional[int] = Field(default=None, ge=1)
    correlation_id: Optional[str] = None
    provider_evidence: ProviderEvidenceIntegrity

    @model_validator(mode="after")
    def _coherent_segment_identity(self) -> "ObservationBundleMetaV4":
        problems = _meta_identity_problems(self)
        problems.extend(_timestamp_problems(self.timestamp))
        problems.extend(_segment_shape_problems(self))
        if len(set(self.incomplete_streams)) != len(self.incomplete_streams):
            problems.append("incomplete_streams must not contain duplicates")
        if self.incomplete_streams:
            if self.status != "abandoned":
                problems.append("only an abandoned bundle may carry incomplete record streams")
            if not str(self.stream_diagnostic or "").strip():
                problems.append("incomplete record streams require a bounded diagnostic")
        elif self.stream_diagnostic is not None:
            problems.append("complete record streams must not carry a diagnostic")
        if self.status == "completed" and self.provider_evidence.integrity != "complete":
            problems.append("completed bundle requires complete provider evidence")
        if problems:
            raise ValueError("invalid observation-bundle meta: " + "; ".join(problems))
        return self


def _meta_identity_problems(meta: ObservationBundleMetaV4) -> list[str]:
    problems = [
        f"{name} is blank"
        for name in ("run_id", "workflow_id", "segment_id", "timestamp", "definition_digest")
        if not str(getattr(meta, name) or "").strip()
    ]
    for name in ("run_id", "segment_id"):
        try:
            assert_plain_identity(str(getattr(meta, name)), what=name)
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def _timestamp_problems(value: str) -> list[str]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return [f"timestamp {value!r} is not ISO-8601"]
    if parsed.tzinfo is None:
        return [f"timestamp {value!r} is not timezone-aware"]
    return []


def _segment_shape_problems(meta: ObservationBundleMetaV4) -> list[str]:
    if meta.segment_kind == "initial" and (meta.segment_index != 0 or meta.attempt is not None):
        return [
            "initial segment must have segment_index=0 and no attempt — got "
            f"index={meta.segment_index}, attempt={meta.attempt!r}"
        ]
    if meta.segment_kind in ("resume", "wait_terminal") and meta.segment_index < 1:
        return [f"{meta.segment_kind} segment needs segment_index >= 1"]
    return []


def load_bundle_meta_v4(bundle_dir: Path) -> ObservationBundleMetaV4:
    """Read one strict v4 segment meta without following symlinks or guessing formats."""

    root = _validated_bundle_root(bundle_dir)
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
            f"{BUNDLE_SCHEMA_VERSION}, got {version!r} — v4 has no v3 reader, importer, "
            "or compatibility path; use a fresh observation root"
        )
    return ObservationBundleMetaV4.model_validate(raw)


def _validated_bundle_root(bundle_dir: Path) -> Path:
    if Path(bundle_dir).is_symlink():
        raise ValueError(
            f"observation bundle directory {bundle_dir} is a symlink — symlinked children "
            "of the bundle root are rejected (history lives in real directories)"
        )
    return Path(bundle_dir).resolve()


def _assert_body_identity(raw: bytes, expected_sha256: str, expected_length: int) -> None:
    import hashlib

    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != expected_sha256 or len(raw) != expected_length:
        raise ValueError(
            "inline observation body failed canonical digest/length validation: "
            f"got sha256={actual_sha256!r}, bytes={len(raw)}"
        )


__all__ = [
    "ABANDON_MARKER_NAME",
    "ARTIFACT_DIR_NAME",
    "ARTIFACT_MANIFEST_NAME",
    "BUNDLE_SCHEMA_VERSION",
    "BundleStatus",
    "COMMIT_MARKER_NAME",
    "DEFAULT_ARTIFACT_MAX_BYTES",
    "ObservationBundleMetaV4",
    "ObservationDetailEnvelope",
    "ObservationDetailKind",
    "ObservationSegment",
    "ObservationStreamName",
    "PersistedObservationBody",
    "ProviderEvidenceIntegrity",
    "ReferencedObservationBody",
    "InlineJsonObservationBody",
    "InlineTextObservationBody",
    "INLINE_BODY_MAX_BYTES",
    "assert_plain_identity",
    "load_bundle_meta_v4",
    "resolve_child_dir",
]
