# Workflow Engine Architecture

Status: target architecture
Owner: Artem
Last updated: 2026-07-11

Binding source of truth: `docs/executable-workflow-engine-spec.md`. This file is the design overview;
if it conflicts with the binding spec, the binding spec wins. Current Anki/workflow acceptance
criteria and validation evidence are tracked in `docs/anki-acceptance-criteria.md`.

Related project documents:

- [Workflow engine extraction discussion](/Users/artemm/PycharmProjects/TGHandyUtils/docs/_discussion/2026-06-07-workflow-engine-extraction.md)
- [AI workflow engine README](/Users/artemm/PycharmProjects/TGHandyUtils/packages/ai_workflow_engine/README.md)
- [Anki acceptance criteria](/Users/artemm/PycharmProjects/TGHandyUtils/docs/anki-acceptance-criteria.md)
- [Anki implementation plan](/Users/artemm/PycharmProjects/TGHandyUtils/docs/anki-implementation-plan.md)
- [MageQA QA orchestrator design](/Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md)
- [MageQA agentic tester architecture](/Users/artemm/PycharmProjects/MageQA/docs/14-agentic-tester-architecture.md)
- [GoPro workflow execution engine requirements](/Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md)
- [GoPro universal profile sheet](/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/UNIVERSAL_PROFILE_SHEET.md)
- [GoPro home inventory case](/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/HOME_INVENTORY_CASE.md)
- [Executable workflow engine contract](/Users/artemm/PycharmProjects/gopro-streaming/docs/_discussion/2026-06-08-executable-workflow-engine-contract.md)
- [Claude implementation handoff](/Users/artemm/PycharmProjects/TGHandyUtils/docs/workflow-engine-claude-implementation-handoff.md)

## Goal

Build a reusable, executable, LLM-oriented workflow application builder/runtime. Product implementers
define goals, workflow definitions, gates, tools, prompts, schemas, adapters, limits, and policies;
the engine executes the full workflow and owns orchestration mechanics.

The engine is not an Anki engine. Anki is the first product workflow using it.

Scope decision (2026-06-07, Artem): build the FULL framework now — all primitives listed below —
validate it end-to-end on Anki, then adopt the same engine in GoPro and MageQA. This supersedes any
earlier "add machinery only when a second workflow needs it" guidance elsewhere in this doc. The
implementation order and per-phase Anki acceptance live in
`docs/workflow-engine-implementation-plan.md`.

The framework must also fit future goal-driven workflows that are neither flashcards nor QA, for
example building a user's calendar from available time, energy level, priorities, task pool, and
focus projects. This is a planning/constraint workload: it uses low-level scripts and integrations,
but only as typed capabilities driven by a higher-level goal and evaluator.

## Core Concept

```text
WorkflowDefinition + WorkflowGoal + WorkflowProfile + registered capabilities/adapters
  -> WorkflowEngine.run(...)
       -> executes nodes, gates, branches, fan-out, subflows, and evaluators
       -> validates structured outputs and repairs JSON where configured
       -> retries, retraces, falls back, fails, or asks the user within policy
       -> enforces budget, side effects, privacy/raw-media policy, scheduling, and checkpoints
       -> emits trace, usage, cost, cache, artifact, and decision records
  -> product-owned delivery/storage
```

The target developer experience is closer to a typed, LLM-focused n8n/LangGraph/DI runtime than to
a helper library. Product code fills contracts; it must not build a mini-engine around primitives.

Current status note (2026-07-01): the binding spec records declare/wire/run core mechanics as built,
with the master all-workload no-product-loop proof still partial. Treat older "primitive kit" or
"WorkflowDefinition missing" wording in historical docs as superseded by
`docs/executable-workflow-engine-spec.md`.

## Engine Soul

The engine is a goal-driven capability runtime.

It is not:

- an Anki-specific graph with reusable-looking names;
- a general-purpose visual no-code product;
- a menu of scripts;
- an interface-only package where each product has to reimplement the real runtime;
- an unrestricted autonomous agent that can invent tools, side effects, and retry loops at runtime.

It is an executable workflow builder/runtime: product workflow definitions are authored by project
implementers, but the engine owns execution.

### Complexity Governance: powerful without becoming a knot

The architecture minimizes **mandatory consumer complexity and accidental coupling**, not capability
or implementation depth. A complex, explicitly approved feature is welcome when it belongs to the
engine, centralizes a universal mechanic, or creates a clean extension seam. "Simple workflows stay
simple" means consumers that do not select that feature should not have to understand, configure,
wire, or activate its behavior. Negligible inert plumbing may stay in the canonical runtime when
splitting it would create more concepts or a parallel execution path. This principle is not permission
to replace an advanced feature with a reduced version.

Do not invoke the Soul as a generic simplicity/YAGNI veto. Any proposal to reject, defer, remove, or
narrow functionality must name the concrete architectural harm, the functionality lost, and the least
destructive alternative; functionality and scope cuts require the user's decision. Module/import/class
counts are diagnostics, not architecture verdicts.

Complexity should be paid once at the right boundary:

- universal mechanics live in the domain-neutral engine;
- reusable specialized mechanics live in optional services or packs;
- product semantics live in domain packs;
- every substantial addition has a typed contract, bounded behavior, tests, observability, ownership,
  and explicit degradation analysis.

The prohibited outcome is a knot: product special cases in core, duplicated runtimes, circular
ownership, stringly hidden contracts, or unrelated consumers forced to change. The prohibited remedy
is capability stripping: deleting or underspecifying useful behavior just to make the framework look smaller.

## Executable Workflow Contract

Product owns:

- domain goal and workflow definition;
- node names, graph shape, and domain-specific branch labels;
- prompts, schemas, rubrics, validators, tools, adapters, storage, delivery, and UI transport;
- domain policy such as what counts as a good flashcard, grounded QA finding, or valid inventory
  observation.

Engine owns:

- workflow execution and node dispatch;
- branching, conditional gates, supervisor loops, fan-out/gather, and subworkflow execution;
- retries, retrace, fallback, fail-open/fail-closed behavior, and evaluator mechanics;
- structured LLM parsing and repair;
- scheduling, backpressure, cancellation semantics, and concurrency lanes;
- side-effect enforcement, privacy/raw-media export policy, budget/cost limits, trace,
  checkpointing, pause/resume, and human clarification mechanics.

If product code must manually implement these mechanics, the engine is unfinished.

Required finished-state API shape:

