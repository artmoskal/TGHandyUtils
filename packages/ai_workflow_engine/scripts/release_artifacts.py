"""Execute, assemble, and verify an engine release without importing runtime code.

The release contract is latest-only. This script deliberately has no compatibility commands for
the pre-v0.11.5 manifest shape: evidence is created by commands this process actually executes,
builds are bound to a clean annotated-tag checkout, and consumers verify one closed directory
before installing any wheel.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# This script ships INSIDE the closed release directory it validates, so importing its sibling
# must not write __pycache__ there and make the bundle reject its own inventory. Scoped to
# standalone execution and set before the sibling import: importing this module from tests or
# tooling must not mutate the caller's process-global bytecode policy.
if __name__ == "__main__":
    sys.dont_write_bytecode = True

from release_contract import (  # noqa: E402
    EXPECTED_PACKAGES,
    ReleaseError,
    VERIFIER_FILENAMES,
    assemble_manifest,
    copy_regular_file,
    hash_path,
    inspect_wheel,
    load_evidence_record,
    manifest_bundle_filenames,
    tagged_source,
    validate_gate_record,
    verify_bundle,
    write_sha256sums,
    _normalize_utc_now,
)

MAX_RETAINED_LOG_BYTES = 16 << 20


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise ReleaseError(f"refusing to overwrite evidence file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _strict_command(command: Sequence[str]) -> list[str]:
    if not command:
        raise ReleaseError("gate command must be a nonempty argv list")
    argv: list[str] = []
    for index, token in enumerate(command):
        if type(token) is not str or not token or "\x00" in token:
            raise ReleaseError(f"gate command token {index} must be a nonblank string")
        argv.append(token)
    return argv


def _strict_timeout(value: float | None) -> float | None:
    if value is None:
        return None
    if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
        raise ReleaseError("timeout_s must be finite and > 0")
    return float(value)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=2)
        return
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait()


def execute_gate(
    *,
    name: str,
    command: Sequence[str],
    cwd: Path,
    record_path: Path,
    log_path: Path,
    timeout_s: float | None,
    environment: Mapping[str, str] | None = None,
    build_fields: Mapping[str, Any] | None = None,
    child_umask: int | None = None,
    post_success: Callable[[], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Execute one command and create evidence from the observed process and retained log."""

    if name not in {"build", "test", "smoke"}:
        raise ReleaseError("gate name must be build, test, or smoke")
    argv = _strict_command(command)
    timeout_s = _strict_timeout(timeout_s)
    working_directory = cwd.resolve(strict=True)
    if not working_directory.is_dir():
        raise ReleaseError("gate working directory must be a directory")
    observed_source_commit = _git(working_directory, "rev-parse", "HEAD")
    if _git(working_directory, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ReleaseError("gate checkout must be clean before command execution")
    if (
        build_fields is not None
        and build_fields.get("source_commit") != observed_source_commit
    ):
        raise ReleaseError(
            "build evidence source_commit does not equal the working directory HEAD"
        )
    if record_path.resolve(strict=False) == log_path.resolve(strict=False):
        raise ReleaseError("gate record and log must be different files")
    if record_path.exists() or log_path.exists():
        raise ReleaseError("gate evidence paths must not already exist")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    started_at = _normalize_utc_now()
    total_bytes = 0
    retained_bytes = 0
    drain_error: BaseException | None = None
    process: subprocess.Popen[bytes] | None = None
    process_error: BaseException | None = None
    timed_out = False
    post_fields: dict[str, Any] = {}
    old_umask: int | None = None

    with log_path.open("xb") as log:
        try:
            if child_umask is not None:
                old_umask = os.umask(child_umask)
            process = subprocess.Popen(
                argv,
                cwd=str(working_directory),
                env=dict(environment) if environment is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name == "posix"),
            )
        except BaseException as exc:
            process_error = exc
        finally:
            if old_umask is not None:
                os.umask(old_umask)

        def drain() -> None:
            nonlocal total_bytes, retained_bytes, drain_error
            try:
                assert process is not None and process.stdout is not None
                while True:
                    chunk = process.stdout.read(64 << 10)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    remaining = MAX_RETAINED_LOG_BYTES - retained_bytes
                    if remaining > 0:
                        kept = chunk[:remaining]
                        log.write(kept)
                        retained_bytes += len(kept)
            except BaseException as exc:
                drain_error = exc

        thread: threading.Thread | None = None
        if process is not None:
            thread = threading.Thread(target=drain, name=f"release-{name}-log", daemon=True)
            thread.start()
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop_process(process)
            except BaseException as exc:
                process_error = exc
                _stop_process(process)
            thread.join(timeout=5)
            if thread.is_alive():
                process_error = ReleaseError("gate output drainer did not settle")
                _stop_process(process)
                thread.join(timeout=2)
        if drain_error is not None and process_error is None:
            process_error = drain_error

        exit_code = (
            127
            if process is None
            else 124
            if timed_out
            else process.returncode
            if process.returncode is not None
            else 1
        )
        if process_error is None and exit_code == 0:
            try:
                if _git(working_directory, "rev-parse", "HEAD") != observed_source_commit:
                    raise ReleaseError("gate command changed the checkout HEAD")
                if _git(
                    working_directory,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                ):
                    raise ReleaseError("gate command left the checkout dirty")
                if post_success is not None:
                    post_fields.update(dict(post_success() or {}))
            except BaseException as exc:
                process_error = exc
                exit_code = 1
                diagnostic = f"\nrelease post-check failed: {exc}\n".encode("utf-8")
                total_bytes += len(diagnostic)
                remaining = MAX_RETAINED_LOG_BYTES - retained_bytes
                if remaining > 0:
                    kept = diagnostic[:remaining]
                    log.write(kept)
                    retained_bytes += len(kept)
        log.flush()
        os.fsync(log.fileno())

    log_size, log_sha = hash_path(log_path)
    completed_at = _normalize_utc_now()
    status = "passed" if process_error is None and exit_code == 0 and not timed_out else "failed"
    record: dict[str, Any] = {
        "name": name,
        "command_argv": argv,
        "status": status,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "exit_code": int(exit_code),
        "working_directory": str(working_directory),
        "source_commit": observed_source_commit,
        "log_filename": log_path.name,
        "log_size_bytes": log_size,
        "log_total_bytes": total_bytes,
        "log_truncated": total_bytes > log_size,
        "log_sha256": log_sha,
    }
    if build_fields is not None:
        record.update(dict(build_fields))
    record.update(post_fields)
    if status == "passed":
        validate_gate_record(record, expected_name=name, build=(name == "build"))
    _atomic_json(record_path, record)

    if timed_out:
        raise ReleaseError(f"{name} gate timed out after {timeout_s:g}s")
    if process_error is not None:
        if isinstance(process_error, (KeyboardInterrupt, SystemExit)):
            raise process_error
        raise ReleaseError(f"{name} gate failed before evidence approval: {process_error}")
    if exit_code != 0:
        raise ReleaseError(f"{name} gate command exited with {exit_code}")
    return record


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise ReleaseError(f"git {' '.join(args)} failed: {detail}") from exc


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ReleaseError(f"required build tool is not installed: {name}") from exc


