"""Latest-only v4 bundle, value-store, reader, integrity, and boundary contracts.

Every engine-written meta validates through ``ObservationBundleMetaV4``. Older shapes fail
loudly and retention never half-reads a foreign or corrupt run.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys

import pytest

from ai_workflow_engine import (
    ObservationBundleMetaV4,
    ProviderEvidenceIntegrity,
    WorkflowTraceEvent,
    load_bundle_meta_v4,
    open_observation_run_bundle,
    prune_observation_bundles,
)
from ai_workflow_engine.workflow import WorkflowBuilder
from ai_workflow_engine.models import ObservationDetail, ObservationJsonBody, ObservationTextBody
from ai_workflow_engine.observation_contract import (
    ObservationDetailEnvelope,
)
from ai_workflow_engine.observation_reader import ObservationReader
from ai_workflow_engine.observation_values import (
    INLINE_BODY_MAX_BYTES,
    RunValueStore,
    body_sha256,
    persist_observation_body,
    render_observation_body_text,
)

pytestmark = [pytest.mark.unit]


def _logical_detail(value, *, detail_id: str = "detail-1", run_id: str = "run-v4"):
    body = ObservationJsonBody(value=value)
    return ObservationDetail(
        detail_id=detail_id,
        event_id="event-1",
        run_id=run_id,
        sequence=1,
        invocation_id="invocation-1",
        kind="tool_result",
        content_type="application/json",
        body=body,
        digest=body_sha256(body),
    )


def _v4_segment(tmp_path: Path, detail: ObservationDetail) -> tuple[Path, ObservationDetailEnvelope]:
    run_root = tmp_path / str(detail.run_id)
    segment = run_root / "segments" / str(detail.run_id)
    segment.mkdir(parents=True)
    store = RunValueStore(run_root)
    envelope = ObservationDetailEnvelope(
        **detail.model_dump(exclude={"body", "digest"}),
        body=persist_observation_body(store, detail.body),
    )
    (segment / "details.jsonl").write_text(envelope.model_dump_json() + "\n")
    meta = ObservationBundleMetaV4(
        bundle_schema_version=4,
        run_id=str(detail.run_id),
        workflow_id="wf",
        status="completed",
        timestamp="2026-09-01T12:00:00Z",
        trace_path="trace.jsonl",
        detail_path="details.jsonl",
        usage_path="usage.jsonl",
        definition_path="definition.json",
        definition_digest="d" * 64,
        artifact_manifest_path="artifacts.json",
        artifact_root="artifacts",
        value_store_layout="run-scoped-sha256-gzip-v1",
        inline_body_max_bytes=4096,
        artifact_count=0,
        artifacts_copied=0,
        trace_count=0,
        detail_count=1,
        usage_count=0,
        usage_totals_scope="run_cumulative_at_finalize",
        total_tokens=0,
        segment_id=str(detail.run_id),
        segment_index=0,
        segment_kind="initial",
        provider_evidence=ProviderEvidenceIntegrity(integrity="complete"),
    )
    (segment / "meta.json").write_text(meta.model_dump_json())
    return segment, envelope


def test_v4_logical_detail_has_one_closed_canonical_body():
    detail = _logical_detail({"z": 1, "a": "value"})
    assert detail.body.kind == "json"
    assert detail.digest == hashlib.sha256(b'{"a":"value","z":1}').hexdigest()

    with pytest.raises(Exception, match="digest must equal canonical body"):
        detail.model_copy(update={"digest": "0" * 64}).model_validate(
            detail.model_copy(update={"digest": "0" * 64}).model_dump()
        )
    with pytest.raises(Exception, match="raw bytes"):
        ObservationJsonBody(value={"raw": b"not allowed"})
    with pytest.raises(Exception):
        ObservationJsonBody(value={"number": float("nan")})
    with pytest.raises(Exception, match="extra"):
        ObservationDetail.model_validate({**detail.model_dump(), "privacy": "internal"})
    with pytest.raises(Exception, match="extra"):
        ObservationDetail.model_validate({**detail.model_dump(), "redaction_state": "none"})


def test_v4_display_text_is_derived_from_the_complete_canonical_body():
    body = ObservationJsonBody(value={"z": {"b": 2, "a": 1}, "a": "value"})

    assert body_sha256(body) == hashlib.sha256(
        b'{"a":"value","z":{"a":1,"b":2}}'
    ).hexdigest()
    assert render_observation_body_text(body) == (
        '{\n'
        '  "a": "value",\n'
        '  "z": {\n'
        '    "a": 1,\n'
        '    "b": 2\n'
        '  }\n'
        '}'
    )


def test_v4_value_store_reuses_complete_bodies_without_clobber(tmp_path):
    run_root = tmp_path / "run-v4"
    store = RunValueStore(run_root)
    text_body = ObservationTextBody(value="evidence " * 20_000)

    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(lambda _index: persist_observation_body(store, text_body), range(16)))

    assert {body.sha256 for body in bodies} == {body_sha256(text_body)}
    assert {body.byte_length for body in bodies} == {len(text_body.value.encode())}
    objects = list((run_root / "values").glob("*/*.body.gz"))
    assert len(objects) == 1
    assert not list((run_root / "values").glob("*/.value-*.tmp"))
    assert store.validate(bodies[0].sha256, bodies[0].byte_length) == objects[0]


def test_v4_inline_threshold_and_reference_keep_one_canonical_identity(tmp_path):
    store = RunValueStore(tmp_path / "run-v4")
    inline_body = ObservationTextBody(value="x" * INLINE_BODY_MAX_BYTES)
    referenced_body = ObservationTextBody(value="x" * (INLINE_BODY_MAX_BYTES + 1))

    inline = persist_observation_body(store, inline_body)
    referenced = persist_observation_body(store, referenced_body)

    assert inline.kind == "inline_text"
    assert inline.byte_length == INLINE_BODY_MAX_BYTES
    assert inline.sha256 == body_sha256(inline_body)
    assert referenced.kind == "body_ref"
    assert referenced.byte_length == INLINE_BODY_MAX_BYTES + 1
    assert referenced.sha256 == body_sha256(referenced_body)


def test_v4_value_store_rejects_corrupt_existing_objects(tmp_path):
    store = RunValueStore(tmp_path / "run-v4")
    body = ObservationTextBody(value="immutable evidence " * 1_000)
    persisted = persist_observation_body(store, body)
    object_path = store.object_path(persisted.sha256)
    object_path.write_bytes(object_path.read_bytes()[:-5] + b"broken")

    with pytest.raises(ValueError, match="corrupt|digest/length"):
        persist_observation_body(store, body)


def test_v4_value_store_cleans_owned_temp_when_publication_is_interrupted(
    tmp_path,
    monkeypatch,
):
    import ai_workflow_engine.observation_values as values

    store = RunValueStore(tmp_path / "run-v4")
    payload = b"interrupted evidence" * 1_000

    def fail_link(_source, _target):
        raise OSError("hard-link publication interrupted")

    monkeypatch.setattr(values.os, "link", fail_link)
    with pytest.raises(OSError, match="publication interrupted"):
        store.publish(lambda: iter((payload,)))

    assert not list(store.root.glob("*/*.body.gz"))
    assert not list(store.root.glob("*/.value-*.tmp"))


def test_v4_value_store_refuses_symlinked_shards_before_writing(tmp_path):
    run_root = tmp_path / "run-v4"
    store = RunValueStore(run_root)
    body = ObservationTextBody(value="outside write fence " * 1_000)
    digest = body_sha256(body)
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / digest[:2]).symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        persist_observation_body(store, body)

    assert list(outside.iterdir()) == []


def test_v4_reader_validates_suffix_before_emitting_and_reuses_one_open(tmp_path, monkeypatch):
    detail = _logical_detail({"payload": "x" * 100_000})
    segment, envelope = _v4_segment(tmp_path, detail)
    reader = ObservationReader(segment)
    object_path = reader.value_store.object_path(envelope.body.sha256)
    real_open = Path.open
    body_open_count = 0

    def counted_open(path, *args, **kwargs):
        nonlocal body_open_count
        if path == object_path:
            body_open_count += 1
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    assert b"".join(reader.iter_body_bytes(envelope)) == b'{"payload":"' + b"x" * 100_000 + b'"}'
    assert body_open_count == 1, "validation and emission must seek one already-open object"

    raw = object_path.read_bytes()
    object_path.write_bytes(raw[:-8] + b"corrupt!")
    emitted = []
    with pytest.raises(ValueError, match="corrupt|digest/length"):
        for chunk in reader.iter_body_bytes(envelope):
            emitted.append(chunk)
    assert emitted == [], "a corrupt suffix must be found before the first byte is emitted"


def test_v4_reader_enforces_body_bounds_and_bindings(tmp_path):
    detail = _logical_detail({"payload": "bounded" * 2_000})
    segment, envelope = _v4_segment(tmp_path, detail)
    reader = ObservationReader(segment)

    with pytest.raises(ValueError, match="above explicit read limit"):
        reader.read_body_bytes(envelope, max_bytes=10)
    preview = reader.preview_bytes(envelope, max_bytes=17)
    assert len(preview) == 17
    assert reader.parse_json(envelope, max_bytes=envelope.body.byte_length) == detail.body.value
    with pytest.raises(ValueError, match="belongs to invocation"):
        reader.get_detail(detail.detail_id, invocation_id="foreign-invocation")
    forged = envelope.model_copy(update={"run_id": "foreign-run"})
    with pytest.raises(ValueError, match="belongs to run"):
        list(reader.iter_body_bytes(forged))
    absent = envelope.model_copy(update={"detail_id": "other-detail"})
    with pytest.raises(ValueError, match="not persisted in segment"):
        list(reader.iter_body_bytes(absent))
    altered = envelope.model_copy(update={"metadata": {"forged": True}})
    with pytest.raises(ValueError, match="disagrees with its persisted envelope"):
        list(reader.iter_body_bytes(altered))


def test_v4_reader_rejects_meta_record_count_drift(tmp_path):
    detail = _logical_detail({"payload": "counted"})
    segment, _envelope = _v4_segment(tmp_path, detail)
    reader = ObservationReader(segment)

    reader.validate_record_counts(trace_count=0, detail_count=1, usage_count=0)
    with pytest.raises(ValueError, match="detail_count disagrees.*expected 1, read 0"):
        reader.validate_record_counts(trace_count=0, detail_count=0, usage_count=0)


@pytest.mark.parametrize("stream_name", ["trace.jsonl", "details.jsonl", "usage.jsonl"])
def test_v4_finalization_rejects_a_corrupt_stream_suffix_without_committing_meta(
    tmp_path,
    stream_name,
):
    bundle = open_observation_run_bundle(tmp_path, f"corrupt-{stream_name.split('.')[0]}")
    target = bundle.path / stream_name
    with target.open("a", encoding="utf-8") as stream:
        stream.write('{"truncated":')

    with pytest.raises(ValueError, match=f"invalid {stream_name} record"):
        bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="failed")

    assert not (bundle.path / "meta.json").exists()


def test_v4_finalization_revalidates_every_referenced_body_before_meta_commit(tmp_path):
    bundle = open_observation_run_bundle(tmp_path, "corrupt-body-finalize")
    detail = _logical_detail(
        {"payload": "corrupt after publication: " * 8_000},
        run_id="corrupt-body-finalize",
    )
    bundle.detail_sink.record(detail)
    [object_path] = list((bundle.run_path / "values").glob("*/*.body.gz"))
    object_path.write_bytes(object_path.read_bytes()[:-7] + b"damaged")

    with pytest.raises(ValueError, match="corrupt|digest/length"):
        bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="failed")

    assert not (bundle.path / "meta.json").exists()


def test_v4_finalization_peak_memory_does_not_scale_with_referenced_body_bytes(tmp_path):
    """Two isolated processes keep finalization RSS flat while body bytes grow 16x."""

    script = """
