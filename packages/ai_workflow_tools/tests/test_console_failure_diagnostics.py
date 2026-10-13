"""Real subprocess failures retain bounded diagnostics, never raw private output."""
import asyncio
import json
import os
import sys
import time

import pytest
from pydantic import BaseModel

from ai_workflow_engine import ObservationConfig, ObservationReader, StructuredLLMNode, WorkflowBuilder, WorkflowEngineBuilder
from ai_workflow_engine.engine.external import ExternalProcessCapability
from ai_workflow_engine.engine.process_io import ProcessIOLimits
from ai_workflow_tools.cli_agents import ConsoleCliError, ConsoleLLMClient, claude_p, codex_exec

pytestmark = pytest.mark.unit


class Answer(BaseModel):
    ok: bool


def _claude_result_client(
    tmp_path, *, message, is_error, returncode=1, subtype="success", hold=False,
):
    """Constructed offline envelope; original native process stdout was not retained."""
    envelope = {
        "type": "result", "subtype": subtype, "is_error": is_error, "result": message,
        "total_cost_usd": 0.031,
        "usage": {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 3},
        "credentials": {"token": "PRIVATE_SECRET"},
    }
    executable = tmp_path / "fake_claude.py"
    executable.write_text(
        "import sys,time,os\n"
        "from pathlib import Path\n"
        "sys.stdin.read()\n"
        f"sys.stdout.write({json.dumps(envelope)!r})\n"
        "sys.stdout.flush()\n"
        + (f"Path({str(tmp_path / 'ready')!r}).write_text(str(os.getpid()))\n"
           "time.sleep(20)\n" if hold else "")
        + f"raise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    return ConsoleLLMClient(
        claude_p.model_copy(update={"base_argv": [sys.executable, str(executable)]}),
        model="claude-offline-contract", timeout_s=5,
    )


@pytest.mark.parametrize(("returncode", "is_error"), [(0, True), (1, True), (1, False)])
async def test_claude_session_limit_has_safe_tools_owned_capacity_diagnostic(
    tmp_path, returncode, is_error,
):
    from ai_workflow_engine.llm_protocol import LLMRequest

    client = _claude_result_client(
        tmp_path, is_error=is_error, returncode=returncode,
        message="You've hit your session limit · resets 6:50pm (UTC) PRIVATE_SECRET "
                "https://private.invalid/?token=PRIVATE_SECRET",
    )
    with pytest.raises(ConsoleCliError) as excinfo:
        await client(LLMRequest(user="Review retained evidence."))

    error = excinfo.value
    assert error.returncode == returncode
    assert error.failure_kind == "cap"
    assert error.cli_subtype == ("success" if is_error else "")
    assert error.provider_error_diagnostic is not None
    assert error.provider_error_diagnostic.metadata() == {
        "provider": "claude", "subtype": "result",
        "category": "quota_capacity_rate_limit", "message": "Claude session limit reached",
        "reset_at": None,
    }
    assert "Claude session limit reached" in str(error)
    assert "PRIVATE_SECRET" not in str(error)
    assert "private.invalid" not in str(error)
    assert "6:50" not in str(error)
    assert error.notional_usd == pytest.approx(0.031)
    assert error.normalized_usage.uncached_input_tokens == 11
    assert error.normalized_usage.cache_read_input_tokens == 3
    assert error.normalized_usage.non_reasoning_output_tokens == 7
    assert error.usage_error is None


@pytest.mark.parametrize("message", ["Authentication failed", "Unexpected provider failure"])
async def test_claude_nonquota_failure_keeps_provider_classification(tmp_path, message):
    from ai_workflow_engine.llm_protocol import LLMRequest

    client = _claude_result_client(tmp_path, message=message, is_error=True)
    with pytest.raises(ConsoleCliError) as excinfo:
        await client(LLMRequest(user="Review retained evidence."))
    assert excinfo.value.failure_kind == "provider"
    assert excinfo.value.provider_error_diagnostic is None


async def test_claude_successful_answer_mentioning_session_limit_stays_successful(tmp_path):
    from ai_workflow_engine.llm_protocol import LLMRequest

    message = "You've hit your session limit is the error being discussed, not this result."
    reply = await _claude_result_client(
        tmp_path, message=message, is_error=False, returncode=0,
    )(LLMRequest(user="Explain the example error."))
    assert reply.text == message


async def test_claude_quota_before_process_timeout_does_not_replace_timeout(tmp_path):
    from ai_workflow_engine.llm_protocol import LLMRequest

    client = _claude_result_client(
        tmp_path, message="You've hit your session limit", is_error=True, hold=True,
    )
    client.timeout_s = 0.25
    with pytest.raises(ConsoleCliError) as excinfo:
        await client(LLMRequest(user="Review retained evidence."))
    assert excinfo.value.failure_kind == "timeout"
    assert excinfo.value.provider_error_diagnostic.metadata()["reset_at"] is None
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "ready").read_text()), 0)


