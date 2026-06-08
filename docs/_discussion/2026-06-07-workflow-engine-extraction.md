# Workflow Engine Extraction And Supervisor Runtime

Status: superseded coordination artifact; durable decisions live in
`docs/workflow-engine-architecture.md`, `docs/workflow-engine-implementation-plan.md`,
`docs/workflow-engine-config-architecture.md`, and `docs/workflow-engine-acceptance-criteria.md`
Created: 2026-06-07 19:41:16 WEST
Updated: 2026-06-07 20:13:11 WEST
Permanent home if any: `docs/workflow-engine-architecture.md`,
`packages/ai_workflow_engine/README.md`, possibly MageQA
`docs/17-qa-orchestrator-architecture.md`, and possibly GoPro
`docs/architecture/workflow-execution-engine-requirements.md`

## Cross-Referenced Source Documents

This discussion intentionally cross-checks three real workloads. Use these source documents when
reviewing or implementing the engine; do not rely on chat memory alone.

TGHandyUtils / Anki:

- [Workflow engine architecture](/Users/artemm/PycharmProjects/TGHandyUtils/docs/workflow-engine-architecture.md)
- [AI workflow engine README](/Users/artemm/PycharmProjects/TGHandyUtils/packages/ai_workflow_engine/README.md)
- [Anki acceptance criteria](/Users/artemm/PycharmProjects/TGHandyUtils/docs/anki-acceptance-criteria.md)
- [Anki implementation plan](/Users/artemm/PycharmProjects/TGHandyUtils/docs/anki-implementation-plan.md)
- [Anki prompt quality review](/Users/artemm/PycharmProjects/TGHandyUtils/docs/anki-prompt-quality-review.md)
- [Anki PPLA visual identity](/Users/artemm/PycharmProjects/TGHandyUtils/docs/anki-ppla-visual-identity.md)

MageQA:

- [QA orchestrator and superagent design](/Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md)
- [Agentic tester architecture](/Users/artemm/PycharmProjects/MageQA/docs/14-agentic-tester-architecture.md)
- [MageQA system architecture](/Users/artemm/PycharmProjects/MageQA/docs/02-architecture.md)
- [Run inspector dashboard](/Users/artemm/PycharmProjects/MageQA/docs/14-run-inspector-dashboard.md)

GoPro / universal descriptor:

- [Workflow execution engine requirements](/Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md)
- [Universal profile sheet](/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/UNIVERSAL_PROFILE_SHEET.md)
- [Home inventory case](/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/HOME_INVENTORY_CASE.md)
- [Universal descriptor architecture](/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/ARCHITECTURE.md)
- [Agentic reasoning layer](/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/AGENTIC_REASONING_LAYER.md)
- [Observability and eval](/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/OBSERVABILITY_AND_EVAL.md)

## Settled Direction

User-confirmed direction: build a fully functional reusable framework, prove it on the current
Anki flow, then ship it to the MageQA implementer with clear integration instructions.

This is not a "coordinator-only first" target. Coordinator-only may be a temporary compatibility
checkpoint during implementation, but the framework target must cover:

- Anki full flow: planning, provider calls, rendering, QA, retrace, fallback, packaging handoff;
- MageQA full flow: plan, bounded browser-agent dispatch, fan-out/gather, deterministic hooks,
  QA review, deepen/re-dispatch, curation, report handoff;
- GoPro / streaming full flow: profile compilation, runtime plan, evidence strategy selection,
  bounded tool/agent loop, task state, clarification, domain-adapter handoff, scheduling policy,
  trace/audit, and replay-friendly outputs;
- reusable image/voice provider capabilities;
- reusable agent/subprocess capability with safety, tools, tracing, budgets, and partial salvage.

The framework is considered shippable only when Anki runs through it without functional regression
and MageQA/GoPro migration packages exist.

## Universality Requirement

The engine must be designed as a general workflow framework first. Anki and MageQA are validation
workloads, not the shape of the abstraction.

The public API must not contain product-specific concepts such as cards, clozes, Telegram,
scenario specs, browser findings, Magento, screenshots, or reports. Those belong in product
capability packs. Engine contracts should speak in generic terms:

- goal;
- workflow profile;
- runtime plan;
- capability;
- subworkflow;
- tool;
- input/output schema;
- trigger/evidence strategy;
- evidence ref and evidence role;
- task/session state;
- runtime limits;
- scheduling policy;
- safety policy;
- fail mode;
- external/domain adapter;
- clarification request;
- artifact;
- trace;
- usage;
- evaluation decision;
- retry/retrace/fallback;
- provider request/result.

Every new engine primitive must pass the "third project" test: it should make sense for a future
workflow such as home-inventory video processing, document analysis, customer-support triage, or
voice/video report generation without importing Anki or MageQA ideas.

Product-specific examples are allowed in docs and tests only as validation cases. They must not
determine names, base classes, runtime fields, or framework invariants.

## Current Architecture Requirements

These are not future nice-to-haves. They must shape the first framework implementation, otherwise
the engine will become Anki-specific plumbing again and will need a redesign before MageQA can use
it:

- capability specs with schemas, safety, runtime limits, side-effect metadata, and prompt refs;
- capability packs for skills/domains/providers;
- workflow profiles that compile into inspectable runtime plans with warnings/errors;
- evidence refs and evidence roles; raw bytes stay outside task state unless a permitted tool
  explicitly fetches them;
- task/session state lifecycle with provisional values, confidence, evidence refs, and traceable
  mutations;
- scheduling/concurrency policies for live latest-only, bounded queue, manual priority,
  coalescing, replay/process-all, single-flight, and per-backend limits;
- tool allow-lists with side-effect classes and hard denial traces;
- explicit fail modes such as `fail_open`, `fail_closed`, and product-defined equivalents;
- external/domain adapters that receive structured observations and evidence refs, not hidden
  domain writes;
- human clarification as a traceable capability/tool, with pluggable channels;
- structured LLM capabilities for decision gates and JSON-producing steps;
- recursive subworkflow support through the same runtime;
- evaluator decisions that can retry, retrace, fallback, fail, or later ask a user;
- criticism/feedback passed back to the retried/retraced capability;
- trace sink and artifact ownership from the first version;
- budget inheritance across parent/child workflows;
- provider-neutral image and voice capabilities.

Implementation can be phased, but the data model and public API must be designed for these from the
start.

## Engine Soul And Values

The framework's soul is a reusable, typed, budgeted, traceable AI workflow runtime. It should feel
like a programmable workflow engine with AI decision gates, not a single giant agent prompt and not
a bag of hardcoded scripts.

Values:

- **Goal-first:** every run starts from a `WorkflowGoal` and explicit constraints.
- **Universal primitives, product packs:** engine concepts stay product-neutral; Anki, MageQA, and
  future projects plug in capability packs.
- **Capabilities over scripts:** every callable operation is a typed registered capability with
  schema, limits, safety, tracing, and side-effect metadata.
- **AI where judgment helps:** use LLMs for planning, scenario writing, QA judgment, curation, and
  open-ended browser/design work; keep deterministic invariants in code.
- **Product meaning stays local:** Anki owns card quality; MageQA owns finding validity; the engine
  owns execution, routing, retry, retrace, budgets, and trace.
- **Recursive but bounded:** subworkflows can call other capabilities/subworkflows, but only through
  explicit limits, depth caps, tool policy, and traceable child contexts.
- **Observable by default:** every branch, model call, tool call, artifact, retry, fallback, and cost
  decision must be reconstructable from trace.
