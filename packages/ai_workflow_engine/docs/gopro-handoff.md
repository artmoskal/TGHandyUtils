# GoPro — AI Workflow Engine Usage Guide

> **FREEZE NOTICE (2026-06-12):** pin tag `engine-v0.1.0` and build a wheel from it — the live
> branch is under authorized breaking changes (state-machine round, lands as `engine-v0.2.0`).
> Read `working-against-the-freeze.md` (same folder) for the pin/upgrade protocol and the exact
> v0.2.0 change list BEFORE wiring anything.

Status: **engine implemented and ready for adoption** (2026-06-12). The `WorkflowDefinition` /
`WorkflowExecutor` / DI layer this doc previously waited on is live and proven (Anki migrated +
live-tested; one `WorkflowExecutor` runs the product-neutral examples). The first sibling tools
package, `ai_workflow_tools`, now ships CLI-agent and console-LLM support for `claude -p` /
`codex exec`.
Current working-tree delta (2026-06-21): after the v0.4.x guide text, the engine also has the T1
memory seam (`AgentMemory`, `FullReplayMemory`, `ImageEvictingMemory`, `MemoryStore`,
`MemoryNamespace(product, tenant, subject, kind)`, `InMemoryMemoryStore`), canonical memory modes
`full_replay` / `image_evicting`, and the non-default memory snapshot/resume determinism test.
Durable/semantic memory, FlowArtifact v1.5, ProcessArtifact/v2, and browser/no-API executors are
still deferred. Consumers should still move tag-to-tag; do not track the live branch implicitly.
Source needs: `/Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md`,
`/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/HOME_INVENTORY_CASE.md`.

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
- Reminder (threading contract): one `WorkflowExecutor` ↔ one event loop; sidecar threads marshal via `asyncio.run_coroutine_threadsafe(engine.run(...), engine_loop)`.

**Also landed 2026-06-12 (later — all additive, no breaking changes):**
- **Flow-as-data**: `await engine.run_authored_flow(FlowArtifact(...), payload)` — an LLM can emit
  a constrained workflow (steps/branches/gates), validated exhaustively before compiling, run on
  the same rails. AI-authored flows cannot contain planners/flow-authors (recursion firewall).
- **Bounded recursive planning**: `.plan(..., max_plan_depth=2, max_total_planned_tasks=32)` —
  planned tasks may themselves be planners, depth pre-set to 1 (flat) unless you opt in; cumulative
  task budget caps total work across all levels.
- **Parallel sub-workflows**: `engine.register_workflow_capability("run_child", "child_flow")` then
  `.fanout(..., capability="run_child")` — child workflows in parallel with failure isolation.
- **Visualizer**: `workflow_to_mermaid(defn, result)` / `save_workflow_html(...)` — the state
  machine as a diagram, with executed-path overlay (status colors, ✓ on taken transitions).

## v0.2.0 migration (tag `engine-v0.2.0` = 8d58f88, 2026-06-12) — upgrade window OPEN

Retag → rebuild wheel → run your suite (719/213/21 green at the tag). Expected work:

1. **Builder-based code (all current GoPro integration): nothing breaks.**
2. Raw-definition construction/introspection only: `WorkflowDefinition.edges` →
   `.transitions`, `WorkflowEdge` → `Transition` (`conditional` → `policy`).
3. Any branch label that closes a loop now REQUIRES a pre-set gate:
   `.branch(..., bounds={"label": N}, exhausted={"label": "escape"})` — ungated cycles fail
   validation loudly by design. If the S1 goal-compiler emits loops, it must emit bounds.

Earmarked for GoPro (your ACK list, all landed): `engine.resume(snapshot, event)` for
ask-location clarification across processes (payloads must be JSON-serializable for the
cross-process path); `register_guard` for deterministic zero-LLM gates;
`model_dump_json()` round-trip for goal-compiler machine storage.

## v0.3.0 delta (additive — upgrading from v0.2.0 needs NO code changes)

Self-describing machine: declare label semantics once (`.branch(..., describe={...})`), let
deciders receive their legal moves + live gate budgets via `inject_machine=True`
(`context.metadata["machine"]`), and feed your flow-author prompts with
`render_capability_catalog(engine.registry, allowed)` instead of hand-maintained tool lists.
`render_machine_card(definition, node_id, state)` is available standalone for debugging and UIs.

### How to use it (GoPro)

```python
# 1) Declare label semantics ONCE in the machine — stop maintaining them in decider prompts.
.branch(
    "evidence_quality_gate",
    {"enough": "extract_items", "ambiguous": "ask_location", "bad": "fallback_or_fail"},
    describe={
        "enough":    "frames clearly show distinct items to inventory",
        "ambiguous": "items visible but location/context unclear — ask the user",
        "bad":       "footage unusable (dark/blurred) — fall back or fail",
    },
    inject_machine=True,   # this node's decider receives its legal moves at call time
)

# 2) The decider reads the card — labels, semantics, LIVE gate budgets — from metadata:
def decide(context, payload):
    card = context.metadata["machine"]
    # e.g. "- retry -> select_evidence — ... [gate: 1 of 2 remaining]"
    prompt = f"{card}\n\nEvidence:\n{payload}\n\nReturn exactly one label."
    ...

# 3) Zero-LLM routing where a predicate suffices (decision_policy='deterministic' in trace):
engine.register_guard("evidence_quality_gate", lambda p: "enough" if p.frames else "bad")

# 4) S1 goal-compiler: feed the author prompt the engine-generated catalog, not a hand list —
#    side effects outside the allow-list are marked (DENIED), AI-writers marked NOT-AUTHORABLE:
from ai_workflow_engine import render_capability_catalog
catalog = render_capability_catalog(engine.registry, allowed_side_effects=allowed)

# 5) Debugging / dashboards: render any state's moves standalone:
from ai_workflow_engine import render_machine_card
print(render_machine_card(definition, "evidence_quality_gate"))
```

Pairing with v0.2.0: when the compiler authors loops, emit `branch_bounds` + `describe` together —
the card then shows navigators exactly how much loop budget remains before the gate trips.

## v0.4.0 delta

Media generation (image/voice) now lives in `ai_workflow_tools.media`: the engine remains
domain/provider-neutral while modality-specific providers live in tool packs. `vision`/image-input
stays in the engine as LLM protocol. Breaking ONLY for direct media imports — none in your current
integration. Install `ai-workflow-tools[media]` if you adopt the media pack.
