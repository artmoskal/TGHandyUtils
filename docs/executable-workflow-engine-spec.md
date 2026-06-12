# Executable Workflow Engine — Binding Spec (Concept · DI/Builder · Acceptance · Build Outline)

Status: **authoritative spec** — this is what we build. Supersedes any "primitive kit" reading.
Author: Claude, 2026-06-08 · Implementer: **Claude** (per Artem)
Binds together: GoPro contract
(`/Users/artemm/PycharmProjects/gopro-streaming/docs/_discussion/2026-06-08-executable-workflow-engine-contract.md`),
`docs/workflow-engine-architecture.md`, `docs/workflow-engine-acceptance-criteria.md`.
Reading rule: if any sentence here conflicts with another doc, **this doc wins** until merged.

---

## 0. ONE SENTENCE (no side-readings)
The engine is an **AI-powered state machine** (executable, DI-wired): the machine provides the
rails — states (nodes), **first-class transitions with declared policies**
(`always`/`decision`/`on_accept`/`on_reject`), and **pre-set gates on every cycle** — while
intelligence (deterministic code, LLMs, humans) only **navigates** (selects among declared
transitions), **authors** (emits validated machine fragments: plans, flows), and **waits**
(suspends durably, resumes on events). A product **declares** a workflow with a builder/DSL and
**registers** capabilities/prompts/schemas/adapters/policies; then `await engine.run(workflow,
input)` executes the **whole** machine — node dispatch, branching, gates, retries, retrace,
fallback, fan-out/gather, scheduling, cancellation, side-effect/privacy/budget enforcement, trace,
**snapshot/suspend/resume**, subworkflows — with **ZERO product-side orchestration loop**.
Values: every navigation decision is a Transition with a declared policy; deterministic-first
(never burn an LLM call where a predicate suffices); every cycle has a pre-set gate; every wait is
resumable; the machine is data (serializable, visualizable, authorable).

It is **not** a folder of helper classes a product wires in its own loop. The primitives already
exist (the bricks); this spec builds **the engine that runs workflows** (the building).

## 1. THE HARD LINE (what makes it an engine, not a kit)
**Engine owns** (product must NOT reimplement): workflow execution, node dispatch, branching,
conditional gates, supervisor/decision loop, retries, retrace, fallback, structured-LLM parse/repair,
fan-out/gather, scheduling/backpressure, cancellation semantics, side-effect enforcement,
privacy/raw-media policy, cost/budget limits, trace, checkpointing, pause/resume, human-clarification
mechanics, subworkflow execution, recursive workflow-as-capability.

**Product owns** (and ONLY this): domain goal, the workflow definition (graph shape/node names),
prompts, domain schemas, validation rubrics, domain tools/capabilities, external adapters, storage &
delivery, product UI/transport.

**Litmus (binding definition of done):**
> Can a product **delete its orchestration loops** and still run each workflow from
> `WorkflowDefinition + RuntimeProfile + registered capabilities/adapters`?

If the answer is not an unconditional **YES** (proven by Test A + Test B below), the engine is
**unfinished** — list which mechanic still lives in product code and move it into the engine.

## 2. CENTERPIECE — DI / builder wiring (the constructor) ← get this right first
The whole value is that **building a new flow is trivial and orchestration-free.** Treat
`WorkflowBuilder` as a **constructor** other flows reuse to assemble flexible flows. This is the exact
target developer experience (the API shape is part of the spec, not a suggestion):