async def test_claude_quota_before_cancellation_preserves_cancelled_error_and_reaps_process(tmp_path):
    from ai_workflow_engine.llm_protocol import LLMRequest

    client = _claude_result_client(
        tmp_path, message="You've hit your session limit", is_error=True, hold=True,
    )
    task = asyncio.create_task(client(LLMRequest(user="Review retained evidence.")))
    try:
        for _ in range(200):
            if (tmp_path / "ready").exists():
                break
            await asyncio.sleep(0.01)
        assert (tmp_path / "ready").exists(), "fake Claude never reached cancellation barrier"
        pid = int((tmp_path / "ready").read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_sync_claude_console_uses_same_quota_diagnostic(tmp_path):
    from langchain_core.messages import HumanMessage
    from ai_workflow_tools.cli_agents import ConsoleChatModel

    client = _claude_result_client(
        tmp_path, message="You've hit your session limit", is_error=True,
    )
    model = ConsoleChatModel(client.flavor, model="claude-offline-contract", timeout_s=5)
    with pytest.raises(ConsoleCliError) as excinfo:
        model.invoke([HumanMessage(content="Review retained evidence.")])
    assert excinfo.value.failure_kind == "cap"
    assert excinfo.value.provider_error_diagnostic.metadata()["provider"] == "claude"


async def test_claude_agent_projects_same_safe_diagnostic_without_losing_failure(tmp_path, capability_context):
    from ai_workflow_tools.cli_agents import CliAgentCapability, CliAgentRequest

    client = _claude_result_client(
        tmp_path, message="You've hit your session limit PRIVATE_SECRET", is_error=True,
    )
    result = await CliAgentCapability(client.flavor, name="claude_quota")(
        capability_context,
        CliAgentRequest(prompt="Review retained evidence.", workspace_dir=str(tmp_path),
                        allowed_tools=[], timeout_s=5),
    )
    assert result.status == "failed"
    assert result.output.status == "error"
    assert result.output.returncode == 1
    assert result.output.normalized_usage.uncached_input_tokens == 11
    assert result.metadata["provider_error_diagnostic"] == {
        "provider": "claude", "subtype": "result", "category": "quota_capacity_rate_limit",
        "message": "Claude session limit reached", "reset_at": None,
    }
    assert "PRIVATE_SECRET" not in result.error


@pytest.mark.parametrize("progress", [False, True])
async def test_timeout_is_one_process_and_diagnostics_reach_actual_reader(tmp_path, progress):
    marker = tmp_path / "invocations"
    executable = tmp_path / "fake.py"
    executable.write_text(
        "import sys,time,os,json\n"
        "from pathlib import Path\n"
        "data=sys.stdin.read()\n"
        f"with Path({str(marker)!r}).open('a') as f: f.write(str(os.getpid())+'\\n')\n"
        + ("print(json.dumps({'type':'thread.started','thread_id':'PRIVATE_SECRET'}),flush=True)\n"
           "print(json.dumps({'type':'turn.started','private':'PRIVATE_SECRET'}),flush=True)\n"
           "print('PRIVATE_SECRET'*1000,flush=True)\n"
           "print('authorization=PRIVATE_SECRET',file=sys.stderr,flush=True)\n" if progress else "")
        + "time.sleep(20)\n"
    )
    flavor = codex_exec.model_copy(update={"base_argv": [sys.executable, str(executable)]})
    client = ConsoleLLMClient(flavor, model="gpt-5.6-sol", timeout_s=0.25,
        external_runner=ExternalProcessCapability(io_limits=ProcessIOLimits(max_stdout_bytes=128)))
    node = StructuredLLMNode(name="review", config=object(), output_model=Answer,
                             prompt_template="Review", llm=client, max_repair_rounds=1)
    failures = []

    async def review(ctx, payload):
        try:
            return await node.run({})
        except ConsoleCliError as exc:
            failures.append(exc)
            raise

    engine = (WorkflowEngineBuilder().with_observation(ObservationConfig(
        enabled=True, bundle_dir=str(tmp_path / "bundles"), capture="full"))
        .register_capability("review", review, kind="llm")
        .register_workflow(WorkflowBuilder("timeout").step("review").build()).build())
    start = time.monotonic()
    result = await engine.run("timeout", {})
    assert time.monotonic() - start < 2
    assert result.status == "failed"
    pids = marker.read_text().splitlines()
    assert len(pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(int(pids[0]), 0)
    [failure] = failures
    assert failure.failure_kind == "timeout"
    assert failure.process_io["stdout"]["total_bytes"] > 0 if progress else failure.process_io["stdout"]["total_bytes"] == 0
    assert failure.cli_progress["last_event"] == ("turn.started" if progress else None)
    assert failure.cli_progress["event_count"] == (2 if progress else 0)
    assert failure.process_io["stdout"]["truncated"] is progress
    assert failure.normalized_usage is None
    assert failure.usage_error == "usage_event_missing"
    reader = ObservationReader(result.observation_bundle_path)
    bodies = [json.loads(item.body_bytes) for item in reader.iter_detail_bodies(
        kinds={"llm_response"}, max_body_bytes=10000)]
    errors = [body for body in bodies if isinstance(body, dict) and body.get("error_type") == "ConsoleCliError"]
    assert len(errors) == 1
    assert errors[0]["process_io"] == failure.process_io
    assert errors[0]["cli_progress"] == failure.cli_progress
    assert "PRIVATE_SECRET" not in json.dumps(errors)
    assert len(json.dumps(errors)) < 4000
    assert len(list(reader.iter_usage_events())) == 1
