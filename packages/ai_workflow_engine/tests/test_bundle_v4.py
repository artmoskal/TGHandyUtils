"""Latest-only v4 bundle, value-store, reader, integrity, and boundary contracts.

Every engine-written meta validates through ``ObservationBundleMetaV4``. Older shapes fail
loudly and retention never half-reads a foreign or corrupt run.
"""

from __future__ import annotations

import gzip
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
    ObservationSegment,
    ProviderEvidenceIntegrity,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
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
from ai_workflow_engine.usage_contract import NotionalPricingResult
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
    (segment / "trace.jsonl").write_text("")
    (segment / "usage.jsonl").write_text("")
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
        trace_sha256=hashlib.sha256((segment / "trace.jsonl").read_bytes()).hexdigest(),
        detail_sha256=hashlib.sha256((segment / "details.jsonl").read_bytes()).hexdigest(),
        usage_sha256=hashlib.sha256((segment / "usage.jsonl").read_bytes()).hexdigest(),
        incomplete_streams=[],
        stream_diagnostic=None,
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


def test_v4_reader_rejects_valid_gzip_with_wrong_complete_body_before_emitting(tmp_path):
    """The final SHA/length comparison, not gzip framing, owns complete-body truth."""

    detail = _logical_detail({"payload": "x" * 100_000})
    segment, envelope = _v4_segment(tmp_path, detail)
    reader = ObservationReader(segment)
    object_path = reader.value_store.object_path(envelope.body.sha256)
    expected = b"".join(reader.iter_body_bytes(envelope))
    forged = expected.replace(b"x", b"y")
    assert len(forged) == len(expected) and forged != expected
    object_path.write_bytes(gzip.compress(forged, mtime=0))

    emitted = []
    with pytest.raises(ValueError, match="digest/length"):
        for chunk in reader.iter_body_bytes(envelope):
            emitted.append(chunk)
    assert emitted == [], "valid gzip with forged body bytes must emit nothing"


def test_v4_reader_rejects_persisted_compact_stream_tampering(tmp_path):
    """Envelope equality must be bound to finalized bytes, not a detached caller copy."""

    detail = _logical_detail({"payload": "bound"})
    segment, _envelope = _v4_segment(tmp_path, detail)
    persisted = json.loads((segment / "details.jsonl").read_text())
    persisted["metadata"] = {"forged_after_finalize": True}
    (segment / "details.jsonl").write_text(json.dumps(persisted) + "\n")

    with pytest.raises(ValueError, match="details.jsonl.*SHA-256|stream.*digest"):
        list(ObservationReader(segment).iter_detail_envelopes())


@pytest.mark.parametrize(
    ("stream_name", "reader_method"),
    [
        ("trace.jsonl", "iter_trace_events"),
        ("details.jsonl", "iter_detail_envelopes"),
        ("usage.jsonl", "iter_usage_events"),
    ],
)
def test_v4_reader_rejects_each_finalized_stream_tamper_before_records(
    tmp_path,
    stream_name,
    reader_method,
):
    bundle = open_observation_run_bundle(tmp_path, "sealed-streams")
    bundle.trace_sink.record(WorkflowTraceEvent(node="n", event_id="trace-1"))
    bundle.detail_sink.record(
        _logical_detail({"payload": "sealed"}, run_id="sealed-streams").model_copy(
            update={"sequence": None}
        )
    )
    bundle.usage_sink.record(WorkflowUsageEvent(node="n", event_id="usage-1"))
    bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="failed")
    target = bundle.path / stream_name
    target.write_bytes(target.read_bytes() + b"\n")  # still valid JSONL, different exact bytes

    with pytest.raises(ValueError, match=f"{stream_name}.*SHA-256"):
        list(getattr(ObservationReader(bundle.path), reader_method)())


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