```python
# 1) DECLARE the flow — composable, fluent, typed. No orchestration code.
workflow = (
    WorkflowBuilder("home_inventory")
      .step("select_evidence")
      .branch("evidence_quality_gate", {
          "enough":    "extract_items",
          "ambiguous": "ask_location",
          "bad":       "fallback_or_fail",
      })
      .step("extract_items")
      .evaluate("quality_gate", on_reject=Retrace("select_evidence"))
      .subworkflow("enrich", workflow=enrich_flow)        # a workflow used AS a capability
      .step("write_inventory")
      .build()
)

# 2) WIRE dependencies via DI — products inject; the engine enforces the contract.
engine = WorkflowEngine.from_config("gopro.inventory.yaml")   # profile/limits/policies/sinks
engine.register_capability("select_evidence", SelectEvidenceTool(...))
engine.register_capability("extract_items",  VlmItemExtractor(...))
engine.register_capability("ask_location",   HumanClarification(...))
engine.register_capability("write_inventory",InventoryAdapter(...))

# 3) RUN — engine owns everything from here.
result = await engine.run(workflow, video_segment)
```

The product must **never** have to write:
```python
ev = await runtime.invoke("select_evidence", ...)      # ❌ manual orchestration
if ev.bad: ...
items = await runtime.invoke("extract_items", ...)
if items.low_confidence: retry_or_retrace_somehow()    # ❌ product mini-engine
```

**Builder/DI requirements (all are AC, see §5):**
- **Fluent constructor:** `WorkflowBuilder(name).step/branch/evaluate/fanout/subworkflow/...().build()`
  returns a typed `WorkflowDefinition`. Reusable, composable, no engine edits to add a flow.
- **IoC/DI container:** products inject prompts, schemas, LLM clients/models, Python tools, external
  adapters, validators, QA rubrics, cost limits, trace/checkpoint sinks, storage/delivery — by
  capability name. The engine resolves + wires + enforces (timeouts, side-effect class, budget,
  privacy, model profile) per capability.
- **Flexibility metric (explicit AC):** a brand-new flow (e.g. calendar) is expressible in **≲30
  lines** of builder + `register_capability` calls, with **zero** orchestration code and **zero**
  engine source edits. "Easily used by other flows" = time-to-new-flow is *builder + capabilities
  only*.
- **Config-driven:** `WorkflowEngine.from_config(yaml)` loads profile/limits/policies/model-profiles/
  sinks; runtime policy is configured, never hand-coded in product loops.

## 2b. LAYERED EXTENSIBILITY — the swiss-army contract (Artem, 2026-06-11)
The framework stays **lightweight at the core and infinitely extensible at the edges**. Three
layers, each extended **by addition, never by editing the layer below**:

- **L0 — Core engine (orchestration only).** Graph execution, branching, retry/retrace/replan,
  fan-out, scheduling/cancellation, budgets, side-effect/privacy gates, trace, checkpoints,
  model binding, plan-as-data. Zero domain knowledge. Minimal deps (LangGraph is the hidden
  execution backend; nothing else is sacred). *Litmus: a new scenario shape must be expressible
  with existing node kinds + registered capabilities — if it needs an engine edit, that edit must
  itself be a new generic node kind, not a special case.*
- **L1 — Universal executors (client adapters).** HOW a model/tool is reached, behind ONE socket:
  the `LLMCallable` protocol (and the LangChain-shaped `.invoke` family as a peer, not a
  privilege). Members today/planned: LangChain clients · plain async callables (raw HTTP, e.g.
  Ollama) · **console executors** (non-interactive `claude -p` / `codex exec`) · **no-API
  executors** (e.g. a browser extension driving a logged-in web LLM and returning results).
  *Invariant: parse/repair/pre_parse, metering (incl. `cost_known=false` for subscription/no-API),
  budgets, timeouts, and model binding apply IDENTICALLY through every executor — policy is
  engine-owned, transport is pluggable.*
- **L2 — Domain node sets (packs).** Reusable `WorkflowPack`s for project *families*, installed as
  extras and wired via `register_pack`: media/image generation (incl. reference images + QC),
  voice generation, presentation-builder-from-images-and-scenario, future MCP tool suites.
  Universal *within their domain*, irrelevant outside it — and never required by L0/L1.
- (Products are effectively L3: workflow definitions, prompts, schemas, adapters, delivery.)

