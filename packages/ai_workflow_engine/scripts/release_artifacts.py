"""Release-artifact trust boundary (v0.11.5, codex-settled R2) — producer-side owner.

Standard-library only; never imported by the runtime engine. One trustworthy chain:

- **Wheel identity (M1/M2):** artifact bytes must be a valid ZIP wheel containing exactly one
  coherent ``*.dist-info`` with ``METADATA``, ``WHEEL``, and ``RECORD``; the EMBEDDED
  ``Name``/``Version`` are authoritative and must agree with the normalized filename.
- **Source identity (M3):** the Git tag proves source; the engine wheel version must equal the
  ``engine-vX.Y.Z`` tag suffix, and the tag must be ANNOTATED and peel to the recorded commit.
- **Manifest (M4-M8, schema ``release-manifest-v2``):** closed and type-strict; one coherent
  package matrix without duplicates; build environment recorded without placeholders; test and
  smoke evidence must be structured with ``status == "passed"``, a UTC completion timestamp, and
  an evidence-file SHA-256 before anything verifies as publishable; every published artifact
  records size, SHA-256, and URI.
- **Consumer pre-install verification (M9):** ``SHA256SUMS`` is generated from the same artifact
  records; standard checksum verification fails on any byte change, missing file, or extra claim.

The whole-wheel SHA-256 of the exact published bytes is the authoritative artifact identity.
``dist-info/RECORD`` is diagnostic content evidence only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = "release-manifest-v2"

_REQUIRED_TOP = (
    "manifest_schema_version",
    "tag",
    "tag_object_id",
    "source_commit",
    "build",
    "test_evidence",
    "smoke_evidence",
    "artifacts",
)
_REQUIRED_BUILD = ("python_version", "command_argv", "source_date_epoch", "umask")
_REQUIRED_ARTIFACT = ("package", "version", "filename", "size_bytes", "sha256", "published", "uri")
_REQUIRED_EVIDENCE = ("command", "status", "completed_at_utc", "evidence_file_sha256")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$")


class ReleaseError(ValueError):
    """Loud, named release-contract failure."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ wheel identity (M1/M2)


def inspect_wheel(path: Path) -> dict[str, str]:
    """Validate wheel BYTES and return the authoritative embedded identity."""

    if not zipfile.is_zipfile(path):
        raise ReleaseError(f"{path.name}: not a valid wheel (bytes are not a ZIP archive)")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        dist_infos = sorted({n.split("/")[0] for n in names if "/" in n and n.split("/")[0].endswith(".dist-info")})
        if len(dist_infos) != 1:
            raise ReleaseError(
                f"{path.name}: a wheel must contain exactly one *.dist-info directory, found {dist_infos}"
            )
        dist_info = dist_infos[0]
        for member in ("METADATA", "WHEEL", "RECORD"):
            if f"{dist_info}/{member}" not in names:
                raise ReleaseError(f"{path.name}: wheel is missing {dist_info}/{member}")
        metadata = zf.read(f"{dist_info}/METADATA").decode("utf-8", errors="strict")
    fields = {}
    for line in metadata.splitlines():
        if line.startswith("Name:"):
            fields["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Version:"):
            fields["version"] = line.split(":", 1)[1].strip()
    if "name" not in fields or "version" not in fields:
        raise ReleaseError(f"{path.name}: embedded METADATA lacks Name/Version")

    stem = path.name[: -len(".whl")]
    file_package, file_version = stem.split("-")[0], stem.split("-")[1]
    normalized_meta = fields["name"].lower().replace("-", "_")
    if normalized_meta != file_package.lower():
        raise ReleaseError(
            f"{path.name}: embedded Name {fields['name']!r} disagrees with filename package "
            f"{file_package!r} — embedded metadata is authoritative"
        )
    if fields["version"] != file_version:
        raise ReleaseError(
            f"{path.name}: embedded Version {fields['version']!r} disagrees with filename "
            f"version {file_version!r}"
        )
    return {"package": fields["name"].lower().replace("_", "-"), "version": fields["version"]}


# ------------------------------------------------------------------ tag binding (M3)


def validate_release_tag(repo: Path, tag: str, *, engine_version: str) -> dict[str, str]:
    """The tag proves source identity: annotated, peelable, and version-bound."""

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    expected_suffix = tag.removeprefix("engine-v")
    if engine_version != expected_suffix:
        raise ReleaseError(
            f"engine wheel version {engine_version!r} does not match tag {tag!r} — the tag "
            f"suffix must equal the released engine version"
        )
    object_type = _git("cat-file", "-t", tag)
    if object_type != "tag":
        raise ReleaseError(
            f"{tag}: release tags must be annotated tag objects, found {object_type!r} "
            f"(lightweight tags carry no message/identity)"
        )
    return {
        "tag_object_id": _git("rev-parse", tag),
        "source_commit": _git("rev-parse", f"{tag}^{{}}"),
    }


# ------------------------------------------------------------------ manifest build (M4-M8)