@pytest.mark.parametrize("kind", ["trace", "detail", "usage"])
def test_v4_writer_rejects_foreign_run_identity_before_persistence(tmp_path, kind):
    bundle = open_observation_run_bundle(tmp_path, "owned-run")
    if kind == "trace":
        record = WorkflowTraceEvent(node="n", run_id="foreign-run")
        sink = bundle.trace_sink
    elif kind == "detail":
        record = _logical_detail({"payload": "foreign"}, run_id="foreign-run")
        sink = bundle.detail_sink
    else:
        record = WorkflowUsageEvent(node="n", run_id="foreign-run")
        sink = bundle.usage_sink

    with pytest.raises(ValueError, match="foreign-run.*owned-run|owned-run.*foreign-run"):
        sink.record(record)
    assert bundle.trace_path.read_text() == ""
    assert bundle.detail_path.read_text() == ""
    assert bundle.usage_path.read_text() == ""


def test_v4_writer_rejects_duplicate_pre_stamped_sequence_before_persistence(tmp_path):
    bundle = open_observation_run_bundle(tmp_path, "sequence-owner")
    bundle.trace_sink.record(WorkflowTraceEvent(node="n", sequence=3))
    with pytest.raises(ValueError, match="duplicate observation sequence 3"):
        bundle.usage_sink.record(WorkflowUsageEvent(node="n", sequence=3))
    assert bundle.usage_path.read_text() == ""


@pytest.mark.parametrize("duplicate", ["sequence", "event_id"])
def test_v4_finalization_rejects_duplicate_persisted_identity(tmp_path, duplicate):
    bundle = open_observation_run_bundle(tmp_path, f"duplicate-{duplicate}")
    first = WorkflowTraceEvent(
        node="n",
        run_id=bundle.run_id,
        sequence=1,
        event_id="event-1",
    )
    if duplicate == "sequence":
        second = WorkflowUsageEvent(
            node="n",
            run_id=bundle.run_id,
            sequence=1,
            event_id="usage-2",
        )
        bundle.trace_path.write_text(first.model_dump_json() + "\n")
        bundle.usage_path.write_text(second.model_dump_json() + "\n")
    else:
        second = first.model_copy(update={"sequence": 2})
        bundle.trace_path.write_text(
            first.model_dump_json() + "\n" + second.model_dump_json() + "\n"
        )

    with pytest.raises(ValueError, match=f"duplicate observation {duplicate}"):
        bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="failed")
    assert not (bundle.path / "meta.json").exists()


def test_v4_finalization_rejects_foreign_persisted_run_identity(tmp_path):
    bundle = open_observation_run_bundle(tmp_path, "expected-run")
    foreign = WorkflowTraceEvent(
        node="n",
        run_id="foreign-run",
        sequence=1,
        event_id="foreign-event",
    )
    bundle.trace_path.write_text(foreign.model_dump_json() + "\n")

    with pytest.raises(ValueError, match="foreign-run.*expected-run|expected-run.*foreign-run"):
        bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="failed")
    assert not (bundle.path / "meta.json").exists()


@pytest.mark.parametrize("stream", ["trace", "usage", "detail"])
def test_v4_finalization_rejects_duplicate_per_stream_record_identity(tmp_path, stream):
    bundle = open_observation_run_bundle(tmp_path, f"duplicate-{stream}-identity")
    if stream == "trace":
        records = [
            WorkflowTraceEvent(node="n", event_id="same-id", sequence=1, run_id=bundle.run_id),
            WorkflowTraceEvent(node="n", event_id="same-id", sequence=2, run_id=bundle.run_id),
        ]
        bundle.trace_path.write_text("\n".join(item.model_dump_json() for item in records) + "\n")
        expected = "duplicate observation event_id"
    elif stream == "usage":
        records = [
            WorkflowUsageEvent(node="n", event_id="same-id", sequence=1, run_id=bundle.run_id),
            WorkflowUsageEvent(node="n", event_id="same-id", sequence=2, run_id=bundle.run_id),
        ]
        bundle.usage_path.write_text("\n".join(item.model_dump_json() for item in records) + "\n")
        expected = "duplicate observation event_id"
    else:
        first = _logical_detail({"payload": 1}, detail_id="same-id", run_id=bundle.run_id)
        second = _logical_detail({"payload": 2}, detail_id="same-id", run_id=bundle.run_id)
        bundle.detail_sink.record(first.model_copy(update={"sequence": None}))
        bundle.detail_sink.record(second.model_copy(update={"sequence": None}))
        expected = "duplicate observation detail_id"

    with pytest.raises(ValueError, match=expected):
        bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="failed")
    assert not (bundle.path / "meta.json").exists()


