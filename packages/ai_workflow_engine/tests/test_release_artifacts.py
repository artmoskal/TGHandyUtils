"""Release trust boundary: real commands, real wheels, and hostile bundle shapes."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Callable

import pytest

pytestmark = pytest.mark.unit

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _PACKAGE_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import release_artifacts as cli  # noqa: E402
import release_build as builder  # noqa: E402
import release_contract as contract  # noqa: E402


def _expect_rejection(callable_, *args, fragment: str, **kwargs) -> Exception:
    try:
        callable_(*args, **kwargs)
    except AssertionError:
        raise
    except Exception as exc:
        assert fragment.lower() in str(exc).lower(), (
            f"rejection must name {fragment!r}; got {exc!r}"
        )
        return exc
    raise AssertionError(f"must reject {fragment!r}, but accepted")


def _record_hash(data: bytes) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(data).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def _real_wheel(
    directory: Path,
    *,
    package: str = "ai_workflow_engine",
    version: str = "0.11.5",
    filename: str | None = None,
    wheel_metadata: bytes | None = None,
    record_override: bytes | None = None,
    extra_members: dict[str, bytes] | None = None,
    duplicate_member: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = filename or f"{package}-{version}-py3-none-any.whl"
    path = directory / name
    dist_info = f"{package}-{version}.dist-info"
    files = {
        f"{package}/__init__.py": f"__version__ = {version!r}\n".encode(),
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\n"
            f"Name: {package.replace('_', '-')}\n"
            f"Version: {version}\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            wheel_metadata
            if wheel_metadata is not None
            else (
                b"Wheel-Version: 1.0\n"
                b"Generator: setuptools (84.0.0)\n"
                b"Root-Is-Purelib: true\n"
                b"Tag: py3-none-any\n"
            )
        ),
    }
    files.update(extra_members or {})
    record_name = f"{dist_info}/RECORD"
    if record_override is None:
        rows = [
            f"{arcname},sha256={_record_hash(data)},{len(data)}"
            for arcname, data in files.items()
        ]
        rows.append(f"{record_name},,")
        files[record_name] = ("\n".join(rows) + "\n").encode()
    else:
        files[record_name] = record_override
    with zipfile.ZipFile(path, "w") as archive:
        for arcname, data in files.items():
            archive.writestr(arcname, data)
        if duplicate_member is not None:
            archive.writestr(duplicate_member, files[duplicate_member])
    return path


def _write_package(
    repo: Path,
    relative_pyproject: str,
    name: str,
    version: str,
    *,
    build_requires: tuple[str, ...] = (
        "setuptools==84.0.0",
        "wheel==0.48.0",
    ),
) -> None:
    package_root = repo / relative_pyproject
    package_root.mkdir(parents=True, exist_ok=True)
    module_name = name.replace("-", "_")
    module = package_root / module_name
    module.mkdir()
    (module / "__init__.py").write_text(
        f"__version__ = {version!r}\n",
        encoding="utf-8",
    )
    (package_root / "pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                f"requires = {json.dumps(list(build_requires))}",
                'build-backend = "setuptools.build_meta"',
                "",
                "[project]",
                f'name = "{name}"',
                f'version = "{version}"',
                "",
                "[tool.setuptools.packages.find]",
                'where = ["."]',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _scratch_tag_repo(
    tmp_path: Path,
    *,
    tag: str = "engine-v0.11.5",
    annotated: bool = True,
    versions: dict[str, str] | None = None,
    inspect_tag: bool = True,
    annotation: str | None = None,
    build_requires: dict[str, tuple[str, ...]] | None = None,
) -> tuple[Path, dict]:
    versions = versions or {
        "ai-workflow-engine": "0.11.5",
        "ai-workflow-tools": "0.5.1",
        "ai-workflow-viewer": "0.3.1",
    }
    build_requires = build_requires or {}
    repo = tmp_path / "tagged-source"
    repo.mkdir(parents=True)
    _write_package(
        repo,
        "packages/ai_workflow_engine",
        "ai-workflow-engine",
        versions["ai-workflow-engine"],
        build_requires=build_requires.get(
            "ai-workflow-engine", ("setuptools==84.0.0", "wheel==0.48.0")
        ),
    )
    _write_package(
        repo,
        "packages/ai_workflow_tools",
        "ai-workflow-tools",
        versions["ai-workflow-tools"],
        build_requires=build_requires.get(
            "ai-workflow-tools", ("setuptools==84.0.0", "wheel==0.48.0")
        ),
    )
    _write_package(
        repo,
        "packages/ai_workflow_viewer",
        "ai-workflow-viewer",
        versions["ai-workflow-viewer"],
        build_requires=build_requires.get(
            "ai-workflow-viewer", ("setuptools==84.0.0", "wheel==0.48.0")
        ),
    )
    verifier_dir = repo / "packages/ai_workflow_engine/scripts"
    verifier_dir.mkdir(parents=True)
    for name in contract.VERIFIER_FILENAMES:
        (verifier_dir / name).write_bytes((_SCRIPTS / name).read_bytes())
    test_script = repo / "test.sh"
    test_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    test_script.chmod(0o755)
    (repo / ".gitignore").write_text(
        "__pycache__/\n*.egg-info/\nbuild/\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git_identity = ["-c", "user.email=release@test", "-c", "user.name=release-test"]
    subprocess.run(["git", *git_identity, "-C", str(repo), "add", "."], check=True)
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-19T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-07-19T00:00:00Z",
    }
    subprocess.run(
        ["git", *git_identity, "-C", str(repo), "commit", "-qm", "release source"],
        check=True,
        env=commit_env,
    )
    if annotated:
        source_commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        message = (
            annotation
            if annotation is not None
            else contract.release_tag_annotation(tag, source_commit, versions)
        )
        subprocess.run(
            ["git", *git_identity, "-C", str(repo), "tag", "-a", tag, "-m", message],
            check=True,
            env=commit_env,
        )
    else:
        subprocess.run(["git", "-C", str(repo), "tag", tag], check=True)
    source = contract.tagged_source(repo, tag) if annotated and inspect_tag else {}
    return repo, source


def _wheel_matrix(directory: Path) -> list[Path]:
    return [
        _real_wheel(directory, package="ai_workflow_engine", version="0.11.5"),
        _real_wheel(directory, package="ai_workflow_tools", version="0.5.1"),
        _real_wheel(directory, package="ai_workflow_viewer", version="0.3.1"),
    ]


def test_installed_smoke_uses_fresh_venv_and_installs_declared_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheels = _wheel_matrix(tmp_path / "wheels")
    calls: list[list[str]] = []

    def capture_run(argv, **_kwargs):
        calls.append([str(item) for item in argv])
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(cli.subprocess, "run", capture_run)
    cli.run_installed_smoke(
        wheels=wheels,
        venv_dir=tmp_path / "venv",
        work_dir=tmp_path / "work",
    )

    assert len(calls) == 3
    assert calls[0] == [sys.executable, "-m", "venv", str(tmp_path / "venv")]
    pip_call = calls[1]
    assert pip_call[1:4] == ["-m", "pip", "install"]
    assert "--no-deps" not in pip_call
    assert "--system-site-packages" not in calls[0]
    assert pip_call[4:] == [
        str(wheels[0].resolve()),
        f"ai-workflow-tools[openai] @ {wheels[1].resolve().as_uri()}",
        str(wheels[2].resolve()),
    ]
    assert calls[2][1:3] == ["-I", "-c"]
    smoke_program = calls[2][3]
    assert "OpenAICompatibleLLMClient" in smoke_program
    assert "codex_prompt_transport" in smoke_program
    assert "open_observation_run_bundle" in smoke_program
    assert "prepare_detail_delivery" in smoke_program
    assert "export_observation_group" in smoke_program
    assert 'assert "site-packages" in ai_workflow_tools.__file__' in smoke_program
    assert 'assert "site-packages" in ai_workflow_viewer.__file__' in smoke_program
    assert 'assert "VIEWER-HEAD" not in viewer_html' in smoke_program
    assert 'assert b"".join(exact_delivery.chunks) == expected_viewer_body' in smoke_program
    assert 'assert "file://" not in export_index.read_text' in smoke_program
    assert "shutil.rmtree(viewer_root)" in smoke_program
    assert "exported_artifact.read_bytes() == viewer_artifact_bytes" in smoke_program
    assert "installed live viewer served a tampered artifact as success" in smoke_program
    assert 'assert "manifest identity" in exc.read().decode' in smoke_program
    assert 'assert codex_exec.prompt_delivery == "stdin"' in smoke_program
    assert "_argv == [CODEX_STDIN_MARKER]" in smoke_program
    assert "len(_stdin) == 256 * 1024" in smoke_program
    assert 'prompt_delivery="argv_last"' in smoke_program
    assert "argv_last must be rejected by the installed model" in smoke_program


def test_release_runbook_requires_installed_real_browser_rss_for_viewer_changes():
    repository = Path(__file__).parents[3]
    operations = (
        repository / "packages/ai_workflow_engine/docs/operations.md"
    ).read_text(encoding="utf-8")
    qualifier = (
        repository / "packages/ai_workflow_viewer/scripts/qualify_browser_rss.py"
    ).read_text(encoding="utf-8")

    assert "changes observation storage, readers, viewer delivery, or static export" in operations
    assert '--installed-python "$OUT/smoke-venv/bin/python"' in operations
    assert '--record "$OUT/viewer-browser-rss.json"' in operations
    assert '"site-packages" in value' in qualifier
    assert 'page.locator("[data-load-detail]").first' in qualifier
    assert '"server_peak_rss_kib"' in qualifier
    assert '"browser_peak_rss_kib"' in qualifier
    assert '"server_baseline_rss_kib"' in qualifier
    assert '"browser_baseline_rss_kib"' in qualifier
    assert 'for key in ("server_growth_rss_kib", "browser_growth_rss_kib")' in qualifier
    assert '"schema": "viewer-browser-rss-v2"' in qualifier


def test_release_runbook_requires_installed_large_v4_qualification():
    repository = Path(__file__).parents[3]
    operations = (
        repository / "packages/ai_workflow_engine/docs/operations.md"
    ).read_text(encoding="utf-8")
    qualifier = (
        repository / "packages/ai_workflow_engine/scripts/qualify_observation_v4.py"
    ).read_text(encoding="utf-8")

    assert 'V4_QUALIFIER="$BUILD_A/packages/ai_workflow_engine/scripts/qualify_observation_v4.py"' in operations
    assert '"$OUT/smoke-venv/bin/python" -I "$V4_QUALIFIER"' in operations
    assert '--record "$OUT/observation-v4-qualification.json"' in operations
    assert '"schema": "observation-v4-installed-qualification-v1"' in qualifier
    assert '"full_corpus": args.record_count == RECORD_COUNT' in qualifier
    assert '"deterministic_second_generation": True' in qualifier
    assert 'if not all("site-packages" in origin' in qualifier
    assert 'first["artifact_source"].unlink()' in qualifier


def _sibling_checkout(repo: Path, destination: Path) -> Path:
    """A second clean detached checkout of the same commit.

    The release contract requires wheel reproducibility from two INDEPENDENT checkouts and
    test/smoke evidence from a third, so the fixture must model that shape rather than
    reusing one working tree."""

    subprocess.run(
        ["git", "clone", "--no-hardlinks", "--quiet", str(repo), str(destination)],
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(destination), "checkout", "--quiet", "--detach", head],
        check=True,
        capture_output=True,
    )
    return destination


def _execute_evidence(
    tmp_path: Path,
    repo: Path,
    source: dict,
    wheels: list[Path],
) -> tuple[Path, Path, Path, Path]:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    second_repo = _sibling_checkout(repo, tmp_path / "checkout-b")
    gate_repo = _sibling_checkout(repo, tmp_path / "checkout-gate")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)
    inspected = [contract.inspect_wheel(path) for path in wheels]
    build_record = evidence_root / "build-evidence.json"
    cli.execute_gate(
        name="build",
        command=[sys.executable, "-c", "print('build gate')"],
        cwd=repo,
        record_path=build_record,
        log_path=evidence_root / "build.log",
        timeout_s=10,
        build_fields={
            "python_implementation": "CPython",
            "python_version": "3.12.0",
            "frontend": {"name": "release_build", "version": "1.0"},
            "backend": {"name": "setuptools.build_meta", "version": "84.0.0"},
            "tool_versions": [
                {"name": "pip", "version": "26.2.1"},
                {"name": "setuptools", "version": "84.0.0"},
                {"name": "wheel", "version": "0.48.0"},
            ],
            "source_commit": source["source_commit"],
            "source_date_epoch": source["source_date_epoch"],
            "umask": "022",
        },
        post_success=lambda: {
            "built_artifacts": [
                {
                    key: item[key]
                    for key in (
                        "package", "version", "filename", "size_bytes", "sha256", "generator"
                    )
                }
                for item in sorted(inspected, key=lambda row: row["package"])
            ]
        },
    )
    second_build_record = evidence_root / "build-b-evidence.json"
    cli.execute_gate(
        name="build",
        command=[sys.executable, "-c", "print('second build gate')"],
        cwd=second_repo,
        record_path=second_build_record,
        log_path=evidence_root / "build-b.log",
        timeout_s=10,
        build_fields={
            "python_implementation": "CPython",
            "python_version": "3.12.0",
            "frontend": {"name": "release_build", "version": "1.0"},
            "backend": {"name": "setuptools.build_meta", "version": "84.0.0"},
            "tool_versions": [
                {"name": "pip", "version": "26.2.1"},
                {"name": "setuptools", "version": "84.0.0"},
                {"name": "wheel", "version": "0.48.0"},
            ],
            "source_commit": source["source_commit"],
            "source_date_epoch": source["source_date_epoch"],
            "umask": "022",
        },
        post_success=lambda: {
            "built_artifacts": [
                {
                    key: item[key]
                    for key in (
                        "package", "version", "filename", "size_bytes", "sha256", "generator"
                    )
                }
                for item in sorted(inspected, key=lambda row: row["package"])
            ]
        },
    )
    test_record = evidence_root / "test-evidence.json"
    cli.execute_gate(
        name="test",
        command=["./test.sh", "unit"],
        cwd=gate_repo,
        record_path=test_record,
        log_path=evidence_root / "test.log",
        timeout_s=10,
    )
    smoke_record = evidence_root / "smoke-evidence.json"
    smoke_command = [
        str(fake_python),
        str(repo / "packages/ai_workflow_engine/scripts/release_artifacts.py"),
        "smoke-installed",
        "--venv-dir",
        str(tmp_path / "smoke-venv"),
        "--work-dir",
        str(tmp_path / "smoke-work"),
    ]
    for wheel in wheels:
        smoke_command.extend(["--wheel", str(wheel)])
    cli.execute_gate(
        name="smoke",
        command=smoke_command,
        cwd=gate_repo,
        record_path=smoke_record,
        log_path=evidence_root / "smoke.log",
        timeout_s=10,
    )
    return build_record, second_build_record, test_record, smoke_record


def _release_inputs(tmp_path: Path) -> dict:
    repo, source = _scratch_tag_repo(tmp_path)
    wheels = _wheel_matrix(tmp_path / "wheels")
    second_wheel_dir = tmp_path / "second-wheels"
    second_wheel_dir.mkdir()
    second_wheels = []
    for wheel in wheels:
        copied = second_wheel_dir / wheel.name
        shutil.copy2(wheel, copied)
        second_wheels.append(copied)
    build, second_build, test, smoke = _execute_evidence(
        tmp_path,
        repo,
        source,
        wheels,
    )
    return {
        "repo": repo,
        "source": source,
        "wheels": wheels,
        "second_wheels": second_wheels,
        "build": build,
        "second_build": second_build,
        "test": test,
        "smoke": smoke,
        "verifiers": [
            repo / "packages/ai_workflow_engine/scripts" / name
            for name in sorted(contract.VERIFIER_FILENAMES)
        ],
    }


def _assemble(tmp_path: Path) -> tuple[Path, dict]:
    inputs = _release_inputs(tmp_path)
    bundle = tmp_path / "bundle"
    manifest = cli.assemble_release_bundle(
        repo=inputs["repo"],
        tag="engine-v0.11.5",
        bundle_dir=bundle,
        uri_base="file:///private-cache/engine-v0.11.5/",
        wheels=inputs["wheels"],
        build_evidence_path=inputs["build"],
        second_wheels=inputs["second_wheels"],
        second_build_evidence_path=inputs["second_build"],
        test_evidence_path=inputs["test"],
        smoke_evidence_path=inputs["smoke"],
        verifier_paths=inputs["verifiers"],
    )
    return bundle, manifest


def _rewrite_sums(bundle: Path) -> None:
    contract.write_sha256sums(
        [
            child
            for child in bundle.iterdir()
            if child.name != "SHA256SUMS"
        ],
        bundle / "SHA256SUMS",
    )


def test_wheel_requires_real_zip_and_coherent_controls(tmp_path: Path) -> None:
    fake = tmp_path / "ai_workflow_engine-0.11.5-py3-none-any.whl"
    fake.write_bytes(b"not a wheel")
    _expect_rejection(contract.inspect_wheel, fake, fragment="ZIP wheel")

    empty_wheel = _real_wheel(
        tmp_path / "empty-wheel",
        wheel_metadata=b"",
    )
    _expect_rejection(contract.inspect_wheel, empty_wheel, fragment="Wheel-Version")

    empty_record = _real_wheel(
        tmp_path / "empty-record",
        record_override=b"",
    )
    _expect_rejection(contract.inspect_wheel, empty_record, fragment="RECORD member set")


def test_wheel_rejects_duplicate_unsafe_and_digest_drift(tmp_path: Path) -> None:
    duplicate = _real_wheel(
        tmp_path / "duplicate",
        duplicate_member="ai_workflow_engine/__init__.py",
    )
    _expect_rejection(contract.inspect_wheel, duplicate, fragment="duplicate")

    unsafe = _real_wheel(
        tmp_path / "unsafe",
        extra_members={"../outside.py": b"x"},
    )
    _expect_rejection(contract.inspect_wheel, unsafe, fragment="unsafe archive member")

    bad_record = _real_wheel(
        tmp_path / "bad-record",
        record_override=(
            b"ai_workflow_engine/__init__.py,sha256=wrong,1\n"
            b"ai_workflow_engine-0.11.5.dist-info/METADATA,sha256=wrong,1\n"
            b"ai_workflow_engine-0.11.5.dist-info/WHEEL,sha256=wrong,1\n"
            b"ai_workflow_engine-0.11.5.dist-info/RECORD,,\n"
        ),
    )
    _expect_rejection(contract.inspect_wheel, bad_record, fragment="size mismatch")


def test_wheel_path_must_be_plain_non_symlink_regular_file(tmp_path: Path) -> None:
    wheel = _real_wheel(tmp_path / "real")
    link = tmp_path / wheel.name
    link.symlink_to(wheel)
    _expect_rejection(contract.inspect_wheel, link, fragment="non-symlink regular")


def test_tagged_source_requires_annotated_exact_three_package_matrix(
    tmp_path: Path,
) -> None:
    repo, source = _scratch_tag_repo(tmp_path / "good")
    assert source["matrix"] == {
        "ai-workflow-engine": "0.11.5",
        "ai-workflow-tools": "0.5.1",
        "ai-workflow-viewer": "0.3.1",
    }
    assert source["source_date_epoch"] > 0

    engine_pyproject = repo / "packages/ai_workflow_engine/pyproject.toml"
    engine_pyproject.write_text(
        engine_pyproject.read_text(encoding="utf-8").replace(
            'version = "0.11.5"',
            'version = "99.0.0"',
        ),
        encoding="utf-8",
    )
    assert contract.tagged_source(repo, "engine-v0.11.5")["matrix"][
        "ai-workflow-engine"
    ] == "0.11.5"

    light_repo, _ = _scratch_tag_repo(tmp_path / "light", annotated=False)
    _expect_rejection(
        contract.tagged_source,
        light_repo,
        "engine-v0.11.5",
        fragment="annotated",
    )

    wrong_repo, _ = _scratch_tag_repo(
        tmp_path / "wrong",
        versions={
            "ai-workflow-engine": "0.11.4",
            "ai-workflow-tools": "0.5.1",
            "ai-workflow-viewer": "0.3.1",
        },
        inspect_tag=False,
    )
    _expect_rejection(
        contract.tagged_source,
        wrong_repo,
        "engine-v0.11.5",
        fragment="tag suffix",
    )


def test_tagged_source_rejects_copied_release_evidence_in_annotation(
    tmp_path: Path,
) -> None:
    repo, source = _scratch_tag_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "tag", "-d", "engine-v0.11.5"],
        check=True,
        capture_output=True,
    )
    annotation = contract.release_tag_annotation(
        "engine-v0.11.5",
        source["source_commit"],
        source["matrix"],
    ) + "\n\nGate: 100 passed / 2 skipped\n\nWheel SHA-256: invented"
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=release@test",
            "-c",
            "user.name=release-test",
            "-C",
            str(repo),
            "tag",
            "-a",
            "engine-v0.11.5",
            "-m",
            annotation,
        ],
        check=True,
    )

    _expect_rejection(
        contract.tagged_source,
        repo,
        "engine-v0.11.5",
        fragment="source identity only",
    )


def test_manifest_binds_test_and_smoke_to_canonical_release_commands(tmp_path: Path) -> None:
    _bundle, manifest = _assemble(tmp_path)

    focused = copy.deepcopy(manifest)
    focused["test_evidence"]["command_argv"].extend(
        ["--", "packages/ai_workflow_engine/tests/test_release_artifacts.py"]
    )
    _expect_rejection(contract.parse_manifest, focused, fragment="exactly ./test.sh unit")

    source_smoke = copy.deepcopy(manifest)
    source_smoke["smoke_evidence"]["command_argv"] = ["./test.sh", "unit"]
    _expect_rejection(contract.parse_manifest, source_smoke, fragment="smoke-installed")

    foreign_wheel = copy.deepcopy(manifest)
    smoke_argv = foreign_wheel["smoke_evidence"]["command_argv"]
    wheel_index = smoke_argv.index("--wheel") + 1
    smoke_argv[wheel_index] = str(Path(smoke_argv[wheel_index]).with_name("foreign.whl"))
    _expect_rejection(contract.parse_manifest, foreign_wheel, fragment="artifact filenames")

    duplicate_wheel = copy.deepcopy(manifest)
    duplicate_argv = duplicate_wheel["smoke_evidence"]["command_argv"]
    first_wheel = duplicate_argv[duplicate_argv.index("--wheel") + 1]
    duplicate_argv.extend(["--wheel", first_wheel])
    _expect_rejection(contract.parse_manifest, duplicate_wheel, fragment="artifact filenames")


def test_smoke_command_binding_is_interpreter_neutral(tmp_path: Path) -> None:
    """The published v0.11.12 and v0.11.13 smoke records ran under an executable named
    `python` (a conda prefix), not `python3`. Binding on the interpreter BASENAME rejected
    that real evidence while proving nothing: a fake executable can just as easily be named
    `python3`. The facts that carry meaning already have owners — the release script, the
    subcommand, the closed option grammar, and the exact wheel identities."""

    _bundle, manifest = _assemble(tmp_path)

    for interpreter in (
        "/Users/someone/miniconda3/bin/python",  # the exact published v0.11.13 shape
        "/opt/conda/bin/python",
        "python",
        "/usr/bin/python3.11",
        "python3",
        "/usr/local/bin/python3.12",
    ):
        accepted = copy.deepcopy(manifest)
        accepted["smoke_evidence"]["command_argv"][0] = interpreter
        parsed = contract.parse_manifest(accepted)
        assert parsed["smoke_evidence"]["command_argv"][0] == interpreter

    # Dropping the basename rule must not loosen the structure that does carry proof.
    wrong_script = copy.deepcopy(manifest)
    argv = wrong_script["smoke_evidence"]["command_argv"]
    argv[1] = str(Path(argv[1]).with_name("other_tool.py"))
    _expect_rejection(contract.parse_manifest, wrong_script, fragment="smoke-installed")

    wrong_subcommand = copy.deepcopy(manifest)
    wrong_subcommand["smoke_evidence"]["command_argv"][2] = "verify-bundle"
    _expect_rejection(contract.parse_manifest, wrong_subcommand, fragment="smoke-installed")

    stray_option = copy.deepcopy(manifest)
    stray_option["smoke_evidence"]["command_argv"].extend(["--allow-network", "1"])
    _expect_rejection(contract.parse_manifest, stray_option, fragment="smoke-installed")

    truncated = copy.deepcopy(manifest)
    truncated["smoke_evidence"]["command_argv"] = truncated["smoke_evidence"]["command_argv"][:2]
    _expect_rejection(contract.parse_manifest, truncated, fragment="smoke-installed")


def test_embedded_verifier_runs_in_place_without_mutating_the_bundle(tmp_path: Path) -> None:
    """A release directory is a closed inventory, and the verifier ships inside it. Running
    that shipped copy in place imported its sibling and wrote `__pycache__` into the very
    directory under validation, so the bundle rejected itself with `extra=['__pycache__']` —
    indistinguishable, to an operator, from real corruption. Verification must never mutate
    its subject."""

    bundle, _manifest = _assemble(tmp_path)
    before = sorted(entry.name for entry in bundle.iterdir())

    environment = dict(os.environ)
    # A consumer will not have this set; the guarantee must not depend on their environment.
    environment.pop("PYTHONDONTWRITEBYTECODE", None)

    for attempt in (1, 2):
        completed = subprocess.run(
            [
                sys.executable,
                str(bundle / "release_artifacts.py"),
                "verify-bundle",
                "--dir",
                str(bundle),
            ],
            cwd=str(tmp_path),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, (
            f"attempt {attempt} failed: {completed.stdout}\n{completed.stderr}"
        )
        assert not (bundle / "__pycache__").exists(), (
            f"attempt {attempt} wrote __pycache__ into the closed release directory"
        )
        assert sorted(entry.name for entry in bundle.iterdir()) == before, (
            f"attempt {attempt} changed the release directory inventory"
        )


def test_importing_release_artifacts_never_changes_global_bytecode_policy(tmp_path: Path) -> None:
    """The bytecode guard must stay scoped to standalone execution. Suppressing bytecode is right
    for the CLI shipped inside a closed release directory, but a plain `import release_artifacts`
    from tests or tooling must not reach out and change process-global import behaviour for its
    caller. Without this fence the `__name__ == "__main__"` qualifier can be dropped — turning the
    fix into a global side effect — while every other release test stays green, because they all
    exercise standalone execution only, where correct and incorrect behave identically.

    The probe runs in a subprocess: this test module already imported the script at collection, so
    an in-process check would observe a cached module and prove nothing.
    """

    probe = tmp_path / "import_probe.py"
    probe.write_text(
        "\n".join(
            [
                "import sys",
                f"sys.path.insert(0, {str(_SCRIPTS)!r})",
                "prior = sys.argv[1] == 'True'",
                "sys.dont_write_bytecode = prior",
                "import release_artifacts  # normal module import, never __main__",
                "print('UNCHANGED' if sys.dont_write_bytecode == prior else 'CHANGED')",
            ]
        ),
        encoding="utf-8",
    )

    environment = dict(os.environ)
    # The guarantee must hold on its own, not because the caller happened to export this.
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    # Keep any bytecode the correct implementation legitimately writes out of the source tree.
    environment["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pycache")

    for prior in ("False", "True"):
        completed = subprocess.run(
            [sys.executable, str(probe), prior],
            cwd=str(tmp_path),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, f"probe failed: {completed.stdout}\n{completed.stderr}"
        assert completed.stdout.strip() == "UNCHANGED", (
            f"importing release_artifacts mutated sys.dont_write_bytecode for a caller that had "
            f"set it to {prior}"
        )


def test_manifest_assembly_binds_smoke_to_released_wheel_bytes(tmp_path: Path) -> None:
    inputs = _release_inputs(tmp_path)
    alien = _real_wheel(
        tmp_path / "alien-smoke",
        package="ai_workflow_engine",
        version="0.11.5",
        extra_members={"ai_workflow_engine/alien.txt": b"different-valid-wheel"},
    )
    smoke_record = json.loads(inputs["smoke"].read_text(encoding="utf-8"))
    argv = smoke_record["command_argv"]
    for index, token in enumerate(argv[:-1]):
        if token == "--wheel" and Path(argv[index + 1]).name == alien.name:
            argv[index + 1] = str(alien)
            break
    else:
        raise AssertionError("engine wheel missing from smoke fixture")
    inputs["smoke"].write_text(json.dumps(smoke_record), encoding="utf-8")

    _expect_rejection(
        contract.assemble_manifest,
        repo=inputs["repo"],
        tag="engine-v0.11.5",
        wheels=inputs["wheels"],
        build_evidence_path=inputs["build"],
        second_wheels=inputs["second_wheels"],
        second_build_evidence_path=inputs["second_build"],
        test_evidence_path=inputs["test"],
        smoke_evidence_path=inputs["smoke"],
        uri_base="file:///cache/engine-v0.11.5/",
        verifier_paths=inputs["verifiers"],
        fragment="wheel bytes disagree",
    )


def test_gate_evidence_comes_from_executed_command_and_bounded_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source = _scratch_tag_repo(tmp_path / "source")
    monkeypatch.setattr(cli, "MAX_RETAINED_LOG_BYTES", 64)
    record_path = tmp_path / "test-evidence.json"
    log_path = tmp_path / "test.log"
    record = cli.execute_gate(
        name="test",
        command=[sys.executable, "-c", "print('x' * 1024)"],
        cwd=repo,
        record_path=record_path,
        log_path=log_path,
        timeout_s=10,
    )
    assert record["status"] == "passed"
    assert record["source_commit"] == source["source_commit"]
    assert record["command_argv"][0] == sys.executable
    assert record["log_size_bytes"] == 64
    assert record["log_total_bytes"] > record["log_size_bytes"]
    assert record["log_truncated"] is True
    embedded = contract.load_evidence_record(record_path, "test")
    assert embedded["record_sha256"] == contract.hash_path(record_path)[1]

    log_path.write_text("caller-replaced-evidence", encoding="utf-8")
    _expect_rejection(
        contract.load_evidence_record,
        record_path,
        "test",
        fragment="size mismatch",
    )
    log_path.unlink()
    _expect_rejection(
        contract.load_evidence_record,
        record_path,
        "test",
        fragment="missing",
    )


def test_failed_and_timed_out_commands_never_emit_passed_evidence(
    tmp_path: Path,
) -> None:
    repo, _source = _scratch_tag_repo(tmp_path / "source")
    failed_record = tmp_path / "failed.json"
    _expect_rejection(
        cli.execute_gate,
        name="test",
        command=[sys.executable, "-c", "raise SystemExit(7)"],
        cwd=repo,
        record_path=failed_record,
        log_path=tmp_path / "failed.log",
        timeout_s=10,
        fragment="exited with 7",
    )
    assert json.loads(failed_record.read_text())["status"] == "failed"
    _expect_rejection(
        contract.load_evidence_record,
        failed_record,
        "test",
        fragment="successful command",
    )

    timeout_record = tmp_path / "timeout.json"
    _expect_rejection(
        cli.execute_gate,
        name="smoke",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=repo,
        record_path=timeout_record,
        log_path=tmp_path / "timeout.log",
        timeout_s=0.1,
        fragment="timed out",
    )
    timeout = json.loads(timeout_record.read_text())
    assert timeout["status"] == "failed"
    assert timeout["exit_code"] == 124

    dirty_record = tmp_path / "dirty.json"
    _expect_rejection(
        cli.execute_gate,
        name="test",
        command=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('unexpected.py').write_text('dirty')",
        ],
        cwd=repo,
        record_path=dirty_record,
        log_path=tmp_path / "dirty.log",
        timeout_s=10,
        fragment="left the checkout dirty",
    )
    assert json.loads(dirty_record.read_text())["status"] == "failed"


def test_manifest_is_nested_closed_type_strict_and_time_ordered(
    tmp_path: Path,
) -> None:
    inputs = _release_inputs(tmp_path)
    manifest = contract.assemble_manifest(
        repo=inputs["repo"],
        tag="engine-v0.11.5",
        wheels=inputs["wheels"],
        build_evidence_path=inputs["build"],
        second_wheels=inputs["second_wheels"],
        second_build_evidence_path=inputs["second_build"],
        test_evidence_path=inputs["test"],
        smoke_evidence_path=inputs["smoke"],
        uri_base="file:///cache/engine-v0.11.5/",
        verifier_paths=inputs["verifiers"],
    )
    assert contract.parse_manifest(manifest) == manifest

    attacks: list[tuple[str, Callable[[dict], None]]] = [
        ("unknown fields", lambda value: value["test_evidence"].update({"surprise": 1})),
        ("boolean", lambda value: value["artifacts"][0].update({"published": "false"})),
        (
            "real ISO-8601",
            lambda value: value["smoke_evidence"].update(
                {"completed_at_utc": "2026-99-99T99:99:99Z"}
            ),
        ),
        (
            "precedes",
            lambda value: value["test_evidence"].update(
                {
                    "started_at_utc": "2026-07-20T00:00:00Z",
                    "completed_at_utc": "2026-07-19T00:00:00Z",
                }
            ),
        ),
        ("integer", lambda value: value["artifacts"][0].update({"size_bytes": True})),
        ("nonblank", lambda value: value["artifacts"][0].update({"uri": ""})),
        ("matrix", lambda value: value["matrix"].pop("ai-workflow-viewer")),
        (
            "build evidence",
            lambda value: value["build"]["built_artifacts"][0].update({"sha256": "f" * 64}),
        ),
        (
            "reproducibility",
            lambda value: value["reproducibility"]["second_build"][
                "built_artifacts"
            ][0].update({"sha256": "e" * 64}),
        ),
        (
            "invalid format",
            lambda value: value["build"]["frontend"].update({"version": "not-run"}),
        ),
        (
            "exact release build toolchain",
            lambda value: value["build"]["tool_versions"][0].update(
                {"version": "83.0.0"}
            ),
        ),
        (
            "pinned wheel generator",
            lambda value: value["build"]["built_artifacts"][0].update(
                {"generator": "setuptools (83.0.0)"}
            ),
        ),
        (
            "test evidence source commit",
            lambda value: value["test_evidence"].update({"source_commit": "f" * 40}),
        ),
    ]
    for fragment, mutate in attacks:
        attacked = copy.deepcopy(manifest)
        mutate(attacked)
        _expect_rejection(contract.parse_manifest, attacked, fragment=fragment)


def test_manifest_binds_supplied_wheels_and_verifiers_to_build_and_tag(
    tmp_path: Path,
) -> None:
    inputs = _release_inputs(tmp_path)
    alien = _real_wheel(
        tmp_path / "alien",
        package="ai_workflow_engine",
        version="0.11.5",
        extra_members={"ai_workflow_engine/extra.txt": b"different-valid-wheel"},
    )
    _expect_rejection(
        contract.assemble_manifest,
        repo=inputs["repo"],
        tag="engine-v0.11.5",
        wheels=[alien, *inputs["wheels"][1:]],
        build_evidence_path=inputs["build"],
        second_wheels=inputs["second_wheels"],
        second_build_evidence_path=inputs["second_build"],
        test_evidence_path=inputs["test"],
        smoke_evidence_path=inputs["smoke"],
        uri_base="file:///cache/",
        verifier_paths=inputs["verifiers"],
        fragment="build evidence",
    )
    _expect_rejection(
        contract.assemble_manifest,
        repo=inputs["repo"],
        tag="engine-v0.11.5",
        wheels=inputs["wheels"],
        build_evidence_path=inputs["build"],
        second_wheels=[alien, *inputs["second_wheels"][1:]],
        second_build_evidence_path=inputs["second_build"],
        test_evidence_path=inputs["test"],
        smoke_evidence_path=inputs["smoke"],
        uri_base="file:///cache/",
        verifier_paths=inputs["verifiers"],
        fragment="second build evidence",
    )

    changed_verifier = tmp_path / "release_contract.py"
    changed_verifier.write_text("# not tagged\n", encoding="utf-8")
    _expect_rejection(
        contract.assemble_manifest,
        repo=inputs["repo"],
        tag="engine-v0.11.5",
        wheels=inputs["wheels"],
        build_evidence_path=inputs["build"],
        second_wheels=inputs["second_wheels"],
        second_build_evidence_path=inputs["second_build"],
        test_evidence_path=inputs["test"],
        smoke_evidence_path=inputs["smoke"],
        uri_base="file:///cache/",
        verifier_paths=[
            changed_verifier
            if path.name == "release_contract.py"
            else path
            for path in inputs["verifiers"]
        ],
        fragment="byte-match",
    )
    _expect_rejection(
        contract.assemble_manifest,
        repo=inputs["repo"],
        tag="engine-v0.11.5",
        wheels=inputs["wheels"],
        build_evidence_path=inputs["build"],
        second_wheels=inputs["second_wheels"],
        second_build_evidence_path=inputs["second_build"],
        test_evidence_path=inputs["test"],
        smoke_evidence_path=inputs["smoke"],
        uri_base="file:///cache/",
        verifier_paths=inputs["verifiers"][:1],
        fragment="exact release verifier",
    )


def test_complete_bundle_round_trip_and_exact_inventory(tmp_path: Path) -> None:
    bundle, manifest = _assemble(tmp_path)
    verified = contract.verify_bundle(bundle, "release-manifest.json", "SHA256SUMS")
    assert verified == manifest
    expected = contract.manifest_bundle_filenames(manifest) | {"SHA256SUMS"}
    assert {child.name for child in bundle.iterdir()} == expected

    extra = bundle / "not-in-manifest.txt"
    extra.write_text("extra", encoding="utf-8")
    _expect_rejection(
        contract.verify_bundle,
        bundle,
        "release-manifest.json",
        "SHA256SUMS",
        fragment="directory set",
    )


def test_checksum_extras_are_rejected_before_unlisted_bytes_are_hashed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _manifest = _assemble(tmp_path)
    extra = bundle / "unlisted-large.bin"
    extra.write_bytes(b"unlisted")
    sums = bundle / "SHA256SUMS"
    sums.write_text(
        sums.read_text(encoding="utf-8")
        + f"{'0' * 64}  {extra.name}\n",
        encoding="utf-8",
    )
    original = contract._safe_regular_hash

    def guarded(directory, filename, **kwargs):
        assert filename != extra.name, "unlisted bytes were hashed before exact-set rejection"
        return original(directory, filename, **kwargs)

    monkeypatch.setattr(contract, "_safe_regular_hash", guarded)
    _expect_rejection(
        contract.verify_bundle,
        bundle,
        "release-manifest.json",
        "SHA256SUMS",
        fragment="SHA256SUMS set disagrees",
    )


@pytest.mark.parametrize("shape", ["empty", "duplicate", "traversal"])
def test_checksum_sidecar_rejects_open_or_unsafe_claims(
    tmp_path: Path,
    shape: str,
) -> None:
    bundle, _manifest = _assemble(tmp_path)
    sums = bundle / "SHA256SUMS"
    if shape == "empty":
        sums.write_text("", encoding="utf-8")
        fragment = "must not be empty"
    elif shape == "duplicate":
        line = sums.read_text(encoding="utf-8").splitlines()[0]
        sums.write_text(f"{line}\n{line}\n", encoding="utf-8")
        fragment = "duplicate"
    else:
        outside = tmp_path / "outside-secret"
        outside.write_text("secret", encoding="utf-8")
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        sums.write_text(f"{digest}  ../outside-secret\n", encoding="utf-8")
        fragment = "plain child filename"
    _expect_rejection(
        contract.verify_bundle,
        bundle,
        "release-manifest.json",
        "SHA256SUMS",
        fragment=fragment,
    )


@pytest.mark.parametrize("shape", ["symlink", "fifo"])
def test_bundle_refuses_non_regular_expected_child_without_opening(
    tmp_path: Path,
    shape: str,
) -> None:
    bundle, _manifest = _assemble(tmp_path)
    target = bundle / "release_contract.py"
    target.unlink()
    if shape == "symlink":
        outside = tmp_path / "outside.py"
        outside.write_text("outside", encoding="utf-8")
        target.symlink_to(outside)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("mkfifo unavailable on this platform")
        os.mkfifo(target)
    _expect_rejection(
        contract.verify_bundle,
        bundle,
        "release-manifest.json",
        "SHA256SUMS",
        fragment="non-symlink regular",
    )


def test_bundle_rejects_tampered_wheel_evidence_manifest_and_missing_file(
    tmp_path: Path,
) -> None:
    for shape in ("wheel", "evidence", "manifest", "missing"):
        case = tmp_path / shape
        case.mkdir()
        bundle, _manifest = _assemble(case)
        if shape == "wheel":
            wheel = next(bundle.glob("ai_workflow_engine-*.whl"))
            data = bytearray(wheel.read_bytes())
            data[-1] ^= 1
            wheel.write_bytes(data)
            fragment = "SHA-256 mismatch"
        elif shape == "evidence":
            (bundle / "test.log").write_text("tampered", encoding="utf-8")
            fragment = "SHA-256 mismatch"
        elif shape == "manifest":
            manifest_path = bundle / "release-manifest.json"
            value = json.loads(manifest_path.read_text())
            value["test_evidence"]["surprise"] = 1
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
            _rewrite_sums(bundle)
            fragment = "unknown fields"
        else:
            (bundle / "smoke.log").unlink()
            fragment = "missing"
        _expect_rejection(
            contract.verify_bundle,
            bundle,
            "release-manifest.json",
            "SHA256SUMS",
            fragment=fragment,
        )


def test_two_clean_tag_builds_are_byte_identical_and_recorded(
    tmp_path: Path,
) -> None:
    try:
        actual_toolchain = builder.installed_toolchain()
    except contract.ReleaseError as exc:
        pytest.skip(f"exact release toolchain unavailable: {exc}")
    if actual_toolchain != builder.RELEASE_BUILD_TOOLS:
        pytest.skip(
            f"exact release toolchain unavailable: {actual_toolchain!r}"
        )
    repo, source = _scratch_tag_repo(tmp_path / "source")
    first_dir = tmp_path / "first-wheels"
    first_record = tmp_path / "first-build.json"
    cli.run_reproducible_build(
        repo=repo,
        tag="engine-v0.11.5",
        wheel_dir=first_dir,
        record_path=first_record,
        log_path=tmp_path / "first-build.log",
        timeout_s=120,
    )
    first = contract.load_evidence_record(first_record, "build", build=True)
    assert first["source_commit"] == source["source_commit"]
    assert first["frontend"] == {"name": "release_build", "version": "1.0"}
    assert first["backend"] == {
        "name": "setuptools.build_meta",
        "version": "84.0.0",
    }
    assert first["tool_versions"] == [
        {"name": "pip", "version": "26.2.1"},
        {"name": "setuptools", "version": "84.0.0"},
        {"name": "wheel", "version": "0.48.0"},
    ]
    assert {item["package"] for item in first["built_artifacts"]} == set(
        contract.EXPECTED_PACKAGES
    )
    assert {
        contract.inspect_wheel(path)["generator"] for path in first_dir.glob("*.whl")
    } == {"setuptools (84.0.0)"}

    clone = tmp_path / "second-source"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "checkout", "-q", source["source_commit"]],
        check=True,
    )
    second_dir = tmp_path / "second-wheels"
    cli.run_reproducible_build(
        repo=clone,
        tag="engine-v0.11.5",
        wheel_dir=second_dir,
        record_path=tmp_path / "second-build.json",
        log_path=tmp_path / "second-build.log",
        timeout_s=120,
    )
    compared = cli.compare_builds(
        repo=repo,
        tag="engine-v0.11.5",
        first=first_dir,
        second=second_dir,
    )
    assert len(compared) == 3


def test_tagged_source_rejects_incoherent_exact_build_requirements(tmp_path: Path) -> None:
    repo, _ = _scratch_tag_repo(
        tmp_path,
        inspect_tag=False,
        build_requires={
            "ai-workflow-viewer": ("setuptools==83.0.0", "wheel==0.48.0"),
        },
    )

    with pytest.raises(contract.ReleaseError, match="exact build toolchain"):
        contract.tagged_source(repo, "engine-v0.11.5")


def test_repository_packages_declare_the_closed_build_backend() -> None:
    expected = ["setuptools==84.0.0", "wheel==0.48.0"]
    for relative in contract.EXPECTED_PACKAGES.values():
        pyproject = _PACKAGE_ROOT.parents[1] / relative
        build_system = tomllib.loads(pyproject.read_text(encoding="utf-8"))["build-system"]
        assert build_system == {
            "requires": expected,
            "build-backend": "setuptools.build_meta",
        }


def test_release_builder_rejects_installed_backend_mismatch_before_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = _scratch_tag_repo(tmp_path / "source")
    mismatched = dict(builder.RELEASE_BUILD_TOOLS)
    mismatched["setuptools"] = "67.8.0"
    monkeypatch.setattr(builder, "installed_toolchain", lambda: mismatched)
    wheel_dir = tmp_path / "wheels"
    record = tmp_path / "build.json"
    log = tmp_path / "build.log"

    with pytest.raises(cli.ReleaseError, match="setuptools.*67.8.0.*84.0.0"):
        cli.run_reproducible_build(
            repo=repo,
            tag="engine-v0.11.5",
            wheel_dir=wheel_dir,
            record_path=record,
            log_path=log,
            timeout_s=120,
        )

    assert not record.exists()
    assert not log.exists()
    assert not wheel_dir.exists()


def test_release_scripts_stay_outside_runtime_import_graph() -> None:
    engine_root = _PACKAGE_ROOT / "ai_workflow_engine"
    offenders = [
        str(path)
        for path in engine_root.rglob("*.py")
        if "release_contract" in path.read_text(encoding="utf-8")
        or "release_artifacts" in path.read_text(encoding="utf-8")
        or "release_build" in path.read_text(encoding="utf-8")
        or "release_toolchain" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
    assert not (engine_root / "scripts").exists()


def test_consumer_verifier_import_does_not_require_producer_only_tomllib() -> None:
    program = f"""
