"""Standalone viewer consumes engine observability records."""

from __future__ import annotations

import json

import pytest

from ai_workflow_engine import (
    ObservationDetail,
    WorkflowBuilder,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
)
from ai_workflow_viewer import JsonlObservationViewer

pytestmark = pytest.mark.unit


def _write_bundle(
    base,
    run_id: str,
    definition,
    *,
    trace_events=None,
    details=None,
    usage_events=None,
    dir_name=None,
    meta_extra=None,
):
    run_path = base / (dir_name or run_id)
    run_path.mkdir(parents=True)
    (run_path / "definition.json").write_text(definition.model_dump_json(), encoding="utf-8")
    (run_path / "trace.jsonl").write_text(
        "\n".join(event.model_dump_json() for event in (trace_events or [])) + "\n",
        encoding="utf-8",
    )
    (run_path / "details.jsonl").write_text(
        "\n".join(detail.model_dump_json(by_alias=True) for detail in (details or [])) + "\n",
        encoding="utf-8",
    )
    (run_path / "usage.jsonl").write_text(
        "\n".join(event.model_dump_json() for event in (usage_events or [])) + "\n",
        encoding="utf-8",
    )
    (run_path / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "workflow_id": definition.workflow_id,
                "timestamp": "2026-06-21T20:00:00Z",
                "definition_path": "definition.json",
                "trace_path": "trace.jsonl",
                "detail_path": "details.jsonl",
                "usage_path": "usage.jsonl",
                **(meta_extra or {}),
            }
        ),
        encoding="utf-8",
    )
    return run_path


def test_jsonl_observation_viewer_renders_html_from_public_contracts(tmp_path):
    definition = WorkflowBuilder("viewer").step("plan").step("render").build()
    event = WorkflowTraceEvent(
        node="plan",
        decision="llm:prompt",
        phase="llm:request",
        detail_refs=["detail-1"],
        run_id="run-1",
        sequence=1,
    )
    detail = ObservationDetail(
        detail_id="detail-1",
        event_id=event.event_id,
        kind="rendered_prompt",
        content_type="text/plain",
        digest="digest",
        run_id="run-1",
        sequence=2,
    )
    run_path = _write_bundle(
        tmp_path,
        "run-1",
        definition,
        trace_events=[
            event,
            WorkflowTraceEvent(node="render", decision="accepted", run_id="run-1", sequence=3),
        ],
        usage_events=[WorkflowUsageEvent(node="plan", total_tokens=9, estimated_usd=0.01, run_id="run-1", sequence=4)],
        details=[detail],
    )

    viewer = JsonlObservationViewer.from_run_bundle(run_path, title="Viewer test")

    html = viewer.html()
    assert "Viewer test" in html
    assert "Investigation Graph" in html
    assert 'data-node-id="plan"' in html
    assert "flowchart TD" in html
    assert "9 tok" in html
    assert "metered $0.0100" in html
    assert "digest" in html
    assert "<dialog" in html
    assert "Copy raw" in html
    assert [record["type"] for record in viewer.event_records()] == ["trace", "detail", "trace", "usage"]


def test_jsonl_observation_viewer_lists_runs_for_multi_run_bundle_sources(tmp_path):
    definition = WorkflowBuilder("viewer").step("plan").build()
    run_1_event = WorkflowTraceEvent(
        node="plan",
        decision="accepted",
        detail_refs=["detail-1"],
        run_id="run-1",
        sequence=1,
    )
    run_2_event = WorkflowTraceEvent(
        node="plan",
        decision="accepted",
        detail_refs=["detail-2"],
        run_id="run-2",
        sequence=1,
    )
    _write_bundle(
        tmp_path,
        "run-1",
        definition,
        trace_events=[run_1_event],
        details=[
            ObservationDetail(
                detail_id="detail-1",
                event_id=run_1_event.event_id,
                run_id="run-1",
                kind="rendered_prompt",
                digest="run-1-digest",
                sequence=2,
            ),
        ],
    )
    _write_bundle(
        tmp_path,
        "run-2",
        definition,
        trace_events=[run_2_event],
        details=[
            ObservationDetail(
                detail_id="detail-2",
                event_id=run_2_event.event_id,
                run_id="run-2",
                kind="rendered_prompt",
                digest="run-2-digest",
                sequence=2,
            ),
        ],
    )

    viewer = JsonlObservationViewer.from_run_bundle(tmp_path)
    index = viewer.html()
    assert "Workflow observations" in index
    assert "run-1" in index
    assert "run-2" in index
    assert "?run_id=run-2" in index

    selected = JsonlObservationViewer.from_run_bundle(tmp_path, run_id="run-2")
    html = selected.html()

    assert "run-2-digest" in html
    assert "run-1-digest" not in html
    assert [record["record"]["run_id"] for record in selected.event_records()] == ["run-2", "run-2"]


# ---------------------------------------------------------------------------
# W4.3/W4.4/W4.5 — grouped logical-run reading, honest merge, truthful render
# ---------------------------------------------------------------------------