def test_v4_finalization_rejects_nonpositive_persisted_sequence(tmp_path):
    bundle = open_observation_run_bundle(tmp_path, "invalid-sequence")
    invalid = WorkflowTraceEvent(node="n", event_id="event-1", sequence=0, run_id=bundle.run_id)
    bundle.trace_path.write_text(invalid.model_dump_json() + "\n")

    with pytest.raises(ValueError, match="positive observation sequence"):
        bundle.finalize(WorkflowBuilder("wf").step("s").build(), status="failed")
    assert not (bundle.path / "meta.json").exists()


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
    expected_run_id=segment.parent.parent.name,
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


def _run_v4_spike(tmp_path: Path):
    script = Path(__file__).parents[1] / "scripts" / "observation_v4_spike.py"
    output = tmp_path / "observation-v4-spike.json"
    child_env = {
        key: value for key, value in os.environ.items() if not key.startswith("COVERAGE")
    }
    subprocess.run(
        [sys.executable, str(script), "--output", str(output)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
        env=child_env,
    )
    return json.loads(output.read_text(encoding="utf-8"))


def test_v4_format_spike_exercises_the_closed_threshold_and_codec_matrix(tmp_path):
    report = _run_v4_spike(tmp_path)

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
    assert meta.incomplete_streams == [] and meta.stream_diagnostic is None
    assert meta.trace_sha256 == hashlib.sha256((path / "trace.jsonl").read_bytes()).hexdigest()
    assert meta.detail_sha256 == hashlib.sha256((path / "details.jsonl").read_bytes()).hexdigest()
    assert meta.usage_sha256 == hashlib.sha256((path / "usage.jsonl").read_bytes()).hexdigest()
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


def test_retention_uses_highest_durable_attempt_not_newest_timestamp(tmp_path):
    from ai_workflow_engine.segment_lifecycle import commit_attempt

    run_id = "durable-retention"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    _mutated_meta(initial.path, timestamp="2025-01-01T00:00:00Z")

    attempts = (
        (1, "2025-01-03T00:00:00Z", "completed"),
        (2, "2025-01-02T00:00:00Z", "requires_user_input"),
    )
    for attempt, timestamp, status in attempts:
        segment_id = f"{run_id}--s001-r{attempt}"
        bundle = open_observation_run_bundle(
            tmp_path,
            run_id,
            segment=ObservationSegment(
                segment_id=segment_id,
                segment_index=1,
                kind="resume",
                definition_digest=definition.definition_digest(),
                attempt=attempt,
            ),
        )
        bundle.finalize(definition, status=status)
        _mutated_meta(bundle.path, timestamp=timestamp)
        assert commit_attempt(tmp_path, run_id, segment_id, attempt=attempt)

    newer = _finalized(tmp_path, "newer-terminal")
    _mutated_meta(newer, timestamp="2026-01-01T00:00:00Z")
    prune_observation_bundles(tmp_path, 1)

    assert (tmp_path / run_id).exists(), (
        "the highest committed durable attempt is suspended, so the logical run is "
        "in-flight even when an older completed attempt has a later timestamp"
    )
    assert (tmp_path / "newer-terminal").exists()


def test_retention_preserves_group_during_finalize_to_commit_window(tmp_path):
    from ai_workflow_engine.segment_lifecycle import commit_attempt

    run_id = "provisional-retention"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    _mutated_meta(initial.path, timestamp="2025-01-01T00:00:00Z")

    committed_id = f"{run_id}--s001-r1"
    committed = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=committed_id,
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    committed.finalize(definition, status="completed")
    _mutated_meta(committed.path, timestamp="2025-01-02T00:00:00Z")
    assert commit_attempt(tmp_path, run_id, committed_id, attempt=1)

    provisional = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001-r2",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=2,
        ),
    )
    provisional.finalize(definition, status="completed")
    _mutated_meta(provisional.path, timestamp="2025-01-03T00:00:00Z")
    assert not (provisional.path / "commit.json").exists()

    newer = _finalized(tmp_path, "newer-terminal-provisional")
    _mutated_meta(newer, timestamp="2026-01-01T00:00:00Z")
    prune_observation_bundles(tmp_path, 1)

    assert (tmp_path / run_id).exists(), (
        "a finalized durable attempt awaiting its commit marker is still in flight; "
        "retention must preserve the complete logical run"
    )
    assert (tmp_path / "newer-terminal-provisional").exists()

    assert commit_attempt(
        tmp_path,
        run_id,
        provisional.segment.segment_id,
        attempt=2,
    )
    prune_observation_bundles(tmp_path, 1)
    assert not (tmp_path / run_id).exists(), (
        "after commit resolves the provisional attempt, ordinary retention resumes"
    )


