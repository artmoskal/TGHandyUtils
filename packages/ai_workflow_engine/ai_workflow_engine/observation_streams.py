"""Shared JSONL iteration mechanics for observation readers."""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
from pathlib import Path
from typing import Any


_CHUNK_BYTES = 64 * 1024


def iter_jsonl_models(path: Path, model: type[Any]) -> Iterator[Any]:
    if not path.is_file():
        raise FileNotFoundError(f"observation segment is missing {path.name}")
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                yield model.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"invalid observation record in {path.name} at line {line_number}: {exc}"
                ) from exc


def iter_jsonl_prefix(
    path: Path,
    model: type[Any],
    count: int,
    *,
    expected_sha256: str,
) -> Iterator[Any]:
    if not path.is_file():
        if count == 0:
            return
        raise FileNotFoundError(f"missing abandoned observation stream {path.name}")
    digest_builder = hashlib.sha256()
    with path.open("rb") as raw:
        while chunk := raw.read(_CHUNK_BYTES):
            digest_builder.update(chunk)
    if digest_builder.hexdigest() != expected_sha256:
        raise ValueError(
            f"abandoned observation stream {path.name} digest mismatch before prefix read"
        )
    if count == 0:
        return
    yielded = 0
    with path.open("rb") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            if yielded == count:
                break
            try:
                record = model.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"invalid abandoned {path.name} prefix at line {line_number}: {exc}"
                ) from exc
            yielded += 1
            yield record
    if yielded != count:
        raise ValueError(
            f"{path.name} contains {yielded} valid records, expected prefix of {count}"
        )


__all__ = ["iter_jsonl_models", "iter_jsonl_prefix"]