def _segment_meta(run_id, segment_id, index, *, kind=None, parent=None, digest="dig-1", status="completed", timestamp=None, attempt=None):
    return {
        "run_id": run_id,
        "segment_id": segment_id,
        "segment_index": index,
        "segment_kind": kind or ("initial" if index == 0 else "resume"),
        "parent_segment_id": parent,  # informational only since R1 (lineage is logical)
        "definition_digest": digest,
        "usage_totals_scope": "run_cumulative_at_finalize",
        "status": status,
        "timestamp": timestamp or f"2026-07-11T10:0{index}:00Z",
        "total_tokens": 0,
        "metered_usd": None,
        "notional_usd": None,
        "usage_count": 0,
        "attempt": attempt,
    }


def _mark(run_path, marker):
    import json as _json

    (run_path / marker).write_text(_json.dumps({}), encoding="utf-8")


def _write_group(tmp_path, *, duplicate_usage_id=False, second_digest="dig-1"):
    """Suspension segment + resume segment of one logical run, per-segment sequences
    RESTARTING at 1 (the exact shape that broke the old global sequence check)."""

    from ai_workflow_engine import WorkflowBuilder

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    real_digest = definition.definition_digest()
    usage_0 = WorkflowUsageEvent(
        node="gate", total_tokens=7, estimated_usd=0.01, cost_class="metered",
        run_id="logical-run", sequence=3, event_id="usage-a",
    )
    _write_bundle(
        tmp_path,
        "logical-run",
        definition,
        trace_events=[
            WorkflowTraceEvent(node="gate", decision="llm:prompt", run_id="logical-run", sequence=1, event_id="t-1"),
            WorkflowTraceEvent(node="gate", node_status="requires_user_input", phase="node:result", run_id="logical-run", sequence=2, event_id="t-2"),
        ],
        usage_events=[usage_0],
        meta_extra=_segment_meta(
            "logical-run", "logical-run", 0, status="requires_user_input", digest=real_digest
        ) | {"total_tokens": 7, "metered_usd": 0.01, "usage_count": 1},
    )
    resume_usage = [
        WorkflowUsageEvent(
            node="finish", total_tokens=5, estimated_usd=0.02, cost_class="metered",
            run_id="logical-run", sequence=2, event_id="usage-b",
        )
    ]
    if duplicate_usage_id:
        resume_usage.append(
            WorkflowUsageEvent(
                node="finish", total_tokens=7, estimated_usd=0.01, cost_class="metered",
                run_id="logical-run", sequence=3, event_id="usage-a",  # copied from segment 0
            )
        )
    _write_bundle(
        tmp_path,
        "logical-run",
        definition,
        dir_name="logical-run--s001",
        trace_events=[
            WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="logical-run", sequence=1, event_id="t-3"),
            WorkflowTraceEvent(node="finish", node_status="completed", phase="node:result", run_id="logical-run", sequence=4, event_id="t-4"),
        ],
        usage_events=resume_usage,
        meta_extra=_segment_meta(
            "logical-run", "logical-run--s001", 1, parent="logical-run",
            digest=(real_digest if second_digest == "dig-1" else second_digest),
            status="completed",
        ) | {"total_tokens": 12, "metered_usd": 0.03, "usage_count": 2},
    )
    return definition


def test_read_group_merges_segments_ordered_with_per_segment_sequences(tmp_path):
    """W4.3: merged order is (segment_index, per-segment sequence); sequences restarting
    per segment are VALID (the old global duplicate-sequence check would have exploded);
    event ids stay globally unique; single-bundle read() semantics are untouched."""

    from ai_workflow_viewer import FileEventSource

    _write_group(tmp_path)
    source = FileEventSource(tmp_path)
    group = source.read_group("logical-run")

    assert group.run_id == "logical-run"
    assert [segment.segment_index for segment in group.segments] == [0, 1]
    assert group.status == "completed"
    assert [record.event_id for record in group.records] == ["t-1", "t-2", "usage-a", "t-3", "usage-b", "t-4"], (
        "merge must order by (segment_index, per-segment sequence)"
    )
    # duplicate sequence NUMBERS across segments are legal — each segment restarts at 1
    assert {record.sequence for record in group.records if record.type == "trace"} == {1, 2, 4}
    # single-bundle semantics unchanged: reading one segment alone still works
    single = FileEventSource(tmp_path / "logical-run--s001").read()
    assert single.run_id == "logical-run" and len(single.records) == 3


def test_read_group_aggregates_usage_once_and_labels_cumulative(tmp_path):
    """W4.4: group spend = segment-local events counted ONCE (7 + 5 tokens), while the
    engine's cumulative-at-finalize meta (12 on the resumed segment) is reported under its
    own label — summing metas would double-charge the pre-suspension half."""

    from ai_workflow_viewer import FileEventSource

    _write_group(tmp_path)
    group = FileEventSource(tmp_path).read_group("logical-run")

    assert group.usage_totals["total_tokens"] == 12  # 7 + 5, each event exactly once
    assert group.usage_totals["usage_count"] == 2
    assert group.usage_totals["metered_usd"] == 0.03
    assert group.usage_totals["scope"] == "actual_all_attempts"
    assert group.canonical_usage_totals["total_tokens"] == 12, (
        "with no non-canonical attempts, actual == canonical"
    )
    assert group.non_canonical_usage_totals["usage_count"] == 0
    assert group.cumulative_meta_totals["scope"] == "run_cumulative_at_finalize"
    assert group.cumulative_meta_totals["total_tokens"] == 12, (
        "cumulative label comes from the NEWEST segment's meta, never a sum of metas"
    )
    meta_sum = sum(segment.data.meta["total_tokens"] for segment in group.segments)
    assert meta_sum == 19 and group.usage_totals["total_tokens"] != meta_sum, (
        "the naive per-segment meta sum (19) double-charges — the reader must not use it"
    )