def test_retention_preserves_group_while_attempt_has_no_meta(tmp_path):
    from ai_workflow_engine.segment_lifecycle import commit_attempt

    run_id = "open-attempt-retention"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    _mutated_meta(initial.path, timestamp="2025-01-01T00:00:00Z")

    committed_id = f"{run_id}--s001-r1"
    committed = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=committed_id,
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    committed.finalize(definition, status="completed")
    _mutated_meta(committed.path, timestamp="2025-01-02T00:00:00Z")
    assert commit_attempt(tmp_path, run_id, committed_id, attempt=1)

    open_attempt = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001-r2",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=2,
        ),
    )
    assert open_attempt.path.is_dir()
    assert not (open_attempt.path / "meta.json").exists()

    newer = _finalized(tmp_path, "newer-terminal-open-attempt")
    _mutated_meta(newer, timestamp="2026-01-01T00:00:00Z")
    prune_observation_bundles(tmp_path, 1)

    assert (tmp_path / run_id).exists(), (
        "an open physical attempt without metadata is in flight and must protect its "
        "complete logical run from retention"
    )


def test_canonical_segment_selector_owns_attempt_dispositions():
    from ai_workflow_engine.observation_canonical import (
        CanonicalSegmentCandidate,
        select_canonical_segments,
    )

    candidate = CanonicalSegmentCandidate
    selection = select_canonical_segments(
        "run",
        [
            candidate("run", 0, None, False, False, "2026-01-01T00:00:00Z"),
            candidate("attempt-1", 1, 1, True, False, "2026-01-01T00:03:00Z"),
            candidate("attempt-2", 1, 2, True, False, "2026-01-01T00:02:00Z"),
            candidate("attempt-3", 1, 3, False, False, "2026-01-01T00:04:00Z"),
            candidate("local-old", 2, None, False, False, "2026-01-01T00:05:00Z"),
            candidate("local-new", 2, None, False, False, "2026-01-01T00:06:00Z"),
            candidate("abandoned", 3, 1, False, True, "2026-01-01T00:07:00Z"),
        ],
    )

    assert selection.canonical_ids == ("run", "attempt-2", "local-new")
    assert selection.dispositions == {
        "abandoned": "abandoned",
        "run": "canonical",
        "attempt-1": "superseded",
        "attempt-2": "canonical",
        "attempt-3": "provisional",
        "local-old": "superseded",
        "local-new": "canonical",
    }
    with pytest.raises(ValueError, match="both committed and abandoned"):
        select_canonical_segments(
            "run",
            [candidate("conflict", 1, 1, True, True, "2026-01-01T00:00:00Z")],
        )
    with pytest.raises(ValueError, match="duplicate segment identity"):
        select_canonical_segments(
            "run",
            [
                candidate("same", 0, None, False, False, "2026-01-01T00:00:00Z"),
                candidate("same", 1, None, False, False, "2026-01-01T00:01:00Z"),
            ],
        )
    with pytest.raises(ValueError, match="share ordinal 1"):
        select_canonical_segments(
            "run",
            [
                candidate("attempt-1a", 1, 1, True, False, "2026-01-01T00:00:00Z"),
                candidate("attempt-1b", 1, 1, True, False, "2026-01-01T00:01:00Z"),
                candidate("attempt-2", 1, 2, True, False, "2026-01-01T00:02:00Z"),
            ],
        )