def _inspect_wheel_directory(directory: Path, matrix: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise ReleaseError(f"wheel output is not a directory: {root}")
    children = list(root.iterdir())
    if not children:
        raise ReleaseError(f"wheel output is empty: {root}")
    if any(child.suffix != ".whl" for child in children):
        raise ReleaseError("wheel output directory must contain wheels only")
    inspected = [inspect_wheel(child) for child in children]
    by_package = {item["package"]: item for item in inspected}
    if len(by_package) != len(inspected) or set(by_package) != set(EXPECTED_PACKAGES):
        raise ReleaseError("wheel output must contain exactly one wheel for each release package")
    for package, version in matrix.items():
        if by_package[package]["version"] != version:
            raise ReleaseError(f"wheel output version disagrees with tagged source for {package}")
    return by_package


def run_reproducible_build(
    *,
    repo: Path,
    tag: str,
    wheel_dir: Path,
    record_path: Path,
    log_path: Path,
    timeout_s: float,
) -> dict[str, Any]:
    """Build all three wheels from a clean checkout exactly at the annotated tag."""

    source = tagged_source(repo, tag)
    repo = repo.resolve(strict=True)
    if _git(repo, "rev-parse", "HEAD") != source["source_commit"]:
        raise ReleaseError("build checkout HEAD does not equal the annotated tag's source commit")
    dirty = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise ReleaseError("build checkout must be clean before release evidence is created")
    for label, output in (
        ("wheel directory", wheel_dir),
        ("build record", record_path),
        ("build log", log_path),
    ):
        candidate = output.resolve(strict=False)
        if candidate == repo or repo in candidate.parents:
            raise ReleaseError(f"{label} must live outside the clean tagged checkout")
    wheel_dir.mkdir(parents=True, exist_ok=True)
    if any(wheel_dir.iterdir()):
        raise ReleaseError("wheel output directory must start empty")

    pip_version = _distribution_version("pip")
    backend_name = source["backend_name"]
    backend_distribution = (
        "setuptools"
        if backend_name.startswith("setuptools")
        else backend_name.split(".", 1)[0]
    )
    backend_version = _distribution_version(backend_distribution)
    command = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--no-build-isolation",
        "--wheel-dir",
        str(wheel_dir.resolve()),
        *[
            str((repo / source_path).parent)
            for source_path in EXPECTED_PACKAGES.values()
        ],
    ]
    environment = dict(os.environ)
    environment.update(
        {
            "SOURCE_DATE_EPOCH": str(source["source_date_epoch"]),
            "PYTHONHASHSEED": "0",
        }
    )
    build_fields = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "frontend": {"name": "pip", "version": pip_version},
        "backend": {"name": backend_name, "version": backend_version},
        "tool_versions": [
            {"name": "pip", "version": pip_version},
            {"name": "setuptools", "version": _distribution_version("setuptools")},
        ],
        "source_commit": source["source_commit"],
        "source_date_epoch": source["source_date_epoch"],
        "umask": "022",
    }

    def inspect_built_artifacts() -> Mapping[str, Any]:
        matrix = _inspect_wheel_directory(wheel_dir, source["matrix"])
        return {
            "built_artifacts": [
                {
                    key: item[key]
                    for key in ("package", "version", "filename", "size_bytes", "sha256")
                }
                for item in sorted(matrix.values(), key=lambda row: row["package"])
            ]
        }

    return execute_gate(
        name="build",
        command=command,
        cwd=repo,
        record_path=record_path,
        log_path=log_path,
        timeout_s=timeout_s,
        environment=environment,
        build_fields=build_fields,
        child_umask=0o22,
        post_success=inspect_built_artifacts,
    )


