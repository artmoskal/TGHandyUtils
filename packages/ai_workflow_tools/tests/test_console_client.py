from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from ai_workflow_engine import ImageInput, LLMRequest, StructuredLLMNode, ToolSpec, WEAK_MODEL_CLEANER
from ai_workflow_engine.models import WorkflowRunContext, WorkflowUsageSummary
from ai_workflow_engine.usage import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope
from pydantic import BaseModel

from ai_workflow_tools.cli_agents import ConsoleLLMClient, claude_p, codex_exec

pytestmark = pytest.mark.unit


class Verdict(BaseModel):
    label: str


def _node(client: ConsoleLLMClient, **kwargs) -> StructuredLLMNode:
    return StructuredLLMNode(
        name="console_node",
        config=object(),
        output_model=Verdict,
        prompt_template="Classify {item}.",
        input_variables=["item"],
        llm=client,
        **kwargs,
    )


def _fake_flavor(flavor, fake_cli_path: Path):
    return flavor.model_copy(update={"base_argv": [sys.executable, str(fake_cli_path)]})


def _configure_fake_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mode: str,
    result: str = '{"label": "ok"}',
    append: bool = False,
    sequence: list[str] | None = None,
) -> Path:
    record_path = tmp_path / f"console-record-{mode}.json"
    monkeypatch.setenv("FAKE_CLI_MODE", mode)
    monkeypatch.setenv("FAKE_CLI_RESULT", result)
    monkeypatch.setenv("FAKE_CLI_RECORD", str(record_path))
    if append:
        monkeypatch.setenv("FAKE_CLI_RECORD_APPEND", "1")
    else:
        monkeypatch.delenv("FAKE_CLI_RECORD_APPEND", raising=False)
    if sequence is not None:
        counter_path = tmp_path / f"counter-{mode}.txt"
        monkeypatch.setenv("FAKE_CLI_RESULTS_JSON", json.dumps(sequence))
        monkeypatch.setenv("FAKE_CLI_COUNTER_FILE", str(counter_path))
    else:
        monkeypatch.delenv("FAKE_CLI_RESULTS_JSON", raising=False)
        monkeypatch.delenv("FAKE_CLI_COUNTER_FILE", raising=False)
    return record_path


async def test_console_claude_structured_node_uses_pre_parse_and_subscription_usage(
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    record_path = _configure_fake_cli(
        monkeypatch,
        tmp_path,
        mode="envelope",
        result='<think>noise</think>\n```json\n{"label": "clean"}\n```',
    )
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))
    node = _node(client, pre_parse=WEAK_MODEL_CLEANER)
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(WorkflowRunContext(workflow_id="wf-console", workflow_type="console"), summary, WorkflowBudget())
    ):
        result = await node.run({"item": "mug"})

    assert result.label == "clean"
    usage = summary.events[0]
    assert usage.cost_class == "subscription_notional"
    assert usage.notional_usd == 0.123
    assert usage.estimated_usd is None
    assert usage.input_tokens == 11
    assert usage.output_tokens == 7

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["stdin"] == "user: Classify mug."
    assert record["argv"] == [str(fake_cli_path), "--output-format", "json"]
    assert "--mcp-config" not in record["argv"]


async def test_console_codex_reads_result_file_and_keeps_cost_unknown(
    fake_cli_path,
    monkeypatch,
    tmp_path,
):
    record_path = _configure_fake_cli(
        monkeypatch,
        tmp_path,
        mode="result_file",
        result='{"label": "codex"}',
    )
    client = ConsoleLLMClient(_fake_flavor(codex_exec, fake_cli_path))
    node = _node(client)
    summary = WorkflowUsageSummary()

    with workflow_usage_scope(
        WorkflowUsageContext(WorkflowRunContext(workflow_id="wf-console", workflow_type="console"), summary, WorkflowBudget())
    ):
        result = await node.run({"item": "mug"})

    assert result.label == "codex"
    usage = summary.events[0]
    assert usage.cost_class == "subscription_notional"
    assert usage.notional_usd is None
    assert usage.estimated_usd is None
    assert usage.metadata["cost_known"] is False
    assert usage.metadata["cost_source"] == "unknown"

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["stdin"] == ""
    assert "--output-last-message" in record["argv"]
    assert "--config" not in record["argv"]
    assert record["argv"][-1] == "user: Classify mug."


async def test_console_refuses_images_and_tools_before_spawn(fake_cli_path, monkeypatch, tmp_path):
    record_path = _configure_fake_cli(monkeypatch, tmp_path, mode="envelope")
    client = ConsoleLLMClient(_fake_flavor(claude_p, fake_cli_path))

    with pytest.raises(ValueError, match="cannot receive images"):
        await client(
            LLMRequest(
                user="inspect",
                images=[ImageInput(source="base64", data="aGVsbG8=", media_type="image/png")],
            )
        )
    with pytest.raises(ValueError, match="cannot host tool-calling"):
        await client(LLMRequest(user="inspect", tools=[ToolSpec(name="click")]))

    assert not record_path.exists()


@pytest.mark.parametrize(
    ("flavor_factory", "mode", "prompt_location"),
    [
        (lambda path: _fake_flavor(claude_p, path), "envelope", "stdin"),
        (lambda path: _fake_flavor(codex_exec, path), "result_file", "argv"),
    ],
)
async def test_console_repair_round_spawns_twice_and_delivers_repair_prompt_per_flavor(
    fake_cli_path,
    flavor_factory,
    mode,
    prompt_location,
    monkeypatch,
    tmp_path,
):
    record_path = _configure_fake_cli(
        monkeypatch,
        tmp_path,
        mode=mode,
        append=True,
        sequence=["utter garbage", '{"label": "repaired"}'],
    )
    client = ConsoleLLMClient(flavor_factory(fake_cli_path))
    node = _node(client)

    result = await node.run({"item": "mug"})

    assert result.label == "repaired"
    records = json.loads(record_path.read_text(encoding="utf-8"))
    assert len(records) == 2
    first_prompt = records[0]["stdin"] if prompt_location == "stdin" else records[0]["argv"][-1]
    second_prompt = records[1]["stdin"] if prompt_location == "stdin" else records[1]["argv"][-1]
    assert first_prompt == "user: Classify mug."
    assert "previous structured-output response was invalid" in second_prompt
