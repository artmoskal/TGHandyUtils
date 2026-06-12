from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from ai_workflow_engine.engine.capabilities import CapabilityRegistry, CapabilityRuntime, InMemoryTraceSink
from ai_workflow_engine.models import EvidenceRef, WorkflowUsageSummary
from ai_workflow_engine.usage import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope

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


async def test_codex_flavor_reads_result_file_and_records_honest_unknown_cost(
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
    request = CliAgentRequest(prompt="Inspect", workspace_dir=str(workspace), model="gpt-5.4-codex")
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(WorkflowUsageContext(capability_context.run_context, summary, WorkflowBudget())):
        cap_result = await cap(capability_context, request)

    result = cap_result.output
    assert cap_result.status == "accepted"
    assert result.status == "completed"
    assert result.text == 'Done: {"ok": true}'
    assert result.parsed == {"ok": True}
    assert result.notional_cost_usd is None
    assert summary.notional_usd is None
    usage = summary.events[0]
    assert usage.metadata["cost_known"] is False
    assert usage.metadata["cost_source"] == "unknown"
    assert usage.estimated_usd is None
    assert usage.notional_usd is None

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["stdin"] == ""
    assert record["argv"][-1] == "Inspect"
    assert "--output-last-message" in record["argv"]


async def test_timeout_returns_partial_and_salvages_png(
    capability_context,
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    _configure_fake_cli(monkeypatch, tmp_path, workspace, mode="sleep", sleep_s="5")
    cap = CliAgentCapability(_fake_flavor(claude_p, fake_cli_path), name="browser_agent")
    request = CliAgentRequest(prompt="Capture", workspace_dir=str(workspace), timeout_s=0.1)

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
    request = CliAgentRequest(prompt="Inspect", workspace_dir=str(workspace))
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

    cap_result = await cap(capability_context, CliAgentRequest(prompt="Collect", workspace_dir=str(workspace)))

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
    )

    with pytest.raises(ValueError, match="asset_loader"):
        await cap(capability_context, request)

    assert not record_path.exists()