```python
workflow = (
    WorkflowBuilder("home_inventory")
    .step("select_evidence")
    .branch("evidence_quality_gate", branches={
        "enough": "extract_items",
        "ambiguous": "ask_location",
        "bad": "fallback_or_fail",
    })
    .step("extract_items")
    .evaluate("quality_gate", on_reject=Retrace("select_evidence"))
    .step("write_inventory")
    .build()
)

engine = WorkflowEngine.from_config("gopro.inventory.yaml")
engine.register_capability("select_evidence", SelectEvidenceTool(...))
engine.register_capability("extract_items", VlmItemExtractor(...))
engine.register_capability("ask_location", HumanClarification(...))
engine.register_capability("write_inventory", InventoryAdapter(...))

result = await engine.run(workflow, video_segment)
```

The product should not write a manual sequence of `runtime.invoke(...)`, `if result.bad`, retry,
retrace, fallback, and trace calls. That is the exact drift this architecture forbids.

## Anti-Drift Rule

Do not declare the engine complete because individual primitives exist.

This is not sufficient:

```text
CapabilityRuntime exists.
TraceSink exists.
Evaluator exists.
Scheduler exists.
Agent wrapper exists.
Product code manually wires them in a custom loop.
```

The completion bar is:

```text
WorkflowDefinition + registered capabilities + profile/config
  -> engine.run(...)
  -> fully executed workflow with trace, scheduling, retries, gates, side-effect/privacy policy,
     checkpoints, usage/cost, and result.
```

It should feel like a small framework for agentic product workflows:

- a product states a goal, profile, constraints, evidence, and delivery target;
- the supervisor sees the goal plus a registry of typed capabilities it is allowed to use;
- capabilities may be LLM calls, deterministic Python tools, media generators, voice generators,
  browser/CLI agents, subworkflows, human-clarification steps, or external adapters;
- every capability has a schema, description, side-effect policy, budget policy, timeout policy,
  trace policy, and quality contract;
- the evaluator can accept, repair, retry one capability, retrace to a prior step, fall back, fail,
  or ask the user, within bounded policy;
- artifacts, cost, prompt/cache metadata, branches, rejected outputs, and fallback reasons are
  visible in trace instead of being hidden inside product code.

## Contract Guardrails

The framework stays universal through a small number of load-bearing guards. The rule is narrow:
each important seam gets one declared contract and one fail-build guard. Do not replace this with
pervasive linting, a required base-class hierarchy, or product-wide style policing.

External product-to-engine guards:

- **One provider door:** provider clients are constructed only in named sanctioned modules. Today
  that means `services/llm_factory.py` for shared product LLM/raw OpenAI clients and
  `packages/ai_workflow_tools/ai_workflow_tools/media/image_generation.py` for the reusable image
  provider adapter. New construction sites require a reviewed allow-list entry.
- **No product orchestration loop:** products declare workflows and register capabilities; they do
  not hand-roll `StateGraph`, retry/retrace, fan-out, scheduler, branch, or side-effect/budget loops
  around the engine.
- **Declared run-state statuses:** engine-facing and dashboard/API projections use the engine status
  vocabulary, or a product enum with an explicit deterministic mapping. Runtime normalization is not
  enough because adopter projections can drift outside the engine path.

Internal engine guards:

- **Engine does not import products:** `ai_workflow_engine` remains product-neutral.
- **Machine identity:** definitions carry a content digest; compiled graphs and registries key
  on `(workflow_id, digest)` so a re-registered machine can never execute stale.
- **Executor/node boundary:** after the `NodeExecutionServices` refactor, node handlers depend on a
  narrow service protocol and the executor<->nodes cycle is forbidden.
- **Reserved state keys:** the engine's state-channel keys are a closed, guarded set; node
  handlers writing any other control key fail the build (schema and set may never drift apart).
- **Single bundle owner:** when a bundle is passed to `engine.run(observation_bundle=...)`, the run
  session finalizes it exactly once with the true terminal status (raise -> failed; the
  `terminal_status` hook lets product post-validation override before the record is written).
- **Evidence resolution (G1, decided 2026-07-02 — engine-owned):** at finalize, the run's
  `WorkflowArtifact`s are archived under the bundle's `artifacts/` with an `artifacts.json`
  manifest (`source_path -> bundle_path`, sha256, honest `skip_reason` for anything not copied).
  Dashboards resolve `EvidenceRef`s through the manifest; artifacts prune WITH the bundle so
  `retention_limit` stays the single cleanup policy (no second evidence store, no second
  retention job). Policy knobs live in `ObservationConfig` (`artifacts: copy|off`,
  `artifact_max_bytes`); a run that raises before producing an envelope archives an honestly
  empty manifest.
- **Injected machine descriptions:** when legal routes are injected into a branch/decision prompt,
  every legal label must have a description; missing labels fail validation instead of producing an
  opaque machine card.

The engine should ship canonical enums and reusable guard templates, but adopter repos must run the
product-facing checks on their own packs and projections.

**CLI-worker tool surface — scoped by call type (decided 2026-07-04; implemented + regression-tested
2026-07-05, `allowed_tools` tri-state: `None`→default / `[]`→`--tools ""` / list→exact):** a
`claude -p`/`codex exec` worker's tool access is a property of *how it
is used*, not one blanket rule. (1) A **structured LLM-node call** (`ConsoleLLMClient`/
`ConsoleChatModel` behind a `StructuredLLMNode`) is a completion, not an agent — all tools
disabled; a tool call would break the JSON parse/repair contract and add an injection surface for
no benefit. (2) A **staged-vision call** gets `Read(./inputs/**)` only. (3) A
**`CliAgentCapability`** — the real subagent — gets a documented, deliberately-generous
investigation default (read/search/web plus `Bash`) when the request names none. Because `Bash` can
write files or reach the network, a Bash-enabled default must declare `workspace_write` and
`external_call` side effects and fail before process spawn when the workflow policy denies them. A
non-empty request list is an exact override. `codex_exec` is governed by its sandbox instead of
Claude-style tool names, so non-`None` `allowed_tools` fails loudly and its workspace-write
subscription episode must still declare the same side effects before spawn. `Edit`/`Write`/MCP stay
opt-in because they are stronger side-effecting tools and belong behind the `external_write` gate,
never a silent default.
The principle: lean generous for investigation agents, none for completions — never bolt tools onto
a node call to avoid it becoming an agent.

**Enriched decisions — the supported recipe (decided 2026-07-01, supersedes a `transition_branch`
primitive):** when a decision needs situation-specific enrichment ("retry — and change THIS") or an
evolving in-run scratchpad, do not reach for a new node kind. Use a judgment `step` whose output
carries the full judgment (verdict + rationale + instructions/odds) followed by a `branch`/guard that
routes on it — outputs already flow forward, stay validated, and replay cleanly. `evaluate` covers
repair loops (criticism flows into the retry); authored subflows cover dynamic sub-processes. A richer
transition primitive is reconsidered only when a real consumer has ≥2 hand-written judgment-step+branch
pairs and the two-node shape demonstrably hurts (then: envelope first, state-patch last).

