"""Hermetic per-run gate for the sealed CURRENT-contract oracle (Phase 1A, manifest row M1).

The v0.10.1 wheel-equality gate is retired (final run recorded EQUAL x4 at 53ef867 in the plan
evidence); integrity now comes from this tamper-evidence chain: every sealed input — corpus
fixture, public-surface fixture, the corpus module, the sealer, and the intentional-break ledger —
is sha256-pinned by the provenance record, regeneration is explicit-only via the host sealer, and
the sealer refuses any fixture delta not covered by the ledger. Runs inside ./test.sh with no
git/network/pip.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from current_contract_seal import active_entries, load_ledger, partition_deltas

pytestmark = [pytest.mark.unit]

_HERE = Path(__file__).resolve().parent
_FIXTURES = _HERE / "fixtures"
_FIXTURE = _FIXTURES / "invocation_current_contract.json"
_PUBLIC_FIXTURE = _FIXTURES / "public_surface_current.json"
_PROVENANCE = _FIXTURES / "current_contract.provenance.json"
_LEDGER = _FIXTURES / "v011_intentional_break_ledger.json"
_ORACLE = _HERE / "invocation_oracle.py"
_SEALER = _HERE / "current_contract_seal.py"

_RESEAL_HINT = (
    "current-contract seal missing/stale — reseal explicitly on the host:\n"
    "  python3 packages/ai_workflow_engine/tests/current_contract_seal.py --seal"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_current_contract_provenance_is_sealed_and_matches():
    for f in (_FIXTURE, _PUBLIC_FIXTURE, _PROVENANCE, _LEDGER):
        if not f.exists():
            pytest.fail(f"{f.name} missing; {_RESEAL_HINT}")
    prov = json.loads(_PROVENANCE.read_text(encoding="utf-8"))

    assert prov["contract"] == "v0.12 current-contract seal"
    assert prov["determinism"] == "two-run-equal"
    mismatched = {
        name: (recorded, actual)
        for name, recorded, actual in (
            ("fixture_sha256", prov["fixture_sha256"], _sha256(_FIXTURE)),
            ("public_surface_sha256", prov["public_surface_sha256"], _sha256(_PUBLIC_FIXTURE)),
            ("corpus_sha256", prov["corpus_sha256"], _sha256(_ORACLE)),
            ("sealer_sha256", prov["sealer_sha256"], _sha256(_SEALER)),
            ("ledger_sha256", prov["ledger_sha256"], _sha256(_LEDGER)),
        )
        if recorded != actual
    }
    assert not mismatched, (
        f"sealed inputs changed WITHOUT a reseal: {sorted(mismatched)} — {_RESEAL_HINT}"
    )

    import pydantic

    assert prov["pydantic_pin"] == pydantic.VERSION, (
        f"seal pydantic {prov['pydantic_pin']} != runtime {pydantic.VERSION}; corpus error "
        f"strings embed the pydantic version — reseal for this environment"
    )


def test_ledger_partition_refuses_unledgered_deltas():
    """The reseal guard's core rule, unit-locked: a delta is accepted ONLY when its path matches a
    ledger entry's path_prefix; everything else is refused (and would abort a reseal)."""

    entries = [
        {"path_prefix": "invalid_input.result.error", "reason": "x", "manifest_row": "M2"},
    ]
    deltas = [
        "invalid_input.result.error: 'a' != 'b'",              # ledgered
        "sync_success.result.status: 'accepted' != 'failed'",  # NOT ledgered
        "extra_row: scenario only in candidate",               # NOT ledgered
    ]
    ledgered, unledgered = partition_deltas(deltas, entries)
    assert ledgered == [deltas[0]]
    assert unledgered == deltas[1:], "unledgered deltas must be refused, never absorbed"


def test_ledger_schema_is_strict():
    """Malformed ledger entries must fail loudly at load (path_prefix/reason/manifest_row all
    required) — the committed ledger itself must load."""

    assert isinstance(load_ledger(_LEDGER), list)

    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"entries": [{"path_prefix": "x", "reason": "r"}]}, fh)  # missing manifest_row
        name = fh.name
    try:
        with pytest.raises(SystemExit):
            load_ledger(Path(name))
    finally:
        Path(name).unlink()



