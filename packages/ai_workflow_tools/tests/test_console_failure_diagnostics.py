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
from ai_workflow_tools.cli_agents import ConsoleCliError, ConsoleLLMClient, codex_exec

pytestmark = pytest.mark.unit


class Answer(BaseModel):
    ok: bool


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
