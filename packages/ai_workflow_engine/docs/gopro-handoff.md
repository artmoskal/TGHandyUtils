# GoPro — AI Workflow Engine Usage Guide

> **PIN (2026-07-07):** pin tag `engine-v0.8.1` and build a wheel from it. The consumer model is
> breaking-allowed tag-to-tag — do NOT track the live branch. The tag carries the full framework
> capability set (see **"What engine-v0.8.1 gives you"**):
> agent prompt-memory policies plus the `MemoryStore` seam, config-first observation
> (log→bundle→viewer; HTML rendering lives in the separate `ai_workflow_viewer` package),
> strict prompt files, machine identity, and hardened seam guards.
> **Release gate: CLOSED 2026-07-07** — package version metadata is lockstep-guarded in both
> packages (engine 0.8.1, tools 0.3.0; pyproject ↔ `__version__` release-guard tests), the
> one-call façade exposes the full run envelope (`return_result=True`), and console tool-flag
> handling covers joined/kebab forms (regression-locked). The tag builds packages that report
> the matching versions.
> **v0.8.1 delta (2026-07-07) — memory/checkpoint hardening patch:** v0.8.0 delivered the GoPro P1
> memory policies; v0.8.1 makes custom reducers/compactors and persisted state fail-closed before
> you lean on them. The shared guard walks Pydantic/dataclass/computed/private fields, mappings,
> concrete containers, scalar subclasses, enum values, exception args, partial args, and object
> attributes; raw `bytes` / `ImageInput`, actual rendered `data:*;base64,` state, iterators, unsafe
> mapping keys, wrapper-carried unsafe values, and opaque/arbitrary custom objects now fail loudly.
> Reducers should emit dict/list/scalar/value objects or evidence refs, not domain objects by repr.
> Unsafe `CompactingMemory(inner=StructuredStateMemory(...))` is rejected with guidance to use
> `StructuredStateMemory(base=CompactingMemory(...))`; compacting wording no longer claims a custom
> compactor preserved findings unless it did; R4 node-level `memory=` is proven end-to-end through
> `engine.run`.
> **v0.8.0 delta (2026-07-05) — YOUR P1 MEMORY REQUEST DELIVERED (additive, no breaking change):**
> R1 `StructuredStateMemory` — pure derived-state reducer (default: per-tool calls/arg-summaries/
> ok/error tallies, product-neutral, AC-S4-pinned), state block appended as the LAST memory-
> produced message (repair prompt lands after it — order pinned by test), byte-free LOUD
> validation of reducer/renderer output (never silent stripping), `reducer_label` +
> `state_chars` on `memory:projection` (counts/labels only; text rides the existing capture
> path). Composes with your target config: `{"mode": "structured_state", "base": {"mode":
> "image_evicting", "keep_last_images": 1}}` (nested base, AC-S3-tested). The AC-S2 behavioral
> proof is in `tests/test_agent_planner.py`: a scripted weak model that follows ONLY the state
> block stops re-zooming visited sections; with full replay alone it repeats.
> R2 `WindowedMemory(max_turns=N)` (dropped turns leave an activity tally + bounded excerpts
> of their OUTPUTS — discovered findings survive windowing by default, size-capped) +
> `CompactingMemory(inner=…, token_threshold=…, keep_last_turns=…)` (pass-through under
> threshold; rule-based default compactor; pluggable compactor is CODE — no hidden model calls).
> R3 builder `memory=` pass-through test exists. R4 per-NODE selection:
> `.step("zoom_agent", memory={...})` — mirrors `model_profile`: loud at graph validation,
> delivered per call via context, overrides the constructed default. Unknown modes/options fail
> loudly everywhere (incl. a strictness fix: `image_evicting` now rejects unknown options).
> Re-validate per your §7: suite + code sweep at tag `engine-v0.8.1`.
> **v0.7.0 delta (2026-07-05) — CONTRACT CHANGE, read before re-pinning:**
> 1. **`CliAgentRequest.allowed_tools` is tri-state.** `None` (the NEW field default) → the
>    documented `DEFAULT_AGENT_TOOLS` (`Read, Grep, Glob, LS, WebFetch, WebSearch, Bash`);
>    `[]` → explicit no-tools (`--tools ""`); a non-empty list → EXACT override, never merged.
>    Old behavior — default `[]` silently inheriting the CLI's own tool defaults — is gone.
>    `codex_exec` rejects a non-None list loudly (its surface is governed by `--sandbox`).
> 2. **Side-effect ledger honesty.** The default `CliAgentCapability` spec now declares
>    `workspace_write` + `external_call` (Bash is in the default tool set; codex_exec always
>    runs `--sandbox workspace-write`). Your workflow profile must ALLOW those effects for
>    agent nodes or the engine refuses pre-invocation. Migrate: allow both effects on real
>    agent flows. For `claude_p`, you may narrow by setting explicit `side_effects=[...]`
>    and a Bash-less `allowed_tools` list. For `codex_exec`, `allowed_tools` is rejected;
>    a narrowed spec that omits the Codex workspace-write/subscription side effects is
>    refused PRE-SPAWN.
> 3. **Text-only console completions are tool-free** (`claude -p … --tools ""`): the injection
>    surface on structured text calls is closed. Staged vision keeps ONLY scoped
>    `Read(./inputs/**)` — the exfiltration canary is re-verified in every live run.
> 4. **Crash fix you want:** external-process stream reading is chunked — a CLI emitting one
>    huge single-line JSON (>64KiB) no longer kills the call (the `LimitOverrunError` class
>    of failures is gone; regression-locked).
> 5. **NEW — discoverable toolset:** `from ai_workflow_tools import TOOL_CATALOG,
>    render_tool_catalog, register_from_catalog` — every shipped tool described (kind, side
>    effects, builder), plus presets `READ_ONLY / WEB / INVESTIGATION / NO_TOOLS`
>    (`DEFAULT_AGENT_TOOLS is INVESTIGATION`); a completeness guard keeps the catalog honest.
> 6. **NEW — one-call façade:** `from ai_workflow_engine import run_single_llm,
>    run_single_step` — a 1-node workflow on the real engine (usage/trace/budget/parse-repair
>    intact) for the trivial case; trace/usage stay reachable via `return_result=True` even when the
>    helper creates the engine internally.
> **GoPro migration check (2 minutes):** a sidecar `CliAgentCapability` worker that relied on
> the old empty-default (CLI-inherit) now gets the documented investigation set — pass `[]`
> explicitly if you want a tool-free episode. For (2): your inventory/goal-compiler profile
> must allow `workspace_write` + `external_call` on agent nodes (deterministic/VLM steps are
> untouched).
> **One-door rule for consumers:** construct provider clients only through sanctioned factory/adapter
> modules (in this repo: `services/llm_factory.py`, `ai_workflow_tools.media.image_generation`,
> `ai_workflow_tools.chatgpt_browser`, `ai_workflow_tools.catalog` — the catalog builders are the
> supported discovery door). A direct provider client in workflow code is a reviewed allow-list
> entry, not a local shortcut — otherwise you lose observability, cost accounting, and model-swap.

