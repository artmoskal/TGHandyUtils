"""Closed source and toolchain contract for release production and verification."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

EXPECTED_PACKAGES = {
    "ai-workflow-engine": "packages/ai_workflow_engine/pyproject.toml",
    "ai-workflow-tools": "packages/ai_workflow_tools/pyproject.toml",
    "ai-workflow-viewer": "packages/ai_workflow_viewer/pyproject.toml",
}
RELEASE_BUILD_BACKEND = "setuptools.build_meta"
RELEASE_BUILD_FRONTEND = {"name": "release_build", "version": "1.0"}
RELEASE_BUILD_TOOLS = {
    "pip": "26.2.1",
    "setuptools": "84.0.0",
    "wheel": "0.48.0",
}
RELEASE_BUILD_REQUIREMENTS = tuple(
    f"{name}=={RELEASE_BUILD_TOOLS[name]}" for name in ("setuptools", "wheel")
)
RELEASE_WHEEL_GENERATOR = f"setuptools ({RELEASE_BUILD_TOOLS['setuptools']})"

GIT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[A-Za-z0-9.+_-]*)?$")


class ReleaseError(ValueError):
    """A named release contract violation."""


def canonical_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def git_output(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReleaseError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout.strip()


def _version(value: Any, path: str) -> str:
    if type(value) is not str or VERSION_RE.fullmatch(value) is None:
        raise ReleaseError(f"{path} must be a release version")
    return value


def candidate_source(repo: Path, source_commit: str) -> dict[str, Any]:
    """Read one exact source commit and require the closed release build toolchain."""

    import tomllib

    if type(source_commit) is not str or GIT_ID_RE.fullmatch(source_commit) is None:
        raise ReleaseError("source_commit must be a full Git object id")
    if git_output(repo, "rev-parse", f"{source_commit}^{{commit}}") != source_commit:
        raise ReleaseError("source_commit must identify one exact commit")
    matrix: dict[str, str] = {}
    backends: set[str] = set()
    requirements: set[tuple[str, ...]] = set()
    for expected_package, source_path in EXPECTED_PACKAGES.items():
        raw = git_output(repo, "show", f"{source_commit}:{source_path}")
        try:
            data = tomllib.loads(raw)
            project = data["project"]
            build_system = data["build-system"]
            package = canonical_package(project["name"])
            version = project["version"]
            backend = build_system["build-backend"]
            build_requires = build_system["requires"]
        except (KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
            raise ReleaseError(
                f"source {source_path} lacks release package/build identity"
            ) from exc
        if package != expected_package:
            raise ReleaseError(
                f"source {source_path} names {package!r}, expected {expected_package!r}"
            )
        matrix[package] = _version(version, f"source matrix.{package}")
        if type(backend) is not str or not backend:
            raise ReleaseError(f"source backend.{package} must be a string")
        backends.add(backend)
        if not isinstance(build_requires, list) or not all(
            type(item) is str and item for item in build_requires
        ):
            raise ReleaseError(f"source build requirements for {package} must be strings")
        requirements.add(tuple(sorted(build_requires)))
    expected_requirements = tuple(sorted(RELEASE_BUILD_REQUIREMENTS))
    if backends != {RELEASE_BUILD_BACKEND} or requirements != {expected_requirements}:
        raise ReleaseError(
            "release packages must share the exact build toolchain: "
            f"backend={RELEASE_BUILD_BACKEND!r}, requirements={list(RELEASE_BUILD_REQUIREMENTS)!r}"
        )
    epoch_text = git_output(repo, "log", "-1", "--format=%ct", source_commit)
    if re.fullmatch(r"[0-9]+", epoch_text) is None:
        raise ReleaseError("source commit has an invalid timestamp")
    return {
        "source_commit": source_commit,
        "source_date_epoch": int(epoch_text),
        "matrix": matrix,
        "backend_name": RELEASE_BUILD_BACKEND,
        "backend_version": RELEASE_BUILD_TOOLS["setuptools"],
        "build_requirements": list(RELEASE_BUILD_REQUIREMENTS),
    }
