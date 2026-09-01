"""Bounded, integrity-checked delivery of one selected observation detail."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain
from typing import Protocol

from ai_workflow_engine import ObservationReader


PREVIEW_BYTES = 64 * 1024


class DetailReaderSource(Protocol):
    def reader_for_segment(self, run_id: str, segment_id: str) -> ObservationReader:
        """Return the strict engine reader for one exact segment."""


@dataclass(frozen=True)
class DetailDelivery:
    chunks: Iterator[bytes]
    content_length: int
    body_length: int
    sha256: str
    content_type: str
    content_disposition: str
    truncated: bool


def prepare_detail_delivery(
    source: DetailReaderSource,
    *,
    run_id: str,
    segment_id: str,
    detail_id: str,
    invocation_id: str | None,
    mode: str,
) -> DetailDelivery:
    """Validate one persisted occurrence before any HTTP success is published."""

    if mode not in {"preview", "download"}:
        raise ValueError("observation detail mode must be 'preview' or 'download'")
    reader_for_segment = getattr(source, "reader_for_segment", None)
    if not callable(reader_for_segment):
        raise ValueError("viewer source does not support bounded detail delivery")
    reader = reader_for_segment(run_id, segment_id)
    try:
        detail = reader.get_detail(detail_id, invocation_id=invocation_id)
    except KeyError as exc:
        raise FileNotFoundError(str(exc)) from exc

    if mode == "preview":
        body = reader.preview_bytes(detail, max_bytes=PREVIEW_BYTES)
        return DetailDelivery(
            chunks=iter((body,)),
            content_length=len(body),
            body_length=detail.body.byte_length,
            sha256=detail.body.sha256,
            content_type="text/plain; charset=utf-8",
            content_disposition="inline",
            truncated=detail.body.byte_length > len(body),
        )

    stream = reader.iter_body_bytes(detail)
    try:
        first = next(stream)
    except StopIteration:
        first = b""
    return DetailDelivery(
        chunks=chain((first,), stream),
        content_length=detail.body.byte_length,
        body_length=detail.body.byte_length,
        sha256=detail.body.sha256,
        content_type="application/octet-stream",
        content_disposition=f'attachment; filename="detail-{detail.body.sha256[:12]}.bin"',
        truncated=False,
    )


__all__ = ["DetailDelivery", "PREVIEW_BYTES", "prepare_detail_delivery"]