Low-level scripts and tools are explicitly allowed. They become architecture only after they are
registered as capabilities with typed inputs/outputs, limits, trace, and failure semantics. A Python
script that optimizes a calendar, a browser agent that audits a page, a frame sampler that inspects
video, and an image generator that creates a flashcard visual are the same class of runtime object:
a capability the supervisor may select and the evaluator may judge.

## Completeness Bar

The engine must ship reusable implementation, not just abstractions.

Product code should not need to reimplement:

- capability registration and invocation;
- supervisor planning over capabilities;
- structured LLM calls and JSON repair;
- model/profile selection;
- prompt loading and static/dynamic prompt separation;
- evaluator decisions, retrace, retry, fallback, and fail modes;
- loop and recursion limits;
- usage, budget, cache, and cost tracing;
- artifact ownership and cleanup;
- media/voice provider seams and default adapters;
- external process/agent wrappers;
- fan-out/gather execution;
- scheduling policies;
- human clarification hooks;
- trace sinks and replayable run records.

Product code still owns domain facts: schemas, product prompts, rubrics, adapters for domain systems,
delivery, and product-specific state. If a second project must copy more than product-pack code to
use the engine, that is a framework gap, not acceptable "client implementation."

The reusable engine should own product-agnostic execution primitives:

- workflow goal and constraints;
- workflow profile and compiled runtime plan;
- trigger/evidence strategy and evidence references;
- task/session state lifecycle;
- instrument registry;
- model profile selection;
- node execution wrappers;
- capability/tool allow-list and side-effect policy;
- structured output parsing and retry policy;
- scheduling/concurrency policy;
- explicit fail mode;
- external/domain adapter boundary;
- human clarification capability;
- trace events and artifact ownership;
- bounded loop/retrace primitives and shared policy hooks;
- checkpointing/replay seams where a product needs restart recovery.

Product workflows own:

- product-specific state models;
- product-specific prompts and schemas;
- product-specific validators;
- product-specific policy/rubric inputs that say why a branch, retry, retrace, or fallback is
  appropriate in the domain;
- product-specific rendering and side effects;
- product-specific delivery.

This boundary is not an excuse to keep 90% of implementation client-side. Product workflows describe
what a capability means in their domain; the engine runs, traces, limits, retries, evaluates, and
connects capabilities under a shared control model.

Current implementation note: the binding spec is authoritative for built vs partial status. As of
2026-07-01, `WorkflowDefinition`/`WorkflowBuilder`, `WorkflowExecutor`, and `WorkflowEngine.run` are
the declared runtime path. The remaining architecture-completion claim is narrower: keep proving that
every product pack uses that path instead of product-side orchestration, and keep the all-workload
same-executor/no-product-loop gates explicit.

## Library Boundary

Start as an internal package, not a published generic library.

Recommended package shape:

```text
packages/ai_workflow_engine/
  pyproject.toml
  ai_workflow_engine/
    models.py
    engine/
      supervisor.py
      instruments.py
      llm_node.py
      runner.py
      artifacts.py
    usage.py
    prompt_loader.py
    image_generation.py
    image_models.py
    voice_generation.py
```

Current internal package:

```text
packages/ai_workflow_engine/
```

This package no longer imports TGHandyUtils app config interfaces, app logging helpers, app
exception classes, or the app LLM factory. Product code must inject concrete LLM clients or an
`llm_factory` into `StructuredLLMNode`; TGHandyUtils Anki planners inject `services.llm_factory`
from outside the engine. Prompt loading supports a custom prompt root through `PromptTemplateLoader`
so another project can keep its own prompt directory.

External-git rule: only move this internal package into a separate git repository after at least two
real product workflows use it.
For example:

- Anki card generation;
- home inventory video/image processing;
- calendar building from availability, energy, priorities, tasks, and focus projects;
- task/reminder enrichment or another non-Anki workflow.

Before that point, keep the boundary clean but avoid a generic framework that hides product logic.

## Framework Decision

Use LangGraph as the explicit graph-control backend for product workflows that need conditional
edges, cycles, retrace/fallback, interrupts, or checkpointing.

Why:

- LangGraph is explicitly a low-level orchestration framework for long-running, stateful agents and
  workflows.
- It supports routing, orchestrator-worker patterns, evaluator-optimizer loops, persistence,
  checkpointing, interrupts, and time travel.
- It does not force a single prompt or state shape.
- Product workflows can remain typed and explicit.

Use LangChain only for model/tool integration where useful.

Do not replace the engine with Pydantic AI now. Pydantic AI is attractive for reusable typed agents,
tools, output validation, model profiles, and agent-style apps, but the current need is explicit
graph flow control. It can be used later inside nodes or reconsidered if workflows become mostly
agent/tool loops rather than explicit graphs.

External frameworks:

- LangGraph: best fit for explicit graph customization, routing, orchestrator-worker patterns,
  evaluator-optimizer loops, persistence, and retrace/time-travel style debugging.
- LangChain agents: useful for common tool-calling loops, but too implicit for product workflows
  that need exact branch/fallback behavior.
- Pydantic AI: useful for typed agents/tools/output validation and may be good inside individual
  nodes; not the primary graph engine for this project yet.
- AutoGen/Crew-style multi-agent systems: useful for collaborative agent chat patterns, but too
  broad for deterministic product pipelines unless a later use case needs multi-agent debate.

This does not mean every reusable engine primitive imports LangGraph. The package-level capability
runtime, scheduler, evaluator, trace, budget, and adapter primitives stay plain Python and
framework-neutral; product graphs such as Anki may use LangGraph and pass runtime controls such as
`recursion_limit` through `WorkflowRunner`. The engine is not a replacement for LangGraph and not a
no-code workflow product.

## Key Engine Objects

```python
class WorkflowGoal(BaseModel):
    workflow_type: str
    objective: str
    constraints: dict[str, Any]
    delivery_target: str | None
    user_id: int | None
    metadata: dict[str, Any]
    goal_id: str

class WorkflowInstrumentSpec(BaseModel):
    name: str
    kind: Literal["workflow", "llm", "tool", "deterministic"]
    description: str
    input_schema: type[BaseModel] | None
    output_schema: type[BaseModel] | None

class ModelProfile(BaseModel):
    name: str
    model: str
    temperature: float
    timeout_s: int | None
    max_retries: int
```

Model profiles are required because different nodes may need different models:

- current mini model for routing, scenario planning, card descriptors, and quality checks by
  default;
- stronger model for rare complex supervisor decisions;
- image model/provider for visual generation;
- optional future nano profile for high-volume gates only after evals.

Current OpenAI text profile guidance, checked on 2026-06-04:

- use `gpt-5.4-mini` for decision gates, smoke tests, routine scenario planning, card rendering,
  and quality evaluation by default;
