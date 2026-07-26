#!/usr/bin/env python3
"""Qualify a protected large prompt through installed wheels and the real Codex CLI.

The default test tier calls ``run_qualification`` with the repository's fake CLI. Release
operators invoke this file with ``python -I`` from an external environment containing the exact
release wheels. The evidence record contains prompt identity, never prompt content.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import ai_workflow_engine
import ai_workflow_tools
from ai_workflow_engine import (
    ObservationConfig,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowGoal,
    WorkflowProfile,
)
from ai_workflow_engine.models import RuntimeLimits, SafetyPolicy
from ai_workflow_tools.cli_agents import CliAgentCapability, CliAgentRequest, CliFlavor, codex_exec
from ai_workflow_tools.cli_agents.assembly import build_cli_agent_invocation
from ai_workflow_tools.toolsets import BASH_SIDE_EFFECTS


@dataclass(frozen=True)
class QualificationConfig:
    prompt_file: Path
    expected_length: int
    expected_sha256: str
    workspace_dir: Path
    bundle_dir: Path
    model: str
    timeout_s: float
    expected_output: str
    require_installed: bool = True


def _read_verified_prompt(config: QualificationConfig) -> str:
    prompt = config.prompt_file.read_text(encoding="utf-8")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if len(prompt) != config.expected_length:
        raise ValueError(
            f"prompt length mismatch: expected {config.expected_length}, got {len(prompt)}"
        )
    if digest != config.expected_sha256:
        raise ValueError(
            f"prompt SHA-256 mismatch: expected {config.expected_sha256}, got {digest}"
        )
    return prompt


async def run_qualification(
    config: QualificationConfig,
    *,
    flavor: CliFlavor = codex_exec,
) -> dict[str, Any]:
    engine_origin = Path(ai_workflow_engine.__file__).resolve()
    tools_origin = Path(ai_workflow_tools.__file__).resolve()
    if config.require_installed:
        for package, origin in (("engine", engine_origin), ("tools", tools_origin)):
            if "site-packages" not in str(origin):
                raise RuntimeError(f"{package} did not import from site-packages: {origin}")

    prompt = _read_verified_prompt(config)
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    config.bundle_dir.mkdir(parents=True, exist_ok=True)
    if flavor.name == codex_exec.name and not (config.workspace_dir / ".git").exists():
        raise RuntimeError(
            "Codex qualification workspace must be a disposable Git repository; "
            "initialize it before the call rather than weakening the production argv"
        )

    request = CliAgentRequest(
        prompt=prompt,
        workspace_dir=str(config.workspace_dir),
        model=config.model,
        reasoning_effort="low",
        timeout_s=config.timeout_s - 10,
        expect_json_result=False,
    )
    assembled = build_cli_agent_invocation(flavor, request)
    if assembled.stdin_data != prompt:
        raise AssertionError("assembled stdin differs from the verified prompt")
    if flavor.name == codex_exec.name:
        if assembled.argv[-1] != "-":
            raise AssertionError("Codex stdin marker is absent from argv")
        if any(config.expected_sha256 in item or prompt[:64] in item for item in assembled.argv):
            raise AssertionError("prompt identity or content leaked into argv")

    capability = CliAgentCapability(flavor, name="codex_agent")
    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(
            enabled=True,
            bundle_dir=str(config.bundle_dir),
            capture="off",
            artifacts="off",
        )
    )
    builder.register_capability_spec(capability.spec, capability)
    builder.register_workflow(
        WorkflowBuilder("codex_stdin_qualification").step("codex_agent").build(),
        profile=WorkflowProfile(
            workflow_type="codex_stdin_qualification",
            safety=SafetyPolicy(allowed_side_effects=list(BASH_SIDE_EFFECTS)),
            limits=RuntimeLimits(timeout_s=config.timeout_s, max_worker_calls=1),
        ),
    )
    result = await builder.build().run(
        "codex_stdin_qualification",
        request,
        goal=WorkflowGoal(
            workflow_type="codex_stdin_qualification",
            objective="Qualify exact released-wheel Codex stdin transport.",
            metadata={"run_id": "codex-stdin-qualification"},
        ),
    )

    if result.status != "completed":
        raise RuntimeError(f"qualification workflow {result.status}: {result.error}")
    if not result.observation_bundle_path:
        raise RuntimeError("qualification produced no observation bundle")
    if result.usage.tool_call_count != 1 or len(result.usage.events) != 1:
        raise RuntimeError("qualification must record exactly one provider attempt")
    usage = result.usage.events[0]
    if usage.provider != flavor.name or not usage.success:
        raise RuntimeError("qualification usage event does not identify a successful provider call")
    if usage.input_tokens <= 0 or usage.output_tokens <= 0:
        raise RuntimeError("qualification usage counters are missing")
    if usage.notional_usd is None:
        raise RuntimeError("qualification notional pricing is missing")

    output = result.output
    output_text = output.text if hasattr(output, "text") else str(output)
    if config.expected_output.lower() not in output_text.lower():
        raise RuntimeError("qualification output did not contain the expected marker")
    serialized_trace = json.dumps(result.trace, default=str)
    if prompt[:64] in serialized_trace:
        raise RuntimeError("capture=off trace retained prompt content")

    return {
        "bundle": result.observation_bundle_path,
        "engine_origin": str(engine_origin),
        "model": usage.model,
        "notional_usd": usage.notional_usd,
        "output_tokens": usage.output_tokens,
        "prompt_length": len(prompt),
        "prompt_sha256": config.expected_sha256,
        "provider": usage.provider,
        "provider_attempts": len(result.usage.events),
        "status": result.status,
        "tools_origin": str(tools_origin),
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--expected-length", required=True, type=int)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--workspace-dir", required=True)
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--record", required=True)
    parser.add_argument("--model", default="gpt-5.4-codex")
    parser.add_argument("--timeout-s", type=float, default=180)
    parser.add_argument("--expected-output", default="stdin transport qualified")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.timeout_s <= 10:
        raise ValueError("timeout-s must exceed the 10-second subprocess settlement reserve")
    version = subprocess.run(
        [codex_exec.base_argv[0], "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    evidence = asyncio.run(
        run_qualification(
            QualificationConfig(
                prompt_file=Path(args.prompt_file),
                expected_length=args.expected_length,
                expected_sha256=args.expected_sha256,
                workspace_dir=Path(args.workspace_dir),
                bundle_dir=Path(args.bundle_dir),
                model=args.model,
                timeout_s=args.timeout_s,
                expected_output=args.expected_output,
            )
        )
    )
    evidence["cli_version"] = version
    _atomic_json(Path(args.record), evidence)
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
