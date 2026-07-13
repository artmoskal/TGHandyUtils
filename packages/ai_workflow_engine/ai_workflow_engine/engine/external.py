"""External process capability helpers."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import time
from typing import Any

from ai_workflow_engine.execution_window import (
    invocation_window_remaining_s,
    resolve_invocation_bound,
)
from ai_workflow_engine.engine.process_io import (
    BoundedStreamCollector,
    ProcessIOLimits,
    ResultCapture,
    StreamCapture,
    capture_result_file,
)
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    CapabilitySpec,
    ExternalWriteRequest,
    ExternalWriteResult,
)


@dataclass(frozen=True)
class ExternalProcessRequest:
    command: list[str]
    timeout_s: float = 30
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    stdin_data: str | None = None
    result_file: str | None = None
    kill_grace_s: float = 10.0
    # v0.10.1: OPTIONAL per-request I/O policy; it may only NARROW the capability's caps
    # (never enlarge or select unlimited).
    io_limits: ProcessIOLimits | None = None


class ExternalProcessCapability:
    """Run a subprocess as a bounded capability with partial-output salvage.

    This is the engine's REAL process-enforced door: it owns a killable child and always
    reaps it — at the work deadline (terminate→grace→kill→wait), on outer cancellation
    (the authoritative hard boundary fired: immediate kill + reap + reader settle), and on
    any unexpected error (a belt in ``finally``). The engine's ambient invocation window
    clamps ``request.timeout_s`` (an explicit request may narrow it, never widen it).
    ``spec`` declares ``timeout_enforcement="process"`` so direct registration is honest.
    """

    def __init__(
        self,
        *,
        name: str = "external_process",
        description: str | None = None,
        side_effects: list[str] | None = None,
        io_limits: ProcessIOLimits | None = None,
    ) -> None:
        # v0.10.1: construction-time I/O policy (finite defaults; zero-config safe). A
        # per-request policy may only tighten it.
        self.io_limits = io_limits or ProcessIOLimits()
        self.spec = CapabilitySpec(
            name=name,
            kind="external",
            description=description
            or "Bounded subprocess with partial-output salvage (process-enforced kill/reap).",
            # The historical registration contract (§4): an external step declares
            # external_call; registrants whose COMMAND also writes pass side_effects= with
            # workspace_write — the capability cannot know what an arbitrary command does.
            # NOTE: registration auto-adopts this spec (handler.spec), so overrides belong
            # HERE (constructor), not in register_capability kwargs.
            side_effects=side_effects if side_effects is not None else ["external_call"],
            timeout_s=None,
            timeout_enforcement="process",
        )

    async def __call__(
        self,
        _context: CapabilityContext,
        request: ExternalProcessRequest | dict[str, Any],
    ) -> CapabilityResult:
        if isinstance(request, dict):
            request = ExternalProcessRequest(**request)
        if not request.command:
            return CapabilityResult(status="failed", error="external process command is empty")

        # 5R: the engine's ambient invocation window bounds the child — the request timeout
        # may only NARROW it. Outside any engine run the explicit request timeout stands.
        ambient = invocation_window_remaining_s()
        context_window = getattr(_context, "execution_window", None)
        engine_soft_s = ambient.soft_s if ambient is not None else None
        engine_hard_s = ambient.hard_s if ambient is not None else None
        if ambient is None and context_window is not None and context_window.is_bounded:
            engine_soft_s = context_window.soft_timeout_s
            engine_hard_s = context_window.hard_timeout_s
        bound = resolve_invocation_bound(
            engine_soft_s=engine_soft_s,
            engine_hard_s=engine_hard_s,
            explicit_timeout_s=request.timeout_s,
            default_kill_grace_s=request.kill_grace_s,
            owner="external_process",
        )
        if bound.error is not None:
            return CapabilityResult(status="failed", error=bound.error)
        effective_timeout_s = bound.timeout_s
        kill_grace_s = bound.kill_grace_s

        process = await asyncio.create_subprocess_exec(
            *request.command,
            cwd=request.cwd,
            env=request.env or None,
            stdin=asyncio.subprocess.PIPE if request.stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # A process-backed capability owns the whole process tree it starts. On POSIX,
            # isolate it so timeout/cancellation can stop descendants as well as the CLI PID.
            start_new_session=os.name == "posix",
        )
        # v0.10.1: BOUNDED stream capture — drain to EOF (no child pipe deadlock) but retain
        # only a bounded head+tail per stream, counting every source byte.
        io_limits = self.io_limits.narrow(request.io_limits)
        stdout_task = asyncio.create_task(
            BoundedStreamCollector(io_limits.max_stdout_bytes).collect(process.stdout)
        )
        stderr_task = asyncio.create_task(
            BoundedStreamCollector(io_limits.max_stderr_bytes).collect(process.stderr)
        )
        driver_task = asyncio.create_task(
            self._drive_process(process, request.stdin_data)
        )
        bound_meta = {
            "timeout_s": effective_timeout_s,
            "requested_timeout_s": request.timeout_s,
            "kill_grace_s": kill_grace_s,
            "bound_source": bound.source,
            "process_execution_bound": bound.metadata(),
        }
        try:
            done, _pending = await asyncio.wait(
                {driver_task}, timeout=effective_timeout_s
            )
            if driver_task not in done:
                driver_task.cancel()
                killed_after_grace = await self._terminate_with_grace(process, kill_grace_s)
                await asyncio.gather(driver_task, return_exceptions=True)
                stdout = await stdout_task
                stderr = await stderr_task
                # Timeout is truthful PARTIAL regardless of result-file state; an unsafe/oversize
                # result is RECORDED but never upgrades the partial to a clean success.
                result_capture = await capture_result_file(
                    request.result_file, request.cwd, io_limits.max_result_bytes
                )
                result_text = result_capture.text if result_capture.is_present_and_safe else stdout.text
                return CapabilityResult(
                    status="partial",
                    error=f"external process timed out after {effective_timeout_s}s",
                    output={
                        "returncode": process.returncode,
                        "stdout": stdout.text,
                        "stderr": stderr.text,
                        "result": result_text,
                    },
                    metadata={
                        **request.metadata,
                        **bound_meta,
                        "killed_after_grace": killed_after_grace,
                        "process_io": self._io_metadata(stdout, stderr, result_capture),
                    },
                )
            # Re-raise a write/process-wait failure before reporting a successful process.
            driver_task.result()
            # A CLI that exits while leaving descendants behind has not completed its owned
            # process episode. Stop the residual group before waiting for pipe EOF; otherwise a
            # grandchild holding stdout/stderr open can hang this supposedly bounded door.
            await self._ensure_process_tree_reaped(process)
            stdout = await stdout_task
            stderr = await stderr_task
        except asyncio.CancelledError:
            # The outer boundary (engine hard deadline / caller cancel) fired mid-flight.
            # The child must not outlive the engine's claim that it stopped: kill NOW (the
            # polite grace belongs to the timeout path — cancellation means the deadline
            # already passed), reap, settle the reader tasks, then propagate the cancel.
            driver_task.cancel()
            await self._reap_on_cancel(process, driver_task, stdout_task, stderr_task)
            raise
        finally:
            # Belt for ANY exit path that left the child alive (unexpected exception): a
            # subprocess tree must never outlive its capability invocation.
            await self._ensure_process_tree_reaped(process)
            if not driver_task.done():
                driver_task.cancel()
            await asyncio.gather(driver_task, return_exceptions=True)
            await self._settle_readers(stdout_task, stderr_task)

        # v0.10.1: SAFE result capture (descriptor-based; never blocks on a FIFO, never follows
        # a symlink, never slurps an over-cap file) + ONE settlement classifier.
        result_capture = await capture_result_file(
            request.result_file, request.cwd, io_limits.max_result_bytes
        )
        status, error, result_text = self._classify_settlement(
            returncode=process.returncode,
            result_requested=bool(request.result_file),
            result_capture=result_capture,
            stdout=stdout,
        )
        return CapabilityResult(
            status=status,
            error=error,
            output={
                "returncode": process.returncode,
                "stdout": stdout.text,
                "stderr": stderr.text,
                "result": result_text,
            },
            metadata={
                **request.metadata,
                **bound_meta,
                "process_io": self._io_metadata(stdout, stderr, result_capture),
            },
        )

    @staticmethod
    def _io_metadata(
        stdout: StreamCapture, stderr: StreamCapture, result: ResultCapture
    ) -> dict[str, Any]:
        """One structured owner for capture truth: byte totals, truncation, and result-file
        settlement state — persisted to the bundle; summarized (not dumped) by the viewer."""

        return {
            "stdout": stdout.metadata(),
            "stderr": stderr.metadata(),
            "result_file": result.metadata(),
        }

    @staticmethod
    def _classify_settlement(
        *,
        returncode: int | None,
        result_requested: bool,
        result_capture: ResultCapture,
        stdout: StreamCapture,
    ) -> tuple[str, str | None, str]:
        """Map (exit, result request, safe result capture, stdout truncation) → one outcome.

        Non-zero exit → failed. Exit-zero with a valid complete result → accepted (even if
        diagnostics were truncated). Exit-zero, result requested but missing → the ordinary
        stdout fallback, downgraded to partial when that stdout-as-result is itself truncated
        (never an accepted INCOMPLETE result). An unsafe/oversize result → failed with a typed
        reason, and its content is never disclosed."""

        if returncode != 0:
            fallback = result_capture.text if result_capture.is_present_and_safe else stdout.text
            return "failed", f"external process exited {returncode}", fallback
        if result_requested:
            if result_capture.status == "ok":
                return "accepted", None, result_capture.text or ""
            if result_capture.status == "missing":
                if stdout.truncated:
                    return (
                        "partial",
                        "external process produced no result file and its stdout-as-result was "
                        "truncated (incomplete result)",
                        stdout.text,
                    )
                return "accepted", None, stdout.text
            reason = result_capture.reason or f"unsafe result file ({result_capture.status})"
            return "failed", f"external process result file rejected: {reason}", stdout.text
        return "accepted", None, stdout.text

    @classmethod
    async def _drive_process(
        cls,
        process: asyncio.subprocess.Process,
        stdin_data: str | None,
    ) -> None:
        """Bound stdin delivery and process execution as one unit of work."""

        if stdin_data is not None:
            await cls._write_stdin(process, stdin_data)
        await process.wait()

    @classmethod
    async def _ensure_process_tree_reaped(
        cls, process: asyncio.subprocess.Process
    ) -> None:
        """Kill the owned process group (when available) and reap the direct child."""

        if cls._process_tree_alive(process):
            cls._signal_process_tree(process, signal.SIGKILL)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        with contextlib.suppress(Exception):
            await process.wait()

    @classmethod
    async def _reap_on_cancel(
        cls,
        process: asyncio.subprocess.Process,
        driver_task: "asyncio.Task[None]",
        stdout_task: "asyncio.Task[str]",
        stderr_task: "asyncio.Task[str]",
    ) -> None:
        """Bounded, immediate cleanup on cancellation — must fit inside the runtime's
        cooperative cancel grace: kill (no terminate grace), reap, settle readers."""

        await cls._ensure_process_tree_reaped(process)
        if not driver_task.done():
            driver_task.cancel()
        await asyncio.gather(driver_task, return_exceptions=True)
        await cls._settle_readers(stdout_task, stderr_task)

    @staticmethod
    async def _settle_readers(
        stdout_task: "asyncio.Task[str]",
        stderr_task: "asyncio.Task[str]",
    ) -> None:
        """Cancel and join stream readers that were not consumed by the normal result path."""

        for task in (stdout_task, stderr_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    @staticmethod
    async def _write_stdin(process: asyncio.subprocess.Process, data: str) -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.write(data.encode("utf-8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return
        process.stdin.close()
        await process.stdin.wait_closed()

    @classmethod
    async def _terminate_with_grace(
        cls, process: asyncio.subprocess.Process, kill_grace_s: float
    ) -> bool:
        killed_after_grace = False
        if cls._process_tree_alive(process):
            cls._signal_process_tree(process, signal.SIGTERM)
        deadline = time.monotonic() + max(0.0, kill_grace_s)
        while cls._process_tree_alive(process) and time.monotonic() < deadline:
            await asyncio.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        if cls._process_tree_alive(process):
            cls._signal_process_tree(process, signal.SIGKILL)
            killed_after_grace = True
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
        return killed_after_grace

    @staticmethod
    def _process_tree_alive(process: asyncio.subprocess.Process) -> bool:
        if os.name != "posix":
            return process.returncode is None
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _signal_process_tree(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            elif sig == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            return


class InMemoryExternalWriteSink:
    """Reference sink for idempotent external/domain writes in tests and pilots."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], ExternalWriteResult] = {}

    def write(self, request: ExternalWriteRequest) -> ExternalWriteResult:
        key = (request.target, request.operation, request.idempotency_key)
        if key in self.records:
            existing = self.records[key]
            return ExternalWriteResult(
                status="duplicate",
                target=existing.target,
                operation=existing.operation,
                idempotency_key=existing.idempotency_key,
                external_id=existing.external_id,
                metadata={**existing.metadata, "duplicate_of": existing.external_id},
            )
        result = ExternalWriteResult(
            status="created",
            target=request.target,
            operation=request.operation,
            idempotency_key=request.idempotency_key,
            external_id=f"{request.target}:{request.idempotency_key}",
            metadata={
                **request.metadata,
                "privacy_level": request.privacy_level,
                "evidence_count": len(request.evidence_refs),
            },
        )
        self.records[key] = result
        return result


