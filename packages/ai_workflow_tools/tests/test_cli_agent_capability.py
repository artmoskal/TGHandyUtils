from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from ai_workflow_engine import (
    ObservationConfig,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowGoal,
    WorkflowProfile,
)
from ai_workflow_engine.engine.capabilities import CapabilityRegistry, CapabilityRuntime, InMemoryTraceSink
from ai_workflow_engine.execution_window import (
    ExecutionWindowInputs,
    TaskExecutionRequest,
    resolve_execution_window,
)
from ai_workflow_engine.models import (
    CapabilityResult,
    EvidenceRef,
    SafetyPolicy,
    WorkflowUsageSummary,
)
from ai_workflow_engine.budget import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope
from ai_workflow_viewer import (
    FileEventSource,
    build_observation_graph,
    observation_graph_to_html,
)

from ai_workflow_tools.cli_agents import (
    CliAgentCapability,
    CliAgentRequest,
    McpServerConfig,
    claude_p,
    codex_exec,
)

pytestmark = pytest.mark.unit


def _fake_flavor(flavor, fake_cli_path: Path):
    return flavor.model_copy(update={"base_argv": [sys.executable, str(fake_cli_path)]})


def _configure_fake_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    workspace: Path,
    *,
    mode: str,
    result: str = '{"ok": true}',
    sleep_s: str = "5",
) -> Path:
    record_path = tmp_path / f"record-{mode}.json"
    monkeypatch.setenv("FAKE_CLI_MODE", mode)
    monkeypatch.setenv("FAKE_CLI_RESULT", result)
    monkeypatch.setenv("FAKE_CLI_RECORD", str(record_path))
    monkeypatch.setenv("FAKE_CLI_WORKSPACE", str(workspace))
    monkeypatch.setenv("FAKE_CLI_SLEEP_S", sleep_s)
    return record_path


async def test_claude_flavor_happy_path_maps_envelope_usage_and_subscription_cost(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    record_path = _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="envelope")
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), name="browser_agent")
    request = CliAgentRequest(
        prompt="Open the dashboard",
        workspace_dir=str(workspace),
        mcp_servers=[McpServerConfig(name="browser", command="npx", args=["@playwright/mcp"])],
        allowed_tools=["mcp__browser__click"],
        timeout_s=30,
    )
    summary = WorkflowUsageSummary()
    usage_context = WorkflowUsageContext(
        capability_context.run_context,
        summary,
        WorkflowBudget(max_estimated_usd=0.01),
    )

    with workflow_usage_scope(usage_context):
        cap_result = await cap(capability_context, request)

    result = cap_result.output
    assert cap_result.status == "accepted"
    assert result.status == "completed"
    assert result.text == '{"ok": true}'
    assert result.parsed == {"ok": True}
    assert result.input_tokens == 11
    assert result.output_tokens == 7
    assert result.cache_read_tokens == 3
    assert result.cache_creation_tokens == 5
    assert result.num_turns == 2
    assert result.duration_ms == 50
    assert result.notional_cost_usd == 0.123

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["stdin"] == "Open the dashboard"
    assert record["argv"] == [
        str(fake_cli_path),
        "--mcp-config",
        str(workspace / "mcp_config.json"),
        "--strict-mcp-config",
        "--allowedTools",
        "mcp__browser__click",
        "--output-format",
        "json",
    ]
    assert summary.metered_usd is None
    assert summary.notional_usd == 0.123
    usage = summary.events[0]
    assert usage.provider == "claude_p"
    assert usage.operation == "tool"
    assert usage.cost_class == "subscription_notional"
    assert usage.notional_usd == 0.123


async def test_claude_metered_mode_books_provider_total_only_as_metered_cost(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace-metered"
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="envelope")
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), name="metered_agent")
    request = CliAgentRequest(
        prompt="Inspect",
        workspace_dir=str(workspace),
        timeout_s=30,
        subscription_mode=False,
    )
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(capability_context.run_context, summary, WorkflowBudget())
    ):
        cap_result = await cap(capability_context, request)

    assert cap_result.status == "accepted"
    assert summary.metered_usd == pytest.approx(0.123)
    assert summary.notional_usd is None
    usage = summary.events[0]
    assert usage.cost_class == "metered"
    assert usage.estimated_usd == pytest.approx(0.123)
    assert usage.notional_usd is None
    assert usage.provider_reported_notional_usd is None
    assert usage.notional_pricing is None


async def test_claude_zero_exit_error_envelope_fails_capability_and_records_burn(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace-error-envelope"
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="envelope")
    monkeypatch.setenv(
        "FAKE_CLI_STDOUT_OVERRIDE",
        json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "",
                "total_cost_usd": 0.044,
                "usage": {"input_tokens": 15, "output_tokens": 4},
            }
        ),
    )
    monkeypatch.setenv("FAKE_CLI_EXIT_CODE", "0")
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), name="agent")
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(
            capability_context.run_context,
            summary,
            WorkflowBudget(),
        )
    ):
        result = await cap(
            capability_context,
            CliAgentRequest(
                prompt="Inspect",
                workspace_dir=str(workspace),
                timeout_s=30,
            ),
        )

    assert result.status == "failed"
    assert result.output.status == "error"
    assert "error_during_execution" in (result.error or "")
    assert summary.events[0].success is False
    assert summary.events[0].notional_usd == pytest.approx(0.044)