def test_segment_identity_rules_hold_in_the_meta_model():
    base = dict(
        bundle_schema_version=4, run_id="r", workflow_id="wf", status="completed",
        timestamp="2026-07-14T00:00:00Z", trace_path="trace.jsonl", detail_path="details.jsonl",
        usage_path="usage.jsonl", definition_path="definition.json", definition_digest="d" * 8,
        artifact_manifest_path="artifacts.json", artifact_root="artifacts",
        value_store_layout="run-scoped-sha256-gzip-v1", inline_body_max_bytes=4096,
        artifact_count=0, artifacts_copied=0, trace_count=0, detail_count=0, usage_count=0,
        trace_sha256=hashlib.sha256(b"").hexdigest(),
        detail_sha256=hashlib.sha256(b"").hexdigest(),
        usage_sha256=hashlib.sha256(b"").hexdigest(),
        incomplete_streams=[], stream_diagnostic=None,
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
    with pytest.raises(ValueError, match="only an abandoned bundle"):
        ObservationBundleMetaV4.model_validate(
            {
                **base,
                "incomplete_streams": ["trace"],
                "stream_diagnostic": "partial trace line",
                "provider_evidence": {
                    "integrity": "incomplete",
                    "diagnostic": "partial trace line",
                },
            }
        )
    with pytest.raises(ValueError, match="must not contain duplicates"):
        ObservationBundleMetaV4.model_validate(
            {
                **base,
                "status": "abandoned",
                "incomplete_streams": ["trace", "trace"],
                "stream_diagnostic": "partial trace line",
                "provider_evidence": {
                    "integrity": "incomplete",
                    "diagnostic": "partial trace line",
                },
            }
        )
    with pytest.raises(ValueError, match="must not carry a diagnostic"):
        ObservationBundleMetaV4.model_validate(
            {**base, "stream_diagnostic": "unbound diagnostic"}
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


def test_malformed_abandoned_attempt_does_not_poison_successful_retry_group(tmp_path):
    """Crash bytes remain intact and typed while a later canonical retry stays readable."""

    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta
    from ai_workflow_engine.segment_lifecycle import commit_attempt
    from ai_workflow_viewer import FileEventSource

    run_id = "crash-retry"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")

    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    dead.usage_sink.record(WorkflowUsageEvent(node="dead", total_tokens=7))
    dead.trace_path.write_bytes(b'{"node":"dead","event_id":')
    crashed_trace = dead.trace_path.read_bytes()
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=1,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )
    (dead.path / "abandoned.json").write_text('{"attempt":1,"superseded_by_attempt":2}')

    retry = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001-r2",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=2,
        ),
    )
    retry.finalize(definition, status="completed")
    assert commit_attempt(tmp_path, run_id, retry.segment.segment_id, attempt=2)

    dead_meta = load_bundle_meta_v4(dead.path)
    assert dead.trace_path.read_bytes() == crashed_trace, "reconciliation must retain exact crash bytes"
    assert dead_meta.incomplete_streams == ["trace"]
    assert dead_meta.provider_evidence.integrity == "incomplete"

    group = FileEventSource(tmp_path).read_group(run_id)
    assert group.status == "completed"
    assert [item.segment_id for item in group.non_canonical] == [dead.segment.segment_id]
    assert group.non_canonical[0].disposition == "abandoned"
    assert group.non_canonical_usage_totals["total_tokens"] == 7


def test_abandoned_reconciliation_recovers_segment_created_before_streams(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta

    run_id = "early-crash"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    for stream_path in (dead.trace_path, dead.detail_path, dead.usage_path):
        stream_path.unlink()
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=1,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )
    meta = load_bundle_meta_v4(dead.path)
    assert meta.status == "abandoned"
    assert set(meta.incomplete_streams) == {"trace", "detail", "usage"}
    assert (dead.path / "meta.json").is_file()


def test_abandoned_torn_suffix_preserves_valid_usage_prefix_and_totals(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta
    from ai_workflow_viewer import FileEventSource

    run_id = "torn-usage"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    for tokens in (7, 11, 13):
        dead.usage_sink.record(WorkflowUsageEvent(node="provider", total_tokens=tokens))
    with dead.usage_path.open("ab") as stream:
        stream.write(b'{"event_id":"torn"')
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=1,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )
    meta = load_bundle_meta_v4(dead.path)
    assert meta.incomplete_streams == ["usage"]
    assert meta.usage_count == 3
    assert meta.total_tokens == 31
    data = FileEventSource(dead.path).read()
    assert len(data.records) == 3
    assert sum(item.record.total_tokens for item in data.records) == 31


