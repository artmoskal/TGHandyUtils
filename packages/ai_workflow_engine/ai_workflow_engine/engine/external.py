"""External process capability helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
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


class ExternalProcessCapability:
    """Run a subprocess as a bounded capability with partial-output salvage."""

    async def __call__(
        self,
        _context: CapabilityContext,
        request: ExternalProcessRequest | dict[str, Any],
    ) -> CapabilityResult:
        if isinstance(request, dict):
            request = ExternalProcessRequest(**request)
        if not request.command:
            return CapabilityResult(status="failed", error="external process command is empty")

        process = await asyncio.create_subprocess_exec(
            *request.command,
            cwd=request.cwd,
            env=request.env or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_task = asyncio.create_task(self._read_stream(process.stdout))
        stderr_task = asyncio.create_task(self._read_stream(process.stderr))
        try:
            await asyncio.wait_for(process.wait(), timeout=request.timeout_s)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            stdout = await stdout_task
            stderr = await stderr_task
            return CapabilityResult(
                status="partial",
                error=f"external process timed out after {request.timeout_s}s",
                output={
                    "returncode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                metadata={**request.metadata, "timeout_s": request.timeout_s},
            )

        stdout = await stdout_task
        stderr = await stderr_task
        status = "accepted" if process.returncode == 0 else "failed"
        return CapabilityResult(
            status=status,
            error=None if process.returncode == 0 else f"external process exited {process.returncode}",
            output={"returncode": process.returncode, "stdout": stdout, "stderr": stderr},
            metadata={**request.metadata, "timeout_s": request.timeout_s},
        )

    @staticmethod
    async def _read_stream(stream: asyncio.StreamReader | None) -> str:
        if stream is None:
            return ""
        chunks: list[bytes] = []
        while True:
            chunk = await stream.readline()
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
