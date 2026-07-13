"""Bounded, race-resistant process I/O for the engine's external-process door (v0.10.1).

The execution-window work bounds a subprocess's TIME. This module bounds its RESULT-SETTLEMENT
so the same guarantees hold after the child is reaped: draining stdout/stderr can never exhaust
memory, and reading a declared result file can never block the event loop past the deadline
(FIFO/device), disclose an out-of-workspace target (symlink), or slurp an unbounded file.

Two owners, both dependency-light (stdlib + pydantic):

- ``BoundedStreamCollector`` drains an ``asyncio.StreamReader`` fully (no child pipe deadlock)
  while retaining only a bounded head+tail and counting every SOURCE byte, so retained storage
  is ``O(limit)`` regardless of total emitted output.
- ``read_bounded_result_file`` opens the declared result path with ``O_NOFOLLOW``/``O_NONBLOCK``/
  ``O_CLOEXEC``, validates the OPENED descriptor with ``fstat`` (regular file only — a FIFO/
  socket/device/directory is rejected before any read and a FIFO never blocks), and reads at
  most ``cap + 1`` bytes so an over-cap file is rejected without allocation. A symlink final
  component fails the open; its target is never disclosed.

Byte limits are an I/O-adapter policy, deliberately NOT part of ``RuntimeLimits`` (workflow
time/cost). Configure them at ``ExternalProcessCapability`` construction; a per-request value may
only NARROW them and can never select unlimited capture.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import stat
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ProcessIOLimits",
    "StreamCapture",
    "BoundedStreamCollector",
    "ResultCapture",
    "read_bounded_result_file",
    "capture_result_file",
]

_MIB = 1024 * 1024
_KIB = 1024


class ProcessIOLimits(BaseModel):
    """Strict positive-integer caps for one process-backed capability's I/O capture.

    Defaults are safe, not global assumptions: a product may construct a capability with
    stricter or larger finite values. Large binary/media output belongs in artifact files, not
    stdout or engine state — there is no "unlimited" setting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_stdout_bytes: int = Field(default=_MIB, gt=0, strict=True)
    max_stderr_bytes: int = Field(default=256 * _KIB, gt=0, strict=True)
    max_result_bytes: int = Field(default=4 * _MIB, gt=0, strict=True)

    def narrow(self, other: Optional["ProcessIOLimits"]) -> "ProcessIOLimits":
        """Return a policy no looser than either input — a per-request policy may only tighten
        the capability's construction-time caps, never enlarge them or select unlimited."""

        if other is None:
            return self
        return ProcessIOLimits(
            max_stdout_bytes=min(self.max_stdout_bytes, other.max_stdout_bytes),
            max_stderr_bytes=min(self.max_stderr_bytes, other.max_stderr_bytes),
            max_result_bytes=min(self.max_result_bytes, other.max_result_bytes),
        )


@dataclass(frozen=True)
class StreamCapture:
    """Bounded capture of one stream. ``total_bytes`` counts every SOURCE byte observed;
    ``retained_text`` is the decoded head+tail (with a truncation marker when dropped)."""

    text: str
    total_bytes: int
    retained_bytes: int
    limit_bytes: int
    truncated: bool

    def metadata(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "retained_bytes": self.retained_bytes,
            "limit_bytes": self.limit_bytes,
            "truncated": self.truncated,
        }