Status: **ready for adoption — pin `engine-v0.8.1`** and build a wheel; never track the live branch.
The `WorkflowDefinition` / `WorkflowExecutor` / DI layer is live and proven (Anki runs on it in
production; one `WorkflowExecutor` runs the product-neutral examples). The sibling `ai_workflow_tools`
package ships CLI-agent + console-LLM support (`claude -p` / `codex exec`) and the media pack. See
**"What engine-v0.8.1 gives you"** at the end for the full capability list.
Not in v0.8 (deferred): durable/semantic memory STORES beyond the `MemoryStore` seam, FlowArtifact v1.5,
ProcessArtifact/v2, browser/no-API executors.
v0.9 BRANCH UPDATE — FlowArtifact v1.5a IMPLEMENTED ON BRANCH (pending independent acceptance +
the `engine-v0.9.0` tag; do NOT consume until that tag exists — `engine-v0.8.1` stays your pin).
The S1 goal-compiler slice: authored `fanout` over a
registered item capability (max_items REQUIRED, 1..limits.max_authored_fanout_items default 100 /
engine max 1000; oversize runtime lists fail loudly, never truncate; max_parallel rejected — never
clamped — outside 1..limits.max_parallel_children) + the turnkey author
`build_flow_author_capability(llm, *, registry, model_profiles, limits, allowed_side_effects, ...)`
returning `(CapabilitySpec(is_flow_author=True), handler)` with typed `FlowAuthorRequest(goal,
context)`, catalog-grounded overridable prompt, TARGET-registry validation feeding bounded repair,
per-attempt metering/observation. `run_authored_flow` re-validates under the CURRENT effective
limits (stale artifacts rejected). Two-engine handoff (author engine -> processing engine) is
test-locked. STILL future: authorable subworkflow + general input/output mapping (v1.5 remainder),
ProcessArtifact/v2.
**v0.9 migration notes (apply ONLY when `engine-v0.9.0` exists; `engine-v0.8.1` is the current pin):**
strict `RuntimeLimits` (unknown limit keys now fail config load); typed limits are the ONLY budget
source (`budget_from_limits(RuntimeLimits|None)` — host-config/duck-typed objects are rejected; the
old usage-tracking flag no longer disables engine budgets); firewall markers are typed
`CapabilitySpec.is_planner`/`is_flow_author` fields (metadata/handler-attribute markers are ignored;
`CapabilitySpec` rejects unknown fields); application `0 = no cap` is normalized at the PRODUCT
boundary while engine `RuntimeLimits(...=0)` is an honest hard-zero cap; failed provider attempts now
consume call budgets and emit zero-cost/zero-token failure events on BOTH transports; `FlowArtifact`
nodes are a discriminated per-kind schema (plain `model_dump()`/`model_validate()` round-trips are
guaranteed; foreign/unknown node fields fail loudly).