import json
import resource
import sys
from pathlib import Path
from ai_workflow_engine.observation_finalization import summarize_segment_evidence
from ai_workflow_engine.observation_values import RunValueStore

segment = Path(sys.argv[1])
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
summary = summarize_segment_evidence(
    status="failed",
    trace_path=segment / "trace.jsonl",
    detail_path=segment / "details.jsonl",
    usage_path=segment / "usage.jsonl",
    value_store=RunValueStore(segment.parent.parent, create=False),
)
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({"rss_growth_kib": max(0, after - before),
                  "detail_count": summary.detail_count}))
"""

    def measured(size: int, run_id: str) -> int:
        detail = _logical_detail({"payload": "x" * size}, detail_id=f"detail-{run_id}", run_id=run_id)
        segment, _envelope = _v4_segment(tmp_path, detail)
        (segment / "trace.jsonl").write_text("")
        (segment / "usage.jsonl").write_text("")
        child_env = {
            key: value for key, value in os.environ.items() if not key.startswith("COVERAGE")
        }
        child_env["PYTHONHASHSEED"] = "0"
        completed = subprocess.run(
            [sys.executable, "-c", script, str(segment)],
            check=True,
            capture_output=True,
            text=True,
            env=child_env,
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        assert result["detail_count"] == 1
        return int(result["rss_growth_kib"])

    small_rss = measured(2 * 1024 * 1024, "rss-small")
    large_rss = measured(32 * 1024 * 1024, "rss-large")
    assert large_rss <= small_rss + 8 * 1024, (
        "finalization RSS grew with decompressed body bytes: "
        f"small_growth={small_rss} KiB, large_growth={large_rss} KiB"
    )


def test_v4_loader_rejects_v3_without_a_compatibility_route(tmp_path):
    path = tmp_path / "old-run" / "segments" / "old-run"
    path.mkdir(parents=True)
    (path / "meta.json").write_text(
        json.dumps({"bundle_schema_version": 3, "run_id": "old-run"})
    )
    with pytest.raises(ValueError, match="no v3 reader, importer, or compatibility path"):
        load_bundle_meta_v4(path)


def _load_v4_spike_module():
    script = Path(__file__).parents[1] / "scripts" / "observation_v4_spike.py"
    spec = importlib.util.spec_from_file_location("observation_v4_spike", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v4_format_spike_exercises_the_closed_threshold_and_codec_matrix():
    report = _load_v4_spike_module().build_report()

    assert report["schema"] == "observation-v4-spike-v1"
    assert report["record_count"] == 1_524
    assert report["generator_manifest_sha256"]
    assert report["semantic_metrics_sha256"]
    assert report["duplicate_complete_body_occurrences"] > 0
    assert set(report["body_size_buckets"]) == {
        "lte_1k",
        "1k_to_4k",
        "4k_to_16k",
        "16k_to_64k",
        "gt_64k",
    }
    scenarios = {
        (item["threshold_bytes"], item["codec"]): item
        for item in report["scenarios"]
    }
    assert set(scenarios) == {
        (threshold, codec)
        for threshold in (1_024, 4_096, 16_384, 65_536)
        for codec in ("identity", "gzip")
    }
    selected = scenarios[(4_096, "gzip")]
    assert selected["object_count"] > 0
    assert selected["reused_occurrences"] > 0
    assert selected["repeated_inline_bytes"] > 0
    assert selected["physical_bytes"] < scenarios[(4_096, "identity")]["physical_bytes"]


def _finalized(tmp_path: Path, run_id: str = "run-v4") -> Path:
    bundle = open_observation_run_bundle(tmp_path, run_id)
    bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="completed")
    return bundle.path


def test_v4_meta_round_trips_with_the_one_digest_authority(tmp_path):
    path = _finalized(tmp_path)
    meta = load_bundle_meta_v4(path)
    assert meta.bundle_schema_version == 4
    assert meta.segment_kind == "initial" and meta.segment_index == 0
    assert meta.usage_totals_scope == "run_cumulative_at_finalize"
    assert meta.provider_evidence.integrity == "complete"
    definition = WorkflowBuilder("wf").step("s").build()
    assert meta.definition_digest == definition.definition_digest(), (
        "meta digest must come from the ONE digest authority (WorkflowDefinition.definition_digest)"
    )
    # absence-not-null: unset correlation never appears
    raw = json.loads((path / "meta.json").read_text())
    assert "correlation_id" not in raw
    # the v1 duplicate alias key is gone
    assert "workflow" not in raw


def test_pre_v4_and_malformed_metas_fail_loudly(tmp_path):
    old = tmp_path / "old-run" / "segments" / "old-run"
    old.mkdir(parents=True)
    (old / "meta.json").write_text(json.dumps({
        "bundle_schema_version": 2, "run_id": "old-run", "status": "completed",
    }))
    with pytest.raises(ValueError, match="no v3 reader, importer, or compatibility path"):
        load_bundle_meta_v4(old)

    current = _finalized(tmp_path, "run-x")
    raw = json.loads((current / "meta.json").read_text())
    raw["mystery"] = 1
    (current / "meta.json").write_text(json.dumps(raw))
    with pytest.raises(Exception):
        load_bundle_meta_v4(current)  # closed schema: unknown keys fail

    raw.pop("mystery")
    raw["status"] = "sort_of_done"
    (current / "meta.json").write_text(json.dumps(raw))
    with pytest.raises(Exception):
        load_bundle_meta_v4(current)  # closed status vocabulary


def test_retention_isolates_pre_v4_bundles_and_ignores_non_bundles(tmp_path):
    _finalized(tmp_path, "run-a")
    (tmp_path / "not-a-bundle").mkdir()  # no meta.json -> ignored as before
    prune_observation_bundles(tmp_path, 5)

    old = tmp_path / "old-run"
    old_segment = old / "segments" / "old-run"
    old_segment.mkdir(parents=True)
    (old_segment / "meta.json").write_text(
        json.dumps({"bundle_schema_version": 3, "run_id": "old-run"})
    )
    (old_segment / "trace.jsonl").write_text("")
    prune_observation_bundles(tmp_path, 1)
    assert old.exists(), "corrupt/foreign evidence is preserved for operator inspection"


def test_segment_identity_rules_hold_in_the_meta_model():
    base = dict(
        bundle_schema_version=4, run_id="r", workflow_id="wf", status="completed",
        timestamp="2026-07-14T00:00:00Z", trace_path="trace.jsonl", detail_path="details.jsonl",
        usage_path="usage.jsonl", definition_path="definition.json", definition_digest="d" * 8,
        artifact_manifest_path="artifacts.json", artifact_root="artifacts",
        value_store_layout="run-scoped-sha256-gzip-v1", inline_body_max_bytes=4096,
        artifact_count=0, artifacts_copied=0, trace_count=0, detail_count=0, usage_count=0,
        usage_totals_scope="run_cumulative_at_finalize", total_tokens=0,
        segment_id="r", segment_index=0, segment_kind="initial",
        provider_evidence={"integrity": "complete"},
    )
    assert ObservationBundleMetaV4.model_validate(base).segment_kind == "initial"
    with pytest.raises(Exception):
        ObservationBundleMetaV4.model_validate({**base, "segment_kind": "resume"})  # resume needs index>=1
    with pytest.raises(Exception):
        ObservationBundleMetaV4.model_validate({**base, "segment_index": 1, "attempt": 3})  # initial+attempt
    with pytest.raises(Exception):
        ObservationBundleMetaV4.model_validate(
            {key: value for key, value in base.items() if key != "provider_evidence"}
        )
    with pytest.raises(ValueError, match="completed bundle requires complete"):
        ObservationBundleMetaV4.model_validate(
            {
                **base,
                "provider_evidence": {
                    "integrity": "incomplete",
                    "diagnostic": "missing terminal provider trace",
                },
            }
        )


def test_cancelled_bundle_records_incomplete_provider_evidence_and_remains_readable(
    tmp_path,
):
    from ai_workflow_viewer import FileEventSource

    bundle = open_observation_run_bundle(tmp_path, "cancelled-provider")
    bundle.trace_sink.record(
        WorkflowTraceEvent(
            node="provider",
            phase="provider:request",
            invocation_id="inv-inflight",
            detail_capture="capture_mode_off",
        )
    )
    definition = WorkflowBuilder("wf").step("s").build()
    bundle.finalize(definition, status="cancelled")

    meta = load_bundle_meta_v4(bundle.path)
    assert meta.provider_evidence.integrity == "incomplete"
    assert "exactly one usage event" in (meta.provider_evidence.diagnostic or "")
    assert FileEventSource(bundle.path).read().meta.status == "cancelled"
    prune_observation_bundles(tmp_path, 1)
    assert bundle.path.exists()


def test_completed_bundle_rejects_incomplete_provider_evidence(tmp_path):
    bundle = open_observation_run_bundle(tmp_path, "completed-provider")
    bundle.trace_sink.record(
        WorkflowTraceEvent(
            node="provider",
            phase="provider:request",
            invocation_id="inv-inflight",
            detail_capture="capture_mode_off",
        )
    )
    with pytest.raises(ValueError, match="exactly one usage event"):
        bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="completed")
    assert not (bundle.path / "meta.json").exists()


def test_meta_commit_failure_leaves_no_partial_commit_marker(tmp_path, monkeypatch):
    import ai_workflow_engine.observation_writer as writer

    bundle = open_observation_run_bundle(tmp_path, "atomic-meta")

    def fail_replace(_source, _target):
        raise OSError("atomic replace unavailable")

    monkeypatch.setattr(writer.os, "replace", fail_replace)
    with pytest.raises(OSError, match="atomic replace unavailable"):
        bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="failed")

    assert not (bundle.path / "meta.json").exists()
    assert not list(bundle.path.glob(".meta.json.*.tmp"))


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
            load_bundle_meta_v4(path)
        _mutated_meta(path, **{field: {
            "trace_path": "trace.jsonl", "definition_path": "definition.json",
            "usage_path": "usage.jsonl", "artifact_root": "artifacts",
        }[field]})

    # identities are plain names — path syntax is an attack, not an id
    for bad_run_id in ("../escape", "a/b", "..", "C:evil", "~home"):
        _mutated_meta(path, run_id=bad_run_id)
        with pytest.raises(ValueError, match="path syntax|invalid observation-bundle meta"):
            load_bundle_meta_v4(path)
    _mutated_meta(path, run_id="boundary-run")
    _mutated_meta(path, segment_id="seg/../up")
    with pytest.raises(ValueError, match="path syntax"):
        load_bundle_meta_v4(path)
    _mutated_meta(path, segment_id="boundary-run")

    # timestamp must parse tz-aware; naive or garbage is refused
    for bad_ts in ("yesterday", "2026-07-14T10:00:00"):
        _mutated_meta(path, timestamp=bad_ts)
        with pytest.raises(ValueError, match="ISO-8601|timezone-aware"):
            load_bundle_meta_v4(path)
    _mutated_meta(path, timestamp="2026-07-14T10:00:00Z")

    # durable attempts are 1-based ordinals
    _mutated_meta(path, segment_kind="resume", segment_index=1, attempt=0)
    with pytest.raises(Exception):
        load_bundle_meta_v4(path)
    _mutated_meta(path, segment_kind="initial", segment_index=0, attempt=None)

    # costs are finite money, never NaN/inf
    for bad_cost in ("NaN", "Infinity"):
        (path / "meta.json").write_text(
            (path / "meta.json").read_text().replace('"metered_usd": null', f'"metered_usd": {bad_cost}')
        )
        with pytest.raises(Exception):
            load_bundle_meta_v4(path)
        (path / "meta.json").write_text(
            (path / "meta.json").read_text().replace(f'"metered_usd": {bad_cost}', '"metered_usd": null')
        )

    # the published schema tells the truth: status is an enum, layout names are consts
    schema = ObservationBundleMetaV4.model_json_schema()
    status_schema = schema["properties"]["status"]
    assert "enum" in json.dumps(status_schema), (
        "the JSON schema must publish the closed status vocabulary (C2-2)"
    )
    assert schema["properties"]["trace_path"].get("const") == "trace.jsonl"

    # after all mutations were reverted, the bundle still loads
    assert load_bundle_meta_v4(path).run_id == "boundary-run"


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
        load_bundle_meta_v4(attacker)


def test_child_directory_symlinks_never_escape_the_bundle_root(tmp_path):
    """C2GR-1 attacks (1)+(3)+(4): a pre-existing ``base/run -> outside`` symlink is rejected
    by the WRITER with zero outside files; hostile identities (``C:evil``/``~home``/
    ``..prefix``) die BEFORE any directory exists (the writer shares the ONE identity rule);
    ordinary runs and a deliberately symlinked CONFIGURED base keep working — the base is
    trusted, children are not."""

    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "root"
    base.mkdir()
    (base / "trap-run").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        open_observation_run_bundle(base, "trap-run")
    assert list(outside.iterdir()) == [], "the writer must never create files through a symlink"

    # A child symlink pointing INSIDE the base is the case the containment fallback CANNOT
    # catch (it resolves within the root) — only the is-symlink rejection does. Physical
    # segment identity is a real directory: an aliased child is refused, not followed.
    (base / "decoy").mkdir()
    (base / "alias-run").symlink_to(base / "decoy")
    with pytest.raises(ValueError, match="symlink"):
        open_observation_run_bundle(base, "alias-run")
    assert list((base / "decoy").iterdir()) == [], "no files may be written through an inside symlink"

    for bad in ("C:evil", "~home", "..prefix"):
        with pytest.raises(ValueError, match="plain name"):
            open_observation_run_bundle(base, bad)
        assert not (base / bad).exists(), f"{bad!r} must be rejected BEFORE directory creation"

    real_root = tmp_path / "real-root"
    real_root.mkdir()
    alias = tmp_path / "alias-root"
    alias.symlink_to(real_root)
    bundle = open_observation_run_bundle(alias, "ok-run")
    bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="completed")
    assert load_bundle_meta_v4(bundle.path).run_id == "ok-run", (
        "a deliberately symlinked CONFIGURED base is supported configuration"
    )