- treat `gpt-5.4-nano` as a later explicit optimization only after evals show routing and card
  quality still hold;
- use `gpt-5.5` only for complex supervisor decisions where higher cost is justified;
- avoid adding `gpt-4o-mini` defaults in new workflow-engine code. It is legacy for this
  architecture.

Cost visibility and generated-image rollout:

- the local usage meter includes official current prices for `gpt-5.4-mini`/snapshot aliases,
  `gpt-5.4`, `gpt-5.4-nano`, `gpt-5.5`, `gpt-image-2`,
  `gemini-2.5-flash-image`, `gemini-3.1-flash-image`, and `gemini-3-pro-image`;
- cached input tokens are charged at cached-input rates when provider usage metadata reports them;
- for Gemini image models, cached token counts are displayed if returned, but current image-pricing
  docs do not publish cached-image discounts, so estimates price cached image input the same as
  normal input unless `WORKFLOW_MODEL_PRICE_OVERRIDES_JSON` overrides it;
- usage replies include cached input tokens when present, so cache behavior is visible during live
  Telegram testing;
- `WORKFLOW_SHOW_USAGE_IN_REPLY=true` surfaces compact usage and estimated USD in Telegram replies;
- `ANKI_IMAGE_GENERATION_ENABLED=true` enables explicit generated-image requests such as `[i gen]`;
- `ANKI_AUTO_IMAGE_GENERATION_ENABLED=false` keeps AI-decided generated images opt-in, even when the
  type planner thinks a visual card would be useful.
- `ANKI_IMAGE_PROVIDER=openai|gemini|comparison` selects the image backend without changing graph
  topology. OpenAI defaults to `gpt-image-2`; Gemini defaults to `gemini-3.1-flash-image`
  (Nano Banana 2). Use `ANKI_GEMINI_IMAGE_MODEL=gemini-2.5-flash-image` for cheaper Gemini
  comparisons.
- `AnkiImagePromptPolicy` is the product-level image prompt seam. It keeps the shared PPLA
  visual identity and base learning rules stable first for cache affinity, then appends
  provider-specific OpenAI/Gemini/comparison guidance before the card-specific brief.
- The image prompt policy also adds dynamic factuality guards, including a no-invented-numbers
  guard when the source/scenario does not provide numeric values.
- Gemini reference images are sent as inline image parts. Gemini response-format controls
  (`aspectRatio`/`imageSize`) are disabled by default via `ANKI_GEMINI_RESPONSE_FORMAT_ENABLED=false`
  because live REST validation can reject documented values during model/API rollout; enable it only
  for measured provider experiments.
- comparison mode uses `ANKI_IMAGE_COMPARE_PROVIDERS=openai,gemini` and returns only the configured
  primary provider image to the Anki card. Alternative provider outputs are saved for Telegram
  preview/log comparison, not embedded into the note. It intentionally requires
  `WORKFLOW_MAX_IMAGE_CALLS_PER_RUN` high enough for every compared provider.
- `ANKI_VOICE_GENERATION_ENABLED=true` enables explicit language-card audio requests such as
  `[i langvoice ukr->pt gen] ...`. The graph calls a provider-neutral voice tool seam, currently
  backed by ElevenLabs. Voice calls are capped separately by `WORKFLOW_MAX_VOICE_CALLS_PER_RUN`
  and are recorded as tool usage with provider/model/voice ID/character count. Because ElevenLabs
  effective USD cost depends on the account plan, set `ANKI_ELEVENLABS_USD_PER_1K_CHARS` only when
  you want local USD estimates for audio.

Prompt caching rule:

- every new LLM node must put stable instructions, examples, style identity, tool policy, and JSON
  schema in the first/static message;
- dynamic material must stay in later user messages: user text, OCR, uploaded-image analysis,
  `[i ...]` guidance, selected facts, card plan, rendered cards, and tool results;
- do not append reusable schemas after dynamic source content; that defeats prefix-cache reuse;
- prompt text belongs in repository-owned files under `prompts/`, loaded through
  `ai_workflow_engine.prompt_loader`. Use LangChain f-string-style `{variable}` placeholders by
  default; introduce Jinja/Mustache-style template logic only when a prompt genuinely needs loops,
  conditionals, or nested rendering;
- keep the core split provider-agnostic through LangChain `SystemMessage`/`HumanMessage` ordering.
  Provider-specific knobs such as OpenAI `prompt_cache_key` or Claude cache-control blocks belong in
  adapter/model-profile code, not in product prompts;
- OpenAI chat calls may use `OPENAI_PROMPT_CACHE_KEY_PREFIX` and
  `OPENAI_PROMPT_CACHE_RETENTION` (`in_memory` or `24h`) to pass cache-affinity hints from the
  OpenAI adapter. Leave the prefix empty to disable this hint without changing prompts;
- image-generation prompts should also keep reusable visual identity/style instructions before the
  per-card facts. Reused style/source reference images should be stable inputs when possible, but
  generated image output remains separately billed.

Current implementation note:

- the reusable `ModelProfile` class/registry exists in the engine package and is loaded from
  YAML-backed workflow config;
- `StructuredLLMNode` already takes injected LLM clients/factories, so provider construction is not
  hardcoded into the engine;
- the Anki branch already has direct config-level profiles:
  - `ANKI_DECISION_MODEL`;
  - `ANKI_SCENARIO_MODEL`;
  - `ANKI_RENDER_MODEL`;
  - `ANKI_QUALITY_MODEL`;
  - `ANKI_COMPLEX_SUPERVISOR_MODEL`;
  - `ANKI_IMAGE_MODEL`.
  - `ANKI_IMAGE_PROVIDER`;
  - `ANKI_OPENAI_IMAGE_MODEL`;
- `ANKI_GEMINI_IMAGE_MODEL`.
- `ANKI_VOICE_PROVIDER`;
- `ANKI_ELEVENLABS_MODEL`.

Per the 2026-06-07 scope decision (build the full framework now, validate on Anki, then
GoPro/MageQA), the reusable `ModelProfile` registry is framework-owned; `config.py` now consumes the
YAML/profile bundle instead of owning non-secret model defaults directly.

Sources:

- <https://developers.openai.com/api/docs/models>
- <https://developers.openai.com/api/docs/pricing>

## Runtime Internal Ownership (v0.11.1)

`CapabilityRuntime.invoke` remains the engine's ONE capability door for every worker family
(deterministic tools, LLM workers, agents, human steps, external processes, evaluators, and fanout
children). A subworkflow delegates to the same executor; its child nodes cross this door when they
invoke capabilities. Its internals are owned by three
single-purpose collaborators behind that unchanged facade:

- **`engine/capability_contract.py` — pure rules.** Input validation, result normalization,
  side-effect admission comparison, async-handler detection. Stateless functions over a
  `CapabilitySpec` and its inputs; imports models + stdlib only.