Source needs: `/Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md`,
`/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/HOME_INVENTORY_CASE.md`.

**Current GoPro package scope:** the detection image should vendor only `ai-workflow-engine==0.8.1`.
It does not include `ai_workflow_tools` or `ai_workflow_viewer` yet. That is intentional for the
current GoPro code path, which imports core engine APIs directly. Add the tools/viewer packages only
when GoPro starts using CLI-agent tool packs, media helpers, or in-container observation rendering.

This is a **how-to**: declare your flow, register your capabilities, run it. The engine owns every
orchestration mechanic (branch, retry, retrace, fallback, fan-out, scheduling, side-effect/privacy/
budget gates, trace, subworkflows). You own the domain: goal, graph shape, prompts, schemas, tools,
adapters, delivery. **You write no orchestration loops** (a static guard enforces this).

---

## 1. The whole adoption in three steps

```python
from ai_workflow_engine import (
    WorkflowBuilder, WorkflowEngine, Retrace, BranchDecision, EvidenceRef, SchedulingPolicy,
)

LOCAL_VLM_LANE = SchedulingPolicy(mode="drop_not_queue", backend_key="local_vlm", max_backend_concurrency=1)

# (1) DECLARE the flow — composable, typed, no orchestration code.
home_inventory = (
    WorkflowBuilder("home_inventory")
    .step("select_evidence")                        # frame/clip refs in, evidence bundle out
    .branch("evidence_quality_gate", {              # AI/deterministic decision -> route
        "enough":    "extract_items",
        "ambiguous": "ask_location",
        "bad":       "fallback_or_fail",
    })
    .step("extract_items", scheduling=LOCAL_VLM_LANE)               # single-flight local-model lane (§5)
    .evaluate("quality_gate", on_reject=Retrace("select_evidence"))  # re-run upstream w/ criticism
    .subworkflow("enrich", workflow=ENRICH_FLOW)    # a workflow used AS a capability (recursive)
    .step("write_inventory", required_side_effects=["external_write"])
    .human("ask_location")                          # clarification landing pad (branch target)
    .step("fallback_or_fail")
    .build()
)

# (2) WIRE dependencies via DI — products inject; the engine enforces the contract.
engine = WorkflowEngine.from_config("config/gopro.inventory.yaml")   # profile/limits/policies
engine.register_pack(InventoryPack())               # registers capabilities + the workflow

# (3) RUN — engine owns everything from here.
result = await engine.run("home_inventory", video_segment, constraints={"camera_id": "kitchen-1"})
# result.status / .output / .node("extract_items").output / .artifacts / .usage / .trace
```

