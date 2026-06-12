# MageQA — AI Workflow Engine Usage Guide

Status: **engine implemented and ready for adoption** (2026-06-12). The `WorkflowDefinition` /
`WorkflowExecutor` / DI layer this doc previously waited on is live and proven (Anki migrated +
live-tested; one `WorkflowExecutor` runs the product-neutral examples, including site-audit fan-out
and the fake-backed three-axis pilot). The first sibling tools package, `ai_workflow_tools`, now
ships CLI-agent and console-LLM support for `claude -p` / `codex exec`.
Source needs: `/Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md`,
`/Users/artemm/PycharmProjects/MageQA/docs/14-agentic-tester-architecture.md`.

This is a **how-to**: declare your audit flow, register your capabilities, run it. The engine owns the
deterministic harness (plan→fan-out→adjudicate→deepen→report mechanics, scheduling, side-effect/budget
gates, trace, subworkflows); you own the agentic/domain core (browser agents, rubric, grounding,
report). **You write no coordinator loop** (a static guard enforces this).

---

## 1. The whole adoption in three steps

```python
from ai_workflow_engine import (
    WorkflowBuilder, WorkflowEngine, Retrace, Fallback, BranchDecision,
)

# (1) DECLARE the audit flow — plan -> fan-out scenarios -> adjudicate -> deepen -> report.
audit_site = (
    WorkflowBuilder("audit_site")
    .step("plan_qa_session")                         # rubric/budget in -> list of scenario inputs
    .fanout("run_scenarios", capability="run_browser_scenario",   # bounded parallel browser agents
            items_key="plan_qa_session", max_parallel=4)          # partial-failure isolated
    .step("adjudicate_findings")                     # keep only evidence-grounded findings
    .evaluate("coverage_gate", on_reject=Retrace("plan_qa_session"))  # deepen: re-plan if thin, bounded
    .subworkflow("report", workflow=REPORT_FLOW)     # report sub-flow (curate -> render -> write)
    .build()
)

# (2) WIRE dependencies via DI.
engine = WorkflowEngine.from_config("config/mageqa.audit.yaml")   # rubric/budget/per-role models/safety
engine.register_pack(SiteAuditPack())

# (3) RUN.
result = await engine.run("audit_site", SiteAuditInput(url="https://example.test", rubric=[...]))
# result.status / .output (report) / .node("run_scenarios").output / .usage / .trace
```

No MageQA-owned supervisor loop, fan-out loop, retry/deepen loop, or trace/budget runtime. The guard
`test_examples_contain_no_product_orchestration_loops` fails the build if a pack hand-rolls those.

---

## 2. Node kinds → MageQA roles

| Builder call | Node | MageQA role |
|---|---|---|
| `.step("plan_qa_session")` (capability `kind="llm"` via `StructuredLLMNode`) | step | coordinator plan: schema-validated scenario list |
| `.fanout(id, capability="run_browser_scenario", items_key=…, max_parallel=N)` | fanout | bounded browser/agent scenario execution, partial-failure isolated, shared budget |
| `.step("adjudicate_findings")` (deterministic) | step | grounding/adjudication: accept only evidence-backed findings |
| `.evaluate(id, on_reject=Retrace("plan_qa_session"))` | evaluate | review/deepen: re-plan or re-run scenarios under a bounded round cap, with criticism |
| `.branch(id, {label: target}, decider=…)` | branch | route by severity / coverage / "needs human review" |
| `.subworkflow("report", workflow=…)` | subworkflow | report sub-flow (curate → render → write), recursive, own budget |
| `.human(id)` | human | optional human review/triage (pause / provisional / resume) |
| `.step("write_report", side_effects=["external_write"])` | step | idempotent report/store handoff |

Browser/CLI workers are **capabilities**, not raw subprocess calls in flow code:
- an in-engine **bounded agent**: `AgentCapability` over scoped registered browser/MCP tools (step
  caps, tool-call caps, scoped tool names, recorded steps, replay, and subscription-mode metadata);
- a shipped tools-library **CLI agent**: `ai_workflow_tools.cli_agents.CliAgentCapability` over
  `claude_p` or `codex_exec`, with MCP config/env assembly, timeout, input-asset staging,
  salvage-always artifact provenance, `new_artifact_count`, and subscription-notional usage; or