- **No silent degradation:** if a capability is blocked, times out, falls back, or returns partial
  output, the parent workflow sees that as structured state.
- **Provider-neutral media:** image and voice generation are reusable provider capabilities. Product
  workflows decide when they are useful and how to attach results.
- **Framework small, products rich:** the engine should be powerful enough to compose workflows, but
  should not absorb product prompts, schemas, rubrics, rendering, or delivery.

Anti-goals:

- no no-code/untyped arbitrary graph builder;
- no uncontrolled recursive agent loop;
- no Anki/MageQA nouns in engine APIs;
- no supervisor with unrestricted access to every low-level tool;
- no product-specific quality rules inside the engine;
- no hidden paid calls or hidden file side effects;
- no "best effort" fallback that changes behavior without trace.

## Capability And Skill-Pack Model

Capabilities are the universal tool definition.

The same `CapabilitySpec` must describe an Anki card planner, a MageQA browser agent, a TTS
provider, an image generator, a video summarizer, or a deterministic Python parser. If a proposed
field only works for one product, it belongs in `metadata` or the product input/output schema, not
in the core spec.

Minimum capability contract:

```python
class CapabilitySpec(BaseModel):
    name: str
    kind: Literal["llm", "python", "agent", "provider", "subworkflow", "evaluator"]
    description: str
    input_schema_ref: str
    output_schema_ref: str
    safety_policy: SafetyPolicy
    runtime_limits: RuntimeLimits
    retry_policy: RetryPolicy
    artifact_policy: ArtifactPolicy
    side_effects: list[str] = []
    prompt_refs: list[str] = []
    child_capabilities: list[str] = []
    tags: list[str] = []
    examples: list[dict[str, Any]] = []
```

Runtime invocation contract:

```python
async def invoke(ctx: CapabilityContext, input_model: BaseModel) -> CapabilityResult: ...
```

`CapabilityResult` must include status, typed output, artifacts, usage, trace refs, partial-output
metadata, and error/fallback reason.

Skills should be represented as **capability packs**, not as a different execution primitive.

Examples:

- `media_generation_pack`: image generation, image comparison, voice generation;
- `anki_generation_pack`: Anki card planning/scenario/render/evaluation/package capabilities;
- `mageqa_browser_qa_pack`: QA coordinator, browser-agent executor, adjudication/review/report
  capabilities;
- future `home_inventory_pack`: video/frame extraction, object detection, location summarization,
  inventory QA.

A capability pack contains:

- capability specs;
- prompt templates;
- schemas;
- static instructions/policies;
- examples;
- optional reference assets;
- test fixtures;
- handoff docs.

Parent supervisors should normally see a capability's high-level card:

- name;
- description;
- input/output schemas;
- cost/safety/side-effect class;
- examples;
- allowed child capabilities.

For debugging or deeper supervisor investigation, the runtime may expose introspection:

```text
describe_capability(name, level="summary|schema|debug")
```

`summary` and `schema` are safe for normal planning. `debug` can expose prompt refs, child graph
shape, and previous trace, but should not be needed for ordinary routing.

Prompt wiring rules:

- prompts are attached to capabilities, not hidden in random service classes;
- static/system prompt, schema, style policy, and examples come first for cache affinity;
- dynamic user/source/tool state comes later;
- product prompts live in product packs;
- engine owns only generic repair/supervisor/evaluator wrapper prompts;
- structured LLM capabilities own parsing, repair, validator hooks, usage, and trace.

Decision gates and JSON producers are the same primitive:

```text
StructuredLLMCapability[input_model, output_model]
```

The product chooses the schema; the engine supplies validation, retry, usage, and trace.

## Workflow Profile And Runtime Plan Model

GoPro's workflow-engine requirements make one missing abstraction explicit: a `WorkflowGoal` alone
is not enough for live, profile-driven, evidence-based workflows.

The public run input should be closer to:

```python
runner.run(
    WorkflowInput(
        profile=WorkflowProfile(...),
        trigger=WorkflowTrigger(...),
        evidence_refs=[...],
        session_state={...},
    )
)
```

The compiler turns `WorkflowProfile` into `RuntimePlan` before execution. The plan is an audit
artifact and must include:

- selected trigger/evidence strategies;
- selected tools/capabilities and hard allow-list;
- model/backend policy;
- budgets, timeouts, and scheduling policy;
- fail mode;
- output schemas;
- side-effect permissions;
- privacy/retention flags;
- external/domain adapters;
- trace settings;
- compiler warnings/errors and rationale.

Unsupported profile keys are not harmless. They must become warnings or hard errors according to
profile strictness, because GoPro's profile-sheet docs show a real no-op risk when public sheet keys
do not map to runtime behavior.

Evidence is a first-class generic object:

```python
class EvidenceRef(BaseModel):
    source_id: str
    time_range: tuple[float, float] | None = None
    uri: str | None = None
    role: Literal[
        "location_context",
        "transition",
        "contents",
        "readable_label",
        "current_frame",
        "history_sample",
        "screenshot",
        "source_document",
    ]
    quality: dict[str, Any] = {}
    privacy: dict[str, Any] = {}
    confidence: float | None = None
```

This is generic enough for Anki source images, MageQA screenshots, GoPro frames, retail labels, and
home-inventory drawer evidence.

## Recursive Subworkflow Model

Recursive capabilities make sense and are part of the target. A workflow can call a subworkflow,
and that subworkflow can use the same runtime to call its own capabilities.

Valid examples:

- **Anki visual card:** `anki_generation` calls `visual_card_subworkflow`, which calls image prompt
  planning, image provider generation, visual QA, and fallback/retry capabilities.
- **MageQA site speed:** `mageqa_audit` calls `site_speed_subworkflow`, which runs Lighthouse,
  deterministic performance checks, model summarization, and QA of recommendations.
- **MageQA UI issues:** `mageqa_audit` calls `ui_issue_subworkflow`, which can dispatch browser
  agents and visual QA.
- **MageQA design showcase:** after confirmed UI issues, `ui_issue_subworkflow` may call
  `design_mockup_subworkflow` that uses Claude/Lovable/design tooling to generate a better UI
  concept for customer-facing proof.
- **MageQA video presentation:** `report_subworkflow` may call a narration/script capability,
  voice-generation capability, and video assembly capability to produce a voice-narrated summary.
- **Home inventory:** `home_inventory_workflow` calls frame extraction, object/location detection,
  inventory merge, QA review, and "where is X" answer-generation subflows.

This is not too complex if the recursion is not magical. It is a normal capability call with a child
run context:

```text
parent workflow_id
  -> child workflow_id
       inherits budget/safety/artifact policy
       has max_depth and allowed child capabilities
       returns typed output + trace refs + artifacts
```

Recursion policy:

- every subworkflow has a declared input/output schema;
- every subworkflow has declared allowed child capabilities;
- parent passes a reduced/inherited budget, not an unlimited new budget;
- depth is capped globally and per capability;
- child artifacts are owned by the child but visible to the parent;
- parent sees child status: `ok`, `partial`, `failed`, or `blocked`;
- child fallback must be explicit and typed;
- no subworkflow can silently widen tool permissions.

Recommended default caps:

- max recursive depth: 2 or 3 until proven safe;
- max evaluator/retrace attempts per decision class: 1 by default;
- max fan-out children per node: product-configured, small by default;
- max agent/subprocess wall time: required for every agent capability;
- max estimated spend and max paid calls inherited from parent run.

## Mitigations For Framework Risks