You never write `runtime.invoke(...)` + `if bad: retry/retrace` loops. The guard
`test_examples_contain_no_product_orchestration_loops` fails the build if a pack does.

---

## 1a. Adopter contract AC

Before GoPro treats the engine integration as ready, run these checks in the GoPro repo:

- **No product orchestration loop:** inventory/pipeline workflow packs declare `WorkflowDefinition`s
  and register capabilities; they do not hand-roll retry, retrace, fan-out, scheduler, drop-stale, or
  side-effect/budget loops around the engine.
- **One provider door:** VLM/LLM/media/browser/CLI provider clients are constructed only in sanctioned
  GoPro factory/adapter modules.
- **Closed run-state statuses:** dashboard/API/orchestrator projections use engine statuses or a GoPro
  enum mapped deterministically from them. Ad-hoc progress states such as `ok`, `done`, `empty`, or
  `hollow` must fail schema/projection tests.
- **Suspend/resume remains engine-owned:** clarification, blocked, and long-running wait states use
  engine `requires_user_input`/snapshot paths instead of a separate GoPro wait loop.

Reference test shape: enum rejects ad-hoc statuses; engine result maps to deterministic transition;
failed/partial/blocked states remain visible and never collapse to silent-empty output.

---

## 1b. GoPro migration clarifications

These points answer the review questions that matter before wiring the sidecar or goal compiler.

- **The engine replaces local orchestration mechanics.** GoPro should not keep a separate scheduler,
  retry/retrace loop, drop-stale lane manager, observation bundle writer, trace collector, or run-state
  vocabulary beside the engine. GoPro owns video/domain semantics — frame selection, VLM/OCR tools,
  prompts, schemas, inventory adapters, and delivery — as capabilities and packs.
- **Live sidecar threading is explicit.** One `WorkflowEngine` / `WorkflowExecutor` belongs to one
  asyncio event loop. If a camera sidecar or worker thread needs to submit work, marshal into that
  loop with `asyncio.run_coroutine_threadsafe(engine.run(...), engine_loop)`; do not mutate a shared
  engine from multiple event loops.
- **Backend lanes are engine scheduling policy, not product locks.** Put
  `SchedulingPolicy(backend_key=..., max_backend_concurrency=..., mode=...)` on local-model/VLM nodes.
  The engine owns queue/drop/cancel behavior and keeps the backend slot until the worker really
  completes.
- **Goal compiler boundary.** GoPro may use AI to emit structured plans, scenario inputs, or a
  constrained `FlowArtifact` for registered capabilities. The engine-v0.9.0 tag (implemented on
  branch, NOT yet tagged — your v0.8.1 pin has neither) widens the authorable subset with
  bounded `fanout` (v1.5a) and ships the turnkey author capability, so the S1 goal compiler can be
  two engines: an author engine hosting `build_flow_author_capability(...)` wired to the PROCESSING
  engine's registry/limits, and the processing engine executing via `run_authored_flow`. Do not rely
  on fully arbitrary AI-authored process pipelines: authorable subworkflow/general IO mapping and
  reusable generated process artifacts remain v1.5-remainder/v2 future-stage.
- **Observation and evidence are engine-owned.** Configure observation once in application YAML; the
  engine writes the bundle, archives returned `WorkflowArtifact`s under `artifacts/`, and reports
  `result.observation_bundle_path`. A GoPro dashboard or sidecar UI should read the bundle/viewer
  surface, not build a second trace/evidence store.
