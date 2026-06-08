# AI Workflow Engine

Internal package for reusable AI workflow orchestration primitives.

It is the reusable substrate for multi-step AI workflows. Per the 2026-06-07 decision we are
building toward the full framework (see the architecture doc); the lists below separate what exists
today from what is planned, so this README never overstates the current package.

Design promise: this package is not Anki-specific and not an interface-only shell. The engine should
own reusable runtime behavior: capability registration/invocation, supervisor decisions, structured
LLM parsing and repair, model/profile selection, evaluator decisions, retry/retrace/fallback/fail
policy, trace, usage/budget/cost/cache reporting, artifact ownership, media/voice seams,
external-agent wrappers, fan-out/gather, scheduling, and human clarification hooks. Product projects
add domain capability packs, prompts, schemas, adapters, rubrics, and delivery.

Low-level scripts are valid engine instruments only when registered as typed capabilities with
schemas, side-effect policy, timeouts, budget, trace, and failure semantics. This lets the same
runtime handle Anki flashcards, MageQA website QA, GoPro/home-inventory processing, and a future
calendar builder driven by availability, energy, priorities, task pool, and focus projects.

Implemented today (verified against the package source):

- workflow goal + run context, trace events, artifacts, and usage summaries;
- workflow runner with explicit graph runtime config and policy-owned recursion fallback, plus
  instrument registry;
- optional supervisor decision planner (toy-tested; production use starts when a product binds it);
- structured LLM node with Pydantic parsing and one repair attempt;
- workflow profiles, runtime plans, scheduling policy, runtime limits, safety policy, session state,
  evidence references, model profiles, fail modes, `WorkflowResult` uncertainty states, and
  evaluator/retry/retrace models;
- YAML-backed workflow config loading into `WorkflowConfigBundle` with code-defaults → file → env
  override precedence, app-injected env prefixes, model-profile registry access, structural warnings,
  and secret-like key/value rejection;
- capability specs, capability registry, capability runtime, typed context/result envelopes, bounded
  fan-out/gather, `RuntimePlan` side-effect denial before handler execution, and a bounded
  supervisor-loop controller for "choose next capability" workflows;
- generic human clarification capability with pluggable channels, supervisor-loop pause/resume, and
  provisional continuation under policy;
- swappable trace sink contract with in-memory and JSONL sinks;
- checkpoint stores with in-memory and JSONL implementations, raw-byte rejection, and
  `WorkflowLoopController` checkpoint writes for running/waiting/completed/failed transitions;
- external process capability with timeout and partial stdout/stderr salvage;
- bounded agent capability with injected controller, scoped registered tools, max steps/tool calls,
  per-step trace, tool-call history, and subscription-mode metadata;
- external/domain write adapter with typed requests, idempotency, privacy rejection, and JSONL audit
  sink;
- scheduler policy helper for run-immediately, queue, replay-process-all, run-latest,
  live-latest-only, drop-stale, drop-not-queue, coalesce, fan-out-gather, backend concurrency caps,
  and single-flight-cancel decisions that hold the active lock until completion;
- generic `EvaluationController` for product-owned quality evaluators: retry the failed capability
  with criticism, retrace to an earlier capability, route to fallback/repair, ask the user, or fail
  under explicit caps;
- local usage/cost metering and budget checks;
- prompt-template loading with an injectable prompt root;
- switchable image-generation provider seam (`openai`, `gemini`, or bounded comparison mode);
- provider-neutral voice-generation seam for product workflows that need audio artifacts;
- optional media-provider dependency boundary: core package and media modules import without the
  OpenAI SDK, and concrete provider calls are lazy/extra-backed;
- non-Anki toy examples for summary and calendar-building with evaluator rejection/retrace;
- fake-backed site-audit and inventory pilots (`run_toy_site_audit_pilot`,
  `run_toy_inventory_pilot`) that exercise MageQA/GoPro-shaped runtime pressure without importing
  those products;
- TGHandyUtils auto-mode's 5s correction window now uses `HumanClarificationCapability` with a
  Telegram-owned channel, `RuntimePlan` side-effect policy, and traceable pending/answered/provisional
  responses.

Boundary / downstream adoption work:

- recursive subworkflows are supported by registering graphs/callables as capabilities; a separate
  packaging layer should wait for a second product that needs it;
- production browser/MCP adapter packs for external agents; the package runtime has bounded agent
  episodes, but project adapters still need to bind real browser/CLI tools;
- automatic product restart/replay wiring; checkpoint stores exist, but each product still has to
  decide when to resume from latest engine checkpoints versus domain state;
- real MageQA/GoPro repo adoption with browser/camera/domain adapters. The package now has
  fake-backed site-audit and inventory pilots, but open-ended agency and live/long-running scheduling
  are not battle-tested until those projects bind real adapters and domain state.

Product apps own their own state, schemas, prompts, validators, graph topology, and delivery.
TGHandyUtils Anki is the first consumer and the validation workload for every framework phase. Its
generation graph registers nodes as `CapabilitySpec`s and invokes them through `CapabilityRuntime`
with a compiled `RuntimePlan`; the auto-mode correction window now also uses the package
`HumanClarificationCapability`, while Telegram still owns the concrete button/timer UI.
If a later product must copy supervisor, trace, budget, retry, retrace, artifact, scheduler, or
capability-runtime logic, that is a package gap to fix rather than expected product work.

Related architecture and workload docs:

- [Workflow engine architecture](/Users/artemm/PycharmProjects/TGHandyUtils/docs/workflow-engine-architecture.md)
- [Workflow engine config architecture](/Users/artemm/PycharmProjects/TGHandyUtils/docs/workflow-engine-config-architecture.md)
- [Workflow engine extraction discussion](/Users/artemm/PycharmProjects/TGHandyUtils/docs/_discussion/2026-06-07-workflow-engine-extraction.md)
- [Anki acceptance criteria](/Users/artemm/PycharmProjects/TGHandyUtils/docs/anki-acceptance-criteria.md)
- [MageQA QA orchestrator design](/Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md)
- [MageQA agentic tester architecture](/Users/artemm/PycharmProjects/MageQA/docs/14-agentic-tester-architecture.md)
- [MageQA package handoff](docs/mageqa-handoff.md)
- [GoPro workflow execution engine requirements](/Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md)
- [GoPro universal profile sheet](/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/UNIVERSAL_PROFILE_SHEET.md)
- [GoPro home inventory case](/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/HOME_INVENTORY_CASE.md)
- [GoPro package handoff](docs/gopro-handoff.md)

Prompt templates default to `./prompts` from the current working directory. Set
`AI_WORKFLOW_PROMPT_ROOT` or pass `root=` to `load_prompt_template` when another project stores
prompts elsewhere.

Image generation stays provider-neutral at graph level: product code sends an
`ImageGenerationRequest`, while the product profile/config selects the adapter (`openai`,
`gemini`, or bounded comparison mode). Comparison mode is intentionally paid and bounded by the
normal workflow image-call budget. Provider-specific knobs such as Gemini response-format sizing
belong in the product profile or config loader, not in engine APIs. TGHandyUtils currently maps
Anki env/profile values onto this neutral request shape.

Voice generation is also exposed as a product-neutral request/response seam. The current adapter is
ElevenLabs TTS; product graphs decide when to call it, how many calls are allowed, and how generated
audio is attached to their final artifacts.