def compare_builds(*, repo: Path, tag: str, first: Path, second: Path) -> dict[str, str]:
    source = tagged_source(repo, tag)
    first_matrix = _inspect_wheel_directory(first, source["matrix"])
    second_matrix = _inspect_wheel_directory(second, source["matrix"])
    result: dict[str, str] = {}
    for package in sorted(EXPECTED_PACKAGES):
        left = first_matrix[package]
        right = second_matrix[package]
        for field in ("package", "version", "filename", "size_bytes", "sha256"):
            if left[field] != right[field]:
                raise ReleaseError(
                    f"reproducible build mismatch for {package}.{field}: "
                    f"{left[field]!r} != {right[field]!r}"
                )
        result[left["filename"]] = left["sha256"]
    return result


def run_installed_smoke(
    *,
    wheels: list[Path],
    venv_dir: Path,
    work_dir: Path,
) -> None:
    """Install the exact matrix outside the repository and exercise public runtime doors."""

    inspected = [inspect_wheel(path) for path in wheels]
    matrix = {item["package"]: item for item in inspected}
    if len(matrix) != len(inspected) or set(matrix) != set(EXPECTED_PACKAGES):
        raise ReleaseError("installed smoke requires the exact three-package wheel matrix")
    for label, directory in (("venv", venv_dir), ("smoke work", work_dir)):
        if directory.exists() and any(directory.iterdir()):
            raise ReleaseError(f"{label} directory must start empty: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
    smoke_environment = dict(os.environ)
    smoke_environment.pop("PYTHONPATH", None)
    smoke_environment.pop("PYTHONHOME", None)
    smoke_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    venv_python = (
        venv_dir / "Scripts" / "python.exe"
        if os.name == "nt"
        else venv_dir / "bin" / "python"
    )
    subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            *[
                (
                    f"ai-workflow-tools[openai] @ {path.resolve(strict=True).as_uri()}"
                    if inspect_wheel(path)["package"] == "ai-workflow-tools"
                    else str(path.resolve(strict=True))
                )
                for path in wheels
            ],
        ],
        check=True,
        cwd=str(work_dir),
        env=smoke_environment,
    )
    expected_versions = {
        package: item["version"] for package, item in matrix.items()
    }
    smoke_program = r'''
import asyncio
import importlib.metadata
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ai_workflow_engine import (
    DurableWaitPolicy,
    InMemoryWaitCoordinator,
    ObservationConfig,
    WaitEvent,
    WorkflowArtifact,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowGoal,
    load_bundle_meta_v3,
)
from ai_workflow_engine.models import CapabilityResult
from ai_workflow_engine.llm_protocol import LLMRequest
from ai_workflow_tools.providers.openai_compatible import (
    NoAuth,
    OpenAICompatibleLLMClient,
    OpenAICompatibleProviderConfig,
)
from ai_workflow_tools.testing.openai_compatible import (
    OpenAICompatibleTestServer,
    ProviderTestResponse,
)
from ai_workflow_viewer import FileEventSource
from pydantic import BaseModel
import ai_workflow_tools
import ai_workflow_viewer

root = Path(sys.argv[1]).resolve()
expected = json.loads(sys.argv[2])
for package, version in expected.items():
    assert importlib.metadata.version(package) == version

class GateState(BaseModel):
    status: str
    value: str = ""

def durable_engine(coordinator, clock, *, workflow_id, two_gates=False):
    calls = {"gate": 0, "second": 0, "finish": 0, "escalate": 0}

    def gate(context, _payload):
        calls["gate"] += 1
        event = context.metadata.get("resume_event")
        return GateState(
            status="answered" if event is not None else "pending",
            value="" if event is None else str(event),
        )

    def second(context, _payload):
        calls["second"] += 1
        event = context.metadata.get("resume_event")
        return GateState(status="answered" if event is not None else "pending")

    def finish(_context, payload):
        calls["finish"] += 1
        return {"finished": True, "value": getattr(payload, "value", "")}

    def escalate(_context, _payload):
        calls["escalate"] += 1
        return {"escalated": True}

    builder = WorkflowEngineBuilder().with_wait_coordinator(coordinator, clock=clock)
    builder.register_capability("gate", gate)
    builder.register_capability("second", second)
    builder.register_capability("finish", finish)
    builder.register_capability("escalate", escalate)
    flow = WorkflowBuilder(workflow_id).human(
        "gate",
        wait_policy=DurableWaitPolicy(timeout_s=60),
        timeout_to="escalate",
    )
    if two_gates:
        flow = flow.human(
            "second",
            wait_policy=DurableWaitPolicy(timeout_s=60),
            timeout_to="escalate",
        )
    builder.register_workflow(flow.step("finish").step("escalate").build())
    return builder.build(), calls

async def main():
    recorded_provider_requests = []

    def provider_response(request):
        recorded_provider_requests.append(request)
        return ProviderTestResponse(
            200,
            {
                "id": "provider-request-installed",
                "model": "installed-model",
                "choices": [
                    {
                        "message": {"content": "installed-provider-ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                    "prompt_tokens_details": {"cached_tokens": 2},
                    "completion_tokens_details": {"reasoning_tokens": 1},
                },
            },
            headers={"x-request-id": "provider-request-installed"},
        )

    with OpenAICompatibleTestServer(provider_response) as provider_server:
        provider_client = OpenAICompatibleLLMClient(
            OpenAICompatibleProviderConfig(
                base_url=provider_server.base_url,
                model="installed-model",
                provider="installed-loopback",
                auth=NoAuth(),
                timeout_s=5,
            )
        )
        try:
            provider_result = await provider_client(
                LLMRequest(
                    user="installed provider smoke",
                    invocation_id="installed-provider-invocation",
                )
            )
        finally:
            await provider_client.aclose()
    assert provider_result.text == "installed-provider-ok"
    assert provider_result.invocation_id == "installed-provider-invocation"
    assert provider_result.metadata["provider_request_id"] == "provider-request-installed"
    assert provider_result.normalized_usage.uncached_input_tokens == 3
    assert provider_result.normalized_usage.cache_read_input_tokens == 2
    assert provider_result.normalized_usage.non_reasoning_output_tokens == 2
    assert provider_result.normalized_usage.reasoning_output_tokens == 1
    assert len(recorded_provider_requests) == 1
    assert "authorization" not in recorded_provider_requests[0].headers

    ordinary_root = root / "ordinary"
    ordinary_builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(ordinary_root))
    )
    ordinary_builder.register_capability(
        "double", lambda _context, payload: {"value": payload["value"] * 2},
        kind="deterministic",
    )
    ordinary_builder.register_workflow(
        WorkflowBuilder("ordinary").step("double").build()
    )
    ordinary = await ordinary_builder.build().run(
        "ordinary",
        {"value": 21},
        goal=WorkflowGoal(
            workflow_type="ordinary",
            objective="installed wheel smoke",
            metadata={"run_id": "installed-ordinary"},
        ),
    )
    assert ordinary.status == "completed"
    assert ordinary.output == {"value": 42}
    assert FileEventSource(ordinary_root).read_group("installed-ordinary").status == "completed"

    cancel_root = root / "cancelled"
    artifact_path = root / "completed.txt"
    blocked = asyncio.Event()

    async def capture(_context, payload):
        artifact_path.write_text("completed", encoding="utf-8")
        return CapabilityResult(
            output=payload,
            artifacts=[
                WorkflowArtifact(
                    artifact_id="installed-artifact",
                    path=str(artifact_path),
                    source="installed-smoke",
                )
            ],
        )

    async def block(_context, payload):
        blocked.set()
        await asyncio.Event().wait()
        return payload

    cancel_builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(
            enabled=True,
            bundle_dir=str(cancel_root),
            artifacts="copy",
        )
    )
    cancel_builder.register_capability("capture", capture, kind="deterministic")
    cancel_builder.register_capability("block", block, kind="deterministic")
    cancel_builder.register_workflow(
        WorkflowBuilder("cancel").step("capture").step("block").build()
    )
    task = asyncio.create_task(
        cancel_builder.build().run(
            "cancel",
            {},
            goal=WorkflowGoal(
                workflow_type="cancel",
                objective="installed cancellation smoke",
                metadata={"run_id": "installed-cancel"},
            ),
        )
    )
    await asyncio.wait_for(blocked.wait(), timeout=5)
    task.cancel("installed-smoke")
    try:
        await task
    except asyncio.CancelledError as exc:
        assert exc.args == ("installed-smoke",)
    else:
        raise AssertionError("caller cancellation was swallowed")
    meta = load_bundle_meta_v3(cancel_root / "installed-cancel")
    assert meta.status == "cancelled" and meta.artifact_count == 1
    assert FileEventSource(cancel_root).read_group("installed-cancel").status == "cancelled"

    fixed_now = datetime(2036, 1, 1, tzinfo=timezone.utc)
    clock = lambda: fixed_now

    # MageQA blocker 1: cancellation after coordinator commit but before receipt exposure.
    committed = asyncio.Event()
    seen = {}

    class CommitThenBlock(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json, definition_json):
            receipt = await super().register(record, snapshot_json, definition_json)
            seen["receipt"] = receipt
            committed.set()
            await asyncio.Event().wait()
            return receipt

    cancel_coordinator = CommitThenBlock(clock=clock)
    cancel_engine, cancel_calls = durable_engine(
        cancel_coordinator, clock, workflow_id="cancel-registration"
    )
    register_task = asyncio.create_task(
        cancel_engine.run("cancel-registration", {})
    )
    await asyncio.wait_for(committed.wait(), timeout=5)
    register_task.cancel("registration-cancel")
    try:
        await register_task
    except asyncio.CancelledError as exc:
        assert exc.args == ("registration-cancel",)
    else:
        raise AssertionError("registration cancellation was swallowed")
    cancelled_record = await cancel_coordinator.get(seen["receipt"].wait_id)
    assert cancelled_record.status == "cancelled"
    cancelled_claim = await cancel_coordinator.claim_event(
        cancelled_record.wait_id,
        WaitEvent(kind="signal", event_id="hidden-cancelled"),
        registration_id=seen["receipt"].registration_id,
        lease_until=fixed_now,
    )
    assert cancelled_claim.kind == "terminal"
    assert cancel_calls["finish"] == 0

    # MageQA blocker 2: registration commits but acknowledgement is lost.
    ack_seen = {}

    class AckLoss(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json, definition_json):
            receipt = await super().register(record, snapshot_json, definition_json)
            ack_seen["receipt"] = receipt
            raise RuntimeError("registration acknowledgement lost")

    ack_coordinator = AckLoss(clock=clock)
    ack_engine, ack_calls = durable_engine(
        ack_coordinator, clock, workflow_id="ack-loss"
    )
    ack_result = await ack_engine.run("ack-loss", {})
    assert ack_result.status == "failed" and ack_result.wait_handle is None
    ack_record = await ack_coordinator.get(ack_seen["receipt"].wait_id)
    assert ack_record.status == "cancelled"
    ack_claim = await ack_coordinator.claim_event(
        ack_record.wait_id,
        WaitEvent(kind="signal", event_id="hidden-ack-loss"),
        registration_id=ack_seen["receipt"].registration_id,
        lease_until=fixed_now,
    )
    assert ack_claim.kind == "terminal"
    assert ack_calls["finish"] == 0

    # Abrupt process death remains recoverable by an identical retry. The accepted
    # receipt is reused and duplicate event delivery never executes twice.
    class SimulatedProcessDeath(BaseException):
        pass

    class CrashAfterCommit(InMemoryWaitCoordinator):
        async def register(self, record, snapshot_json, definition_json):
            await super().register(record, snapshot_json, definition_json)
            raise SimulatedProcessDeath("worker disappeared before receipt exposure")

    crash_state = {}
    crash_first = CrashAfterCommit(clock=clock, shared_state=crash_state)
    crash_engine, _ = durable_engine(crash_first, clock, workflow_id="crash-retry")
    crash_goal = WorkflowGoal(
        workflow_type="crash-retry",
        objective="installed crash retry",
        metadata={"run_id": "installed-crash-retry"},
    )
    try:
        await crash_engine.run("crash-retry", {}, goal=crash_goal)
    except SimulatedProcessDeath:
        pass
    else:
        raise AssertionError("simulated process death did not escape")
    wait_id = next(iter(crash_state["records"]))
    stored_receipt = await crash_first.load_receipt(wait_id)
    crash_second = InMemoryWaitCoordinator(clock=clock, shared_state=crash_state)
    recovered_engine, recovered_calls = durable_engine(
        crash_second, clock, workflow_id="crash-retry"
    )
    recovered = await recovered_engine.run("crash-retry", {}, goal=crash_goal)
    assert recovered.wait_handle.registration_id == stored_receipt.registration_id
    crash_event = WaitEvent(
        kind="signal",
        event_id="crash-event",
        payload="approved",
    )
    crash_done = await recovered_engine.deliver_wait_event(recovered.wait_handle, crash_event)
    crash_duplicate = await recovered_engine.deliver_wait_event(
        recovered.wait_handle, crash_event
    )
    assert crash_done.kind == "executed"
    assert crash_duplicate.kind == "duplicate"
    assert recovered_calls["finish"] == 1

    # Timeout uses the same complete-handle door, and a resumed run may expose another
    # complete handle without losing the first claim's terminal truth.
    timeout_coordinator = InMemoryWaitCoordinator(clock=clock)
    timeout_engine, timeout_calls = durable_engine(
        timeout_coordinator, clock, workflow_id="timeout-delivery"
    )
    timed = await timeout_engine.run("timeout-delivery", {})
    timed_out = await timeout_engine.deliver_wait_event(
        timed.wait_handle,
        WaitEvent(kind="timeout", event_id="timeout-event"),
    )
    assert timed_out.kind == "executed" and timeout_calls["escalate"] == 1

    chain_coordinator = InMemoryWaitCoordinator(clock=clock)
    chain_engine, chain_calls = durable_engine(
        chain_coordinator, clock, workflow_id="chained-waits", two_gates=True
    )
    first_wait = await chain_engine.run("chained-waits", {})
    second_wait = await chain_engine.deliver_wait_event(
        first_wait.wait_handle,
        WaitEvent(kind="signal", event_id="first-gate", payload="approved"),
    )
    assert second_wait.run_result.status == "requires_user_input"
    assert second_wait.run_result.wait_handle is not None
    chain_done = await chain_engine.deliver_wait_event(
        second_wait.run_result.wait_handle,
        WaitEvent(kind="signal", event_id="second-gate", payload="approved"),
    )
    assert chain_done.run_result.status == "completed"
    assert chain_calls["finish"] == 1

    coordinator = InMemoryWaitCoordinator(
        clock=lambda: datetime.now(timezone.utc),
        shared_state={},
    )
    health = await coordinator.health()
    assert health.failed == 0 and health.integrity_errors == 0

asyncio.run(main())
'''
    subprocess.run(
        [
            str(venv_python),
            "-I",
            "-c",
            smoke_program,
            str(work_dir),
            json.dumps(expected_versions, sort_keys=True),
        ],
        check=True,
        cwd=str(work_dir),
        env=smoke_environment,
    )


