"""Preservation gate for the v0.11 invocation refactor (Iteration 1).

The fast in-process gate: run the deterministic corpus through the public door and compare it,
path-by-path, against the SEALED CURRENT-CONTRACT fixture (v0.11 clean-contract line; the old
v0.10.1 wheel-equality gate is retired — final run recorded EQUAL x4 at 53ef867). Any structural
difference is a behavior delta and fails. A MISSING fixture is a hard failure, never a silent
regeneration: the only sanctioned (re)generation path is the explicit host sealer
(``current_contract_seal.py --seal``), which requires two-run determinism and refuses any fixture
delta not covered by the intentional-break ledger. Provenance verification lives in
``test_current_contract_gate.py``.

Also here: the behavior-inventory completeness guard and the comparator/canonicalizer self-tests
(the oracle is load-bearing code — it must FAIL on real drift and must NOT normalize anything
beyond the single reviewed exact-path volatile fact).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from invocation_oracle import (
    _EXACT_PATH_NORMALIZERS,
    _canon,
    compare_records,
    missing_behavior_rows,
    public_surface_snapshot,
    run_corpus,
)

pytestmark = [pytest.mark.unit]  # async tests are marked individually — do not mark sync tests async

_FIXTURE = Path(__file__).parent / "fixtures" / "invocation_current_contract.json"

_RESEAL_HINT = (
    "current-contract seal missing — regeneration is EXPLICIT-ONLY via the host sealer:\n"
    "  python3 packages/ai_workflow_engine/tests/current_contract_seal.py --seal"
)


# The ACCEPTED plan inventory (plan §"Behavior Inventory To Freeze"), transcribed here
# INDEPENDENTLY of the oracle so the completeness check can never validate the implementation's
# own list against itself (codex I1 finding 4). Every plan row maps to the named scenario key(s);
# removing a scenario fails BY PLAN ROW NAME.
_ACCEPTED_PLAN_INVENTORY: dict[str, list[str]] = {
    "unknown capability": ["unknown_capability"],
    "invalid input": ["invalid_input"],
    "side effect denied": ["side_effect_denied"],
    "budget denied": ["budget_denied", "worker_call_accounting", "usage_event_semantics"],
    "sync success": ["sync_success"],
    "async success": ["async_success"],
    "explicit partial result": ["explicit_partial"],
    "invalid output": ["invalid_output"],
    "handler exception": ["handler_exception"],
    "handler-owned TimeoutError": ["handler_owned_timeout"],
    "cooperative deadline": ["cooperative_deadline"],
    "caller cancellation": ["caller_cancellation", "bounded_caller_cancellation"],
    "swallowed cancellation": ["swallowed_cancellation", "unacknowledged_cancellation"],
    "process timeout": ["process_timeout"],
    "artifact result": ["artifact_result"],
    "capture off": ["capture_off"],
    "capture full": ["capture_full"],
    "trace sink failure": ["trace_sink_failure"],
    "detail sink failure": ["detail_sink_failure"],
    "nested invocation": ["nested_invocation"],
    "concurrent invocations": ["concurrent_invocations"],
    "authored flow/fanout": ["authored_fanout_flow"],
    "durable suspend/resume": ["durable_suspend_resume"],
    # Iteration B / B0 (v0.11.3): human nodes go through the bound door — the row freezes all
    # four node decorations composed with provenance/criticism/resume across two suspensions.
    "human bound context": ["human_bound_context"],
}


def test_behavior_inventory_is_complete():
    """Bijection against the ACCEPTED plan inventory: every plan row has its scenario(s), every
    scenario belongs to a plan row, and the oracle's own row list agrees — three-way, so neither
    the oracle nor this test can drift alone."""

    from invocation_oracle import BEHAVIOR_ROWS, SCENARIOS

    rows_missing_scenarios = {
        row: [key for key in keys if key not in SCENARIOS]
        for row, keys in _ACCEPTED_PLAN_INVENTORY.items()
        if any(key not in SCENARIOS for key in keys)
    }
    assert not rows_missing_scenarios, (
        f"accepted plan rows without their scenario(s): {rows_missing_scenarios}"
    )

    mapped = {key for keys in _ACCEPTED_PLAN_INVENTORY.values() for key in keys}
    unmapped = set(SCENARIOS) - mapped
    assert not unmapped, f"scenarios not mapped to any accepted plan row: {sorted(unmapped)}"

    assert set(BEHAVIOR_ROWS) == set(SCENARIOS), (
        "oracle BEHAVIOR_ROWS drifted from its scenario set: "
        f"{sorted(set(BEHAVIOR_ROWS) ^ set(SCENARIOS))}"
    )
    assert missing_behavior_rows() == []


@pytest.mark.asyncio
async def test_corpus_matches_sealed_current_contract():
    if not _FIXTURE.exists():
        pytest.fail(_RESEAL_HINT)
    records = await run_corpus()
    baseline = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    mismatches = compare_records(baseline, records)
    assert not mismatches, "live corpus diverged from the sealed current contract:\n" + "\n".join(mismatches)


def test_comparator_and_canonicalizer_detect_real_drift():
    """The oracle is load-bearing: prove the comparator FAILS on every category of drift it must
    catch — control, trace, the reference GRAPH (event<->detail<->artifact edges), full detail
    payload/digest, artifact fields, usage classification/summary, and schema default/constraint.
    A comparator that passes any of these would silently bless a behavior-changing refactor."""

    base = {
        "s": {
            "result": {
                "kind": "result", "status": "accepted", "output": {"a": 1}, "error": None,
                "artifacts": [{"artifact": "A0", "path": "a.png", "kind": "media", "source": "x",
                               "owner_node": "art", "cleanup_on_failure": True, "metadata": {}}],
                "metadata": {},
            },
            "exception": None,
            "handler_calls": 1,
            "trace": [
                {"node": "n", "attempt": 1, "phase": "tool:request", "decision": "start",
                 "node_status": None, "severity": "info", "error": None, "metadata": {},
                 "event": "E0", "detail_refs": ["D0"], "artifacts": []},
                {"node": "n", "attempt": 1, "phase": "tool:result", "decision": "accepted",
                 "node_status": None, "severity": "info", "error": None, "metadata": {},
                 "event": "E1", "detail_refs": ["D1"], "artifacts": ["A0"]},
            ],
            "details": [
                {"kind": "tool_payload", "privacy": "internal", "redaction_state": "none",
                 "content_type": "application/json", "event": "E0", "artifact": None,
                 "text": "{payload}", "json": {"capability": "n", "payload": {"a": 1}},
                 "metadata": {}, "has_digest": True, "digest_consistent": True},
            ],
            "usage": {
                "worker_call_count": 1, "text_call_count": 0, "image_call_count": 0,
                "tool_call_count": 0, "tool_character_count": 0,
                "input_tokens": 1, "output_tokens": 2, "total_tokens": 3,
                "cached_input_tokens": 1, "metered_usd": None, "notional_usd": None,
                "estimated_usd": None,
                # full semantic event shape (model_dump minus the reviewed volatile drop-list)
                "events": [{"provider": "openai", "operation": "chat", "cost_class": "metered",
                            "node": "n", "run_id": "oracle-run", "sequence": None, "model": "m",
                            "attempt": 2, "input_tokens": 1, "output_tokens": 2, "total_tokens": 3,
                            "input_token_details": {"cached_tokens": 1},
                            "output_token_details": {"reasoning_tokens": 1},
                            "estimated_usd": 0.01, "notional_usd": None, "request_id": "req-1",
                            "elapsed_ms": 7, "success": True, "error": None,
                            "metadata": {"workflow_type": "oracle"}}],
            },
            # schema-shaped block: the SAME comparator walks full JSON schemas in the public-surface
            # lock, so a default/constraint change there fails identically to these leaf mutations.
            "schema": {"properties": {"x": {"type": "integer", "default": 0, "maximum": 10}}},
        }
    }

    def mutated(fn):
        import copy
        c = copy.deepcopy(base)
        fn(c["s"])
        return compare_records(base, c)

    # --- control + trace ---
    assert mutated(lambda s: s["result"].__setitem__("status", "failed"))          # status literal
    assert mutated(lambda s: s["result"].__setitem__("output", {"a": 2}))          # output value
    assert mutated(lambda s: s["result"].__setitem__("error", "new"))              # error text
    assert mutated(lambda s: s.__setitem__("handler_calls", 2))                    # handler-call count
    assert mutated(lambda s: s["trace"].pop())                                     # event removed
    assert mutated(lambda s: s["trace"].reverse())                                 # event reordered
    assert mutated(lambda s: s["trace"][1].__setitem__("decision", "partial"))     # event decision
    # --- reference graph (edges, not counts) ---
    assert mutated(lambda s: s["trace"][0].__setitem__("detail_refs", ["D9"]))     # event->detail relinked
    assert mutated(lambda s: s["trace"][0].__setitem__("detail_refs", ["D0", "D1"]))  # extra link (count too)
    assert mutated(lambda s: s["trace"][1].__setitem__("artifacts", []))           # event->artifact ref dropped
    assert mutated(lambda s: s["details"][0].__setitem__("event", "E1"))           # detail re-parented
    # --- full detail payload + digest ---
    assert mutated(lambda s: s["details"][0]["json"].__setitem__("payload", {"a": 2}))  # payload corruption
    assert mutated(lambda s: s["details"][0].__setitem__("digest_consistent", False))   # digest drift
    assert mutated(lambda s: s["details"][0].__setitem__("redaction_state", "digest_only"))  # redaction
    # --- artifact fields ---
    assert mutated(lambda s: s["result"]["artifacts"][0].__setitem__("path", "b.png"))      # artifact field
    assert mutated(lambda s: s["result"]["artifacts"][0].__setitem__("kind", "file"))       # artifact kind
    # --- usage (summary + EVERY semantic event field codex flagged as dropped) ---
    assert mutated(lambda s: s["usage"].__setitem__("worker_call_count", 2))               # summary value
    assert mutated(lambda s: s["usage"].__setitem__("cached_input_tokens", 9))             # derived tokens
    assert mutated(lambda s: s["usage"].__setitem__("tool_character_count", 9))            # derived tool chars
    assert mutated(lambda s: s["usage"].__setitem__("estimated_usd", 0.99))                # cost alias
    assert mutated(lambda s: s["usage"]["events"][0].__setitem__("cost_class", "subscription_notional"))  # classification
    assert mutated(lambda s: s["usage"]["events"][0].__setitem__("attempt", 3))            # retry attribution
    assert mutated(lambda s: s["usage"]["events"][0]["input_token_details"].__setitem__("cached_tokens", 9))   # token details
    assert mutated(lambda s: s["usage"]["events"][0].__setitem__("estimated_usd", 0.99))   # per-event cost
    assert mutated(lambda s: s["usage"]["events"][0]["metadata"].__setitem__("workflow_type", "other"))  # event metadata
    assert mutated(lambda s: s["usage"]["events"][0].__setitem__("success", False))        # success flip
    assert mutated(lambda s: s["usage"]["events"][0].__setitem__("error", "quota"))        # event error
    # --- typed trace lifecycle (codex I1.R finding 2) ---
    assert mutated(lambda s: s["trace"][1].__setitem__("node_status", "completed"))        # None -> completed
    # --- schema default + constraint ---
    assert mutated(lambda s: s["schema"]["properties"]["x"].__setitem__("default", 1))     # schema default
    assert mutated(lambda s: s["schema"]["properties"]["x"].__setitem__("maximum", 5))     # schema constraint
    # --- structural ---
    assert mutated(lambda s: s["result"].__setitem__("brand_new_field", True))     # NEW field is a diff
    assert compare_records(base, {**base, "extra": {"x": 1}})                      # scenario only one side
    import copy as _c
    assert compare_records(base, _c.deepcopy(base)) == []                          # equal => no mismatches


def test_usage_summary_projection_covers_every_public_aggregate():
    """The P-06 promise includes the summary, not only its source events. Keep every public
    aggregate visible so a derived-total regression cannot compare equal."""

    from ai_workflow_engine.models import WorkflowUsageSummary
    from invocation_oracle import _canon_usage_summary

    projected = _canon_usage_summary(WorkflowUsageSummary())
    assert {
        "worker_call_count",
        "text_call_count",
        "image_call_count",
        "tool_call_count",
        "tool_character_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "metered_usd",
        "notional_usd",
        "estimated_usd",
        "events",
    } == set(projected)


@pytest.mark.asyncio
async def test_canonicalizer_strips_only_approved_volatile_facts():
    """Two corpus runs must be equal after canonicalization (ids/timestamps/elapsed differ each
    run), proving the canonicalizer neutralizes intrinsically-volatile facts and nothing else."""

    a = await run_corpus()
    b = await run_corpus()
    mismatches = compare_records(a, b)
    assert not mismatches, "two runs of the same code must canonicalize equal:\n" + "\n".join(mismatches)


# --------------------------------------------------------------------------------------
# Canonicalization strictness (Phase I1.R3). The oracle must not hide real facts: no key erasure,
# no global decimal rounding — only the single reviewed exact-path normalizer may exist.
# --------------------------------------------------------------------------------------


def test_canonicalizer_does_not_erase_timeout_bounds():
    """A supervision bound changing 1s -> 99s MUST be a mismatch. The old canonicalizer erased
    every timeout_s/requested_timeout_s/kill_grace_s key to '<volatile>' path-blind, which let a
    99x bound change compare EQUAL — fatal for Iteration 2, whose whole point is moving the
    supervision machinery under this oracle's protection."""

    a = {"s": {"metadata": {"timeout_s": 1.0, "requested_timeout_s": 1.0, "kill_grace_s": 0.5}}}
    b = {"s": {"metadata": {"timeout_s": 99.0, "requested_timeout_s": 99.0, "kill_grace_s": 50.0}}}
    mismatches = compare_records({"s": _canon(a["s"])}, {"s": _canon(b["s"])})
    assert len(mismatches) == 3, f"timeout bounds must all mismatch, got: {mismatches}"


