# Executable Workflow Engine — Binding Spec

Status: **authoritative spec** — this is what we build. Supersedes any "primitive kit" reading.
Author: Claude, 2026-06-08 · Cleanup pass: 2026-06-20 (renumbered to reading order; identity fixed;
EXISTS-vs-INTENDED marked; implementation history moved to Appendix A).
Reading rule: if any sentence here conflicts with another doc, **this doc wins** until merged.
Sections run in reading order §0→§13; **Appendix A** holds implementation history/changelog.
**Status legend used throughout:** **[BUILT]** shipped + tested · **[PARTIAL]** real but not unified/
complete · **[INTENDED]** designed, not built · **[FUTURE / discuss-later]** roadmap, needs its own
spec + explicit approval. See the consolidated table in §13.
**Doc jurisdiction:** this file is the permanent architecture source. Package/root READMEs and
`ARCHITECTURE.md` are summaries. `packages/ai_workflow_engine/docs/engine-completion-spec.md` is
historical implementation/completion provenance. `docs/_discussion/2026-06-19-engine-memory-design.md`
is historical/T2 memory design workspace material; accepted T1 memory decisions are merged here.
GoPro/MageQA handoffs are consumer recipes. If any of those conflict with this file, this file wins.

---

## 0. IDENTITY (one paragraph, no side-readings)
The engine is a **framework for universal AI processing**: any product expresses its AI workflow —
sequential, branching, planned, AI-authored, agentic, multi-modal, memory-backed — by **declaring**
a graph of capabilities and **registering** prompts/schemas/clients/policies; then
`await engine.run(workflow, input)` runs the **whole** thing with **ZERO product-side orchestration
loop**. The framework owns every processing mechanic (node dispatch, branching, gates, retries,
retrace, fallback, fan-out/gather, scheduling, cancellation, side-effect/privacy/budget enforcement,
trace, snapshot/suspend/resume, subworkflows, and — when enabled — memory); the product owns only
domain shapes (goal, graph shape, prompts, schemas, rubrics, adapters, delivery). Intelligence
(deterministic code, LLMs, humans) only **navigates** (selects among declared transitions),
**authors** (emits validated machine fragments: plans, flows), **waits** (suspends durably, resumes
on events), and — when enabled — **remembers** (bounded, non-authoritative context). **Internally it
compiles to a bounded, deterministic state machine (LangGraph, hidden) — that is the engine room, not
the identity; swapping the backend would not change what the framework is.**
Values: deterministic-first (never burn an LLM call where a predicate suffices); every cycle has a
pre-set gate; every wait is resumable; the machine is data (serializable, visualizable, authorable);
memory is input, never control.

It is **not** a folder of helper classes a product wires in its own loop. The primitives already
exist (the bricks); this spec builds **the framework that runs workflows** (the building).

**North-star scale requirement:** the same engine must cover the whole complexity gradient — a
one-step Todoist reminder, Anki card generation, GoPro arbitrary video-processing / pipeline
planning, MageQA QA-session planning, and QA-plan authoring from smoke-test results. Simple cases
must stay simple — a single `step` or `branch` with registered capabilities must not pay for planner,
memory, agent, media, or scheduler machinery it did not opt into. Complex cases scale by adding
declared mechanics, not by forking the engine or rebuilding product-side orchestration.

**Architecture finding (Artem + codex, 2026-06-19):** "core" means **domain-neutral**, not
artificially tiny. Complexity belongs in the engine when it is universal workflow machinery
(workflow memory, artifact/evidence refs, evaluator/adjudicator loops, scheduling, human approval,
model profiles, replay, capability registration, policy gates, secret/env resolution) — pluggable,
opt-in engine services. TTS/STT, image/video, browser/CLI agents, OCR, VLM analysis are reusable
modality packs. Todoist/Anki/GoPro/MageQA are domain packs. Product code assembles domain workflows;
it does not rebuild universal mechanics.

**Memory clarification [BUILT/PARTIAL — see §12/§13]:** conversation memory is only one consumer.
The engine now ships the T1 bounded-agent memory seam: `AgentMemory`,
`FullReplayMemory` (default full prompt replay), `ImageEvictingMemory` (keeps recent image-bearing
turns and replaces evicted images with auditable evidence/fingerprint markers), plus the
`MemoryStore` contract and deterministic `InMemoryMemoryStore`. The public store scope is
`MemoryNamespace(product, tenant, subject, kind)`: `tenant` is the hard isolation boundary, `subject`
is the product-owned target/project/run-family, and `kind` is the record family. Canonical memory mode
strings are exactly `full_replay` and `image_evicting`; unknown names and alias-shaped config fail loudly.
Memory is input, never control: the S0 replay test proves an `image_evicting` run snapshots and
resumes on a fresh engine without re-running the agent/LLM/tool path. Broader workflow/project
memory remains deferred: `WindowedMemory`, `StructuredStateMemory`, `CompactingMemory`, durable T2
stores, semantic search/LangGraphStore adapters, and planner/subworkflow memory scopes require a
named consumer, replay/privacy/budget proof, and explicit approval before implementation.

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