- **Memory is product-owned behind the engine seam.** Implement durable GoPro storage behind
  `MemoryStore`; use namespaces such as
  `MemoryNamespace("gopro", tenant_id, target_id, record_kind)` for learned flows, camera/room
  profiles, inventory history, flaky checks, and planning hints. Memory is prompt/context input, not
  control state.

---

## 2. Building the workflow (`WorkflowBuilder`)

Every node kind is **executed by the engine**. `step` dispatches by the bound capability's `kind`
(deterministic / tool / llm / media / voice / external), so most domain work is a `step`.

| Builder call | Node | GoPro use |
|---|---|---|
| `.step(id, capability=…, input_key=…, output_key=…, retry=Retry(n), scheduling=…, required_side_effects=[…], allow_raw_media_export=False)` | step | frame selection, VLM/OCR inspect, extraction, dedupe, domain write |
| `.branch(id, {label: target}, decider=…)` | branch | evidence-quality gate, location-known gate |
| `.fanout(id, capability=…, items_key="prev.list", max_parallel=N)` | fanout | inspect N frames/regions concurrently, partial-failure isolated |
| `.evaluate(id, target=…, evaluator=…, on_reject=Retry()/Retrace("node")/Fallback("cap"))` | evaluate | grounding/coverage gate that re-runs upstream with criticism, bounded |
| `.subworkflow(id, workflow=child, budget_usd=…)` | subworkflow | visual-inspection / clarification / inventory-write sub-flows |
| `.human(id, capability=…)` | human | ask-location clarification (pause / provisional / resume) |

`input_key` reads any prior node's output (or `"__input__"` for the original payload). Sequential
edges auto-wire between consecutive non-branch nodes; branches route only via labels.

---

## 3. Registering capabilities (a `WorkflowPack`)

A pack bundles your domain registrations — domain specifics, **not** execution mechanics.

```python
class InventoryPack:
    def register(self, builder) -> None:               # builder: WorkflowEngineBuilder | WorkflowEngine
        builder.register_capability("select_evidence", SelectEvidenceTool(...),
                                    kind="tool", input_model=VideoSegment, output_model=EvidenceBundle)
        builder.register_capability("extract_items", VlmItemExtractor(...),
                                    kind="llm", metered=True, timeout_s=30,        # cost + timeout enforced
                                    input_model=ExtractionInput, output_model=ObservationSet)
        builder.register_capability("evidence_quality_gate", QualityDecider(...), kind="deterministic")
        builder.register_capability("quality_gate", GroundingEvaluator(...), kind="deterministic")
        builder.register_capability("ask_location", HumanClarification(channel), kind="human")
        builder.register_capability("write_inventory", InventoryAdapter(sink),
                                    kind="external", side_effects=["external_write"],
                                    input_model=ExternalWriteRequest, output_model=ExternalWriteResult)
        builder.register_workflow(home_inventory, profile=inventory_profile)
```

Handler signature: `def handler(context, payload) -> output | CapabilityResult` (sync or async). A
handler object exposing `.spec` (e.g. `HumanClarificationCapability`, `ExternalAdapterCapability`) is
accepted directly. The `CapabilitySpec` carries input/output schema, `kind`, `side_effects`, `metered`
(cost class), `timeout_s`, `max_attempts` — all engine-enforced.

**Decisions are domain data, not loops:** a branch decider returns `BranchDecision(label="ambiguous")`
(or a label string); an evaluator returns `CapabilityResult(status="accepted")` or
`status="rejected", metadata={"criticism": "..."}`. The engine applies `on_reject`
(retry/retrace/fallback) bounded by `RuntimeLimits` and threads the criticism into the re-run.

---

## 4. Config & profile (`from_config`, secrets-vs-yaml)

Observation policy lives in the same application config (one reviewable YAML):

```yaml
observation:
  enabled: true
  bundle_dir: data/observations
  retention_limit: 100
  capture: full
```

