"""Current-contract SEALER for the v0.11 clean-contract line (Phase 1A, manifest row M1).

Replaces the retired v0.10.1 wheel-equality differential as the oracle's authority: the sealed
fixture now records the CURRENT contract's canonical corpus + public surface, and integrity comes
from a provenance hash chain + explicit-only resealing + a machine-readable intentional-break
ledger — not from equality with an old wheel.

What a seal run does:

1. builds a candidate wheel from the working tree and installs it into a fresh venv with
   ``pydantic`` pinned to the test container's version (parsed from ``environment.yml`` — corpus
   error strings embed the pydantic version, so seal-env and gate-env must match);
2. runs the version-agnostic corpus TWICE in that venv and requires byte-equal canonical results
   (seal-time metamorphic determinism), plus the public-surface snapshot;
3. diffs the new records against the PREVIOUS sealed fixture (the current-contract fixture if it
   exists, else the retained historical v0.10.1 fixture for the transition seal) and REFUSES to
   write if any delta path is not covered by
   ``fixtures/v011_intentional_break_ledger.json`` — every unledgered delta is printed. Resealing
   can therefore never silently bless a regression;
4. writes ``fixtures/invocation_current_contract.json``, ``fixtures/public_surface_current.json``
   and ``fixtures/current_contract.provenance.json`` hashing the fixture, public fixture, corpus
   module, this sealer, and the ledger, plus the python/pydantic pins.

The per-run GATE stays hermetic inside ``./test.sh`` (``test_current_contract_gate.py``): it
re-hashes every sealed input and compares the live corpus to the fixture. This sealer is a host
provenance utility, not pytest:

    Reseal:  python3 packages/ai_workflow_engine/tests/current_contract_seal.py --seal
    Verify:  python3 packages/ai_workflow_engine/tests/current_contract_seal.py
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
_REPO = _HERE.parents[2]
_ENGINE_PKG = _REPO / "packages" / "ai_workflow_engine"
_ORACLE = _HERE / "invocation_oracle.py"
_FIXTURES = _HERE / "fixtures"
_FIXTURE = _FIXTURES / "invocation_current_contract.json"
_PUBLIC_FIXTURE = _FIXTURES / "public_surface_current.json"
_PROVENANCE = _FIXTURES / "current_contract.provenance.json"
_LEDGER = _FIXTURES / "v011_intentional_break_ledger.json"
# Transition predecessor (retained historical files; deleted in Phase 2C after delta closeout).
_HISTORICAL_FIXTURE = _FIXTURES / "invocation_baseline_v0_10_1.json"
_HISTORICAL_PUBLIC = _FIXTURES / "public_surface_v0_10_1.json"

_RUNNER = (
    "import asyncio, json\n"
    "from invocation_oracle import run_corpus\n"
    "print('<<<JSON>>>' + json.dumps(asyncio.run(run_corpus()), sort_keys=True) + '<<<END>>>')\n"
)
_PUBLIC_RUNNER = (
    "import json\n"
    "from invocation_oracle import public_surface_snapshot\n"
    "print('<<<JSON>>>' + json.dumps(public_surface_snapshot(), sort_keys=True) + '<<<END>>>')\n"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_records(baseline: dict, candidate: dict) -> list[str]:
    """Pure structural comparator (inlined so this host utility never imports the engine)."""

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


def load_ledger(path: Path = _LEDGER) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise SystemExit("intentional-break ledger malformed: 'entries' must be a list")
    for e in entries:
        if not isinstance(e, dict) or not e.get("path_prefix") or not e.get("reason") \
                or not e.get("manifest_row"):
            raise SystemExit(f"ledger entry missing path_prefix/reason/manifest_row: {e!r}")
    return entries


def partition_deltas(mismatches: list[str], entries: list[dict]) -> tuple[list[str], list[str]]:
    """Split fixture deltas into (ledgered, unledgered). A delta is ledgered when its path — the
    text before the first ': ' — starts with any entry's path_prefix."""

    ledgered: list[str] = []
    unledgered: list[str] = []
    for m in mismatches:
        path = m.split(": ", 1)[0]
        if any(path.startswith(e["path_prefix"]) for e in entries):
            ledgered.append(m)
        else:
            unledgered.append(m)
    return ledgered, unledgered


def _container_pydantic_pin() -> str:
    text = (_REPO / "environment.yml").read_text(encoding="utf-8")
    m = re.search(r"pydantic==([0-9][0-9a-zA-Z_.]*)", text)
    if not m:
        raise SystemExit("environment.yml has no exact 'pydantic==' pin — refusing to seal against "
                         "an unpinned container environment")
    return m.group(1)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw)


