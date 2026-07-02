# MageQA — AI Workflow Engine Usage Guide

> **PIN (2026-07-02):** pin tag `engine-v0.6.4` and build a wheel from it. The consumer model is
> breaking-allowed tag-to-tag — do NOT track the live branch. The current tag carries the full
> capability set (see **"What v0.6.4 gives you"**): cross-run memory, config-first observation
> (log→bundle→viewer; HTML rendering lives in the separate `ai_workflow_viewer` package), strict
> prompt files, machine identity, and the hardened seam guards.
> **One-door rule for consumers:** construct provider clients only through sanctioned factory/adapter
> modules. A direct provider client in workflow code is a reviewed allow-list entry, not a local
> shortcut — otherwise you lose observability, cost accounting, and model-swap.

Status: **ready for adoption — pin `engine-v0.6.4`** and build a wheel; never track the live branch.
The `WorkflowDefinition` / `WorkflowExecutor` / DI layer is live and proven (Anki runs on it in
production; the product-neutral examples include site-audit fan-out and the three-axis pilot). The
sibling `ai_workflow_tools` package ships CLI-agent + console-LLM support (`claude -p` / `codex exec`)
and the media pack. See **"What v0.6.4 gives you"** at the end for the full capability list.
Not in v0.6 (deferred): durable/semantic memory beyond the `MemoryStore` seam, FlowArtifact v1.5,
ProcessArtifact/v2, browser/no-API executors.
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

## 1a. Adopter contract AC

Before MageQA treats the engine integration as ready, run these checks in the MageQA repo:

- **No product orchestration loop:** audit workflow packs declare `WorkflowDefinition`s and register
  capabilities; they do not hand-roll retry, retrace, fan-out, scheduler, or side-effect/budget loops
  around the engine.
- **One provider door:** `OpenAI`, `AsyncOpenAI`, `ChatOpenAI`, browser/CLI provider clients, and other
  paid/provider SDKs are constructed only in sanctioned MageQA factory/adapter modules.
- **Closed run-state statuses:** dashboard/API/orchestrator projections use engine statuses or a
  MageQA enum mapped deterministically from them. Ad-hoc progress states such as `ok`, `done`,
  `empty`, or `hollow` must fail schema/projection tests.
- **Suspend/resume remains engine-owned:** human review or blocked states use engine
  `requires_user_input`/snapshot paths instead of a separate MageQA wait loop.

Reference MageQA test shape: enum rejects ad-hoc statuses; engine result maps to deterministic
transition; failed/partial/blocked states remain visible and never collapse to silent-empty output.

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