def test_read_group_duplicate_event_id_across_segments_is_loud(tmp_path):
    """W4.4: the same usage event id appearing in two segments is double-counted money —
    the group reader refuses instead of silently merging."""

    import pytest as _pytest

    from ai_workflow_viewer import FileEventSource

    _write_group(tmp_path, duplicate_usage_id=True)
    with _pytest.raises(ValueError, match="Duplicate observation event id across segments"):
        FileEventSource(tmp_path).read_group("logical-run")


def test_read_group_lineage_corruption_is_loud(tmp_path):
    """W4.3/R1: committed-ordinal collision, a gapped canonical chain, and definition
    splits each refuse loudly — no half-true merge. Physical parent pointers left the
    contract; the LOGICAL chain (contiguous committed indexes) is what integrity means."""

    import pytest as _pytest

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").build()

    grouped_definition = WorkflowBuilder("grouped").step("gate").step("finish").build()

    # committed-ordinal collision: two COMMITTED durable attempts share (index, attempt) —
    # impossible under single-claimant CAS, therefore corruption
    _write_group(tmp_path)
    for name in ("logical-run--s001", "logical-run--s001-r2x"):
        _write_bundle(
            tmp_path, "logical-run", grouped_definition, dir_name=name,
            meta_extra=_segment_meta(
                "logical-run", name, 1, digest=grouped_definition.definition_digest(),
                attempt=1,
            ),
        ) if name != "logical-run--s001" else None
    # give BOTH index-1 attempts the same ordinal + commit markers
    import json as _json

    s001_meta = tmp_path / "logical-run--s001" / "meta.json"
    meta = _json.loads(s001_meta.read_text(encoding="utf-8"))
    meta["attempt"] = 1
    s001_meta.write_text(_json.dumps(meta), encoding="utf-8")
    _mark(tmp_path / "logical-run--s001", "commit.json")
    _mark(tmp_path / "logical-run--s001-r2x", "commit.json")
    with _pytest.raises(ValueError, match="share ordinal"):
        FileEventSource(tmp_path).read_group("logical-run")

    # broken chain: the suspension half was deleted by hand -> not contiguous
    import shutil

    shutil.rmtree(tmp_path / "logical-run--s001-r2x")
    shutil.rmtree(tmp_path / "logical-run")
    with _pytest.raises(ValueError, match="contiguous|chain"):
        FileEventSource(tmp_path).read_group("logical-run")

    # forged/stale meta digest: the file recompute is authoritative (W4R.2)
    for path in tmp_path.iterdir():
        shutil.rmtree(path)
    _write_group(tmp_path, second_digest="dig-FORGED")
    with _pytest.raises(ValueError, match="forged or stale"):
        FileEventSource(tmp_path).read_group("logical-run")

    # ACTUAL definition split: correct metas, but segment 1 executes a different machine
    for path in tmp_path.iterdir():
        shutil.rmtree(path)
    other_definition = WorkflowBuilder("grouped").step("gate").step("finish").step("extra").build()
    _write_bundle(
        tmp_path, "split-run", definition,
        meta_extra=_segment_meta(
            "split-run", "split-run", 0, digest=definition.definition_digest()
        ),
    )
    _write_bundle(
        tmp_path, "split-run", other_definition, dir_name="split-run--s001",
        meta_extra=_segment_meta(
            "split-run", "split-run--s001", 1, parent="split-run",
            digest=other_definition.definition_digest(),
        ),
    )
    with _pytest.raises(ValueError, match="DIFFERENT actual workflow definitions"):
        FileEventSource(tmp_path).read_group("split-run")

    # gapped chain: canonical history at indexes 0 and 2 with nothing committed at 1 —
    # execution cannot skip a logical position, so the history is incomplete/corrupt
    for path in tmp_path.iterdir():
        shutil.rmtree(path)
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "skip-run", definition,
        meta_extra=_segment_meta("skip-run", "skip-run", 0, digest=digest),
    )
    _write_bundle(
        tmp_path, "skip-run", definition, dir_name="skip-run--s002",
        meta_extra=_segment_meta("skip-run", "skip-run--s002", 2, digest=digest),
    )
    with _pytest.raises(ValueError, match="contiguous"):
        FileEventSource(tmp_path).read_group("skip-run")

    # wrong kind: a continuation claiming to be 'initial'
    for path in tmp_path.iterdir():
        shutil.rmtree(path)
    _write_bundle(
        tmp_path, "kind-run", definition,
        meta_extra=_segment_meta("kind-run", "kind-run", 0, digest=digest),
    )
    _write_bundle(
        tmp_path, "kind-run", definition, dir_name="kind-run--s001",
        meta_extra=_segment_meta(
            "kind-run", "kind-run--s001", 1, parent="kind-run", digest=digest, kind="initial"
        ),
    )
    with _pytest.raises(ValueError, match="illegal kind|starts with exactly one"):
        FileEventSource(tmp_path).read_group("kind-run")