def test_abandoned_prefix_reader_rejects_persisted_prefix_tampering(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta
    from ai_workflow_viewer import FileEventSource

    run_id = "torn-prefix-tamper"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    dead.usage_sink.record(WorkflowUsageEvent(node="provider", total_tokens=7))
    dead.usage_path.open("ab").write(b'{"event_id":"torn"')
    dead.trace_sink.record(WorkflowTraceEvent(node="provider"))
    dead.trace_path.open("ab").write(b'{"event_id":"torn"')
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=1,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )
    with dead.trace_path.open("r+b") as stream:
        payload = stream.read()
        stream.seek(0)
        stream.write(payload.replace(b'"node":"provider"', b'"node":"tampered"', 1))
        stream.truncate()
    with pytest.raises(ValueError, match="SHA-256 validation|digest mismatch"):
        FileEventSource(dead.path).read()


def test_abandoned_torn_detail_suffix_preserves_valid_prefix(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta
    from ai_workflow_viewer import FileEventSource

    run_id = "torn-detail"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    first = _logical_detail({"value": 1}, detail_id="detail-1", run_id=run_id)
    second = _logical_detail({"value": 2}, detail_id="detail-2", run_id=run_id).model_copy(
        update={"event_id": "event-2", "sequence": None}
    )
    dead.detail_sink.record(first.model_copy(update={"sequence": None}))
    dead.detail_sink.record(second)
    dead.detail_path.open("ab").write(b'{"detail_id":"torn"')
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=1,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )

    data = FileEventSource(dead.path).read()
    assert [item.record.detail_id for item in data.records if item.type == "detail"] == [
        "detail-1",
        "detail-2",
    ]


def test_abandoned_missing_stream_requires_empty_digest(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta
    from ai_workflow_viewer import FileEventSource

    run_id = "missing-stream-digest"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    dead.usage_path.unlink()
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=1,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )
    meta_path = dead.path / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["usage_sha256"] = "f" * 64
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(FileNotFoundError, match="missing abandoned observation stream"):
        FileEventSource(dead.path).read()


def test_abandoned_metadata_adds_recovered_usage_to_prior_cumulative_total(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta

    run_id = "cumulative-recovery"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    prior = WorkflowUsageEvent(node="provider", total_tokens=10)
    initial.usage_sink.record(prior)
    initial.finalize(definition, status="completed", usage=[prior])
    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    dead.usage_sink.record(WorkflowUsageEvent(node="provider", total_tokens=31))
    dead.usage_path.open("ab").write(b'{"event_id":"torn"')
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=1,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )
    assert load_bundle_meta_v4(dead.path).total_tokens == 41


def test_abandoned_metadata_uses_committed_attempt_not_provisional_baseline(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta
    from ai_workflow_engine.segment_lifecycle import commit_attempt

    run_id = "canonical-cumulative-recovery"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")

    provisional = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    provisional_usage = WorkflowUsageEvent(
        node="provider", total_tokens=100, cost_class="metered", estimated_usd=1.0,
    )
    provisional.usage_sink.record(provisional_usage)
    provisional.finalize(definition, status="completed", usage=[provisional_usage])
    assert not (provisional.path / "commit.json").exists()
    assert not (provisional.path / "abandoned.json").exists()

    committed = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001-r2",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=2,
        ),
    )
    committed_usage = WorkflowUsageEvent(
        node="provider", total_tokens=20, cost_class="metered", estimated_usd=0.2,
    )
    committed.usage_sink.record(committed_usage)
    committed.finalize(definition, status="completed", usage=[committed_usage])
    assert commit_attempt(tmp_path, run_id, committed.segment.segment_id, attempt=2)
    assert (committed.path / "commit.json").is_file()
    assert load_bundle_meta_v4(committed.path).segment_index == 1

    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s002",
            segment_index=2,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    dead.usage_sink.record(
        WorkflowUsageEvent(
            node="provider", total_tokens=1, cost_class="metered", estimated_usd=0.01,
        )
    )
    dead.usage_path.open("ab").write(b'{"event_id":"torn"')
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=2,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )

    recovered = load_bundle_meta_v4(dead.path)
    assert recovered.total_tokens == 21
    assert recovered.metered_usd == pytest.approx(0.21)
    assert recovered.notional_usd is None