The same application config file carries the observation policy (see "Config-first
observation" below) — one reviewable YAML for profile, models, and observation:

```yaml
observation:
  enabled: true
  bundle_dir: data/observations
  retention_limit: 100
  capture: full
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
- **Memory store contract.** The engine exposes deterministic `MemoryStore` plus dev-only
  `InMemoryMemoryStore`; MageQA owns the durable sqlite implementation behind that Protocol. Use
  `MemoryNamespace("mageqa", tenant_id, target_origin, record_kind)` where `tenant_id` is the hard
  isolation boundary, `target_origin` is the site/app target, and `record_kind` is a family such as
  `learned_flow`, `finding_history`, `target_profile`, or `flaky_check`. Recall is exact/filter-based
  (`get` / `search(metadata_filter=...)`), non-authoritative, and evidence-linked through
  `MemoryRecord.evidence_refs`; semantic/vector recall and LLM extraction are deferred.

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
5. If you need cross-run memory, implement a product sqlite `MemoryStore` and inject it into the
   capabilities that read/write learned flows, finding history, target profiles, and flaky-check
   state. Do not add a product coordinator loop around the engine for memory.
6. Replace your run entrypoint with `await engine.run("audit_site", site_input)`.

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
JsonlTraceSink, CallbackTraceSink, AsyncQueueTraceSink, TeeTraceSink, MemoryNamespace, MemoryRecord,
MemoryStore, InMemoryMemoryStore, format_trace_events`.
Tools import from `ai_workflow_tools.cli_agents`: `CliAgentCapability`, `CliAgentRequest`,
`CliAgentResult`, `ConsoleLLMClient`, `McpServerConfig`, `claude_p`, `codex_exec`. Runnable
examples: `ai_workflow_engine/examples.py` (`build_demo_engine`, `SiteAuditPack`,
`run_toy_site_audit_pilot`, `run_toy_three_axis_site_audit_pilot`).


---

## What v0.6.4 gives you

The full capability set in `engine-v0.6.4` (older tags are unsupported — no deltas to track):

- **Agent brain + LLM protocol.** Multi-turn, tool-calling protocol (`ChatMessage`/`ToolSpec`/
  `ToolCallRequest`/`ToolResult`); a shipped `LLMAgentPlanner` (screenshot→vision loop, per-turn
  budget, structured finish with repair) and `ReplayPlanner` (freeze a recorded episode into a
  zero-LLM regression). One-line wire: `build_llm_agent_capability(llm, engine.registry,
  allowed_tools=[...], runtime=engine.runtime)`.
- **CLI worker economics** (`ai_workflow_tools`): `CliAgentCapability` (`claude_p`/`codex_exec`, MCP
  config incl. `env`, workspace salvage→`EvidenceRef`s, `new_artifact_count`, fingerprinted
  `input_assets`) and `ConsoleLLMClient` (text→JSON over a subscription CLI behind structured nodes).
  The three-axis pilot (`ai_workflow_tools.pilots.run_toy_three_axis_site_audit_pilot`) is the
  canonical end-to-end recipe; its test proves the replay run makes ZERO LLM calls.
- **Budget + honest cost.** `max_worker_calls`, per-call token/image/USD caps (all Optional);
  `metered` vs `subscription_notional` (`cost_known=false`, never phantom $0); per-call output
  overflow records `truncated_by_budget` and the run continues; hard stops are `max_worker_calls` +
  USD caps. Accounting is split into focused modules (budget / pricing / provider_usage / usage_events / usage_rendering) behind the stable `ai_workflow_engine.usage` facade — public imports unchanged.
- **Self-describing machine.** Declare label semantics once (`.branch(..., describe={...})`); deciders
  get their legal moves + live gate budgets via `inject_machine=True` (`context.metadata["machine"]`).
  Any branch label that closes a loop REQUIRES a pre-set gate (`bounds={"label": N}`,
  `exhausted={"label": "escape"}`) — ungated cycles fail validation loudly. **Validation now requires a `describe` entry for EVERY decision label on an `inject_machine=True` branch** (the goblin rule): an option the navigator cannot understand fails the build loudly instead of producing an opaque machine card. Zero-LLM routing via
  `engine.register_guard(...)` (traced `decision_policy`). Feed flow-author prompts with
  `render_capability_catalog(engine.registry, allowed)`.
- **Flow-as-data + planning.** `engine.run_authored_flow(FlowArtifact(...), payload)` — an LLM emits a
  constrained workflow (steps/branches/gates), validated before compiling, run on the same rails (no
  nested planners/flow-authors — recursion firewall). Bounded recursive planning (`.plan(...,
  max_plan_depth=, max_total_planned_tasks=)`); parallel sub-workflows
  (`register_workflow_capability` + `.fanout(..., capability="run_child")`, failure-isolated).
- **Durable suspend/resume + memory.** Long runs suspend/resume via `result.snapshot` +
  `engine.resume` (budgets cumulative). Cross-run memory via the `MemoryStore` seam
  (`MemoryNamespace`, `MemoryRecord`, `InMemoryMemoryStore`; bring your own sqlite for durability).
- **Media** (`ai_workflow_tools.media`): image/voice providers live in tool packs; the engine stays
  provider-neutral (`vision`/image-input stays in the engine as LLM protocol). Install
  `ai-workflow-tools[media]`.
- **Config-first observation (zero product plumbing).** Declare it once in the application
  config —

  ```yaml
  observation:
    enabled: true
    bundle_dir: data/observations
    retention_limit: 100
    capture: full
  ```

  — and the ENGINE owns every per-run mechanic: it auto-opens the bundle, routes
  trace/details/usage into it, finalizes with the true terminal status, prunes old finalized
  bundles, and reports the location on `result.observation_bundle_path`. Products never call
  bundle mechanics in the normal path; `engine.run(observation_bundle=)` remains the explicit
  escape hatch (it takes precedence) and `terminal_status=` stays the post-validation hook —
  a raising run archives as failed, and the record can never say completed for a user-visible failure.
- **Prompt files with strict rendering.** Prompts can live as FILES under a locked prompt root:
  `StructuredLLMNode(prompt_ref=PromptRef("mageqa/route.txt"), prompt_renderer=engine.prompt_renderer)`
  (split static/dynamic refs supported). A missing variable fails LOUDLY before any model call;
  paths cannot escape the root; template+rendered digests are recorded. Jinja is one optional
  dialect (`renderer="jinja"`, strict-undefined) — requires the product to install `jinja2`;
  never an engine dependency. Raw string templates keep working; refs and raw are mutually
  exclusive per node.
- **Machine identity + fresh compiles.** Every `WorkflowDefinition` carries a content digest;
  compiled graphs and registries key on `(workflow_id, digest)`, so re-registering a changed
  definition under the same id runs the NEW machine (a `machine:re-registered` trace event marks
  the swap) — dynamic/authored flows can never execute a stale graph.
- **Nested suspension is rejected loudly (current restriction).** A subworkflow whose child
  (transitively) declares `human` nodes fails preflight, and a child that suspends at runtime fails
  the parent node with an explicit error instead of silently continuing — waits belong at the
  top-level workflow until nested snapshots ship (named-consumer gated).
- **Envelope trace is sink-independent.** `result.trace` comes from a per-run session buffer — it
  is complete with Jsonl/bundle sinks and long-lived engines no longer accumulate cross-run trace
  history for the envelope. Planner fanout now enforces `max_total_planned_tasks` exactly like
  sequential execution.
- **Hardened seams + per-run isolation.** Contract guards ship with the engine repo and are
  mutation-verified: one provider door (construction only in sanctioned factory/adapter modules),
  no product orchestration loop, closed run-state vocabulary, engine-imports-no-products, and the
  executor/node boundary (`NodeExecutionServices` — node handlers are testable against a fake
  services object; see `tests/test_contract_guards.py` for the guard style to copy). Every run gets
  its own internal run session: concurrent runs on ONE engine are isolation-tested
  (trace/usage/detail partition by run id), resume rebuilds the session without re-executing
  completed nodes, and an attached observation bundle finalizes with the run's terminal status —
  failed runs included.
- **Observability — log→bundle→viewer.** A run appends durable events to a per-run bundle
  (`observations/<run_id>/{trace,details,usage}.jsonl` + `meta.json` + `definition.json`); nothing is
  rendered in the hot path. The separate **`ai_workflow_viewer`** package reads bundles (via
  `EventSource`/`FileEventSource`) and builds every view on demand — the state-machine diagram
  (`workflow_to_mermaid` / `save_workflow_html`), the observation graph + timeline
  (`save_observation_html`), and a run chooser / JSON-detail server (`JsonlObservationViewer` /
  `serve_viewer`). Capture has an off-switch; bundle dir defaults to `data/observations` (override
  `OBSERVATION_DIR`). The disk bundle is transport #1 — swappable to a bus/live consumer later.