- a shipped tools-library **console client**: `ConsoleLLMClient` for simple text-to-JSON planner or
  report nodes that do not need MCP/tools/workspace assets.

---

## 3. Registering capabilities (a `WorkflowPack`)

```python
class SiteAuditPack:
    def register(self, builder) -> None:
        builder.register_capability("plan_qa_session", PlanQaSession(...),       # StructuredLLMNode-backed
                                    kind="llm", metered=True, output_model=SessionPlan)
        builder.register_capability("run_browser_scenario", browser_agent,       # AgentCapability or adapter
                                    kind="agent", side_effects=["browser_drive"], timeout_s=120)
        builder.register_capability("adjudicate_findings", Adjudicate(...), kind="deterministic")
        builder.register_capability("coverage_gate", CoverageEvaluator(...), kind="deterministic")
        builder.register_capability("write_report", ReportAdapter(sink),
                                    kind="external", side_effects=["external_write"])
        builder.register_workflow(audit_site, profile=audit_profile)
        builder.register_workflow(REPORT_FLOW)        # registered so the subworkflow node can call it
```

Handler signature: `def handler(context, payload) -> output | CapabilityResult` (sync or async); an
object exposing `.spec` (e.g. `AgentCapability`, `ExternalAdapterCapability`) is accepted directly.
The `CapabilitySpec` carries input/output schema, `kind`, `side_effects`, `metered` (cost class),
`timeout_s`, `max_attempts`; the engine enforces them and **denies a capability whose `side_effects`
are not in the profile's `allowed_side_effects` — before the handler runs**.

**Coordinator decisions are data, not loops:** `plan_qa_session` returns a structured plan
(schema-validated, JSON-repaired by `StructuredLLMNode`); `coverage_gate` returns
`CapabilityResult(status="rejected", metadata={"criticism": "thin coverage on checkout"})` and the
engine deepens via `Retrace`, bounded by `RuntimeLimits.max_retrace`.

### CLI and console worker recipe

```python
from ai_workflow_engine import EvidenceRef, StructuredLLMNode, WEAK_MODEL_CLEANER
from ai_workflow_tools.cli_agents import (
    CliAgentCapability,
    CliAgentRequest,
    ConsoleLLMClient,
    McpServerConfig,
    claude_p,
)

browser_worker = CliAgentCapability(
    claude_p,
    name="run_browser_scenario",
    side_effects=["workspace_write"],
    asset_loader=load_evidence_bytes,    # product-owned EvidenceRef -> bytes
    trace_sink=live_trace_sink,           # optional Callback/AsyncQueue/Tee sink
)

report_node = StructuredLLMNode(..., llm=ConsoleLLMClient(claude_p), pre_parse=WEAK_MODEL_CLEANER)

async def write_report(_context, payload):
    return await report_node.run(payload)

builder.register_capability_spec(browser_worker.spec, browser_worker)
builder.register_capability("write_report", write_report, kind="llm")

request = CliAgentRequest(
    prompt="Inspect checkout with the supplied seed screenshot and return JSON findings.",
    workspace_dir="/tmp/mageqa/session-123",
    mcp_servers=[
        McpServerConfig(
            name="browser",
            command="npx",
            args=["@modelcontextprotocol/server-puppeteer"],
            env={"BROWSER_CHANNEL": "chrome"},
        )
    ],
    allowed_tools=["mcp__browser__navigate", "mcp__browser__screenshot"],
    input_assets=[EvidenceRef(role="seed", uri="artifact://seed-checkout.png", media_type="image/png")],
    salvage_globs=["*.png", "session*.md", "page-*.yml"],
    subscription_mode=True,
)
```

Prompt convention: staged `input_assets` appear under `inputs/` in the worker workspace. Do not copy
bytes into workflow state; the capability records `input_fingerprints` and emits `inputs_staged`
trace metadata. Salvaged outputs return `EvidenceRef`s plus `new_artifact_count`, and the trace gets
an `artifacts_salvaged` event. Adjudicators should gate on `new_artifact_count` or referenced
evidence, not on ungrounded agent prose.

---

## 4. Config & profile (`from_config`)

