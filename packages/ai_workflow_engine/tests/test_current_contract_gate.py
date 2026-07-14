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

from current_contract_seal import load_ledger, partition_deltas

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

    assert prov["contract"] == "v0.11 current-contract seal"
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
