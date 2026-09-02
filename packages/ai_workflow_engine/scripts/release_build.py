"""Build the exact three-package release matrix with the pinned PEP 517 toolchain."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from release_toolchain import (
    EXPECTED_PACKAGES,
    RELEASE_BUILD_BACKEND,
    RELEASE_BUILD_FRONTEND,
    RELEASE_BUILD_TOOLS,
    RELEASE_WHEEL_GENERATOR,
    ReleaseError,
    candidate_source,
    git_output,
)


def installed_toolchain() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in RELEASE_BUILD_TOOLS:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ReleaseError(f"required release build tool is not installed: {name}") from exc
    return versions


def require_exact_toolchain() -> dict[str, str]:
    versions = installed_toolchain()
    if versions != RELEASE_BUILD_TOOLS:
        differences = ", ".join(
            f"{name}={versions.get(name)!r} (required {version!r})"
            for name, version in RELEASE_BUILD_TOOLS.items()
            if versions.get(name) != version
        )
        raise ReleaseError(f"installed release build toolchain mismatch: {differences}")
    return versions


def build_matrix(*, repo: Path, source_commit: str, wheel_dir: Path) -> None:
    repo = repo.resolve(strict=True)
    source = candidate_source(repo, source_commit)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != source["source_commit"]:
        raise ReleaseError("builder checkout HEAD does not equal source_commit")
    require_exact_toolchain()
    wheel_dir = wheel_dir.resolve(strict=True)
    from setuptools import build_meta

    original_cwd = Path.cwd()
    try:
        for source_path in EXPECTED_PACKAGES.values():
            os.chdir((repo / source_path).parent)
            build_meta.build_wheel(str(wheel_dir))
    finally:
        os.chdir(original_cwd)


def run_recorded_build(
    *,
    repo: Path,
    source: Mapping[str, Any],
    wheel_dir: Path,
    record_path: Path,
    log_path: Path,
    timeout_s: float,
    execute_gate: Callable[..., dict[str, Any]],
    inspect_wheel_directory: Callable[[Path, Mapping[str, str]], dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Build and record the exact matrix without letting the CLI own build policy."""

    repo = repo.resolve(strict=True)
    if git_output(repo, "rev-parse", "HEAD") != source["source_commit"]:
        raise ReleaseError("build checkout HEAD does not equal the release source commit")
    if git_output(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ReleaseError("build checkout must be clean before release evidence is created")
    require_exact_toolchain()
    for label, output in (
        ("wheel directory", wheel_dir),
        ("build record", record_path),
        ("build log", log_path),
    ):
        candidate = output.resolve(strict=False)
        if candidate == repo or repo in candidate.parents:
            raise ReleaseError(f"{label} must live outside the clean release checkout")
    wheel_dir.mkdir(parents=True, exist_ok=True)
    if any(wheel_dir.iterdir()):
        raise ReleaseError("wheel output directory must start empty")
    environment = dict(os.environ)
    environment.update(
        {
            "SOURCE_DATE_EPOCH": str(source["source_date_epoch"]),
            "PYTHONHASHSEED": "0",
        }
    )

    def inspect_built_artifacts() -> Mapping[str, Any]:
        matrix = inspect_wheel_directory(wheel_dir, source["matrix"])
        wrong = {
            package: item["generator"]
            for package, item in matrix.items()
            if item["generator"] != RELEASE_WHEEL_GENERATOR
        }
        if wrong:
            raise ReleaseError(f"wheel Generator disagrees with pinned backend: {wrong}")
        return {
            "built_artifacts": [
                {
                    key: item[key]
                    for key in (
                        "package",
                        "version",
                        "filename",
                        "size_bytes",
                        "sha256",
                        "generator",
                    )
                }
                for item in sorted(matrix.values(), key=lambda row: row["package"])
            ]
        }

    return execute_gate(
        name="build",
        command=[
            sys.executable,
            str(Path(__file__).resolve()),
            "--repo",
            str(repo),
            "--source-commit",
            str(source["source_commit"]),
            "--wheel-dir",
            str(wheel_dir.resolve()),
        ],
        cwd=repo,
        record_path=record_path,
        log_path=log_path,
        timeout_s=timeout_s,
        environment=environment,
        build_fields={
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "frontend": dict(RELEASE_BUILD_FRONTEND),
            "backend": {
                "name": RELEASE_BUILD_BACKEND,
                "version": RELEASE_BUILD_TOOLS["setuptools"],
            },
            "tool_versions": [
                {"name": name, "version": version}
                for name, version in sorted(RELEASE_BUILD_TOOLS.items())
            ],
            "source_commit": source["source_commit"],
            "source_date_epoch": source["source_date_epoch"],
            "umask": "022",
        },
        child_umask=0o22,
        post_success=inspect_built_artifacts,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release_build")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--wheel-dir", required=True)
    args = parser.parse_args(argv)
    try:
        build_matrix(
            repo=Path(args.repo),
            source_commit=args.source_commit,
            wheel_dir=Path(args.wheel_dir),
        )
    except (OSError, ReleaseError, subprocess.CalledProcessError) as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
