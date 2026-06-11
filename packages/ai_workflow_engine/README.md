# AI Workflow Engine

Internal package being evolved into a reusable, executable AI workflow builder/runtime.

> **Status (2026-06-11): the executable engine layer is implemented.** `WorkflowDefinition` +
> `WorkflowBuilder` (declare), `WorkflowEngineBuilder` / `WorkflowEngine.from_config` (DI/IoC wiring),
> and `WorkflowExecutor` / `await engine.run(...)` (execution) are live, with branch, fan-out/gather,
> planner-plan execution, evaluator retry/retrace/replan/fallback, subworkflow-as-capability, human
> clarification, scheduling/cancellation, per-node model binding, and side-effect/privacy/budget gates
> all engine-owned. **LangGraph is the executor's internal backend** (a `WorkflowDefinition` is
> compiled to a `StateGraph` inside `executor.py`); products never import LangGraph. Anki is migrated
> onto this engine (its product LangGraph graph was deleted), and one single `WorkflowExecutor` runs
> the calendar / site-audit / inventory / card-generation example packs (see `examples.py`).

## Gap-Closure Notes (2026-06-10/11)

- **`pre_parse` cleaners (G2):** `StructuredLLMNode(..., pre_parse=WEAK_MODEL_CLEANER,
  max_repair_rounds=N)` sanitizes weak-model output (think-tags, ```json fences, chatter) before
  every parse attempt — no repair LLM call spent on formatting noise. Stock cleaners live in
  `ai_workflow_engine.parsing` (`strip_think_tags`, `extract_fenced_json`,
  `extract_first_json_object`, `compose_cleaners`). Payload changes are logged with hashes/lengths.
- **Vision node (G1):** `StructuredVisionLLMNode.run(values, images=[ImageInput(...)])` attaches
  images (path / base64 / URL, with roles) to the call — and to every repair attempt — using the
  provider-agnostic `image_url` content-part format (OpenAI- and Ollama-style models). Privacy
  invariant: image bytes never enter traces, usage events, or checkpoints — only
  `ImageInput.fingerprint()` (sha12/length/role) is recorded, and the checkpoint guard rejects
  `ImageInput` payloads outright. Persistent state keeps byte-free `EvidenceRef`s; bridge with
  `ImageInput.from_evidence(ref, loader)` at the call boundary. Metering/budget apply unchanged.
- **Bring your own LLM client (G4):** any `async def __call__(request: LLMRequest) -> LLMResponse`
  is a first-class `llm=` for the structured nodes — no LangChain wrapper needed. Example,
  direct Ollama HTTP:

  ```python
  import httpx
  from ai_workflow_engine import LLMRequest, LLMResponse

  class OllamaClient:
      def __init__(self, model="qwen2.5vl:3b", base="http://localhost:11434"):
          self.model, self.base = model, base

      async def __call__(self, request: LLMRequest) -> LLMResponse:
          payload = {"model": self.model, "stream": False,
                     "messages": [{"role": "user", "content": request.user,
                                   "images": [i.data for i in request.images if i.source == "base64"]}]}
          if request.system:
              payload["messages"].insert(0, {"role": "system", "content": request.system})
          async with httpx.AsyncClient() as client:
              data = (await client.post(f"{self.base}/api/chat", json=payload)).json()
          return LLMResponse(text=data["message"]["content"], model=self.model,
                             input_tokens=data.get("prompt_eval_count", 0),
                             output_tokens=data.get("eval_count", 0))

  node = StructuredVisionLLMNode(..., llm=OllamaClient(), pre_parse=WEAK_MODEL_CLEANER)
  ```

  Metering, capability timeouts, pre_parse, and repair rounds apply uniformly. **Cost integrity:**
  if the client reports no cost, the engine falls back to its price table by model name; when the
  model is unknown the usage event records `estimated_usd=None` with `cost_known=false` — never a
  phantom `$0.00`.
- **Per-node model binding (G3):** `WorkflowBuilder.step/branch/evaluate/plan(...,
  model_profile="name")` resolves a registered `ModelProfile` before invocation, injects it into
  `CapabilityContext.model_profile`, and records `model_binding` trace events. Fixed-client LLM
  handlers that cannot honor the binding fail loudly at registration/preflight or first call.
- **Single-flight cancellation (G7):** `SchedulingPolicy(strategy="single_flight_cancel")` is wired
  through the executor: a superseding run cancels the active worker, waits for real exit, promotes the
  latest run, and emits schedule trace decisions.
- **Planner node (G6):** `WorkflowBuilder.plan(...)` invokes a planner capability that emits a
  `PlanArtifact`. The executor validates all declared tasks before execution (registered capability,
  side-effect allow-list, max task count, no recursive planner task), executes sequentially or
  bounded fan-out, stores task status in `plan_artifact` + `node_outputs`, emits
  `plan:task_started/done/failed`, and supports bounded `Replan("planner_node")`. Plan context
  injection is opt-in per node with `inject_plan=True`, which exposes `render_plan(plan)` as
  `context.metadata["plan"]` only for that node call.

It is the reusable substrate for multi-step AI workflows. Per the 2026-06-07 and 2026-06-08
decisions we are building toward the full framework (see the architecture doc and executable
workflow contract); the lists below separate what exists today from what is still missing, so this
README never overstates the current package.

Design promise: this package is not Anki-specific and not an interface-only shell. The engine should
own reusable runtime behavior: capability registration/invocation, supervisor decisions, structured
LLM parsing and repair, model/profile selection, evaluator decisions, retry/retrace/fallback/fail
policy, trace, usage/budget/cost/cache reporting, artifact ownership, media/voice seams,
external-agent wrappers, fan-out/gather, scheduling, and human clarification hooks. Product projects
add domain workflow definitions, capability packs, prompts, schemas, adapters, rubrics, and
delivery.

Finished-product contract: a project should be able to define `WorkflowDefinition` blocks, register
capabilities/adapters, load a profile/config, and call `engine.run(...)`. If product code has to
manually sequence `runtime.invoke(...)` calls and implement branch/retry/retrace/fallback/scheduler
loops, this package is not finished.

Standalone package proof: day-to-day TGHandyUtils validation still uses Docker via `./test.sh`, but
the reusable package has an explicit release gate:
`packages/ai_workflow_engine/scripts/standalone_test.sh`. The script creates a fresh virtual
environment, installs `ai-workflow-engine[test]` editable from this package directory, and runs the
package-owned tests from inside `packages/ai_workflow_engine`. Those tests include a subprocess
guard that importing the package does not load host app modules such as `services`, `config`,
`handlers`, or `platforms`.

Low-level scripts are valid engine instruments only when registered as typed capabilities with
schemas, side-effect policy, timeouts, budget, trace, and failure semantics. This lets the same
runtime handle Anki flashcards, MageQA website QA, GoPro/home-inventory processing, and a future
calendar builder driven by availability, energy, priorities, task pool, and focus projects.

Implemented today (verified against the package source):

- `WorkflowDefinition`, `WorkflowBuilder`, `WorkflowEngineBuilder`, `WorkflowEngine.run(...)`, and
  `WorkflowExecutor` for product-neutral executable workflows;
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
- first-class executable node types for step, branch, fan-out/gather, evaluate, planner,
  subworkflow, and human clarification;
- planner-as-artifact execution with `PlanTask`, `PlanArtifact`, `render_plan`, task-level trace,
  bounded `Replan`, immutable completed/failed task history on replan, plan resume from serialized
  artifact payloads, shared budget caps, and opt-in plan context injection;
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

- production browser/MCP adapter packs for external agents; the package runtime has bounded agent
  episodes, but project adapters still need to bind real browser/CLI tools;
- automatic product restart/replay wiring; checkpoint stores exist, but each product still has to
  decide when to resume from latest engine checkpoints versus domain state. Planner artifacts are
  checkpoint-safe and can resume pending tasks when supplied back as planner input, but product
  restart policy is intentionally product-owned;
- real MageQA/GoPro repo adoption with browser/camera/domain adapters. The package now has
  fake-backed site-audit and inventory pilots, but open-ended agency and live/long-running scheduling
  are not battle-tested until those projects bind real adapters and domain state.

Product apps own their own state, schemas, prompts, validators, workflow definitions/topology, and
delivery. The engine must execute that topology. TGHandyUtils Anki is the first consumer and the
validation workload for every framework phase. Its generation graph currently registers nodes as
`CapabilitySpec`s and invokes them through `CapabilityRuntime` with a compiled `RuntimePlan`; this is
a transitional integration until Anki can run through the first-class workflow executor. The
auto-mode correction window now also uses the package `HumanClarificationCapability`, while Telegram
still owns the concrete button/timer UI.
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
- [Executable workflow engine contract](/Users/artemm/PycharmProjects/gopro-streaming/docs/_discussion/2026-06-08-executable-workflow-engine-contract.md)
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
