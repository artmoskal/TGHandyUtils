"""Release-manifest builder/verifier (v0.11.5 release rigor; host utility + importable lib).

The manifest is the engine-owned identity record for published artifacts (codex-settled D4):
the whole-wheel SHA-256 of the EXACT published bytes is authoritative; consumers verify the
downloaded file against it BEFORE installation. Reproducible builds (SOURCE_DATE_EPOCH from
the release commit + stable umask + recorded tool versions) make that hash meaningful across
clean rebuilds — claim reproducibility only after two independent clean-checkout builds match.

Usage (host, from repo root):

    Build:   python3 packages/ai_workflow_engine/tests/release_manifest.py build \
                 --tag <tag> --out manifest.json <wheel> [<wheel> ...]
    Verify:  python3 packages/ai_workflow_engine/tests/release_manifest.py verify \
                 --manifest manifest.json <wheel> [<wheel> ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = "release-manifest-v1"

_REQUIRED_TOP = (
    "manifest_schema_version",
    "tag",
    "tag_object_id",
    "source_commit",
    "python_version",
    "build_command",
    "reproducibility",
    "artifacts",
)
_REQUIRED_ARTIFACT = ("package", "version", "filename", "size_bytes", "sha256", "published")


class ManifestError(ValueError):
    """Loud, named manifest/verification failure."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wheel_identity(path: Path) -> tuple[str, str]:
    name = path.name
    if not name.endswith(".whl"):
        raise ManifestError(f"not a wheel: {name}")
    package, version = name.split("-")[0], name.split("-")[1]
    return package.replace("_", "-"), version


def build_manifest(
    wheels: list[Path],
    *,
    tag: str,
    tag_object_id: str,
    source_commit: str,
    build_command: str,
    source_date_epoch: str,
    published: set[str] | None = None,
    smoke_result: str = "not-run",
    test_result: str = "not-run",
) -> dict[str, Any]:
    artifacts = []
    for wheel in wheels:
        package, version = _wheel_identity(wheel)
        artifacts.append(
            {
                "package": package,
                "version": version,
                "filename": wheel.name,
                "size_bytes": wheel.stat().st_size,
                "sha256": _sha256(wheel),
                "published": published is None or package in published,
            }
        )
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "tag": tag,
        "tag_object_id": tag_object_id,
        "source_commit": source_commit,
        "python_version": platform.python_version(),
        "build_command": build_command,
        "reproducibility": {
            "source_date_epoch": source_date_epoch,
            "umask": "022",
            "note": "two independent clean-checkout builds must match before claiming reproducibility",
        },
        "test_result": test_result,
        "smoke_result": smoke_result,
        "artifacts": artifacts,
    }


def verify_manifest(manifest: dict[str, Any], wheels: list[Path]) -> None:
    """Verify downloaded/published bytes against the manifest. Raises ManifestError loudly."""

    for field in _REQUIRED_TOP:
        if field not in manifest:
            raise ManifestError(f"manifest missing required field: {field}")
    if manifest["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            f"unknown manifest schema: {manifest['manifest_schema_version']!r} "
            f"(expected {MANIFEST_SCHEMA_VERSION})"
        )
    by_name = {}
    for entry in manifest["artifacts"]:
        for field in _REQUIRED_ARTIFACT:
            if field not in entry:
                raise ManifestError(f"artifact entry missing required field: {field}")
        by_name[entry["filename"]] = entry
    for wheel in wheels:
        entry = by_name.get(wheel.name)
        if entry is None:
            raise ManifestError(f"wheel not in manifest: {wheel.name}")
        actual_size = wheel.stat().st_size
        if actual_size != entry["size_bytes"]:
            raise ManifestError(
                f"{wheel.name}: size mismatch (manifest {entry['size_bytes']}, actual {actual_size})"
            )
        actual_sha = _sha256(wheel)
        if actual_sha != entry["sha256"]:
            raise ManifestError(
                f"{wheel.name}: SHA-256 mismatch — bytes are NOT the published artifact\n"
                f"  manifest: {entry['sha256']}\n  actual:   {actual_sha}"
            )


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release_manifest")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--tag", required=True)
    build.add_argument("--out", required=True)
    build.add_argument("--published", default="ai-workflow-engine")
    build.add_argument("wheels", nargs="+")
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("wheels", nargs="+")
    args = parser.parse_args(argv)

    if args.command == "build":
        tag_object = _git("rev-parse", args.tag)
        commit = _git("rev-parse", f"{args.tag}^{{}}")
        epoch = _git("log", "-1", "--format=%ct", commit)
        manifest = build_manifest(
            [Path(w) for w in args.wheels],
            tag=args.tag,
            tag_object_id=tag_object,
            source_commit=commit,
            build_command=(
                "umask 022 && SOURCE_DATE_EPOCH=<epoch> python3 -m pip wheel --no-deps "
                "-w <out> <package-dirs>  # from a detached clean checkout of the tag"
            ),
            source_date_epoch=epoch,
            published=set(args.published.split(",")),
        )
        Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"manifest written: {args.out}")
        return 0

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    verify_manifest(manifest, [Path(w) for w in args.wheels])
    print("manifest verification PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