def assemble_release_bundle(
    *,
    repo: Path,
    tag: str,
    bundle_dir: Path,
    uri_base: str,
    wheels: list[Path],
    build_evidence_path: Path,
    second_wheels: list[Path],
    second_build_evidence_path: Path,
    test_evidence_path: Path,
    smoke_evidence_path: Path,
    verifier_paths: list[Path],
) -> dict[str, Any]:
    """Stage one exact release directory, then verify it from the staged bytes."""

    bundle_dir.mkdir(parents=True, exist_ok=True)
    if any(bundle_dir.iterdir()):
        raise ReleaseError("release bundle directory must start empty")
    if {path.name for path in verifier_paths} != VERIFIER_FILENAMES:
        raise ReleaseError("release bundle requires the exact two verifier source files")

    manifest = assemble_manifest(
        repo=repo,
        tag=tag,
        wheels=wheels,
        build_evidence_path=build_evidence_path,
        second_wheels=second_wheels,
        second_build_evidence_path=second_build_evidence_path,
        test_evidence_path=test_evidence_path,
        smoke_evidence_path=smoke_evidence_path,
        uri_base=uri_base,
        verifier_paths=verifier_paths,
    )
    evidence = [
        (build_evidence_path, load_evidence_record(build_evidence_path, "build", build=True)),
        (
            second_build_evidence_path,
            load_evidence_record(second_build_evidence_path, "build", build=True),
        ),
        (test_evidence_path, load_evidence_record(test_evidence_path, "test")),
        (smoke_evidence_path, load_evidence_record(smoke_evidence_path, "smoke")),
    ]
    sources = list(wheels) + list(verifier_paths)
    for record_path, record in evidence:
        sources.extend([record_path, record_path.parent / record["log_filename"]])
    names = [path.name for path in sources]
    if len(names) != len(set(names)):
        raise ReleaseError("release input filenames must be unique")
    for source in sources:
        copy_regular_file(source, bundle_dir / source.name)

    manifest_path = bundle_dir / "release-manifest.json"
    _atomic_json(manifest_path, manifest)
    expected = manifest_bundle_filenames(manifest)
    if {path.name for path in bundle_dir.iterdir()} != expected:
        raise ReleaseError("staged release files disagree with the manifest before checksums")
    write_sha256sums(
        [bundle_dir / filename for filename in sorted(expected)],
        bundle_dir / "SHA256SUMS",
    )
    return verify_bundle(bundle_dir, "release-manifest.json", "SHA256SUMS")


