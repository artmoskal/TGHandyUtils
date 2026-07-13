"""Live baseline-wheel-vs-candidate-wheel semantic differential (Phase I1.0 task 2).

This is the INTEGRITY backbone of the preservation oracle: it builds the immutable
``engine-v0.10.1`` baseline wheel AND the candidate wheel from the current tree, installs each
into a SEPARATE venv, copies the version-agnostic corpus into each, and runs it in an isolated
subprocess so Python module caches / ContextVars / package globals can never mix versions. Then
it compares baseline-wheel records vs candidate-wheel records, and baseline-wheel records vs the
committed fixture — so a hand-edited fixture cannot grant a PASS.

Gate infrastructure, NOT a per-run unit test: it builds wheels + venvs (~2-3 min). Run at each
phase boundary:

    python3 packages/ai_workflow_engine/tests/invocation_differential.py

Exit 0 == baseline wheel, candidate wheel, and committed fixture all agree.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]  # tests -> ai_workflow_engine -> packages -> TGHandyUtils
_ENGINE_PKG = _REPO / "packages" / "ai_workflow_engine"
_ORACLE = _HERE / "invocation_oracle.py"
_FIXTURE = _HERE / "fixtures" / "invocation_baseline_v0_10_1.json"
_BASELINE_TAG = "engine-v0.10.1"

# The subprocess that runs the corpus against whichever wheel is installed in its venv.
_RUNNER = (
    "import asyncio, json, sys\n"
    "from invocation_oracle import run_corpus\n"
    "print('<<<JSON>>>' + json.dumps(asyncio.run(run_corpus()), sort_keys=True) + '<<<END>>>')\n"
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


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw)


def _build_and_capture(label: str, source_dir: Path, work: Path) -> dict:
    """Build a wheel from ``source_dir``, install into a fresh venv, run the corpus in isolation."""

    env_dir = work / f"{label}-env"
    wheels = work / f"{label}-wheels"
    _run([sys.executable, "-m", "venv", str(env_dir)])
    py = env_dir / "bin" / "python"
    _run([str(py), "-m", "pip", "-q", "install", "--upgrade", "pip", "wheel"])
    _run([str(py), "-m", "pip", "-q", "wheel", "--no-deps", "-w", str(wheels), str(source_dir)])
    whl = next(wheels.glob("ai_workflow_engine-*.whl"))
    _run([str(py), "-m", "pip", "-q", "install", str(whl)])
    # copy the version-agnostic corpus next to the runner
    (work / f"{label}-run").mkdir(exist_ok=True)
    run_dir = work / f"{label}-run"
    (run_dir / "invocation_oracle.py").write_text(_ORACLE.read_text(encoding="utf-8"), encoding="utf-8")
    proc = subprocess.run(
        [str(py), "-c", _RUNNER], cwd=str(run_dir), capture_output=True, text=True, timeout=300
    )
    if proc.returncode != 0:
        raise SystemExit(f"[{label}] corpus subprocess failed:\n{proc.stderr[-2000:]}")
    out = proc.stdout
    payload = out[out.index("<<<JSON>>>") + len("<<<JSON>>>"): out.index("<<<END>>>")]
    return json.loads(payload)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="invoke-diff-") as tmp:
        work = Path(tmp)
        # 1) baseline wheel from the immutable tag (isolated worktree)
        baseline_src = work / "baseline-src"
        _run(["git", "-C", str(_REPO), "worktree", "add", "--detach", str(baseline_src), _BASELINE_TAG])
        try:
            baseline = _build_and_capture("baseline", baseline_src / "packages" / "ai_workflow_engine", work)
        finally:
            subprocess.run(["git", "-C", str(_REPO), "worktree", "remove", "--force", str(baseline_src)])
            subprocess.run(["git", "-C", str(_REPO), "worktree", "prune"])
        # 2) candidate wheel from the current tree
        candidate = _build_and_capture("candidate", _ENGINE_PKG, work)

    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    bc = _compare_records(baseline, candidate)
    bf = _compare_records(fixture, baseline)
    print(f"baseline-wheel vs candidate-wheel : {'EQUAL' if not bc else str(len(bc)) + ' MISMATCHES'}")
    print(f"committed-fixture vs baseline-wheel: {'EQUAL' if not bf else str(len(bf)) + ' MISMATCHES'}")
    for m in (bc + bf)[:40]:
        print("  -", m)
    if bc or bf:
        print("\nDIFFERENTIAL FAILED — candidate diverged from the immutable v0.10.1 wheel.")
        return 1
    print("\nDIFFERENTIAL PASSED — candidate wheel, baseline wheel, and sealed fixture all agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