async def test_mcp_startup_failure_is_a_loud_diagnosable_episode(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    """V1 (MageQA §2): a broken MCP config must yield a diagnosable FAILED episode —
    never an empty success. claude_p runs with --strict-mcp-config (assembly-tested),
    so a server that cannot start makes the worker exit nonzero with the diagnostic
    on stderr; the capability must surface that, not swallow it."""

    workspace = tmp_path / "workspace"
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="mcp_startup_failure")
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), name="browser_agent")
    request = CliAgentRequest(
        prompt="Open the dashboard",
        workspace_dir=str(workspace),
        mcp_servers=[McpServerConfig(name="browser", command="definitely-not-installed")],
        timeout_s=30,
    )
    summary = WorkflowUsageSummary()
    usage_context = WorkflowUsageContext(
        capability_context.run_context,
        summary,
        WorkflowBudget(max_estimated_usd=0.01),
    )

    with workflow_usage_scope(usage_context):
        cap_result = await cap(capability_context, request)

    assert cap_result.status == "failed"
    assert cap_result.error, "MCP startup failure produced no error message"
    result = cap_result.output
    assert result.status == "error"
    assert result.returncode == 1
    assert "MCP server 'browser' failed to start" in result.stderr_tail
    # the failed run is still a recorded episode (visible in usage/observability),
    # not a silently missing one
    assert summary.events, "failed MCP startup left no usage episode"


async def test_codex_flavor_reads_result_file_and_records_final_structured_usage(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    record_path = _configure_fake_cli(
        monkeypatch,
        tmp_path,
        workspace,
        mode="result_file",
        result='Done: {"ok": true}',
    )
    cap = CliAgentCapability(_fake_flavor(codex_exec, fake_cli_path), name="codex_agent")
    request = CliAgentRequest(
        prompt="Inspect", workspace_dir=str(workspace), model="gpt-5.4-codex", timeout_s=30
    )
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(WorkflowUsageContext(capability_context.run_context, summary, WorkflowBudget())):
        cap_result = await cap(capability_context, request)

    result = cap_result.output
    assert cap_result.status == "accepted"
    assert result.status == "completed"
    assert result.text == 'Done: {"ok": true}'
    assert result.parsed == {"ok": True}
    assert result.input_tokens == 120
    assert result.output_tokens == 30
    assert result.cache_read_tokens == 20
    assert result.notional_cost_usd == pytest.approx(0.000705)
    assert summary.notional_usd == pytest.approx(0.000705)
    usage = summary.events[0]
    assert usage.input_tokens == 120
    assert usage.output_tokens == 30
    assert usage.invocation_id == result.invocation_id
    assert usage.elapsed_ms == result.elapsed_ms
    assert usage.input_token_details["cache_read"] == 20
    assert usage.output_token_details["reasoning"] == 10
    assert usage.estimated_usd is None
    assert usage.notional_usd == pytest.approx(0.000705)

    record = json.loads(record_path.read_text(encoding="utf-8"))
    # v0.11.15: the prompt travels as stdin bytes; argv carries only the stdin marker.
    assert record["stdin"] == "Inspect"
    assert record["argv"][-1] == "-"
    assert "Inspect" not in record["argv"]
    assert "--output-last-message" in record["argv"]
    assert record["argv"].count("--json") == 1


async def test_codex_image_agent_persists_one_linked_provider_invocation_graph(
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    from ai_workflow_tools.toolsets import BASH_SIDE_EFFECTS

    workspace = tmp_path / "workspace-linked"
    bundle_root = tmp_path / "bundles"
    _configure_fake_cli(
        monkeypatch,
        tmp_path,
        workspace,
        mode="result_file",
        result='Inspected image: {"label": "banner"}',
    )
    image_ref = EvidenceRef(
        role="image",
        uri="file:///fixture/banner.png",
        media_type="image/png",
    )
    cap = CliAgentCapability(
        _fake_flavor(codex_exec, fake_cli_path),
        name="codex_agent",
        asset_loader=lambda ref: b"\x89PNG\r\n\x1a\nfixture" if ref == image_ref else b"",
    )
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(
            enabled=True,
            bundle_dir=str(bundle_root),
            capture="full",
        )
    )
    builder.register_capability_spec(cap.spec, cap)
    builder.register_workflow(
        WorkflowBuilder("codex_linked").step("codex_agent").build(),
        profile=WorkflowProfile(
            workflow_type="codex_linked",
            safety=SafetyPolicy(allowed_side_effects=list(BASH_SIDE_EFFECTS)),
        ),
    )
    engine = builder.build()

    result = await engine.run(
        "codex_linked",
        CliAgentRequest(
            prompt="Inspect the staged banner image.",
            workspace_dir=str(workspace),
            input_assets=[image_ref],
            model="gpt-5.4-codex",
            timeout_s=30,
        ),
        goal=WorkflowGoal(
            workflow_type="codex_linked",
            objective="Prove provider evidence linkage.",
            metadata={"run_id": "codex-linked-run"},
        ),
    )

    assert result.status == "completed"
    run = FileEventSource(result.observation_bundle_path).read()
    assert len(run.usage_events) == 1
    usage = run.usage_events[0]
    assert usage.invocation_id
    assert usage.elapsed_ms is not None and usage.elapsed_ms >= 0
    linked_traces = [
        trace for trace in run.trace_events
        if trace.invocation_id == usage.invocation_id
    ]
    assert {trace.phase for trace in linked_traces} >= {
        "provider:request",
        "provider:response",
        "tool:result",
    }
    linked_details = [
        detail for detail in run.details
        if detail.invocation_id == usage.invocation_id
    ]
    assert linked_details
    assert all(trace.detail_capture == "captured" for trace in linked_traces)
    assert all(detail.invocation_id == usage.invocation_id for detail in linked_details)
    detail_json = json.dumps(
        [detail.model_dump(by_alias=True) for detail in linked_details],
        default=str,
    )
    assert "input_fingerprints" in detail_json
    assert "process_result" in detail_json

    graph = build_observation_graph(
        run.definition,
        run.trace_events,
        run.usage_events,
        run.details,
        run_id=run.run_id,
    )
    page = observation_graph_to_html(
        run.definition,
        graph,
        usage_events=run.usage_events,
    )
    assert str(usage.invocation_id) in page
    assert f"{usage.elapsed_ms} ms" in page

    usage_path = Path(result.observation_bundle_path) / "usage.jsonl"
    row = json.loads(usage_path.read_text(encoding="utf-8"))
    row.pop("invocation_id")
    usage_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one usage event"):
        FileEventSource(result.observation_bundle_path).read()


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [("usage_then_fail", "failed"), ("usage_then_sleep", "partial")],
)
async def test_codex_failure_and_timeout_keep_latest_structured_usage(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
    mode,
    expected_status,
):
    workspace = tmp_path / f"workspace-{mode}"
    _configure_fake_cli(
        monkeypatch,
        tmp_path,
        workspace,
        mode=mode,
        sleep_s="30",
    )
    cap = CliAgentCapability(_fake_flavor(codex_exec, fake_cli_path), name="codex_agent")
    request = CliAgentRequest(
        prompt="Inspect",
        workspace_dir=str(workspace),
        model="gpt-5.4-codex",
        timeout_s=0.5 if mode == "usage_then_sleep" else 30,
    )
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(
            capability_context.run_context,
            summary,
            WorkflowBudget(),
        )
    ):
        cap_result = await cap(capability_context, request)

    assert cap_result.status == expected_status
    assert len(summary.events) == 1
    usage = summary.events[0]
    assert usage.success is False
    assert usage.input_tokens == 120
    assert usage.output_tokens == 30
    assert usage.notional_usd == pytest.approx(0.000705)
    assert usage.notional_pricing is not None
    assert usage.notional_pricing.source == "configured_public_rate"