def test_unrelated_export_removal_is_refused_by_the_ledger():
    """Review finding 4: exports are key-addressable, so a REMOVED export names itself and no
    exact ledger entry covers it — the sealer must refuse. The old list-shaped exports let any
    later removal hide under one broad 'public.exports' prefix."""

    entries = load_ledger(_LEDGER)
    deltas = ["public.exports.WorkflowBuilder: only in baseline = 'export'"]
    ledgered, unledgered = partition_deltas(deltas, entries)
    assert unledgered == deltas, "an unrelated export removal slipped through the ledger"


def test_schema_drift_of_every_sealed_model_is_a_delta():
    """Review finding 4: the public seal covers EVERY exported pydantic model — a
    validator/default/constraint change in MachineSnapshot, LLMRequest, or ObservationConfig is
    a named delta the ledger must approve, never a blind spot."""

    import json as _json

    sealed = _json.loads(_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    for model in ("MachineSnapshot", "LLMRequest", "ObservationConfig", "WorkflowGoal"):
        assert model in sealed["schemas"], f"{model} missing from the sealed public surface"

    from current_contract_seal import compare_records

    for model in ("MachineSnapshot", "LLMRequest", "ObservationConfig"):
        mutated = _json.loads(_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
        mutated["schemas"][model]["properties"]["__drift__"] = {"type": "integer", "default": 7}
        deltas = compare_records({"public": sealed}, {"public": mutated})
        assert any(f"public.schemas.{model}" in d for d in deltas), (
            f"schema drift in {model} produced no named delta"
        )
        entries = load_ledger(_LEDGER)
        _, unledgered = partition_deltas(deltas, entries)
        assert unledgered, f"schema drift in {model} was silently ledgered"


def test_ledger_entries_are_exact_no_standing_wildcards():
    """Review finding 4 hygiene: every ledger entry is exact-match — prefix mode may only appear
    for a genuinely subtree-scoped break approved in the manifest, and none exists today."""

    for e in load_ledger(_LEDGER):
        if e.get("match", "prefix") == "exact":
            continue
        # a subtree prefix is legal ONLY as a consumed (spent) one-seal permission — an
        # ACTIVE prefix would be a standing wildcard (recheck R3/R4 rule)
        assert e.get("status") == "spent", (
            f"ACTIVE wildcard entry found: {e['path_prefix']!r} — prefix entries must be "
            f"spent immediately after their one seal"
        )


def test_committed_ledger_has_no_active_permissions():
    """Recheck R3: consumed permissions are SPENT after their seal. The committed state must
    never carry a standing authorization — future intentional changes add a narrow active entry,
    seal, then consume it before commit."""

    entries = load_ledger(_LEDGER)
    still_active = [e["path_prefix"] for e in active_entries(entries)]
    assert not still_active, f"standing ledger permissions found: {still_active}"


def test_spent_entries_never_authorize_deltas():
    """Recheck R3: a spent entry is history, not permission — the exact delta it once approved
    is refused if it reappears."""

    spent = [{"path_prefix": "public.schemas.WorkflowGoal", "match": "exact",
              "reason": "r", "manifest_row": "M15", "status": "spent"}]
    deltas = ["public.schemas.WorkflowGoal: {...} != '<schema-error: X>'"]
    _, unledgered = partition_deltas(deltas, active_entries(spent))
    assert unledgered == deltas


def test_whole_model_loss_is_unledgered_at_every_sealed_path():
    """Recheck R3: for EVERY sealed model, whole-model removal AND schema-error replacement
    produce deltas no committed entry authorizes — the consumed transition can never be reused
    to drop a public contract."""

    import json as _json

    from current_contract_seal import compare_records

    sealed = _json.loads(_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
    entries = active_entries(load_ledger(_LEDGER))
    for model in sealed["schemas"]:
        removed = _json.loads(_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
        del removed["schemas"][model]
        deltas = compare_records({"public": sealed}, {"public": removed})
        _, unledgered = partition_deltas(deltas, entries)
        assert unledgered, f"whole-model removal of {model} was ledgered"

        broken = _json.loads(_PUBLIC_FIXTURE.read_text(encoding="utf-8"))
        broken["schemas"][model] = "<schema-error: Boom>"
        deltas = compare_records({"public": sealed}, {"public": broken})
        _, unledgered = partition_deltas(deltas, entries)
        assert unledgered, f"schema-error replacement of {model} was ledgered"