def test_abandoned_metadata_keeps_attemptless_suspension_usage_and_costs(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta

    run_id = "suspended-cumulative-recovery"
    definition = WorkflowBuilder("wf").step("s").build()
    suspended = open_observation_run_bundle(tmp_path, run_id)
    suspended_usage = WorkflowUsageEvent(
        node="provider", total_tokens=10, cost_class="subscription_notional", notional_usd=0.3,
        provider_reported_notional_usd=0.3,
        notional_pricing=NotionalPricingResult(
            source="provider_reported", amount_usd=0.3, catalog_version="provider-reported"
        ),
    )
    suspended.usage_sink.record(suspended_usage)
    suspended.finalize(definition, status="requires_user_input", usage=[suspended_usage])

    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    dead.usage_sink.record(
        WorkflowUsageEvent(
            node="provider", total_tokens=1, cost_class="subscription_notional", notional_usd=0.02,
            provider_reported_notional_usd=0.02,
            notional_pricing=NotionalPricingResult(
                source="provider_reported", amount_usd=0.02,
                catalog_version="provider-reported",
            ),
        )
    )
    dead.usage_path.open("ab").write(b'{"event_id":"torn"')
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=1,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )

    recovered = load_bundle_meta_v4(dead.path)
    assert recovered.total_tokens == 11
    assert recovered.metered_usd is None
    assert recovered.notional_usd == pytest.approx(0.32)


def test_abandoned_metadata_rejects_multiple_canonical_prior_attempts(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta
    from ai_workflow_engine.segment_lifecycle import commit_attempt

    run_id = "duplicate-canonical-recovery"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")

    for attempt, segment_id in (
        (2, f"{run_id}--s001-r2x"),
        (2, f"{run_id}--s001-r2y"),
    ):
        prior = open_observation_run_bundle(
            tmp_path,
            run_id,
            segment=ObservationSegment(
                segment_id=segment_id,
                segment_index=1,
                kind="resume",
                definition_digest=definition.definition_digest(),
                attempt=attempt,
            ),
        )
        prior.finalize(definition, status="completed")
        assert commit_attempt(tmp_path, run_id, segment_id, attempt=attempt)

    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s002",
            segment_index=2,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    with pytest.raises(ValueError, match="share ordinal 2"):
        write_minimal_abandoned_meta(
            dead.path,
            run_id=run_id,
            definition=definition,
            segment_index=2,
            definition_digest=definition.definition_digest(),
            attempt=1,
        )
    assert not (dead.path / "meta.json").exists()


def test_abandoned_metadata_uses_highest_committed_durable_attempt(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta
    from ai_workflow_engine.segment_lifecycle import commit_attempt

    run_id = "repeated-durable-recovery"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    for attempt, tokens in ((1, 10), (2, 20)):
        segment_id = f"{run_id}--s001" + ("" if attempt == 1 else "-r2")
        prior = open_observation_run_bundle(
            tmp_path,
            run_id,
            segment=ObservationSegment(
                segment_id=segment_id,
                segment_index=1,
                kind="resume",
                definition_digest=definition.definition_digest(),
                attempt=attempt,
            ),
        )
        usage = WorkflowUsageEvent(node="provider", total_tokens=tokens)
        prior.usage_sink.record(usage)
        prior.finalize(definition, status="completed", usage=[usage])
        assert commit_attempt(tmp_path, run_id, segment_id, attempt=attempt)

    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s002",
            segment_index=2,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    dead.usage_sink.record(WorkflowUsageEvent(node="provider", total_tokens=1))
    dead.usage_path.open("ab").write(b'{"event_id":"torn"')
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=2,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )
    assert load_bundle_meta_v4(dead.path).total_tokens == 21


def test_abandoned_metadata_uses_latest_attemptless_local_resume(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta

    run_id = "repeated-local-recovery"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    for suffix, timestamp, tokens in (
        ("aaa", "2026-09-01T10:00:00Z", 10),
        ("bbb", "2026-09-01T10:01:00Z", 20),
    ):
        prior = open_observation_run_bundle(
            tmp_path,
            run_id,
            segment=ObservationSegment(
                segment_id=f"{run_id}--s001-{suffix}",
                segment_index=1,
                kind="resume",
                definition_digest=definition.definition_digest(),
            ),
        )
        usage = WorkflowUsageEvent(node="provider", total_tokens=tokens)
        prior.usage_sink.record(usage)
        prior.finalize(definition, status="completed", usage=[usage])
        meta_path = prior.path / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["timestamp"] = timestamp
        meta_path.write_text(json.dumps(meta))

    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s002",
            segment_index=2,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    dead.usage_sink.record(WorkflowUsageEvent(node="provider", total_tokens=1))
    dead.usage_path.open("ab").write(b'{"event_id":"torn"')
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=2,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )
    assert load_bundle_meta_v4(dead.path).total_tokens == 21


def test_abandoned_reconciliation_never_certifies_incomplete_provider_call(tmp_path):
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta
    from ai_workflow_viewer import FileEventSource

    run_id = "abandoned-provider"
    definition = WorkflowBuilder("wf").step("s").build()
    initial = open_observation_run_bundle(tmp_path, run_id)
    initial.finalize(definition, status="completed")
    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    dead.trace_sink.record(
        WorkflowTraceEvent(
            node="provider",
            phase="provider:request",
            invocation_id="inv-crashed",
            detail_capture="capture_mode_off",
        )
    )
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=1,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )

    meta = load_bundle_meta_v4(dead.path)
    assert meta.incomplete_streams == []
    assert meta.provider_evidence.integrity == "incomplete"
    assert "exactly one usage event" in (meta.provider_evidence.diagnostic or "")
    assert FileEventSource(dead.path).read().meta.status == "abandoned"


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