async def test_codex_cancellation_records_latest_usage_once_and_reaps_process(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace-cancel"
    ready = tmp_path / "codex-ready.pid"
    _configure_fake_cli(
        monkeypatch,
        tmp_path,
        workspace,
        mode="usage_then_sleep",
        sleep_s="30",
    )
    monkeypatch.setenv("FAKE_CLI_READY_FILE", str(ready))
    cap = CliAgentCapability(_fake_flavor(codex_exec, fake_cli_path), name="codex_agent")
    request = CliAgentRequest(
        prompt="Inspect",
        workspace_dir=str(workspace),
        model="gpt-5.4-codex",
        timeout_s=30,
    )
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(
            capability_context.run_context,
            summary,
            WorkflowBudget(),
        )
    ):
        task = asyncio.create_task(cap(capability_context, request))
        for _ in range(200):
            if ready.exists():
                break
            await asyncio.sleep(0.01)
        assert ready.exists(), "fake Codex process never reached the cancellation barrier"
        pid = int(ready.read_text(encoding="utf-8"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(summary.events) == 1
    usage = summary.events[0]
    assert usage.node == "codex_agent"
    assert usage.success is False
    assert usage.input_tokens == 120
    assert usage.notional_usd == pytest.approx(0.000705)
    assert usage.invocation_id
    assert usage.elapsed_ms is not None and usage.elapsed_ms >= 0
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_timeout_returns_partial_and_salvages_png(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="sleep", sleep_s="5")
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), name="browser_agent")
    # 0.5s (not 0.1s): the fake needs python-startup + stdin + PNG-write headroom under
    # parallel-suite CPU load; 0.1s flaked while still being a real timeout test.
    request = CliAgentRequest(prompt="Capture", workspace_dir=str(workspace), timeout_s=0.5)

    cap_result = await cap(capability_context, request)

    result = cap_result.output
    assert cap_result.status == "partial"
    assert result.status == "truncated"
    assert result.new_artifact_count == 1
    assert [artifact.role for artifact in result.artifacts] == ["screenshot"]
    assert cap_result.artifacts[0].kind == "media"


