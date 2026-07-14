"""Phase 2A gate (manifest rows M9/M12): the observation bundle is a CLOSED, versioned v2
contract — every engine-written meta validates through ObservationBundleMetaV2, pre-v2 shapes
fail loudly with the historical-tag route, and retention/reclaim never half-read a foreign meta.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_workflow_engine import (
    ObservationBundleMetaV2,
    load_bundle_meta_v2,
    open_observation_run_bundle,
    prune_observation_bundles,
)
from ai_workflow_engine.workflow import WorkflowBuilder

pytestmark = [pytest.mark.unit]


def _finalized(tmp_path: Path, run_id: str = "run-v2") -> Path:
    bundle = open_observation_run_bundle(tmp_path, run_id)
    bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="completed")
    return tmp_path / run_id


def test_v2_meta_round_trips_with_the_one_digest_authority(tmp_path):
    path = _finalized(tmp_path)
    meta = load_bundle_meta_v2(path)
    assert meta.bundle_schema_version == 2
    assert meta.segment_kind == "initial" and meta.segment_index == 0
    assert meta.usage_totals_scope == "run_cumulative_at_finalize"
    definition = WorkflowBuilder("wf").step("s").build()
    assert meta.definition_digest == definition.definition_digest(), (
        "meta digest must come from the ONE digest authority (WorkflowDefinition.definition_digest)"
    )
    # absence-not-null: unset correlation never appears
    raw = json.loads((path / "meta.json").read_text())
    assert "correlation_id" not in raw
    # the v1 duplicate alias key is gone
    assert "workflow" not in raw


def test_pre_v2_and_malformed_metas_fail_loudly(tmp_path):
    old = tmp_path / "old-run"
    old.mkdir()
    (old / "meta.json").write_text(json.dumps({
        "bundle_schema_version": 1, "run_id": "old-run", "status": "completed",
    }))
    with pytest.raises(ValueError, match="historical engine/viewer tag"):
        load_bundle_meta_v2(old)

    current = _finalized(tmp_path, "run-x")
    raw = json.loads((current / "meta.json").read_text())
    raw["mystery"] = 1
    (current / "meta.json").write_text(json.dumps(raw))
    with pytest.raises(Exception):
        load_bundle_meta_v2(current)  # closed schema: unknown keys fail

    raw.pop("mystery")
    raw["status"] = "sort_of_done"
    (current / "meta.json").write_text(json.dumps(raw))
    with pytest.raises(Exception):
        load_bundle_meta_v2(current)  # closed status vocabulary


def test_retention_is_loud_on_pre_v2_bundles_and_ignores_non_bundles(tmp_path):
    _finalized(tmp_path, "run-a")
    (tmp_path / "not-a-bundle").mkdir()  # no meta.json -> ignored as before
    prune_observation_bundles(tmp_path, 5)

    old = tmp_path / "old-run"
    old.mkdir()
    (old / "meta.json").write_text(json.dumps({"bundle_schema_version": 1, "run_id": "old-run"}))
    (old / "trace.jsonl").write_text("")
    with pytest.raises(ValueError, match="historical engine/viewer tag"):
        prune_observation_bundles(tmp_path, 1)


def test_segment_identity_rules_hold_in_the_meta_model():
    base = dict(
        bundle_schema_version=2, run_id="r", workflow_id="wf", status="completed",
        timestamp="2026-07-14T00:00:00Z", trace_path="trace.jsonl", detail_path="details.jsonl",
        usage_path="usage.jsonl", definition_path="definition.json", definition_digest="d" * 8,
        artifact_manifest_path="artifacts.json", artifact_root="artifacts",
        artifact_count=0, artifacts_copied=0, trace_count=0, detail_count=0, usage_count=0,
        usage_totals_scope="run_cumulative_at_finalize", total_tokens=0,
        segment_id="r", segment_index=0, segment_kind="initial",
    )
    assert ObservationBundleMetaV2.model_validate(base).segment_kind == "initial"
    with pytest.raises(Exception):
        ObservationBundleMetaV2.model_validate({**base, "segment_kind": "resume"})  # resume needs index>=1
    with pytest.raises(Exception):
        ObservationBundleMetaV2.model_validate({**base, "segment_index": 1, "attempt": 3})  # initial+attempt


def _mutated_meta(path: Path, **overrides):
    raw = json.loads((path / "meta.json").read_text())
    raw.update(overrides)
    (path / "meta.json").write_text(json.dumps(raw))


def test_meta_boundary_rejects_path_syntax_and_untruthful_values(tmp_path):
    """C2-1/C2-2 (codex Iteration-2 review): the closed contract is closed at the BOUNDARY —
    layout paths are Literal facts, identities are plain names, timestamps are tz-aware
    ISO-8601, durable attempts are 1-based, costs are finite, and the published JSON schema
    carries the status enum itself."""

    path = _finalized(tmp_path, "boundary-run")

    # layout fields are Literals: any traversal/absolute/renamed value is a schema violation
    for field, value in (
        ("trace_path", "../../evil.jsonl"),
        ("definition_path", "/etc/passwd"),
        ("usage_path", "usage2.jsonl"),
        ("artifact_root", "artifacts/../.."),
    ):
        _mutated_meta(path, **{field: value})
        with pytest.raises(Exception):
            load_bundle_meta_v2(path)
        _mutated_meta(path, **{field: {
            "trace_path": "trace.jsonl", "definition_path": "definition.json",
            "usage_path": "usage.jsonl", "artifact_root": "artifacts",
        }[field]})

    # identities are plain names — path syntax is an attack, not an id
    for bad_run_id in ("../escape", "a/b", "..", "C:evil", "~home"):
        _mutated_meta(path, run_id=bad_run_id)
        with pytest.raises(ValueError, match="path syntax|invalid observation-bundle meta"):
            load_bundle_meta_v2(path)
    _mutated_meta(path, run_id="boundary-run")
    _mutated_meta(path, segment_id="seg/../up")
    with pytest.raises(ValueError, match="path syntax"):
        load_bundle_meta_v2(path)
    _mutated_meta(path, segment_id="boundary-run")

    # timestamp must parse tz-aware; naive or garbage is refused
    for bad_ts in ("yesterday", "2026-07-14T10:00:00"):
        _mutated_meta(path, timestamp=bad_ts)
        with pytest.raises(ValueError, match="ISO-8601|timezone-aware"):
            load_bundle_meta_v2(path)
    _mutated_meta(path, timestamp="2026-07-14T10:00:00Z")

    # durable attempts are 1-based ordinals
    _mutated_meta(path, segment_kind="resume", segment_index=1, attempt=0)
    with pytest.raises(Exception):
        load_bundle_meta_v2(path)
    _mutated_meta(path, segment_kind="initial", segment_index=0, attempt=None)

    # costs are finite money, never NaN/inf
    for bad_cost in ("NaN", "Infinity"):
        (path / "meta.json").write_text(
            (path / "meta.json").read_text().replace('"metered_usd": null', f'"metered_usd": {bad_cost}')
        )
        with pytest.raises(Exception):
            load_bundle_meta_v2(path)
        (path / "meta.json").write_text(
            (path / "meta.json").read_text().replace(f'"metered_usd": {bad_cost}', '"metered_usd": null')
        )

    # the published schema tells the truth: status is an enum, layout names are consts
    schema = ObservationBundleMetaV2.model_json_schema()
    status_schema = schema["properties"]["status"]
    assert "enum" in json.dumps(status_schema), (
        "the JSON schema must publish the closed status vocabulary (C2-2)"
    )
    assert schema["properties"]["trace_path"].get("const") == "trace.jsonl"

    # after all mutations were reverted, the bundle still loads
    assert load_bundle_meta_v2(path).run_id == "boundary-run"


def test_meta_symlink_escaping_the_bundle_is_rejected(tmp_path):
    """C2-1: a symlinked meta.json pointing outside the bundle directory is refused by the
    ONE loader — metadata is read only from inside the bundle."""

    outside = tmp_path / "outside-meta.json"
    victim = _finalized(tmp_path / "root", "sym-run")
    outside.write_text((victim / "meta.json").read_text())
    attacker = tmp_path / "root" / "attacker"
    attacker.mkdir()
    (attacker / "meta.json").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes its bundle directory"):
        load_bundle_meta_v2(attacker)