**Values addendum:** (a) every new core dependency must justify why it isn't an extra; (b) a new
executor = implement `LLMCallable`, a new domain = ship a pack — neither touches engine source;
(c) heterogeneous executors coexist *per node* in one workflow (strong API model on the planner
step, local weak model on extraction, browser-bridged on a zero-budget step) via G3 model binding;
(d) **contract the mechanics, never the shapes** (Artem, 2026-06-12): universal layers contract
what every case shares — transport, provenance (`EvidenceRef` + fingerprints on BOTH sides of an
episode), validation (`PlanArtifact` + allow-lists), salvage — while per-domain payload shapes
(video jobs, screenshot suites, finding schemas) stay role-tagged refs + product schemas at
prompt level, graduating to an L2 pack only when TWO products share the shape.

## 2b-bis. FRAMEWORK ADOPTION DECISIONS (Artem + claude, 2026-06-12)
Lens: a framework earns a mandatory seat only by adding real functionality (per Artem). Decisions:
- **LangChain-core: KEEP as mandatory dep.** It earns it: parsers, prompt templates, message
  types, model ecosystem. (The earlier "[langchain] extra" backlog item is WON'T-DO.)
- **LangGraph: KEEP** — it IS our state-machine/graph framework, hidden in the executor.
- **Dedicated state-machine framework (transitions, python-statemachine…): NO.** It would
  duplicate LangGraph's slot — two execution semantics for one engine is §2c risk #1 in disguise.
- **Dedicated DI framework (dependency-injector, punq…): NO for now.** Our container binds by
  capability NAME and enforces domain policy (side-effect classes, budgets, schemas, model
  profiles) at resolution — generic DI binds by type and knows none of that; we would wrap it and
  keep all our code. Explicit registration is also load-bearing for traceability. Revisit only if
  we ever need lifecycle scopes/interception that the bespoke ~300-line container can't express.

## 2e. STATE-MACHINISH EVOLUTION — AI navigates, authors, and recurses ON RAILS (Artem, 2026-06-12)

**Concept (validated + implemented):** the engine is a compiled state machine whose transitions AI
may *decide* (branch deciders, evaluator gates — existing) and whose work AI may *author* — now at
two sanctioned levels:

1. **Bounded recursive planning (depth-N, pre-set 1).** A planned task MAY itself be a planner
   when the node opts in (`max_plan_depth >= 2`). Justification for allowing recursion at all:
   real decompositions are hierarchical ("audit site" → per-section sub-plans), and forbidding
   depth pushed that need into awkward hand-written subworkflow nesting. Why it stays safe —
   the multiplication problem is bounded on every axis: per-level `max_tasks`, **cumulative
   `max_total_planned_tasks` across ALL levels** (a counter depth cannot outrun), shared run
   budget/USD caps, child plans re-validated at depth+1 against the SAME allow-lists (loud abort,
   zero partial execution), replan owned by the TOP planner node only, recursion in `fanout`
   execution mode rejected at build time. Trace: `plan:subplan_started/done/partial` +
   per-task events with depth metadata.
2. **Flow-as-data (`FlowArtifact` → `engine.run_authored_flow`).** An LLM emits a constrained
   declarative flow (steps/branches/evaluator gates only); the engine validates EVERYTHING before
   compiling — registered capabilities, allow-listed side effects, earlier-step-only retrace
   targets, resolvable model profiles, `max_nodes` — and runs it through the SAME executor,
   preflight, budgets, and trace as hand-written workflows. **The recursion firewall:** authored
   flows may not contain planner or flow-author capabilities — AI-written things never contain
   AI-writers; recursion exists ONLY through the depth mechanism above, where multiplication is
   counter-bounded.

**Visualization:** `workflow_to_mermaid/html` renders any definition as the state machine it is —
node shapes by kind, labeled transitions, dashed bounded back-edges — and overlays a run result
(status-colored states, check-marked taken transitions). Zero new dependencies.

**ROUND 2 (2026-06-12, implemented — the combined AI/state-machine core, per Artem: "prepare plan
… then implement", breaking changes authorized "do not produce legacy shit"):**

1. **Transitions are first-class.** `Transition(source, target, label, policy, max_traversals,
   on_exhausted)` replaced `WorkflowEdge`; `WorkflowDefinition.transitions` is COMPLETE machine
   data (evaluator accept/reject routes are materialized at construction, idempotently). One
   wiring path + one router convention in the executor replaced the per-kind trio. BREAKING:
   `edges`→`transitions`, `conditional`→`policy`; builder-based code unaffected.
2. **Pre-set gate law (the user's doctrine, generalized).** Every cycle through decision
   transitions MUST be broken by a bounded one: `validate_graph` rejects ungated cycles loudly
   (previously a looping LLM decider spun until the blunt global recursion limit — found live as
   an ungated fallback loop in Anki's machine, now gated). At run time the gate counts
   traversals; exhaustion either fails loudly or takes the DECLARED escape label
   (`.branch(..., bounds={...}, exhausted={...})`), traced as `transition:exhausted`.
3. **Deterministic navigation first-class.** `engine.register_guard(name, fn)`: `fn(payload) ->
   label` routes with zero LLM cost; trace records `decision_policy` per decision.
4. **Durable suspend/resume (register row CLOSED).** Suspended runs return a `MachineSnapshot`
   (complete position: outputs, statuses, routes, ALL loop counters, plan, usage);
   `engine.resume(snapshot_or_json, event)` fast-forwards via recorded routes with ZERO
   re-execution, injects the event into the suspended node's context, runs on live — budgets
   cumulative across halves, cross-process when payloads are JSON-serializable (`to_json()`
   fails loudly otherwise). Proven: double-suspension inside a bounded loop counts traversals
   ACROSS suspensions.
5. **Machine-as-data closed.** Authored flows may declare bounded loops
   (`branch_bounds`/`branch_exhausted` — same gates, same validation); any definition
   JSON-round-trips and runs identically. Viz renders gates (`⟲≤N`), policies, and suspended
   states distinctly.

**ROUND 3 (2026-06-12, implemented — the SELF-DESCRIBING machine; Artem's MCP analogy: "we
should have description of nodes entry and exit gates so AI could navigate states"):**

1. **Transitions self-describe.** `Transition.description` ("take this when …") declared once in
   the machine via `.branch(..., describe={label: text})` — label semantics stop being smeared
   across per-product decider prompts. Evaluator routes auto-describe at materialization.
2. **Machine card.** `render_machine_card(definition, node_id, state)` — one state's legal moves
   as text: labels, declared semantics, targets, and LIVE pre-set gate budgets ("again ⟲ 1 of 2
   remaining" / "EXHAUSTED → done"). Pure, deterministic, render_plan's sibling.
3. **Navigation injection (pre-set OFF).** `inject_machine=True` on any builder verb injects the
   card as `context.metadata["machine"]` for that node's capability call only — deciders see
   their legal moves with live budgets (proven: second loop pass sees the decremented count).
   Default off = bit-identical behavior.
4. **Authoring self-description.** `render_capability_catalog(registry, allowed_side_effects)`
   — MCP-style catalog for flow-author prompts (kind, description, side effects with DENIED
   marks, recursion firewall stated inline as NOT-AUTHORABLE). `FlowNodeSpec.describe` flows
   into authored transitions, so authored machines are card-navigable like hand-written ones.
5. **§2c#5 CLOSED.** Executor god-file split landed first (the fence's condition): handlers +
   exclusive helper clusters live in `nodes/` (one module per kind, functions over the
   executor); executor.py 1956 → 762 lines, zero behavior change, zero test edits.

## 2d. DEFERRED / OUT-OF-SCOPE REGISTER (single source; update when items land or die)
| Item | Context | Trigger to do it |
|---|---|---|
| ~~Media/voice seams → tools lib~~ | **DONE 2026-06-13 (v0.4.0)**: `ai_workflow_tools.media` (image_generation/image_models/voice_generation), shim-free — Anki + container + tests migrated in the same commit; engine `[media]` extra removed (tools has it); L0 purity guard test blocks regression. `vision.py` (image INPUT) stays in core — it is LLM protocol, not generation | — |
| Browser-bridge no-API executor | L1 member, protocol-ready; §2c #3: highest-maintenance member | A concrete paying use case (build LAST) |
| ~~`workflow_capability` adapter~~ | **DONE 2026-06-12**: `engine.register_workflow_capability(name, workflow_id)` — fanout over child workflows with partial-failure isolation | — |
| ~~Durable mid-run resume~~ | **DONE 2026-06-12 (§2e round 2)**: `MachineSnapshot` + `engine.resume` with fast-forward replay, cumulative budgets, cross-process JSON | — |
| Token-level output streaming | Event-level live sinks exist (WP6); token streaming would extend `LLMCallable` | A UI that needs it |
| Image+reference+QC promotion to an L2 pack | Production-proven inside Anki product code | 2nd consumer of the pattern (§2c #4 bar) |
| Presentation-builder pack, MCP tool-suite packs | Named in the L2 vision | When the project materializes |
| ~~Planner depth ≥2~~ | **DONE 2026-06-12 via §2e**: bounded recursion, pre-set `max_plan_depth=1`, cumulative task budget | — |
| `ANKI_*` env alias bridge sunset | config-architecture CFG-8: transition bridge, must not leak into engine | Pi `.env` migrated by aws_deploy |
| `test.sh` `-k "a or b"` word-split bug | Workaround = paths/single tokens, documented everywhere | Next time someone touches test.sh |
| ~~Executor god-file split~~ | **DONE 2026-06-12 (v0.3.0 round, honored the fence: split FIRST, then features)**: `nodes/` package, executor.py 1956→762 | — |

## 2c. RISK WATCHLIST — where this becomes a mess if discipline slips (reviewed 2026-06-12)
The architecture's real threats are **discipline threats**, not design flaws. Each has a named
fence; violating a fence requires a deliberate, user-approved decision — never drift:

1. **Node-kind proliferation** (the inner-platform cliff). 8 kinds exist (step/branch/fanout/
   evaluate/subworkflow/human/planner). Domain needs become *capabilities or packs*, never kinds;
   a new kind must be generic across products. Fence: §2b rule + product-neutrality guard test.
2. **Planner depth creep.** AMENDED 2026-06-12 (Artem, explicit §2e decision): bounded recursion
   is now SUPPORTED but PRE-SET OFF — `max_plan_depth=1` by default; a node opts into depth N
   explicitly and the cumulative `max_total_planned_tasks` caps total AI-authored work across ALL
   levels, so depth can multiply intent but never work. UNBOUNDED recursion remains forbidden;
   AI-authored flows may not contain AI-writers (§2e). Fence: depth-aware validation + cumulative
   budget + tests.
3. **No-API/browser executor = highest-maintenance member of L1.** Brittle (UI drift), slow,
   ToS-gray. The protocol contains its blast radius (it is just one `LLMCallable`,
   `cost_known=false`, aggressive timeouts), but the adapter itself will be a maintenance pet.
   Build it LAST, only against a concrete paying use case.
4. **Protocol fattening.** `LLMRequest/LLMResponse` stayed minimal on purpose (the anti-LangChain
   bet). Tool-calling/multi-turn entered ONLY when a real consumer (MageQA, engine-completion
   spec WP1) needed it — that is the bar for every future field: a named consumer, additive-only,
   single-turn behavior bit-identical. Speculative fields are rejected.
5. **Executor god-file.** `executor.py` (~1.5k lines) trends monolithic; a mechanical
   handlers-per-module split is sanctioned cleanup (cosmetic, no behavior change) when it next
   gets a feature.
6. **Stringly-typed state paths.** `input_key`/`node_outputs` conventions are fine at ≤30-node
   workflows (largest real: Anki, 26). If a product hits real pain, design a typed mapping —
   do not bolt expressions into YAML (that is risk #1 wearing a costume).
7. **Budget-knob proliferation.** Every new cap must map to a proven operational need (the WP3
   matrix mirrors MageQA's production caps) — never speculative knobs. All caps Optional/None.
8. **When NOT to use the engine.** A trivial one-LLM-call script with no retry/budget/trace/
   branching/scheduling need should stay a script. Forcing everything through the engine is how
   frameworks get bypassed and shadow paths form.

## 3. FIRST-CLASS CONCEPTS (every one is executable; none are documentation-only labels)
1. **`WorkflowDefinition`** (+ `WorkflowBuilder` DSL): nodes, edges, branches, conditions,
   subworkflows, retry/retrace/fallback policy, evaluator gates, capability binding, scheduling
   policy, side-effect requirements, budget/limit requirements.
2. **Node harness** — built-in **executable** node types (see §4).
3. **`WorkflowExecutor`** — runs a `WorkflowDefinition` + `RuntimeProfile` end-to-end: node
   lifecycle, input/output mapping, state passing, structured-output validation, branch selection,
   errors, cancellation, limits, trace, checkpointing.
4. **Capability contract** — each capability carries: input schema, output schema, timeout, retry
   policy, model profile, cost class, concurrency lane, side-effect class, privacy/raw-media export
   policy, evidence refs consumed/produced, idempotency-key strategy, trace fields, failure behavior.
   A bare callable is **not** sufficient.
5. **DI/IoC container** — §2.
6. **Composable subflows** — a workflow is usable **as a capability** inside another workflow;
   recursive composition is **core**, not future.

## 4. NODE TYPES (each must be runnable by the executor + have a test)
deterministic Python step · typed capability/tool step · structured-LLM step · AI decision/gate ·
branch · fan-out/gather · evaluator/QA gate · retry · retrace · fallback · subworkflow · recursive
workflow-as-capability · human clarification · external process/script/tool · adapter call ·
media/image/voice node. **Each is executed by the engine; none is a label the product implements.**

## 5. FULL ACCEPTANCE CRITERIA — executable gates (this is the anti-drift)
Every gate below is a **test that must exist and pass**, not prose. No proving test ⇒ not done.

**Binary DoD (the master gate):** the §1 litmus question answers **YES**, proven by A + B.

- **A. Same-executor proof.** ONE `WorkflowExecutor` runs **all four** workloads — Anki, GoPro
  inventory toy, MageQA site-audit toy, calendar-builder toy — from a `WorkflowDefinition` +
  registered capabilities. No workload subclasses/forks the executor.
- **B. No-manual-product-loop proof (static + runtime).** A static/AST check **fails** if any product
  pack contains orchestration: `StateGraph`/`add_conditional_edges`/hand-rolled retry/branch/
  fan-out/scheduler/retrace loops or side-effect enforcement. Those mechanics are engine-only.
- **C. Subworkflow-as-capability.** Define a workflow, register it as a capability, call it from
  another; trace shows parent→child, inputs, outputs, failure boundary.
- **D. Policy enforcement.** A node attempting a forbidden external call / raw-media export is
  **rejected before invocation**; no side effect; trace records the decision; fail mode applied.
- **E. Retry/retrace/fallback.** A fake LLM node returns malformed/low-confidence output; engine
  validates → repair/retry/retrace/fallback per policy; **product writes no loop**; trace shows every
  decision.
- **F. Scheduling/cancellation.** Long-running fake call: `single_flight` blocks concurrent backend
  calls; **cancellation does not release the backend slot until the underlying call actually ends**;
  manual-priority / queue-latest / drop-stale are traceable.
- **G. Human clarification.** Ambiguous state → engine pauses or returns provisional per profile;
  clarification is a runtime event; product transport is just an adapter; resume/checkpoint explicit.
- **H. Builder/DI flexibility.** Adding the calendar flow = builder + `register_capability` only
  (≲30 lines), zero engine edits, zero orchestration code (enforced by B).
- **I. Node-type executability.** Each §4 node type has a test proving the executor runs it.
- Plus the standing enforcement from `docs/workflow-engine-acceptance-criteria.md`: DOD-1 (no stubs),
  DOD-6 (flag-don't-downgrade, never silently simplify), DOD-10 (no interface-only), DOD-12 (no silent
  fallback), and per-capability uniform AC.

## 6. ANTI-DRIFT / ANTI-SIMPLIFICATION (explicit — so it cannot be re-shrunk)
These substitutes are **rejected** and any one of them = the work is **not done**:
- "We have interfaces, the product can wire them."
- "The toy examples call the same primitives."
- "Anki works, so the engine is done."
- "Scheduler/evaluator/trace exist as separate classes."
- "A product can write a loop around the runtime."
- "Unsupported features warn and then a simple mode runs silently."

Enforcement that makes drift impossible to hide:
- The **binary DoD question** must be answered YES with A+B green.
- The **static no-product-loop check (B)** fails the build if a product hand-rolls orchestration.
- **DOD-6:** if the implementer thinks a piece is unneeded/infeasible/should be simplified, **stop and
  flag Artem** — do not ship a weaker version.
- **DOD-9 self-report:** each gate A–I links its proving test; a gate with no test = not done.
- "Every node type executable" (I) kills documentation-only labels.

## 7. BUILD OUTLINE (implementation order — Claude)
Reuse the existing real primitives (scheduler, evaluator, agent, fan-out, evidence, fail modes, trace,
usage, config) as the executor's **internals**. Build the orchestration-ownership layer on top.
- **E1 — `WorkflowDefinition` + `WorkflowBuilder` DSL + node-type registry.** The constructor (§2).
  Gate: build a definition for all four flows in code (no execution yet); types validate.
- **E2 — `WorkflowExecutor`.** Owns dispatch/branch/retry/retrace/fallback/structured-validation/
  errors/cancellation/limits/trace/checkpoint, driving the existing primitives. May use LangGraph
  **internally** as the backend; the product never sees it. Gate: Tests E, F, I.
- **E3 — DI container.** `from_config` + `register_capability` + capability-contract enforcement
  (timeout/side-effect/budget/privacy/model-profile). Gate: Tests D, H.
- **E4 — Subworkflow-as-capability (recursive).** Gate: Test C.
- **E5 — Migrate Anki** onto `WorkflowDefinition + Executor`; **delete the product LangGraph
  orchestration** in `anki_generation_graph.py`. Gate: parity + AC-01..AC-23 green.
- **E6 — Migrate the 3 toys** (calendar/site-audit/inventory) onto the same executor; add the
  no-product-loop static check (B) + same-executor proof (A). Gate: A + B green; full suite green.
- **E7 — GoPro & MageQA handoff packages** (concept-map + migration recipe + acceptance fixtures),
  verified against the real repos.
- Each phase: commit + DOD-9 self-report.

## 8. Reused vs new vs removed
- **Reused (become executor internals):** scheduler, evaluator, agent capability, fan-out, evidence
  refs, fail modes, trace sink, usage/budget, config loader, human-clarification capability.
- **New:** `WorkflowDefinition`, `WorkflowBuilder`, `WorkflowExecutor`, DI container, subflow-as-
  capability, the static no-product-loop check.
- **Removed:** product-owned LangGraph orchestration (Anki's `add_conditional_edges`/`_route_*`).

## 9. Open — confirm before I implement
1. **LangGraph:** OK for the engine to use LangGraph **internally** inside `WorkflowExecutor` (product
   only ever sees `WorkflowBuilder`)? Recommended: yes — reuse, but hidden behind the engine.
2. **Implementer = Claude** (per your note) — confirm, and confirm I start at E1 after your review.
3. Any builder-API naming preferences (`WorkflowBuilder`/`WorkflowEngine.from_config`/`register_capability`)
   before I lock the surface that everything else depends on.
