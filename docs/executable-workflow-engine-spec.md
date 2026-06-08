# Executable Workflow Engine — Binding Spec (Concept · DI/Builder · Acceptance · Build Outline)

Status: **authoritative spec** — this is what we build. Supersedes any "primitive kit" reading.
Author: Claude, 2026-06-08 · Implementer: **Claude** (per Artem)
Binds together: GoPro contract
(`/Users/artemm/PycharmProjects/gopro-streaming/docs/_discussion/2026-06-08-executable-workflow-engine-contract.md`),
`docs/workflow-engine-architecture.md`, `docs/workflow-engine-acceptance-criteria.md`.
Reading rule: if any sentence here conflicts with another doc, **this doc wins** until merged.

---

## 0. ONE SENTENCE (no side-readings)
The engine is an **executable, DI-wired AI-workflow runtime**: a product **declares** a workflow with
a builder/DSL and **registers** capabilities/prompts/schemas/adapters/policies; then
`await engine.run(workflow, input)` executes the **whole** workflow — node dispatch, branching, gates,
retries, retrace, fallback, fan-out/gather, scheduling, cancellation, side-effect/privacy/budget
enforcement, trace, checkpoint, pause/resume, subworkflows — with **ZERO product-side orchestration
loop**.

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
