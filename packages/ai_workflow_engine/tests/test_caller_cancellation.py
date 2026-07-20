"""Caller-cancellation lifecycle: completed evidence survives a cancelled run."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest
from pydantic import BaseModel

from ai_workflow_engine import (
    LocalWaitPolicy,
    ObservationConfig,
    StructuredLLMNode,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowGoal,
    WorkflowProfile,
    load_bundle_meta_v2,
)
from ai_workflow_engine.engine.external import (
    ExternalProcessCapability,
    ExternalProcessRequest,
)
from ai_workflow_engine.models import (
    CapabilityResult,
    SafetyPolicy,
    WorkflowArtifact,
    WorkflowUsageEvent,
)
from ai_workflow_engine.run_artifacts import (
    ArtifactIdentityConflict,
    RunArtifactJournal,
)
from ai_workflow_engine.usage_contract import NormalizedTokenUsage
from ai_workflow_engine.usage_events import record_usage_event


pytestmark = pytest.mark.unit


def _artifact(path: Path, artifact_id: str) -> WorkflowArtifact:
    path.write_bytes(artifact_id.encode("utf-8"))
    return WorkflowArtifact(
        artifact_id=artifact_id,
        path=str(path),
        kind="media",
        source="cancellation-test",
        metadata={"role": "screenshot", "media_type": "image/png"},
    )


def _manifest(bundle: Path) -> list[dict]:
    return json.loads((bundle / "artifacts.json").read_text(encoding="utf-8"))


def test_run_artifact_journal_is_ordered_idempotent_atomic_and_alias_free(
    tmp_path: Path,
) -> None:
    first = _artifact(tmp_path / "first.png", "first")
    second = _artifact(tmp_path / "second.png", "second")
    journal = RunArtifactJournal([first])

    journal.retain([first.model_copy(deep=True), second])
    assert [artifact.artifact_id for artifact in journal.snapshot()] == ["first", "second"]

    snapshot = journal.snapshot()
    snapshot[0].path = "caller-mutated"
    assert journal.snapshot()[0].path == str(tmp_path / "first.png")

    blank = first.model_copy(update={"artifact_id": "   "})
    with pytest.raises(ValueError, match="artifact_id must be nonblank"):
        journal.retain([blank])

    conflicting = first.model_copy(update={"path": str(tmp_path / "different.png")})
    with pytest.raises(ArtifactIdentityConflict, match="different evidence"):
        journal.retain(
            [
                _artifact(tmp_path / "never-committed.png", "third"),
                conflicting,
            ]
        )
    assert [artifact.artifact_id for artifact in journal.snapshot()] == ["first", "second"]


async def test_caller_cancellation_finalizes_bundle_and_preserves_completed_artifact(
    tmp_path: Path,
) -> None:
    """MageQA reproducer: cancellation propagates but completed evidence remains readable."""

    bundle_root = tmp_path / "bundles"
    artifact_path = tmp_path / "captured.png"
    blocked = asyncio.Event()

    async def capture(_context, payload):
        artifact_path.write_bytes(b"same-run-evidence")
        record_usage_event(
            WorkflowUsageEvent(
                provider="codex_exec",
                node="capture",
                operation="tool",
                cost_class="subscription_notional",
                model="gpt-5.4-codex",
                input_tokens=120,
                output_tokens=30,
                total_tokens=150,
                normalized_usage=NormalizedTokenUsage(
                    counter_schema="codex_inclusive",
                    uncached_input_tokens=100,
                    cache_read_input_tokens=20,
                    cache_creation_input_tokens=0,
                    non_reasoning_output_tokens=20,
                    reasoning_output_tokens=10,
                    raw_input_tokens=120,
                    raw_output_tokens=30,
                    raw_total_tokens=150,
                ),
                metadata={"stage": "before-cancellation"},
            )
        )
        return CapabilityResult(
            output=payload,
            artifacts=[
                WorkflowArtifact(
                    artifact_id="capture-artifact",
                    path=str(artifact_path),
                    kind="media",
                    source="cancel-canary",
                    owner_node="capture",
                    metadata={"role": "screenshot", "media_type": "image/png"},
                )
            ],
        )

    async def wait_forever(_context, payload):
        blocked.set()
        await asyncio.Event().wait()
        return payload

    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(bundle_root), artifacts="copy")
    )
    builder.register_capability("capture", capture, kind="deterministic")
    builder.register_capability("wait_forever", wait_forever, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("cancel-canary")
        .step("capture")
        .step("wait_forever")
        .build()
    )
    task = asyncio.create_task(
        builder.build().run(
            "cancel-canary",
            {"goal": "preserve completed evidence when cancelled"},
            goal=WorkflowGoal(
                workflow_type="cancel-canary",
                objective="Exercise caller cancellation.",
                metadata={"run_id": "cancel-canary-run"},
            ),
        )
    )

    await asyncio.wait_for(blocked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    bundle = bundle_root / "cancel-canary-run"
    meta = load_bundle_meta_v2(bundle)
    assert meta.status == "cancelled"
    assert meta.artifact_count == 1
    assert meta.trace_count > 0
    assert meta.detail_count > 0
    assert meta.usage_count == 1
    from ai_workflow_viewer import FileEventSource

    [usage] = FileEventSource(bundle).read().usage_events
    assert usage.notional_pricing is not None
    assert usage.notional_pricing.amount_usd == pytest.approx(0.000705)
    assert usage.notional_pricing.rate is not None
    assert usage.notional_pricing.rate.rate_version == "openai-2026-07-20"
    manifest = _manifest(bundle)
    assert [entry["artifact_id"] for entry in manifest] == ["capture-artifact"]
    assert manifest[0]["copied"] is True
    assert (bundle / manifest[0]["bundle_path"]).read_bytes() == b"same-run-evidence"


async def test_failed_finalization_uses_the_same_completed_artifact_journal(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "before-terminal-failure.png"
    bundle_root = tmp_path / "failed-bundles"

    async def capture(_context, payload):
        return CapabilityResult(
            output=payload,
            artifacts=[
                _artifact(artifact_path, "before-terminal-failure"),
            ],
        )

    def fail_terminal_projection(_envelope):
        raise RuntimeError("terminal projection failed")

    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(bundle_root), artifacts="copy")
    )
    builder.register_capability("capture", capture, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("failed-journal").step("capture").build()
    )

    with pytest.raises(RuntimeError, match="terminal projection failed"):
        await builder.build().run(
            "failed-journal",
            {},
            goal=WorkflowGoal(
                workflow_type="failed-journal",
                objective="retain evidence when terminal projection fails",
                metadata={"run_id": "failed-journal-run"},
            ),
            terminal_status=fail_terminal_projection,
        )

    bundle = bundle_root / "failed-journal-run"
    assert load_bundle_meta_v2(bundle).status == "failed"
    [entry] = _manifest(bundle)
    assert entry["artifact_id"] == "before-terminal-failure"
    assert entry["copied"] is True
    assert (bundle / entry["bundle_path"]).read_bytes() == b"before-terminal-failure"


async def test_cancellation_before_first_capability_result_finalizes_empty_bundle(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    async def wait_forever(_context, payload):
        started.set()
        await asyncio.Event().wait()
        return payload

    root = tmp_path / "bundles"
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(root))
    )
    builder.register_capability("wait", wait_forever, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("cancel-empty").step("wait").build())
    task = asyncio.create_task(
        builder.build().run(
            "cancel-empty",
            {},
            goal=WorkflowGoal(
                workflow_type="cancel-empty",
                objective="cancel before completion",
                metadata={"run_id": "cancel-empty-run"},
            ),
        )
    )

    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel("operator-stop")
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.args == ("operator-stop",)

    bundle = root / "cancel-empty-run"
    assert load_bundle_meta_v2(bundle).status == "cancelled"
    assert _manifest(bundle) == []
    trace = (bundle / "trace.jsonl").read_text(encoding="utf-8")
    assert '"decision":"cancelled"' in trace.replace(" ", "")


async def test_cancelled_plain_llm_finalizes_one_linked_unknown_usage_event(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    class Output(BaseModel):
        label: str

    class BlockingSubscriptionLLM:
        provider_label = "codex_exec"
        subscription_mode = True

        async def __call__(self, _request):
            started.set()
            await asyncio.Event().wait()

    node = StructuredLLMNode(
        name="inspect",
        config=object(),
        output_model=Output,
        prompt_template="Inspect {item}.",
        input_variables=["item"],
        llm=BlockingSubscriptionLLM(),
        max_repair_rounds=0,
    )

    async def inspect(_context, payload):
        return await node.run({"item": payload["item"]})

    root = tmp_path / "llm-cancel-bundles"
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(root), capture="full")
    )
    builder.register_capability("inspect", inspect, kind="llm", metered=True)
    builder.register_workflow(
        WorkflowBuilder("cancel-llm").step("inspect").build()
    )
    task = asyncio.create_task(
        builder.build().run(
            "cancel-llm",
            {"item": "banner"},
            goal=WorkflowGoal(
                workflow_type="cancel-llm",
                objective="Preserve cancelled provider evidence.",
                metadata={"run_id": "cancel-llm-run"},
            ),
        )
    )

    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel("operator-stop")
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.args == ("operator-stop",)

    from ai_workflow_viewer import FileEventSource

    run = FileEventSource(root / "cancel-llm-run").read()
    assert run.meta.status == "cancelled"
    assert len(run.usage_events) == 1
    usage = run.usage_events[0]
    assert usage.success is False
    assert usage.usage_error == "usage_event_missing"
    assert usage.notional_pricing is not None
    assert usage.notional_pricing.source == "unknown"
    assert usage.notional_usd is None
    assert usage.invocation_id
    assert usage.elapsed_ms is not None and usage.elapsed_ms >= 0
    linked = [
        event for event in run.trace_events
        if event.invocation_id == usage.invocation_id
    ]
    assert {event.phase for event in linked} >= {
        "llm:request",
        "llm:response",
    }


async def test_fanout_retains_completed_siblings_before_aggregate_commit(
    tmp_path: Path,
) -> None:
    first_done = asyncio.Event()
    second_done = asyncio.Event()
    blocker_started = asyncio.Event()
    never = asyncio.Event()

    async def worker(_context, item):
        if item["kind"] == "block":
            blocker_started.set()
            await never.wait()
            return item
        if item["index"] == 1:
            await first_done.wait()
        artifact = _artifact(tmp_path / f"fan-{item['index']}.png", f"fan-{item['index']}")
        if item["index"] == 0:
            first_done.set()
        else:
            second_done.set()
        return CapabilityResult(
            status="partial" if item["index"] == 1 else "accepted",
            output=item,
            artifacts=[artifact],
            error="inspection incomplete" if item["index"] == 1 else None,
        )

    root = tmp_path / "bundles"
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(root))
    )
    builder.register_capability("worker", worker, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("cancel-fanout")
        .fanout(
            "inspect",
            capability="worker",
            items_key="payload.items",
            max_parallel=3,
            max_items=3,
        )
        .build()
    )
    task = asyncio.create_task(
        builder.build().run(
            "cancel-fanout",
            {
                "items": [
                    {"kind": "done", "index": 0},
                    {"kind": "done", "index": 1},
                    {"kind": "block", "index": 2},
                ]
            },
            goal=WorkflowGoal(
                workflow_type="cancel-fanout",
                objective="retain completed siblings",
                metadata={"run_id": "cancel-fanout-run"},
            ),
        )
    )

    await asyncio.wait_for(
        asyncio.gather(second_done.wait(), blocker_started.wait()),
        timeout=5,
    )
    assert not task.done(), "fanout must still be blocked when caller cancellation is issued"
    assert task.cancel(), "caller cancellation must target a live fanout run"
    with pytest.raises(asyncio.CancelledError):
        await task

    bundle = root / "cancel-fanout-run"
    assert load_bundle_meta_v2(bundle).status == "cancelled"
    assert [entry["artifact_id"] for entry in _manifest(bundle)] == ["fan-0", "fan-1"]


@pytest.mark.parametrize("door", ["declared", "registered"])
async def test_child_workflow_artifact_survives_parent_cancellation(
    tmp_path: Path,
    door: str,
) -> None:
    blocker_started = asyncio.Event()

    async def child_work(_context, payload):
        return CapabilityResult(
            output=payload,
            artifacts=[_artifact(tmp_path / f"{door}.png", f"{door}-artifact")],
        )

    async def block(_context, payload):
        blocker_started.set()
        await asyncio.Event().wait()
        return payload

    root = tmp_path / f"bundles-{door}"
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(root))
    )
    builder.register_capability("child_work", child_work, kind="deterministic")
    builder.register_capability("block", block, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder(f"{door}-child").step("child_work").build()
    )
    if door == "declared":
        parent = (
            WorkflowBuilder(f"{door}-parent")
            .subworkflow("run_child", workflow=f"{door}-child")
            .step("block")
            .build()
        )
    else:
        parent = (
            WorkflowBuilder(f"{door}-parent")
            .step("child_cap")
            .step("block")
            .build()
        )
    builder.register_workflow(parent)
    engine = builder.build()
    if door == "registered":
        engine.register_workflow_capability("child_cap", f"{door}-child")
    run_id = f"{door}-parent-run"
    task = asyncio.create_task(
        engine.run(
            f"{door}-parent",
            {"door": door},
            goal=WorkflowGoal(
                workflow_type=f"{door}-parent",
                objective="retain child evidence",
                metadata={"run_id": run_id},
            ),
        )
    )

    await asyncio.wait_for(blocker_started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    entries = _manifest(root / run_id)
    assert [entry["artifact_id"] for entry in entries] == [f"{door}-artifact"]


@pytest.mark.parametrize("door", ["declared", "registered"])
async def test_child_artifact_is_deduplicated_on_normal_close(
    tmp_path: Path,
    door: str,
) -> None:
    async def child_work(_context, payload):
        return CapabilityResult(
            output=payload,
            artifacts=[_artifact(tmp_path / "child.png", "child-artifact")],
        )

    root = tmp_path / f"normal-{door}-child-bundles"
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(root))
    )
    builder.register_capability("child_work", child_work, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("normal-child").step("child_work").build()
    )
    if door == "declared":
        parent = (
            WorkflowBuilder("normal-parent")
            .subworkflow("child_node", workflow="normal-child")
            .build()
        )
    else:
        parent = WorkflowBuilder("normal-parent").step("child_cap").build()
    builder.register_workflow(parent)
    engine = builder.build()
    if door == "registered":
        engine.register_workflow_capability("child_cap", "normal-child")

    result = await engine.run(
        "normal-parent",
        {},
        goal=WorkflowGoal(
            workflow_type="normal-parent",
            objective="deduplicate child projection",
            metadata={"run_id": "normal-parent-run"},
        ),
    )

    assert result.status == "completed"
    assert [artifact.artifact_id for artifact in result.artifacts] == ["child-artifact"]
    assert [row["artifact_id"] for row in _manifest(root / "normal-parent-run")] == [
        "child-artifact"
    ]


class _Gate(BaseModel):
    status: str
    value: str = ""


async def test_resumed_cancellation_preserves_snapshot_and_new_artifacts(
    tmp_path: Path,
) -> None:
    blocker_started = asyncio.Event()

    async def before(_context, payload):
        return CapabilityResult(
            output=payload,
            artifacts=[_artifact(tmp_path / "before.png", "before-wait")],
        )

    def ask(context, _payload):
        event = context.metadata.get("resume_event")
        return _Gate(status="pending") if event is None else _Gate(status="answered", value=str(event))

    async def after(_context, payload):
        return CapabilityResult(
            output=payload,
            artifacts=[_artifact(tmp_path / "after.png", "after-wait")],
        )

    async def block(_context, payload):
        blocker_started.set()
        await asyncio.Event().wait()
        return payload

    root = tmp_path / "resume-bundles"
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(root))
    )
    builder.register_capability("before", before, kind="deterministic")
    builder.register_capability("ask", ask, kind="deterministic")
    builder.register_capability("after", after, kind="deterministic")
    builder.register_capability("block", block, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("cancel-resume")
        .step("before")
        .human("ask", wait_policy=LocalWaitPolicy())
        .step("after")
        .step("block")
        .build()
    )
    engine = builder.build()
    first = await engine.run(
        "cancel-resume",
        {},
        goal=WorkflowGoal(
            workflow_type="cancel-resume",
            objective="cancel the continuation",
            metadata={"run_id": "cancel-resume-run"},
        ),
    )
    assert first.status == "requires_user_input"
    assert first.snapshot is not None
    persisted_snapshot = type(first.snapshot).model_validate_json(first.snapshot.to_json())
    assert [artifact["artifact_id"] for artifact in persisted_snapshot.artifacts] == [
        "before-wait"
    ]

    task = asyncio.create_task(engine.resume(persisted_snapshot, "continue"))
    await asyncio.wait_for(blocker_started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    resume_segments = []
    for path in root.iterdir():
        if not (path / "meta.json").exists():
            continue
        meta = load_bundle_meta_v2(path)
        if meta.run_id == "cancel-resume-run" and meta.segment_index == 1:
            resume_segments.append(path)
    assert len(resume_segments) == 1
    resume_bundle = resume_segments[0]
    assert load_bundle_meta_v2(resume_bundle).status == "cancelled"
    assert [entry["artifact_id"] for entry in _manifest(resume_bundle)] == [
        "before-wait",
        "after-wait",
    ]
    from ai_workflow_viewer import FileEventSource

    group = FileEventSource(root).read_group("cancel-resume-run")
    assert group.status == "cancelled"
    assert [segment.status for segment in group.segments] == [
        "requires_user_input",
        "cancelled",
    ]


async def test_cancelled_bundle_artifact_policy_off_keeps_truth_without_copy(
    tmp_path: Path,
) -> None:
    blocked = asyncio.Event()

    async def capture(_context, payload):
        return CapabilityResult(
            output=payload,
            artifacts=[_artifact(tmp_path / "off.png", "off-artifact")],
        )

    async def block(_context, payload):
        blocked.set()
        await asyncio.Event().wait()
        return payload

    root = tmp_path / "off-bundles"
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(root), artifacts="off")
    )
    builder.register_capability("capture", capture, kind="deterministic")
    builder.register_capability("block", block, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("cancel-off").step("capture").step("block").build()
    )
    task = asyncio.create_task(
        builder.build().run(
            "cancel-off",
            {},
            goal=WorkflowGoal(
                workflow_type="cancel-off",
                objective="manifest only",
                metadata={"run_id": "cancel-off-run"},
            ),
        )
    )

    await asyncio.wait_for(blocked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    [entry] = _manifest(root / "cancel-off-run")
    assert entry["artifact_id"] == "off-artifact"
    assert entry["copied"] is False
    assert entry["bundle_path"] is None
    assert entry["skip_reason"] == "artifact_policy_off"


async def test_cancellation_with_observation_disabled_re_raises_without_bundle(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    async def block(_context, payload):
        started.set()
        await asyncio.Event().wait()
        return payload

    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(
            enabled=False,
            bundle_dir=str(tmp_path / "must-not-exist"),
        )
    )
    builder.register_capability("block", block, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("cancel-unobserved").step("block").build()
    )
    task = asyncio.create_task(builder.build().run("cancel-unobserved", {}))

    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / "must-not-exist").exists()


async def test_observation_disabled_does_not_fingerprint_completed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cancellation journal is an observation cost, not a simple-tier artifact tax."""

    import ai_workflow_engine.run_artifacts as run_artifacts

    def forbidden_fingerprint(_artifact):
        raise AssertionError("artifact journal ran with observation disabled")

    monkeypatch.setattr(run_artifacts, "_artifact_fingerprint", forbidden_fingerprint)

    async def capture(_context, payload):
        return CapabilityResult(
            output=payload,
            artifacts=[_artifact(tmp_path / "simple.png", "simple-artifact")],
        )

    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(
            enabled=False,
            bundle_dir=str(tmp_path / "must-not-exist"),
        )
    )
    builder.register_capability("capture", capture, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("simple-artifact").step("capture").build()
    )

    result = await builder.build().run("simple-artifact", {})
    assert result.status == "completed"
    assert [artifact.artifact_id for artifact in result.artifacts] == [
        "simple-artifact"
    ]