async def test_lenient_json_parses_chatter_and_preserves_garbage(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), name="browser_agent")
    request = CliAgentRequest(prompt="Inspect", workspace_dir=str(workspace), timeout_s=30)
    _configure_fake_cli(
        monkeypatch,
        tmp_path,
        workspace,
        mode="envelope",
        result='Here:\n```json\n{"value": 42}\n```',
    )

    parsed_result = await cap(capability_context, request)

    assert parsed_result.output.parsed == {"value": 42}

    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="garbage")
    garbage_result = await cap(capability_context, request)

    assert garbage_result.output.text == "not json at all\n"
    assert garbage_result.output.parsed is None


async def test_side_effect_denial_happens_before_process_spawn(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    record_path = _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="envelope")
    cap = CliAgentCapability(
        _fake_flavor(claude_p, fake_cli_path),
        name="browser_agent",
        side_effects=["browser_drive"],
    )
    registry = CapabilityRegistry()
    registry.register(cap.spec, cap)
    runtime = CapabilityRuntime(registry)

    result = await runtime.invoke(
        "browser_agent",
        CliAgentRequest(prompt="Drive browser", workspace_dir=str(workspace)),
        capability_context,
    )

    assert result.status == "rejected"
    assert "browser_drive" in result.error
    assert not record_path.exists()


async def test_workspace_snapshot_excludes_pre_existing_artifacts_from_new_count(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "screenshot.png").write_bytes(b"old")
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="artifacts")
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), name="browser_agent")

    cap_result = await cap(
        capability_context,
        CliAgentRequest(prompt="Collect", workspace_dir=str(workspace), timeout_s=30),
    )

    result = cap_result.output
    assert result.new_artifact_count == 2
    assert sorted(artifact.role for artifact in result.artifacts) == ["page_record", "screenshot", "session"]
    screenshot = next(artifact for artifact in cap_result.artifacts if artifact.metadata["role"] == "screenshot")
    assert screenshot.metadata["new"] is False


async def test_input_assets_are_staged_with_fingerprint_trace_and_not_counted_as_new(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="envelope")
    trace_sink = InMemoryTraceSink()
    payloads = {"image": b"image-bytes", "note": b"note-bytes"}

    def load_asset(ref: EvidenceRef) -> bytes:
        return payloads[ref.role]

    cap = CliAgentCapability(
        _fake_flavor(claude_p, fake_cli_path),
        name="browser_agent",
        asset_loader=load_asset,
        trace_sink=trace_sink,
    )
    request = CliAgentRequest(
        prompt="Use inputs",
        workspace_dir=str(workspace),
        input_assets=[
            EvidenceRef(role="image", uri="file:///source/input.png", media_type="image/png"),
            EvidenceRef(role="note", uri="file:///source/note.txt", media_type="text/plain"),
        ],
        timeout_s=30,
    )

    cap_result = await cap(capability_context, request)

    assert (workspace / "inputs" / "input-1.png").read_bytes() == b"image-bytes"
    assert (workspace / "inputs" / "input-2.txt").read_bytes() == b"note-bytes"
    assert cap_result.output.new_artifact_count == 0
    assert trace_sink.events[0].decision == "inputs_staged"
    trace_json = trace_sink.events[0].model_dump_json()
    assert "image-bytes" not in trace_json
    assert "note-bytes" not in trace_json
    fingerprints = trace_sink.events[0].metadata["inputs"]
    assert fingerprints == [
        {
            "sha12": "2c8648d103e3",
            "length": 11,
            "role": "image",
            "media_type": "image/png",
            "path": "inputs/input-1.png",
        },
        {
            "sha12": "88965fd2aaf0",
            "length": 10,
            "role": "note",
            "media_type": "text/plain",
            "path": "inputs/input-2.txt",
        },
    ]
    # Provenance is part of the episode RECORD (freeze-to-replay covers inputs), not only the
    # optional trace sink: same fingerprints on the result and the capability metadata.
    assert cap_result.output.input_fingerprints == fingerprints
    assert cap_result.metadata["input_fingerprints"] == fingerprints
    assert "image-bytes" not in cap_result.output.model_dump_json()


async def test_input_assets_without_loader_fail_before_spawn(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    record_path = _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="envelope")
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), name="browser_agent")
    request = CliAgentRequest(
        prompt="Use inputs",
        workspace_dir=str(workspace),
        input_assets=[EvidenceRef(role="image", uri="file:///source/input.png", media_type="image/png")],
        timeout_s=30,
    )

    with pytest.raises(ValueError, match="asset_loader"):
        await cap(capability_context, request)

    assert not record_path.exists()


def test_capability_default_spec_declares_bash_side_effects_honestly():
    # Owner decision 2026-07-04: the default tool set includes Bash (shell writes +
    # network), so the DEFAULT spec must declare it — the safety ledger must not lie.
    from ai_workflow_tools.toolsets import BASH_SIDE_EFFECTS

    cap = CliAgentCapability(claude_p)
    assert list(BASH_SIDE_EFFECTS) == cap.spec.side_effects

    narrowed = CliAgentCapability(claude_p, side_effects=["workspace_write"])
    assert narrowed.spec.side_effects == ["workspace_write"]