def test_read_group_handles_legacy_and_mixed_roots(tmp_path):
    """W4.1/W4R.2 degradation: pure v0.8.1 bundles read as groups of one; the REAL
    migration shape — a pre-W4 suspension bundle continued by a segmented resume — merges
    validly (parent = the legacy directory, digests recomputed equal); a continuation with
    NO recorded lineage is now LOUD (incomplete history is refused, not annotated), while
    its single bundle stays readable via plain read(). list_groups stays group-per-run."""

    import pytest as _pytest

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("legacy").step("gate").build()
    # pure v0.8.1 bundle: no segment fields at all
    _write_bundle(
        tmp_path, "old-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="old-run", sequence=1, event_id="o-1")],
        meta_extra={"status": "completed"},
    )
    # new-style segmented pair under another logical id
    _write_group(tmp_path)
    # the valid MIXED migration shape: legacy suspension + post-upgrade segmented resume
    _write_bundle(
        tmp_path, "mixed-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="requires_user_input", phase="node:result", run_id="mixed-run", sequence=1, event_id="m-0")],
        meta_extra={"status": "requires_user_input"},  # v0.8.1 suspension: no segment meta
    )
    _write_bundle(
        tmp_path, "mixed-run", definition, dir_name="mixed-run--s001-abc",
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="mixed-run", sequence=1, event_id="m-1")],
        meta_extra=_segment_meta(
            "mixed-run", "mixed-run--s001-abc", 1, parent="mixed-run",
            digest=definition.definition_digest(),
        ),
    )
    # lineage-less continuation (pre-W4 snapshot): REFUSED as incomplete history
    _write_bundle(
        tmp_path, "half-run", definition, dir_name="half-run--s001-abc",
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="half-run", sequence=1, event_id="h-1")],
        meta_extra=_segment_meta("half-run", "half-run--s001-abc", 1, parent=None, digest=None),
    )

    source = FileEventSource(tmp_path)
    old_group = source.read_group("old-run")
    assert len(old_group.segments) == 1 and old_group.segments[0].segment_index == 0
    assert old_group.segments[0].kind == "initial" and not old_group.non_canonical
    assert old_group.cumulative_meta_totals["scope"] == "single_bundle"

    mixed = source.read_group("mixed-run")
    assert [segment.segment_index for segment in mixed.segments] == [0, 1]
    assert mixed.status == "completed", "legacy suspension + segmented resume merge validly"

    with _pytest.raises(ValueError, match="contiguous|chain"):
        source.read_group("half-run")
    single = FileEventSource(tmp_path / "half-run--s001-abc").read()
    assert single.run_id == "half-run" and single.records, (
        "the refused group's single bundle must remain readable on its own"
    )

    groups = {entry["run_id"]: entry for entry in source.list_groups()}
    assert set(groups) == {"old-run", "logical-run", "mixed-run", "half-run"}
    assert groups["logical-run"]["segment_count"] == 2
    assert groups["logical-run"]["status"] == "completed"


def test_group_html_renders_one_truthful_lifecycle(tmp_path):
    """W4.5 (Q-R5 reproducer): the node that suspended in segment 0 and completed in
    segment 1 renders COMPLETED — never a false still-running/suspended state — and the
    segment strip + honest usage line are present with semantic markup."""

    from ai_workflow_viewer import FileEventSource, observation_group_to_html

    _write_group(tmp_path)
    group = FileEventSource(tmp_path).read_group("logical-run")
    html = observation_group_to_html(group)

    assert 'data-segment-index="0"' in html and 'data-segment-index="1"' in html
    assert 'data-kind="resume"' in html
    assert "Run segments (2)" in html
    assert "logical-run" in html
    assert "actual spend (all attempts, counted once)" in html
    assert "12 tokens" in html
    # the gate node must show its FINAL truth in the node table: completed, not suspended
    import re as _re

    gate_row = _re.search(r"<tr[^>]*>\s*<td><code>gate</code></td>.*?</tr>", html, _re.S)
    assert gate_row and "completed" in gate_row.group(0), (
        "suspended-then-completed node must render completed in the grouped view"
    )
    assert "suspended" not in (gate_row.group(0)), gate_row.group(0)


def test_single_resumed_segment_read_is_no_longer_empty(tmp_path):
    """Q-R5 regression (the original defect): reading the RESUMED segment's bundle alone
    now yields records attributable to the logical run id — before W4 the meta carried a
    '--resume-<uuid>' id while events carried the logical id, so the projection filtered
    everything out and rendered an empty run."""

    from ai_workflow_viewer import FileEventSource, build_observation_graph

    _write_group(tmp_path)
    data = FileEventSource(tmp_path / "logical-run--s001").read()
    assert data.run_id == "logical-run"
    graph = build_observation_graph(
        data.definition, data.trace_events, data.usage_events, data.details, run_id=data.run_id
    )
    assert graph.timeline, "resumed-segment projection must not be empty"
    assert graph.nodes["finish"].status == "completed"


