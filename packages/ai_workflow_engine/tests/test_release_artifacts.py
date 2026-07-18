"""R0.3 (v0.11.5 corrective) — release-artifact trust-boundary attacks (RED-first).

Targets the producer tool at its corrected owner (``scripts/release_artifacts.py``); during
the RED phase the loader falls back to the defective ``tests/release_manifest.py`` so every
attack demonstrably fails against candidate ``202e87a`` before the repair lands. The fallback
dies with the old module in R2.1.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _load_release_module():
    for candidate in (
        _PACKAGE_ROOT / "scripts" / "release_artifacts.py",
        _PACKAGE_ROOT / "tests" / "release_manifest.py",
    ):
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("release_artifacts_under_test", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise AssertionError("no release tool found at either owner")


def _real_wheel(
    directory: Path,
    *,
    package: str = "ai_workflow_engine",
    version: str = "0.11.5",
    filename: str | None = None,
) -> Path:
    """A REAL minimal wheel: valid zip with coherent dist-info METADATA/WHEEL/RECORD."""

    name = filename or f"{package}-{version}-py3-none-any.whl"
    path = directory / name
    dist_info = f"{package}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {package.replace('_', '-')}\nVersion: {version}\n"
    wheel_meta = "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    module_body = "__version__ = " + repr(version) + "\n"

    def _record_line(arcname: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        return f"{arcname},sha256={digest},{len(data)}"

    files = {
        f"{package}/__init__.py": module_body.encode(),
        f"{dist_info}/METADATA": metadata.encode(),
        f"{dist_info}/WHEEL": wheel_meta.encode(),
    }
    record_lines = [_record_line(arc, data) for arc, data in files.items()]
    record_lines.append(f"{dist_info}/RECORD,,")
    files[f"{dist_info}/RECORD"] = ("\n".join(record_lines) + "\n").encode()
    with zipfile.ZipFile(path, "w") as zf:
        for arcname, data in files.items():
            zf.writestr(arcname, data)
    return path


def _scratch_tag_repo(tmp_path: Path, *, tag: str, annotated: bool = True) -> tuple[Path, str]:
    repo = tmp_path / "scratch-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "x.txt").write_text("x")
    env_args = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", *env_args, "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", *env_args, "-C", str(repo), "commit", "-qm", "c"], check=True)
    if annotated:
        subprocess.run(["git", *env_args, "-C", str(repo), "tag", "-a", tag, "-m", tag], check=True)
    else:
        subprocess.run(["git", *env_args, "-C", str(repo), "tag", tag], check=True)
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{tag}^{{}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, commit


def _expect_named_rejection(callable_, *args, fragment: str, **kwargs):
    try:
        callable_(*args, **kwargs)
    except AssertionError:
        raise
    except Exception as exc:  # the rejection must NAME the violated rule — no masking
        assert fragment.lower() in str(exc).lower(), (
            f"rejection must name {fragment!r}; got {exc!r} — a different guard masked it"
        )
        return
    raise AssertionError(f"must reject ({fragment}) — accepted instead")


# ---------------------------------------------------------------- M1/M2: wheel identity


def test_fake_bytes_with_whl_suffix_are_rejected(tmp_path):
    """M1 (CR-1 live attack): arbitrary bytes with a .whl name must never be certifiable."""

    mod = _load_release_module()
    fake = tmp_path / "ai_workflow_engine-0.11.5-py3-none-any.whl"
    fake.write_bytes(b"not a wheel")  # the exact 11-byte fake
    _expect_named_rejection(
        mod.build_manifest,
        [fake],
        tag="engine-v0.11.5",
        tag_object_id="t" * 40,
        source_commit="c" * 40,
        build_command="x",
        source_date_epoch="1",
        fragment="wheel",
    )


def test_embedded_metadata_must_agree_with_filename(tmp_path):
    """M2: embedded METADATA Name/Version is authoritative; filename disagreement rejects."""

    mod = _load_release_module()
    lying = _real_wheel(
        tmp_path, package="ai_workflow_engine", version="9.9.9",
        filename="ai_workflow_engine-0.11.5-py3-none-any.whl",
    )
    _expect_named_rejection(
        mod.build_manifest,
        [lying],
        tag="engine-v0.11.5",
        tag_object_id="t" * 40,
        source_commit="c" * 40,
        build_command="x",
        source_date_epoch="1",
        fragment="version",
    )


# ---------------------------------------------------------------- M3: tag binding


def test_engine_wheel_version_must_match_tag_and_tag_must_be_annotated(tmp_path):
    """M3: engine 0.11.5 wheel under engine-v0.11.4 rejects; lightweight tags reject."""

    mod = _load_release_module()
    wheel = _real_wheel(tmp_path)

    repo_wrong, commit_wrong = _scratch_tag_repo(tmp_path, tag="engine-v0.11.4")
    _expect_named_rejection(
        mod.build_release_manifest,
        [wheel],
        repo=repo_wrong,
        tag="engine-v0.11.4",
        fragment="tag",
    )

    light_dir = tmp_path / "light"
    light_dir.mkdir()
    repo_light, _ = _scratch_tag_repo(light_dir, tag="engine-v0.11.5", annotated=False)
    _expect_named_rejection(
        mod.build_release_manifest,
        [wheel],
        repo=repo_light,
        tag="engine-v0.11.5",
        fragment="annotated",
    )


# ---------------------------------------------------------------- M4-M6: matrix + evidence


def test_manifest_requires_coherent_matrix_without_duplicates(tmp_path):
    """M4: duplicate package identities and incomplete matrices reject."""

    mod = _load_release_module()
    first = _real_wheel(tmp_path)
    dup_dir = tmp_path / "dup"
    dup_dir.mkdir()
    duplicate = _real_wheel(dup_dir)
    _expect_named_rejection(
        mod.build_manifest,
        [first, duplicate],
        tag="engine-v0.11.5",
        tag_object_id="t" * 40,
        source_commit="c" * 40,
        build_command="x",
        source_date_epoch="1",
        fragment="duplicate",
    )


def test_manifest_requires_passed_gate_evidence(tmp_path):
    """M6 (CR-2): 'not-run'/failed/missing test+smoke evidence is never publishable."""

    mod = _load_release_module()
    wheel = _real_wheel(tmp_path)
    manifest = mod.build_manifest(
        [wheel],
        tag="engine-v0.11.5",
        tag_object_id="t" * 40,
        source_commit="c" * 40,
        build_command="x",
        source_date_epoch="1",
    )
    _expect_named_rejection(mod.verify_manifest, manifest, [wheel], fragment="evidence")


def test_verification_rejects_unpublished_artifacts(tmp_path):
    """M8: an artifact not marked published==true must not verify as consumable."""

    mod = _load_release_module()
    wheel = _real_wheel(tmp_path, package="ai_workflow_tools", version="0.5.1")
    manifest = mod.build_manifest(
        [wheel],
        tag="engine-v0.11.5",
        tag_object_id="t" * 40,
        source_commit="c" * 40,
        build_command="x",
        source_date_epoch="1",
        published=set(),  # nothing published
        test_evidence=_passed_evidence(),   # evidence valid so the PUBLISHED check is reached
        smoke_evidence=_passed_evidence(),
    )
    _expect_named_rejection(mod.verify_manifest, manifest, [wheel], fragment="published")


# ---------------------------------------------------------------- M9: SHA256SUMS sidecar


def test_sha256sums_sidecar_generation_and_tamper_rejection(tmp_path):
    """M9: the sidecar is generated from artifact records; byte changes, missing files,
    extra claims, and disagreement with the manifest all fail."""

    mod = _load_release_module()
    assert hasattr(mod, "write_sha256sums") and hasattr(mod, "verify_sha256sums"), (
        "release tool must own SHA256SUMS generation/verification (missing surface)"
    )
    wheel = _real_wheel(tmp_path)
    sums = mod.write_sha256sums([wheel], tmp_path / "SHA256SUMS")
    mod.verify_sha256sums(sums, tmp_path)  # clean pass

    tampered = bytearray(wheel.read_bytes())
    tampered[-1] ^= 0x01
    wheel.write_bytes(bytes(tampered))
    _expect_named_rejection(mod.verify_sha256sums, sums, tmp_path, fragment="mismatch")


# ---------------------------------------------------------------- positive path + guards


def _passed_evidence() -> dict:
    return {
        "command": ["./test.sh", "unit"],
        "status": "passed",
        "completed_at_utc": "2026-07-19T00:00:00Z",
        "evidence_file_sha256": "a" * 64,
    }


def test_full_manifest_round_trip_with_real_wheel_and_evidence(tmp_path):
    """Happy path: a REAL wheel matrix with passed evidence builds and verifies; the sidecar
    verifies the same bytes; a byte flip afterwards fails BOTH doors."""

    mod = _load_release_module()
    engine = _real_wheel(tmp_path)
    tools = _real_wheel(tmp_path, package="ai_workflow_tools", version="0.5.1")
    viewer = _real_wheel(tmp_path, package="ai_workflow_viewer", version="0.3.1")
    wheels = [engine, tools, viewer]

    manifest = mod.build_manifest(
        wheels,
        tag="engine-v0.11.5",
        tag_object_id="t" * 40,
        source_commit="c" * 40,
        build_command=["python3", "-m", "pip", "wheel", "--no-deps"],
        source_date_epoch="1700000000",
        uri_prefix="cache://releases/0.11.5/",
        test_evidence=_passed_evidence(),
        smoke_evidence=_passed_evidence(),
    )
    mod.verify_manifest(manifest, wheels)
    sums = mod.write_sha256sums(wheels, tmp_path / "SHA256SUMS")
    mod.verify_sha256sums(sums, tmp_path)

    tampered = bytearray(engine.read_bytes())
    tampered[-1] ^= 0x01
    engine.write_bytes(bytes(tampered))
    _expect_named_rejection(mod.verify_manifest, manifest, wheels, fragment="mismatch")
    _expect_named_rejection(mod.verify_sha256sums, sums, tmp_path, fragment="mismatch")


def test_manifest_schema_is_closed_and_placeholders_reject(tmp_path):
    """M5/M7: unknown fields and placeholder build commands fail by name."""

    mod = _load_release_module()
    wheel = _real_wheel(tmp_path)
    manifest = mod.build_manifest(
        [wheel],
        tag="engine-v0.11.5",
        tag_object_id="t" * 40,
        source_commit="c" * 40,
        build_command=["ok"],
        source_date_epoch="1",
        test_evidence=_passed_evidence(),
        smoke_evidence=_passed_evidence(),
    )
    alien = dict(manifest)
    alien["surprise"] = 1
    _expect_named_rejection(mod.verify_manifest, alien, [wheel], fragment="unknown")

    _expect_named_rejection(
        mod.build_manifest,
        [wheel],
        tag="engine-v0.11.5",
        tag_object_id="t" * 40,
        source_commit="c" * 40,
        build_command=["pip", "wheel", "-w", "<out>"],
        source_date_epoch="1",
        fragment="placeholder",
    )


def test_positive_release_manifest_binds_real_annotated_tag(tmp_path):
    """M3 positive: an annotated engine-v0.11.5 tag + matching wheel builds a full manifest."""

    mod = _load_release_module()
    wheel = _real_wheel(tmp_path)
    repo, commit = _scratch_tag_repo(tmp_path, tag="engine-v0.11.5")
    manifest = mod.build_release_manifest(
        [wheel], repo=repo, tag="engine-v0.11.5",
        test_evidence=_passed_evidence(), smoke_evidence=_passed_evidence(),
    )
    assert manifest["source_commit"] == commit
    assert manifest["artifacts"][0]["published"] is True
    mod.verify_manifest(manifest, [wheel])


def test_release_script_is_not_imported_by_the_runtime_engine():
    """R2.1 guard: scripts/ is release tooling; normal engine import must never load it."""

    engine_root = _PACKAGE_ROOT / "ai_workflow_engine"
    offenders = [
        str(path)
        for path in engine_root.rglob("*.py")
        if "release_artifacts" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"runtime engine references release tooling: {offenders}"
    assert not (engine_root / "scripts").exists(), "release tooling must live outside the package"
