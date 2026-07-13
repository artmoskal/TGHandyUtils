"""Unit tests for the bounded process-I/O owner (v0.10.1).

Fast, fine-grained, tiny caps: the cap-edge algebra of stream capture and the safe result-file
boundary (FIFO/symlink/socket/device/directory rejection, oversize rejection, byte-equivalence),
plus the settlement outcome matrix. The engine.run-level attack reproducers live in
``test_v010_runtime_contracts.py``; these prove the owner's arithmetic directly.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time

import pytest

from ai_workflow_engine.engine.process_io import (
    BoundedStreamCollector,
    ProcessIOLimits,
    ResultCapture,
    StreamCapture,
    read_bounded_result_file,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


# ---------------------------------------------------------------------- limits policy


def test_process_io_limits_are_strict_positive_and_narrow_only():
    from pydantic import ValidationError

    default = ProcessIOLimits()
    assert default.max_stdout_bytes > 0 and default.max_result_bytes > 0

    with pytest.raises(ValidationError):
        ProcessIOLimits(max_stdout_bytes=0)
    with pytest.raises(ValidationError):
        ProcessIOLimits(max_result_bytes=-1)
    with pytest.raises(ValidationError):
        ProcessIOLimits(max_stderr_bytes=1.5)  # strict int
    with pytest.raises(ValidationError):
        ProcessIOLimits(unlimited=True)  # no such escape hatch (extra=forbid)

    # narrow only tightens; a looser per-request policy cannot enlarge the caps
    base = ProcessIOLimits(max_stdout_bytes=1000, max_stderr_bytes=1000, max_result_bytes=1000)
    looser = ProcessIOLimits(max_stdout_bytes=999999, max_stderr_bytes=999999, max_result_bytes=999999)
    tighter = ProcessIOLimits(max_stdout_bytes=10, max_stderr_bytes=10, max_result_bytes=10)
    assert base.narrow(looser).max_stdout_bytes == 1000
    assert base.narrow(tighter).max_stdout_bytes == 10
    assert base.narrow(None) is base


# ---------------------------------------------------------------------- stream capture


async def _capture(data: bytes, limit: int) -> StreamCapture:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return await BoundedStreamCollector(limit).collect(reader)


async def test_stream_under_and_at_cap_is_byte_equivalent_and_untruncated():
    cap = await _capture(b"hello world", 1000)
    assert cap.text == "hello world" and cap.total_bytes == 11 and cap.truncated is False

    exact = await _capture(b"X" * 100, 100)
    assert exact.total_bytes == 100 and exact.truncated is False and exact.text == "X" * 100


async def test_stream_over_cap_retains_head_and_tail_with_accurate_total():
    # 10_000 bytes into a 100-byte cap: head 50 + tail 50 survive, middle annotated, total exact
    data = bytes(range(256)) * 40  # 10_240 bytes, non-uniform so head != tail
    cap = await _capture(data, 100)
    assert cap.truncated is True
    assert cap.total_bytes == len(data)
    assert cap.retained_bytes == 100  # head_cap 50 + tail_cap 50
    assert "bytes truncated" in cap.text
    # head is the first 50 source bytes; tail is the last 50 source bytes
    head_expected = data[:50].decode("utf-8", errors="replace")
    tail_expected = data[-50:].decode("utf-8", errors="replace")
    assert cap.text.startswith(head_expected)
    assert cap.text.endswith(tail_expected)


async def test_stream_retained_memory_is_bounded_regardless_of_volume():
    # 4 MiB through a 64-byte cap: retained stays O(cap), not O(total)
    cap = await _capture(b"Z" * (4 * 1024 * 1024), 64)
    assert cap.total_bytes == 4 * 1024 * 1024
    assert cap.retained_bytes == 64
    assert len(cap.text) < 200  # head+tail+marker, nowhere near 4 MiB


async def test_stream_huge_single_line_is_captured_not_dropped():
    # a single 200 KiB line (no newline) — bounded reads, not readline(), still capture it
    cap = await _capture(b"J" * (200 * 1024), 512 * 1024)
    assert cap.total_bytes == 200 * 1024 and cap.truncated is False


# ---------------------------------------------------------------------- result file


def test_result_regular_file_at_cap_and_cap_plus_one(tmp_path):
    path = tmp_path / "r.txt"
    path.write_bytes(b"A" * 100)
    ok = read_bounded_result_file(str(path), None, max_bytes=100)
    assert ok.status == "ok" and ok.text == "A" * 100 and ok.total_bytes == 100

    over = tmp_path / "big.txt"
    over.write_bytes(b"A" * 101)
    rejected = read_bounded_result_file(str(over), None, max_bytes=100)
    assert rejected.status == "oversize" and rejected.text is None  # never allocated/disclosed


def test_result_missing_is_missing(tmp_path):
    cap = read_bounded_result_file(str(tmp_path / "nope.txt"), None, max_bytes=100)
    assert cap.status == "missing" and cap.text is None


def test_result_fifo_is_unsafe_without_blocking(tmp_path):
    fifo = tmp_path / "r.fifo"
    os.mkfifo(fifo)
    started = time.monotonic()
    cap = read_bounded_result_file(str(fifo), None, max_bytes=100)
    assert time.monotonic() - started < 2.0, "reading a writer-less FIFO must not block"
    assert cap.status == "unsafe" and cap.kind == "fifo" and cap.text is None


def test_result_symlink_is_unsafe_and_never_discloses_target(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("TOP-SECRET-a1b2c3", encoding="utf-8")
    link = tmp_path / "r.link"
    os.symlink(secret, link)
    cap = read_bounded_result_file(str(link), None, max_bytes=100)
    assert cap.status == "unsafe" and cap.kind == "symlink"
    assert cap.text is None and "TOP-SECRET" not in (cap.reason or "")


def test_result_directory_is_unsafe(tmp_path):
    cap = read_bounded_result_file(str(tmp_path), None, max_bytes=100)
    assert cap.status == "unsafe" and cap.kind == "directory"


def test_result_char_device_is_unsafe_without_reading(tmp_path):
    if not os.path.exists("/dev/zero"):
        pytest.skip("no /dev/zero on this platform")
    started = time.monotonic()
    cap = read_bounded_result_file("/dev/zero", None, max_bytes=100)  # infinite if read!
    assert time.monotonic() - started < 2.0, "a char device must be rejected before reading"
    assert cap.status == "unsafe" and cap.kind == "char_device" and cap.text is None


def test_result_unix_socket_is_unsafe(tmp_path):
    sock_path = tmp_path / "r.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(sock_path))
        cap = read_bounded_result_file(str(sock_path), None, max_bytes=100)
        assert cap.status == "unsafe" and cap.kind == "socket" and cap.text is None
    finally:
        server.close()


def test_result_relative_path_resolves_against_cwd(tmp_path):
    (tmp_path / "out.txt").write_text("relative-ok", encoding="utf-8")
    cap = read_bounded_result_file("out.txt", str(tmp_path), max_bytes=100)
    assert cap.status == "ok" and cap.text == "relative-ok"


# ---------------------------------------------------------------------- outcome matrix


@pytest.mark.parametrize(
    "returncode, requested, result_status, stdout_trunc, expected_status",
    [
        (1, True, "ok", False, "failed"),          # non-zero exit is always failed
        (0, True, "ok", True, "accepted"),         # valid result, truncated diagnostics -> accepted
        (0, True, "missing", False, "accepted"),   # no result, untruncated stdout fallback -> accepted
        (0, True, "missing", True, "partial"),     # no result, truncated stdout-as-result -> partial
        (0, True, "unsafe", False, "failed"),      # unsafe result -> failed (typed reason)
        (0, True, "oversize", False, "failed"),    # oversize result -> failed
        (0, False, "missing", True, "accepted"),   # no result requested -> stdout truncation is fine
    ],
)
def test_settlement_classifier_outcome_matrix(
    returncode, requested, result_status, stdout_trunc, expected_status
):
    from ai_workflow_engine.engine.external import ExternalProcessCapability

    rc = ResultCapture(
        status=result_status,
        text="RESULT" if result_status == "ok" else None,
        reason="bad" if result_status in ("unsafe", "oversize") else None,
    )
    stdout = StreamCapture(
        text="OUT", total_bytes=3, retained_bytes=3, limit_bytes=1000, truncated=stdout_trunc
    )
    status, error, result_text = ExternalProcessCapability._classify_settlement(
        returncode=returncode,
        result_requested=requested,
        result_capture=rc,
        stdout=stdout,
    )
    assert status == expected_status
    if expected_status == "failed" and result_status in ("unsafe", "oversize"):
        assert "result file rejected" in (error or "")
    # a rejected result never leaks its (never-captured) content into the output field
    if result_status in ("unsafe", "oversize"):
        assert result_text == "OUT"