def test_read_group_reports_abandoned_attempts_without_merging_them(tmp_path):
    """W4R.1 reader side: abandoned crash-attempts appear as typed evidence on the group
    (with their partial spend included in actual economics), but never enter canonical
    records, group status, or the index chain — and their duplicate index vs the successful
    retry is NOT a lineage error."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    digest = definition.definition_digest()
    _write_group(tmp_path)
    _write_bundle(
        tmp_path, "logical-run", definition, dir_name="logical-run--s001-dead",
        trace_events=[
            WorkflowTraceEvent(node="finish", decision="start", run_id="logical-run", sequence=1, event_id="dead-t1"),
        ],
        usage_events=[
            WorkflowUsageEvent(
                node="finish", total_tokens=3, estimated_usd=0.005,
                cost_class="metered", run_id="logical-run", sequence=2,
                event_id="dead-u1",
            ),
            WorkflowUsageEvent(
                node="finish", total_tokens=2, notional_usd=0.007,
                cost_class="subscription_notional", run_id="logical-run", sequence=3,
                event_id="dead-u2", metadata={"cost_known": True},
            ),
            WorkflowUsageEvent(
                node="finish", total_tokens=1, cost_class="metered",
                run_id="logical-run", sequence=4, event_id="dead-u3",
                metadata={"cost_known": False},
            ),
        ],
        meta_extra=_segment_meta(
            "logical-run", "logical-run--s001-dead", 1, parent="logical-run", digest=digest,
        ) | {"status": "abandoned", "abandoned_attempt": 1},
    )

    group = FileEventSource(tmp_path).read_group("logical-run")
    assert [segment.segment_index for segment in group.segments] == [0, 1]
    assert group.status == "completed"
    assert [segment.segment_id for segment in group.non_canonical] == ["logical-run--s001-dead"]
    assert group.non_canonical[0].disposition == "abandoned"
    # W4R.3 cost honesty: the crashed attempt's paid call is REAL spend — the primary
    # actual total includes it; the canonical/non-canonical split never hides money.
    assert group.usage_totals["total_tokens"] == 18, "actual spend includes abandoned work"
    assert group.usage_totals["metered_usd"] == 0.035
    assert group.usage_totals["notional_usd"] == 0.007
    assert group.usage_totals["unknown_cost_count"] == 1
    assert group.canonical_usage_totals["total_tokens"] == 12
    assert group.non_canonical_usage_totals["total_tokens"] == 6
    assert group.non_canonical_usage_totals["notional_usd"] == 0.007
    assert group.non_canonical_usage_totals["unknown_cost_count"] == 1
    assert group.usage_totals["total_tokens"] == (
        group.canonical_usage_totals["total_tokens"]
        + group.non_canonical_usage_totals["total_tokens"]
    ), "actual = canonical + non-canonical, nothing double-counted"
    assert all(record.event_id != "dead-t1" for record in group.records), (
        "non-canonical machine EVENTS still never merge into canonical history"
    )
    assert group.non_canonical[0].data.usage_events[0].estimated_usd == 0.005
    from ai_workflow_viewer import observation_group_to_html

    rendered = observation_group_to_html(group)
    assert "actual spend (all attempts, counted once)" in rendered
    assert "notional $0.007" in rendered
    assert "unknown-cost events 1" in rendered
    assert "non-canonical attempts: 6 tokens" in rendered


def test_wait_terminal_segment_closes_the_group_and_fake_definitions_are_loud(tmp_path):
    """W3R.3b reader side: a `wait_terminal` evidence segment (suspension failed with no
    continuation) is a legal FINAL chain member — the group reads `failed`, so viewers and
    retention agree with coordinator truth. Its definition.json is the REGISTERED bytes
    and is recomputed like any segment: a wait_terminal carrying a DIFFERENT machine is
    refused (the old exemption is gone)."""

    import pytest as _pytest

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "wfail-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="requires_user_input", phase="node:result", run_id="wfail-run", sequence=1, event_id="w-0")],
        meta_extra=_segment_meta("wfail-run", "wfail-run", 0, digest=digest, status="requires_user_input"),
    )
    _write_bundle(
        tmp_path, "wfail-run", definition, dir_name="wfail-run--s001-wfail",
        trace_events=[WorkflowTraceEvent(node="gate", decision="wait:failed", node_status="failed", phase="node:result", error="digest mismatch", run_id="wfail-run", sequence=1, event_id="w-1")],
        meta_extra=_segment_meta(
            "wfail-run", "wfail-run--s001-wfail", 1, parent="wfail-run",
            digest=digest, kind="wait_terminal", status="failed",
        ),
    )

    group = FileEventSource(tmp_path).read_group("wfail-run")
    assert [segment.kind for segment in group.segments] == ["initial", "wait_terminal"]
    assert group.status == "failed", (
        "a terminally failed wait must close the group — no suspended lie for retention"
    )

    # forge: same meta, but the evidence file carries a DIFFERENT machine
    other = WorkflowBuilder("grouped").step("gate").step("finish").step("extra").build()
    (tmp_path / "wfail-run--s001-wfail" / "definition.json").write_text(
        other.model_dump_json(), encoding="utf-8"
    )
    with _pytest.raises(ValueError, match="DIFFERENT actual workflow definitions|forged or stale"):
        FileEventSource(tmp_path).read_group("wfail-run")


def test_served_viewer_renders_the_whole_logical_run(tmp_path):
    """R3/F5 (permanent): the SERVED surface (JsonlObservationViewer -> serve_viewer) uses
    the grouped API — a suspended-then-resumed run renders as ONE completed lifecycle with
    one index row per logical run; the resumed half is no longer unreachable and the gate
    no longer reads suspended forever (the original Q-R5 defect, now closed end to end)."""

    import re as _re

    from ai_workflow_viewer import FileEventSource

    _write_group(tmp_path)
    viewer = JsonlObservationViewer(FileEventSource(tmp_path), title="Grouped serve test")

    rows = viewer.runs()
    assert [row["run_id"] for row in rows] == ["logical-run"], (
        "one index row per LOGICAL run — segments must not render as duplicate rows"
    )
    assert rows[0]["status"] == "completed" and rows[0]["segment_count"] == 2

    html = viewer.html("logical-run")
    assert "Run segments (2)" in html, "the served page is the grouped render"
    gate_row = _re.search(r"<tr[^>]*>\s*<td><code>gate</code></td>.*?</tr>", html, _re.S)
    assert gate_row and "completed" in gate_row.group(0)
    assert "suspended" not in gate_row.group(0), (
        "the suspended-then-completed gate must render completed through the SERVER path"
    )

    records = viewer.event_records("logical-run")
    assert len(records) == 6, "SSE/event stream carries the merged canonical records"


def test_chooser_and_detail_agree_on_double_local_resume(tmp_path):
    """R0R3-C2 (codex probe, permanent): the run chooser (list_groups) derives its counts
    from the SAME canonical selection as the detail page (read_group) — two local resumes
    at one index are 2 canonical + 1 superseded on BOTH surfaces, never 3 + 0."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "dl-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="requires_user_input", phase="node:result", run_id="dl-run", sequence=1, event_id="d-0")],
        meta_extra=_segment_meta("dl-run", "dl-run", 0, digest=digest, status="requires_user_input"),
    )
    for suffix, ts, event in (("aaa", "2026-07-12T10:01:00Z", "d-1"), ("bbb", "2026-07-12T10:02:00Z", "d-2")):
        _write_bundle(
            tmp_path, "dl-run", definition, dir_name=f"dl-run--s001-{suffix}",
            trace_events=[WorkflowTraceEvent(node="finish", node_status="completed", phase="node:result", run_id="dl-run", sequence=1, event_id=event)],
            meta_extra=_segment_meta(
                "dl-run", f"dl-run--s001-{suffix}", 1, digest=digest, timestamp=ts
            ),
        )

    source = FileEventSource(tmp_path)
    group = source.read_group("dl-run")
    assert len(group.segments) == 2 and len(group.non_canonical) == 1
    assert group.non_canonical[0].disposition == "superseded"
    assert group.segments[1].segment_id == "dl-run--s001-bbb", "latest committed local wins"

    rows = {row["run_id"]: row for row in source.list_groups()}
    assert rows["dl-run"]["segment_count"] == 2, (
        "the chooser must count CANONICAL segments exactly like the detail page"
    )
    assert rows["dl-run"]["non_canonical_count"] == 1