async def test_capability_pre_spawn_denial_when_bash_granted_but_side_effects_undeclared(
    capability_context, tmp_path
):
    class _MustNotSpawn:
        calls = 0

        async def __call__(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("external process must not spawn after pre-spawn denial")

    runner = _MustNotSpawn()
    cap = CliAgentCapability(claude_p, side_effects=[], external_runner=runner)
    # allowed_tools=None -> DEFAULT_AGENT_TOOLS (includes Bash) against an empty ledger.
    result = await cap(
        capability_context,
        CliAgentRequest(prompt="investigate", workspace_dir=str(tmp_path / "ws")),
    )

    assert result.status == "failed"
    assert "pre-spawn denial" in result.error
    assert result.metadata["pre_spawn_denial"] is True
    assert _MustNotSpawn.calls == 0


async def test_codex_exec_pre_spawn_denial_when_workspace_side_effects_undeclared(
    capability_context, tmp_path
):
    class _MustNotSpawn:
        calls = 0

        async def __call__(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("codex process must not spawn after pre-spawn denial")

    runner = _MustNotSpawn()
    cap = CliAgentCapability(codex_exec, side_effects=[], external_runner=runner)

    result = await cap(
        capability_context,
        CliAgentRequest(prompt="inspect", workspace_dir=str(tmp_path / "ws")),
    )

    assert result.status == "failed"
    assert "codex_exec" in result.error
    assert "workspace_write" in result.error
    assert result.metadata["pre_spawn_denial"] is True
    assert _MustNotSpawn.calls == 0


async def test_capability_narrowed_spec_allows_bashless_requests(
    capability_context, fake_cli_path, monkeypatch, tmp_path
):
    workspace = tmp_path / "workspace"
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="envelope")
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), side_effects=[])

    result = await cap(
        capability_context,
        CliAgentRequest(
            prompt="read only",
            workspace_dir=str(workspace),
            allowed_tools=["Read", "Grep"],
            timeout_s=30,
        ),
    )

    assert result.status == "accepted"


# --------------------------------------------------------------------------------------
# v0.10 Phase 3: CLI agents inherit the engine execution window (soft deadline drives the
# subprocess; an explicit request may narrow but NEVER enlarge it; a missing window with no
# explicit timeout fails loudly instead of resurrecting a hidden 600s default).
# --------------------------------------------------------------------------------------


class _SpyRunner:
    """Captures the ExternalProcessRequest without spawning; returns a canned success."""

    def __init__(self) -> None:
        self.request = None
        self.calls = 0

    async def __call__(self, _context, request):
        self.calls += 1
        self.request = request
        return CapabilityResult(
            status="accepted",
            output={"returncode": 0, "stdout": '{"ok": true}', "stderr": "", "result": '{"ok": true}'},
        )


def _windowed(context, *, hard, reserve=0.0, enforcement="process"):
    window = resolve_execution_window(
        ExecutionWindowInputs(
            request=TaskExecutionRequest(timeout_s=hard, completion_reserve_s=reserve)
        ),
        enforcement=enforcement,
    )
    return context.model_copy(update={"execution_window": window})


def _bashless(flavor, **kwargs):
    return CliAgentCapability(flavor, side_effects=[], **kwargs)


def test_cli_agent_declares_process_enforcement_truthfully():
    # It owns a killable subprocess, so it declares process — kind alone no longer implies it.
    assert CliAgentCapability(claude_p).spec.timeout_enforcement == "process"


async def test_engine_soft_window_drives_subprocess_bound_and_finalization_notice(
    capability_context, tmp_path
):
    spy = _SpyRunner()
    cap = _bashless(claude_p, external_runner=spy)
    context = _windowed(capability_context, hard=10.0, reserve=2.0)  # soft work = 8s

    await cap(
        context,
        CliAgentRequest(prompt="Investigate", workspace_dir=str(tmp_path), allowed_tools=["Read"]),
    )

    assert spy.request.timeout_s == 8.0  # inherited the SOFT window, no explicit request timeout
    assert spy.request.kill_grace_s <= 2.0  # terminate/kill/reap fits inside the reserve
    # finalization notice rides in the prompt (stdin for claude_p) — no host paths / secrets
    assert "Execution window" in spy.request.stdin_data
    assert str(tmp_path) not in spy.request.stdin_data
    assert spy.request.metadata["execution_bound_source"] == "engine_window"


async def test_explicit_request_narrows_the_engine_window(capability_context, tmp_path):
    spy = _SpyRunner()
    cap = _bashless(claude_p, external_runner=spy)
    context = _windowed(capability_context, hard=100.0)  # soft = 100s

    await cap(
        context,
        CliAgentRequest(
            prompt="q", workspace_dir=str(tmp_path), allowed_tools=["Read"], timeout_s=5.0
        ),
    )

    assert spy.request.timeout_s == 5.0  # explicit 5s narrowed the 100s window
    assert spy.request.metadata["execution_bound_source"] == "explicit_request"


