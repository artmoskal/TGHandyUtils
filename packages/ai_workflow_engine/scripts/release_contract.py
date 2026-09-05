"""Strict release artifact contract for the latest engine line.

This module is producer/consumer tooling, not runtime engine code. It uses only
the Python standard library so a consumer can verify a downloaded release bundle
before installing any project wheel.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlsplit

from release_toolchain import (
    EXPECTED_PACKAGES,
    GIT_ID_RE as _GIT_ID_RE,
    RELEASE_BUILD_BACKEND,
    RELEASE_BUILD_FRONTEND,
    RELEASE_BUILD_TOOLS,
    RELEASE_WHEEL_GENERATOR,
    VERSION_RE as _VERSION_RE,
    ReleaseError,
    candidate_source,
    canonical_package,
    git_output as _git,
)

MANIFEST_SCHEMA_VERSION = "release-manifest-v2"
VERIFIER_FILENAMES = {
    "release_artifacts.py",
    "release_contract.py",
    "release_toolchain.py",
}
MAX_CONTROL_MEMBER_BYTES = 1 << 20
MAX_JSON_BYTES = 1 << 20
MAX_CHECKSUM_BYTES = 1 << 20
MAX_WHEEL_BYTES = 256 << 20
MAX_WHEEL_MEMBER_BYTES = 128 << 20
MAX_WHEEL_TOTAL_BYTES = 512 << 20
MAX_WHEEL_MEMBERS = 10_000

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TAG_RE = re.compile(r"^engine-v([0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+_-]*)?)$")

_TOP_KEYS = {
    "manifest_schema_version",
    "tag",
    "tag_object_id",
    "source_commit",
    "matrix",
    "build",
    "reproducibility",
    "test_evidence",
    "smoke_evidence",
    "artifacts",
    "verification_tools",
}
_GATE_RECORD_KEYS = {
    "name",
    "command_argv",
    "status",
    "started_at_utc",
    "completed_at_utc",
    "exit_code",
    "working_directory",
    "source_commit",
    "log_filename",
    "log_size_bytes",
    "log_total_bytes",
    "log_truncated",
    "log_sha256",
}
_BUILD_RECORD_KEYS = _GATE_RECORD_KEYS | {
    "python_implementation",
    "python_version",
    "frontend",
    "backend",
    "tool_versions",
    "source_date_epoch",
    "umask",
    "built_artifacts",
}
_EMBEDDED_EVIDENCE_KEYS = _GATE_RECORD_KEYS | {
    "record_filename",
    "record_size_bytes",
    "record_sha256",
}
_EMBEDDED_BUILD_KEYS = _BUILD_RECORD_KEYS | {
    "record_filename",
    "record_size_bytes",
    "record_sha256",
}
_TOOL_KEYS = {"name", "version"}
_ARTIFACT_KEYS = {
    "package",
    "version",
    "filename",
    "size_bytes",
    "sha256",
    "published",
    "uri",
}
_FILE_RECORD_KEYS = {"filename", "size_bytes", "sha256"}
_BUILT_ARTIFACT_KEYS = {
    "package",
    "version",
    "filename",
    "size_bytes",
    "sha256",
    "generator",
}
_REPRODUCIBILITY_KEYS = {"second_build"}


def _exact_mapping(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseError(f"{path} must be an object")
    actual = set(value)
    missing = keys - actual
    unknown = actual - keys
    if missing:
        raise ReleaseError(f"{path} missing required fields: {sorted(missing)}")
    if unknown:
        raise ReleaseError(f"{path} carries unknown fields: {sorted(unknown)}")
    return value


def _strict_string(value: Any, path: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value.strip():
        raise ReleaseError(f"{path} must be a nonblank string")
    if "\x00" in value:
        raise ReleaseError(f"{path} contains NUL")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ReleaseError(f"{path} has invalid format: {value!r}")
    return value


def _strict_int(value: Any, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ReleaseError(f"{path} must be an integer >= {minimum}")
    return value


def _strict_bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise ReleaseError(f"{path} must be a boolean")
    return value


def _strict_argv(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ReleaseError(f"{path} must be a nonempty argv list")
    result = []
    for index, token in enumerate(value):
        result.append(_strict_string(token, f"{path}[{index}]"))
    return result


def _strict_utc(value: Any, path: str) -> str:
    text = _strict_string(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseError(f"{path} must be a real ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ReleaseError(f"{path} must use UTC")
    return text


def _plain_filename(value: Any, path: str) -> str:
    name = _strict_string(value, path)
    if name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name:
        raise ReleaseError(f"{path} must be a plain child filename")
    return name


def _strict_sha(value: Any, path: str) -> str:
    return _strict_string(value, path, pattern=_SHA256_RE)


def _strict_version(value: Any, path: str) -> str:
    return _strict_string(value, path, pattern=_VERSION_RE)


def validate_wheel_identity_transition(
    predecessor: Any,
    candidate: Any,
) -> None:
    """Reject changed wheel bytes that reuse a predecessor package version."""

    matrices: list[dict[str, dict[str, str]]] = []
    for label, value in (("predecessor", predecessor), ("candidate", candidate)):
        matrix = _exact_mapping(value, set(EXPECTED_PACKAGES), label)
        identities: dict[str, dict[str, str]] = {}
        for package in EXPECTED_PACKAGES:
            identity = _exact_mapping(
                matrix[package],
                {"version", "sha256"},
                f"{label}.{package}",
            )
            identities[package] = {
                "version": _strict_version(
                    identity["version"], f"{label}.{package}.version"
                ),
                "sha256": _strict_sha(
                    identity["sha256"], f"{label}.{package}.sha256"
                ),
            }
        matrices.append(identities)

    previous, current = matrices
    reused = [
        package
        for package in EXPECTED_PACKAGES
        if previous[package]["version"] == current[package]["version"]
        and previous[package]["sha256"] != current[package]["sha256"]
    ]
    if reused:
        raise ReleaseError(
            "wheel bytes changed under an unchanged package version: "
            + ", ".join(reused)
        )


def _strict_uri(value: Any, path: str, *, expected_filename: str) -> str:
    uri = _strict_string(value, path)
    parsed = urlsplit(uri)
    if parsed.scheme not in {"file", "https", "s3"} or parsed.query or parsed.fragment:
        raise ReleaseError(f"{path} must be an absolute file/https/s3 URI without query or fragment")
    if not parsed.path.endswith("/" + quote(expected_filename)):
        raise ReleaseError(f"{path} must end with the encoded artifact filename")
    return uri


def _normalize_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_stream(stream: Any) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1 << 20), b""):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


@contextmanager
def _safe_regular_fd(
    directory: Path,
    filename: str,
    *,
    expected_size: int | None = None,
    max_bytes: int | None = None,
):
    name = _plain_filename(filename, "bundle filename")
    root = directory.resolve(strict=True)
    target = root / name
    try:
        before = os.lstat(target)
    except FileNotFoundError as exc:
        raise ReleaseError(f"bundle file is missing: {name}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise ReleaseError(f"bundle file must be a non-symlink regular file: {name}")
    if target.resolve(strict=True).parent != root:
        raise ReleaseError(f"bundle file escapes configured directory: {name}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        raise ReleaseError(f"refusing unsafe bundle file: {name}") from exc
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise ReleaseError(f"bundle file changed identity during verification: {name}")
        if expected_size is not None and current.st_size != expected_size:
            raise ReleaseError(
                f"{name}: size mismatch (expected {expected_size}, actual {current.st_size})"
            )
        if max_bytes is not None and current.st_size > max_bytes:
            raise ReleaseError(f"{name}: file exceeds {max_bytes} bytes")
        yield fd, current
    finally:
        os.close(fd)


def _safe_regular_hash(
    directory: Path,
    filename: str,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    max_bytes: int | None = None,
) -> tuple[int, str]:
    name = _plain_filename(filename, "bundle filename")
    with _safe_regular_fd(
        directory,
        name,
        expected_size=expected_size,
        max_bytes=max_bytes,
    ) as (fd, _current):
        with os.fdopen(os.dup(fd), "rb") as handle:
            size, digest = _hash_stream(handle)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ReleaseError(
            f"{name}: SHA-256 mismatch (expected {expected_sha256}, actual {digest})"
        )
    return size, digest


def _safe_read_bytes(
    directory: Path,
    filename: str,
    *,
    max_bytes: int,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> bytes:
    name = _plain_filename(filename, "bundle filename")
    with _safe_regular_fd(
        directory,
        name,
        expected_size=expected_size,
        max_bytes=max_bytes,
    ) as (fd, current):
        data = bytearray()
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, min(1 << 20, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            digest.update(chunk)
            if len(data) > max_bytes:
                raise ReleaseError(f"{name}: file exceeds {max_bytes} bytes")
        if len(data) != current.st_size:
            raise ReleaseError(f"{name}: file changed size during verification")
    if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
        raise ReleaseError(
            f"{name}: SHA-256 mismatch "
            f"(expected {expected_sha256}, actual {digest.hexdigest()})"
        )
    return bytes(data)


def hash_path(path: Path) -> tuple[int, str]:
    return _safe_regular_hash(path.parent, path.name)


def copy_regular_file(source: Path, destination: Path) -> tuple[int, str]:
    """Copy one non-symlink regular file without reopening the validated source path."""

    name = _plain_filename(destination.name, "destination filename")
    destination_parent = destination.parent.resolve(strict=True)
    destination = destination_parent / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    destination_fd: int | None = None
    try:
        with _safe_regular_fd(source.parent, source.name) as (source_fd, source_stat):
            destination_fd = os.open(destination, flags, 0o644)
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(source_fd, 1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
            os.fsync(destination_fd)
            if size != source_stat.st_size:
                raise ReleaseError(f"{source.name}: source changed size during staging")
            return size, digest.hexdigest()
    except Exception:
        if destination_fd is not None:
            os.close(destination_fd)
            destination_fd = None
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        if destination_fd is not None:
            os.close(destination_fd)


def _safe_json(
    directory: Path,
    filename: str,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        value = json.loads(
            _safe_read_bytes(
                directory,
                filename,
                max_bytes=MAX_JSON_BYTES,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"{filename}: invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"{filename}: top-level JSON must be an object")
    return value


def _git_bytes(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip() or str(exc)
        raise ReleaseError(f"git {' '.join(args)} failed: {detail}") from exc


def release_tag_annotation(
    tag: str,
    source_commit: str,
    matrix: Mapping[str, str],
) -> str:
    """Return the one source-only annotation accepted for a release tag."""

    tag = _strict_string(tag, "tag")
    source_commit = _strict_string(source_commit, "source_commit")
    if _TAG_RE.fullmatch(tag) is None:
        raise ReleaseError("tag must have exact engine-vX.Y.Z form")
    if _GIT_ID_RE.fullmatch(source_commit) is None:
        raise ReleaseError("source_commit must be a full Git object id")
    if set(matrix) != set(EXPECTED_PACKAGES):
        raise ReleaseError("tag annotation matrix must name exactly the release packages")
    versions = {
        package: _strict_version(matrix[package], f"matrix.{package}")
        for package in EXPECTED_PACKAGES
    }
    return "\n\n".join(
        (
            tag,
            f"Source: {source_commit}",
            (
                f"Matrix: ai-workflow-engine {versions['ai-workflow-engine']} / "
                f"ai-workflow-tools {versions['ai-workflow-tools']} / "
                f"ai-workflow-viewer {versions['ai-workflow-viewer']}"
            ),
            (
                "Artifact bytes and gate evidence are identified only by the "
                "verified release directory."
            ),
        )
    )


def tagged_source(repo: Path, tag: str) -> dict[str, Any]:
    match = _TAG_RE.fullmatch(_strict_string(tag, "tag"))
    if match is None:
        raise ReleaseError("tag must have exact engine-vX.Y.Z form")
    if _git(repo, "cat-file", "-t", tag) != "tag":
        raise ReleaseError(f"{tag}: release tag must be annotated")
    tag_object_id = _git(repo, "rev-parse", tag)
    source_commit = _git(repo, "rev-parse", f"{tag}^{{}}")
    if not _GIT_ID_RE.fullmatch(tag_object_id) or not _GIT_ID_RE.fullmatch(source_commit):
        raise ReleaseError("tag/source ids must be full Git object ids")
    source = candidate_source(repo, source_commit)
    matrix = source["matrix"]
    if matrix["ai-workflow-engine"] != match.group(1):
        raise ReleaseError("tag suffix does not match tagged engine package version")
    annotation = _git(
        repo,
        "for-each-ref",
        "--format=%(contents)",
        f"refs/tags/{tag}",
    )
    expected_annotation = release_tag_annotation(tag, source_commit, matrix)
    if annotation != expected_annotation:
        raise ReleaseError(
            f"{tag}: annotation must contain source identity only and exactly match "
            "the tagged package matrix; build, test, smoke, and wheel evidence belong "
            "only in the verified release directory"
        )
    return {
        **source,
        "tag": tag,
        "tag_object_id": tag_object_id,
    }


def _safe_zip_member(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReleaseError(f"wheel contains unsafe archive member: {name!r}")


def _read_control(zf: zipfile.ZipFile, info: zipfile.ZipInfo, label: str) -> bytes:
    if info.file_size > MAX_CONTROL_MEMBER_BYTES:
        raise ReleaseError(f"{label} exceeds {MAX_CONTROL_MEMBER_BYTES} bytes")
    with zf.open(info, "r") as handle:
        data = handle.read(MAX_CONTROL_MEMBER_BYTES + 1)
    if len(data) > MAX_CONTROL_MEMBER_BYTES:
        raise ReleaseError(f"{label} exceeds {MAX_CONTROL_MEMBER_BYTES} bytes")
    return data


def _wheel_filename(path: Path) -> dict[str, str]:
    name = _plain_filename(path.name, "wheel filename")
    if not name.endswith(".whl"):
        raise ReleaseError(f"{name}: wheel filename must end in .whl")
    parts = name[:-4].split("-")
    if len(parts) not in {5, 6}:
        raise ReleaseError(f"{name}: unsupported wheel filename shape")
    distribution, version = parts[0], parts[1]
    python_tag, abi_tag, platform_tag = parts[-3:]
    if not all((distribution, version, python_tag, abi_tag, platform_tag)):
        raise ReleaseError(f"{name}: wheel filename has blank identity/tag fields")
    return {
        "distribution": distribution,
        "version": version,
        "tag": f"{python_tag}-{abi_tag}-{platform_tag}",
    }


def _wheel_generator(wheel_meta: Any, path: Path) -> str:
    generators = wheel_meta.get_all("Generator", [])
    if len(generators) != 1:
        raise ReleaseError(f"{path.name}: WHEEL requires exactly one Generator")
    return _strict_string(generators[0], "WHEEL.Generator")


def inspect_wheel(path: Path) -> dict[str, Any]:
    """Validate wheel identity, controls, RECORD, and member hashes."""

    filename = _wheel_filename(path)
    try:
        with _safe_regular_fd(
            path.parent,
            path.name,
            max_bytes=MAX_WHEEL_BYTES,
        ) as (fd, wheel_stat):
            with os.fdopen(os.dup(fd), "rb") as wheel_stream:
                if not zipfile.is_zipfile(wheel_stream):
                    raise ReleaseError(f"{path.name}: bytes are not a ZIP wheel")
                wheel_stream.seek(0)
                with zipfile.ZipFile(wheel_stream) as zf:
                    result = _inspect_open_wheel(zf, path, filename)
            os.lseek(fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(fd), "rb") as wheel_stream:
                size, sha256 = _hash_stream(wheel_stream)
            if size != wheel_stat.st_size:
                raise ReleaseError(f"{path.name}: wheel changed size during inspection")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        raise ReleaseError(f"{path.name}: invalid wheel ZIP") from exc
    return {
        **result,
        "filename": path.name,
        "size_bytes": size,
        "sha256": sha256,
    }


def _inspect_open_wheel(
    zf: zipfile.ZipFile,
    path: Path,
    filename: dict[str, str],
) -> dict[str, str]:
    infos = zf.infolist()
    if not infos or len(infos) > MAX_WHEEL_MEMBERS:
        raise ReleaseError(
            f"{path.name}: wheel member count must be within 1..{MAX_WHEEL_MEMBERS}"
        )
    total_uncompressed = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ReleaseError(f"{path.name}: encrypted wheel members are unsupported")
        if info.file_size > MAX_WHEEL_MEMBER_BYTES:
            raise ReleaseError(
                f"{path.name}: member {info.filename!r} exceeds "
                f"{MAX_WHEEL_MEMBER_BYTES} bytes"
            )
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_WHEEL_TOTAL_BYTES:
            raise ReleaseError(
                f"{path.name}: total uncompressed wheel size exceeds "
                f"{MAX_WHEEL_TOTAL_BYTES} bytes"
            )
    names = [info.filename for info in infos]
    for name in names:
        _safe_zip_member(name)
    if len(names) != len(set(names)):
        raise ReleaseError(f"{path.name}: wheel contains duplicate archive members")
    dist_infos = {
        PurePosixPath(name).parts[0]
        for name in names
        if len(PurePosixPath(name).parts) > 1
        and PurePosixPath(name).parts[0].endswith(".dist-info")
    }
    if len(dist_infos) != 1:
        raise ReleaseError(f"{path.name}: wheel must contain exactly one dist-info owner")
    dist_info = next(iter(dist_infos))
    info_by_name = {info.filename: info for info in infos}
    control_names = {
        label: f"{dist_info}/{label}" for label in ("METADATA", "WHEEL", "RECORD")
    }
    for label, member in control_names.items():
        if member not in info_by_name:
            raise ReleaseError(f"{path.name}: wheel is missing {member}")
    metadata_bytes = _read_control(
        zf, info_by_name[control_names["METADATA"]], "METADATA"
    )
    wheel_bytes = _read_control(zf, info_by_name[control_names["WHEEL"]], "WHEEL")
    record_bytes = _read_control(zf, info_by_name[control_names["RECORD"]], "RECORD")
    try:
        metadata = Parser().parsestr(metadata_bytes.decode("utf-8", errors="strict"))
        wheel_meta = Parser().parsestr(wheel_bytes.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise ReleaseError(f"{path.name}: wheel controls must be UTF-8") from exc
    names_found = metadata.get_all("Name", [])
    versions_found = metadata.get_all("Version", [])
    if len(names_found) != 1 or len(versions_found) != 1:
        raise ReleaseError(f"{path.name}: METADATA requires exactly one Name and Version")
    package = canonical_package(names_found[0])
    version = _strict_version(versions_found[0], "METADATA.Version")
    if canonical_package(filename["distribution"]) != package:
        raise ReleaseError(f"{path.name}: filename and METADATA package disagree")
    if filename["version"] != version:
        raise ReleaseError(f"{path.name}: filename and METADATA version disagree")
    expected_dist_info = f"{filename['distribution']}-{version}.dist-info"
    if dist_info != expected_dist_info:
        raise ReleaseError(
            f"{path.name}: dist-info owner {dist_info!r} disagrees with wheel identity"
        )
    wheel_versions = wheel_meta.get_all("Wheel-Version", [])
    pure_flags = wheel_meta.get_all("Root-Is-Purelib", [])
    wheel_tags = wheel_meta.get_all("Tag", [])
    if len(wheel_versions) != 1 or not wheel_versions[0].startswith("1."):
        raise ReleaseError(f"{path.name}: unsupported or missing Wheel-Version")
    generator = _wheel_generator(wheel_meta, path)
    if len(pure_flags) != 1 or pure_flags[0].lower() not in {"true", "false"}:
        raise ReleaseError(f"{path.name}: invalid Root-Is-Purelib")
    if not wheel_tags or filename["tag"] not in wheel_tags:
        raise ReleaseError(f"{path.name}: WHEEL Tag disagrees with filename tags")
    try:
        record_rows = list(
            csv.reader(io.StringIO(record_bytes.decode("utf-8", errors="strict")))
        )
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ReleaseError(f"{path.name}: invalid RECORD CSV") from exc
    record_map: dict[str, tuple[str, str]] = {}
    for row_index, row in enumerate(record_rows):
        if len(row) != 3 or not row[0]:
            raise ReleaseError(f"{path.name}: invalid RECORD row {row_index + 1}")
        _safe_zip_member(row[0])
        if row[0] in record_map:
            raise ReleaseError(f"{path.name}: duplicate RECORD entry {row[0]!r}")
        record_map[row[0]] = (row[1], row[2])
    if set(record_map) != set(names):
        raise ReleaseError(f"{path.name}: RECORD member set disagrees with archive")
    record_name = control_names["RECORD"]
    if record_map[record_name] != ("", ""):
        raise ReleaseError(f"{path.name}: RECORD self-entry must have blank hash and size")
    for member_name, info in info_by_name.items():
        if member_name == record_name:
            continue
        digest_field, size_field = record_map[member_name]
        if not digest_field.startswith("sha256="):
            raise ReleaseError(f"{path.name}: RECORD requires sha256 for {member_name}")
        if re.fullmatch(r"[0-9]+", size_field) is None:
            raise ReleaseError(
                f"{path.name}: RECORD size is not a non-negative integer for {member_name}"
            )
        expected_size = int(size_field)
        if expected_size != info.file_size:
            raise ReleaseError(f"{path.name}: RECORD size mismatch for {member_name}")
        with zf.open(info, "r") as handle:
            actual_size, hex_digest = _hash_stream(handle)
        encoded = base64.urlsafe_b64encode(bytes.fromhex(hex_digest)).rstrip(b"=").decode()
        if actual_size != info.file_size or digest_field != f"sha256={encoded}":
            raise ReleaseError(f"{path.name}: RECORD digest mismatch for {member_name}")
    return {
        "package": package,
        "version": version,
        "generator": generator,
    }


def _validate_tool(value: Any, path: str) -> dict[str, str]:
    record = _exact_mapping(value, _TOOL_KEYS, path)
    return {
        "name": _strict_string(record["name"], f"{path}.name"),
        "version": _strict_version(record["version"], f"{path}.version"),
    }


def _validate_release_gate_commands(
    test: Mapping[str, Any],
    smoke: Mapping[str, Any],
    artifacts: Iterable[Mapping[str, Any]],
) -> None:
    if test["command_argv"] != ["./test.sh", "unit"]:
        raise ReleaseError("test evidence command must be exactly ./test.sh unit")
    _validate_smoke_command(smoke, artifacts)


def _validate_smoke_command(
    smoke: Mapping[str, Any],
    artifacts: Iterable[Mapping[str, Any]],
) -> list[str]:

    argv = smoke["command_argv"]
    invocation_error = (
        "smoke evidence must invoke release_artifacts.py smoke-installed with only "
        "--venv-dir, --work-dir, and --wheel arguments"
    )
    if len(argv) < 3:
        raise ReleaseError(invocation_error)
    # argv[0] is deliberately unconstrained. A basename such as `python3` is not proof of
    # interpreter identity — any executable may carry that name — while real published records
    # (v0.11.12, v0.11.13) ran under a conda prefix named `python`. Proof lives in the script,
    # the subcommand, the closed option grammar, and producer-side wheel byte inspection.
    if Path(argv[1]).name != "release_artifacts.py" or argv[2] != "smoke-installed":
        raise ReleaseError(invocation_error)

    singleton_options: dict[str, str] = {}
    wheel_paths: list[str] = []
    index = 3
    while index < len(argv):
        option = argv[index]
        if option not in {"--venv-dir", "--work-dir", "--wheel"} or index + 1 >= len(argv):
            raise ReleaseError(invocation_error)
        value = argv[index + 1]
        if option == "--wheel":
            wheel_paths.append(value)
        elif option in singleton_options:
            raise ReleaseError(invocation_error)
        else:
            singleton_options[option] = value
        index += 2
    if set(singleton_options) != {"--venv-dir", "--work-dir"}:
        raise ReleaseError(invocation_error)

    expected_filenames = {str(item["filename"]) for item in artifacts}
    actual_filenames = [Path(path).name for path in wheel_paths]
    if len(actual_filenames) != len(expected_filenames) or set(actual_filenames) != expected_filenames:
        raise ReleaseError(
            "smoke evidence --wheel basenames must equal the manifest artifact filenames"
        )
    return wheel_paths


def _validate_smoked_wheel_bytes(
    smoke: Mapping[str, Any],
    artifacts: Iterable[Mapping[str, Any]],
) -> None:
    material = list(artifacts)
    recorded_paths = _validate_smoke_command(smoke, material)
    cwd = Path(str(smoke["working_directory"]))
    inspected: list[dict[str, Any]] = []
    try:
        for value in recorded_paths:
            path = Path(value)
            if not path.is_absolute():
                path = cwd / path
            inspected.append(inspect_wheel(path.resolve(strict=True)))
    except (OSError, ReleaseError) as exc:
        raise ReleaseError(f"smoke evidence wheel path is unavailable or invalid: {exc}") from exc

    expected = {str(item["package"]): item for item in material}
    actual = {str(item["package"]): item for item in inspected}
    if set(actual) != set(expected) or len(actual) != len(inspected):
        raise ReleaseError("smoke evidence wheel bytes disagree with release artifacts")
    for package, artifact in expected.items():
        for field in ("package", "version", "filename", "size_bytes", "sha256"):
            if actual[package][field] != artifact[field]:
                raise ReleaseError(
                    f"smoke evidence wheel bytes disagree with release artifacts for "
                    f"{package}.{field}"
                )


def _validate_build_fields(record: Mapping[str, Any], path: str) -> dict[str, Any]:
    tools = record["tool_versions"]
    if not isinstance(tools, list) or not tools:
        raise ReleaseError(f"{path}.tool_versions must be a nonempty list")
    parsed_tools = [
        _validate_tool(tool, f"{path}.tool_versions[{index}]")
        for index, tool in enumerate(tools)
    ]
    names = [tool["name"] for tool in parsed_tools]
    if len(names) != len(set(names)) or set(names) != set(RELEASE_BUILD_TOOLS):
        raise ReleaseError(
            f"{path}.tool_versions must equal the exact release build toolchain"
        )
    actual_tools = {tool["name"]: tool["version"] for tool in parsed_tools}
    if actual_tools != RELEASE_BUILD_TOOLS:
        raise ReleaseError(
            f"{path}.tool_versions disagree with the exact release build toolchain"
        )
    frontend = _validate_tool(record["frontend"], f"{path}.frontend")
    if frontend != RELEASE_BUILD_FRONTEND:
        raise ReleaseError(f"{path}.frontend must equal the release-owned build frontend")
    backend = _validate_tool(record["backend"], f"{path}.backend")
    if backend != {
        "name": RELEASE_BUILD_BACKEND,
        "version": RELEASE_BUILD_TOOLS["setuptools"],
    }:
        raise ReleaseError(f"{path}.backend must equal the pinned package backend")
    built_raw = record["built_artifacts"]
    if not isinstance(built_raw, list):
        raise ReleaseError(f"{path}.built_artifacts must be a list")
    built_artifacts = [
        _validate_built_artifact(item, index) for index, item in enumerate(built_raw)
    ]
    built_by_package = {item["package"]: item for item in built_artifacts}
    if (
        len(built_by_package) != len(built_artifacts)
        or set(built_by_package) != set(EXPECTED_PACKAGES)
    ):
        raise ReleaseError(
            f"{path}.built_artifacts must contain the exact three-package matrix"
        )
    wrong_generators = {
        package: item["generator"]
        for package, item in built_by_package.items()
        if item["generator"] != RELEASE_WHEEL_GENERATOR
    }
    if wrong_generators:
        raise ReleaseError(
            f"{path}.built_artifacts disagree with the pinned wheel generator: "
            f"{wrong_generators}"
        )
    built_names = [item["filename"] for item in built_artifacts]
    if len(built_names) != len(set(built_names)):
        raise ReleaseError(f"{path}.built_artifacts filenames must be unique")
    umask = _strict_string(record["umask"], f"{path}.umask")
    if umask != "022":
        raise ReleaseError(f"{path}.umask must equal '022'")
    return {
        "python_implementation": _strict_string(
            record["python_implementation"], f"{path}.python_implementation"
        ),
        "python_version": _strict_string(
            record["python_version"],
            f"{path}.python_version",
            pattern=_VERSION_RE,
        ),
        "frontend": frontend,
        "backend": backend,
        "tool_versions": sorted(parsed_tools, key=lambda tool: tool["name"]),
        "source_commit": _strict_string(
            record["source_commit"], f"{path}.source_commit", pattern=_GIT_ID_RE
        ),
        "source_date_epoch": _strict_int(
            record["source_date_epoch"], f"{path}.source_date_epoch", minimum=1
        ),
        "umask": umask,
        "built_artifacts": sorted(
            built_artifacts,
            key=lambda item: item["package"],
        ),
    }


def validate_gate_record(
    value: Any,
    *,
    expected_name: str,
    build: bool = False,
    embedded: bool = False,
) -> dict[str, Any]:
    if build:
        keys = _EMBEDDED_BUILD_KEYS if embedded else _BUILD_RECORD_KEYS
    else:
        keys = _EMBEDDED_EVIDENCE_KEYS if embedded else _GATE_RECORD_KEYS
    path = f"{expected_name}_evidence"
    record = _exact_mapping(value, keys, path)
    started_at = _strict_utc(record["started_at_utc"], f"{path}.started_at_utc")
    completed_at = _strict_utc(record["completed_at_utc"], f"{path}.completed_at_utc")
    if datetime.fromisoformat(completed_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        started_at.replace("Z", "+00:00")
    ):
        raise ReleaseError(f"{path}.completed_at_utc precedes started_at_utc")
    result: dict[str, Any] = {
        "name": _strict_string(record["name"], f"{path}.name"),
        "command_argv": _strict_argv(record["command_argv"], f"{path}.command_argv"),
        "status": _strict_string(record["status"], f"{path}.status"),
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "exit_code": _strict_int(record["exit_code"], f"{path}.exit_code"),
        "working_directory": _strict_string(
            record["working_directory"], f"{path}.working_directory"
        ),
        "source_commit": _strict_string(
            record["source_commit"],
            f"{path}.source_commit",
            pattern=_GIT_ID_RE,
        ),
        "log_filename": _plain_filename(record["log_filename"], f"{path}.log_filename"),
        "log_size_bytes": _strict_int(
            record["log_size_bytes"], f"{path}.log_size_bytes"
        ),
        "log_total_bytes": _strict_int(
            record["log_total_bytes"], f"{path}.log_total_bytes"
        ),
        "log_truncated": _strict_bool(
            record["log_truncated"], f"{path}.log_truncated"
        ),
        "log_sha256": _strict_sha(record["log_sha256"], f"{path}.log_sha256"),
    }
    if result["name"] != expected_name:
        raise ReleaseError(f"{path}.name must equal {expected_name!r}")
    if result["status"] != "passed" or result["exit_code"] != 0:
        raise ReleaseError(f"{path} must record a successful command")
    if not Path(result["working_directory"]).is_absolute():
        raise ReleaseError(f"{path}.working_directory must be absolute")
    if result["log_total_bytes"] < result["log_size_bytes"]:
        raise ReleaseError(f"{path}.log_total_bytes cannot be smaller than retained log bytes")
    if result["log_truncated"] != (
        result["log_total_bytes"] > result["log_size_bytes"]
    ):
        raise ReleaseError(f"{path}.log_truncated disagrees with retained/total byte counts")
    if embedded:
        result.update(
            {
                "record_filename": _plain_filename(
                    record["record_filename"], f"{path}.record_filename"
                ),
                "record_size_bytes": _strict_int(
                    record["record_size_bytes"], f"{path}.record_size_bytes"
                ),
                "record_sha256": _strict_sha(
                    record["record_sha256"], f"{path}.record_sha256"
                ),
            }
        )
    if build:
        result.update(_validate_build_fields(record, path))
    return result


def _validate_artifact(value: Any, index: int) -> dict[str, Any]:
    path = f"artifacts[{index}]"
    record = _exact_mapping(value, _ARTIFACT_KEYS, path)
    filename = _plain_filename(record["filename"], f"{path}.filename")
    published = _strict_bool(record["published"], f"{path}.published")
    if not published:
        raise ReleaseError(f"{path}.published must be true for a release bundle")
    return {
        "package": canonical_package(_strict_string(record["package"], f"{path}.package")),
        "version": _strict_version(record["version"], f"{path}.version"),
        "filename": filename,
        "size_bytes": _strict_int(record["size_bytes"], f"{path}.size_bytes", minimum=1),
        "sha256": _strict_sha(record["sha256"], f"{path}.sha256"),
        "published": published,
        "uri": _strict_uri(record["uri"], f"{path}.uri", expected_filename=filename),
    }


def _validate_file_record(value: Any, path: str) -> dict[str, Any]:
    record = _exact_mapping(value, _FILE_RECORD_KEYS, path)
    return {
        "filename": _plain_filename(record["filename"], f"{path}.filename"),
        "size_bytes": _strict_int(record["size_bytes"], f"{path}.size_bytes", minimum=1),
        "sha256": _strict_sha(record["sha256"], f"{path}.sha256"),
    }


def _validate_built_artifact(value: Any, index: int) -> dict[str, Any]:
    path = f"build_evidence.built_artifacts[{index}]"
    record = _exact_mapping(value, _BUILT_ARTIFACT_KEYS, path)
    return {
        "package": canonical_package(
            _strict_string(record["package"], f"{path}.package")
        ),
        "version": _strict_version(record["version"], f"{path}.version"),
        "filename": _plain_filename(record["filename"], f"{path}.filename"),
        "size_bytes": _strict_int(
            record["size_bytes"], f"{path}.size_bytes", minimum=1
        ),
        "sha256": _strict_sha(record["sha256"], f"{path}.sha256"),
        "generator": _strict_string(record["generator"], f"{path}.generator"),
    }


def parse_manifest(value: Any) -> dict[str, Any]:
    manifest = _exact_mapping(value, _TOP_KEYS, "manifest")
    if manifest["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ReleaseError(
            f"manifest.manifest_schema_version must equal {MANIFEST_SCHEMA_VERSION!r}"
        )
    tag = _strict_string(manifest["tag"], "manifest.tag")
    match = _TAG_RE.fullmatch(tag)
    if match is None:
        raise ReleaseError("manifest.tag must have exact engine-vX.Y.Z form")
    tag_object_id = _strict_string(
        manifest["tag_object_id"], "manifest.tag_object_id", pattern=_GIT_ID_RE
    )
    source_commit = _strict_string(
        manifest["source_commit"], "manifest.source_commit", pattern=_GIT_ID_RE
    )
    matrix = _exact_mapping(manifest["matrix"], set(EXPECTED_PACKAGES), "manifest.matrix")
    parsed_matrix = {
        package: _strict_version(matrix[package], f"manifest.matrix.{package}")
        for package in EXPECTED_PACKAGES
    }
    if parsed_matrix["ai-workflow-engine"] != match.group(1):
        raise ReleaseError("manifest tag suffix and engine matrix version disagree")
    build = validate_gate_record(manifest["build"], expected_name="build", build=True, embedded=True)
    reproducibility = _exact_mapping(
        manifest["reproducibility"],
        _REPRODUCIBILITY_KEYS,
        "manifest.reproducibility",
    )
    second_build = validate_gate_record(
        reproducibility["second_build"],
        expected_name="build",
        build=True,
        embedded=True,
    )
    test = validate_gate_record(
        manifest["test_evidence"], expected_name="test", embedded=True
    )
    smoke = validate_gate_record(
        manifest["smoke_evidence"], expected_name="smoke", embedded=True
    )
    artifacts_raw = manifest["artifacts"]
    if not isinstance(artifacts_raw, list):
        raise ReleaseError("manifest.artifacts must be a list")
    artifacts = [_validate_artifact(item, i) for i, item in enumerate(artifacts_raw)]
    by_package = {item["package"]: item for item in artifacts}
    if len(by_package) != len(artifacts) or set(by_package) != set(EXPECTED_PACKAGES):
        raise ReleaseError("manifest artifacts must contain the exact three-package matrix")
    for package, version in parsed_matrix.items():
        if by_package[package]["version"] != version:
            raise ReleaseError(f"manifest artifact version disagrees with matrix for {package}")
    _validate_release_gate_commands(test, smoke, artifacts)
    built_by_package = {item["package"]: item for item in build["built_artifacts"]}
    second_built_by_package = {
        item["package"]: item for item in second_build["built_artifacts"]
    }
    for package, artifact in by_package.items():
        built = built_by_package[package]
        for field in ("package", "version", "filename", "size_bytes", "sha256"):
            if artifact[field] != built[field]:
                raise ReleaseError(
                    f"manifest artifact disagrees with build evidence for {package}.{field}"
                )
            if built[field] != second_built_by_package[package][field]:
                raise ReleaseError(
                    f"reproducibility build disagrees for {package}.{field}"
                )
    tools_raw = manifest["verification_tools"]
    if not isinstance(tools_raw, list):
        raise ReleaseError("manifest.verification_tools must be a list")
    tools = [
        _validate_file_record(item, f"verification_tools[{i}]")
        for i, item in enumerate(tools_raw)
    ]
    if {item["filename"] for item in tools} != VERIFIER_FILENAMES:
        raise ReleaseError("manifest must carry the exact release verifier source set")
    all_names = (
        [item["filename"] for item in artifacts]
        + [item["filename"] for item in tools]
        + [
            build["record_filename"],
            build["log_filename"],
            second_build["record_filename"],
            second_build["log_filename"],
            test["record_filename"],
            test["log_filename"],
            smoke["record_filename"],
            smoke["log_filename"],
            "release-manifest.json",
        ]
    )
    if len(all_names) != len(set(all_names)):
        raise ReleaseError("manifest bundle filenames must be unique")
    if build["source_commit"] != source_commit:
        raise ReleaseError("build evidence source commit disagrees with manifest")
    if second_build["source_commit"] != source_commit:
        raise ReleaseError("second build evidence source commit disagrees with manifest")
    if second_build["source_date_epoch"] != build["source_date_epoch"]:
        raise ReleaseError("independent builds use different SOURCE_DATE_EPOCH values")
    if test["source_commit"] != source_commit:
        raise ReleaseError("test evidence source commit disagrees with manifest")
    if smoke["source_commit"] != source_commit:
        raise ReleaseError("smoke evidence source commit disagrees with manifest")
    # v0.11.11: reproducibility is only meaningful ACROSS checkouts. Reusing one checkout
    # proves the same working tree builds twice, never that the tagged source rebuilds
    # independently — and the operator discipline in operations.md ("two independent
    # detached, clean checkouts ... and a third checkout for test/smoke evidence") was
    # previously unenforced, so a single-checkout run produced a passing but false proof.
    # The gate checkout may serve both test and smoke; the two builds may share nothing.
    build_directory = build["working_directory"]
    second_directory = second_build["working_directory"]
    if build_directory == second_directory:
        raise ReleaseError(
            "wheel reproducibility must come from two independent checkouts: both build "
            f"records ran in {build_directory!r}"
        )
    for label, record in (("test", test), ("smoke", smoke)):
        gate_directory = record["working_directory"]
        if gate_directory in {build_directory, second_directory}:
            raise ReleaseError(
                f"{label} evidence must come from a checkout separate from both build "
                f"checkouts: it ran in {gate_directory!r}"
            )
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "tag": tag,
        "tag_object_id": tag_object_id,
        "source_commit": source_commit,
        "matrix": parsed_matrix,
        "build": build,
        "reproducibility": {"second_build": second_build},
        "test_evidence": test,
        "smoke_evidence": smoke,
        "artifacts": artifacts,
        "verification_tools": tools,
    }


def load_evidence_record(path: Path, expected_name: str, *, build: bool = False) -> dict[str, Any]:
    value = _safe_json(path.parent, path.name)
    validated = validate_gate_record(value, expected_name=expected_name, build=build)
    log_path = path.parent / validated["log_filename"]
    log_size, log_hash = _safe_regular_hash(
        path.parent,
        validated["log_filename"],
        expected_size=validated["log_size_bytes"],
        expected_sha256=validated["log_sha256"],
    )
    if (log_size, log_hash) != (
        validated["log_size_bytes"],
        validated["log_sha256"],
    ):
        raise ReleaseError(f"{path.name}: evidence log identity mismatch")
    record_size, record_hash = _safe_regular_hash(path.parent, path.name)
    return {
        **validated,
        "record_filename": _plain_filename(path.name, "evidence record filename"),
        "record_size_bytes": record_size,
        "record_sha256": record_hash,
    }


def _uri_base(value: str) -> str:
    uri = _strict_string(value, "uri_base")
    parsed = urlsplit(uri)
    if parsed.scheme not in {"file", "https", "s3"} or parsed.query or parsed.fragment:
        raise ReleaseError("uri_base must be an absolute file/https/s3 URI")
    if not uri.endswith("/"):
        raise ReleaseError("uri_base must end with '/'")
    return uri


def assemble_manifest(
    *,
    repo: Path,
    tag: str,
    wheels: list[Path],
    build_evidence_path: Path,
    second_wheels: list[Path],
    second_build_evidence_path: Path,
    test_evidence_path: Path,
    smoke_evidence_path: Path,
    uri_base: str,
    verifier_paths: list[Path],
) -> dict[str, Any]:
    source = tagged_source(repo, tag)
    inspected = [inspect_wheel(path) for path in wheels]
    by_package = {item["package"]: item for item in inspected}
    if len(by_package) != len(inspected) or set(by_package) != set(EXPECTED_PACKAGES):
        raise ReleaseError("release requires exactly one wheel for each package in the matrix")
    for package, version in source["matrix"].items():
        if by_package[package]["version"] != version:
            raise ReleaseError(f"wheel version disagrees with tagged source for {package}")
    build = load_evidence_record(build_evidence_path, "build", build=True)
    second_build = load_evidence_record(
        second_build_evidence_path,
        "build",
        build=True,
    )
    test = load_evidence_record(test_evidence_path, "test")
    smoke = load_evidence_record(smoke_evidence_path, "smoke")
    if build["source_commit"] != source["source_commit"]:
        raise ReleaseError("build evidence was not produced from the tagged source commit")
    if build["source_date_epoch"] != source["source_date_epoch"]:
        raise ReleaseError("build SOURCE_DATE_EPOCH disagrees with tagged source")
    if second_build["source_commit"] != source["source_commit"]:
        raise ReleaseError("second build evidence was not produced from the tagged source commit")
    if second_build["source_date_epoch"] != source["source_date_epoch"]:
        raise ReleaseError("second build SOURCE_DATE_EPOCH disagrees with tagged source")
    built_by_package = {item["package"]: item for item in build["built_artifacts"]}
    second_inspected = [inspect_wheel(path) for path in second_wheels]
    second_by_package = {item["package"]: item for item in second_inspected}
    if (
        len(second_by_package) != len(second_inspected)
        or set(second_by_package) != set(EXPECTED_PACKAGES)
    ):
        raise ReleaseError(
            "release reproducibility requires exactly one second-build wheel per package"
        )
    second_recorded = {
        item["package"]: item for item in second_build["built_artifacts"]
    }
    for package, item in by_package.items():
        built = built_by_package[package]
        for field in ("package", "version", "filename", "size_bytes", "sha256"):
            if item[field] != built[field]:
                raise ReleaseError(
                    f"supplied wheel disagrees with build evidence for {package}.{field}"
                )
            if second_by_package[package][field] != second_recorded[package][field]:
                raise ReleaseError(
                    f"second wheel disagrees with second build evidence for {package}.{field}"
                )
            if item[field] != second_by_package[package][field]:
                raise ReleaseError(
                    f"independent builds disagree for {package}.{field}"
                )
    base = _uri_base(uri_base)
    artifacts = [
        {
            **{
                key: item[key]
                for key in ("package", "version", "filename", "size_bytes", "sha256")
            },
            "published": True,
            "uri": base + quote(item["filename"]),
        }
        for item in sorted(inspected, key=lambda item: item["package"])
    ]
    _validate_smoked_wheel_bytes(smoke, artifacts)
    verifier_records = []
    for path in verifier_paths:
        if path.name not in VERIFIER_FILENAMES:
            raise ReleaseError(f"unexpected verifier source: {path.name}")
        size, sha256 = hash_path(path)
        tagged_bytes = _git_bytes(
            repo,
            "show",
            (
                f"{source['source_commit']}:"
                f"packages/ai_workflow_engine/scripts/{path.name}"
            ),
        )
        if size != len(tagged_bytes) or sha256 != hashlib.sha256(tagged_bytes).hexdigest():
            raise ReleaseError(
                f"verifier source {path.name} does not byte-match the annotated tag"
            )
        verifier_records.append(
            {"filename": path.name, "size_bytes": size, "sha256": sha256}
        )
    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "tag": source["tag"],
        "tag_object_id": source["tag_object_id"],
        "source_commit": source["source_commit"],
        "matrix": source["matrix"],
        "build": build,
        "reproducibility": {"second_build": second_build},
        "test_evidence": test,
        "smoke_evidence": smoke,
        "artifacts": artifacts,
        "verification_tools": verifier_records,
    }
    return parse_manifest(manifest)


def manifest_bundle_filenames(manifest: dict[str, Any]) -> set[str]:
    parsed = parse_manifest(manifest)
    names = {"release-manifest.json"}
    names.update(item["filename"] for item in parsed["artifacts"])
    names.update(item["filename"] for item in parsed["verification_tools"])
    for key in ("build", "test_evidence", "smoke_evidence"):
        names.add(parsed[key]["record_filename"])
        names.add(parsed[key]["log_filename"])
    second_build = parsed["reproducibility"]["second_build"]
    names.add(second_build["record_filename"])
    names.add(second_build["log_filename"])
    return names


def write_sha256sums(paths: Iterable[Path], out: Path) -> Path:
    material = list(paths)
    if not material:
        raise ReleaseError("cannot create an empty SHA256SUMS")
    names = [_plain_filename(path.name, "checksum filename") for path in material]
    if len(names) != len(set(names)):
        raise ReleaseError("SHA256SUMS input filenames must be unique")
    lines = []
    for path in sorted(material, key=lambda item: item.name):
        size, digest = _safe_regular_hash(path.parent, path.name)
        if size < 0:
            raise ReleaseError(f"invalid file size for {path.name}")
        lines.append(f"{digest}  {path.name}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def parse_sha256sums(directory: Path, filename: str = "SHA256SUMS") -> dict[str, str]:
    try:
        lines = _safe_read_bytes(
            directory,
            filename,
            max_bytes=MAX_CHECKSUM_BYTES,
        ).decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ReleaseError("SHA256SUMS must be readable UTF-8") from exc
    if not lines:
        raise ReleaseError("SHA256SUMS must not be empty")
    result: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ReleaseError(f"SHA256SUMS line {line_number} is blank")
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ReleaseError(f"malformed SHA256SUMS line {line_number}")
        filename = _plain_filename(match.group(2), f"SHA256SUMS line {line_number} filename")
        if filename in result:
            raise ReleaseError(f"duplicate SHA256SUMS claim: {filename}")
        result[filename] = match.group(1)
    return result


def _verify_embedded_evidence(
    directory: Path, embedded: dict[str, Any], expected_name: str, *, build: bool = False
) -> None:
    actual = _safe_json(
        directory,
        embedded["record_filename"],
        expected_size=embedded["record_size_bytes"],
        expected_sha256=embedded["record_sha256"],
    )
    validated = validate_gate_record(actual, expected_name=expected_name, build=build)
    expected = {key: value for key, value in embedded.items() if not key.startswith("record_")}
    if validated != expected:
        raise ReleaseError(f"{expected_name} evidence record content disagrees with manifest")
    _safe_regular_hash(
        directory,
        embedded["log_filename"],
        expected_size=embedded["log_size_bytes"],
        expected_sha256=embedded["log_sha256"],
    )


def verify_bundle(directory: Path, manifest_name: str, sums_name: str) -> dict[str, Any]:
    root = directory.resolve(strict=True)
    manifest_name = _plain_filename(manifest_name, "manifest filename")
    sums_name = _plain_filename(sums_name, "sums filename")
    sums = parse_sha256sums(root, sums_name)
    if manifest_name not in sums:
        raise ReleaseError(f"{sums_name} must cover {manifest_name}")
    manifest = parse_manifest(
        _safe_json(
            root,
            manifest_name,
            expected_sha256=sums[manifest_name],
        )
    )
    expected = manifest_bundle_filenames(manifest)
    if set(sums) != expected:
        raise ReleaseError(
            f"SHA256SUMS set disagrees with manifest bundle: "
            f"missing={sorted(expected - set(sums))}, extra={sorted(set(sums) - expected)}"
        )
    actual_children = set()
    for child in root.iterdir():
        _plain_filename(child.name, "bundle child")
        actual_children.add(child.name)
    expected_children = expected | {sums_name}
    if actual_children != expected_children:
        raise ReleaseError(
            f"bundle directory set disagrees with manifest: "
            f"missing={sorted(expected_children - actual_children)}, "
            f"extra={sorted(actual_children - expected_children)}"
        )
    for filename, expected_hash in sums.items():
        _safe_regular_hash(root, filename, expected_sha256=expected_hash)
    for item in manifest["artifacts"]:
        path = root / item["filename"]
        _safe_regular_hash(
            root,
            item["filename"],
            expected_size=item["size_bytes"],
            expected_sha256=item["sha256"],
        )
        identity = inspect_wheel(path)
        for field in ("package", "version", "filename", "size_bytes", "sha256"):
            if identity[field] != item[field]:
                raise ReleaseError(
                    f"{item['filename']}: wheel {field} disagrees with manifest"
                )
    for item in manifest["verification_tools"]:
        _safe_regular_hash(
            root,
            item["filename"],
            expected_size=item["size_bytes"],
            expected_sha256=item["sha256"],
        )
    _verify_embedded_evidence(root, manifest["build"], "build", build=True)
    _verify_embedded_evidence(
        root,
        manifest["reproducibility"]["second_build"],
        "build",
        build=True,
    )
    _verify_embedded_evidence(root, manifest["test_evidence"], "test")
    _verify_embedded_evidence(root, manifest["smoke_evidence"], "smoke")
    return manifest


__all__ = [
    "EXPECTED_PACKAGES",
    "MANIFEST_SCHEMA_VERSION",
    "ReleaseError",
    "VERIFIER_FILENAMES",
    "assemble_manifest",
    "copy_regular_file",
    "hash_path",
    "inspect_wheel",
    "load_evidence_record",
    "manifest_bundle_filenames",
    "parse_manifest",
    "parse_sha256sums",
    "tagged_source",
    "validate_gate_record",
    "validate_wheel_identity_transition",
    "verify_bundle",
    "write_sha256sums",
    "_normalize_utc_now",
]
