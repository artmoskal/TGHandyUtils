# Workflow Engine — Implementation Plan

Status: **implementation in progress** (Claude-authored 2026-06-07; Codex-expanded phase/task split
and AC gates; PF1 fixed; first reusable runtime primitives and YAML/profile config loading
implemented and tested 2026-06-08)
Owner: Artem
Parent architecture: `docs/workflow-engine-architecture.md`
Rationale + fit analysis: `docs/_discussion/2026-06-07-workflow-engine-extraction.md`
(this doc supersedes that discussion's "Extraction Strategy" section once accepted)

## Governing decisions (settled 2026-06-07)

- **D1:** build the FULL framework now → validate on Anki → ship to GoPro/MageQA.
- **D2 (media):** abstract `ImageGenerator`/voice protocols + request/response models in core;
  concrete OpenAI/Gemini/ElevenLabs adapters in a separate optional `[media]` pack, registered &
  swappable via the capability registry. Core must not hard-depend on media SDKs.
- **D3 (trace):** a strong `TraceSink` abstraction with swappable concrete impls (in-memory +
  file/JSONL shipped; SQLite optional); products select/swap or supply their own. No single
  hard-owned store. Quality bar: good enough that MageQA could replace `ProcessingTrace` with it.

## Verified current state (grep-confirmed 2026-06-08)

- **Exists:** `WorkflowGoal`, `WorkflowRunContext`, `WorkflowRunner`, `WorkflowInstrumentRegistry`,
  `WorkflowSupervisor` + `WorkflowDecisionPlanner` (toy-tested; **used by no production workflow**),
  `StructuredLLMNode`, `PromptTemplateLoader`, usage/budget metering, artifact cleanup, image+voice
  provider seams (request/response protocols in core package modules; concrete SDK use behind the
  optional `media` dependency). Anki runs via `WorkflowRunner` wrapping a product-authored LangGraph
  graph (`services/content/anki_generation_graph.py`).
- **Implemented 2026-06-08:** `CapabilitySpec`/`CapabilityContext`/`CapabilityResult`,
  `CapabilityRegistry`/`CapabilityRuntime`, `WorkflowProfile`/`RuntimePlan`/`RuntimePlanCompiler`,
  `WorkflowInput`, `WorkflowTrigger`, `EvidenceRef`, `SessionState`, `SchedulingPolicy`,
  `FailMode`, `RuntimeLimits`, `SafetyPolicy`, `ModelProfile`, `EvaluationDecision`,
  `CriticismEnvelope`, `RetracePolicy`/`RetryPolicy`, `TraceSink` protocol, `InMemoryTraceSink`,
  `JsonlTraceSink`, `ExternalProcessCapability`, `ExternalAdapterCapability`,
  `InMemoryExternalWriteSink`, `JsonlExternalWriteSink`, bounded `gather_capabilities`,
  `WorkflowScheduler`, bounded `WorkflowLoopController`, `CapabilityRuntime` side-effect denial
  against `RuntimePlan.safety.allowed_side_effects`, `HumanClarificationCapability` with
  pluggable channels for `ask_user` pause/resume/provisional continuation, `AgentCapability` with
  bounded episode steps, scoped tool allow-list, tool-call history, trace events, and
  subscription-mode accounting metadata, `CheckpointStore`/`InMemoryCheckpointStore`/
  `JsonlCheckpointStore` with raw-byte rejection and loop checkpoint writes, and `WorkflowConfigLoader` /
  `WorkflowConfigBundle` for defaults -> YAML -> env override profile loading. Media provider SDKs
  are optional: core/media modules import without the OpenAI SDK, and concrete OpenAI/requests usage
  is behind the `[media]` extra or lazy provider calls. Anki graph nodes are registered
  `CapabilitySpec`s and invoked through `CapabilityRuntime` with a compiled `RuntimePlan`, including
  side-effect classes for image, voice, and packaging nodes. TGHandyUtils auto mode's 5-second
  Telegram correction window now uses `HumanClarificationCapability` with a product-owned Telegram
  channel and traceable pending/answered/provisional responses. `WorkflowResult` exposes explicit
  uncertainty states. `EvaluationController` now provides a reusable evaluator-driven retry,
  retrace, fallback/repair, ask-user, and fail policy for product-owned evaluators under bounded
  caps. `WorkflowScheduler` now covers run-immediately, queue, replay-process-all, run-latest,
  live-latest-only, drop-stale, drop-not-queue, coalesce, fan-out-gather, backend concurrency caps,
  and single-flight cancel-not-release behavior.
- **Downstream / outside current package handoff:** real MageQA/GoPro repo adoption with production browser/MCP,
  camera/frame, and domain adapters; automatic product restart/replay wiring; optional `[media]`
  package cleanup; and the explicit approval/sunset decision for TGHandyUtils product-local
  `ANKI_*` env transition aliases. Package-level fake-backed site-audit and inventory pilots now
  exist for MageQA/GoPro-shaped pressure tests.

## Validation evidence (2026-06-08)

- `./test.sh unit -- tests/unit/test_workflow_engine.py --cov-fail-under=0` -> `58 passed`.
  Covers package import from a clean temp project, no app/Anki imports in the package, capability
  runtime validation/trace, optional OpenAI media SDK import guard, RuntimePlan side-effect denial before handler execution, JSONL trace
  sink, JSONL checkpoint reload, raw-byte checkpoint rejection, plan compiler warnings, bounded
  fan-out including timeout isolation, divergent supervisor capability selection, bounded supervisor
  loop, external-process timeout salvage, generic `ask_user`
  pause/resume/provisional continuation through a pluggable channel, bounded agent episodes with
  scoped tool allow-list, subscription-mode metadata, step-limit truncation, idempotent
  external/domain adapter writes, scheduler mode spectrum, `WorkflowResult` uncertainty states,
  reusable evaluator retry/retrace/fallback policy, usage budgets, structured JSON repair, and
  non-Anki toy workflows, including fake-backed site-audit and inventory pilots.
- `./test.sh unit -- tests/unit/test_config_profile.py tests/unit/test_workflow_config_loader.py tests/unit/test_workflow_engine.py tests/unit/test_container_wiring.py tests/unit/test_anki_generation_graph.py --cov-fail-under=0` -> `112 passed`.
  Covers YAML/profile config loading, package/runtime guards, container wiring, and Anki graph
  capability-runtime/RuntimePlan behavior.
- `./test.sh unit -- tests/unit/test_auto_mode.py tests/unit/test_workflow_engine.py tests/unit/test_anki_generation_graph.py --cov-fail-under=0` -> `104 passed`.
  Covers auto-mode `HumanClarificationCapability`, reusable engine proofs, MageQA/GoPro-shaped
  fake-backed pilots, and Anki graph capability-runtime behavior.
- `./test.sh unit -- --cov-fail-under=0` -> `543 passed, 142 deselected`, 72.0% line coverage.
  Covers the safe unit suite after the package/runtime changes. Retained artifacts were scanned for
  `api.todoist`, `api.trello`, `api.openai`, `generativelanguage`, and `elevenlabs`; no live provider
  URLs were found outside ignored HTML coverage output.
- `./test.sh unit -- tests/integration/test_anki_processor_flow.py --cov-fail-under=0` -> `6 passed`.
  This processor-flow file is intentionally marked unit-safe because it uses fake graph/provider
  outputs and mocked Telegram boundary; it covers APKG delivery, generated image preview, comparison
  preview, voice preview, fallback caption, buffer behavior, and usage summary without live spend.
- `./test.sh integration ...` is now fail-closed unless `ALLOW_PAID_TESTS=1` is set. A previous
  unattended integration attempt started live API calls; the test runner was changed so that cannot
  happen again silently.
- `./test.sh integration -- tests/integration/test_anki_processor_flow.py --cov-fail-under=0` without
  `ALLOW_PAID_TESTS=1` -> exits `2` before Docker/provider work.
- `./test.sh unit -- tests/unit/test_config_profile.py tests/unit/test_workflow_config_loader.py tests/unit/test_workflow_engine.py tests/unit/test_container_wiring.py tests/unit/test_anki_generation_graph.py --cov-fail-under=0` -> `112 passed`.
  Covers YAML/profile config loading, application config consuming committed profile defaults with
  no non-secret env, generic overrides plus the currently open product-transition alias bridge,
  secret-file rejection, model profile registry access, engine package isolation, style-reference DI
  wiring, and Anki graph RuntimePlan wiring.

## Pre-flight

- **PF1 — RESOLVED 2026-06-07 by Codex.** `./test.sh unit` previously failed 2:
  - `tests/unit/test_auto_mode.py::test_classifier_maps_responses` — classifier returns REMINDER
    for "anki" content; reconcile classifier contract vs test.
  - `tests/unit/test_container_wiring.py::test_container_resolves_style_reference_paths_as_strings`
    — `container.anki_generation_graph()` fails to construct; fix the DI wiring.
  Fixes applied:
  - `IntentClassifier._model_name()` ignores non-string mocked config values and falls back to
    `gpt-5.4-mini`.
  - `ApplicationContainer` resolves optional image/voice config fields with defaults for minimal
    config objects.
  Exit verified: `./test.sh unit -- --cov-fail-under=0` -> `543 passed, 142 deselected`
  after the first runtime implementation slice.

Framework implementation is underway; PF1 no longer blocks.

## Cross-cutting rules (apply every phase)

1. **Anki stays green:** every phase ends with `./test.sh unit` (and relevant integration) green.
   Anki is the per-phase regression gate **for the primitives it uses**.
2. **Anki coverage (corrected 2026-06-07):** Anki is the concrete source workload and per-phase
   regression gate. After a primitive is implemented and wired through Anki, Anki validates that
   primitive's Anki-shaped contract/data-model instance. Anki does not stress two execution axes:
   **open-ended agentic control** (→ MageQA) and **streaming/durable run lifetime** (→ GoPro). Those
   axes rely on the Phase-5 toy bar plus Phases 7-9 before they can be called production-ready.
   Human-in-the-loop is still a real source behavior: the 5s correction window now uses the generic
   `ask_user` capability with Telegram as its channel.
3. **Core stays product-neutral:** no `anki`/`mageqa`/`gopro` names or fields in engine APIs.
   Add/keep a guard test asserting `packages/ai_workflow_engine/` imports no `core|services|config`.
4. **Bounded loops:** set an explicit LangGraph `recursion_limit`; every retrace/retry path has a
   cap that routes to fallback/fail, proven by a toy that exhausts it.
5. **MageQA/GoPro requirements are verified, not assumed:** before a primitive is shaped by an
   external workload, read the real doc in `/Users/artemm/PycharmProjects/MageQA` or
   `/Users/artemm/PycharmProjects/gopro-streaming` and cite it. Tag unverified mappings.
6. **Engine is not interface-only:** every generic primitive ships with runtime behavior, tests, and
   at least one toy or Anki wiring. A phase cannot pass by adding protocols that product code must
   fully implement itself.
7. **Scripts are capabilities:** low-level scripts/tools are allowed only behind `CapabilitySpec`,
   `CapabilityContext`, typed input/output schemas, timeout, side-effect policy, trace, and budget
   rules.
8. **No silent degradation:** every fallback/partial/rejected output records the failed step,
   criticism, selected policy, cost impact, and user-visible effect.
9. **Task size:** implementation tasks below are intended to be less than 4 hours each. If a task
   grows past that, split it before coding and update this plan.
10. **Calendar-builder pressure test:** at least one toy workload must build a calendar plan from
    availability, task pool, energy, priorities, and focus projects. This prevents overfitting the
    framework to Anki/media or MageQA/browser automation.
11. **No legacy preservation by default:** during architecture work, do not keep obsolete code paths,
    data shapes, or config aliases only for compatibility. If a bridge is needed for deployment,
    record it as an explicit user decision before implementation.

## Phase/task split

Each task is sized for less than 4 hours. The AC gate at the end of each phase is blocking: do not
start the next phase until the gate is green or this plan is updated.

### Phase 0 - Stabilize package and guard the boundary

Goal: make the current internal package safe to build on before redesigning runtime primitives.

- **P0.1 Config architecture — implemented 2026-06-08:** follow `docs/workflow-engine-config-architecture.md`: env is secrets
  plus deployment overrides; non-secret workflow config moves to YAML/profile files loaded into
  `WorkflowProfile`/`ModelProfile` and compiled to `RuntimePlan`. `WORKFLOW_*` is an override prefix,
  not a mass rename target. Product-local `ANKI_*` aliases are a compatibility bridge with an open
  approval/sunset decision, not a library feature. AC: no `ANKI_` config lookup inside
  `packages/ai_workflow_engine`, no required non-secret env for Anki fake/unit runs, and committed
  config files contain no secrets. Proof: `tests/unit/test_config_profile.py` +
  `tests/unit/test_workflow_config_loader.py`.
- **P0.2 Clean import proof:** add/keep a test that imports the package from a temporary project
  with no TGHandyUtils prompts, config, services, or app logging. AC: package import succeeds.
- **P0.3 Decoupling guard:** add an automated guard that fails on `import core`, `import services`,
  `import config`, or TGHandyUtils-specific exception/logging imports under the package. AC: guard
  fails on an injected forbidden import and passes on current source.
- **P0.4 README example:** add a runnable toy non-Anki workflow example to the package README or
  examples directory. AC: example runs in tests without Telegram/Anki.
- **P0.5 Current Anki regression:** run targeted Anki graph/image/voice tests plus full unit suite.
  AC: `./test.sh unit -- --cov-fail-under=0` green.

Phase 0 AC gate: boundary guards exist, package can be imported from a clean temp project, and Anki
tests still pass.

### Phase 1 - Capability runtime and runtime plans

Goal: replace ad hoc product wiring with real engine-owned capability execution.

- **P1.1 Core models:** add `WorkflowInput`, `WorkflowProfile`, `RuntimePlan`,
  `RuntimePlanCompiler`, `WorkflowTrigger`, `EvidenceRef`, `SessionState`, `SchedulingPolicy`,
  `FailMode`, `RuntimeLimits`, and `SafetyPolicy`. AC: models have product-neutral
  tests and no Anki/MageQA/GoPro field names.
- **P1.2 Capability contract:** add `CapabilitySpec`, `CapabilityContext`, `CapabilityResult`,
  `CapabilityRegistry`, and `CapabilityRuntime`. AC: Python-callable capability runs with typed
  input/output, trace, timeout, and failure result.
- **P1.3 Built-in adapters:** implement generic adapters for structured LLM node, deterministic
  Python callable, subworkflow graph, bounded external process, and idempotent external/domain
  writes. AC: toy supervisor can pick python/LLM/subworkflow/external capabilities by spec.
- **P1.4 Trace and usage integration:** wire capability runtime into `TraceSink` protocol,
  `WorkflowUsageSummary`, budget caps, request IDs, cache tokens when present, and artifact refs.
  AC: toy run records per-capability usage and budget blocks a second paid call.
- **P1.5 Profile compiler:** compile profile/directive constraints into an inspectable runtime plan;
  unsupported keys warn or fail by policy. AC: toy profile compiles, unknown key is visible, and the
  supervisor receives the compiled plan.
- **P1.6 Anki capability registration:** register existing Anki planners/renderers/media as
  capabilities while keeping this step transitional and clean. AC: Anki fixtures still satisfy
  current acceptance criteria, usage remains visible, and no compatibility wrapper is added to
  preserve obsolete architecture.

Phase 1 AC gate: toy workflow runs through capability runtime, Anki capabilities are registered, no
product terms appear in engine APIs, and full unit tests pass.

### Phase 2 - Evaluation, retry, retrace, fallback, and human clarification

Goal: make quality control generic instead of product-local retry logic.

- **P2.1 Decision model:** add `EvaluationDecision` with `accept`, `retry_capability`,
  `retrace_to`, `repair`, `fallback`, `fail`, and `ask_user`. AC: invalid decision is rejected by
  schema and logged.
- **P2.2 Criticism envelope:** add `CriticismEnvelope` carrying observed output, expected goal,
  severity, recommended target step, and user-visible effect. AC: retry prompt receives criticism in
  a stable/static-friendly structure.
- **P2.3 Retry/retrace policies:** add `RetryPolicy` and `RetracePolicy` with max attempts, allowed
  targets, and loop guards. AC: toy run retries once with criticism, then accepts.
- **P2.4 Failure caps:** prove exhaustion routes to fallback/fail without `GraphRecursionError`.
  Implemented: `WorkflowRunner` accepts a workflow-owned `recursion_fallback` policy hook, logs
  `outcome=fallback`, and keeps usage accounting on the same run context.
  Proof:
  `tests/unit/test_workflow_engine.py::test_workflow_runner_routes_recursion_exhaustion_to_policy_fallback`
  and
  `tests/unit/test_anki_generation_graph.py::test_graph_recursion_exhaustion_uses_text_fallback_instead_of_leaking_error`.
- **P2.5 Artifact cleanup:** clean abandoned branch artifacts on retry/retrace/fallback. AC: toy image
  branch creates two artifacts, rejects one, keeps only accepted/fallback artifacts.
- **P2.6 Anki evaluator migration:** route Anki render repair, scenario retry, card-plan retry, and
  fallback through generic `EvaluationDecision`. AC: annex/list coverage failure can ask for
  scenario/card-plan retry with criticism and cannot silently sample one fact.
- **P2.7 Clarification hook:** model the 5s correction window as a generic `ask_user` source
  behavior, even if Telegram still owns the concrete UI. AC: toy clarification emits pending choice
  and resumes from selected answer.

Phase 2 AC gate: evaluator/retrace/fallback works in toys and Anki quality rejection uses the same
decision model under capped retries.

### Phase 3 - Media, voice, and model profiles as capability packs

Goal: keep media/voice reusable without making core hard-depend on provider SDKs.

- **P3.1 Model profiles:** add `ModelProfile` and provider-profile registry with node-level model
  selection. AC: toy gate uses cheap profile, toy scenario uses stronger profile, trace records both.
- **P3.2 Media protocols in core:** keep abstract `ImageGenerator`/voice protocols plus request and
  response models in core. AC: core import works with no media extra installed.
- **P3.3 Optional media pack:** move concrete OpenAI/Gemini/ElevenLabs adapters to optional
  `[media]` pack or separable module registered through capabilities. AC: `toy_media` runs only when
  the adapter pack is installed.
- **P3.4 Provider-specific prompt policy seam:** keep image prompt subject/policy per product while
  provider nuances live in adapters/model profiles. AC: OpenAI/Gemini prompt payloads differ only in
  adapter-specific envelope, not graph topology.
- **P3.5 Cost and cache reporting:** ensure image/voice/text calls report provider, model, cache
  tokens if returned, request IDs, and estimated USD when pricing is known. AC: usage table can split
  prompt ID, model, cached input, uncached input, output, image/voice units, and cost.
- **P3.6 Anki media regression:** generated image cards, uploaded image reuse/reference, comparison
  mode, and `[i langvoice ...]` voice cards still work through the capability seam. AC: simulated TG
  e2e covers one text, one generated image, one uploaded-image decision, and one voice card.

Phase 3 AC gate: core is media-SDK-free, adapters are swappable, Anki media behavior is intact, and
usage/cost split is visible.

### Phase 4 - Agent and external process capabilities

Goal: support MageQA-style autonomous workers without baking MageQA into the engine.

- **P4.1 Verify MageQA docs:** reread the MageQA orchestrator/agent docs and record exact mapping
  from worker boundary to capability spec. AC: implementation notes cite the source docs.
- **P4.2 External process capability:** implement timeout, stdout/stderr capture, output-file
  capture, partial-output salvage, and fail/partial result. AC: fake CLI timeout salvages partial
  output and records stderr/artifacts.
- **P4.3 Agent capability:** model an external AI/browser/MCP worker as a capability with allowed
  tools, subscription-vs-metered metadata, max steps, and output schema. AC: toy agent cannot call an
  undeclared tool and records step count.
- **P4.4 Supervisor open-action toy:** build a toy where the supervisor chooses among several tools
  in an open loop under max steps. AC: max-step exhaustion fails closed with trace.
- **P4.5 Safety guard:** prohibit raw subprocess/agent calls outside capability runtime in product
  workflows. AC: guard test covers helper imports or direct adapter construction where practical.

Phase 4 AC gate: MageQA-shaped agent/subprocess behavior is toy-tested with failure, salvage,
side-effect, and trace semantics.

### Phase 5 - Fan-out, scheduling, sessions, and framework completion bar

Goal: finish the generic primitives before Anki becomes the full validation milestone.

- **P5.1 Fan-out/gather:** add bounded `gather_capabilities`, shared budget guard, per-child trace
  IDs, and failure isolation. AC: 3 children run, 1 times out, 2 return, parent result exposes
  partials.
- **P5.2 Scheduling policy:** add drop-stale, single-flight cancel, queue, and run-latest policies.
  AC: toy scheduler drops stale inputs and records why.
- **P5.3 Evidence strategy:** carry `EvidenceRef` roles for uploaded images, video frames, OCR,
  summaries, and source documents without raw bytes in shared state. AC: toy source has refs only;
  artifact store owns bytes.
- **P5.4 Session lifecycle:** define in-memory `SessionState` plus checkpoint-store seams.
  AC: short session resumes after `ask_user`; checkpoints are written/reloadable; no product-level
  restart-recovery claim is made until a product maps checkpoints back to domain state.
- **P5.5 Calendar-builder toy:** create a non-media, non-browser toy workflow:
  availability + task pool + energy + priorities + focus projects -> proposed calendar -> overload
  evaluator -> retrace/replan -> accepted plan. AC: evaluator rejects an overbooked plan and the
  repaired plan preserves priority coverage.
- **P5.6 Required toy suite:** run `toy_llm_retry`, `toy_retrace`, `toy_subworkflow`,
  `toy_external_agent_timeout`, `toy_fanout_partial`, `toy_media`, `toy_profile_compile`,
  `toy_evidence_strategy`, `toy_clarification`, `toy_fail_closed`, `toy_scheduler_drop`, and
  `toy_calendar_builder`. AC: no Anki/MageQA/GoPro imports.
- **P5.7 Clean-project proof:** import package and run toy suite from a clean temp project. AC:
  package does not require TGHandyUtils files.

Phase 5 AC gate: all generic primitives are implemented with reusable runtime behavior, not just
interfaces. Toy suite passes from a clean project.

### Phase 6 - Product migration: Anki validation milestone

Goal: make Anki use the full framework, not a parallel hardcoded graph.

- **P6.1 Capability inventory:** list and register Anki capabilities: directive/source packaging,
  image-asset planning, card-set planning, text scenario, cloze scenario, visual scenario, image
  generation, voice generation, rendering, quality evaluation, package build, Telegram preview.
  AC: every existing Anki paid/side-effecting step maps to one capability.
- **P6.2 Runtime plan mapping:** compile `/anki`, user settings, auto mode, `[i ...]`, uploaded
  media, and model/provider config into a `RuntimePlan`. AC: explicit commands bypass intent
  classification while auto mode still uses resolver.
- **P6.3 Replace/adapt graph:** adapt or replace `AnkiGenerationGraph` onto the capability runtime.
  AC: no duplicate retry/fallback loop remains outside the generic evaluator except Telegram UX.
- **P6.4 Regression fixtures:** validate text, cloze, visual, uploaded image, image-reference,
  fallback, annex/list coverage, and voice fixtures against acceptance criteria. AC: no accidental
  loss of fields, usage, attachments, buffer behavior, or fallback explanation, and no obsolete code
  path is kept only for compatibility.
- **P6.5 Simulated Telegram e2e:** simulate TG handler call through Anki graph and inspect output
  card/package/message. AC: one basic, one cloze, one generated image, one uploaded-image reuse, one
  voice/language card, one fallback, one annex/list coverage scenario.
- **P6.6 Manual Telegram smoke:** run the bot and validate live behavior with bounded paid tests.
  AC: cost caps respected, usage table appears, media attached, quality acceptable by human review.

Phase 6 AC gate: Anki is green on the new engine, including manual TG smoke. This is the first true
Anki e2e milestone, not just unit testing.

### Phase 7 - MageQA handoff and pilot mapping

Goal: hand the framework to MageQA without repeating the "set of scripts" failure.

- **P7.1 Read and map:** reread MageQA docs and map supervisor goal, browser agents, scripts,
  screenshot analysis, performance checks, accessibility checks, QA evaluator, and report/video
  generation into capabilities/subworkflows. AC: mapping doc cites exact MageQA files.
- **P7.2 Handoff package:** produce install/use instructions, capability examples, accepted
  boundaries, and missing adapters. AC: MageQA can import package in a clean environment.
- **P7.3 Pilot workflow:** build or specify a minimal MageQA pilot: "QA this website" ->
  browser/test/perf/a11y capabilities -> evaluator -> report. AC: fake-backed pilot passes with no
  Anki concepts. Current package proof: `run_toy_site_audit_pilot()` plus
  `test_toy_site_audit_pilot_fans_out_adjudicates_and_writes_report`.
- **P7.4 Falsifier review:** explicitly check whether MageQA would still need to write its own
  supervisor/runtime. AC: any copied runtime logic is either moved into engine or documented as a
  gap requiring user decision.

Phase 7 AC gate: MageQA has a concrete migration path and a fake-backed pilot workflow proving open-ended
agent capability usage.

### Phase 8 - GoPro handoff and pilot mapping

Goal: prove live/streaming/scheduling requirements are represented by the same engine.

- **P8.1 Read and map:** reread GoPro workflow execution requirements, universal profile sheet, and
  home-inventory case. AC: mapping doc cites exact GoPro files.
- **P8.2 Scheduling pilot:** build/spec a fake-backed workflow with drop-stale/single-flight/latest
  policies around frame or event evidence. AC: toy records dropped stale work and accepted latest work.
- **P8.3 Home-inventory pilot:** map video/image evidence, frame sampler, object/location extractor,
  relation planner, deduplicator, evaluator, and index writer to capabilities. AC: fake-backed run uses
  `EvidenceRef`, not raw bytes in state. Current package proof: `run_toy_inventory_pilot()` plus
  `test_toy_inventory_pilot_uses_evidence_refs_scheduler_and_clarification`.
- **P8.4 Durability decision:** decide from measured pilot needs whether checkpointing is required
  now or remains a seam. AC: user-visible decision recorded before claiming restart recovery.

Phase 8 AC gate: GoPro continuity/scheduling pressure has been validated by fake-backed pilot behavior and
durability status is explicit.

### Phase 9 - Cross-project hardening and extraction decision

Goal: decide whether the internal package is ready to become an external git dependency.

- **P9.1 Cross-workload review:** compare Anki, MageQA pilot, GoPro pilot, and calendar toy against
  the architecture's completeness bar. AC: no shared runtime logic is duplicated in product packs.
- **P9.2 API cleanup:** remove product leakage, obsolete aliases, and accidental compatibility
  bridges unless explicitly approved. AC: public package API is product-neutral.
- **P9.3 Packaging:** add pyproject extras, versioning, import smoke tests, and install docs for
  another repo. AC: package installs into a clean venv and runs toy suite.
- **P9.4 Extraction decision:** either keep internal with reasons or move to separate git/submodule.
  AC: user decision recorded; no silent cross-repo migration.

Phase 9 AC gate: engine is either extractable with install instructions or blocked by named gaps.

### Phase 10 - Product restart/replay wiring

Goal: turn the existing checkpoint-store seam into product-level restart/replay only where a product
needs it.

- **P10.1 Need assessment:** collect failures or pilot requirements needing restart replay,
  long-pause resume, or persisted multi-turn state. AC: product-level restart/replay is justified by
  evidence.
- **P10.2 Checkpoint adapter wiring:** use `CheckpointStore`/`JsonlCheckpointStore` (or a product
  store) behind the core protocol. AC: one toy/product run resumes after process restart when this
  mode is enabled.
- **P10.3 Trace/replay split:** define what is trace-only vs state checkpoint. AC: replay docs show
  what can be inspected, rerun, or resumed.
- **P10.4 Product opt-in:** wire only products that need durability. AC: no durability overhead in
  short Anki runs unless configured.

Phase 10 AC gate: product-level durable resume is real if implemented; otherwise docs continue to
describe checkpoint stores as implemented but restart recovery as product-specific/unproven.

## Guard / falsifier tests to add (from review)

1. Recursion-limit: input that exhausts every retry branch → graceful fallback, no
   `GraphRecursionError`.
2. Engine-decoupling guard: no `import core|services|config` under `packages/ai_workflow_engine/`.
3. Supervisor-reality: a real workflow uses `WorkflowSupervisor`, or it is marked experimental.
4. Core-without-media: package imports and a non-media workflow runs with the `[media]` extra absent.
5. Profile-neutrality: a toy `WorkflowProfile` compiles with zero Anki/MageQA/GoPro field names.
6. Interface-only drift: every new protocol/abstract type has at least one runtime implementation or
   toy adapter before the phase can pass.
7. Raw-call guard: no direct OpenAI/Gemini/ElevenLabs/subprocess/browser-agent call from product
   workflow code unless it goes through a registered capability or approved adapter.
8. Silent-degradation guard: fallback/partial result without failed step, criticism/reason, fail
   mode, and user-visible effect fails validation.
9. Calendar-builder falsifier: a toy workload that overbooks the user's week must be rejected and
   retraced/replanned, not accepted as a plausible-looking schedule.

## Open / next

- F1/PF1 is resolved. YAML/profile config loading plus the current reusable runtime set are
  implemented and validated: capability runtime, runtime plans, trace/checkpoint sinks, human
  clarification, supervisor loop, agent/subprocess wrappers, external adapter, fan-out, scheduler
  modes, uncertainty result envelope, and evaluator retry/retrace/fallback controller.
- Current package/Anki milestone is validated by the evidence above and is ready for MageQA/GoPro
  handoff as an internal package.
- Downstream adoption work: optional external `[media]` packaging cleanup, product restart/replay
  policy wiring, and real MageQA/GoPro repo adoption with production adapters. These should be
  driven by the receiving project rather than hidden inside TGHandyUtils.
- Cleanup owner: after the user signs off on the handoff docs, migrate any remaining durable content
  from the discussion docs into permanent docs and delete transient discussion files that are no
  longer needed for review.
