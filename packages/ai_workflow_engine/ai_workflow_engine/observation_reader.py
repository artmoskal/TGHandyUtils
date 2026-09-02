"""Bytes-first reader for latest-only observation bundle v4 details."""

from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, get_args

from ai_workflow_engine.models import WorkflowTraceEvent, WorkflowUsageEvent
from ai_workflow_engine.observation_contract import (
    ObservationBundleMetaV4,
    ObservationDetailEnvelope,
    ObservationDetailKind,
    assert_plain_identity,
    load_bundle_meta_v4,
)
from ai_workflow_engine.observation_streams import iter_jsonl_models, iter_jsonl_prefix
from ai_workflow_engine.observation_values import (
    RunValueStore,
    canonical_json_chunks,
    canonical_text_chunks,
)
from ai_workflow_engine.workflow import WorkflowDefinition


_CHUNK_BYTES = 64 * 1024
_DETAIL_KINDS = frozenset(get_args(ObservationDetailKind))


@dataclass(frozen=True)
class HydratedObservationDetail:
    """One persisted detail envelope with its validated canonical body bytes."""

    envelope: ObservationDetailEnvelope
    body_bytes: bytes


class ObservationReader:
    """Read compact v4 envelopes and explicitly selected validated bodies."""

    def __init__(self, segment_path: str | Path) -> None:
        self.segment_path = Path(segment_path)
        self.meta = load_bundle_meta_v4(self.segment_path)
        self.run_root = _resolve_run_root(self.segment_path, self.meta)
        self.value_store = RunValueStore(self.run_root, create=False)
        self.detail_path = _contained_file(self.segment_path, self.meta.detail_path)

    def read_definition(self) -> WorkflowDefinition:
        path = _contained_file(self.segment_path, self.meta.definition_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"observation segment {self.meta.segment_id!r} is missing definition.json"
            )
        definition = WorkflowDefinition.model_validate_json(path.read_text(encoding="utf-8"))
        actual = definition.definition_digest()
        if actual != self.meta.definition_digest:
            raise ValueError(
                f"observation segment {self.meta.segment_id!r} claims definition digest "
                f"{self.meta.definition_digest!r}, but definition.json recomputes {actual!r}"
            )
        return definition

    def read_artifact_manifest(self) -> list[dict[str, Any]]:
        """Return the sealed artifact rows after validating persisted manifest identity."""

        path = _contained_file(self.segment_path, self.meta.artifact_manifest_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"observation segment {self.meta.segment_id!r} is missing artifacts.json"
            )
        self._validate_stream_digest(
            "artifact manifest", path, self.meta.artifact_manifest_sha256
        )
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"artifact manifest under segment {self.meta.segment_id!r} is unreadable"
            ) from exc
        if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
            raise ValueError(
                f"artifact manifest under segment {self.meta.segment_id!r} must be a row list"
            )
        copied = sum(bool(entry.get("copied")) for entry in entries)
        if len(entries) != self.meta.artifact_count or copied != self.meta.artifacts_copied:
            raise ValueError(
                f"artifact manifest counts disagree with meta: rows={len(entries)}, "
                f"copied={copied}, expected={self.meta.artifact_count}/"
                f"{self.meta.artifacts_copied}"
            )
        return entries

    def iter_trace_events(self) -> Iterator[WorkflowTraceEvent]:
        path = _contained_file(self.segment_path, self.meta.trace_path)
        self._validate_compact_stream("trace", path, self.meta.trace_sha256)
        yield from iter_jsonl_models(
            path,
            WorkflowTraceEvent,
        )

    def iter_usage_events(self) -> Iterator[WorkflowUsageEvent]:
        path = _contained_file(self.segment_path, self.meta.usage_path)
        self._validate_compact_stream("usage", path, self.meta.usage_sha256)
        yield from iter_jsonl_models(
            path,
            WorkflowUsageEvent,
        )

    def iter_trace_prefix(self) -> Iterator[WorkflowTraceEvent]:
        self._require_incomplete_prefix("trace")
        path = self._stream_path("trace")
        self._validate_prefix_stream("trace", path, self.meta.trace_sha256, self.meta.trace_count)
        yield from iter_jsonl_prefix(
            path, WorkflowTraceEvent, self.meta.trace_count,
            expected_sha256=self.meta.trace_sha256,
        )

    def iter_usage_prefix(self) -> Iterator[WorkflowUsageEvent]:
        self._require_incomplete_prefix("usage")
        path = self._stream_path("usage")
        self._validate_prefix_stream("usage", path, self.meta.usage_sha256, self.meta.usage_count)
        yield from iter_jsonl_prefix(
            path, WorkflowUsageEvent, self.meta.usage_count,
            expected_sha256=self.meta.usage_sha256,
        )

    def iter_detail_prefix(self) -> Iterator[ObservationDetailEnvelope]:
        self._require_incomplete_prefix("detail")
        count = self.meta.detail_count
        path = self.detail_path
        self._validate_prefix_stream("detail", path, self.meta.detail_sha256, count)
        if count == 0:
            return
        with path.open("rb") as source:
            yielded = 0
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                if yielded == count:
                    break
                try:
                    detail = ObservationDetailEnvelope.model_validate_json(line)
                except Exception as exc:
                    raise ValueError(f"invalid abandoned detail prefix at line {line_number}: {exc}") from exc
                self._validate_binding(detail)
                yielded += 1
                yield detail
        if yielded != count:
            raise ValueError(f"details.jsonl contains {yielded} valid records, expected prefix of {count}")

    def _stream_path(self, name: str) -> Path:
        return _contained_file(self.segment_path, getattr(self.meta, f"{name}_path"))

    def _require_incomplete_prefix(self, name: str) -> None:
        if self.meta.status != "abandoned" or name not in self.meta.incomplete_streams:
            raise ValueError(
                f"{name} prefix reads are allowed only for abandoned incomplete streams"
            )

    def iter_detail_envelopes(self) -> Iterator[ObservationDetailEnvelope]:
        self._validate_compact_stream("detail", self.detail_path, self.meta.detail_sha256)
        yield from self._iter_validated_detail_envelopes()

    def iter_detail_bodies(
        self,
        *,
        kinds: Collection[ObservationDetailKind],
        max_body_bytes: int,
    ) -> Iterator[HydratedObservationDetail]:
        """Hydrate selected kinds in persisted order after one detail-stream validation.

        ``kinds`` is a declarative envelope filter, not a body callback. Non-selected bodies
        are never opened. Each selected body must fit ``max_body_bytes`` and is fully validated
        before one result is yielded, so the iterator retains at most one hydrated body. A yielded
        item remains provisional until normal exhaustion validates the final detail record count.
        """

        selected_kinds = frozenset(kinds)
        if not selected_kinds:
            raise ValueError("observation detail kinds must not be empty")
        unsupported = selected_kinds - _DETAIL_KINDS
        if unsupported:
            raise ValueError(f"unsupported observation detail kinds: {sorted(unsupported)!r}")
        if max_body_bytes < 0:
            raise ValueError("observation body max_body_bytes must be nonnegative")
        self._validate_compact_stream("detail", self.detail_path, self.meta.detail_sha256)
        for detail in self._iter_validated_detail_envelopes():
            if detail.kind not in selected_kinds:
                continue
            yield HydratedObservationDetail(
                envelope=detail,
                body_bytes=self._read_persisted_body_bytes(
                    detail,
                    max_bytes=max_body_bytes,
                ),
            )

    def _iter_validated_detail_envelopes(self) -> Iterator[ObservationDetailEnvelope]:
        if not self.detail_path.is_file():
            raise FileNotFoundError(
                f"observation segment {self.meta.segment_id!r} is missing details.jsonl"
            )
        seen: set[str] = set()
        parsed_count = 0
        with self.detail_path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    detail = ObservationDetailEnvelope.model_validate_json(line)
                except Exception as exc:
                    raise ValueError(
                        f"invalid observation detail envelope at line {line_number}: {exc}"
                    ) from exc
                self._validate_binding(detail)
                if detail.detail_id in seen:
                    raise ValueError(
                        f"observation segment contains duplicate detail_id {detail.detail_id!r}"
                    )
                seen.add(detail.detail_id)
                parsed_count += 1
                yield detail
        if parsed_count != self.meta.detail_count:
            raise ValueError(
                f"observation segment {self.meta.segment_id!r} detail_count disagrees with "
                f"meta: expected {self.meta.detail_count}, read {parsed_count}"
            )

    def _stream_is_complete(self, name: str) -> bool:
        if name not in {"trace", "detail", "usage"}:
            raise ValueError(f"unknown observation stream {name!r}")
        return name not in self.meta.incomplete_streams

    def get_detail(
        self,
        detail_id: str,
        *,
        invocation_id: str | None = None,
    ) -> ObservationDetailEnvelope:
        assert_plain_identity(detail_id, what="observation detail_id")
        matches = [detail for detail in self.iter_detail_envelopes() if detail.detail_id == detail_id]
        if not matches:
            raise KeyError(
                f"observation detail {detail_id!r} does not exist in segment "
                f"{self.meta.segment_id!r}"
            )
        detail = matches[0]
        if invocation_id is not None and detail.invocation_id != invocation_id:
            raise ValueError(
                f"observation detail {detail_id!r} belongs to invocation "
                f"{detail.invocation_id!r}, not {invocation_id!r}"
            )
        return detail

    def details_for_invocation(self, invocation_id: str) -> list[ObservationDetailEnvelope]:
        if not str(invocation_id).strip():
            raise ValueError("observation invocation_id must be nonblank")
        details = [
            detail
            for detail in self.iter_detail_envelopes()
            if detail.invocation_id == invocation_id
        ]
        return sorted(
            details,
            key=lambda detail: (
                detail.sequence if detail.sequence is not None else 10**18,
                detail.detail_id,
            ),
        )

    def iter_body_bytes(
        self,
        detail: ObservationDetailEnvelope,
        *,
        chunk_bytes: int = _CHUNK_BYTES,
    ) -> Iterator[bytes]:
        """Validate the complete body first, then emit exact canonical bytes."""

        if chunk_bytes < 1:
            raise ValueError("observation body chunk_bytes must be positive")
        persisted = self._require_persisted_detail(detail)
        yield from self._iter_persisted_body_bytes(persisted, chunk_bytes=chunk_bytes)

    def _iter_persisted_body_bytes(
        self,
        detail: ObservationDetailEnvelope,
        *,
        chunk_bytes: int,
    ) -> Iterator[bytes]:
        body = detail.body
        if body.kind == "inline_json":
            yield from canonical_json_chunks(body.value)
            return
        if body.kind == "inline_text":
            yield from canonical_text_chunks(body.value)
            return
        yield from self.value_store.iter_validated_bytes(
            body.sha256,
            body.byte_length,
            chunk_bytes=chunk_bytes,
        )

    def read_body_bytes(
        self,
        detail: ObservationDetailEnvelope,
        *,
        max_bytes: int,
    ) -> bytes:
        persisted = self._require_persisted_detail(detail)
        return self._read_persisted_body_bytes(persisted, max_bytes=max_bytes)

    def _read_persisted_body_bytes(
        self,
        detail: ObservationDetailEnvelope,
        *,
        max_bytes: int,
    ) -> bytes:
        if detail.body.byte_length > max_bytes:
            raise ValueError(
                f"observation body {detail.detail_id!r} is {detail.body.byte_length} bytes, "
                f"above explicit read limit {max_bytes}"
            )
        return b"".join(
            self._iter_persisted_body_bytes(detail, chunk_bytes=_CHUNK_BYTES)
        )

    def preview_bytes(
        self,
        detail: ObservationDetailEnvelope,
        *,
        max_bytes: int,
    ) -> bytes:
        if max_bytes < 0:
            raise ValueError("observation preview max_bytes must be nonnegative")
        remaining = max_bytes
        output = bytearray()
        for chunk in self.iter_body_bytes(detail):
            if remaining:
                output.extend(chunk[:remaining])
                remaining -= min(remaining, len(chunk))
            if remaining == 0:
                break
        return bytes(output)

    def validate_record_counts(
        self,
        *,
        trace_count: int,
        detail_count: int,
        usage_count: int,
    ) -> None:
        actual = {
            "trace_count": trace_count,
            "detail_count": detail_count,
            "usage_count": usage_count,
        }
        for field, count in actual.items():
            expected = getattr(self.meta, field)
            if count != expected:
                raise ValueError(
                    f"observation segment {self.meta.segment_id!r} {field} disagrees with "
                    f"meta: expected {expected}, read {count}"
                )

    def parse_json(
        self,
        detail: ObservationDetailEnvelope,
        *,
        max_bytes: int,
    ) -> Any:
        if detail.content_type != "application/json":
            raise ValueError(
                f"observation detail {detail.detail_id!r} is {detail.content_type!r}, not JSON"
            )
        return json.loads(self.read_body_bytes(detail, max_bytes=max_bytes))

    def _validate_binding(self, detail: ObservationDetailEnvelope) -> None:
        if detail.run_id != self.meta.run_id:
            raise ValueError(
                f"observation detail {detail.detail_id!r} belongs to run {detail.run_id!r}, "
                f"not segment run {self.meta.run_id!r}"
            )

    def _validate_compact_stream(self, name: str, path: Path, expected_sha256: str) -> None:
        if not self._stream_is_complete(name):
            raise ValueError(
                f"observation {name} stream is incomplete after crash reconciliation: "
                f"{self.meta.stream_diagnostic}"
            )
        if not path.is_file():
            raise FileNotFoundError(
                f"observation segment {self.meta.segment_id!r} is missing {path.name}"
            )
        self._validate_stream_digest(name, path, expected_sha256)

    def _validate_prefix_stream(
        self, name: str, path: Path, expected_sha256: str, count: int
    ) -> None:
        if not path.is_file():
            if count == 0 and expected_sha256 == hashlib.sha256(b"").hexdigest():
                return
            raise FileNotFoundError(
                f"missing abandoned observation stream {path.name}"
            )
        self._validate_stream_digest(name, path, expected_sha256)

    def _validate_stream_digest(self, name: str, path: Path, expected_sha256: str) -> None:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                while chunk := source.read(_CHUNK_BYTES):
                    digest.update(chunk)
        except OSError as exc:
            raise ValueError(f"cannot validate observation stream {path.name}: {exc}") from exc
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise ValueError(
                f"observation {name} {path.name} failed SHA-256 validation: "
                f"expected {expected_sha256!r}, got {actual!r}"
            )

    def _require_persisted_detail(
        self,
        detail: ObservationDetailEnvelope,
    ) -> ObservationDetailEnvelope:
        self._validate_binding(detail)
        matches = [
            persisted
            for persisted in self.iter_detail_envelopes()
            if persisted.detail_id == detail.detail_id
        ]
        if not matches:
            raise ValueError(
                f"observation detail {detail.detail_id!r} is not persisted in segment "
                f"{self.meta.segment_id!r}"
            )
        if matches[0] != detail:
            raise ValueError(
                f"observation detail {detail.detail_id!r} disagrees with its persisted "
                f"envelope in segment {self.meta.segment_id!r}"
            )
        return matches[0]


def _resolve_run_root(segment_path: Path, meta: ObservationBundleMetaV4) -> Path:
    segment = segment_path.resolve()
    segments_root = segment.parent
    run_root = segments_root.parent
    if segment_path.is_symlink() or segments_root.is_symlink() or run_root.is_symlink():
        raise ValueError("symlinked v4 run/segments paths are rejected")
    if segments_root.name != "segments":
        raise ValueError(
            f"v4 segment {segment_path} must live under its logical run's segments directory"
        )
    if segment.name != meta.segment_id or run_root.name != meta.run_id:
        raise ValueError(
            f"v4 segment path identity disagrees with meta: path run/segment="
            f"{run_root.name!r}/{segment.name!r}, meta={meta.run_id!r}/{meta.segment_id!r}"
        )
    return run_root


def _contained_file(segment_path: Path, name: str) -> Path:
    root = segment_path.resolve()
    candidate = root / name
    if not candidate.resolve().is_relative_to(root):
        raise ValueError(f"observation segment file {name!r} escapes {segment_path}")
    return candidate


__all__ = ["HydratedObservationDetail", "ObservationReader"]