import builtins
import runpy
import sys

real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "tomllib":
        raise ModuleNotFoundError("simulated Python 3.10")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
sys.path.insert(0, {str(_SCRIPTS)!r})
surface = runpy.run_path({str(_SCRIPTS / "release_contract.py")!r})
assert callable(surface["verify_bundle"])
"""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_bundle_rejects_reused_build_and_gate_checkouts(tmp_path: Path) -> None:
    """v0.11.11: wheel reproducibility is only meaningful ACROSS checkouts.

    A v0.11.10 closeout ran both builds plus test/smoke from ONE checkout and still passed
    verification, producing a true-looking but false independence proof. The verifier now
    compares recorded working directories: the two builds may share nothing, and gate
    evidence must come from a checkout separate from both. The gate checkout itself may
    legitimately serve BOTH test and smoke (that is the documented runbook shape), so the
    fixture's shared gate directory must keep verifying cleanly.
    """

    shapes = {
        "builds": ("reproducibility", "two independent checkouts"),
        "test": ("test_evidence", "separate from both build checkouts"),
        "smoke": ("smoke_evidence", "separate from both build checkouts"),
    }
    for shape, (_section, fragment) in shapes.items():
        case = tmp_path / shape
        case.mkdir()
        bundle, manifest = _assemble(case)
        manifest_path = bundle / "release-manifest.json"
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        build_directory = value["build"]["working_directory"]
        second_directory = value["reproducibility"]["second_build"]["working_directory"]
        assert build_directory != second_directory, (
            "fixture must model two independent build checkouts"
        )
        if shape == "builds":
            value["reproducibility"]["second_build"]["working_directory"] = build_directory
        elif shape == "test":
            value["test_evidence"]["working_directory"] = build_directory
        else:
            value["smoke_evidence"]["working_directory"] = second_directory
        manifest_path.write_text(json.dumps(value), encoding="utf-8")
        _rewrite_sums(bundle)
        _expect_rejection(
            contract.verify_bundle,
            bundle,
            "release-manifest.json",
            "SHA256SUMS",
            fragment=fragment,
        )


def test_bundle_accepts_one_gate_checkout_serving_test_and_smoke(tmp_path: Path) -> None:
    """The documented runbook uses ONE third checkout for both test and smoke evidence;
    the new distinctness rule must not forbid that."""

    bundle, manifest = _assemble(tmp_path)
    assert (
        manifest["test_evidence"]["working_directory"]
        == manifest["smoke_evidence"]["working_directory"]
    ), "fixture models a single shared gate checkout"
    assert manifest["test_evidence"]["working_directory"] not in {
        manifest["build"]["working_directory"],
        manifest["reproducibility"]["second_build"]["working_directory"],
    }
    contract.verify_bundle(bundle, "release-manifest.json", "SHA256SUMS")
