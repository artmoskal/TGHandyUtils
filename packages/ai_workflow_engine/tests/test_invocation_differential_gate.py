"""Hermetic in-container live differential + provenance gate (Phase I1.R3).

Runs through ``./test.sh`` with NO git, NO network, NO pip: the sealed ``engine-v0.10.1`` baseline
wheel is a committed fixture, so the live baseline-vs-candidate differential executes inside the
mandatory wrapper — unzip the wheel, run the version-agnostic corpus against it in an isolated
subprocess, and compare against the in-container candidate and the sealed fixtures.

The provenance gate makes the seal tamper-evident: the exact peeled baseline commit is pinned HERE
(independently of the generator), and every sealed input — wheel, corpus fixture, public-surface
fixture, the oracle, and the generator itself — is sha256-verified against the provenance record.
Editing any of them without an explicit host reseal (``invocation_differential.py --seal``) fails
loudly. The pydantic pin is re-checked against the interpreter actually running this gate, because
corpus error strings embed the pydantic version.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from invocation_oracle import compare_records, public_surface_snapshot, run_corpus

pytestmark = [pytest.mark.unit]

_HERE = Path(__file__).resolve().parent
_FIXTURES = _HERE / "fixtures"
_FIXTURE = _FIXTURES / "invocation_baseline_v0_10_1.json"
_PUBLIC_FIXTURE = _FIXTURES / "public_surface_v0_10_1.json"
_WHEEL_FIXTURE = _FIXTURES / "baseline_wheel_v0_10_1.whl"
_PROVENANCE = _FIXTURES / "invocation_baseline_v0_10_1.provenance.json"
_ORACLE = _HERE / "invocation_oracle.py"
_GENERATOR = _HERE / "invocation_differential.py"
# Pinned INDEPENDENTLY of the generator: laundering a moved baseline tag through a reseal would
# also require editing this constant — a visible source change, not a quiet fixture swap.
_EXPECTED_COMMIT = "873a109559aface088cffb078bcf288d7cbc76d5"

_RESEAL_HINT = (
    "the sealed v0.10.1 baseline is missing or stale — reseal on a host with git:\n"
    "  python3 packages/ai_workflow_engine/tests/invocation_differential.py --seal"
)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_provenance() -> dict:
    if not _PROVENANCE.exists():
        pytest.fail(f"provenance record missing; {_RESEAL_HINT}")
    return json.loads(_PROVENANCE.read_text(encoding="utf-8"))


def test_baseline_provenance_is_sealed_and_matches():
    """Every sealed input hash-matches the provenance record; the baseline commit is the pinned
    immutable one; the runtime pydantic matches the seal's pin. Any drift = loud failure."""

    for f in (_FIXTURE, _PUBLIC_FIXTURE, _WHEEL_FIXTURE):
        if not f.exists():
            pytest.fail(f"{f.name} missing; {_RESEAL_HINT}")
    prov = _load_provenance()

    assert prov["baseline_tag"] == "engine-v0.10.1"
    assert prov["peeled_commit"] == _EXPECTED_COMMIT, (
        f"sealed baseline commit {prov['peeled_commit']} != pinned {_EXPECTED_COMMIT} — "
        "the immutable baseline tag appears to have moved"
    )
    assert prov["differential_result"] == "EQUAL", "seal recorded a non-EQUAL differential"

    mismatched = {
        name: (recorded, actual)
        for name, recorded, actual in (
            ("wheel_sha256", prov["wheel_sha256"], _sha256(_WHEEL_FIXTURE)),
            ("fixture_sha256", prov["fixture_sha256"], _sha256(_FIXTURE)),
            ("public_surface_sha256", prov["public_surface_sha256"], _sha256(_PUBLIC_FIXTURE)),
            ("oracle_sha256", prov["oracle_sha256"], _sha256(_ORACLE)),
            ("generator_sha256", prov["generator_sha256"], _sha256(_GENERATOR)),
        )
        if recorded != actual
    }
    assert not mismatched, (
        f"sealed inputs changed WITHOUT a reseal: {sorted(mismatched)} — {_RESEAL_HINT}"
    )

    import pydantic

    assert prov["pydantic_pin"] == pydantic.VERSION, (
        f"seal pydantic {prov['pydantic_pin']} != runtime pydantic {pydantic.VERSION}; corpus "
        f"error strings embed the pydantic version, so the fixture must be resealed for this env"
    )