If the answer is not an unconditional **YES** (proven by Test A + Test B in §9), the engine is
**unfinished** — list which mechanic still lives in product code and move it into the engine.
[PARTIAL status note — Anki + reminder prove zero product orchestration for live consumers; the
master A+B gate remains open until §9 Test A all-four-workload same-executor proof and §9 Test B
static/runtime no-product-loop proof are cited here.]

## 1a. LOAD-BEARING SEAMS AND CONTRACT GUARDS
The engine stays universal only if its important seams are both narrow and enforced. The rule is:
every load-bearing seam gets a declared contract and one fail-build guard. Do **not** replace this
with pervasive style linting, broad product-code policing, a mandatory base class tree, or a giant
unified invocation object.

External seams, product -> engine:

1. **One provider door.** Model/provider clients are constructed only in sanctioned provider
   construction modules. Today the allow-list is:
   - `services/llm_factory.py` for product LLM/chat and raw OpenAI clients used by shared product
     services;
   - `packages/ai_workflow_tools/ai_workflow_tools/media/image_generation.py` for the reusable image
     provider adapter.

   A new provider-construction module is an explicit reviewed decision. Product workflow code must
   not create its own `OpenAI`, `AsyncOpenAI`, `ChatOpenAI`, Gemini, ElevenLabs, browser, or CLI
   provider client in a way that bypasses model/profile selection, usage, budget, timeout, trace, and
   observability. This guard is intentionally a named allow-list, not a vague "chat vs non-chat"
   distinction, because the same SDK client class can serve several APIs.

2. **No product orchestration loop.** Product packs declare `WorkflowDefinition`s and register
   capabilities. They do not implement their own `StateGraph`, conditional edges, runtime-invoke
   retry loops, fan-out schedulers, retrace loops, or side-effect/budget enforcement around the
   engine. The existing no-product-loop guard is the template; adopter repos must run the same kind of
   guard on their workflow packs.

3. **Declared run-state statuses.** Engine results and adopter projections must use the engine status
   vocabulary or an explicit product-domain enum with a deterministic mapping from engine statuses.
   Capability-level statuses are `accepted`, `rejected`, `partial`, and `failed`; workflow-level
   statuses include `completed`, `partial`, `failed`, and `requires_user_input`. Adopter dashboards,
   APIs, and orchestrator projections must not invent ambiguous progress states such as `ok`, `done`,
   `empty`, or `hollow` unless those are product-domain labels mapped from the engine result. This
   guard is not redundant with runtime normalization: the runtime can normalize only data that enters
   the engine, while product projections can drift outside it.

Internal seams:

1. **Engine does not import products.** `ai_workflow_engine` remains product-neutral and must not
   import TGHandyUtils, Anki, MageQA, GoPro, Telegram, browser-dashboard, or other product packages.
2. **Executor and node handlers do not depend on each other cyclically.** The executor exposes a
   narrow node-service protocol; node handlers depend on that protocol, not executor internals. This
   guard is enforced after the `NodeExecutionServices` refactor lands.
3. **Injected machine context is complete.** If a branch/decision capability receives legal routes via
   `inject_machine=True`, every legal output label must have a human-readable `describe` entry.
   Missing descriptions fail workflow validation and name the undescribed labels. This is scoped to
   injected-machine branches for the hardening wave; expanding it to every branch requires a separate
   reviewed decision.

Guard ownership:

- the binding rules live in this spec;
- the engine package may ship reusable guard helpers/templates and canonical enums;
- each adopter repo must run the relevant guards on its own packs/projections, because the engine repo
  cannot prove external product code never bypasses the framework.

## 2. CENTERPIECE — DI / builder wiring (the constructor)
The whole value is that **building a new flow is trivial and orchestration-free.** Treat
`WorkflowBuilder` as a **constructor** other flows reuse. This is the exact target developer
experience (the API shape is part of the spec, not a suggestion): [BUILT]

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

**Builder/DI requirements (all are AC, see §9):**
- **Fluent constructor:** `WorkflowBuilder(name).step/branch/evaluate/fanout/subworkflow/...().build()`
  returns a typed `WorkflowDefinition`. Reusable, composable, no engine edits to add a flow.
- **IoC/DI container:** products inject prompts, schemas, LLM clients/models, Python tools, external
  adapters, validators, QA rubrics, cost limits, trace/checkpoint sinks, storage/delivery — by
  capability name. The engine resolves + wires + enforces (timeouts, side-effect class, budget,
  privacy, model profile) per capability.
- **Flexibility metric (explicit AC):** a brand-new flow (e.g. calendar) is expressible in **≲30
  lines** of builder + `register_capability` calls, zero orchestration code, zero engine source edits.
- **Config-driven:** `WorkflowEngine.from_config(yaml)` loads profile/limits/policies/model-profiles/
  sinks; runtime policy is configured, never hand-coded in product loops.

## 3. LAYERED EXTENSIBILITY — the swiss-army contract (Artem, 2026-06-11)
The framework stays **domain-neutral at the core and infinitely extensible at the edges**. Layers are
extended **by addition, never by editing the layer below**:

- **L0 — Core engine kernel + universal services.** Graph execution, branching, retry/retrace/replan,
  fan-out, scheduling/cancellation, budgets, side-effect/privacy gates, trace, checkpoints, model
  binding, plan-as-data, replay, human approval, evaluator/adjudicator mechanics, capability registry,
  policy/secrets plumbing, artifact/evidence references, and the T1 workflow-memory seam.
  **[BUILT/PARTIAL for L0: `AgentMemory`, `FullReplayMemory`, `ImageEvictingMemory`,
  `MemoryStore`, `MemoryNamespace(product, tenant, subject, kind)`, `InMemoryMemoryStore`;
  durable/semantic/project memory policies remain deferred.]**
  The evidence-ref machinery exists today via `EvidenceRef`/`WorkflowArtifact`;
  product/storage-specific artifact stores remain outside core.
  Zero domain knowledge. Minimal deps (LangGraph hidden; nothing else sacred).
  *Litmus: a new scenario shape must be expressible with existing node kinds, universal services, and
  registered capabilities — if it needs an engine edit, that edit must itself be a new generic
  mechanic, not a special case.*
- **L1 — Universal executors (client adapters).** HOW a model/tool is reached, behind ONE socket: the
  `LLMCallable` protocol (and the LangChain-shaped `.invoke` family as a peer, not a privilege).
  Members today/planned: LangChain clients · plain async callables (raw HTTP, e.g. Ollama) · console
  executors (`claude -p` / `codex exec`) · no-API executors (browser-bridged) [FUTURE]. *Invariant:
  parse/repair/pre_parse, metering (incl. `cost_known=false`), budgets, timeouts, model binding apply
  IDENTICALLY through every executor — policy engine-owned, transport pluggable.*
- **L2 — Modality/tool packs.** Reusable but non-mandatory: CLI/browser agents, MCP tool suites,
  TTS/STT, image generation, video producer/analyzer, OCR, VLM analysis, presentation building.
  Universal within a modality, irrelevant outside it, never required by L0/L1.
- **L3 — Domain packs.** Todoist, Anki, GoPro, MageQA, etc. — workflow definitions, prompts, rubrics,
  schemas, adapters on top of L0–L2.
- (Products are effectively L4: deployment, UI/transport, account config, delivery.)

**Values addendum:** (a) every new core dependency must justify why it isn't an extra; (b) a new
executor = implement `LLMCallable`, a new domain = ship a pack — neither touches engine source;
(c) heterogeneous executors coexist *per node* in one workflow via model binding; (d) **contract the
mechanics, never the shapes** — universal layers contract what every case shares (transport,
provenance via `EvidenceRef` + fingerprints, validation via `PlanArtifact` + allow-lists, salvage)
while per-domain payload shapes stay role-tagged refs + product schemas, graduating to an L2 pack
only when TWO products share the shape; (e) complexity is pay-as-you-use — advanced mechanics opt-in
and composable so the simple Todoist path and the deep GoPro/MageQA agent paths share one engine.