Non-secret config lives in YAML (`WorkflowProfile` / `ModelProfile`); secrets stay in env. Swap the
profile to change models/limits/policy **without touching workflow code** (proven by
`test_engine_from_config_swaps_profile`).

```python
profile = WorkflowProfile(
    workflow_type="home_inventory",
    safety=SafetyPolicy(fail_mode="fail_closed",
                        allowed_side_effects=["external_write", "notification"]),  # raw_media_export absent
    limits=RuntimeLimits(
        max_retries=1,
        max_retrace=1,
        max_parallel_children=4,
        max_estimated_usd=0.50,
        max_worker_calls=12,
        max_input_tokens_per_call=80_000,
        max_output_tokens_per_call=4_000,
        max_images_per_call=10,
    ),
    scheduling=SchedulingPolicy(mode="live_latest_only"),
)
engine.register_workflow(home_inventory, profile=profile)
```

Per-run inputs (camera id, location hint) go via `engine.run(..., constraints={...})` or
`goal=WorkflowGoal(..., constraints={...})`; they merge into `plan.constraints` for that run.

---

## 5. GoPro-specific engine features (do NOT reimplement these)

- **Scheduling / backpressure (live streams).** Put `scheduling=SchedulingPolicy(...)` on the
  backend-bound node. The engine holds the backend slot **until the worker actually completes** and
  drops/queues concurrent calls; **cancellation does not release the slot early** (proven by
  `test_backend_slot_held_until_worker_completes_blocks_concurrent_call`). Modes:
  `single_flight_cancel`, `live_latest_only`, `run_latest`, `drop_stale` (`stale_after_s`),
  `coalesce`, `queue`, `drop_not_queue`. Lane = `backend_key` + `max_backend_concurrency`.
  Threading contract: one engine instance is bound to one live asyncio event loop. If a sidecar
  thread needs to submit work, use
  `asyncio.run_coroutine_threadsafe(engine.run(...), engine_loop)`.
- **Evidence refs, never raw bytes in state.** `EvidenceRef(role="contents", uri="frame://cam-1/…",
  media_type="image/jpeg", summary=…)`. Raw pixels stay out of state / trace / checkpoints.
- **Raw-media export = double consent.** A capability exporting raw media declares
  `side_effects=["raw_media_export"]`; the engine permits it only if the **profile** allows it **and**
  the **node** sets `allow_raw_media_export=True` (profile is the ceiling, node opts in). Forbidden
  export is **blocked before the handler runs** and traced (`test_raw_media_export_denied_then_allowed_by_node_flag`).
- **Fail-closed.** `SafetyPolicy(fail_mode="fail_closed")`: a denied side effect / missing capability /
  unsupported node / budget exhaustion **fails loudly + traces**, never silently downgrades.
- **External adapters / processes.** `ExternalAdapterCapability(sink)` for domain writes (idempotency
  key + privacy level on `ExternalWriteRequest`); `ExternalProcessCapability` wraps a CLI/local worker
  with timeout + partial-output salvage. Both run as side-effect-gated `step` nodes.
- **Subscription sidecar workers.** Use `ai_workflow_tools.cli_agents.CliAgentCapability` when the
  sidecar work should run through `claude -p` / `codex exec` with a workspace. `CliAgentRequest`
  stages product-owned `input_assets` under `inputs/`, records `input_fingerprints`, salvages output
  files into `EvidenceRef`s, exposes `new_artifact_count`, and records
  `cost_class="subscription_notional"` for flat-rate lanes. Use `ConsoleLLMClient` for simple
  text-to-JSON report or planner nodes that do not need workspace artifacts.
- **Resumable human clarification.** `.human("ask_location")` pauses (`status="requires_user_input"`)
  or returns a provisional value (`continue_without_answer` + `default_value`); resume by re-running
  once the answer is submitted. Product transport is just an adapter.
- **Uncertainty output.** `WorkflowRunResult.status` includes `requires_user_input` / `partial`;
  fanout returns partial results when some children fail.