| Risk | Mitigation |
| --- | --- |
| Uncontrolled recursive agent loops | Depth caps, step/call caps, allowed child capability lists, retrace policy, and budget inheritance. |
| Parent supervisor overuses low-level tools | Parent sees high-level capability cards by default; low-level tools remain inside subworkflows unless explicitly exposed. |
| Product logic leaks into engine | Product schemas/prompts/validators stay in capability packs; engine only owns runtime primitives and generic provider seams. |
| Prompt chaos / poor cache behavior | Prompt registry with static/dynamic split; stable instructions first; product prompts path-backed and tested. |
| Wrong JSON or schema drift | Structured LLM capability with Pydantic parsing, repair retry, validator hooks, and schema refs in capability specs. |
| Retrying the wrong step | Evaluator returns `target` plus criticism; `RetracePolicy` validates target is allowed before execution. |
| Hidden fallback/degradation | All blocked/fallback/partial states are `CapabilityResult.status` and trace events. |
| Artifact leaks across retry/fallback | Artifact ownership, cleanup policy, abandoned-branch cleanup, and child-artifact visibility. |
| Runaway provider cost | Per-run and per-capability limits for text/image/voice/tool calls plus estimated USD caps. |
| Profile keys silently no-op | `WorkflowProfile` compiler emits warnings/errors and persists the compiled `RuntimePlan` with every run. |
| Raw media leaks into generic state | Engine state stores evidence refs/roles/quality/privacy; tools fetch raw bytes only when profile policy allows. |
| Live streams backlog while model calls run | Scheduling policies define drop/coalesce/queue/manual-priority behavior and record dropped/coalesced work. |
| Domain CRUD leaks into engine | Domain writes happen only through registered external adapters with side-effect metadata and idempotency policy. |
| Fail-closed safety silently downgrades | Fail mode is part of the runtime plan; fallback cannot change `fail_closed` to `fail_open`. |
| Cancellation releases locks too early | Per-scope single-flight remains held until underlying model/tool calls have actually stopped or returned. |
| MageQA safety violations | `SafetyPolicy` passed to every agent capability; tool/MCP policy is capability metadata; product safety gate remains deterministic. |
| Poor MageQA evidence grounding | Engine records artifacts; MageQA product rules still reject ungrounded findings. |
| Trace-store overreach | Start with `TraceSink` adapter so MageQA and TGHandyUtils keep local trace storage; only add shared checkpoint store when needed. |
| Framework becomes too abstract | Prove with toy scenarios, then Anki production flow, then MageQA handoff. Do not add primitives without a test scenario. |

## Current State

TGHandyUtils now has an internal package:

```text
packages/ai_workflow_engine/
```

It currently provides useful workflow plumbing:

- `WorkflowGoal`, run context, trace/artifact/usage models;
- `WorkflowRunner`;
- `WorkflowInstrumentRegistry`;
- `WorkflowSupervisor`;
- `StructuredLLMNode`;
- prompt loading;
- usage/cost/budget tracking;
- artifact cleanup;
- provider seams for generated images and generated voice.

Superseded status: the reusable package now includes `WorkflowSupervisor`, `WorkflowLoopController`,
bounded `AgentCapability`, `ExternalProcessCapability`, `gather_capabilities`, scheduler modes, and
generic evaluator retry/retrace/fallback controller. Anki still owns its domain graph topology, but
retry/retrace semantics are now represented by generic engine models and toy-tested outside Anki.

MageQA `docs/17-qa-orchestrator-architecture.md` is the first serious agent/subprocess pressure
test for extraction. It needs a runtime that can spawn bounded agents/subflows, pass tools, collect
structured output, fan out, review partial results, deepen/re-dispatch, preserve artifacts, enforce
safety and budgets, and salvage partial output on timeout.

GoPro `docs/architecture/workflow-execution-engine-requirements.md` is the first serious
profile/evidence/live-scheduling pressure test. It needs profile compilation, runtime plans,
evidence refs, task/session state, scheduling policy, clarification, fail modes, and external
domain adapters.

## Architecture Draft

Decision: promote the library from "workflow plumbing" to a small capability runtime. Keep product
graphs explicit and typed. Do not build a free-form autonomous platform.

Target shape:

```text
WorkflowGoal
  -> SupervisorRuntime
       -> CapabilityRegistry
            - subworkflow capabilities
            - structured LLM capabilities
            - Python/script capabilities
            - provider capabilities such as image or voice generation
            - QA/evaluator capabilities
       -> plan / route / execute
       -> evaluate
       -> retry same capability, retrace to earlier capability, call fallback, or stop
  -> product-owned delivery
```

Everything callable by a supervisor should be a `Capability`.

Capability kinds:

- `subworkflow`: another graph or workflow with its own state and optional supervisor;
- `llm`: structured LLM call with schema validation;
- `tool`: external provider/API/browser/file operation;
- `python`: deterministic function or script;
- `evaluator`: QA/review gate that returns accept/retry/retrace/fallback;
- `provider`: reusable side-effect provider such as image or voice generation.

Subworkflows may be recursive, but only under explicit limits:

- max depth;
- max steps/calls;
- max time;
- max estimated cost;
- allowed tools;
- allowed retrace targets;
- artifact cleanup policy.

## What Belongs In The Library

Keep in `ai_workflow_engine`:

- product-neutral models:
  - `WorkflowGoal`;
  - `WorkflowRunContext`;
  - `WorkflowArtifact`;
  - `WorkflowUsageEvent`;
  - `WorkflowUsageSummary`;
  - `WorkflowDecision`;
  - `WorkflowTraceEvent`.
- runtime:
  - `WorkflowRunner`;
  - `WorkflowSupervisor`;
  - `CapabilityRegistry`;
  - `CapabilitySpec`;
  - `CapabilityResult`;
  - `SubworkflowCapability`;
  - `StructuredLLMCapability`;
  - `PythonFunctionCapability`;
  - `ExternalProcessCapability`.
- evaluator/retrace primitives:
  - `EvaluationDecision`;
  - `CriticismEnvelope`;
  - `RetracePolicy`;
  - `RetryPolicy`;
  - `FallbackPolicy`;
  - `RuntimeLimits`.
- provider capabilities:
  - generated image provider seam;
  - generated voice provider seam;
  - future web/search/browser provider seams only if they remain product-neutral.
- observability:
  - trace event sink interface;
  - usage/cost ledger;
  - artifact ownership and cleanup;
  - budget checks.

Image and voice generation should stay in the library because they are reusable capabilities across
projects. They should be exposed as provider-neutral capabilities, not Anki-specific helpers.

Generic config names should be preferred:

```text
WORKFLOW_IMAGE_PROVIDER
WORKFLOW_OPENAI_IMAGE_MODEL
WORKFLOW_GEMINI_IMAGE_MODEL
WORKFLOW_IMAGE_COMPARE_PROVIDERS
WORKFLOW_VOICE_PROVIDER
WORKFLOW_ELEVENLABS_VOICE_ID
WORKFLOW_MAX_TEXT_CALLS_PER_RUN
WORKFLOW_MAX_IMAGE_CALLS_PER_RUN
WORKFLOW_MAX_VOICE_CALLS_PER_RUN
WORKFLOW_MAX_ESTIMATED_USD_PER_RUN
```

Existing `ANKI_*` names can remain as product aliases in TGHandyUtils, not as the library's primary
contract.

## What Should Stay Product-Specific

Keep outside the library:

- Anki card schemas, prompts, renderers, cloze rules, PPLA visual identity, Telegram delivery;
- MageQA `ScenarioSpec`, `Observation`, `Finding`, safety rubric, grounding rules, report rendering;
- home-inventory object/location schema, room taxonomy, storage model, video frame semantics;
- product-specific graph topology;
- product-specific validators;
- product-specific acceptance criteria.

The library can run/evaluate/retrace a graph, but it must not decide what makes a good Anki card, a
valid website QA finding, or a good home-inventory answer.

## What Current Engine Covers

Covered now:

- basic goal/run context;
- instrument registry;
- supervisor that can choose registered instruments;
- structured LLM call with Pydantic parsing and one repair;
- usage/cost tracking;
- budget caps for text/image/tool calls;
- artifact cleanup helper;
- image and voice provider seams;
- prompt root injection.

Not covered enough yet:

- first-class capability abstraction with schemas, limits, safety, and side-effect metadata;
- bounded sub-agent spawn;
- external process/CLI capability with salvage-on-timeout;
- parallel fan-out/gather;
- generic evaluator decision model;
- generic retrace policy;
- recursive subworkflow execution;
- checkpoint/resume;
- trace sink adapter compatible with MageQA `ProcessingTrace`;
- tool/MCP injection policy.

## MageQA Fit Review

MageQA `docs/17-qa-orchestrator-architecture.md` minimum viable subset:

| MageQA capability | Current library fit | Gap |
| --- | --- | --- |
| Bounded sub-agent spawn | partial | Need `ExternalProcessCapability`/`AgentCapability` with step/time/cost/safety limits and partial output salvage. |
| Schema-validated structured output | good | `StructuredLLMNode` covers LLM JSON; CLI/subprocess output needs same parse/repair envelope. |
| Budget caps per-agent and per-run | partial | Usage has per-run caps; needs per-capability/per-agent limits and subscription-call accounting. |
| Safety propagation | weak | Need `SafetyPolicy` passed into every capability and surfaced in trace. |
| Full per-agent trace | partial | Usage/events exist; need trace sink interface and prompt/raw/parsed/artifact file layout. |
| Tool/MCP injection | not covered | Need capability metadata for allowed tools and project-owned adapter implementation. |
| Parallel fan-out/gather | not covered | Add runtime helper or rely on LangGraph node patterns with shared budget guard. |
| Iterative deepen/re-dispatch | not covered | Add evaluator/retrace primitives; product graph owns criteria. |
| Salvage on timeout | not covered | MageQA `WorkerBoundary` already has better shape for this; extract it conceptually. |
| Resumable/idempotent run state | not covered | Future checkpoint/trace-store stage. |

Conclusion: the target should be full coverage, not a coordinator-only sidecar. The expanded
library should be able to run Anki's card-generation graph and MageQA's full QA orchestrator class:
plan, dispatch bounded agents/subflows, gather observations, evaluate, deepen/re-dispatch, curate,
and clean up artifacts. The current package is not there yet, so implementation must build the
missing runtime pieces while keeping both product workflows green.

## GoPro Fit Review

GoPro's
[workflow execution engine requirements](/Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md)
fit the same engine direction and are a useful third-project pressure test. They make the engine
more universal, not less, because they require live scheduling, evidence refs, profile compilation,
domain adapter boundaries, and replayable trace without importing any Anki or MageQA nouns.

| GoPro requirement | Fit with current target | Required adjustment |
| --- | --- | --- |
| Task/profile-driven execution | partial | Add `WorkflowProfile` and `RuntimePlanCompiler`; do not run from `WorkflowGoal` alone. |
| Runtime plan audit artifact | weak | Persist compiled plan and warnings with every run/result. |
| Evidence refs and roles | weak | Add `EvidenceRef` model with role, quality, privacy, provenance, confidence. |
| Task/session state | partial | Add generic session state lifecycle and provisional values; keep domain CRUD behind adapters. |
| Tool allow-list and side-effect classes | partial | Extend `CapabilitySpec` with side-effect levels and permission gates. |
| Live scheduling/concurrency | not covered | Add scheduling policy model: latest-only, bounded queue, manual priority, coalescing, replay, single-flight. |
| Human clarification | weak | Treat clarification as a capability/tool with traceable prompts and answers. |
| External/domain adapters | partial | Add adapter contract for local, HTTP, MCP, JSONL/file, queue/event bus, and mock. |
| Fail modes | weak | Add explicit fail-open/fail-closed/product-defined policy; no silent fallback from safety failure. |
| Framework-independent trace | partial | `TraceSink` is good, but trace records need runtime plan, evidence refs, denied tools, fallbacks, cancel/error paths. |
| Cancellation/single-flight | not covered | Define lock lifetime and cancellation semantics before live stream adoption. |

Conclusion: GoPro fits if we expand the framework now around profile/runtime-plan/evidence/scheduler
primitives. If we implement only the Anki/MageQA concepts, GoPro will force a redesign later.

GoPro end-state example:

```text
WorkflowProfile("home inventory session")
  -> RuntimePlanCompiler
       selects manual/stable-camera trigger, sweep evidence builder, inventory tools,
       clarification channel, privacy/retention policy, fail mode, adapter policy
  -> workflow runner
       gathers evidence refs: location_context + transition + contents
       invokes object/location extractor capabilities
       asks for room/drawer label if confidence is low
       emits structured observations to inventory adapter
       records runtime plan, evidence refs, tool trace, uncertainty, and handoff refs
```

## Full Coverage Target

Decision: the framework should become the common runtime for Anki, MageQA, and GoPro-style
profile/evidence workflows, while each product keeps its own domain schemas, prompts, validators,
and delivery.

Target responsibility split:

| Concern | Engine owns | Product workflow owns |
| --- | --- | --- |
| Top-level goal/profile | `WorkflowGoal`/`WorkflowProfile`, constraints, run context, compiled runtime plan | Product-specific objective/profile normalization |
| Capabilities | registration, limits, safety metadata, execution envelope | Which capabilities exist and what they mean |
| Subworkflows | recursive execution, child run context, budget inheritance | Graph topology and state schemas |
| LLM calls | structured output, prompt split, retry, model profile, usage | Prompt content and output schema |
| Python/scripts | execution envelope, timeout, trace, parsed result | Actual function/script body |
| External agents | spawn envelope, tool policy, timeout, salvage, trace | Browser/tester prompt and allowed domain tools |
| QA/evaluation | generic decision schema, retry/retrace/fallback guards | Quality criteria and evaluator prompt/schema |
| Fan-out/gather | bounded parallel execution and failure isolation | Work partitioning and result interpretation |
| Artifacts | ownership, cleanup, trace references | Domain artifact meaning and final packaging/report |
| Providers | reusable image/voice providers and metering | When/why to call provider and how to attach result |

End-state examples:

```text
Anki:
  WorkflowGoal("generate study card")
    -> anki_generation subworkflow
       -> card-set planner capability
       -> per-type scenario capability
       -> image/voice provider capabilities when needed
       -> renderer capability
       -> quality evaluator capability
       -> retry/retrace/fallback through engine policy
    -> Telegram/Anki delivery owned by TGHandyUtils

MageQA:
  WorkflowGoal("QA this website")
    -> mageqa_audit subworkflow
       -> deterministic crawl/gate/catalog Python capabilities
       -> coordinator planner capability
       -> bounded browser-agent executor capabilities, fan-out by scenario
       -> deterministic rules/adjudication Python capabilities
       -> coordinator review capability
       -> deepen/re-dispatch loop through engine policy
       -> coordinator curate capability
       -> report capability
    -> MageQA repository/report delivery owned by MageQA

GoPro:
  WorkflowProfile("catalog this drawer")
    -> runtime plan compiler
       -> evidence strategy capability selects stable burst + sweep coverage
       -> visual/object extractor capabilities create structured observations
       -> clarification capability asks for location labels when needed
       -> evaluator checks coverage/uncertainty
       -> external inventory adapter receives observations + evidence refs
    -> GoPro descriptor/domain systems own media storage, replay, and inventory/search UI
```