## 4. FRAMEWORK ADOPTION DECISIONS (Artem + claude, 2026-06-12)
A framework earns a mandatory seat only by adding real functionality. Decisions:
- **LangChain-core: KEEP as mandatory dep.** Earns it: parsers, prompt templates, message types,
  model ecosystem. (The earlier "[langchain] extra" item is WON'T-DO.)
- **LangGraph: KEEP** — the hidden execution/graph backend inside the executor. (It is a *backend*,
  not the framework's identity — see §0.)
- **Dedicated state-machine framework (transitions, python-statemachine…): NO.** Would duplicate
  LangGraph's slot — two execution semantics for one engine is §11 risk #1 in disguise.
- **Dedicated DI framework (dependency-injector, punq…): NO for now.** Our container binds by
  capability NAME and enforces domain policy (side-effect classes, budgets, schemas, model profiles)
  at resolution — generic DI binds by type and knows none of that. Revisit only if we need lifecycle
  scopes/interception the bespoke ~300-line container can't express.

## 5. UNIVERSAL CAPABILITY / WORKER CONTRACT — the anti-"LLM-node-DAG" line
**Status: [PARTIAL — principle binding; no big envelope rewrite].** The universal contract is a
semantic contract enforced by the engine, not one physical request class. Today it is spelled as
`CapabilityContext` + payload, `LLMRequest`, `CliAgentRequest`, and human/subworkflow adapters —
real and working, with different transport-specific DTOs. **Near-term rule (binding): do NOT create
a second "agent node" abstraction that bypasses capabilities; tighten existing capability seams
toward this contract.** A literal merge of all invocation DTOs is not planned.

The main actor is the **engine/executor**, not an LLM node. An LLM call is one possible worker behind
a capability; so are deterministic Python functions, local/API models, `claude -p`/`codex exec`,
browser/CLI agents, media processors, human clarification, external scripts, and whole subworkflows.
The engine routes any of them through one normalized contract, then owns validation, retry/retrace/
fallback, budgets, trace, policy, artifacts, memory scope, and state transitions around them.

**Why this exists:** without a universal worker contract the system drifts into a simplistic graph of
prompt calls, and every complex product rebuilds missing orchestration locally. The contract keeps
simple cases cheap (Todoist ignores memory/artifacts/agents) while complex cases plug in (GoPro,
MageQA opt into them without a second runtime).

**Boundary of responsibility:**
- **Engine owns orchestration and enforcement** — chooses next node, builds invocation context,
  validates schemas + branch labels, checks side-effect/privacy/model-profile/budget policy before
  invocation, records trace/usage/artifact metadata, handles retry/retrace/fallback, manages
  scheduling/cancellation, persists checkpoints/snapshots, resumes suspended runs.
- **Worker owns one bounded unit of work** — consumes declared payload/context, uses only declared
  tools/side effects, returns a normalized result, emits no hidden control loop. Internal provider
  retries for transport noise are OK; semantic retry/replan/retrace/branch-repair/fallback are
  engine-owned and traceable.
- **Products own shapes and adapters** — domain payload schemas, prompts, rubrics, workflow
  definitions, storage/delivery adapters, which capabilities are registered. They do not hand-roll
  node-to-node orchestration.

**Physical shape decision (binding, Artem + codex + Claude, 2026-06-20):**
- Keep the engine-level seam as `CapabilitySpec` + `CapabilityContext`/payload +
  `CapabilityResult`, with policy/trace/budget/artifact/memory enforcement around every call.
- Keep transport-specific request DTOs where they are useful (`LLMRequest`, `CliAgentRequest`, media
  requests, human/subworkflow adapters). They may be implemented as normal classes and helpers
  inside their packages.
- Do **not** build a central `WorkerInvocation` mega-type or an inheritance tree such as
  `WorkerInvocation -> LLMInvocation -> AgentInvocation -> CliAgentInvocation`. The axes are
  cross-cutting rather than tree-shaped: text/vision, API/CLI/local/browser transport, workspace,
  MCP/tools, artifacts, human suspend/resume, subworkflow execution, memory, streaming, side effects,
  and cost class combine in many ways. A central tree would force engine edits for new modalities
  and expose irrelevant fields to workers.
- Prefer composition: capability specs, policy objects, memory/artifact declarations, adapter-local
  DTOs, and reusable helper functions/mixins. New worker families are added by registering
  capabilities/clients/packs, not by editing a core taxonomy.

**Normalized invocation envelope (semantic contract, not a physical class):** task/objective + node
id; typed input payload + explicit input mapping (`input_key`/future data map); input
artifacts/evidence as byte-free `EvidenceRef`/`ImageInput` handles with fingerprints (never raw
bytes in state/trace/checkpoint); output schema + required artifact/evidence expectations; legal
branch labels / evaluator routes / machine card when the worker may navigate; allowed tools,
side-effect classes, raw-media/export policy, secrets/env scope, MCP/tool-pack config; budget,
timeout, concurrency lane, cancellation policy, model profile, cost class; retry/evaluator/retrace
policy chosen by the workflow not the worker; trace/run/checkpoint ids and declared workflow-memory
read/write scope where a built memory policy supports it.

**Normalized result envelope:** every worker result is converted to `CapabilityResult` semantics
(`accepted`/`rejected`/`partial`/`failed`) or a workflow-level suspension (`requires_user_input`). It
may carry structured output, a branch label, evaluator criticism, usage/cost, `WorkflowArtifact`/
`EvidenceRef` refs, `new_artifact_count`, trace metadata, and an error/failure class with
retryability. Raw provider text, CLI stdout, browser files, media bytes are implementation details
until parsed/salvaged into this envelope.

**Static vs dynamic:**
- **Static/declarative:** workflow graph, transitions, cycle bounds, node kind, capability binding,
  capability specs, model profiles, prompts/templates, schemas, rubrics, allowed side effects,
  secrets policy, memory-scope declarations, artifact expectations.
- **Dynamic/run-time:** run payload/goal, branch labels chosen, evaluator decisions, plan artifacts,
  authored `FlowArtifact`s, worker outputs, evidence/artifact refs, usage/cost, trace events, live
  memory reads, retry history, snapshot/resume state.

**Prompt/executor rule:** prompt files are one worker-adapter input. `PromptTemplateLoader` loads
templates; structured nodes use LangChain-style `{var}` formatting; CLI workers receive rendered task
envelopes. The architecture must not depend on prompts being Jinja, API-only, chat-only, or even
LLM-backed.

**Validation / retry law:** the engine validates before and after invocation. Pre: capability exists,
input schema parses, side effects + model profile allowed, artifact inputs are refs not bytes,
budgets/worker-call caps/timeouts/lanes permit the call, node is legal in current state. Post: output
schema parses, branch label declared, evaluator result valid, required artifacts present, raw-byte
guards hold, status maps to a declared route. Failure is loud and traced; no worker silently
downgrades.

**Implementation hygiene guardrails (2026-06-20 review follow-ups):**
- Probe/fallback helpers may recover only from expected narrow validation misses. Arbitrary
  exceptions must surface. If a fallback is taken for a shape that looked like a declared plan,
  image, or evidence artifact, preserve the validation reason in warning/trace/result metadata.
- Coercion that can change control flow, such as `PlanArtifact` parsing, must not collapse
  malformed input to anonymous `None`; the caller needs a reasoned failure path.
- Prompt modes are mutually exclusive and loud: either one `prompt_template` or paired
  `static_prompt_template`/`dynamic_prompt_template`, never both and never only half.
- Multimodal message assembly must preserve existing structured content-part lists; cleanup may
  remove redundant conditionals, but must not stringify valid list content.

**Sequencing guardrail for memory + architecture cleanup (2026-06-20):** completed for the T1 memory
slice. The prerequisite fail-loud hygiene items landed, node handlers now use neutral runtime-state
keys instead of executor internals, S0 proved non-default memory does not own replay control, and
the minimal memory seam landed. `WorkflowDefinition.validate_graph` and
`build_definition_from_artifact` were decomposed before larger FlowArtifact v1.5/process-authoring
work. `_apply_eval` remains a deferred complexity cleanup to do when evaluator behavior is next
touched. Root app coupling cleanup is real debt but does not block T1 memory unless future memory
work starts touching product composition.

**Worker families covered under one contract:** deterministic Python capabilities · `StructuredLLMNode`/
`StructuredVisionLLMNode` over `LLMCallable` · local/weak models (Qwen/Ollama via async callables) ·
strong API models · console workers (`claude -p`, `codex exec`) via `ConsoleLLMClient` · bounded
CLI/browser agent episodes via `CliAgentCapability` + MCP/tool packs · external process/script
capabilities · media/voice/image/video/OCR/VLM modality-pack workers · human clarification ·
subworkflows registered as capabilities.

**Replacement acceptance scenarios:** (1) a structured-JSON-decision node swaps API↔local↔console by
changing registration/profiles, not control logic; (2) an undeclared branch label / malformed JSON /
forbidden side effect / missing artifact / raw bytes in state fails loudly and follows declared
policy; (3) a simple Todoist flow runs with no memory/artifacts/planner/agent/scheduler code beyond
registration; (4) a GoPro/MageQA flow combines planners, fan-out, bounded agents, evidence refs,
memory, subworkflows on the same executor + result envelope.

## 6. AI AUTHORING & RECURSION ON RAILS (flow-as-data + bounded planning)
The engine's transitions AI may *decide* (branch deciders, evaluator gates) and its work AI may
*author*, at two sanctioned levels — both bounded:

1. **Bounded recursive planning (depth-N, pre-set 1). [BUILT]** A planned task MAY itself be a planner
   when the node opts in (`max_plan_depth >= 2`). Safe because multiplication is bounded on every
   axis: per-level `max_tasks`, **cumulative `max_total_planned_tasks` across ALL levels**, shared
   run budget/USD caps, child plans re-validated at depth+1 against the SAME allow-lists (loud abort),
   replan owned by the TOP planner only, recursion in `fanout` mode rejected at build time.
2. **Flow-as-data v1 (`FlowArtifact` → `engine.run_authored_flow`). [BUILT]** An LLM emits a
   constrained declarative flow (**steps/branches/evaluator gates only**); the engine validates
   EVERYTHING before compiling — registered capabilities, allow-listed side effects, earlier-step-only
   retrace, resolvable model profiles, `max_nodes` — and runs it through the SAME executor, preflight,
   budgets, trace. **Recursion firewall:** authored flows may not contain planner or flow-author
   capabilities — AI-written things never contain AI-writers; recursion exists ONLY through the
   bounded depth mechanism above.

**Generated-process authoring boundary (Artem + codex, 2026-06-20):** shipped `FlowArtifact` is **v1
constrained flow-as-data**, NOT a full arbitrary pipeline builder. It fits a GoPro-style goal compiler
when the generated process is a small validated machine over registered capabilities
(step → branch → evaluate/retrace/fallback). It **cannot** author `fanout`/`subworkflow`/`human`/
`planner` nodes and cannot invent/register tools.

**`FlowArtifact` v1.5 — near-term small expansion [INTENDED].** After memory scope and
artifact/evidence expectations are explicit enough not to be bypassed, widen the authorable subset
only where the executor already has generic mechanics: authorable `fanout` over a registered item
capability; authorable `subworkflow` over a registered workflow; explicit `input_key`/`output_key`
mapping; same catalog/allow-list/model-profile/bounded-cycle/trace/preflight as v1; recursion
firewall unchanged. This is the near-term scope because it gives GoPro/MageQA more goal-compiled
shapes without changing the safety model.

**`ProcessArtifact` / `FlowArtifact` v2 — endgame [FUTURE / discuss-later, KEPT not discarded].** Full
generated pipelines may later add durable compile/register/reuse lifecycle, richer DAG/dataflow
dependencies, segment/video fan-out, artifact routing, workflow-memory scopes, evaluator/adjudicator
bundles, and typed IO binding strong enough for production GoPro video processing. **Explicitly not
current-stage**; requires a separate spec + explicit Artem approval after v1.5, T2 memory/storage
needs, and the artifact/evidence store have proven functional in their narrower scope.

**Visualization [BUILT]:** `workflow_to_mermaid/html` renders any definition as the machine it is
(node shapes by kind, labeled transitions, dashed bounded back-edges) and overlays a run result
(status-colored states, check-marked taken transitions). Zero new dependencies.

*(Implementation history of this section — first-class transitions, pre-set gate law, deterministic
guards, durable suspend/resume, self-describing machine cards, the executor split — is in Appendix A.)*

## 7. FIRST-CLASS CONCEPTS (every one is executable; none are documentation-only labels)
1. **`WorkflowDefinition`** (+ `WorkflowBuilder` DSL): nodes, transitions, branches, conditions,
   subworkflows, retry/retrace/fallback policy, evaluator gates, capability binding, scheduling
   policy, side-effect requirements, budget/limit requirements.
2. **Node harness** — built-in executable node types (see §8).
3. **`WorkflowExecutor`** — runs a `WorkflowDefinition` + `RuntimeProfile` end-to-end: node lifecycle,
   input/output mapping, state passing, structured-output validation, branch selection, errors,
   cancellation, limits, trace, checkpointing.
4. **Capability / worker contract** — see §5. Each capability carries input/output schema, timeout,
   retry policy, model profile, cost class, concurrency lane, side-effect class, privacy/raw-media
   export policy, evidence refs consumed/produced, idempotency-key strategy, trace fields, failure
   behavior, and declared memory scope where a built memory policy supports it. A bare callable is
   **not** sufficient.
5. **DI/IoC container** — §2.
6. **Composable subflows** — a workflow is usable **as a capability** inside another; recursive
   composition is **core**, not future.

## 8. NODE TYPES (each runnable by the executor + has a test)
deterministic Python step · typed capability/tool step · structured-LLM step · AI decision/gate ·
branch · fan-out/gather · evaluator/QA gate · retry · retrace · fallback · subworkflow · recursive
workflow-as-capability · human clarification · external process/script/tool · adapter call ·
media/image/voice node. **Each is executed by the engine; none is a label the product implements.**

## 9. FULL ACCEPTANCE CRITERIA — executable gates (the anti-drift)
Every gate is a **test that must exist and pass**, not prose. No proving test ⇒ not done.
**Binary DoD (master gate):** the §1 litmus answers **YES**, proven by A + B.

- **A. Same-executor proof.** ONE `WorkflowExecutor` runs all four workloads — Anki, GoPro inventory
  toy, MageQA site-audit toy, calendar-builder toy — from a `WorkflowDefinition` + registered
  capabilities. No workload subclasses/forks the executor.
- **B. No-manual-product-loop proof (static + runtime).** A static/AST check **fails** if any product
  pack contains orchestration (`StateGraph`/`add_conditional_edges`/hand-rolled retry/branch/fan-out/
  scheduler/retrace loops or side-effect enforcement). Those mechanics are engine-only.
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
- **I. Node-type executability.** Each §8 node type has a test proving the executor runs it.
- **J. Universal worker replacement.** One workflow node is tested with ≥2 worker families (e.g.
  deterministic fake + `LLMCallable`, or structured LLM + console client) while the graph/transition
  logic stays unchanged; malformed output/branch/artifact cases fail loudly through the same path.
- **K. Contract-guard proof.** The §1a guards are enforced where they can fail usefully:
  sanctioned provider-construction allow-list, product no-orchestration-loop guard, adopter
  status-projection/schema guard, engine-import-product guard, injected-machine description
  completeness guard, and executor/node cycle guard after the `NodeExecutionServices` boundary exists.
- Plus standing enforcement from `docs/workflow-engine-acceptance-criteria.md`: DOD-1 (no stubs),
  DOD-6 (flag-don't-downgrade), DOD-10 (no interface-only), DOD-12 (no silent fallback), per-capability
  uniform AC.

## 10. ANTI-DRIFT / ANTI-SIMPLIFICATION (explicit — so it cannot be re-shrunk)
These substitutes are **rejected**; any one = work is **not done**:
- "We have interfaces, the product can wire them." · "The toy examples call the same primitives." ·
  "Anki works, so the engine is done." · "Scheduler/evaluator/trace exist as separate classes." ·
  "A product can write a loop around the runtime." · "Unsupported features warn and then a simple mode
  runs silently." · "Agents get their own node/runtime outside capabilities." · "Everything becomes
  one giant invocation DTO or core inheritance tree."

Enforcement that makes drift impossible to hide: the **binary DoD** must be YES with A+B green; the
**static no-product-loop check (B)** fails the build on hand-rolled orchestration; **DOD-6:** if the
implementer thinks a piece is unneeded/infeasible/should be simplified, **stop and flag Artem** — do
not ship a weaker version; **DOD-9 self-report:** each gate A–J links its proving test; "every node
type executable" (I) kills documentation-only labels.

## 11. RISK WATCHLIST — where this becomes a mess if discipline slips
Real threats are **discipline threats**, not design flaws. Each has a named fence; violating a fence
requires a deliberate, user-approved decision — never drift:

1. **Node-kind proliferation** (the inner-platform cliff). 8 kinds exist (step/branch/fanout/evaluate/
   subworkflow/human/planner). Domain needs become *capabilities or packs*, never kinds; a new kind
   must be generic across products. Fence: §3 rule + product-neutrality guard test.
2. **Planner depth creep.** Bounded recursion is SUPPORTED but PRE-SET OFF — `max_plan_depth=1` by
   default; opt into depth N explicitly; cumulative `max_total_planned_tasks` caps total AI-authored
   work across ALL levels. UNBOUNDED recursion forbidden; AI-authored flows may not contain AI-writers
   (§6). Fence: depth-aware validation + cumulative budget + tests.
3. **No-API/browser executor = highest-maintenance L1 member.** Brittle (UI drift), slow, ToS-gray.
   The protocol contains the blast radius (one `LLMCallable`, `cost_known=false`, aggressive timeouts),
   but the adapter is a maintenance pet. Build it LAST, only against a concrete paying use case.
4. **Protocol fattening.** `LLMRequest/LLMResponse` stayed minimal on purpose. New fields enter ONLY
   for a named consumer, additive-only, single-turn behavior bit-identical. Speculative fields,
   central worker-invocation mega-types, and core OOP taxonomies are rejected. (This is the engine's
   main YAGNI fence — applies to memory tiers, process v2, etc.)
5. **Executor god-file.** Sanctioned mechanical handlers-per-module split — DONE (`nodes/`); keep it
   that way (don't recreate a monolith).
6. **Stringly-typed state paths.** `input_key`/`node_outputs` conventions are fine at ≤30-node
   workflows (largest real: Anki, 26). If a product hits real pain, design a typed mapping — do not
   bolt expressions into YAML (risk #1 in a costume).
7. **Budget-knob proliferation.** Every new cap maps to a proven operational need — never speculative
   knobs. All caps Optional/None.
8. **When NOT to use the engine.** A trivial one-LLM-call script with no retry/budget/trace/branching/
   scheduling need stays a script. Forcing everything through the engine is how frameworks get
   bypassed and shadow paths form.

## 12. REJECTED / DEFERRED DECISIONS REGISTER (single source; update when items land or die)

### 12a. Actively avoid — wrong shape for this engine
| Item | Decision | Rationale |
|---|---|---|
| Second agent-node abstraction | **Do not build** | Bypasses capabilities, fragments policy/trace/budget/artifact/memory enforcement, and recreates the "LLM-node DAG" failure mode. Agent episodes are capabilities. |
| Literal unified-envelope rewrite | **Do not build now** | The semantic contract is already enforceable through existing seams. Merging `CapabilityContext`, `LLMRequest`, `CliAgentRequest`, human/subworkflow inputs, etc. into one class is high-regression/low-value until a concrete need appears. |
| Central worker OOP taxonomy | **Do not build** | Worker axes are compositional, not tree-shaped. A core inheritance hierarchy would become a rigid taxonomy and force engine edits for new modalities. Use adapter-local classes + capability specs instead. |
| `langmem` as engine-core dependency | **Do not put in core** | It performs LLM-driven extraction/consolidation with its own assumptions. In core that risks hidden model calls, budget/privacy bypass, schema drift, and replay ambiguity. It may later be a product-level chat-personalization adapter behind our memory interface. |
| Token-level streaming as engine feature | **Do not add now** | Event-level live sinks exist, and voice/Twilio can BYO a streaming `LLMCallable`. Adding protocol surface without an engine-owned consumer is protocol fattening; revisit only if BYO transport cannot satisfy a concrete product need. |

### 12b. Deferred — valid direction, gated on named consumer/proof
| Item | Status / context | Trigger to do it |
|---|---|---|
| Browser-bridge no-API executor | L1 member, protocol-ready; §11 #3 highest-maintenance | A concrete paying use case (build LAST) |
| Workflow memory T1 | **[BUILT/PARTIAL]** `AgentMemory` seam, `FullReplayMemory`, `ImageEvictingMemory`, `MemoryStore`, `MemoryNamespace(product, tenant, subject, kind)`, `InMemoryMemoryStore`, canonical modes `full_replay` / `image_evicting`, and S0 snapshot/resume determinism proof | Keep memory as prompt input, never control. Do not add aliases or hidden model calls. |
| Workflow memory T2/T3 | **[INTENDED/deferred]** `WindowedMemory`, `StructuredStateMemory`, `CompactingMemory`, durable stores, semantic search, LangGraphStore adapter, planner/subworkflow/project scopes | Add each memory policy/backend only when a named consumer proves the need and replay/privacy/budget invariants are covered by tests |
| `FlowArtifact` v1.5 (authorable fanout/subworkflow + io-mapping) | [INTENDED] §6 — additive, small | After memory/artifact contracts solid + GoPro need |
| `ProcessArtifact` / FlowArtifact v2 | **[FUTURE / discuss-later — KEPT]** §6 endgame | Separate spec + explicit approval after v1.5 |
| Worker-contract seam tightening | **[PARTIAL]** §5 principle live; literal envelope unification is rejected for now | Add adapter metadata only when a concrete feature needs it: memory scope, artifact expectations, cost/timeout policy, validation hooks |
| PromptRenderer/Jinja prompt refs | Valid small next slice after H1 | Add `PromptRef`/`PromptRenderer` with strict `{var}` default and Jinja as one renderer; fail loudly on path escape/missing variables; keep raw templates compatible; no transition/state-patch work in that slice |
| Image+reference+QC → L2 pack | Production-proven inside Anki product code | 2nd consumer (§11 #4 bar) |
| Presentation-builder pack, MCP tool-suite packs | Named in the L2 vision | When the project materializes |
| `ANKI_*` env alias bridge sunset | config-architecture CFG-8 transition bridge | Pi `.env` migrated by aws_deploy |
| H1 internal seam hardening: `NodeExecutionServices` | Accepted framework-health cleanup | Refactor node handlers to depend on a narrow service protocol, then enforce no executor<->nodes cycle |
| H2 run-session lifecycle | Accepted framework-health cleanup | Introduce internal `WorkflowRunSession`; keep public `engine.run(...)`; prove concurrent runs isolate trace/detail/usage/bundle state |
| H3 usage/accounting split | Accepted framework-health cleanup | Split budget gates, usage events, provider extraction, pricing, token estimation, and rendering while preserving `usage.py` compatibility exports through the next tag |
| `test.sh` `-k "a or b"` word-split bug | Workaround = paths/single tokens | Next time someone touches test.sh |
| Done & closed | Media/voice → `ai_workflow_tools.media` (v0.4.0) · `workflow_capability` adapter · durable resume · planner depth ≥2 · executor split · `AgentRunRequest.metadata` passthrough (v0.4.1) · T1 memory seam/store · canonical memory modes · S0 non-default memory replay proof · `validate_graph`/`build_definition_from_artifact` decomposition · `_images_from_output` fail-loud complexity cleanup · runtime observability graph + full byte-free detail capture (v0.5.0) | — |

## 13. STATUS — EXISTS vs INTENDED (read this before building on a promise)
| Area | Status |
|---|---|
| Core mechanics: declare/wire/run on a single executor | **[BUILT]** |
| Master no-product-loop proof across §9 Test A+B | **[PARTIAL]** — Anki + reminder live; all-four-workload/static guard evidence still required before claiming architecture-complete |
| Node kinds: step/branch/fanout/evaluate/subworkflow/human/planner | **[BUILT]** |
| Transitions, pre-set cycle gates, deterministic guards, snapshot/suspend/resume | **[BUILT]** |
| Flow-as-data v1 (authored step/branch/evaluate) + recursion firewall + bounded planning | **[BUILT]** |
| Self-describing machine cards + capability catalog | **[BUILT]** |
| Budgets/cost-honesty, trace sinks, model binding, scheduling/cancellation, evidence refs | **[BUILT]** |
| Runtime observability graph/detail capture | **[BUILT]** — compact trace events + linked full byte-free details when internal capture is enabled; usage ledger remains separate |
| L1 executors: LangChain, plain-callable, console (`claude -p`/`codex exec`) | **[BUILT]** |
| Universal worker-contract seam (§5) | **[PARTIAL]** — principle live; adapter DTOs stay specialized |
| Contract guard program (§1a/§9K) | **[PARTIAL]** — no-product-loop and product-neutrality guards exist; provider allow-list, adopter status-projection guard template, source-inspection import/coupling guards, injected-machine description guard, and post-H1 executor/node cycle guard remain to implement |
| Workflow memory T1 (§0/§12) | **[BUILT/PARTIAL]** — `AgentMemory`, `FullReplayMemory`, `ImageEvictingMemory`, `MemoryStore`, `MemoryNamespace(product, tenant, subject, kind)`, `InMemoryMemoryStore`; scope is bounded-agent prompt projection + deterministic store contract |
| Workflow memory T2/T3 (§0/§12) | **[INTENDED/deferred]** — durable/semantic backends, windowed/structured/compacting policies, LangGraphStore adapter, planner/subworkflow/project scopes |
| FlowArtifact v1.5 (authorable fanout/subworkflow) | **[INTENDED]** |
| ProcessArtifact / FlowArtifact v2; no-API/browser executor; token streaming | **[FUTURE]** |

---

## Appendix A — Implementation history & changelog (historical; not current-stage instructions)
*Preserved for provenance. The current truth is §0–§13 above; this records how it got there and the
original build order (released through v0.5.0).*

**§6 authoring/state-machine rounds (2026-06-12):**
- *Round 2 — combined AI/state-machine core:* first-class `Transition(source,target,label,policy,
  max_traversals,on_exhausted)` replaced `WorkflowEdge` (`edges`→`transitions`, `conditional`→
  `policy`); evaluator accept/reject routes materialized at construction; one wiring path + one router.
  Pre-set gate law (ungated cycles rejected; runtime traversal counters + declared escape label).
  `engine.register_guard` deterministic navigation. Durable suspend/resume via `MachineSnapshot` +
  `engine.resume` (fast-forward replay, cumulative budgets, cross-process JSON). Authored bounded
  loops; definition JSON round-trips.
- *Round 3 — self-describing machine (MCP analogy):* `Transition.description` + `.branch(describe=)`;
  `render_machine_card(definition,node,state)` with live gate budgets; `inject_machine` (pre-set OFF);
  `render_capability_catalog`; executor god-file split into `nodes/` (1956→762 lines, fence honored).

**Original build outline (E1–E7, all shipped):** E1 `WorkflowDefinition`+`WorkflowBuilder`+node
registry · E2 `WorkflowExecutor` (LangGraph internal) · E3 DI container (`from_config`+
`register_capability`+contract enforcement) · E4 subworkflow-as-capability · E5 migrate Anki (delete
product LangGraph orchestration) · E6 migrate 3 toys + static no-product-loop check + same-executor
proof · E7 GoPro & MageQA handoff packages.

**Reused vs new vs removed (at extraction):** Reused as executor internals — scheduler, evaluator,
agent capability, fan-out, evidence refs, fail modes, trace sink, usage/budget, config loader, human
clarification. New — `WorkflowDefinition`, `WorkflowBuilder`, `WorkflowExecutor`, DI container,
subflow-as-capability, static no-product-loop check. Removed — product-owned LangGraph orchestration
(Anki's `add_conditional_edges`/`_route_*`).

**Original open questions (all resolved):** LangGraph internal-only → YES (hidden backend);
implementer = Claude → done; builder API names (`WorkflowBuilder`/`WorkflowEngine.from_config`/
`register_capability`) → locked and shipped.