async def test_concurrent_cancelled_runs_keep_artifact_journals_isolated(
    tmp_path: Path,
) -> None:
    both_captured = asyncio.Event()
    captured = 0
    lock = asyncio.Lock()

    async def capture(context, payload):
        nonlocal captured
        run_id = context.run_context.workflow_id
        artifact = _artifact(tmp_path / f"{run_id}.png", f"artifact-{run_id}")
        async with lock:
            captured += 1
            if captured == 2:
                both_captured.set()
        return CapabilityResult(output=payload, artifacts=[artifact])

    async def block(_context, payload):
        await both_captured.wait()
        await asyncio.Event().wait()
        return payload

    root = tmp_path / "isolated-bundles"
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(root))
    )
    builder.register_capability("capture", capture, kind="deterministic")
    builder.register_capability("block", block, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("cancel-isolated").step("capture").step("block").build()
    )
    engine = builder.build()

    tasks = [
        asyncio.create_task(
            engine.run(
                "cancel-isolated",
                {},
                goal=WorkflowGoal(
                    workflow_type="cancel-isolated",
                    objective="isolate run artifacts",
                    metadata={"run_id": run_id},
                ),
            )
        )
        for run_id in ("run-a", "run-b")
    ]
    await asyncio.wait_for(both_captured.wait(), timeout=5)
    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task

    assert [row["artifact_id"] for row in _manifest(root / "run-a")] == [
        "artifact-run-a"
    ]
    assert [row["artifact_id"] for row in _manifest(root / "run-b")] == [
        "artifact-run-b"
    ]