This still does not mean the engine becomes a no-code autonomous agent. It becomes a typed runtime
for registered capabilities and product-authored graphs.

## Extraction Strategy

### Phase 0 - Stabilize Current Package

Goal: make the package importable and usable outside TGHandyUtils without pulling Anki/TG code.

Tasks:

- keep built-in generic prompts for engine repair/supervisor defaults;
- move library-facing config names from `ANKI_*` to `WORKFLOW_*`, with Anki aliases in product code;
- add README handoff with install and toy workflow example;
- add import test from a temporary directory without TGHandyUtils prompt files;
- keep Anki working through adapters/aliases.

Exit tests:

- import package from a directory without TGHandyUtils prompt files;
- current Anki graph tests still pass;
- image/voice provider tests still pass.

### Phase 1 - Capability Runtime

Goal: make every callable thing look like a typed capability. This is the core redesign, not a
thin rename of the current instrument registry.

Design constraint: phase 1 must be implemented without importing Anki or MageQA modules. Product
workflows can adapt into the framework later, but core runtime tests must use toy schemas and toy
capabilities so product-specific leakage is obvious.

Add:

- `WorkflowInput`;
- `WorkflowProfile`;
- `RuntimePlanCompiler`;
- `RuntimePlan`;
- `WorkflowTrigger`;
- `EvidenceRef`;
- `SessionState`;
- `SchedulingPolicy`;
- `FailMode`;
- `ExternalAdapter`;
- `CapabilitySpec`;
- `CapabilityContext`;
- `CapabilityResult`;
- `RuntimeLimits`;
- `SafetyPolicy`;
- `CapabilityRegistry`;
- `CapabilityRuntime`;
- `TraceSink` protocol;
- `ModelProfile` / provider-profile abstraction;
- adapters for:
  - structured LLM call;
  - Python callable;
  - subworkflow graph;
  - external process/CLI.

Acceptance:

- toy supervisor can choose between a Python capability, a structured LLM capability, and a toy
  subworkflow;
- toy profile compiles to an inspectable runtime plan with a warning for one unsupported key;
- toy evidence refs pass through a capability and appear in the result/trace without raw bytes;
- each call records trace and usage;
- budget blocks another capability call visibly.
- Anki can register its card-set planner and image/voice generators as capabilities without changing
  card behavior.
- code review check: no Anki/MageQA-specific names or fields in core runtime APIs.

### Phase 2 - Evaluator And Retrace Runtime

Goal: make QA/retry loops reusable without moving product quality rules into the engine.

Add:

- `EvaluationDecision` with:
  - `accept`;
  - `retry_capability`;
  - `retrace_to`;
  - `fallback`;
  - `fail`;
  - `ask_user` as future stage.
- `CriticismEnvelope`;
- `RetracePolicy`;
- `RetryPolicy`;
- runtime guard against invalid retrace target and infinite loops.

Acceptance:

- toy flow fails QA, retries one capability with criticism, then accepts;
- toy flow retraces to an earlier capability;
- cap exhaustion routes to fallback/fail;
- artifacts from abandoned branches are cleaned.
- Anki quality evaluator uses the generic `EvaluationDecision` route for render repair, scenario
  retry, card-plan retry, and fallback.

### Phase 3 - Agent/Subprocess Capability

Goal: cover MageQA's `WorkerBoundary` pattern generically.

Add:

- `ExternalProcessCapability`;
- `AgentCapability` as a typed specialization of external process/subagent execution;
- timeout handling;
- stdout/stderr capture;
- output file capture;
- partial-output salvage;
- subscription-call vs metered-call metadata;
- allowed tools/MCP metadata as configuration, not hardcoded engine behavior.

Acceptance:

- fake CLI timeout keeps partial artifacts and parsed partial output if present;
- failed CLI records fallback reason and does not silently downgrade;
- trace includes prompt/raw/parsed/stderr/artifacts.
- MageQA `WorkerBoundary` behavior has parity tests against `AgentCapability`, including
  subscription-call accounting and salvage-on-timeout.

### Phase 4 - Parallel Fan-Out

Goal: cover MageQA executor fan-out without product-specific browser logic.

Add:

- bounded `gather_capabilities`;
- shared budget guard;
- per-child trace IDs;
- failure isolation.

Acceptance:

- three child capabilities run, one times out, two return results;
- supervisor/evaluator sees partial results;
- parent budget reflects all child calls.
- MageQA can dispatch one browser-agent capability per scenario and preserve partial observations
  when one child fails or times out.

### Phase 5 - Framework Completion Bar

Goal: make the framework independently usable before product migration work hides gaps.

Required framework scenarios:

1. `toy_llm_retry`: structured LLM capability emits bad JSON, repair succeeds, trace records both
   attempts.
2. `toy_retrace`: evaluator rejects output, runtime retraces to an earlier capability with
   criticism, then accepts.
3. `toy_subworkflow`: supervisor calls a subworkflow capability with child run context and inherited
   budget.
4. `toy_external_agent_timeout`: fake CLI/subprocess times out, partial output/artifacts are
   salvaged, failure is visible.
5. `toy_fanout_partial`: three child capabilities run in parallel, one fails, two results are
   gathered, evaluator proceeds with partials.
6. `toy_media`: image/voice provider capability is invoked through generic request/response models
   and usage is recorded.
7. `toy_profile_compile`: profile keys compile to runtime plan; unsupported keys warn/error; plan is
   persisted in trace.
8. `toy_evidence_strategy`: evidence refs with roles/quality/privacy move through tools and output.
9. `toy_clarification`: workflow emits an ask-user capability call, records answer/provisional
   continuation, and finishes.
10. `toy_fail_closed`: safety/profile failure blocks fallback and returns auditable failure.
11. `toy_scheduler_drop`: live latest-only/coalescing policy drops or coalesces work visibly.

Acceptance:

- all toy scenarios are tested without Anki or MageQA imports;
- package imports from a clean temp project;
- README has install, capability registration, subworkflow, evaluator/retrace, agent capability,
  and media-provider examples.
- public API names remain product-neutral after Anki and MageQA validation scenarios are added.

### Phase 6 - Product Migration: Anki

Goal: make Anki use the generic capability/evaluator runtime while preserving current Telegram and
Anki packaging behavior.

Add:

- Anki capability registry:
  - directive/source preparation;
  - image asset planning;
  - card-set planning;
  - text/cloze/visual scenario planning;
  - image generation;
  - voice generation;
  - rendering;
  - quality evaluation;
  - package build.
- adapter from current `AnkiGenerationGraph` to capability runtime, or a clean replacement graph if
  that is simpler.
- regression harness that compares old graph result shape against new runtime result shape on
  fixture scenarios.

Acceptance:

- no loss of `/anki`, `[i ...]`, image, voice, fallback, usage, or buffer behavior;
- all Anki unit/integration tests pass;
- manual Telegram smoke passes.

### Phase 7 - MageQA Handoff Package

Goal: ship the framework to the MageQA implementer with enough instruction to build the full
orchestrator, not just a coordinator sidecar.