async def test_explicit_request_can_never_enlarge_the_engine_window(capability_context, tmp_path):
    """The authoritative guard: a larger explicit request must NOT extend a tighter engine
    window — the engine's bound always wins the ceiling. With no completion reserve
    (soft == hard), represented cleanup headroom is carved from WORK time so terminate/kill/
    reap finishes BEFORE the hard deadline instead of racing it (codex 5R finding 2)."""

    spy = _SpyRunner()
    cap = _bashless(claude_p, external_runner=spy)
    context = _windowed(capability_context, hard=5.0)  # soft = 5s, reserve = 0

    await cap(
        context,
        CliAgentRequest(
            prompt="q", workspace_dir=str(tmp_path), allowed_tools=["Read"], timeout_s=100.0
        ),
    )

    # engine wins the ceiling; 1s cleanup headroom carved from the reserveless window:
    # work 4s + kill grace <= 1s all complete before the 5s hard boundary.
    assert spy.request.timeout_s == 4.0
    assert spy.request.timeout_s + spy.request.kill_grace_s <= 5.0
    assert spy.request.metadata["execution_bound_source"] == "engine_window"


async def test_missing_window_and_no_explicit_timeout_fails_loudly_without_spawning(
    capability_context, tmp_path
):
    spy = _SpyRunner()
    cap = _bashless(claude_p, external_runner=spy)

    result = await cap(
        capability_context,  # no execution_window
        CliAgentRequest(prompt="q", workspace_dir=str(tmp_path), allowed_tools=["Read"]),
    )

    assert result.status == "failed"
    assert "no execution bound" in result.error
    assert result.metadata["no_execution_bound"] is True
    assert spy.calls == 0  # never spawned — a missing window is not a hidden default


@pytest.mark.parametrize("flavor", [claude_p, codex_exec], ids=["claude_p", "codex_exec"])
async def test_both_flavors_share_window_inheritance_semantics(flavor, capability_context, tmp_path):
    spy = _SpyRunner()
    # codex_exec requires workspace_write; declare it so we exercise inheritance, not denial.
    from ai_workflow_tools.toolsets import BASH_SIDE_EFFECTS

    cap = CliAgentCapability(flavor, side_effects=list(BASH_SIDE_EFFECTS), external_runner=spy)
    context = _windowed(capability_context, hard=12.0)  # soft = 12s, reserve = 0

    await cap(context, CliAgentRequest(prompt="q", workspace_dir=str(tmp_path)))

    # both flavors share the same algebra: 1s cleanup headroom carved from the reserveless
    # window → 11s work, reap complete before the 12s hard boundary.
    assert spy.request.timeout_s == 11.0
    assert spy.request.timeout_s + spy.request.kill_grace_s <= 12.0
    assert spy.request.metadata["execution_bound_source"] == "engine_window"


async def test_engine_invoke_resolves_the_window_the_cli_agent_then_inherits(
    capability_context, tmp_path
):
    """End-to-end through the real CapabilityRuntime: a per-task execution request becomes an
    engine window that the CLI capability inherits as its subprocess bound — no product
    timeout arithmetic anywhere in between."""

    spy = _SpyRunner()
    cap = _bashless(claude_p, name="browser_agent", external_runner=spy)
    registry = CapabilityRegistry()
    registry.register(cap.spec, cap)
    runtime = CapabilityRuntime(registry)
    context = capability_context.model_copy(
        update={"execution_request": TaskExecutionRequest(timeout_s=8.0, completion_reserve_s=1.0)}
    )

    result = await runtime.invoke(
        "browser_agent",
        CliAgentRequest(prompt="q", workspace_dir=str(tmp_path), allowed_tools=["Read"]),
        context,
    )

    assert result.status == "accepted"
    assert spy.request.timeout_s == pytest.approx(7.0, abs=0.01)


async def test_engine_window_actually_bounds_a_real_slow_subprocess_and_salvages(
    capability_context, fake_cli_path, monkeypatch, tmp_path
):
    """Real subprocess, engine-window-driven (NO explicit request timeout): the soft window
    stops a 5s worker, the PNG it wrote survives as a partial, and the child is reaped."""

    workspace = tmp_path / "workspace"
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="sleep", sleep_s="5")
    cap = _bashless(_fake_flavor(claude_p, fake_cli_path))
    # 1.2s reserveless engine window, no explicit request timeout: 0.3s cleanup headroom is
    # carved (represented) → ~0.9s work for the fake to start + write its PNG under load.
    context = _windowed(capability_context, hard=1.2)

    cap_result = await cap(
        context,
        CliAgentRequest(prompt="Capture", workspace_dir=str(workspace), allowed_tools=["Read"]),
    )

    result = cap_result.output
    assert cap_result.status == "partial"
    assert result.status == "truncated"
    assert result.new_artifact_count == 1
    assert [artifact.role for artifact in result.artifacts] == ["screenshot"]
    assert cap_result.metadata["execution_bound_source"] == "engine_window"


# --------------------------------------------------------------------------------------
# v0.10.1 door sweep: the CLI agent inherits the shared bounded-process-I/O semantics and
# surfaces the same capture truth — the invariant must not drift between process doors.
# --------------------------------------------------------------------------------------