def _command_after_separator(tokens: list[str]) -> list[str]:
    return tokens[1:] if tokens and tokens[0] == "--" else tokens


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release_artifacts")
    sub = parser.add_subparsers(dest="command", required=True)

    gate = sub.add_parser("run-gate")
    gate.add_argument("--name", choices=("test", "smoke"), required=True)
    gate.add_argument("--record", required=True)
    gate.add_argument("--log", required=True)
    gate.add_argument("--cwd", default=".")
    gate.add_argument("--timeout-s", type=float, required=True)
    gate.add_argument("argv", nargs=argparse.REMAINDER)

    build = sub.add_parser("run-build")
    build.add_argument("--repo", default=".")
    build.add_argument("--tag", required=True)
    build.add_argument("--wheel-dir", required=True)
    build.add_argument("--record", required=True)
    build.add_argument("--log", required=True)
    build.add_argument("--timeout-s", type=float, default=900)

    compare = sub.add_parser("compare-builds")
    compare.add_argument("--repo", default=".")
    compare.add_argument("--tag", required=True)
    compare.add_argument("--first", required=True)
    compare.add_argument("--second", required=True)

    assemble = sub.add_parser("assemble")
    assemble.add_argument("--repo", default=".")
    assemble.add_argument("--tag", required=True)
    assemble.add_argument("--bundle-dir", required=True)
    assemble.add_argument("--uri-base", required=True)
    assemble.add_argument("--build-evidence", required=True)
    assemble.add_argument("--second-build-evidence", required=True)
    assemble.add_argument("--test-evidence", required=True)
    assemble.add_argument("--smoke-evidence", required=True)
    assemble.add_argument("--wheel", action="append", required=True)
    assemble.add_argument("--second-wheel", action="append", required=True)
    assemble.add_argument("--verifier", action="append")

    verify = sub.add_parser("verify-bundle")
    verify.add_argument("--dir", required=True)
    verify.add_argument("--manifest", default="release-manifest.json")
    verify.add_argument("--sums", default="SHA256SUMS")

    inspect = sub.add_parser("inspect-wheel")
    inspect.add_argument("wheel")

    smoke = sub.add_parser("smoke-installed")
    smoke.add_argument("--venv-dir", required=True)
    smoke.add_argument("--work-dir", required=True)
    smoke.add_argument("--wheel", action="append", required=True)
    return parser


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "run-gate":
            execute_gate(
                name=args.name,
                command=_command_after_separator(args.argv),
                cwd=Path(args.cwd),
                record_path=Path(args.record),
                log_path=Path(args.log),
                timeout_s=args.timeout_s,
            )
            print(f"{args.name} evidence PASSED: {args.record}")
        elif args.command == "run-build":
            run_reproducible_build(
                repo=Path(args.repo),
                tag=args.tag,
                wheel_dir=Path(args.wheel_dir),
                record_path=Path(args.record),
                log_path=Path(args.log),
                timeout_s=args.timeout_s,
            )
            print(f"build evidence PASSED: {args.record}")
        elif args.command == "compare-builds":
            result = compare_builds(
                repo=Path(args.repo),
                tag=args.tag,
                first=Path(args.first),
                second=Path(args.second),
            )
            print(json.dumps(result, sort_keys=True, indent=2))
        elif args.command == "assemble":
            verifier_paths = (
                [Path(item) for item in args.verifier]
                if args.verifier
                else [_SCRIPT_DIR / name for name in sorted(VERIFIER_FILENAMES)]
            )
            manifest = assemble_release_bundle(
                repo=Path(args.repo),
                tag=args.tag,
                bundle_dir=Path(args.bundle_dir),
                uri_base=args.uri_base,
                wheels=[Path(item) for item in args.wheel],
                build_evidence_path=Path(args.build_evidence),
                second_wheels=[Path(item) for item in args.second_wheel],
                second_build_evidence_path=Path(args.second_build_evidence),
                test_evidence_path=Path(args.test_evidence),
                smoke_evidence_path=Path(args.smoke_evidence),
                verifier_paths=verifier_paths,
            )
            print(
                f"release bundle PASSED: {args.bundle_dir} "
                f"({manifest['tag']} {manifest['source_commit']})"
            )
        elif args.command == "verify-bundle":
            manifest = verify_bundle(Path(args.dir), args.manifest, args.sums)
            print(
                f"release bundle verification PASSED: "
                f"{manifest['tag']} {manifest['source_commit']}"
            )
        elif args.command == "inspect-wheel":
            print(json.dumps(inspect_wheel(Path(args.wheel)), sort_keys=True, indent=2))
        else:
            run_installed_smoke(
                wheels=[Path(item) for item in args.wheel],
                venv_dir=Path(args.venv_dir),
                work_dir=Path(args.work_dir),
            )
            print("installed-wheel smoke PASSED")
        return 0
    except ReleaseError as exc:
        print(f"release contract FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