def test_related_run_id_filters_without_merging(tmp_path):
    """W5R.6: two INDEPENDENT runs sharing one Related-run ID stay two runs — the chooser
    exposes and filters by the related id (no state/history merge), one resumed run keeps
    ONE run id across its segments, and uncorrelated rows stay clear."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, JsonlObservationViewer, observation_group_to_html

    definition = WorkflowBuilder("cases").step("gate").build()
    digest = definition.definition_digest()
    for run_id, corr in (("case9-a", "case-9"), ("case9-b", "case-9"), ("lone-run", None)):
        extra = _segment_meta(run_id, run_id, 0, digest=digest)
        if corr:
            extra["correlation_id"] = corr
        _write_bundle(
            tmp_path, run_id, definition,
            trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id=run_id, sequence=1, event_id=f"e-{run_id}")],
            meta_extra=extra,
        )

    source = FileEventSource(tmp_path)
    rows = {r["run_id"]: r for r in source.list_groups()}
    assert rows["case9-a"]["related_run_id"] == "case-9"
    assert rows["lone-run"]["related_run_id"] is None

    related = source.list_groups(related_run_id="case-9")
    assert sorted(r["run_id"] for r in related) == ["case9-a", "case9-b"], (
        "the filter selects the case's runs WITHOUT merging them — two rows, two run ids"
    )
    assert all(r["segment_count"] == 1 for r in related)

    html = observation_group_to_html(source.read_group("case9-a"))
    assert "Related-run ID <code>case-9</code>" in html and "Run ID <code>case9-a</code>" in html
    index = JsonlObservationViewer(source).index_html()
    assert "Related-run ID" in index and index.count("case-9") >= 2


def test_group_related_run_identity_drift_is_corruption(tmp_path):
    """W5RR.3: one run has ONE related-run identity — segments claiming different ids,
    or present-vs-absent drift between same-contract segments, refuse loudly on the
    detail read and show status=corrupt on the chooser; legacy all-absent groups stay
    valid; a consistent group exposes ONE validated value on BOTH surfaces."""

    import pytest as _pytest

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").step("finish").build()
    digest = definition.definition_digest()

    def seg(run, name, idx, corr, **kw):
        extra = _segment_meta(run, name, idx, digest=digest, **kw)
        if corr is not None:
            extra["correlation_id"] = corr
        _write_bundle(tmp_path, run, definition, dir_name=name, meta_extra=extra)

    # (a) two different ids -> loud + corrupt row
    seg("drift-run", "drift-run", 0, "case-a", status="requires_user_input")
    seg("drift-run", "drift-run--s001-x", 1, "case-b")
    source = FileEventSource(tmp_path)
    with _pytest.raises(ValueError, match="DIFFERENT related-run ids"):
        source.read_group("drift-run")
    row = {r["run_id"]: r for r in source.list_groups()}["drift-run"]
    assert row["status"] == "corrupt", "the chooser must not silently pick first/newest"

    # (b) present-vs-absent drift between same-contract segments -> loud
    seg("half-corr", "half-corr", 0, "case-c", status="requires_user_input")
    seg("half-corr", "half-corr--s001-y", 1, None)
    with _pytest.raises(ValueError, match="identity drift"):
        source.read_group("half-corr")

    # (b2) an ABANDONED attempt claiming a different case is the SAME corruption —
    # its spend counts in actual economics, so its identity must match the group's
    seg("aband-drift", "aband-drift", 0, "case-d", status="requires_user_input")
    extra = _segment_meta("aband-drift", "aband-drift--s001-x", 1, digest=digest)
    extra["correlation_id"] = "case-ELSE"
    extra["status"] = "abandoned"
    _write_bundle(tmp_path, "aband-drift", definition, dir_name="aband-drift--s001-x", meta_extra=extra)
    with _pytest.raises(ValueError, match="DIFFERENT related-run ids"):
        source.read_group("aband-drift")
    assert {r["run_id"]: r for r in source.list_groups()}["aband-drift"]["status"] == "corrupt"

    # (c) consistent group: ONE validated value on detail AND chooser
    seg("good-run", "good-run", 0, "case-ok", status="requires_user_input")
    seg("good-run", "good-run--s001-z", 1, "case-ok")
    group = source.read_group("good-run")
    assert group.related_run_id == "case-ok"
    rows = {r["run_id"]: r for r in source.list_groups()}
    assert rows["good-run"]["related_run_id"] == "case-ok"


def test_served_viewer_filter_is_real_http_behavior(tmp_path):
    """W5RR.4: the SERVED chooser filters by Related-run ID — query parsed, exactly the
    case's rows rendered (still separate runs), links preserve the filter, clear-filter
    present, and the no-match state is explicit."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, JsonlObservationViewer

    definition = WorkflowBuilder("cases").step("gate").build()
    digest = definition.definition_digest()
    for run_id, corr in (("f-a", "case-9"), ("f-b", "case-9"), ("f-lone", None)):
        extra = _segment_meta(run_id, run_id, 0, digest=digest)
        if corr:
            extra["correlation_id"] = corr
        _write_bundle(
            tmp_path, run_id, definition,
            trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id=run_id, sequence=1, event_id=f"e-{run_id}")],
            meta_extra=extra,
        )

    viewer = JsonlObservationViewer(FileEventSource(tmp_path), title="filter test")
    page = viewer.html(related_run_id="case-9")  # what do_GET renders for ?related_run_id=
    assert "Filtered by Related-run ID" in page and "clear filter" in page
    assert "f-a" in page and "f-b" in page and "f-lone" not in page, (
        "exactly the case's runs render — still two separate rows"
    )
    assert 'href="?run_id=f-a&related_run_id=case-9"' in page, (
        "open links preserve the active filter for back-navigation"
    )
    assert 'href="?related_run_id=case-9"' in viewer.index_html(), (
        "the Related-run ID cell is the clickable filter control"
    )
    empty = viewer.html(related_run_id="case-none")
    assert "No runs for Related-run ID" in empty and "case-none" in empty

    # OUTERMOST boundary: the real HTTP handler must parse the query itself —
    # calling viewer.html directly would leave do_GET's parsing untested.
    import threading
    import urllib.request

    from ai_workflow_viewer import serve_viewer

    server = serve_viewer(viewer, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(
            f"http://{host}:{port}/?related_run_id=case-9", timeout=5
        ) as response:
            served = response.read().decode("utf-8")
        assert "f-a" in served and "f-b" in served and "f-lone" not in served, (
            "the SERVED page must apply the query filter, not just the Python API"
        )
        assert "Filtered by Related-run ID" in served
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_group_html_resolves_artifacts_with_previews_and_skip_reasons(tmp_path):
    """Q4.2 repair: artifact IDs resolve through artifacts.json into USER-FACING evidence —
    image artifacts render a preview + clickable bundle-local link, uncopied entries show
    their honest skip reason, and a caller-supplied href base keeps links relative."""

    import json as _json

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, observation_group_to_html

    definition = WorkflowBuilder("arty").step("gate").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "art-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="art-run", sequence=1, event_id="a-1")],
        meta_extra=_segment_meta("art-run", "art-run", 0, digest=digest),
    )
    bundle = tmp_path / "art-run"
    (bundle / "artifacts").mkdir()
    (bundle / "artifacts" / "frame.png").write_bytes(b"\x89PNG fake")
    (bundle / "artifacts.json").write_text(_json.dumps([
        {"artifact_id": "art-1", "bundle_path": "artifacts/frame.png", "copied": True,
         "kind": "media", "media_type": "image/png", "owner_node": "gate",
         "size_bytes": 9, "skip_reason": None},
        {"artifact_id": "art-2", "bundle_path": None, "copied": False,
         "kind": "media", "media_type": "image/png", "owner_node": "gate",
         "size_bytes": 999, "skip_reason": "exceeds artifact_max_bytes"},
    ]), encoding="utf-8")

    group = FileEventSource(tmp_path).read_group("art-run")
    page = observation_group_to_html(group, artifact_href_for=lambda p: "../rel/art-run")
    assert '<img src="../rel/art-run/artifacts/frame.png"' in page, "image preview rendered"
    assert '<a href="../rel/art-run/artifacts/frame.png">' in page, "clickable bundle-local link"
    assert "NOT archived: exceeds artifact_max_bytes" in page, "honest skip reason"
    default_page = observation_group_to_html(group)  # absolute-path fallback stays clickable
    assert f'{bundle}/artifacts/frame.png' in default_page