def build_manifest(
    wheels: list[Path],
    *,
    tag: str,
    tag_object_id: str,
    source_commit: str,
    build_command: Any,
    source_date_epoch: str,
    published: set[str] | None = None,
    uri_prefix: str = "",
    test_evidence: dict[str, Any] | None = None,
    smoke_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifacts = []
    seen_packages: set[str] = set()
    seen_filenames: set[str] = set()
    for wheel in wheels:
        identity = inspect_wheel(wheel)
        if identity["package"] in seen_packages:
            raise ReleaseError(f"duplicate package identity in matrix: {identity['package']}")
        if wheel.name in seen_filenames:
            raise ReleaseError(f"duplicate filename in matrix: {wheel.name}")
        seen_packages.add(identity["package"])
        seen_filenames.add(wheel.name)
        is_published = published is None or identity["package"] in published
        artifacts.append(
            {
                "package": identity["package"],
                "version": identity["version"],
                "filename": wheel.name,
                "size_bytes": wheel.stat().st_size,
                "sha256": _sha256(wheel),
                "published": is_published,
                "uri": (uri_prefix + wheel.name) if is_published else "",
            }
        )
    command_argv = build_command if isinstance(build_command, list) else [str(build_command)]
    for token in command_argv:
        if "<" in str(token) and ">" in str(token):
            raise ReleaseError(f"build command contains a placeholder token: {token!r}")
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "tag": tag,
        "tag_object_id": tag_object_id,
        "source_commit": source_commit,
        "build": {
            "python_version": platform.python_version(),
            "command_argv": command_argv,
            "source_date_epoch": str(source_date_epoch),
            "umask": "022",
        },
        "test_evidence": test_evidence,
        "smoke_evidence": smoke_evidence,
        "artifacts": artifacts,
    }


