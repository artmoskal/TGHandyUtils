# AI Workflow Tools

Reusable tool libraries for `ai-workflow-engine`.

This package currently ships CLI-agent support. It stays outside engine core so provider and CLI
flag drift do not force core-engine changes.

## Package Role

`ai_workflow_engine` owns orchestration policy. This package owns reusable execution surfaces that
are provider- or CLI-specific:

- `claude -p` / `codex exec` argv and result-envelope details live here, not in core.
- CLI-agent episodes are still typed engine capabilities with side-effect gates, budgets, traces,
  timeouts, and usage events.
- Console LLM calls use the same `LLMCallable` socket as API clients, so structured nodes keep
  parse/repair, model binding, and cost accounting.
- Input and output evidence is fingerprinted and referenced; raw bytes stay out of workflow state.
- Subscription workers record `subscription_notional` or honest unknown cost instead of fake zero.

## What Is Shipped

- `CliAgentCapability`: one bounded CLI-agent episode as an engine capability.
- `ConsoleLLMClient`: an `LLMCallable` over a non-interactive CLI for plain text-in,
  structured/text-out calls.
- `CliAgentRequest`: prompt, workspace, MCP servers, allowed tools, `input_assets`,
  salvage globs, model/reasoning flags, timeout, and subscription-mode cost behavior.
- `McpServerConfig.env`: zero-trust MCP environment config passed through flavor assembly.
- `CliAgentResult`: parsed text/JSON, salvaged `EvidenceRef`s, `new_artifact_count`,
  `input_fingerprints`, token fields, return code, and stderr tail.
- Shipped flavors: `claude_p` (`claude -p`) and `codex_exec` (`codex exec`).

Input and output provenance is fingerprinted, not stored as bytes. `input_assets` are materialized
under `inputs/` in the workspace by a product-provided loader; salvage finds configured output files
after the run and reports only refs plus counts.

## Install

From the monorepo:

```bash
pip install -e packages/ai_workflow_engine
pip install -e packages/ai_workflow_tools
```

The standalone release gate performs the same install in a clean virtual environment:

```bash
packages/ai_workflow_tools/scripts/standalone_test.sh
```

Day-to-day repo validation still uses Docker:

```bash
./test.sh unit -- packages/ai_workflow_tools/tests/ --cov-fail-under=0 -q
```

## CLI-Agent Quickstart

```python
from ai_workflow_engine import (
    EvidenceRef,
    SafetyPolicy,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowProfile,
)
from ai_workflow_tools.cli_agents import (
    CliAgentCapability,
    CliAgentRequest,
    McpServerConfig,
    claude_p,
)


def load_asset(ref: EvidenceRef) -> bytes:
    # Product code owns URI resolution. Bytes are staged into the workspace only at call time.
    return b"asset bytes"


browser_agent = CliAgentCapability(
    claude_p,
    name="run_browser_scenario",
    side_effects=["workspace_write"],
    asset_loader=load_asset,
)

workflow = (
    WorkflowBuilder("audit_site")
    .step("run_browser_scenario")
    .build()
)

engine = (
    WorkflowEngineBuilder()
    .register_capability_spec(browser_agent.spec, browser_agent)
    .register_workflow(
        workflow,
        profile=WorkflowProfile(
            workflow_type="audit_site",
            safety=SafetyPolicy(allowed_side_effects=["workspace_write"]),
        ),
    )
    .build()
)

result = await engine.run(
    "audit_site",
    CliAgentRequest(
        prompt="Inspect the subscription form and return JSON.",
        workspace_dir="/tmp/audit-site",
        mcp_servers=[
            McpServerConfig(
                name="browser",
                command="npx",
                args=["@modelcontextprotocol/server-puppeteer"],
                env={"BROWSER_CHANNEL": "chrome"},
            )
        ],
        allowed_tools=["mcp__browser__navigate", "mcp__browser__screenshot"],
        input_assets=[
            EvidenceRef(role="seed", uri="memory://site-map", media_type="text/plain")
        ],
        salvage_globs=["*.png", "session*.md"],
        subscription_mode=True,
    ),
)
agent_result = result.output
```

`subscription_mode=True` records `WorkflowUsageEvent.cost_class="subscription_notional"` and fills
typed normalized usage from Claude/Codex structured output. Claude's provider-reported total wins;
Codex counters are priced by the engine's versioned public/proxy catalog. If usage or a matching
rate is unavailable, `notional_pricing.source="unknown"` carries a closed reason instead of a fake
zero. This is API-equivalent plan value, not billed subscription spend.

## Console Quickstart

Use `ConsoleLLMClient` when the job is a simple structured LLM call and does not need MCP tools,
workspace assets, or artifact salvage:

```python
from pydantic import BaseModel

from ai_workflow_engine import StructuredLLMNode, WEAK_MODEL_CLEANER
from ai_workflow_tools.cli_agents import ConsoleLLMClient, claude_p


class Verdict(BaseModel):
    status: str
    reason: str


node = StructuredLLMNode(
    name="report_verdict",
    config=object(),
    output_model=Verdict,
    prompt_template="Return a JSON verdict for: {summary}",
    input_variables=["summary"],
    llm=ConsoleLLMClient(claude_p),
    pre_parse=WEAK_MODEL_CLEANER,
)

verdict = await node.run({"summary": "all scenarios passed"})
```

`ConsoleLLMClient` refuses images and tool-calling turns before spawning a process. Use
`CliAgentCapability` when the episode needs tools, MCP, staged assets, or workspace artifacts.

## Verification

The tools package tests are hermetic and fake-backed. `tests/fake_cli.py` covers the shipped envelope,
artifact, sleep/timeout, result-file, and garbage-output modes used by the capability and console
client tests.