- **`engine/invocation_supervision.py` — time and cancellation.** Execution-window resolution
  (enforcement-first; intersects capability limit, run remaining, per-task request, and the
  parent's remaining soft budget), the ambient nested-window publish/reset scope, bounded
  awaiting with cancellation containment (timeout → honest PARTIAL; suppressed cancellation →
  containment FAILURE; caller cancellation propagates untranslated). Its only engine collaborator
  is `execution_window.py`, which remains the window-arithmetic owner.
- **`engine/capability_observation.py` — projection only.** Start/terminal trace events and
  linked payload/result/error/artifact details. It receives ALREADY-DECIDED facts and resolves
  the runtime's CURRENT trace sink and observation capture at each record (consumers may replace
  `runtime.trace_sink`/`runtime.observation` after construction). It never selects a status,
  branch, timeout, budget decision, or return value — observation is output, never control.

Unchanged ownership: budgets and usage stay in `budget.py`/`usage.py`/`usage_events.py`; node
result commitment stays in the executor; subprocess settlement stays in `engine/process_io.py`.
These module names are INTERNAL — consumers keep importing the same public surface and never
need the owner split as a concept.

### Node-Context Binding Tiers

Three invocation tiers, one door underneath — WHICH tier an invocation uses decides what
context it may receive:

- **Universal door — `CapabilityRuntime.invoke`.** Every capability call ends here (validation,
  execution windows, budgets, observation). It has no node knowledge.
- **Declared-node door — `NodeExecutionServices.invoke_bound` / `gather_bound`.** Node handlers invoke their
  DECLARED capability through this decorator: it composes the node's context — typed retrace
  provenance (consumed exactly once, by the retraced invocation), `inject_plan`/`inject_machine`
  cards, `memory` config (delivered as `context.metadata["agent_memory"]`), and `model_profile`
  binding (with an honest `model_binding` trace decision) — then uses the universal door. Human
  nodes are declared nodes and use this door like step/branch/evaluate (corrected in the
  `engine-v0.11.3` release candidate), with the
  resume event merged into metadata before decoration so both compose. Decorations bind at
  INVOCATION time only: resolution paths that perform no invocation — the declared wait-timeout
  transition — never enter the capability, decorated or not.
- **Dynamic selection — base/task context BY DESIGN.** A capability selected inside another
  node's execution (the evaluator's dynamic fallback, planner-task capabilities) is a DIFFERENT
  capability, not the declaring node: it must not silently inherit that node's model profile,
  memory, or injection flags. The evaluator fallback therefore runs on the base run context —
  criticism content still rides its payload — and planner tasks run on their task-specific
  context. An EXPLICIT fallback-binding schema would be an extension-lifecycle change on a
  concrete consumer request, not a default.

The binding matrix is closed and graph-validated; configuring an unsupported field fails instead
of silently doing nothing:

| Node kind | Plan card | Machine card | Memory | Model profile | Invocation shape |
|---|---:|---:|---:|---:|---|
| `step`, `branch`, `evaluate`, `human`, `planner` | yes | yes | yes | yes | one bound capability call |
| `fanout` | yes | yes | yes | yes | every item independently crosses `gather_bound` |
| `subworkflow` | yes | yes | rejected | rejected | cards enter the child context; child nodes bind their own capability settings |
| evaluator dynamic fallback | no inheritance | no inheritance | no inheritance | no inheritance | base run context by design |
| planner-created task | task context | task context | task declaration | task binding | planner-task execution contract |

`WorkflowDefinition.validate_graph()` consumes the same `NODE_CONTEXT_BINDINGS` matrix used by
the builder-facing contract tests. Adding a node kind or binding field requires updating the
matrix, its handler, and one public execution test together.

The workflow and observation runtimes follow the same ownership rule. `WorkflowExecutor` is the
coordinator; it does not reimplement its collaborators:

| Internal owner | One reason to change | Must not own |
|---|---|---|
| `machine_compiler.py` | Validate and compile one definition, including digest-keyed cache and routes | Run lifecycle, node results, observation |
| `node_services.py` | Supply the narrow node execution/scheduling contract and child-run port | Whole-executor access or product orchestration |
| `suspension.py` | Build identity-sealed snapshots and register durable waits before exposure | Scheduling clocks or product persistence |
| `result_assembly.py` | Project final state into the public result envelope | Execute nodes or choose transitions |
| `observation_contract.py` | Persisted schema and safe path/identity rules | Filesystem sequencing or HTML |
| `observation_writer.py` / `observation_retention.py` | Write/finalize/archive and prune logical run groups | Runtime control or viewer projection |
| viewer `event_source.py` / `grouping.py` | Read strict bundle facts and select canonical lifecycle history | Status invention or presentation |
| viewer `projection.py` | Reduce persisted typed events into graph/view truth | Storage or HTML transport |
| viewer `rendering.py` / `server.py` | Escape/render projected truth and serve validated resources | Recompute machine status or canonical history |

These are ownership boundaries, not consumer extension points. Product work normally lands as a
registered capability, declared node/workflow, optional L2/L3 pack, or injected product adapter.
Change an internal owner only for a universal mechanic that cannot be expressed through those
public seams. Do not add executor branches for product behavior, a generic event bus, a service
locator, or a forwarding facade that leaves two implementations alive.

Preservation evidence (v0.11 latest-only line): a sealed 28-scenario CURRENT-contract oracle
runs the full behavior inventory through the public door and is locked as committed fixtures
(`invocation_current_contract.json`, `public_surface_current.json`) with a provenance hash chain,
so `./test.sh unit -- packages/ai_workflow_engine/tests/test_current_contract_gate.py` re-checks
the live corpus against the seal hermetically (no git/network) and any un-resealed edit to the
oracle, fixtures, sealer, or intentional-break ledger fails loudly. Resealing refuses any delta
not covered by an ACTIVE ledger row, and the v0.10.1→v0.11 transition closed with corpus deltas 0
and all 68 public-surface deltas mapped to (now spent) ledger rows; the old-wheel differential and
its baseline artifacts are deleted — historical lines are inspected with their historical tags. One-door enforcement is locked by
AST guards (`tests/test_one_door.py`) as an ACCIDENTAL-BYPASS lock: every ordinary call shape —
direct chained `registry.get(...)[1](...)`, unpacked or subscript-bound handlers, whole-pair
calls, and simple function-local registry aliases — fails the suite (each family has a permanent
probe), while inspection-only lookups stay legal. It is a static guard against ordinary code, not
a whole-program security proof.

## Guidance Propagation

User guidance is not consumed by one early parser.

`[i ...]` and free-form guidance become structured constraints/preferences on the goal and product
state, then flow into later prompts where relevant.

Example:

```text
[i visual funny] explain Bernoulli principle
```

Expected propagation:

- card planner sees visual/funny as constraints/preferences;
- visual scenario planner receives `style_preference="funny"`;
- image prompt includes the humor/style request while preserving factual constraints;
- evaluator checks that humor did not damage study quality or invent facts.

## Closed Loop

Every serious workflow should be able to complete this loop:

```text
plan
  -> scenario
  -> tool/render
  -> evaluate
  -> accept | repair | retry | retrace | fallback
```

The loop must be bounded. Every retry/retrace path has a max attempt count and records why it ran.

## Scenario And Renderer Boundary

Current Anki rule:

- card-set planning chooses one card family for the input: `basic`, `cloze`, or `visual_basic`;
- each family has its own scenario planner because the useful planning questions differ by type;
- renderers run after scenario planning and convert the planned scenario into Anki front/back/media
  fields;
- visual rendering is deterministic: the visual scenario must already contain exact
  `question_text`, exact `answer_text`, layout, and image prompt. The renderer only attaches the
  generated image to the front or back according to layout;
- text and cloze rendering are branch-specific wrappers over one shared Anki-card LLM renderer.
  This avoids duplicating Anki JSON/repair rules while the scenario prompts carry most type-specific
  behavior.

Card-count policy is coverage-first:

- the planner counts independent study objectives, not source bullets;
- sibling items under one heading/category are treated as one set when that is the study objective;
- one grouped card is acceptable only when the answer covers the complete planned set or grouped
  categories;
- if one answer is unreadable, split into 2-3 logical groups rather than one card per item by
  default;
- quality evaluation rejects one-card outputs that silently sample one item from a planned set.

Pros of the current split:

- keeps the expensive/generative decision work in the scenario stage where branch-specific context is
  clearest;
- keeps visual cards from being rewritten by a generic text-card renderer after image planning;
- keeps Anki packaging/front-back media placement deterministic and easy to test;
- avoids three nearly identical renderer prompts for basic/cloze while card quality is still driven
  by per-type scenario planners.

Risks to watch:

- shared text/cloze renderer can still drift from the scenario guide, especially for cloze deletion
  quality;
- if basic and cloze renderer behavior diverges more, split the final renderer prompts too instead of
  adding conditional complexity to one prompt;
- front/back leakage rules must be side-aware: front/question content must not reveal the answer,
  but a back-side generated image should explain or reveal the answer relationship as a mnemonic.

Cost guard rule:

- all paid provider calls must go through metered app wrappers, not raw product-code API calls;
- workflow runs carry a local `WorkflowUsageSummary` with per-call model, node, attempt, tokens,
  request id, image call count, and estimated USD when pricing is known;
- paid tool nodes need an explicit per-run cap before they are wired into production;
- text and image call counts have configurable per-run caps;
- USD caps are enforced locally when estimates are available; LangSmith is optional observability,
  not the runtime budget guard;
- image generation defaults to one generation attempt per run in the Anki workflow;
- model-based quality repair defaults to one repair/retry loop per run;
- fallback cards skip an extra LLM quality pass by default after deterministic validation;
- budget exhaustion routes to fallback or failure, never another paid retry.

## What Should Stay Product-Specific

Do not make the engine understand Anki fields, video inventory schemas, Telegram reply formats, or
OpenAI image prompts.

For the home-inventory example, the same engine could run:

```text
WorkflowGoal("build searchable home inventory from video")
  -> source analyzer
  -> frame sampler
  -> object/location extractor
  -> room/container relation planner
  -> deduplicator
  -> quality evaluator
  -> searchable index writer
```

The reusable part is orchestration, model profiles, retries, tracing, and artifact handling. The
inventory schemas and prompts stay in that product workflow.

For the calendar-builder example, the same engine could run:

```text
WorkflowGoal("build a realistic calendar for the next week")
  -> availability reader
  -> task/focus-project reader
  -> energy and priority interpreter
  -> schedule planner
  -> deterministic constraint optimizer
  -> conflict and overload evaluator
  -> repair/retrace if overbooked or priority coverage is poor
  -> calendar write or user-review adapter
```

The reusable part is still the supervisor, capability runtime, structured LLM wrappers, model
profiles, retrace policy, trace, budgets, artifact/state lifecycle, and human clarification. The
calendar product owns task schemas, calendar APIs, user preference models, scheduling rubrics, and
final write/review UX.

For MageQA, the same engine should drive a supervisor that can choose browser agents, deterministic
test scripts, screenshot analysis, performance checks, accessibility checks, and report generation.
For GoPro, it should drive evidence strategies, frame/video processing, drop-stale scheduling,
home-inventory extraction, and index writing. These projects differ in tools and schemas, not in the
runtime pattern.

## Degradation Prevention Gates

These are architectural gates, not optional implementation hygiene:

- no unregistered tool or provider call inside a workflow;
- no raw provider calls that bypass usage, budget, timeout, retry, and trace wrappers;
- no silent fallback: every downgrade records the failed step, reason, criticism, and selected fail
  mode;
- no unbounded agent loops, fan-out, retries, retrace, or recursive subworkflows;
- no product-specific field names in core engine APIs;
- no product media bytes stored in shared state when an `EvidenceRef`/artifact reference is enough;
- no fallback that changes the user-visible meaning without an evaluator decision;
- no branch that spends money without per-run caps and visible cost accounting;
- no prompt drift that puts dynamic user content before reusable static instructions when caching is
  possible;
- no "works on Anki" claim for open-ended agentic control or streaming/durable lifetime until
  MageQA/GoPro exercise those axes.
- no obsolete behavior, data shape, config alias, or compatibility bridge preserved during
  architecture work unless Artem explicitly approves it.

The implementation plan converts these into tests and acceptance gates.

## Architecture Health Backlog

These are accepted framework-hardening tasks, not product blockers unless a direct bug appears:

1. **H1 executor/node service boundary.** Extract a `NodeExecutionServices` protocol so node handlers
   use a narrow service surface instead of the full executor. Then enforce the executor/node
   coupling guard.
2. **H2 run-session lifecycle.** Separate long-lived engine application state from per-run session
   state: run id, observation bundle, usage scope, trace/detail sinks, checkpoint/resume metadata.
   Keep `engine.run(...)` as the public API first.
3. **H3 usage/accounting split.** Split budget gates, usage-event recording, provider usage
   extraction, pricing, token estimation, and usage rendering while preserving `usage.py`
   compatibility exports through the next tag.

Sequence: H0 baseline, H1, guard batch, H2, H3, H4 verification/cleanup. External product-facing
guards can land in parallel because they are check-only; the executor/node cycle guard waits for H1.

## Non-Goals