async def test_engine_cancellation_keeps_artifact_and_reaps_external_process(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "child.pid"

    async def capture(_context, payload):
        return CapabilityResult(
            output=payload,
            artifacts=[_artifact(tmp_path / "before-process.png", "before-process")],
        )

    process = ExternalProcessCapability(name="process")
    root = tmp_path / "process-bundles"
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(root))
    )
    builder.register_capability("capture", capture, kind="deterministic")
    builder.register_capability("process", process, spec=process.spec)
    builder.register_workflow(
        WorkflowBuilder("cancel-process").step("capture").step("process").build(),
        profile=WorkflowProfile(
            workflow_type="cancel-process",
            safety=SafetyPolicy(allowed_side_effects=["external_call"]),
        ),
    )
    request = ExternalProcessRequest(
        command=[
            sys.executable,
            "-c",
            (
                "import os,pathlib,sys,time; "
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                "time.sleep(30)"
            ),
            str(pid_file),
        ],
        timeout_s=30,
    )
    task = asyncio.create_task(
        builder.build().run(
            "cancel-process",
            request,
            goal=WorkflowGoal(
                workflow_type="cancel-process",
                objective="reap process and preserve prior artifact",
                metadata={"run_id": "cancel-process-run"},
            ),
        )
    )

    for _ in range(200):
        if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip():
            break
        await asyncio.sleep(0.025)
    else:
        task.cancel()
        raise AssertionError("external child never started")
    pid = int(pid_file.read_text(encoding="utf-8"))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)
        raise AssertionError("cancelled engine run left its external process alive")

    assert load_bundle_meta_v2(root / "cancel-process-run").status == "cancelled"
    assert [row["artifact_id"] for row in _manifest(root / "cancel-process-run")] == [
        "before-process"
    ]


async def test_cancellation_bundle_finalization_failure_is_loud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_workflow_engine.observation_writer as observation_writer

    started = asyncio.Event()

    async def block(_context, payload):
        started.set()
        await asyncio.Event().wait()
        return payload

    def broken_finalize(self, definition, *, status, usage=None, artifacts=None):
        raise RuntimeError("observation archive unavailable")

    monkeypatch.setattr(
        observation_writer.ObservationRunBundle,
        "finalize",
        broken_finalize,
    )
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(tmp_path / "broken"))
    )
    builder.register_capability("block", block, kind="deterministic")
    builder.register_workflow(WorkflowBuilder("cancel-broken").step("block").build())
    task = asyncio.create_task(builder.build().run("cancel-broken", {}))

    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(RuntimeError, match="observation archive unavailable") as caught:
        await task
    assert isinstance(caught.value.__cause__, asyncio.CancelledError)