- **Subworkflows (recursive).** `.subworkflow("enrich", workflow=enrich_flow)` runs another workflow
  as a capability on the **same executor**, in the parent's usage/budget scope (narrow with
  `budget_usd=`); parent/child appear in the trace.
- **Trace / sidecar.** `format_trace_events(result.trace, usage=result.usage)` renders nodes,
  decisions, key info, timings, metered/notional cost, and artifacts. For live sidecar progress, wire
  `CallbackTraceSink`, `AsyncQueueTraceSink`, or `TeeTraceSink`. The hot capture path stays in your
  app; engine reasoning runs in the sidecar. Your code never imports LangGraph (the executor's
  internal backend); the engine never imports your transport.

---

## 6. Migration recipe

1. Keep your domain functions (frame select, VLM inspect, extract, dedupe, write) — register each as a
   capability with a `CapabilitySpec` (schema + side-effect class + timeout + cost).
2. Turn each routing `if` into a **branch decider** returning a label; each quality check into an
   **evaluator** returning accept/reject + criticism.
3. Express the graph with `WorkflowBuilder` (one `WorkflowDefinition`); delete your orchestration loop,
   your `StateGraph`/`add_conditional_edges`, and manual scheduler calls.
4. Move models/limits/scheduling/side-effects into a YAML profile; load via `from_config`.
5. Replace your run entrypoint with `await engine.run("home_inventory", segment)`.

**Done when** you can delete product orchestration loops and still run from
`WorkflowDefinition + registered capabilities` through `engine.run`. Verify with a `format_trace_events`
dump + the no-product-loop guard.

---

## 7. Reference (import from `ai_workflow_engine`)

`WorkflowBuilder, WorkflowDefinition, WorkflowNode, WorkflowEdge, WorkflowEngine,
WorkflowEngineBuilder, WorkflowPack, WorkflowExecutor, WorkflowRunResult, NodeResult,
BranchDecision, Retry, Retrace, Fallback, SubworkflowRef, EvidenceRef, ExternalWriteRequest,
ExternalWriteResult, ExternalAdapterCapability, ExternalProcessCapability,
HumanClarificationCapability, InMemoryHumanClarificationChannel, AgentCapability, LLMAgentPlanner,
ReplayPlanner, SchedulingPolicy, SafetyPolicy, RuntimeLimits, WorkflowProfile, ModelProfile,
WorkflowGoal, CallbackTraceSink, AsyncQueueTraceSink, TeeTraceSink, format_trace_events`. Tools
import from `ai_workflow_tools.cli_agents`: `CliAgentCapability`, `CliAgentRequest`,
`CliAgentResult`, `ConsoleLLMClient`, `McpServerConfig`, `claude_p`, `codex_exec`. Runnable example:
`ai_workflow_engine/examples.py` (`build_demo_engine`, `InventoryPack`, `run_toy_inventory_pilot`).


---

## What engine-v0.8.1 gives you

`engine-v0.8.1` gives GoPro the full capability set below (older tags
are unsupported — no deltas to track):

- **Discoverable toolset (v0.7.0).** `TOOL_CATALOG` / `render_tool_catalog()` /
  `register_from_catalog(engine, name, **kw)` in `ai_workflow_tools`; presets
  `READ_ONLY/WEB/INVESTIGATION/NO_TOOLS`; tri-state `allowed_tools` with Bash-honest
  side-effect declaration + pre-spawn denial.
- **One-call façade (v0.7.0).** `run_single_llm` / `run_single_step` — trivial calls on the
  real engine, with usage/trace/budget/loud failures inspectable from the returned run wrapper or
  the passed `engine=`.

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
  `exhausted={"label": "escape"}`) — ungated cycles fail validation loudly. **Validation now requires a `describe` entry for EVERY decision label on an `inject_machine=True` branch**: an option the navigator cannot understand fails the build loudly instead of producing an opaque machine card. Zero-LLM routing via
  `engine.register_guard(...)` (traced `decision_policy`). Feed the S1 goal-compiler prompt with
  `render_capability_catalog(engine.registry, allowed)` (side effects outside the allow-list marked
  DENIED, AI-writers NOT-AUTHORABLE); `render_machine_card(definition, node_id)` renders any state
  standalone for dashboards.