```python
profile = WorkflowProfile(
    workflow_type="audit_site",
    safety=SafetyPolicy(fail_mode="fail_closed",
                        allowed_side_effects=["browser_drive", "workspace_write", "external_write"]),
    limits=RuntimeLimits(
        max_parallel_children=4,
        max_retrace=2,
        max_estimated_usd=2.00,
        max_worker_calls=24,
        max_input_tokens_per_call=120_000,
        max_output_tokens_per_call=8_000,
        max_images_per_call=12,
    ),
)
```

Per-role models go in the YAML model registry (`ModelProfile` per capability/role); rubric/budget come
in via `engine.run(..., constraints={...})` or the goal. Swap the profile to change models/budget/
safety without touching the workflow (proven by `test_engine_from_config_swaps_profile`).
Metered API calls debit `WorkflowUsageSummary.metered_usd`; CLI subscription workers record
`WorkflowUsageEvent.cost_class="subscription_notional"` and `notional_usd` when the CLI reports it.
Unknown CLI cost stays explicit through `cost_known=false` metadata.

---

## 5. MageQA-specific engine features (do NOT reimplement)

- **Fan-out / gather.** `.fanout(..., max_parallel=N)` runs N scenarios concurrently with bounded
  parallelism and **partial-failure isolation** (a timed-out/failed scenario doesn't sink the run;
  the parent gets the successes, status `partial`) — proven by `test_fanout_gather_isolates_partial_failure`.
- **Bounded agents.** `AgentCapability` runs a scoped tool/reasoning episode with step + tool-call
  caps, allowed-tool scoping, recorded steps, structured finish parsing, replay, and
  subscription-mode metadata — so a runaway agent can't blow the budget. MCP/browser tools are
  reachable as registered capabilities.
- **CLI-agent episodes.** `CliAgentCapability` runs `claude -p` / `codex exec` through the same
  engine capability runtime, with side-effect denial before spawn, MCP config env, staged
  `input_assets`, artifact salvage, `new_artifact_count`, and shared engine JSON cleaners.
- **Console report/planner calls.** `ConsoleLLMClient` is an `LLMCallable` for simple CLI-backed
  structured nodes; images/tool-calling turns are refused before process spawn.
- **Deepen loop = evaluator retrace.** `.evaluate("coverage_gate", on_reject=Retrace("plan_qa_session"))`
  re-plans/re-runs under `max_retrace`, threading criticism — the engine owns the loop (proven by
  `test_evaluate_retrace_to_earlier_node_then_accepts`).
- **Grounding gate.** Adjudication is a deterministic capability that accepts only evidence-grounded
  findings; carry evidence as `EvidenceRef(role=…, uri=…)` (no raw bytes in state). Findings reference
  their `evidence_ref_id`.
- **Cost / subscription.** Mark metered capabilities `metered=True`; the engine tracks usage/cost and
  **denies metered calls once `max_estimated_usd` is exhausted** (proven by
  `test_budget_exhaustion_denies_metered_capability`). Subscription/flat-rate workers report
  `subscription_notional` usage; this is visible in traces and summaries but does not debit the
  metered budget.
- **Per-role models.** Register a `ModelProfile` per role; `from_config` selects them; swapping config
  changes models without code edits.
- **Trace.** `format_trace_events(result.trace, usage=result.usage)` renders capability name,
  decision, key output, timings, cost, artifacts, and deepen/fallback decisions — your
  `ProcessingTrace` maps onto the engine `TraceSink` (`InMemoryTraceSink`, `JsonlTraceSink`,
  `CallbackTraceSink`, `AsyncQueueTraceSink`, or `TeeTraceSink`).
- **Subworkflows (recursive).** `page_discovery` / `accessibility` / `performance` / `report` are
  separate `WorkflowDefinition`s registered as capabilities and composed via `.subworkflow(...)` on the
  same executor, with inherited/narrowed budget and parent/child trace.
- **Fail-closed safety propagation.** `SafetyPolicy(fail_mode="fail_closed")`; forbidden side effects /
  missing capabilities / unsupported nodes fail loudly + trace, never silently no-op.

---

## 6. Migration recipe

1. Keep your coordinator/agent/check/report functions — register each as a capability with a
   `CapabilitySpec` (schema + side-effect class + timeout + cost). Wrap browser/CLI workers as
   `AgentCapability` or `CliAgentCapability`; use `ConsoleLLMClient` for plain text-to-JSON planner
   or report calls.