- No no-code n8n clone.
- No generic prompt that can solve every workflow.
- No thin interface package that pushes the real engine into client applications.
- No script menu without goal, evaluator, trace, and budget control.
- No cross-project publishing before a second workflow proves the boundary.
- No product-level restart/recovery guarantee until retries, user review, or long-running tools
  prove it is needed and a product maps engine checkpoints back to domain state.
- No mixing several unrelated product-specific state schemas into the engine.

## Capability Coverage Model — how Anki informs and validates the framework

Why this section exists: a reviewer (including future-me after context loss) reading the flat
primitive list above can wrongly conclude "Anki only exercises half of these primitives, so the
framework has no concrete source material." That conclusion is false and was reached once during
review. Every target engine primitive below is a **generalization of a concrete behavior that already
exists in the Anki flow**.

Be precise about the claim: before Phase 1 defines the generic contracts, Anki is the concrete
source workload and regression gate. After the primitive is implemented and wired into the Anki
flow, Anki validates that primitive's Anki-shaped contract/data-model instance. Anki structurally
fails to stress only **two execution axes** — everything else should be proven by toy tests first and
then by the Anki migration. Keep this table updated when a primitive is added.

| Engine primitive | Concrete Anki instance | What Anki validates | Stress mode Anki can't reach → validator |
| --- | --- | --- | --- |
| Capability runtime / registry | every planner/renderer/generator/evaluator | typed invoke, schema, trace, usage | open selection among many tools → MageQA |
| Supervisor / decision gate | card-set planner picks basic/cloze/visual | LLM decision gate over a **closed** set | self-directed action loop over an open set → MageQA |
| `WorkflowProfile` → `RuntimePlan` | `[i ...]` + content_mode → constraints | profile parse → structured constraints → routing; unknown keys dropped(=warn) | rich multi-strategy profiles → GoPro |
| External side-effecting capability (timeout/cost/artifact/salvage/cleanup) | image-gen + voice-gen calls | bounded external call, budget, artifact capture, failure routing, cleanup | autonomous agent loop + raw process I/O → MageQA |
| Fan-out / gather | multiple cards / could gen multiple images | child set, per-child trace/budget, failure isolation (serial) | true concurrency race (1-of-N timeout) → MageQA (or parallel image-gen) |
| Evaluator / retry / retrace / fallback | quality evaluator → repair/retry-scenario/retry-plan/fallback | full accept/repair/retry/retrace/fallback under caps | — (fully validated on Anki) |
| `EvidenceRef` / roles | uploaded image bytes + OCR/summary in `ContentSource` | evidence carried + roled through stages | refs-not-bytes at scale/privacy → GoPro |
| Human clarification (`ask_user`) | 5s auto-mode correction window (guess → buttons → user picks → continue) | emit clarification, await human answer, continue on choice | durable pause/resume across a long gap → GoPro/checkpointing |
| `FailMode` (fail_open/closed) | text fallback on degradation | policy-routed failure | fail-closed "block, don't degrade" safety gate → MageQA/GoPro |
| Trace / budget / model profiles | per-node trace, `WorkflowUsageSummary`, per-stage models | tracing, metering, budget caps, model selection | — (validated on Anki) |

The **two irreducible axes** Anki cannot stress (because card-gen is a closed-decision, single-shot
domain — this is "Anki's domain doesn't need it", not "the framework can't be tested"):

1. **Open-ended agentic control** — an agent choosing its own next action from an open/uncertain
   space and looping on it. Anki's decisions are closed sets; its loops are fixed graph cycles.
   Validated by **MageQA** (autonomous browser agents).
2. **Long-lived / streaming / resumable run lifetime** — live drop-stale/single-flight-cancel under a
   continuous stream, durable suspend/resume across long gaps, multi-turn evolving session state.
   Anki runs are ephemeral and single-shot. Validated by **GoPro** (live camera hot path).

Human-in-the-loop is not absent from the product: the 5s correction window is now the concrete source
behavior for `ask_user`, using `HumanClarificationCapability` with Telegram as the product-owned
channel. This proves short correction-window clarification; durable pause/resume across long gaps
still belongs to the GoPro/checkpointing pressure test.

Workload complementarity (none redundant): **Anki** proves the typed pipeline core after migration;
**MageQA** proves open-ended agency; **GoPro** proves continuity; the **calendar-builder toy
workload** proves multi-constraint planning/optimization without media or browser automation. The
two Anki-unstressable axes map onto MageQA and GoPro, while calendar-building prevents the engine
from overfitting to card generation and media-heavy workflows.

Honest residual risk: the agentic-autonomy and streaming/durable-lifetime code is built and
toy-tested, including fake-backed site-audit and inventory pilots, but is not battle-tested until
MageQA/GoPro wire in real browser/camera/domain adapters (Phases 7–9). Do not call those two axes
production-ready off package pilots alone.

## Current Fit Audit

Status checked on 2026-06-08 (coverage model + scope decision added 2026-06-07).

