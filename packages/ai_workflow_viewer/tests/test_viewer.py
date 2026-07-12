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


def _segment_meta(run_id, segment_id, index, *, kind=None, parent=None, digest="dig-1", status="completed", timestamp=None):
    return {
        "run_id": run_id,
        "segment_id": segment_id,
        "segment_index": index,
        "segment_kind": kind or ("initial" if index == 0 else "resume"),
        "parent_segment_id": parent,
        "definition_digest": digest,
        "usage_totals_scope": "run_cumulative_at_finalize",
        "status": status,
        "timestamp": timestamp or f"2026-07-11T10:0{index}:00Z",
        "total_tokens": 0,
        "metered_usd": None,
        "notional_usd": None,
        "usage_count": 0,
    }


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
        "with no abandoned attempts, actual == canonical"
    )
    assert group.abandoned_usage_totals["usage_count"] == 0
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
    """W4.3: duplicate segment index, a NAMED parent that is absent, and mixed definition
    digests each refuse loudly — no half-true merge."""

    import pytest as _pytest

    from ai_workflow_engine import WorkflowBuilder
    from ai_workflow_viewer import FileEventSource

    definition = WorkflowBuilder("grouped").step("gate").build()

    grouped_definition = WorkflowBuilder("grouped").step("gate").step("finish").build()

    # duplicate index: two finalized segments both claiming index 1
    _write_group(tmp_path)
    _write_bundle(
        tmp_path, "logical-run", grouped_definition, dir_name="logical-run--s001-r2",
        meta_extra=_segment_meta(
            "logical-run", "logical-run--s001-r2", 1, parent="logical-run",
            digest=grouped_definition.definition_digest(),
        ),
    )
    with _pytest.raises(ValueError, match="[Dd]uplicate segment index"):
        FileEventSource(tmp_path).read_group("logical-run")

    # broken chain: the suspension half was deleted by hand -> not contiguous
    import shutil

    shutil.rmtree(tmp_path / "logical-run--s001-r2")
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

    # skipped parent: index 2 names index 0 as parent while index 1 exists
    for path in tmp_path.iterdir():
        shutil.rmtree(path)
    digest = definition.definition_digest()
    _write_bundle(
        tmp_path, "skip-run", definition,
        meta_extra=_segment_meta("skip-run", "skip-run", 0, digest=digest),
    )
    _write_bundle(
        tmp_path, "skip-run", definition, dir_name="skip-run--s001",
        meta_extra=_segment_meta("skip-run", "skip-run--s001", 1, parent="skip-run", digest=digest),
    )
    _write_bundle(
        tmp_path, "skip-run", definition, dir_name="skip-run--s002",
        meta_extra=_segment_meta("skip-run", "skip-run--s002", 2, parent="skip-run", digest=digest),
    )
    with _pytest.raises(ValueError, match="preceding segment"):
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
    assert old_group.segments[0].kind == "initial" and not old_group.abandoned
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
    assert [segment.segment_id for segment in group.abandoned] == ["logical-run--s001-dead"]
    # W4R.3 cost honesty: the crashed attempt's paid call is REAL spend — the primary
    # actual total includes it; the canonical/abandoned split never hides money.
    assert group.usage_totals["total_tokens"] == 18, "actual spend includes abandoned work"
    assert group.usage_totals["metered_usd"] == 0.035
    assert group.usage_totals["notional_usd"] == 0.007
    assert group.usage_totals["unknown_cost_count"] == 1
    assert group.canonical_usage_totals["total_tokens"] == 12
    assert group.abandoned_usage_totals["total_tokens"] == 6
    assert group.abandoned_usage_totals["notional_usd"] == 0.007
    assert group.abandoned_usage_totals["unknown_cost_count"] == 1
    assert group.usage_totals["total_tokens"] == (
        group.canonical_usage_totals["total_tokens"]
        + group.abandoned_usage_totals["total_tokens"]
    ), "actual = canonical + abandoned, nothing double-counted"
    assert all(record.event_id != "dead-t1" for record in group.records), (
        "abandoned machine EVENTS still never merge into canonical history"
    )
    assert group.abandoned[0].data.usage_events[0].estimated_usd == 0.005
    from ai_workflow_viewer import observation_group_to_html

    rendered = observation_group_to_html(group)
    assert "actual spend (all attempts, counted once)" in rendered
    assert "notional $0.007" in rendered
    assert "unknown-cost events 1" in rendered
    assert "abandoned attempts: 6 tokens" in rendered


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
