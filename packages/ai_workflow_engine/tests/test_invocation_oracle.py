"""Preservation gate for the v0.11 invocation refactor (Iteration 1).

The fast in-process gate: run the deterministic corpus through the public door and compare it,
path-by-path, against the SEALED v0.10.1 canonical baseline fixture. Any structural difference is
a behavior delta and fails. The live baseline-wheel-vs-candidate-wheel differential (proving the
fixture was not hand-edited) is a separate gate script run at each phase boundary.

Also here: the behavior-inventory completeness guard, and the comparator/canonicalizer self-tests
(the oracle is load-bearing code — it must FAIL on real drift). Regenerate the sealed baseline —
ONLY from the immutable v0.10.1 production code — with ORACLE_REGEN=1.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from invocation_oracle import (
    compare_records,
    missing_behavior_rows,
    run_corpus,
)

pytestmark = [pytest.mark.unit]  # async tests are marked individually — do not mark sync tests async

_FIXTURE = Path(__file__).parent / "fixtures" / "invocation_baseline_v0_10_1.json"


def test_behavior_inventory_is_complete():
    missing = missing_behavior_rows()
    assert missing == [], f"behavior-inventory rows without a scenario: {missing}"


@pytest.mark.asyncio
async def test_corpus_matches_sealed_v0_10_1_baseline():
    records = await run_corpus()
    if os.environ.get("ORACLE_REGEN") == "1" or not _FIXTURE.exists():
        _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        _FIXTURE.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
        pytest.skip(f"regenerated sealed baseline at {_FIXTURE}")
    baseline = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    mismatches = compare_records(baseline, records)
    assert not mismatches, "candidate diverged from the sealed v0.10.1 baseline:\n" + "\n".join(mismatches)


def test_comparator_and_canonicalizer_detect_real_drift():
    """The oracle is load-bearing: prove the comparator FAILS on every category of drift it must
    catch (>= 5 oracle mutations). A comparator that passes drift would silently bless a refactor."""

    base = {
        "s": {
            "result": {"kind": "result", "status": "accepted", "output": {"a": 1},
                       "error": None, "artifact_count": 0, "artifact_kinds": [], "metadata": {}},
            "exception": None,
            "handler_calls": 1,
            "trace": [
                {"node": "n", "attempt": 1, "phase": "tool:request", "decision": "start",
                 "severity": "info", "error": None, "metadata": {}, "detail_ref_count": 0,
                 "artifact_ref_count": 0},
                {"node": "n", "attempt": 1, "phase": "tool:result", "decision": "accepted",
                 "severity": "info", "error": None, "metadata": {}, "detail_ref_count": 0,
                 "artifact_ref_count": 0},
            ],
            "details": [],
        }
    }

    def mutated(fn):
        import copy
        c = copy.deepcopy(base)
        fn(c["s"])
        return compare_records(base, c)

    # 1) status change
    assert mutated(lambda s: s["result"].__setitem__("status", "failed"))
    # 2) output change
    assert mutated(lambda s: s["result"].__setitem__("output", {"a": 2}))
    # 3) error text change
    assert mutated(lambda s: s["result"].__setitem__("error", "new"))
    # 4) handler call count change
    assert mutated(lambda s: s.__setitem__("handler_calls", 2))
    # 5) event removed
    assert mutated(lambda s: s["trace"].pop())
    # 6) event reordered
    assert mutated(lambda s: s["trace"].reverse())
    # 7) event decision change
    assert mutated(lambda s: s["trace"][1].__setitem__("decision", "partial"))
    # 8) detail-ref count change
    assert mutated(lambda s: s["trace"][1].__setitem__("detail_ref_count", 3))
    # 9) a NEW candidate field is a difference, not silently ignored
    assert mutated(lambda s: s["result"].__setitem__("brand_new_field", True))
    # 10) a scenario appearing only on one side is caught
    assert compare_records(base, {**base, "extra": {"x": 1}})
    # a truly-equal comparison yields no mismatches
    import copy as _c
    assert compare_records(base, _c.deepcopy(base)) == []


@pytest.mark.asyncio
async def test_canonicalizer_strips_only_approved_volatile_facts():
    """Two corpus runs must be equal after canonicalization (ids/timestamps/elapsed differ each
    run), proving the canonicalizer neutralizes intrinsically-volatile facts and nothing else."""

    a = await run_corpus()
    b = await run_corpus()
    mismatches = compare_records(a, b)
    assert not mismatches, "two runs of the same code must canonicalize equal:\n" + "\n".join(mismatches)


# --------------------------------------------------------------------------------------
# Public-surface + simple-tier import-gradient locks (Phase I1.0 task 4). A behavior-preserving
# refactor must not drift the public API, signatures, or Pydantic schemas, and must not pull
# advanced modules into the simple one-step tier.
# --------------------------------------------------------------------------------------

import inspect  # noqa: E402

_PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "public_surface_v0_10_1.json"


def _public_surface_snapshot() -> dict:
    import ai_workflow_engine as pkg
    from ai_workflow_engine.engine.capabilities import CapabilityRuntime
    from ai_workflow_engine.models import CapabilityResult, CapabilitySpec

    def _sig(obj) -> str:
        try:
            return str(inspect.signature(obj))
        except (TypeError, ValueError):
            return "<no-signature>"

    return {
        "exports": sorted(getattr(pkg, "__all__", []) or [n for n in dir(pkg) if not n.startswith("_")]),
        "signatures": {
            "CapabilityRuntime.__init__": _sig(CapabilityRuntime.__init__),
            "CapabilityRuntime.invoke": _sig(CapabilityRuntime.invoke),
        },
        "schemas": {
            "CapabilitySpec": sorted(CapabilitySpec.model_json_schema().get("properties", {})),
            "CapabilityResult": sorted(CapabilityResult.model_json_schema().get("properties", {})),
        },
    }


def test_public_surface_is_locked_against_v0_10_1():
    snapshot = _public_surface_snapshot()
    if not _PUBLIC_FIXTURE.exists():
        _PUBLIC_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        _PUBLIC_FIXTURE.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
        pytest.skip(f"sealed public surface at {_PUBLIC_FIXTURE}")
    sealed = json.loads(_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    mismatches = compare_records({"public": sealed}, {"public": snapshot})
    assert not mismatches, "public surface drifted from v0.10.1:\n" + "\n".join(mismatches)


def test_simple_tier_loads_no_advanced_modules():
    """A one-step deterministic run must not import advanced tiers (waits/memory/flow-authoring/
    viewer/tools). Checked in a FRESH interpreter so prior test imports do not pollute the set."""

    import subprocess
    import sys

    script = (
        "import asyncio, sys\n"
        "from ai_workflow_engine import WorkflowBuilder, WorkflowEngineBuilder\n"
        "e=(WorkflowEngineBuilder()"
        ".register_capability('n', lambda c,p: {'ok':1}, kind='deterministic')"
        ".register_workflow(WorkflowBuilder('w').step('n').build()).build())\n"
        "asyncio.run(e.run('w', {}))\n"
        # a REAL advanced import, not pip's editable-install finder machinery
        "_ADV=('ai_workflow_engine.waits','ai_workflow_engine.memory',"
        "'ai_workflow_engine.flow_authoring','ai_workflow_viewer','ai_workflow_tools')\n"
        "adv=[m for m in sys.modules if not m.startswith('__editable__') and "
        "any(m==t or m.startswith(t+'.') for t in _ADV)]\n"
        "print('ADVANCED=' + ','.join(sorted(adv)))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"simple-tier run failed: {out.stderr[-800:]}"
    line = next((l for l in out.stdout.splitlines() if l.startswith("ADVANCED=")), "ADVANCED=")
    loaded = [m for m in line[len("ADVANCED="):].split(",") if m]
    assert not loaded, f"simple tier pulled advanced modules: {loaded}"