Handoff contents:

- install path:
  - editable local package;
  - future git dependency/submodule option;
  - required Python/runtime dependencies.
- concept map:
  - MageQA `WorkerBoundary` -> `AgentCapability`;
  - MageQA `ProcessingTrace` -> `TraceSink` adapter;
  - MageQA `ScenarioSpec` executor fan-out -> `gather_capabilities`;
  - MageQA coordinator plan/review/curate -> structured LLM/evaluator capabilities;
  - MageQA deterministic crawl/catalog/rules/report -> Python/subworkflow capabilities.
- migration recipe:
  1. register deterministic MageQA capabilities;
  2. register coordinator structured LLM capabilities;
  3. register browser-agent capability using scoped MCP/tool policy;
  4. build `mageqa_audit` subworkflow with fan-out and review/deepen loop;
  5. adapt `ProcessingTrace` to engine trace sink;
  6. run MageQA parity/regression tests.
- safety/budget checklist:
  - public-surface-only policy is passed to every agent capability;
  - per-agent timeout and step/cost caps are required;
  - partial salvage is required;
  - findings without grounded evidence stay product-rejected by MageQA rules.
- acceptance fixtures:
  - fake browser-agent success;
  - fake browser-agent timeout with partial screenshot/session artifacts;
  - coordinator review requests one deepen round;
  - budget exhaustion stops re-dispatch and renders partial report.

### Phase 8 - GoPro Handoff Package

Goal: prove the same framework can satisfy GoPro's profile/evidence/task-engine contract before
MageQA migration hides missing live-stream requirements.

Handoff contents:

- mapping:
  - GoPro `UniversalProfileSheet` -> `WorkflowProfile`;
  - GoPro `DescriptorRuntimePlan` -> engine `RuntimePlan`;
  - descriptor evidence/image store refs -> `EvidenceRef`;
  - `AgentEpisodeRunner`/descriptor sidecar -> subworkflow/agent capability;
  - inventory/retail/safety adapters -> `ExternalAdapter`;
  - decision JSONL/replay/image store -> `TraceSink`/trace-store adapter.
- required profile compiler behavior:
  - unknown strategy/capability warnings or errors;
  - persisted runtime plan with selected strategies, tools, budgets, fail mode, privacy, adapters;
  - no public profile key silently ignored.
- live scheduling checklist:
  - latest-only/drop-not-queue policy for live camera hot paths;
  - manual priority queue for user-initiated work;
  - background coalescing for timers;
  - replay/process-all mode for offline datasets;
  - per-profile and per-backend concurrency limits.
- acceptance fixtures:
  - home inventory emits location/content evidence refs and mock inventory adapter call;
  - retail unreadable evidence asks for steadier/closer evidence or returns unreadable;
  - safety timeout/inconclusive follows fail-closed policy;
  - profile with unsupported tool fails or warns according to strictness.

### Phase 9 - Product Migration: MageQA Full Orchestrator

Goal: make MageQA use the same runtime for the full QA orchestrator, not only plan/review/curate.

Add MageQA capabilities:

- `gate_site`;
- `crawl_site`;
- `catalog_checks`;
- `coordinator_plan`;
- `browser_agent_execute_scenario`;
- `adjudicate_observations`;
- `coordinator_review`;
- `coordinator_curate`;
- `render_report`;
- `safety_evaluate_run`.

Runtime behavior:

```text
gate_site
  -> crawl_site
  -> coordinator_plan
  -> fan_out(browser_agent_execute_scenario[])
  -> catalog_checks
  -> adjudicate_observations
  -> coordinator_review
      -> accept -> coordinator_curate -> render_report
      -> deepen/re-dispatch -> fan_out(...) -> adjudicate_observations -> coordinator_review
      -> fallback/fail -> render partial report with trace
  -> safety_evaluate_run
```

Acceptance:

- natural-language QA session can plan site-specific scenarios;
- browser agents run one scenario each with scoped tool/MCP policy;
- parallel fan-out is bounded by scenario count, steps, time, and budget;
- timeout salvages screenshots/session records/partial findings;
- deterministic catalog/rules still run and feed the same observation/finding model;
- review can deepen/re-dispatch within configured limits;
- report is generated from structured findings and curated plan, not agent prose;
- ProcessingTrace or a trace adapter shows prompts, raw/parsed outputs, tools, screenshots,
  economics, and fallback reasons.

### Phase 10 - Optional Checkpointing

Only after Anki or MageQA needs restart/replay beyond trace inspection.

Add:

- checkpoint store interface;
- resume policy;
- idempotency keys for side-effect capabilities.

## TGHandyUtils Regression Test Plan

This extraction must preserve current TGHandyUtils behavior:

- direct `/anki` and saved `anki` mode bypass auto classifier;
- saved `auto` still uses intent/classifier/supervisor path before commit;
- reminder/task path remains unchanged;
- `[i ...]` directive parsing still supports visual/gen/cloze/basic/langvoice cases;
- Anki generated-image cards still package media;
- uploaded image reuse still works;
- language voice cards still attach audio;
- quality evaluator can:
  - accept;
  - repair render;
  - retry scenario;
  - retry card-set plan;
  - fallback.
- cost/usage reply still includes node rows, cached tokens when available, text/image/tool counts,
  and estimated USD;
- image/voice call caps prevent runaway retries;
- bot starts and polls.

Automated validation set:

```bash
python -m py_compile \
  packages/ai_workflow_engine/ai_workflow_engine/*.py \
  packages/ai_workflow_engine/ai_workflow_engine/engine/*.py \
  models/anki_workflow.py \
  services/content/anki_generation_graph.py \
  services/content/anki_card_set_planner.py \
  services/content/anki_scenario_planners.py \
  services/content/anki_quality_evaluator.py \
  services/anki_card_service.py

./test.sh unit -- \
  tests/unit/test_workflow_engine.py \
  tests/unit/test_image_generation_provider.py \
  tests/unit/test_prompt_loader.py \
  tests/unit/test_openai_cache.py \
  tests/unit/test_anki_directives_buffer.py \
  tests/unit/test_anki_card_set_planner.py \
  tests/unit/test_anki_scenario_planners.py \
  tests/unit/test_anki_quality_evaluator.py \
  tests/unit/test_anki_card_service.py \
  tests/unit/test_anki_generation_graph.py \
  tests/unit/test_content_processors.py \
  tests/unit/test_container_wiring.py \
  --tb=short --cov-fail-under=0

./test.sh integration -- \
  tests/integration/test_anki_processor_flow.py \
  tests/integration/test_anki_workflow_graph_integration.py \
  --tb=short --cov-fail-under=0
```

Manual smoke after automated tests:

1. Start bot.
2. Send simple `/anki` text without directives; expect one sensible card.
3. Send `[i cloze]` fact; expect meaningful cloze, not filler-word deletion.
4. Send `[i gen q1]` PPLA-style visual request; expect generated image, media preview, usage row,
   and no fallback unless provider fails.
5. Send image upload with no directive; expect planner can reuse uploaded image or make text card
   based on content.
6. Send `[i langvoice ukr->pt gen] ...`; expect visual language card plus audio attachment or clear
   voice fallback if cap/provider disabled.
7. Force `ANKI_MAX_QUALITY_REPAIRS_PER_RUN=0`; expect quality rejection falls back without retry
   loop.

## Resolved Implementation Shape Decisions

The full-framework target is settled. The previously open implementation-shape questions are also
settled by D2/D3 below:

- Media provider seams stay in core, while concrete OpenAI/Gemini/ElevenLabs adapters move to a
  swappable optional `[media]` pack.
- Trace is a strong `TraceSink` abstraction with swappable concrete sinks. The engine does not own
  one hard primary trace store.

## Claude Critical Review Handoff

Give this section to Claude or another reviewer. The goal is adversarial review, not supportive
agreement.

Reviewer instruction:

```text
You are reviewing Codex's workflow-engine extraction architecture for TGHandyUtils, MageQA, and
GoPro. Do not rubber-stamp it. Your job is to find drift, missing abstractions, over-generalization,
product-specific leakage, unsafe assumptions, and anything that would force a redesign when this is
used in MageQA or GoPro.

Read these source docs before judging:

- /Users/artemm/PycharmProjects/TGHandyUtils/docs/_discussion/2026-06-07-workflow-engine-extraction.md
- /Users/artemm/PycharmProjects/TGHandyUtils/docs/workflow-engine-architecture.md
- /Users/artemm/PycharmProjects/TGHandyUtils/packages/ai_workflow_engine/README.md
- /Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md
- /Users/artemm/PycharmProjects/MageQA/docs/14-agentic-tester-architecture.md
- /Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md
- /Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/UNIVERSAL_PROFILE_SHEET.md
- /Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/HOME_INVENTORY_CASE.md

Review questions:

1. Does the proposed engine genuinely support all three workloads, or does it still mostly describe
   Anki/MageQA with GoPro terms pasted on?
2. Are WorkflowProfile, RuntimePlan, EvidenceRef, CapabilitySpec, SessionState, SchedulingPolicy,
   FailMode, and ExternalAdapter the right public primitives? Which are too broad, too weak,
   missing, or misnamed?
3. Is the capability/subworkflow recursion model safe enough for bounded agent workflows, or does it
   leave room for uncontrolled loops, hidden tool access, or untraceable fallback?
4. Does the architecture handle GoPro live-mode constraints: latest-only/drop-not-queue,
   single-flight cancellation, manual priority, background coalescing, replay/process-all, and
   evidence refs instead of raw media in state?
5. Does the architecture handle MageQA constraints: bounded per-scenario agents, scoped MCP/browser
   tools, fan-out/gather, salvage-on-timeout, ProcessingTrace integration, safety propagation, and
   subscription-call accounting?
6. Does the architecture preserve Anki behavior while avoiding Anki-specific APIs inside the engine?
7. Are image/voice provider capabilities correctly generic, or should they be optional packs instead
   of core package features?
8. Are trace and persistence boundaries right? Should the engine expose TraceSink, own a TraceStore,
   or provide both?
9. Is the implementation plan ordered correctly, or will product migration begin before core runtime
   primitives are proven with toy tests?
10. List concrete tests that would falsify Codex's claims. Prefer failing examples over vague advice.

Output format:

- Findings first, ordered by severity.
- For each finding: cite source doc section or code/doc path; state the violated requirement; explain
  expected correction.
- Then list missing acceptance tests.
- Then list any high-impact open decisions that require Artem's explicit choice.
- Do not write "looks good overall" unless you found no material issues after checking the source
  docs.
```

## Cleanup Notes

- Keep in permanent docs/specs: capability-runtime model, extract/keep split, MageQA fit table,
  GoPro fit table, source-doc cross-links, phased extraction, TGHandyUtils regression plan, and
  current architecture requirements.
- Drop after resolution: discussion wrapper and open-decision tags once content moves into permanent
  docs.

---

## [claude] Critical Review — 2026-06-07

Verification method: grepped the engine package + services for every asserted symbol; read
`anki_generation_graph.py`, `engine/runner.py`, `engine/supervisor.py`; rebuilt the test image
(engine installed editable per `environment.yml:20`); ran `./test.sh unit`. Findings are
severity-ordered; each cites evidence, the violated requirement, and the correction. Not
rubber-stamping: there are material issues, including a non-green branch.

### F1 (BLOCKING) — branch is not green; 2 unit tests fail
- Evidence: `./test.sh unit` -> `2 failed, 480 passed`.
  - `tests/unit/test_auto_mode.py:33` — `IntentClassifier.classify()` returns `REMINDER` when the
    mocked LLM content is `"anki"`; test expects `ANKI`. Either the classifier contract changed
    (see `services/content/classifier.py`, modified) and the test is stale, OR auto-mode
    classification regressed. User-visible (auto mode), so resolve it, don't silence it.
  - `tests/unit/test_container_wiring.py:41` — `container.anki_generation_graph()` raises inside
    `dependency_injector` Factory provide (`providers.pyx:2679`). The graph provider fails to
    construct under the test config; risks real startup/runtime failure depending on config.
- Violated requirement: CLAUDE.md "never commit untested shit"; Anki must run through the engine
  "without regression" (this doc :58-59).
- Correction: codex fixes both before any migration/merge; if the classifier contract changed on
  purpose, update the test and state the new contract.

### F2 (HIGH) — README overstates what the engine package implements
- Evidence: `packages/ai_workflow_engine/README.md:5-18` claims the package "owns" workflow
  profiles, compiled runtime plans, evidence references, runtime limits, scheduling/fail-mode
  policy, and domain-adapter boundaries. Grep shows these MISSING: `WorkflowProfile`, `RuntimePlan`,
  `EvidenceRef`, `CapabilitySpec`, `SchedulingPolicy`, `FailMode`, `ExternalAdapter`, `SessionState`,
  `ModelProfile`. What exists: `WorkflowGoal`, `WorkflowRunner`, `WorkflowInstrumentRegistry`,
  `WorkflowSupervisor`, `WorkflowDecisionPlanner`, `StructuredLLMNode`, `PromptTemplateLoader`,
  usage/artifacts/image/voice seams.
- Violated requirement: "current state" claims must be verifiable. The README contradicts this
  doc's own "Not covered enough yet" list (:566-576) and Doc 2 (`ModelProfile` "still target
  architecture", workflow-engine-architecture.md:262).
- Correction (proposed, NOT applied — permanent doc): split README "owns" into "Implemented now"
  vs "Planned (not implemented)". Awaiting Artem/codex confirmation before edit.

### F3 (HIGH, decision) — the two governing docs disagree on scope; production evidence backs the conservative one
- Doc 1 (this file :58-59, Phase-1 list :734-757): ship only when Anki+MageQA+GoPro packages exist;
  build the full Capability/Profile/Evidence framework now.
- Doc 2 (workflow-engine-architecture.md:278-279, 424-427): add machinery only when a second
  product needs it; "not a general-purpose workflow library … until another product workflow uses
  the engine boundary."
- Evidence: the only orchestration model used in production is `WorkflowRunner` wrapping a
  product-authored LangGraph `StateGraph` (`anki_generation_graph.py:162-303`). `WorkflowSupervisor`/
  `WorkflowDecisionPlanner`/`WorkflowInstrumentRegistry` are used in NO production module (grep:
  engine + tests only). The Capability/`CapabilitySpec`/`WorkflowProfile`/`RuntimePlan`/`EvidenceRef`
  layer Doc 1 centers on does not exist.
- Violated requirement: the plan's own rule "Do not add framework machinery until it solves a
  concrete current problem" (anki-cards-plan.md:36) and this doc's own "framework too abstract" risk.
- Decision for Artem: pick the governing doc. Evidence favors Doc 2 — keep the proven
  thin-runner-over-LangGraph core; demote Doc 1's Capability/Supervisor framework to `future-stage`
  until a real second workload exercises it.

