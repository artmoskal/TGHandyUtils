"""Canonical observation bodies and run-scoped immutable value storage."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, BinaryIO, Literal

from ai_workflow_engine.observation_contract import canonical_json_bytes, canonical_json_chunks


INLINE_BODY_MAX_BYTES = 4_096
OBSERVATION_BODY_CODEC: Literal["gzip"] = "gzip"
VALUE_DIR_NAME = "values"
_CHUNK_BYTES = 64 * 1024


def canonical_text_chunks(value: str) -> Iterator[bytes]:
    if not isinstance(value, str):
        raise TypeError("observation text body must be a string")
    encoded = value.encode("utf-8")
    for offset in range(0, len(encoded), _CHUNK_BYTES):
        yield encoded[offset : offset + _CHUNK_BYTES]


def canonical_body_chunks(body: Any) -> Iterator[bytes]:
    kind = getattr(body, "kind", None)
    if kind == "json":
        yield from canonical_json_chunks(body.value)
        return
    if kind == "text":
        yield from canonical_text_chunks(body.value)
        return
    raise TypeError(f"unsupported logical observation body kind: {kind!r}")


def canonical_body_bytes(body: Any) -> bytes:
    return b"".join(canonical_body_chunks(body))


def body_sha256(body: Any) -> str:
    digest = hashlib.sha256()
    for chunk in canonical_body_chunks(body):
        digest.update(chunk)
    return digest.hexdigest()


def render_observation_body_text(body: Any) -> str:
    """Derive exact display text from an inline logical or persisted body."""

    kind = getattr(body, "kind", None)
    if kind in {"json", "inline_json"}:
        return json.dumps(
            body.value,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
    if kind in {"text", "inline_text"}:
        return body.value
    raise TypeError(f"observation body {kind!r} has no inline display text")


class RunValueStore:
    """Immutable SHA-256 objects owned by one logical observation run."""

    def __init__(self, run_root: str | Path, *, create: bool = True) -> None:
        self.run_root = Path(run_root)
        self.root = self.run_root / VALUE_DIR_NAME
        if self.root.is_symlink():
            raise ValueError(f"observation value root {self.root} must not be a symlink")
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.resolve().is_relative_to(self.run_root.resolve()):
            raise ValueError(f"observation value root {self.root} escapes run root {self.run_root}")

    def publish(
        self,
        chunks: Callable[[], Iterator[bytes]],
        *,
        expected_sha256: str | None = None,
        expected_length: int | None = None,
    ) -> tuple[str, int]:
        """Publish deterministic chunks once and return uncompressed identity and length."""

        if expected_sha256 is None or expected_length is None:
            sha256, byte_length = _measure_chunks(chunks())
        else:
            sha256, byte_length = expected_sha256, expected_length
            _validate_sha256(sha256)
            if byte_length < 0:
                raise ValueError("observation body byte length must be nonnegative")
        target = self.object_path(sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink(target.parent, what="observation value shard")
        if target.exists():
            self._validate_object(target, expected_sha256=sha256, expected_length=byte_length)
            return sha256, byte_length

        fd, temp_name = tempfile.mkstemp(prefix=".value-", suffix=".tmp", dir=target.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as encoded:
                    actual = hashlib.sha256()
                    actual_length = 0
                    for chunk in chunks():
                        actual.update(chunk)
                        actual_length += len(chunk)
                        encoded.write(chunk)
                raw.flush()
                os.fsync(raw.fileno())
            if actual.hexdigest() != sha256 or actual_length != byte_length:
                raise ValueError("observation body changed between identity and publication passes")
            try:
                os.link(temp_path, target)
            except FileExistsError:
                self._validate_object(
                    target,
                    expected_sha256=sha256,
                    expected_length=byte_length,
                )
            else:
                _fsync_directory(target.parent)
        finally:
            temp_path.unlink(missing_ok=True)
        return sha256, byte_length

    def object_path(self, sha256: str) -> Path:
        _validate_sha256(sha256)
        shard = self.root / sha256[:2]
        target = shard / f"{sha256[2:]}.body.gz"
        if shard.is_symlink() or target.is_symlink():
            raise ValueError("symlinked observation value paths are rejected")
        if target.exists() and not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("observation value path escapes its logical run")
        return target

    def validate(self, sha256: str, byte_length: int) -> Path:
        target = self.object_path(sha256)
        if not target.is_file():
            raise FileNotFoundError(f"observation body {sha256!r} is missing")
        self._validate_object(
            target,
            expected_sha256=sha256,
            expected_length=byte_length,
        )
        return target

    def iter_validated_bytes(
        self,
        sha256: str,
        byte_length: int,
        *,
        chunk_bytes: int = _CHUNK_BYTES,
    ) -> Iterator[bytes]:
        """Validate a complete object, then emit it from the same open file."""

        if chunk_bytes < 1:
            raise ValueError("observation body chunk_bytes must be positive")
        target = self.object_path(sha256)
        if not target.is_file():
            raise FileNotFoundError(f"observation body {sha256!r} is missing")
        with target.open("rb") as raw:
            self._validate_open_object(
                raw,
                expected_sha256=sha256,
                expected_length=byte_length,
                chunk_bytes=chunk_bytes,
            )
            raw.seek(0)
            with gzip.GzipFile(fileobj=raw, mode="rb") as decoded:
                while chunk := decoded.read(chunk_bytes):
                    yield chunk

    @staticmethod
    def _validate_object(
        path: Path,
        *,
        expected_sha256: str,
        expected_length: int,
    ) -> None:
        with path.open("rb") as raw:
            RunValueStore._validate_open_object(
                raw,
                expected_sha256=expected_sha256,
                expected_length=expected_length,
                chunk_bytes=_CHUNK_BYTES,
            )

    @staticmethod
    def _validate_open_object(
        raw: BinaryIO,
        *,
        expected_sha256: str,
        expected_length: int,
        chunk_bytes: int,
    ) -> None:
        digest = hashlib.sha256()
        length = 0
        try:
            with gzip.GzipFile(fileobj=raw, mode="rb") as decoded:
                while chunk := decoded.read(chunk_bytes):
                    digest.update(chunk)
                    length += len(chunk)
        except (OSError, EOFError) as exc:
            raise ValueError(f"observation body {expected_sha256!r} is corrupt: {exc}") from exc
        if digest.hexdigest() != expected_sha256 or length != expected_length:
            raise ValueError(
                f"observation body {expected_sha256!r} failed digest/length validation: "
                f"got sha256={digest.hexdigest()!r}, bytes={length}"
            )


def persist_observation_body(store: RunValueStore, body: Any) -> Any:
    """Choose the one v4 inline/reference representation for a logical body."""

    from ai_workflow_engine.observation_contract import (
        InlineJsonObservationBody,
        InlineTextObservationBody,
        ReferencedObservationBody,
    )

    sha256, byte_length = _measure_chunks(canonical_body_chunks(body))
    if byte_length <= INLINE_BODY_MAX_BYTES:
        if body.kind == "json":
            return InlineJsonObservationBody(
                kind="inline_json",
                value=body.value,
                sha256=sha256,
                byte_length=byte_length,
            )
        return InlineTextObservationBody(
            kind="inline_text",
            value=body.value,
            sha256=sha256,
            byte_length=byte_length,
        )
    stored_sha256, stored_length = store.publish(
        lambda: canonical_body_chunks(body),
        expected_sha256=sha256,
        expected_length=byte_length,
    )
    return ReferencedObservationBody(
        kind="body_ref",
        sha256=stored_sha256,
        byte_length=stored_length,
        codec=OBSERVATION_BODY_CODEC,
    )


def _measure_chunks(chunks: Iterator[bytes]) -> tuple[str, int]:
    digest = hashlib.sha256()
    length = 0
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise TypeError("canonical observation chunks must be bytes")
        digest.update(chunk)
        length += len(chunk)
    return digest.hexdigest(), length


def _validate_sha256(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"observation body sha256 must be 64 lowercase hex characters: {value!r}")
    return value


def _reject_symlink(path: Path, *, what: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{what} {path} must not be a symlink")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = [
    "INLINE_BODY_MAX_BYTES",
    "OBSERVATION_BODY_CODEC",
    "RunValueStore",
    "VALUE_DIR_NAME",
    "body_sha256",
    "canonical_body_bytes",
    "canonical_body_chunks",
    "canonical_json_bytes",
    "canonical_json_chunks",
    "canonical_text_chunks",
    "persist_observation_body",
    "render_observation_body_text",
]
