"""External process capability helpers."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from ai_workflow_engine.execution_window import (
    invocation_soft_remaining_s,
    resolve_invocation_bound,
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
    ) -> None:
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
        bound = resolve_invocation_bound(
            engine_soft_s=invocation_soft_remaining_s(),
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
        )
        stdout_task = asyncio.create_task(self._read_stream(process.stdout))
        stderr_task = asyncio.create_task(self._read_stream(process.stderr))
        bound_meta = {
            "timeout_s": effective_timeout_s,
            "requested_timeout_s": request.timeout_s,
            "kill_grace_s": kill_grace_s,
            "bound_source": bound.source,
        }
        try:
            if request.stdin_data is not None:
                await self._write_stdin(process, request.stdin_data)
            try:
                await asyncio.wait_for(process.wait(), timeout=effective_timeout_s)
            except asyncio.TimeoutError:
                killed_after_grace = await self._terminate_with_grace(process, kill_grace_s)
                stdout = await stdout_task
                stderr = await stderr_task
                result_text = self._read_result_file(request)
                return CapabilityResult(
                    status="partial",
                    error=f"external process timed out after {effective_timeout_s}s",
                    output={
                        "returncode": process.returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                        "result": result_text if result_text is not None else stdout,
                    },
                    metadata={
                        **request.metadata,
                        **bound_meta,
                        "killed_after_grace": killed_after_grace,
                    },
                )

            stdout = await stdout_task
            stderr = await stderr_task
        except asyncio.CancelledError:
            # The outer boundary (engine hard deadline / caller cancel) fired mid-flight.
            # The child must not outlive the engine's claim that it stopped: kill NOW (the
            # polite grace belongs to the timeout path — cancellation means the deadline
            # already passed), reap, settle the reader tasks, then propagate the cancel.
            await self._reap_on_cancel(process, stdout_task, stderr_task)
            raise
        finally:
            # Belt for ANY exit path that left the child alive (unexpected exception): a
            # subprocess must never outlive its capability invocation.
            await self._ensure_child_reaped(process)

        result_text = self._read_result_file(request)
        status = "accepted" if process.returncode == 0 else "failed"
        return CapabilityResult(
            status=status,
            error=None if process.returncode == 0 else f"external process exited {process.returncode}",
            output={
                "returncode": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "result": result_text if result_text is not None else stdout,
            },
            metadata={
                **request.metadata,
                **bound_meta,
            },
        )

    @staticmethod
    async def _ensure_child_reaped(process: asyncio.subprocess.Process) -> None:
        """Immediate kill + reap for any exit path that left the child alive: a subprocess
        must never outlive its capability invocation (no survivor, no zombie)."""

        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.wait()

    @classmethod
    async def _reap_on_cancel(
        cls,
        process: asyncio.subprocess.Process,
        stdout_task: "asyncio.Task[str]",
        stderr_task: "asyncio.Task[str]",
    ) -> None:
        """Bounded, immediate cleanup on cancellation — must fit inside the runtime's
        cooperative cancel grace: kill (no terminate grace), reap, settle readers."""

        await cls._ensure_child_reaped(process)
        for task in (stdout_task, stderr_task):
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

    @staticmethod
    async def _terminate_with_grace(process: asyncio.subprocess.Process, kill_grace_s: float) -> bool:
        killed_after_grace = False
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                await process.wait()
                return killed_after_grace
        try:
            await asyncio.wait_for(process.wait(), timeout=max(0.0, kill_grace_s))
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
                killed_after_grace = True
            await process.wait()
        return killed_after_grace

    @staticmethod
    def _read_result_file(request: ExternalProcessRequest) -> str | None:
        if not request.result_file:
            return None
        result_path = Path(request.result_file)
        if not result_path.is_absolute() and request.cwd:
            result_path = Path(request.cwd) / result_path
        if not result_path.exists():
            return None
        return result_path.read_text(encoding="utf-8")

    @staticmethod
    async def _read_stream(stream: asyncio.StreamReader | None) -> str:
        if stream is None:
            return ""
        chunks: list[bytes] = []
        while True:
            # Bounded chunks, not readline(): a single line beyond asyncio's 64KiB stream
            # limit raises LimitOverrunError — and CLI workers legitimately emit huge
            # single-line JSON envelopes.
            chunk = await stream.read(8192)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")


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