async def test_cli_agent_bounds_a_stdout_flood_and_surfaces_process_io_truth(
    capability_context, fake_cli_path, monkeypatch, tmp_path
):
    workspace = tmp_path / "workspace"
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="flood")
    monkeypatch.setenv("FAKE_CLI_FLOOD_BYTES", str(6 * 1024 * 1024))  # 6 MiB, well over the cap
    cap = _bashless(_fake_flavor(claude_p, fake_cli_path))
    context = _windowed(capability_context, hard=30.0)

    cap_result = await cap(
        context,
        CliAgentRequest(prompt="flood", workspace_dir=str(workspace), allowed_tools=["Read"]),
    )

    # the child ran to completion; the door retained only a bounded slice, not 6 MiB
    io_meta = cap_result.metadata.get("process_io")
    assert io_meta is not None, "the CLI door must surface process_io capture truth"
    assert io_meta["stdout"]["total_bytes"] >= 6 * 1024 * 1024
    assert io_meta["stdout"]["truncated"] is True
    assert io_meta["stdout"]["retained_bytes"] <= 2 * 1024 * 1024
    assert len(cap_result.output.text) <= 2 * 1024 * 1024, "retained text must be bounded"
    assert cap_result.status == "partial"
    assert cap_result.output.status == "truncated"


# --- v0.11.15 stdin prompt transport (RED before repair) -------------------------------------

_SENTINEL = "PROMPT-SENTINEL-b3f1a9c7"


def _large_prompt(marker: str = _SENTINEL) -> str:
    """A deterministic prompt larger than Linux's per-argument limit (MAX_ARG_STRLEN, 128 KiB).

    MageQA's real failure was a 125,799-character prompt; >200 KiB keeps the reproducer
    unambiguously over the boundary on every supported host without depending on that private
    payload, which is not retained anywhere on disk.
    """

    body = "curated finding line with enough text to be representative\n" * 4000
    assert len(body) > 200 * 1024, "reproducer must exceed the per-argument limit"
    return f"{marker}\n{body}"


async def test_codex_agent_sends_large_prompt_through_stdin_not_argv(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    """MageQA: `[Errno 7] Argument list too long: 'codex'` before Codex started, on a legitimate
    125,799-character curation prompt. Process creation — not the provider — rejected it, because
    `codex_exec` put the whole prompt in argv. The prompt must travel as stdin bytes instead."""

    workspace = tmp_path / "workspace"
    record_path = _configure_fake_cli(
        monkeypatch, tmp_path, workspace, mode="result_file", result="curated"
    )
    prompt = _large_prompt()
    cap = CliAgentCapability(_fake_flavor(codex_exec, fake_cli_path), name="codex_agent")
    request = CliAgentRequest(prompt=prompt, workspace_dir=str(workspace), timeout_s=60)
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(capability_context.run_context, summary, WorkflowBudget())
    ):
        cap_result = await cap(capability_context, request)

    assert cap_result.status == "accepted", f"large prompt must reach the CLI: {cap_result.error}"
    assert cap_result.output.status == "completed"

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["stdin"] == prompt, "the CLI must receive the exact prompt bytes on stdin"
    assert record["argv"].count(prompt) == 0, "the prompt must not appear in argv"
    assert not any(_SENTINEL in item for item in record["argv"]), (
        "no prompt fragment may appear in argv"
    )
    assert record["argv"][-1] != prompt


async def test_codex_agent_prompt_is_absent_from_kernel_process_command_line(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    """Even a small prompt in argv is readable by any process on the host through
    /proc/<pid>/cmdline. Asserting on the Python argv list would not prove that; this reads the
    command line as the kernel reports it for the live CLI process."""

    workspace = tmp_path / "workspace"
    record_path = _configure_fake_cli(
        monkeypatch, tmp_path, workspace, mode="result_file", result="ok"
    )
    prompt = f"{_SENTINEL} small confidential instruction"
    cap = CliAgentCapability(_fake_flavor(codex_exec, fake_cli_path), name="codex_agent")
    request = CliAgentRequest(prompt=prompt, workspace_dir=str(workspace), timeout_s=30)
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(capability_context.run_context, summary, WorkflowBudget())
    ):
        cap_result = await cap(capability_context, request)

    assert cap_result.status == "accepted"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    os_cmdline = record["os_cmdline"]
    assert os_cmdline is not None, "kernel command line unavailable; the tier must run on Linux"
    assert _SENTINEL not in os_cmdline, (
        "prompt text is exposed through process command-line inspection"
    )
    assert record["stdin"] == prompt