def test_canonicalizer_does_not_round_arbitrary_strings():
    """Decimals inside ordinary strings are FACTS: '3.141' vs '3.144' must mismatch. The old
    global _round_decimals_in_string collapsed them."""

    assert compare_records({"s": _canon("v=3.141")}, {"s": _canon("v=3.144")}), (
        "decimal drift inside an arbitrary string was silently hidden"
    )


def test_deadline_normalizer_is_leaf_and_pattern_scoped():
    """I1.RR2: the cooperative-deadline normalizer touches ONLY the seconds token of the known
    deadline message at result.error / trace[*].error. (1) An UNRELATED decimal change in those
    same leaves still mismatches; (2) genuine sub-10ms jitter in the real message still
    normalizes equal; (3) an error field NOT at the approved leaves (e.g. a detail payload or
    exception message) is never touched."""

    from invocation_oracle import _normalize_record

    def rec(err, detail_err="model 3.141"):
        return {
            "result": {"error": err},
            "trace": [{"error": err}],
            "details": [{"json": {"error": detail_err}}],
            "exception": None,
        }

    # (1) unrelated decimal in the SAME approved leaves -> still a mismatch
    a = _normalize_record("cooperative_deadline", rec("model 3.141"))
    b = _normalize_record("cooperative_deadline", rec("model 3.144"))
    assert compare_records({"s": a}, {"s": b}), "unrelated decimal drift was hidden at the leaves"

    # (2) genuine jitter in the real deadline message -> normalized equal
    msg1 = "capability 'slow_async' exceeded its 0.249988s execution window (work deadline)"
    msg2 = "capability 'slow_async' exceeded its 0.249981s execution window (work deadline)"
    assert compare_records(
        {"s": _normalize_record("cooperative_deadline", rec(msg1))},
        {"s": _normalize_record("cooperative_deadline", rec(msg2))},
    ) == [], "sub-10ms deadline jitter must canonicalize equal"

    # (2b) a MATERIAL bound change in the same message (0.25s -> 99s) still mismatches
    msg99 = "capability 'slow_async' exceeded its 99.000000s execution window (work deadline)"
    assert compare_records(
        {"s": _normalize_record("cooperative_deadline", rec(msg1))},
        {"s": _normalize_record("cooperative_deadline", rec(msg99))},
    ), "a real bound change must never normalize away"

    # (3) non-leaf error fields (detail payloads) are untouched even in this scenario
    c = _normalize_record("cooperative_deadline", rec(msg1, detail_err="pi 3.14159"))
    assert c["details"][0]["json"]["error"] == "pi 3.14159", "normalizer recursed beyond its leaves"