def _capture(py: Path, run_dir: Path, runner: str) -> dict:
    run_dir.mkdir(exist_ok=True)
    (run_dir / "invocation_oracle.py").write_text(_ORACLE.read_text(encoding="utf-8"), encoding="utf-8")
    proc = subprocess.run([str(py), "-c", runner], cwd=str(run_dir),
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit(f"corpus subprocess failed in {run_dir.name}:\n{proc.stderr[-2000:]}")
    out = proc.stdout
    return json.loads(out[out.index("<<<JSON>>>") + len("<<<JSON>>>"): out.index("<<<END>>>")])


def _build_candidate_env(work: Path, pydantic_pin: str) -> Path:
    env_dir = work / "candidate-env"
    wheels = work / "candidate-wheels"
    _run([sys.executable, "-m", "venv", str(env_dir)])
    py = env_dir / "bin" / "python"
    _run([str(py), "-m", "pip", "-q", "wheel", "--no-deps", "-w", str(wheels), str(_ENGINE_PKG)])
    whl = next(wheels.glob("ai_workflow_engine-*.whl"))
    _run([str(py), "-m", "pip", "-q", "install", str(whl)])
    _run([str(py), "-m", "pip", "-q", "install", f"pydantic=={pydantic_pin}"])
    return py


def _predecessor_fixture() -> tuple[Path, str] | tuple[None, None]:
    if _FIXTURE.exists():
        return _FIXTURE, "current-contract fixture"
    if _HISTORICAL_FIXTURE.exists():
        return _HISTORICAL_FIXTURE, "historical v0.10.1 fixture (transition seal)"
    return None, None


def main() -> int:
    seal = "--seal" in sys.argv[1:]
    pydantic_pin = _container_pydantic_pin()
    entries = load_ledger()

    with tempfile.TemporaryDirectory(prefix="current-seal-") as tmp:
        work = Path(tmp)
        py = _build_candidate_env(work, pydantic_pin)
        first = _capture(py, work / "run-1", _RUNNER)
        second = _capture(py, work / "run-2", _RUNNER)
        determinism = compare_records(first, second)
        print(f"pydantic pin (container-matched)   : {pydantic_pin}")
        print(f"two-run determinism                : {'EQUAL' if not determinism else str(len(determinism)) + ' MISMATCHES'}")
        for m in determinism[:20]:
            print("  -", m)
        if determinism:
            print("\nSEAL REFUSED — the corpus is not deterministic; fix jitter before sealing.")
            return 1
        public = _capture(py, work / "run-public", _PUBLIC_RUNNER)

    predecessor, label = _predecessor_fixture()
    if predecessor is not None:
        deltas = compare_records(json.loads(predecessor.read_text(encoding="utf-8")), first)
        ledgered, unledgered = partition_deltas(deltas, entries)
        print(f"deltas vs {label}: {len(deltas)} ({len(ledgered)} ledgered / {len(unledgered)} unledgered)")
        for m in unledgered[:40]:
            print("  UNLEDGERED -", m)
        if unledgered:
            print("\nSEAL REFUSED — every fixture delta must match an intentional-break ledger "
                  "entry (fixtures/v011_intentional_break_ledger.json). Ledger the break with its "
                  "manifest row, or fix the regression.")
            return 1

    if not seal:
        if not _FIXTURE.exists():
            print("\nNO CURRENT-CONTRACT SEAL — run with --seal to create it.")
            return 1
        drift = compare_records(json.loads(_FIXTURE.read_text(encoding="utf-8")), first)
        print(f"sealed fixture vs live corpus      : {'EQUAL' if not drift else str(len(drift)) + ' MISMATCHES'}")
        for m in drift[:40]:
            print("  -", m)
        if drift:
            print("\nVERIFY FAILED — live corpus diverged from the sealed current contract.")
            return 1
        print("\nVERIFY PASSED.")
        return 0

    _FIXTURES.mkdir(parents=True, exist_ok=True)
    _FIXTURE.write_text(json.dumps(first, indent=2, sort_keys=True), encoding="utf-8")
    _PUBLIC_FIXTURE.write_text(json.dumps(public, indent=2, sort_keys=True), encoding="utf-8")
    provenance = {
        "contract": "v0.11 current-contract seal",
        "fixture_sha256": sha256_file(_FIXTURE),
        "public_surface_sha256": sha256_file(_PUBLIC_FIXTURE),
        "corpus_sha256": sha256_file(_ORACLE),
        "sealer_sha256": sha256_file(Path(__file__).resolve()),
        "ledger_sha256": sha256_file(_LEDGER),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "pydantic_pin": pydantic_pin,
        "determinism": "two-run-equal",
        "sealed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "how_to_verify": (
            "re-run this sealer in verify mode (rebuilds the candidate wheel and compares to the "
            "sealed fixture); the hermetic per-run gate re-hashes every sealed input and compares "
            "the live corpus inside ./test.sh."
        ),
    }
    _PROVENANCE.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nSEALED current contract: {_FIXTURE.name}, {_PUBLIC_FIXTURE.name}, {_PROVENANCE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