class _HeadTailBuffer:
    """Retain the first ``head`` and last ``tail`` bytes of a stream while counting all bytes.

    Memory is ``O(limit)`` no matter how much is fed. Startup context (head) and terminal
    diagnostics (tail) both survive; the dropped middle is annotated on finalize.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._head_cap = limit // 2
        self._tail_cap = limit - self._head_cap
        self._head = bytearray()
        self._tail = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if len(self._head) < self._head_cap:
            take = self._head_cap - len(self._head)
            self._head += chunk[:take]
            chunk = chunk[take:]
        if chunk:
            self._tail += chunk
            if len(self._tail) > self._tail_cap:
                del self._tail[: len(self._tail) - self._tail_cap]

    def finalize(self) -> StreamCapture:
        retained_source = len(self._head) + len(self._tail)
        truncated = self.total > retained_source
        if truncated:
            dropped = self.total - retained_source
            marker = f"\n...[{dropped} bytes truncated]...\n".encode("utf-8")
            retained_bytes = bytes(self._head) + marker + bytes(self._tail)
        else:
            retained_bytes = bytes(self._head) + bytes(self._tail)
        return StreamCapture(
            text=retained_bytes.decode("utf-8", errors="replace"),
            total_bytes=self.total,
            retained_bytes=retained_source,
            limit_bytes=self._limit,
            truncated=truncated,
        )


class BoundedStreamCollector:
    """Drain an ``asyncio.StreamReader`` to EOF, retaining only a bounded head+tail."""

    _CHUNK = 64 * _KIB

    def __init__(self, limit_bytes: int) -> None:
        self._buffer = _HeadTailBuffer(limit_bytes)

    async def collect(self, stream: "asyncio.StreamReader | None") -> StreamCapture:
        if stream is None:
            return self._buffer.finalize()
        while True:
            # Bounded reads, not readline(): a single line beyond asyncio's 64KiB stream limit
            # raises LimitOverrunError — and CLI workers legitimately emit huge single-line JSON.
            chunk = await stream.read(self._CHUNK)
            if not chunk:
                break
            self._buffer.feed(chunk)
        return self._buffer.finalize()


ResultCaptureStatus = Literal["ok", "missing", "unsafe", "oversize", "error"]


@dataclass(frozen=True)
class ResultCapture:
    """Outcome of reading a declared result file through the safe boundary.

    ``ok`` — regular file within the cap; ``text`` is its decoded content.
    ``missing`` — the file does not exist (ordinary CLI stdout-fallback contract).
    ``unsafe`` — a non-regular path (fifo/socket/device/directory) or a symlink; NOT read.
    ``oversize`` — a regular file over the cap; rejected without allocating it.
    ``error`` — an unexpected OS error while opening/reading.
    A rejected capture NEVER carries the target's content (symlink non-disclosure).
    """

    status: ResultCaptureStatus
    text: Optional[str] = None
    total_bytes: Optional[int] = None
    limit_bytes: Optional[int] = None
    kind: Optional[str] = None  # "fifo"/"socket"/"char_device"/"block_device"/"directory"/"symlink"
    reason: Optional[str] = None

    @property
    def is_present_and_safe(self) -> bool:
        return self.status == "ok"

    def metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "kind": self.kind,
            "total_bytes": self.total_bytes,
            "limit_bytes": self.limit_bytes,
            "reason": self.reason,
        }


def _kind_of(mode: int) -> str:
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "char_device"
    if stat.S_ISBLK(mode):
        return "block_device"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "unknown"


def read_bounded_result_file(
    result_file: Optional[str],
    cwd: Optional[str],
    max_bytes: int,
) -> ResultCapture:
    """Safely read a declared result file. Descriptor-based: open with O_NOFOLLOW/O_NONBLOCK/
    O_CLOEXEC, validate the fd with fstat (regular file only), read at most ``max_bytes + 1``.

    NEVER blocks on a FIFO (O_NONBLOCK opens it immediately; fstat then rejects it) and NEVER
    follows a symlink (O_NOFOLLOW fails the open; where absent, an lstat+fstat identity check
    is used instead). Synchronous by design — call it off the event loop for a legit large
    regular file via :func:`capture_result_file`."""

    if not result_file:
        return ResultCapture(status="missing")

    path = Path(result_file)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path

    # Classify WITHOUT opening first: an lstat never follows a symlink and never blocks, and a
    # socket/FIFO/device cannot always even be opened (a socket open raises ENXIO). Only a
    # regular file is then opened — with O_NOFOLLOW/O_NONBLOCK and an fstat identity check that
    # closes the lstat→open swap race (to a symlink, special file, or a different regular file).
    try:
        lst = os.lstat(path)
    except FileNotFoundError:
        return ResultCapture(status="missing")
    except OSError as exc:
        return ResultCapture(status="error", reason=f"lstat failed: {getattr(exc, 'errno', None)}")
    if stat.S_ISLNK(lst.st_mode):
        return ResultCapture(status="unsafe", kind="symlink", reason="result path is a symlink")
    if not stat.S_ISREG(lst.st_mode):
        kind = _kind_of(lst.st_mode)
        return ResultCapture(
            status="unsafe", kind=kind, reason=f"result path is not a regular file ({kind})"
        )
    lstat_ident = (lst.st_dev, lst.st_ino)

    o_nofollow = getattr(os, "O_NOFOLLOW", 0)
    o_nonblock = getattr(os, "O_NONBLOCK", 0)
    o_cloexec = getattr(os, "O_CLOEXEC", 0)
    flags = os.O_RDONLY | o_nofollow | o_nonblock | o_cloexec

    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return ResultCapture(status="missing")
    except OSError as exc:
        # ELOOP from O_NOFOLLOW = swapped to a symlink after our lstat; never disclose its target.
        errno = getattr(exc, "errno", None)
        if o_nofollow and errno == getattr(__import__("errno"), "ELOOP", None):
            return ResultCapture(status="unsafe", kind="symlink", reason="result path is a symlink")
        return ResultCapture(status="error", reason=f"open failed: {errno}")

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            # swapped to a special file between lstat and open
            kind = _kind_of(st.st_mode)
            return ResultCapture(
                status="unsafe", kind=kind, reason=f"result path is not a regular file ({kind})"
            )
        if (st.st_dev, st.st_ino) != lstat_ident:
            # replaced by a different regular file between lstat and open — refuse the swap
            return ResultCapture(
                status="unsafe", kind="race", reason="result path changed during open"
            )
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(64 * _KIB, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            return ResultCapture(
                status="oversize",
                total_bytes=int(st.st_size) if st.st_size else len(data),
                limit_bytes=max_bytes,
                reason=f"result file exceeds {max_bytes} bytes",
            )
        return ResultCapture(
            status="ok",
            text=data.decode("utf-8", errors="replace"),
            total_bytes=len(data),
            limit_bytes=max_bytes,
        )
    except OSError as exc:
        return ResultCapture(status="error", reason=f"read failed: {getattr(exc, 'errno', None)}")
    finally:
        os.close(fd)


async def capture_result_file(
    result_file: Optional[str],
    cwd: Optional[str],
    max_bytes: int,
) -> ResultCapture:
    """Off-loop wrapper: even a legitimate large regular-file read never blocks the loop."""

    return await asyncio.to_thread(read_bounded_result_file, result_file, cwd, max_bytes)