def test_served_artifact_links_resolve_over_real_http(tmp_path):
    """Invariant sweep (Q4.2 sibling surface): the SERVED group page must link archived
    artifacts through a route the browser can actually fetch — /artifact/... returns the
    manifest-listed bytes with the manifest media type, while non-manifest files (meta.json)
    and traversal shapes 404 even though they exist on disk."""

    import json as _json
    import threading
    import urllib.request
    from urllib.error import HTTPError

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, JsonlObservationViewer, serve_viewer

    definition = WorkflowBuilder("served-arty").step("gate").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "served-art-run", definition,
        trace_events=[WorkflowTraceEvent(node="gate", node_status="completed", phase="node:result", run_id="served-art-run", sequence=1, event_id="sa-1")],
        meta_extra=_segment_meta("served-art-run", "served-art-run", 0, digest=digest),
    )
    bundle = tmp_path / "served-art-run"
    (bundle / "artifacts").mkdir()
    png = b"\x89PNG served"
    (bundle / "artifacts" / "frame.png").write_bytes(png)
    (tmp_path / "outside.secret").write_bytes(b"NEVER SERVED")
    (bundle / "artifacts.json").write_text(_json.dumps([
        {"artifact_id": "sa-art", "bundle_path": "artifacts/frame.png", "copied": True,
         "media_type": "image/png", "owner_node": "gate", "size_bytes": len(png)},
        # hostile manifest row: even a LISTED entry must not escape its segment dir
        {"artifact_id": "sa-evil", "bundle_path": "../outside.secret", "copied": True,
         "media_type": "text/plain", "owner_node": "gate", "size_bytes": 12},
    ]), encoding="utf-8")

    viewer = JsonlObservationViewer(FileEventSource(tmp_path))
    server = serve_viewer(viewer, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_address[1]}"
        page = urllib.request.urlopen(f"{root}/?run_id=served-art-run", timeout=5).read().decode()
        href = "/artifact/served-art-run/served-art-run/artifacts/frame.png"
        assert f'<img src="{href}"' in page and f'<a href="{href}"' in page, page[-2000:]
        fetched = urllib.request.urlopen(root + href, timeout=5)
        assert fetched.headers["Content-Type"] == "image/png"
        assert fetched.read() == png, "served bytes must be the archived artifact"
        for bad in (
            "/artifact/served-art-run/served-art-run/meta.json",       # exists, NOT in manifest
            "/artifact/served-art-run/served-art-run/artifacts/../meta.json",  # traversal shape
            "/artifact/other-run/served-art-run/artifacts/frame.png",  # wrong run
            "/artifact/served-art-run/served-art-run/../outside.secret",  # manifest-listed but escapes
        ):
            try:
                urllib.request.urlopen(root + bad, timeout=5)
                raise AssertionError(f"{bad} must 404, not serve bundle internals")
            except HTTPError as err:
                assert err.code == 404
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_external_nodes_are_labeled_never_silently_disconnected(tmp_path):
    """User-settled UX honesty (Q4.2 round): activity outside the declared graph (fanout
    item calls, provenance markers) renders as EXTERNAL cards with an explicit
    "outside declared graph" note + a canvas legend — and the legend appears ONLY when
    such nodes exist, so ordinary pages carry no dead boilerplate."""

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource, observation_group_to_html

    definition = WorkflowBuilder("ext-label").step("declared_step").build()
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "ext-run", definition,
        trace_events=[
            WorkflowTraceEvent(node="declared_step", node_status="completed", phase="node:result", run_id="ext-run", sequence=1, event_id="x-1"),
            # fanout-item-style activity: node id NOT in the definition
            WorkflowTraceEvent(node="probe_item", node_status="completed", phase="tool:result", run_id="ext-run", sequence=2, event_id="x-2"),
        ],
        meta_extra=_segment_meta("ext-run", "ext-run", 0, digest=digest),
    )
    page = observation_group_to_html(FileEventSource(tmp_path).read_group("ext-run"))
    assert "outside declared graph — no wiring recorded" in page, "external card must be labeled"
    assert "EXTERNAL cards are recorded activity outside" in page, "canvas legend missing"
    assert page.count("outside declared graph — no wiring recorded") == 1, "only the external card is labeled"

    _write_bundle(
        tmp_path / "plain", "plain-run", definition,
        trace_events=[WorkflowTraceEvent(node="declared_step", node_status="completed", phase="node:result", run_id="plain-run", sequence=1, event_id="p-1")],
        meta_extra=_segment_meta("plain-run", "plain-run", 0, digest=digest),
    )
    plain = observation_group_to_html(FileEventSource(tmp_path / "plain").read_group("plain-run"))
    assert "EXTERNAL cards are recorded activity" not in plain, "legend must be conditional"
    assert "outside declared graph — no wiring recorded" not in plain