class JsonlExternalWriteSink:
    """Append-only external-write sink for local handoff/audit runs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[tuple[str, str, str]] = set()

    def write(self, request: ExternalWriteRequest) -> ExternalWriteResult:
        key = (request.target, request.operation, request.idempotency_key)
        status = "duplicate" if key in self._seen else "created"
        self._seen.add(key)
        result = ExternalWriteResult(
            status=status,
            target=request.target,
            operation=request.operation,
            idempotency_key=request.idempotency_key,
            external_id=f"{request.target}:{request.idempotency_key}",
            metadata={
                **request.metadata,
                "privacy_level": request.privacy_level,
                "evidence_count": len(request.evidence_refs),
            },
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "request": request.model_dump(mode="json"),
                        "result": result.model_dump(mode="json"),
                    }
                )
            )
            fh.write("\n")
        return result


class ExternalAdapterCapability:
    """Write structured observations to a product-owned external system via an injected sink."""

    def __init__(self, sink: Any | None = None) -> None:
        self.sink = sink or InMemoryExternalWriteSink()

    def __call__(
        self,
        _context: CapabilityContext,
        request: ExternalWriteRequest | dict[str, Any],
    ) -> CapabilityResult:
        if isinstance(request, dict):
            request = ExternalWriteRequest.model_validate(request)
        if request.privacy_level == "secret":
            return CapabilityResult(
                status="rejected",
                error="external write rejected: secret payloads require a product-owned secure adapter",
                metadata={"target": request.target, "idempotency_key": request.idempotency_key},
            )
        result = self.sink.write(request)
        return CapabilityResult(
            status="accepted" if result.status in {"created", "updated", "duplicate"} else "rejected",
            output=result,
            metadata={
                "target": request.target,
                "operation": request.operation,
                "privacy_level": request.privacy_level,
                "idempotency_key": request.idempotency_key,
                "external_status": result.status,
            },
        )