def test_abandoned_attemptless_segment_is_excluded_from_prior_cumulative_totals(tmp_path):
    """An ATTEMPTLESS segment marked abandoned must not supply the cumulative baseline.

    A durable attempt is already excluded by the commit-marker rule (`attempt is not None`
    without `commit.json` is never canonical), so the `abandoned` signal in the candidate
    builder is load-bearing ONLY for an attemptless segment demoted by `abandoned.json`
    (or `status == "abandoned"`). Without this regression, replacing that signal with
    `abandoned=False` leaves the full bundle+viewer gate green while a demoted attemptless
    resume silently becomes the cumulative baseline.
    """
    from ai_workflow_engine.observation_writer import write_minimal_abandoned_meta

    run_id = "probe-attemptless-abandoned"
    definition = WorkflowBuilder("wf").step("s").build()
    base = open_observation_run_bundle(tmp_path, run_id)
    ev = WorkflowUsageEvent(node="provider", total_tokens=10)
    base.usage_sink.record(ev)
    base.finalize(definition, status="completed", usage=[ev])

    # attemptless resume at a LATER prior index, finalized, then abandoned by marker
    poisoned = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s001",
            segment_index=1,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=None,
        ),
    )
    bad = WorkflowUsageEvent(node="provider", total_tokens=999)
    poisoned.usage_sink.record(bad)
    poisoned.finalize(definition, status="completed", usage=[bad])
    (poisoned.path / "abandoned.json").write_text('{"reason":"superseded"}')

    dead = open_observation_run_bundle(
        tmp_path,
        run_id,
        segment=ObservationSegment(
            segment_id=f"{run_id}--s002",
            segment_index=2,
            kind="resume",
            definition_digest=definition.definition_digest(),
            attempt=1,
        ),
    )
    write_minimal_abandoned_meta(
        dead.path,
        run_id=run_id,
        definition=definition,
        segment_index=2,
        definition_digest=definition.definition_digest(),
        attempt=1,
    )
    import json as _j

    total = _j.loads((dead.path / "meta.json").read_text())["total_tokens"]
    print(f"\n[PROBE-E] cumulative={total} (canonical base=10, abandoned attemptless=999)")
    assert total == 10, (
        f"abandoned attemptless segment leaked into the cumulative baseline: got {total}, "
        "expected 10 from the canonical base segment"
    )