async def test_codex_large_stdin_prompt_to_a_child_that_never_reads_times_out_and_is_reaped(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    """Delivering the prompt as stdin creates a backpressure surface argv never had: a child that
    ignores stdin lets the pipe buffer fill (~64 KiB), so a naive synchronous write would deadlock
    past the execution window. The engine's bounded process owner must instead honour the window,
    terminate the process tree, and reap it — with the original prompt bytes never reaching argv."""

    workspace = tmp_path / "workspace"
    ready = tmp_path / "backpressure-ready.pid"
    record_path = _configure_fake_cli(
        monkeypatch, tmp_path, workspace, mode="ignore_stdin_then_sleep", sleep_s="60"
    )
    monkeypatch.setenv("FAKE_CLI_READY_FILE", str(ready))
    # Well past a single pipe buffer, so the write cannot complete before the child is killed.
    prompt = _SENTINEL + ("x" * (2 * 1024 * 1024))
    cap = CliAgentCapability(_fake_flavor(codex_exec, fake_cli_path), name="codex_agent")
    request = CliAgentRequest(prompt=prompt, workspace_dir=str(workspace), timeout_s=3)
    summary = WorkflowUsageSummary()

    started = asyncio.get_running_loop().time()
    with workflow_usage_scope(
        WorkflowUsageContext(capability_context.run_context, summary, WorkflowBudget())
    ):
        cap_result = await cap(capability_context, request)
    elapsed = asyncio.get_running_loop().time() - started

    # Bounded by the declared window, not by the child's 60s sleep.
    assert elapsed < 30, f"blocked on stdin backpressure for {elapsed:.1f}s"
    # Established convention for a window-terminated process (same as the bounded-output tests):
    # capability `partial` + agent `truncated`, with the timeout named and SIGTERM recorded.
    assert cap_result.status == "partial"
    assert cap_result.output.status == "truncated"
    assert "timed out after 3.0s" in (cap_result.error or "")
    assert cap_result.output.returncode == -15, "process tree must be signalled, not left running"
    assert ready.exists(), "the fake CLI never started; the test would prove nothing"

    pid = int(ready.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # terminated and reaped, no orphan holding the pipe

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert not any(_SENTINEL in item for item in record["argv"])
    assert record["os_cmdline"] is None or _SENTINEL not in record["os_cmdline"]


async def test_codex_caller_cancellation_mid_stdin_drain_reaps_and_reraises(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    """Cancellation while the prompt is still being written.

    Argv delivery had no such window: the payload was handed to execve atomically. With a 2 MiB
    stdin payload and a child that never drains it, the write is still in flight when the caller
    cancels, so the engine must terminate and reap the process tree AND re-raise the original
    `CancelledError` rather than converting an interrupted write into a provider error.
    """

    workspace = tmp_path / "workspace-cancel-drain"
    ready = tmp_path / "drain-ready.pid"
    _configure_fake_cli(
        monkeypatch, tmp_path, workspace, mode="ignore_stdin_then_sleep", sleep_s="60"
    )
    monkeypatch.setenv("FAKE_CLI_READY_FILE", str(ready))
    prompt = _SENTINEL + ("y" * (2 * 1024 * 1024))
    cap = CliAgentCapability(_fake_flavor(codex_exec, fake_cli_path), name="codex_agent")
    request = CliAgentRequest(prompt=prompt, workspace_dir=str(workspace), timeout_s=60)
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(capability_context.run_context, summary, WorkflowBudget())
    ):
        task = asyncio.create_task(cap(capability_context, request))
        for _ in range(500):
            if ready.exists():
                break
            await asyncio.sleep(0.01)
        assert ready.exists(), "fake Codex process never started; the test would prove nothing"
        pid = int(ready.read_text(encoding="utf-8"))
        task.cancel()
        # The ORIGINAL cancellation identity must survive the stdin-write cleanup path.
        with pytest.raises(asyncio.CancelledError):
            await task

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # no orphan left holding the unread pipe


async def test_codex_stdin_prompt_never_leaks_into_result_usage_or_observation_metadata(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    """Equivalence + privacy: moving the prompt to stdin must preserve every contract consumers
    read, and must not relocate the prompt into some other observable surface. The prompt is the
    one thing that moved, so it must appear in NONE of the result, usage event, capability
    metadata, or error text — while result parsing, usage normalization, and artifact accounting
    stay exactly as before."""

    workspace = tmp_path / "workspace-leak"
    _configure_fake_cli(
        monkeypatch, tmp_path, workspace, mode="result_file", result='Done: {"ok": true}'
    )
    prompt = f"{_SENTINEL} confidential audited-site content"
    cap = CliAgentCapability(_fake_flavor(codex_exec, fake_cli_path), name="codex_agent")
    request = CliAgentRequest(
        prompt=prompt, workspace_dir=str(workspace), model="gpt-5.4-codex", timeout_s=30
    )
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(capability_context.run_context, summary, WorkflowBudget())
    ):
        cap_result = await cap(capability_context, request)

    # Contracts preserved, field by field, exactly as the pre-stdin happy path asserted.
    assert cap_result.status == "accepted"
    result = cap_result.output
    assert result.status == "completed"
    assert result.text == 'Done: {"ok": true}'
    assert result.parsed == {"ok": True}
    assert result.input_tokens == 120
    assert result.output_tokens == 30
    assert result.cache_read_tokens == 20
    assert result.notional_cost_usd == pytest.approx(0.000705)
    usage = summary.events[0]
    assert len(summary.events) == 1, "one provider attempt must still record exactly one event"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 30
    assert usage.invocation_id == result.invocation_id
    assert usage.input_token_details["cache_read"] == 20
    assert usage.output_token_details["reasoning"] == 10

    # The prompt moved to stdin; it must not have reappeared anywhere observable.
    surfaces = {
        "capability metadata": json.dumps(cap_result.metadata, default=str),
        "capability error": str(cap_result.error),
        "agent result": result.model_dump_json(),
        "usage event": usage.model_dump_json(),
    }
    for name, payload in surfaces.items():
        assert _SENTINEL not in payload, f"prompt text leaked into {name}"

    # Process accounting still describes the real invocation.
    assert cap_result.metadata["flavor"] == "codex_exec"
    assert cap_result.metadata["process_io"]["result_file"]["status"] != "missing"