def build_release_manifest(
    wheels: list[Path],
    *,
    repo: Path,
    tag: str,
    published: set[str] | None = None,
    uri_prefix: str = "",
    test_evidence: dict[str, Any] | None = None,
    smoke_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full producer path: wheel identity + tag/source binding + manifest."""

    engine = next(
        (inspect_wheel(w) for w in wheels if inspect_wheel(w)["package"] == "ai-workflow-engine"),
        None,
    )
    if engine is None:
        raise ReleaseError("release matrix must include the ai-workflow-engine wheel")
    binding = validate_release_tag(repo, tag, engine_version=engine["version"])
    epoch = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%ct", binding["source_commit"]],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return build_manifest(
        wheels,
        tag=tag,
        tag_object_id=binding["tag_object_id"],
        source_commit=binding["source_commit"],
        build_command=[
            "python3", "-m", "pip", "wheel", "--no-deps", "-w", "wheels",
            "packages/ai_workflow_engine", "packages/ai_workflow_tools", "packages/ai_workflow_viewer",
        ],
        source_date_epoch=epoch,
        published=published,
        uri_prefix=uri_prefix,
        test_evidence=test_evidence,
        smoke_evidence=smoke_evidence,
    )


# ------------------------------------------------------------------ verification (M5-M8)


def _check_evidence(kind: str, evidence: Any) -> None:
    if not isinstance(evidence, dict):
        raise ReleaseError(f"{kind} evidence is required for verification (got {evidence!r})")
    for field in _REQUIRED_EVIDENCE:
        if field not in evidence:
            raise ReleaseError(f"{kind} evidence missing required field: {field}")
    if evidence["status"] != "passed":
        raise ReleaseError(
            f"{kind} evidence status must be 'passed', got {evidence['status']!r} — a manifest "
            f"never converts absence/failure of evidence into release approval"
        )
    if not isinstance(evidence["command"], list) or not evidence["command"]:
        raise ReleaseError(f"{kind} evidence command must be the exact non-empty argv list")
    if not _UTC_RE.match(str(evidence["completed_at_utc"])):
        raise ReleaseError(f"{kind} evidence completed_at_utc must be a UTC timestamp")
    if not _SHA256_RE.match(str(evidence["evidence_file_sha256"])):
        raise ReleaseError(f"{kind} evidence_file_sha256 must be a hex SHA-256")


def verify_manifest(manifest: dict[str, Any], wheels: list[Path]) -> None:
    """Verify published bytes + evidence against the closed manifest. Raises ReleaseError."""

    if not isinstance(manifest, dict):
        raise ReleaseError("manifest must be a mapping")
    unknown = set(manifest) - set(_REQUIRED_TOP)
    if unknown:
        raise ReleaseError(f"manifest carries unknown fields (closed schema): {sorted(unknown)}")
    for field in _REQUIRED_TOP:
        if field not in manifest:
            raise ReleaseError(f"manifest missing required field: {field}")
    if manifest["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ReleaseError(
            f"unknown manifest schema: {manifest['manifest_schema_version']!r} "
            f"(expected {MANIFEST_SCHEMA_VERSION})"
        )
    build = manifest["build"]
    for field in _REQUIRED_BUILD:
        if field not in build or not str(build[field]).strip():
            raise ReleaseError(f"manifest build record missing/blank required field: {field}")
    for token in build["command_argv"]:
        if "<" in str(token) and ">" in str(token):
            raise ReleaseError(f"build command contains a placeholder token: {token!r}")
    _check_evidence("test", manifest["test_evidence"])
    _check_evidence("smoke", manifest["smoke_evidence"])

    by_name: dict[str, dict] = {}
    seen_packages: set[str] = set()
    for entry in manifest["artifacts"]:
        for field in _REQUIRED_ARTIFACT:
            if field not in entry:
                raise ReleaseError(f"artifact entry missing required field: {field}")
        if entry["package"] in seen_packages:
            raise ReleaseError(f"duplicate package identity in manifest: {entry['package']}")
        seen_packages.add(entry["package"])
        if entry["filename"] in by_name:
            raise ReleaseError(f"duplicate filename in manifest: {entry['filename']}")
        if not _SHA256_RE.match(str(entry["sha256"])):
            raise ReleaseError(f"{entry['filename']}: sha256 must be a hex SHA-256")
        by_name[entry["filename"]] = entry

    for wheel in wheels:
        entry = by_name.get(wheel.name)
        if entry is None:
            raise ReleaseError(f"wheel not in manifest: {wheel.name}")
        if not entry["published"]:
            raise ReleaseError(
                f"{wheel.name}: artifact is not marked published — unpublished entries are "
                f"never consumable"
            )
        identity = inspect_wheel(wheel)
        if identity["package"] != entry["package"] or identity["version"] != entry["version"]:
            raise ReleaseError(
                f"{wheel.name}: embedded identity {identity} disagrees with manifest entry"
            )
        actual_size = wheel.stat().st_size
        if actual_size != entry["size_bytes"]:
            raise ReleaseError(
                f"{wheel.name}: size mismatch (manifest {entry['size_bytes']}, actual {actual_size})"
            )
        actual_sha = _sha256(wheel)
        if actual_sha != entry["sha256"]:
            raise ReleaseError(
                f"{wheel.name}: SHA-256 mismatch — bytes are NOT the published artifact\n"
                f"  manifest: {entry['sha256']}\n  actual:   {actual_sha}"
            )


# ------------------------------------------------------------------ SHA256SUMS (M9)


def write_sha256sums(paths: list[Path], out: Path) -> Path:
    lines = [f"{_sha256(path)}  {path.name}" for path in sorted(paths, key=lambda p: p.name)]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def verify_sha256sums(sums_path: Path, directory: Path) -> None:
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            digest, filename = line.split(None, 1)
        except ValueError as exc:
            raise ReleaseError(f"malformed SHA256SUMS line: {line!r}") from exc
        target = directory / filename.strip()
        if not target.exists():
            raise ReleaseError(f"SHA256SUMS claims a missing file: {filename.strip()}")
        actual = _sha256(target)
        if actual != digest:
            raise ReleaseError(
                f"{filename.strip()}: checksum mismatch — refuse to install\n"
                f"  expected: {digest}\n  actual:   {actual}"
            )


# ------------------------------------------------------------------ CLI


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release_artifacts")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--repo", default=".")
    build.add_argument("--tag", required=True)
    build.add_argument("--out", required=True)
    build.add_argument("--published", default="ai-workflow-engine")
    build.add_argument("--uri-prefix", default="")
    build.add_argument("--test-evidence", help="JSON file with the passed test evidence record")
    build.add_argument("--smoke-evidence", help="JSON file with the passed smoke evidence record")
    build.add_argument("wheels", nargs="+")
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("wheels", nargs="+")
    sums = sub.add_parser("sums")
    sums.add_argument("--out", required=True)
    sums.add_argument("files", nargs="+")
    checksums = sub.add_parser("check-sums")
    checksums.add_argument("--sums", required=True)
    checksums.add_argument("--dir", required=True)
    args = parser.parse_args(argv)

    if args.command == "build":
        evidence = {}
        for kind in ("test", "smoke"):
            source = getattr(args, f"{kind}_evidence")
            evidence[kind] = json.loads(Path(source).read_text()) if source else None
        manifest = build_release_manifest(
            [Path(w) for w in args.wheels],
            repo=Path(args.repo),
            tag=args.tag,
            published=set(args.published.split(",")),
            uri_prefix=args.uri_prefix,
            test_evidence=evidence["test"],
            smoke_evidence=evidence["smoke"],
        )
        Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"manifest written: {args.out}")
        return 0
    if args.command == "verify":
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        verify_manifest(manifest, [Path(w) for w in args.wheels])
        print("manifest verification PASSED")
        return 0
    if args.command == "sums":
        write_sha256sums([Path(f) for f in args.files], Path(args.out))
        print(f"SHA256SUMS written: {args.out}")
        return 0
    verify_sha256sums(Path(args.sums), Path(args.dir))
    print("checksum verification PASSED")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