- **Flow-as-data + planning.** `engine.run_authored_flow(FlowArtifact(...), payload)` — an LLM emits a
  constrained workflow (steps/branches/gates), validated before compiling, run on the same rails (no
  nested planners/flow-authors — recursion firewall). Bounded recursive planning (`.plan(...,
  max_plan_depth=, max_total_planned_tasks=)`); parallel sub-workflows
  (`register_workflow_capability` + `.fanout(..., capability="run_child")`, failure-isolated).
- **Durable suspend/resume + memory.** Long runs suspend/resume via `result.snapshot` +
  `engine.resume(snapshot, event)` — works **cross-process** for ask-location clarification (payloads
  must be JSON-serializable); `model_dump_json()` round-trips the machine for goal-compiler storage.
  Cross-run memory via the `MemoryStore` seam (`MemoryNamespace`, `MemoryRecord`,
  `InMemoryMemoryStore`; bring your own sqlite for durability).
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
  bundles, and reports the location on `result.observation_bundle_path`. Run artifacts
  (frames, analysis outputs — anything returned in `CapabilityResult.artifacts`) are archived
  INTO the bundle with an `artifacts.json` manifest (source_path -> bundle_path + sha256), so a
  GoPro dashboard resolves archived artifact files exactly like MageQA's — and evidence prunes WITH
  its bundle (one retention policy: `retention_limit`; knobs: `artifacts: copy|off`,
  `artifact_max_bytes`). If GoPro emits custom non-file `EvidenceRef.uri` values, it must also return
  the matching `WorkflowArtifact.path` (or keep the URI equal to that path) so the dashboard can join
  evidence to the manifest. Products never call
  bundle mechanics in the normal path; `engine.run(observation_bundle=)` remains the explicit
  escape hatch (it takes precedence) and `terminal_status=` stays the post-validation hook —
  a raising run archives as failed, and the record can never say completed for a user-visible failure.
- **Prompt files with strict rendering.** Prompts can live as FILES under a locked prompt root:
  `StructuredLLMNode(prompt_ref=PromptRef("gopro/route.txt"), prompt_renderer=engine.prompt_renderer)`
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

**GoPro threading contract:** one `WorkflowExecutor` ↔ one event loop; sidecar threads marshal via
`asyncio.run_coroutine_threadsafe(engine.run(...), engine_loop)`.

## v0.9 (branch) delta — durable waits: 2-minute migration

**Pin stays engine-v0.8.1 until the v0.9.0 tag exists.** When you move:

1. Every `.human(node)` now requires a wait policy:
   `.human("gate", wait_policy=LocalWaitPolicy())` = exactly the old suspend/resume;
   `.human("gate", wait_policy=DurableWaitPolicy(timeout_s=3600), timeout_to="escalate")`
   = durable (declared timeout route is mandatory graph structure).
2. Durable needs a coordinator: `builder.with_wait_coordinator(your_adapter, clock=...)`.
   Validate your adapter with `run_wait_registration_conformance` (full lifecycle:
   register/claim/lease/frozen-acceptance/CAS/failure kinds/cancel/stalled).
3. Your loop owns time (engine never self-fires): `due(now)` → deliver timeouts via
   `engine.deliver_wait_event(wait_id, {"kind": "timeout", ...})`; `stalled(now)` →
   redeliver the accepted event or `engine.cancel_wait(wait_id, reason=...)`.
4. Guarantee wording: at-least-once with idempotent effects (key external writes on
   `context.metadata["wait_idempotency"]`) — never advertise exactly-once.
5. Observation: one logical run = a GROUP of segments; use
   `FileEventSource.read_group(run_id)` / the served viewer (already grouped). Suspended
   groups are never evicted by default; opt-in cap = `ObservationConfig.evict_suspended_after_s`
   (evicts viewer history only — the wait itself stays resumable).