2. Make the coordinator emit a **structured plan** (scenario list); make review/deepen an **evaluator**
   returning accept/reject + criticism; make routing a **branch decider**.
3. Express plan→fan-out→adjudicate→deepen→report with `WorkflowBuilder`; delete your coordinator loop,
   fan-out/gather loop, retry/deepen loop, and any trace/budget runtime.
4. Move per-role models / rubric defaults / budget / safety into a YAML profile; load via `from_config`.
5. Replace your run entrypoint with `await engine.run("audit_site", site_input)`.

**Done when** you can delete MageQA orchestration loops and still run from
`WorkflowDefinition + registered capabilities` through `engine.run`. Verify with a `format_trace_events`
dump + the no-product-loop guard.

---

## 7. Reference (import from `ai_workflow_engine`)

`WorkflowBuilder, WorkflowDefinition, WorkflowNode, WorkflowEdge, WorkflowEngine,
WorkflowEngineBuilder, WorkflowPack, WorkflowExecutor, WorkflowRunResult, NodeResult,
BranchDecision, Retry, Retrace, Fallback, SubworkflowRef, AgentCapability, LLMAgentPlanner,
ReplayPlanner, EvidenceRef, ExternalWriteRequest, ExternalWriteResult, ExternalAdapterCapability,
ExternalProcessCapability, HumanClarificationCapability, StructuredLLMNode, SchedulingPolicy,
SafetyPolicy, RuntimeLimits, WorkflowProfile, ModelProfile, WorkflowGoal, InMemoryTraceSink,
JsonlTraceSink, CallbackTraceSink, AsyncQueueTraceSink, TeeTraceSink, format_trace_events`.
Tools import from `ai_workflow_tools.cli_agents`: `CliAgentCapability`, `CliAgentRequest`,
`CliAgentResult`, `ConsoleLLMClient`, `McpServerConfig`, `claude_p`, `codex_exec`. Runnable
examples: `ai_workflow_engine/examples.py` (`build_demo_engine`, `SiteAuditPack`,
`run_toy_site_audit_pilot`, `run_toy_three_axis_site_audit_pilot`).


---

## What's new since this guide's examples were written (engine-completion delta, 2026-06-12)

Implemented and gate-verified (commits up to `b014268`+):
- **Multi-turn + tool-calling LLM protocol** (`ChatMessage`/`ToolSpec`/`ToolCallRequest`/`ToolResult`
  on `LLMRequest/LLMResponse`) and a **shipped agent brain**: `LLMAgentPlanner` (screenshot→vision
  loop, budget per turn, structured finish with repair) + `ReplayPlanner` (freeze a recorded episode
  into a rigid, zero-LLM regression). Wire one line: `build_llm_agent_capability(llm, engine.registry,
  allowed_tools=[...], runtime=engine.runtime)` — pass the engine runtime so episode traces join the
  same sink.
- **CLI worker economics** (`ai_workflow_tools`): `CliAgentCapability` (claude_p / codex_exec flavors,
  MCP config incl. `env`, workspace salvage→`EvidenceRef`s, `new_artifact_count`,
  `input_assets` staged + fingerprinted into the episode record) and `ConsoleLLMClient` (plain
  text→JSON over a subscription CLI behind structured nodes).
- **Budget matrix** (`max_worker_calls`, per-call input/output token, image, and USD caps — all
  Optional) and **honest cost classes**: `metered` vs `subscription_notional`; `cost_known=false`
  instead of phantom $0; the notional total renders only when subscription events exist.
- **Per-call output-token overflow records `truncated_by_budget` on the usage event and the run
  CONTINUES** (the output is already paid for); hard stops remain `max_worker_calls` + USD caps.
- **Live trace sinks**: `CallbackTraceSink`, `AsyncQueueTraceSink` (drop-oldest, never blocks the
  run), `TeeTraceSink` (live + JSONL together).
- **The three-axis pilot** (rigid/semi-rigid/flexible execution × set-composition × freeze-to-replay)
  lives at `ai_workflow_tools.pilots.run_toy_three_axis_site_audit_pilot` — read it as the canonical
  end-to-end recipe; its test proves the replay run makes ZERO LLM calls.
- Your completion spec (engine-completion-spec.md) is fully implemented — WP1–WP7 plus review items AC-R3…R6; adoption can start at the pilot + the §7.5 console quickstart in the tools README.