| Concept | Current fit | Remaining limit |
| --- | --- | --- |
| Reusable engine, not Anki-only code | `packages/ai_workflow_engine` is an installable internal package with `models`, `engine`, `usage`, `prompt_loader`, capability runtime, scheduler, loop controller, generic evaluator controller, external process wrapper, external/domain write adapter, uncertainty result envelope, and media/voice seams. It avoids TGHandyUtils app config/logging/exception/LLM-factory imports. Public engine names/model fields are guarded against product-specific terms; Telegram correlation IDs now live in product-supplied metadata instead of shared fields. Anki schemas/prompts/rendering live outside the package. Fake-backed site-audit and inventory pilots prove the package can express non-Anki workloads. | The engine is still internal to this repo, not a separate git dependency. External extraction waits for real second-product adoption or an explicit user decision. |
| Master/supervisor style orchestration | `WorkflowSupervisor`, `WorkflowDecisionPlanner`, `WorkflowInstrumentRegistry`, and `WorkflowLoopController` can choose among registered instruments/capabilities and run bounded multi-step loops. Anki graph nodes invoke through `CapabilityRuntime` with a compiled `RuntimePlan` capability inventory. | Telegram still uses command/settings/auto routing directly. Anki graph topology is product-authored rather than dynamically assembled by an autonomous supervisor, which is intentional for the first production workflow. |
| AI decision gates inside product flow | Anki has an AI card-set planner that chooses `basic`, `cloze`, or `visual_basic`, image policy, count, source facts, and fallback. | If the planner fails, the graph uses a conservative deterministic fallback. |
| Per-type scenario preparation | Text, cloze, and visual branches have separate scenario planners with branch-specific prompts and schemas. | Mixed card families from one input are intentionally out of scope for the first workflow. |
| Flexible but controlled branching | LangGraph conditional edges route by card kind, visual generation need, validation, quality decision, card-plan retry, repair, scenario retry, and fallback. `WorkflowRunner` forwards explicit graph runtime config, detects graph recursion exhaustion, and delegates to workflow-owned fallback policy. Anki sets a recursion limit sized from retry/media caps and has an adversarial tiny-limit test proving fallback without leaked `GraphRecursionError`. | The graph shape is explicit and typed; it is not dynamically assembled by an autonomous agent. Second-product adapter battle testing remains separate from the current package/Anki proof. |
| Bounded retry and repair | Structured LLM nodes retry invalid JSON once; the generic `EvaluationController` can retry one failed capability with criticism, retrace to an earlier capability, route to fallback/repair, ask the user, or fail under explicit caps. Anki quality rejection can retry the card-set planner, retry per-type scenario planning, repair rendering, or fall back under a run cap; image generation has run caps. Rejections emit generic `EvaluationDecision` metadata. | Product-level restart recovery is deferred until long-running tools or restart loss become measured problems. |
| Prompt-cache-aware LLM calls | Structured nodes, the Anki renderer, classifier, task parser, timezone parser, and image analyzer use stable system/static prefixes followed by dynamic user/source tails. Usage summaries expose cached input tokens when providers return them. | OpenAI chat cache-affinity hints are enabled through the provider adapter when `OPENAI_PROMPT_CACHE_KEY_PREFIX` is set; retention remains optional. Add provider-specific controls only through adapters after cross-provider tests. |
| Generated image support | Switchable image provider seam exists, generated image cards can be packaged and previewed, and style/source references are supported for OpenAI and Gemini image models. | Explicit `[i gen]` image generation is enabled by default; AI-chosen automatic generation remains opt-in. Broad live visual quality eval remains manual/API-backed, not a full golden-set benchmark. |
| Uploaded image handling | Uploaded image bytes are preserved as source assets; planner can reuse, ignore, or use them as references. | Image understanding happens upstream before `ContentSource`; the graph does not independently call a vision analysis step if upstream analysis is missing. |
| Cost safety | Image generation and quality repairs are capped; fallback avoids paid retry loops by default; `./test.sh integration` is fail-closed unless `ALLOW_PAID_TESTS=1` is explicitly set. | No per-user/day budget ledger yet. Add only if usage volume requires it. |
| Persistence/restart recovery | Generated local artifacts are tracked and cleaned on abandoned branches; engine checkpoint stores exist for loop progress. | Product-level restart recovery is deferred until long-running tools or restart loss become measured problems. |

Conclusion: the current implementation is ready as the current **package + Anki validation
milestone**:
capability/runtime/profile/evidence/evaluator-controller/trace/external/fan-out/scheduler/supervisor-loop
primitives exist in the reusable package and are toy-tested; fake-backed site-audit and inventory
pilots cover MageQA/GoPro-shaped pressure; Anki graph nodes run through the generic
`CapabilityRuntime` with a compiled `RuntimePlan`; and Telegram auto-mode clarification uses
`HumanClarificationCapability`. The package can be handed to MageQA/GoPro for adapter binding.
Two axes — open-ended agentic control and streaming/durable run lifetime (see the Capability
Coverage Model above) — remain downstream adoption risks until MageQA and GoPro wire real
browser/camera/domain adapters. Do not describe those two axes as production-ready before that
second-product evidence exists.

Extraction readiness test coverage:

- `tests/unit/test_workflow_engine.py::test_engine_public_contract_has_no_product_specific_names`
  proves public exports and shared model fields stay product-neutral.
- `tests/unit/test_workflow_engine.py::test_workflow_runner_forwards_optional_graph_config` and
  `tests/unit/test_anki_generation_graph.py::test_graph_invokes_runner_with_explicit_recursion_limit`
  prove graph runtime controls can be passed explicitly and Anki does not rely on LangGraph's
  default recursion cap.
- `tests/unit/test_workflow_engine.py::test_workflow_runner_routes_recursion_exhaustion_to_policy_fallback`
  and
  `tests/unit/test_anki_generation_graph.py::test_graph_recursion_exhaustion_uses_text_fallback_instead_of_leaking_error`
  prove graph recursion exhaustion routes through a workflow-owned fallback instead of leaking
  `GraphRecursionError`.
- `tests/unit/test_workflow_engine.py::test_engine_runs_toy_non_anki_workflow_through_supervisor_and_runner`
  proves the supervisor/runner can execute a non-Anki workflow.
- `tests/unit/test_workflow_engine.py::test_workflow_loop_controller_runs_supervisor_style_steps_to_completion`
  proves the generic loop controller can drive multiple capability decisions to completion.
- `tests/unit/test_workflow_engine.py::test_workflow_loop_controller_fails_closed_on_step_limit`
  proves supervisor loops are bounded and fail closed when no terminal decision appears.
- `tests/unit/test_workflow_engine.py::test_workflow_supervisor_selects_different_instruments_for_different_goals`
  proves the supervisor can select different instruments for different goals instead of acting as a
  fixed linear runner.
- `tests/unit/test_workflow_engine.py::test_evaluation_controller_retries_one_capability_with_criticism_then_accepts`,
  `test_evaluation_controller_retraces_to_earlier_capability_with_criticism`, and
  `test_evaluation_controller_falls_back_or_fails_when_policy_exhausts` prove evaluator-driven
  retry, retrace, fallback, and cap-exhaustion behavior.
- `tests/unit/test_workflow_engine.py::test_workflow_scheduler_modes_priority_coalesce_and_backend_caps`
  proves scheduler mode coverage for replay priority, coalescing, live latest-only, drop-not-queue,
  fan-out gather, and backend caps.
- `tests/unit/test_workflow_engine.py::test_external_adapter_capability_writes_idempotently` and
  `test_jsonl_external_write_sink_records_machine_readable_audit` prove the domain-write adapter is a
  real idempotent capability with audit output, not just an interface.
- `tests/unit/test_workflow_engine.py::test_structured_llm_node_requires_injected_llm_or_factory`
  proves provider construction is not hidden inside the engine.
- `tests/unit/test_prompt_loader.py::test_prompt_template_loader_accepts_custom_root` and
  `test_default_prompt_loader_accepts_custom_root` prove prompt roots are project-injectable.

## References

- LangGraph overview: <https://docs.langchain.com/oss/python/langgraph/overview>
- LangGraph workflows and agents: <https://docs.langchain.com/oss/python/langgraph/workflows-agents>
- LangGraph persistence: <https://docs.langchain.com/oss/python/langgraph/persistence>
- Pydantic AI agents: <https://pydantic.dev/docs/ai/core-concepts/agent/>
- Pydantic AI output: <https://pydantic.dev/docs/ai/core-concepts/output/>
- Gemini image generation: <https://ai.google.dev/gemini-api/docs/image-generation>
- Gemini API pricing: <https://ai.google.dev/gemini-api/docs/pricing>
