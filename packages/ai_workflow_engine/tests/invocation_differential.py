"""Pinned baseline SEALER for the v0.11 preservation oracle (Phase I1.R3).

This is the ONLY sanctioned way to (re)generate the sealed ``engine-v0.10.1`` baseline. It:

1. verifies the ``engine-v0.10.1`` tag still peels to the immutable commit ``_EXPECTED_COMMIT``
   (a moved/retagged baseline refuses to seal);
2. materializes that exact commit read-only via ``git archive`` (NO worktree add/remove — the
   working repo is never mutated) and builds the baseline wheel from it;
3. builds a candidate wheel from the current tree;
4. installs each wheel into a SEPARATE venv with ``pydantic`` pinned to the version the test
   container runs (parsed from ``environment.yml`` — corpus error strings embed the pydantic
   version, so seal-env and gate-env must match), copies the version-agnostic corpus in, and runs
   it in an isolated subprocess so module caches / ContextVars / package globals never mix;
5. requires baseline-wheel records == candidate-wheel records (EQUAL) before sealing; and
6. with ``--seal`` writes, FROM THE BASELINE WHEEL'S OWN OUTPUT (v0.10.1 is authoritative — the
   candidate can never redefine baseline truth):
     - ``fixtures/invocation_baseline_v0_10_1.json``   (canonical corpus records)
     - ``fixtures/public_surface_v0_10_1.json``        (exports/signatures/full JSON schemas)
     - ``fixtures/baseline_wheel_v0_10_1.whl``         (the baseline wheel itself)
     - ``fixtures/invocation_baseline_v0_10_1.provenance.json`` (tag, peeled commit, wheel sha256,
       python + pydantic versions, generator/oracle/fixture sha256s, differential result)

Division of labor (the test image has no git and cannot build the tagged baseline):
  - THIS host tool does the git-pinned build + seal. It uses the network only to resolve build/
    runtime deps into its scratch venvs — never inside the test wrapper.
  - The PER-RUN GATE is hermetic and runs through ``./test.sh``
    (``test_invocation_differential_gate.py``): it re-runs the live differential by unzipping the
    COMMITTED baseline wheel (no git, no network, no pip) against the in-container candidate, and
    verifies every provenance hash, the pinned commit, and the pydantic pin. Any edit to the
    oracle, the generator, a fixture, or the wheel without a reseal fails loudly.

    Reseal:       python3 packages/ai_workflow_engine/tests/invocation_differential.py --seal
    Verify only:  python3 packages/ai_workflow_engine/tests/invocation_differential.py
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]  # tests -> ai_workflow_engine -> packages -> TGHandyUtils
_ENGINE_PKG = _REPO / "packages" / "ai_workflow_engine"
_ORACLE = _HERE / "invocation_oracle.py"
_FIXTURES = _HERE / "fixtures"
_FIXTURE = _FIXTURES / "invocation_baseline_v0_10_1.json"
_PUBLIC_FIXTURE = _FIXTURES / "public_surface_v0_10_1.json"
_WHEEL_FIXTURE = _FIXTURES / "baseline_wheel_v0_10_1.whl"
_PROVENANCE = _FIXTURES / "invocation_baseline_v0_10_1.provenance.json"
_BASELINE_TAG = "engine-v0.10.1"
# The immutable baseline commit. Tags are frozen by project rule; pinning the peeled commit in code
# (and again, independently, in the gate test) means a moved tag cannot be laundered through a
# reseal without a visible source edit.
_EXPECTED_COMMIT = "873a109559aface088cffb078bcf288d7cbc76d5"

# Subprocesses run against whichever wheel is installed in the venv.
_RUNNER = (
    "import asyncio, json, sys\n"
    "from invocation_oracle import run_corpus\n"
    "print('<<<JSON>>>' + json.dumps(asyncio.run(run_corpus()), sort_keys=True) + '<<<END>>>')\n"
)
_PUBLIC_RUNNER = (
    "import json\n"
    "from invocation_oracle import public_surface_snapshot\n"
    "print('<<<JSON>>>' + json.dumps(public_surface_snapshot(), sort_keys=True) + '<<<END>>>')\n"
)


def _compare_records(baseline: dict, candidate: dict) -> list[str]:
    """Pure structural comparator (a copy of the oracle's, inlined so this orchestrator never
    imports ai_workflow_engine — its host env may hold a stale/absent build)."""

    mismatches: list[str] = []

    def _walk(path: str, a, b) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b), key=str):
                if k not in a:
                    mismatches.append(f"{path}.{k}: only in candidate = {b[k]!r}")
                elif k not in b:
                    mismatches.append(f"{path}.{k}: only in baseline = {a[k]!r}")
                else:
                    _walk(f"{path}.{k}", a[k], b[k])
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                mismatches.append(f"{path}: length {len(a)} != {len(b)}")
            for i, (x, y) in enumerate(zip(a, b)):
                _walk(f"{path}[{i}]", x, y)
        elif a != b:
            mismatches.append(f"{path}: {a!r} != {b!r}")

    for name in sorted(set(baseline) | set(candidate)):
        if name not in baseline:
            mismatches.append(f"{name}: scenario only in candidate")
        elif name not in candidate:
            mismatches.append(f"{name}: scenario only in baseline")
        else:
            _walk(name, baseline[name], candidate[name])
    return mismatches


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw)


def _peeled_commit() -> str:
    return _run(["git", "-C", str(_REPO), "rev-parse", f"{_BASELINE_TAG}^{{commit}}"]).stdout.strip()


def _container_pydantic_pin() -> str:
    """The exact pydantic version the test container runs (single source: environment.yml). The
    corpus records pydantic ValidationError strings, which embed the pydantic version — seal-env
    and gate-env MUST agree or the fixture is unusable. Loud failure if the pin is absent."""

    text = (_REPO / "environment.yml").read_text(encoding="utf-8")
    m = re.search(r"pydantic==([0-9][0-9a-zA-Z_.]*)", text)
    if not m:
        raise SystemExit("environment.yml has no exact 'pydantic==' pin — refusing to seal against "
                         "an unpinned container environment")
    return m.group(1)


def _materialize_baseline(work: Path) -> Path:
    """Extract the pinned tag's tree with ``git archive`` (read-only; no worktree mutation). The
    archive is a binary tar stream, so pipe it straight into tar rather than capturing as text."""

    src = work / "baseline-src"
    src.mkdir()
    p1 = subprocess.Popen(["git", "-C", str(_REPO), "archive", _BASELINE_TAG], stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["tar", "-x", "-C", str(src)], stdin=p1.stdout)
    p1.stdout.close()
    p2.communicate()
    if p2.returncode != 0 or not (src / "packages" / "ai_workflow_engine").exists():
        raise SystemExit("git archive | tar failed while materializing the baseline tree")
    return src / "packages" / "ai_workflow_engine"


def _capture_in_venv(py: Path, run_dir: Path, runner: str) -> dict:
    run_dir.mkdir(exist_ok=True)
    (run_dir / "invocation_oracle.py").write_text(_ORACLE.read_text(encoding="utf-8"), encoding="utf-8")
    proc = subprocess.run([str(py), "-c", runner], cwd=str(run_dir),
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit(f"corpus subprocess failed in {run_dir.name}:\n{proc.stderr[-2000:]}")
    out = proc.stdout
    return json.loads(out[out.index("<<<JSON>>>") + len("<<<JSON>>>"): out.index("<<<END>>>")])


def _build_and_capture(label: str, source_dir: Path, work: Path, pydantic_pin: str) -> tuple[dict, dict, Path]:
    """Build a wheel from ``source_dir``, install it into a fresh venv with pydantic pinned to the
    container's version, and run the corpus + public-surface snapshot in isolated subprocesses.
    Returns (corpus_records, public_snapshot, wheel_path)."""

    env_dir = work / f"{label}-env"
    wheels = work / f"{label}-wheels"
    _run([sys.executable, "-m", "venv", str(env_dir)])
    py = env_dir / "bin" / "python"
    _run([str(py), "-m", "pip", "-q", "wheel", "--no-deps", "-w", str(wheels), str(source_dir)])
    whl = next(wheels.glob("ai_workflow_engine-*.whl"))
    _run([str(py), "-m", "pip", "-q", "install", str(whl)])
    _run([str(py), "-m", "pip", "-q", "install", f"pydantic=={pydantic_pin}"])
    records = _capture_in_venv(py, work / f"{label}-run", _RUNNER)
    public = _capture_in_venv(py, work / f"{label}-public", _PUBLIC_RUNNER)
    return records, public, whl


def _write_seal(baseline: dict, public: dict, wheel: Path, pydantic_pin: str) -> str:
    _FIXTURES.mkdir(parents=True, exist_ok=True)
    _FIXTURE.write_text(json.dumps(baseline, indent=2, sort_keys=True), encoding="utf-8")
    _PUBLIC_FIXTURE.write_text(json.dumps(public, indent=2, sort_keys=True), encoding="utf-8")
    _WHEEL_FIXTURE.write_bytes(wheel.read_bytes())
    wheel_sha = sha256_file(_WHEEL_FIXTURE)
    provenance = {
        "baseline_tag": _BASELINE_TAG,
        "peeled_commit": _peeled_commit(),
        "wheel_filename": _WHEEL_FIXTURE.name,
        "wheel_sha256": wheel_sha,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "pydantic_pin": pydantic_pin,
        "generator_sha256": sha256_file(Path(__file__).resolve()),
        "oracle_sha256": sha256_file(_ORACLE),
        "fixture_sha256": sha256_file(_FIXTURE),
        "public_surface_sha256": sha256_file(_PUBLIC_FIXTURE),
        "differential_result": "EQUAL",
        "sealed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "how_to_verify": (
            "rerun this generator (verify mode) on a host with git: it re-peels the tag, rebuilds "
            "both wheels, and re-compares; or unzip the committed wheel and diff its *.py files "
            "against `git archive engine-v0.10.1` (zip metadata timestamps differ per build; "
            "sources must be identical)."
        ),
    }
    _PROVENANCE.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")
    return wheel_sha


def main() -> int:
    seal = "--seal" in sys.argv[1:]
    peeled = _peeled_commit()
    if peeled != _EXPECTED_COMMIT:
        print(f"BASELINE TAG MOVED: {_BASELINE_TAG} -> {peeled} (expected {_EXPECTED_COMMIT})")
        return 1
    pydantic_pin = _container_pydantic_pin()

    with tempfile.TemporaryDirectory(prefix="invoke-diff-") as tmp:
        work = Path(tmp)
        baseline_src = _materialize_baseline(work)
        baseline, base_public, base_whl = _build_and_capture("baseline", baseline_src, work, pydantic_pin)
        candidate, cand_public, _ = _build_and_capture("candidate", _ENGINE_PKG, work, pydantic_pin)

        bc = _compare_records(baseline, candidate)
        pc = _compare_records({"public": base_public}, {"public": cand_public})
        print(f"peeled commit                        : {peeled} (== expected)")
        print(f"pydantic pin (container-matched)     : {pydantic_pin}")
        print(f"baseline-wheel vs candidate corpus   : {'EQUAL' if not bc else str(len(bc)) + ' MISMATCHES'}")
        print(f"baseline-wheel vs candidate public   : {'EQUAL' if not pc else str(len(pc)) + ' MISMATCHES'}")
        for m in (bc + pc)[:40]:
            print("  -", m)
        if bc or pc:
            print("\nDIFFERENTIAL FAILED — candidate diverged from the immutable v0.10.1 wheel.")
            return 1

        if seal:
            wheel_sha = _write_seal(baseline, base_public, base_whl, pydantic_pin)
            print(f"\nSEALED from the baseline wheel: {_FIXTURE.name}, {_PUBLIC_FIXTURE.name}, "
                  f"{_WHEEL_FIXTURE.name} (sha256 {wheel_sha[:12]}…), {_PROVENANCE.name}")
            return 0

    # verify-only: the on-disk seal must already equal the freshly built baseline truth.
    problems: list[str] = []
    if not _FIXTURE.exists() or not _PUBLIC_FIXTURE.exists() or not _PROVENANCE.exists():
        print("\nNO COMPLETE SEAL ON DISK — run with --seal to generate it.")
        return 1
    bf = _compare_records(json.loads(_FIXTURE.read_text(encoding="utf-8")), baseline)
    pf = _compare_records({"public": json.loads(_PUBLIC_FIXTURE.read_text(encoding="utf-8"))},
                          {"public": base_public})
    print(f"sealed corpus fixture vs baseline    : {'EQUAL' if not bf else str(len(bf)) + ' MISMATCHES'}")
    print(f"sealed public fixture vs baseline    : {'EQUAL' if not pf else str(len(pf)) + ' MISMATCHES'}")
    for m in (bf + pf)[:40]:
        print("  -", m)
    if bf or pf:
        print("\nDIFFERENTIAL FAILED — the sealed fixtures no longer match the v0.10.1 wheel.")
        return 1
    print("\nDIFFERENTIAL PASSED — candidate, baseline wheel, and sealed fixtures all agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