def test_exact_path_normalizer_table_is_reviewed_and_minimal():
    """The normalizer table is a REVIEWED allow-list, not a blanket rule. Exactly one entry is
    approved: the cooperative-deadline wall-jitter rounding. Any widening must fail here and be
    consciously re-reviewed."""

    assert set(_EXACT_PATH_NORMALIZERS) == {"cooperative_deadline"}, (
        f"unapproved normalized paths: {sorted(set(_EXACT_PATH_NORMALIZERS) - {'cooperative_deadline'})}"
    )


# --------------------------------------------------------------------------------------
# Public-surface + simple-tier import-gradient locks (Phase I1.0 task 4, hardened in I1.R3). A
# behavior-preserving refactor must not drift the public API, signatures, or full Pydantic schemas
# (defaults/constraints included), and must not pull advanced modules into the simple tier. The
# snapshot logic is the oracle's version-agnostic public_surface_snapshot — the same code the
# sealer runs in its pinned venv, so fixture and candidate are measured identically.
# --------------------------------------------------------------------------------------

_PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "public_surface_current.json"


def test_public_surface_matches_sealed_current_contract():
    if not _PUBLIC_FIXTURE.exists():
        pytest.fail(_RESEAL_HINT)
    snapshot = public_surface_snapshot()
    sealed = json.loads(_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    mismatches = compare_records({"public": sealed}, {"public": snapshot})
    assert not mismatches, "public surface drifted from the sealed current contract:\n" + "\n".join(mismatches)


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
