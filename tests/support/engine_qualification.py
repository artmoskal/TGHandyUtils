"""Cross-consumer engine qualification support — Q0.2 runtime identity manifest.

The manifest makes a qualification run diagnosable (alias/CLI drift) WITHOUT leaking
anything sensitive: it records exactly the six identity facts and nothing else —
no auth token, no environment dump, no prompt text, no home paths.
"""

from __future__ import annotations

import platform as _platform_module
import subprocess
from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, field_validator

_SENSITIVE_MARKERS = ("sk-ant-", "oauth", "token=", "authorization:")


class QualificationRunIdentity(BaseModel):
    """The ONLY identity facts a qualification manifest may carry (extra fields forbidden)."""

    model_config = ConfigDict(extra="forbid")

    requested_model: str
    cli_version: str
    git_commit: str
    platform: str
    started_at: str
    finished_at: Optional[str] = None

    @field_validator("*")
    @classmethod
    def _no_sensitive_values(cls, value):
        if isinstance(value, str):
            lowered = value.lower()
            for marker in _SENSITIVE_MARKERS:
                if marker in lowered:
                    raise ValueError(f"sensitive marker {marker!r} must never enter the manifest")
            if lowered.startswith(("/users/", "/home/")):
                raise ValueError("home paths must never enter the manifest")
        return value


def capture_run_identity(
    requested_model: str,
    *,
    cli_version_reader: Optional[Callable[[], str]] = None,
    git_commit_reader: Optional[Callable[[], str]] = None,
    platform_reader: Optional[Callable[[], str]] = None,
    clock: Optional[Callable[[], str]] = None,
) -> QualificationRunIdentity:
    """Capture the run identity. Readers are injectable so unit tests stay deterministic
    and spawn nothing; the defaults shell out to `claude --version` / `git rev-parse`."""

    if not requested_model:
        raise ValueError("requested_model is required — a run without a model alias is not reproducible")
    read_version = cli_version_reader or _read_claude_cli_version
    read_commit = git_commit_reader or _read_git_commit
    read_platform = platform_reader or (lambda: f"{_platform_module.system()}-{_platform_module.machine()}")
    read_clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return QualificationRunIdentity(
        requested_model=requested_model,
        cli_version=read_version(),
        git_commit=read_commit(),
        platform=read_platform(),
        started_at=read_clock(),
    )


def _read_claude_cli_version() -> str:
    proc = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude --version failed: {proc.stderr.strip()[-300:]}")
    return proc.stdout.strip()


def _read_git_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {proc.stderr.strip()[-300:]}")
    return proc.stdout.strip()