def _run_against_committed_wheel(tmp_path: Path, runner: str) -> dict:
    """Run a corpus/public runner in a subprocess whose ONLY engine is the committed v0.10.1 wheel:
    unzip the wheel and make it the sole PYTHONPATH entry (the compose PYTHONPATH would otherwise
    shadow it with the candidate tree); third-party deps resolve from the interpreter's own
    site-packages. No git, no network, no pip."""

    lib = tmp_path / "baseline-lib"
    with zipfile.ZipFile(_WHEEL_FIXTURE) as zf:
        zf.extractall(lib)
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    (run_dir / "invocation_oracle.py").write_text(_ORACLE.read_text(encoding="utf-8"), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(lib)
    proc = subprocess.run([sys.executable, "-c", runner], cwd=str(run_dir), env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"baseline-wheel subprocess failed:\n{proc.stderr[-2000:]}"
    out = proc.stdout
    return json.loads(out[out.index("<<<JSON>>>") + len("<<<JSON>>>"): out.index("<<<END>>>")])


_CORPUS_RUNNER = (
    "import asyncio, json\n"
    "from invocation_oracle import run_corpus\n"
    "print('<<<JSON>>>' + json.dumps(asyncio.run(run_corpus()), sort_keys=True) + '<<<END>>>')\n"
)
_PUBLIC_RUNNER = (
    "import json\n"
    "from invocation_oracle import public_surface_snapshot\n"
    "print('<<<JSON>>>' + json.dumps(public_surface_snapshot(), sort_keys=True) + '<<<END>>>')\n"
)


@pytest.mark.asyncio
async def test_live_differential_baseline_wheel_vs_candidate(tmp_path):
    """THE live differential, hermetic and wrapper-run: v0.10.1-wheel corpus == candidate corpus ==
    sealed fixture. This is what proves the sealed fixture is real baseline output, not a
    hand-authored file — the wheel itself is sha-pinned by the provenance gate above."""

    baseline = _run_against_committed_wheel(tmp_path, _CORPUS_RUNNER)
    candidate = await run_corpus()

    wheel_vs_candidate = compare_records(baseline, candidate)
    assert not wheel_vs_candidate, (
        "candidate diverged from the sealed v0.10.1 wheel:\n" + "\n".join(wheel_vs_candidate)
    )
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    fixture_vs_wheel = compare_records(fixture, baseline)
    assert not fixture_vs_wheel, (
        "sealed fixture no longer matches the v0.10.1 wheel it claims to record:\n"
        + "\n".join(fixture_vs_wheel)
    )


def test_live_public_surface_baseline_wheel_vs_candidate(tmp_path):
    """Public exports/signatures/full-schema differential against the committed wheel, plus the
    sealed public fixture — locks API, defaults, constraints, and status literals to v0.10.1."""

    baseline = _run_against_committed_wheel(tmp_path, _PUBLIC_RUNNER)
    candidate = public_surface_snapshot()

    wheel_vs_candidate = compare_records({"public": baseline}, {"public": candidate})
    assert not wheel_vs_candidate, (
        "public surface diverged from the sealed v0.10.1 wheel:\n" + "\n".join(wheel_vs_candidate)
    )
    fixture = json.loads(_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    fixture_vs_wheel = compare_records({"public": fixture}, {"public": baseline})
    assert not fixture_vs_wheel, (
        "sealed public fixture no longer matches the v0.10.1 wheel:\n" + "\n".join(fixture_vs_wheel)
    )