### F4 (HIGH) — three-workload support is asserted, not demonstrated
- Handoff Q1. The engine that exists supports exactly one workload (Anki) via the thin runner.
  `WorkflowSupervisor` is toy-tested only (Doc 2 cites
  `test_workflow_engine.py::test_engine_runs_toy_non_anki_workflow_through_supervisor_and_runner`).
  MageQA/GoPro fit (this file :582-594, :429-432; symbols `WorkerBoundary`, `ProcessingTrace`,
  live-mode scheduling) cannot be verified from this repo — those repos are not present.
- Violated requirement: validate claims with real examples; do not promote production-readiness
  without baselines.
- Correction: down-rank MageQA/GoPro requirements to "assumed, unverified — pending review with
  those repos"; do not let unverified external requirements force primitives into this engine now.

### F5 (MEDIUM) — LangGraph recursion limit not set; deep retry/retrace can hit the default cap (25)
- Evidence: `anki_generation_graph.py:302` `graph.compile()` and `runner.py` `graph.ainvoke(state)`
  pass no `recursion_limit`; the graph has multiple cycles (quality-eval -> retry_card_plan ->
  plan_card_type …; scenario retry; repair_render). Per-key counters bound each branch, but
  cumulative node visits on adversarial input can approach 25 -> `GraphRecursionError`.
- Correction: set an explicit `recursion_limit`; add a test that exhausts every retry branch and
  asserts graceful fallback, not a raised recursion error.

### F6 (LOW) — minor doc/impl drift
- `package_cards` is a no-op marker; real packaging/delivery/buffer is in `AnkiProcessor`
  (`anki_generation_graph.py:1427-1430`), but plan graphs show "package_cards -> Delivery+buffer".
- Superseded config status: permanent docs now record the approved three-layer config model and the
  explicit TGHandyUtils `ANKI_*` transition bridge; this is not an engine package dependency.

### Positives (verified, keep)
- Engine <-> app decoupling holds: grep finds NO `core/services/config/models/handlers` imports
  inside `packages/ai_workflow_engine/`. Lock with a guard test.
- `WorkflowRunner` is a genuinely thin, sound wrapper (IDs, usage/budget scope, trace) over
  LangGraph — matches Doc 2's intent.
- The Anki graph is real: bounded retries, retrace edges, fallback, structured per-node trace.

### Missing acceptance tests (falsifiers)
1. Recursion-limit: input forcing every retry branch to exhaust -> assert graceful fallback, no
   `GraphRecursionError`.
2. Engine-decoupling guard: assert no `import core|services|config` under `packages/ai_workflow_engine/`.
3. Supervisor reality: a real workflow uses `WorkflowSupervisor`, or it is marked experimental.
4. A non-Anki toy that exercises branch + bounded retry + fallback through the same runner.
5. Before Capability/Profile/Evidence become "implementation-target": a prototype running a second
   (stub) workload through them; else they stay `future-stage`.

### High-impact decisions for Artem
- D1: Governing scope — Doc 1 (3-workload framework now) vs Doc 2 (minimal until 2nd workload).
  Evidence favors Doc 2.
- D2: Image/voice providers in the core package vs optional extras (`[open]` above).
- D3: Engine exposes `TraceSink` only vs owns a `TraceStore` (`[open]` above).

[superseded]: D1 governing-scope conflict was settled by Artem below. F1/PF1 was later fixed by
Codex; see final discussion status.

### Decision Record — 2026-06-07 (Artem)
- D1 SETTLED: build the FULL framework now (Doc 1 scope — all capability/profile/evidence/
  scheduling/fail-mode/supervisor/recursion primitives), validate end-to-end on Anki, then ship the
  same engine to GoPro and MageQA. Doc 1 governs; Doc 2's "minimal until 2nd workload" is
  superseded and must be reconciled to match.
- [agent-agreement]: 2026-06-07 — F3 objection (generalizing from one workload risks rework) was
  raised by [claude] and explicitly OVERRIDDEN by Artem. Accepted risk. Mitigation: each phase is
  validated on Anki before the next; MageQA/GoPro requirements stay tagged "assumed-unverified"
  until those repos confirm (they are not in this repo).
- F1 (2 failing unit tests) was independent of D1 and blocked migration until fixed.
- D2/D3 were still open at this point in the discussion; they are settled below.

### [claude] correction — 2026-06-07
- F4 revised: the README links MageQA/GoPro docs at real on-disk paths
  (`/Users/artemm/PycharmProjects/MageQA/...`, `/Users/artemm/PycharmProjects/gopro-streaming/...`),
  so those repos exist locally and the cross-workload claims are VERIFIABLE — my earlier "not
  present, unverifiable" was wrong. Revised position: the MageQA/GoPro requirements must be verified
  against those repos during the build (before the engine commits to a primitive shaped by them),
  not taken on faith from this doc.
  Superseded by the permanent workflow-engine architecture and acceptance-criteria docs, which now
  cross-reference the concrete MageQA/GoPro source documents and fake-backed pilot proofs.

### Doc consistency fixes APPLIED — 2026-06-07
- `packages/ai_workflow_engine/README.md`: "owns" list split into Implemented-today vs
  Planned-target (fixes F2 overstatement; matches grep of package source).
- `docs/workflow-engine-architecture.md`: added the 2026-06-07 scope decision (full framework now ->
  Anki -> GoPro/MageQA); replaced the contradicting "add registry only when 2nd workflow needs it"
  line with the build-now stance.
- `docs/anki-cards-plan.md`: marked Anki as the per-phase validation workload + linked the
  implementation plan.
- Remaining doc work deferred until D2/D3 resolved + plan authored.

### D2 / D3 SETTLED — 2026-06-07 (Artem)
Unifying principle: the engine core is **abstractions/interfaces + a registry**; concrete
implementations are **pluggable and swappable** (optional packs or shipped defaults; products may
swap or extend).
- D2 (media): capability-pack model. Abstract `ImageGenerator`/voice protocols + request/response
  models stay in core; concrete OpenAI/Gemini/ElevenLabs adapters move to a separate optional
  `[media]` pack, registered & swappable via the capability registry. Core must not hard-depend on
  media SDKs. (Rejects "fully in core".)
- D3 (trace): a strong `TraceSink` abstraction with swappable concrete impls. Engine ships good
  default sinks (in-memory + file/JSONL; SQLite optional) behind the interface; products select/swap
  or supply their own. Abstraction quality target: good enough that MageQA could replace its
  `ProcessingTrace` with it. Not a single hard-owned store.

### Discussion status — 2026-06-07
- SETTLED: D1 (full framework now -> Anki -> GoPro/MageQA), D2, D3. Docs reconciled (README, Doc 2,
  anki-cards-plan). Governing doc = Doc 1 + Doc 2 (now aligned).
- RESOLVED: F1/PF1 fixed by Codex on 2026-06-07; latest free suite after runtime work:
  `./test.sh unit -- --cov-fail-under=0` -> `543 passed, 142 deselected` in the latest
  post-doc-review validation pass.
- STILL OPEN: implementer split (Artem to decide after plan review).
- DONE: `docs/workflow-engine-implementation-plan.md` drafted (Claude, 2026-06-07) — phased,
  F1-first, per-phase Anki gate, D2/D3 folded in, guard/falsifier tests, honest Anki-coverage note.
  Reviewed by Codex and PF1 updated. On acceptance, migrate the discussion's "Extraction Strategy"
  into it and delete this transient file after Phase 6 is green.
