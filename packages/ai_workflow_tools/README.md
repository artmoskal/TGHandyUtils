# AI Workflow Tools

Reusable tool libraries for `ai-workflow-engine`.

This package ships optional provider, CLI-agent, and media execution surfaces. They stay outside
engine core so wire protocols, provider SDKs, and CLI flag drift do not force core-engine changes.

## Package Role

`ai_workflow_engine` owns orchestration policy. This package owns reusable execution surfaces that
are provider- or CLI-specific:

- `claude -p` / `codex exec` argv and result-envelope details live here, not in core.
- **Prompts are always delivered on stdin, never in argv.** Every shipped flavor declares
  `prompt_delivery="stdin"`, and that is the only accepted value — there is no size threshold and no
  argv fallback. Prompt text in a command-line argument fails at process creation once it approaches
  the Linux per-argument limit (a legitimate 125,799-character prompt surfaced as
  `[Errno 7] Argument list too long` before the provider was ever called), and argv is readable by
  any process through `/proc/<pid>/cmdline`. For Codex the prompt positional is the documented `-`
  stdin marker; large prompts therefore need no prompt file, wrapper, or truncation at the product
  layer. Backpressure, timeout, cancellation, and process-tree cleanup stay with the engine's
  bounded process owner.
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
- `OpenAICompatibleLLMClient`: one async `LLMCallable` for explicit OpenAI-compatible
  `/v1/chat/completions` endpoints, including OpenAI, Ollama, vLLM, and LM Studio.
- `OpenAICompatibleProviderConfig`: closed endpoint/model/provider/auth/timeout configuration;
  it reads no environment variables and has no implicit paid endpoint.
- `run_openai_compatible_provider_conformance`: reusable text/image/tool/usage/error/cancellation/
  concurrency checks for product adapters and endpoints.
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

Install the HTTP provider pack explicitly:

```bash
pip install "ai-workflow-tools[openai]"
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

## OpenAI-Compatible Provider

Products resolve secrets and deployment configuration, then inject the configured client through
their one provider door. The adapter does not read environment variables, retry, fall back to
another provider, or invent missing usage.

```python
from pydantic import SecretStr

from ai_workflow_tools.providers.openai_compatible import (
    ApiKeyAuth,
    NoAuth,
    OpenAICompatibleLLMClient,
    OpenAICompatibleProviderConfig,
)

openai_client = OpenAICompatibleLLMClient(
    OpenAICompatibleProviderConfig(
        base_url="https://api.openai.com/v1",
        model="product-approved-model",
        provider="openai",
        auth=ApiKeyAuth(api_key=SecretStr(product_secret)),
        timeout_s=60.0,
    )
)

ollama_client = OpenAICompatibleLLMClient(
    OpenAICompatibleProviderConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="local-model-name",
        provider="ollama",
        auth=NoAuth(),
        timeout_s=120.0,
    )
)
```

Both clients implement the engine's `LLMCallable` and support simple or multi-turn text, input
images, tool definitions, structured tool calls, and tool-result turns. The engine owns the agent
loop, usage ledger, pricing policy, budgets, and retry decisions. The configured timeout is a total
wall-clock bound for the provider exchange and can only narrow the active engine invocation window.
Caller cancellation propagates unchanged; `401`, `429`, redirects, provider errors, malformed
output, and missing usage stay typed and visible. The adapter never follows or retries them.

OpenAI-style prompt totals include cached input, and completion totals include reasoning output.
The adapter normalizes those subsets once. If an endpoint omits or malforms usage, cost remains
typed unknown rather than `$0`. Local endpoints should normally use a provider label with no
configured rate unless the product has an explicit internal pricing policy.

Run the reusable conformance kit against the client a product actually composes:

```python
from ai_workflow_tools.testing import run_openai_compatible_provider_conformance

await run_openai_compatible_provider_conformance(make_client)
```

Tools `0.7.0` carries the provider and CLI behavior of `0.6.10` unchanged, targets
`ai-workflow-engine>=0.12.0,<0.13`, and belongs to the `engine-v0.12.0` candidate matrix. Do not
install it until that immutable release is published and verified.

The v0.6.4 release carries the v0.6.3 provider behavior unchanged under the corrected source-only
release-evidence contract.

The v0.6.3 release carries the v0.6.2 behavior unchanged and gives the clean-checkout operational
release a unique wheel identity. It targets `ai-workflow-engine>=0.11.12,<0.12`.

The v0.6.2 release carried the v0.6.1 behavior unchanged and only re-pinned the engine floor to
`ai-workflow-engine>=0.11.11,<0.12`, because `engine-v0.11.10` was never published.

The v0.6.1 corrective release retains the v0.6.0 provider pack and additionally makes refusal and
content-filter responses typed failures with retained usage, recognizes Claude semantic errors
even when the CLI exits zero, and prevents browser-provider secrets or URLs from surviving in an
exception cause/context chain. The v0.6.0 provider pack was qualified against a local Ollama
OpenAI endpoint with
`ministral-3:8b-instruct-2512-q4_K_M` for text/tools and `qwen2.5vl:3b` for images. Text, structured
tools, a full engine-agent tool round trip, multimodal input, timeout, usage truth, and concurrent
identity isolation passed. Ollama may retain multiple large models simultaneously; capacity tests
should unload unrelated models rather than treating host memory pressure as adapter retry policy.

## Verification

The default tools tests are hermetic and fake-backed. `tests/fake_cli.py` covers the shipped
envelope, artifact, sleep/timeout, result-file, and garbage-output modes. The OpenAI-compatible
suite adds a real loopback HTTP server, five deliberately broken client negatives, malformed and
oversized response attacks, provider-status/error privacy checks, engine-window timeout, forced
cancellation, concurrency, and a public engine-agent tool round trip. The gated Ollama integration
suite is additional endpoint evidence, not a replacement for those attack tests.
