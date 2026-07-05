# AI Workflow Engine

Reusable, executable AI workflow builder/runtime.

Architecture source of truth: `docs/executable-workflow-engine-spec.md` at the repo root. This
README is a package summary; if it conflicts with the binding spec, the binding spec wins.

> **Status: `engine-v0.6.6` is the current release — pin it, build a wheel, don't track the live
> branch.** Older tags are unsupported. Declare (`WorkflowBuilder`) + wire (`WorkflowEngine.from_config`)
> + run (`await engine.run(...)`); branch, fan-out/gather, planner plans, evaluator
> retry/retrace/replan/fallback, subworkflows, human clarification, scheduling/cancellation, per-node
> model binding, bounded agent episodes, replay, memory, durable observability, and
> side-effect/privacy/budget gates are all engine-owned. LangGraph is the executor's internal backend
> (products never import it). The full capability list is at the end (**Capabilities**); the consumer
> how-tos are `docs/mageqa-handoff.md` / `docs/gopro-handoff.md`; runnable code is `examples.py`.

## Quickstart

```python
# Pin the tag + install (git + subdirectory; no PyPI):
#   pip install "ai-workflow-engine @ git+https://<repo>@engine-v0.6.6#subdirectory=packages/ai_workflow_engine"
from ai_workflow_engine import WorkflowBuilder, WorkflowEngine

flow = (WorkflowBuilder("my_flow")
        .step("prepare")                            # your capability: input -> structured output
        .evaluate("quality", on_reject="prepare")   # gate: accept / retrace (bounded)
        .step("deliver")
        .build())

engine = WorkflowEngine.from_config("config/my.yaml")   # DI: models / budget / safety
engine.register_pack(MyPack())                           # your capabilities
result = await engine.run("my_flow", MyInput(...))
# result.status / .output / .node("prepare").output / .usage / .trace
```

Truly trivial case? The one-call façade rides the same engine (budget/trace/usage/parse-repair
intact — sugar, never a bypass):

```python
from ai_workflow_engine import run_single_llm
verdict = await run_single_llm(my_llm, "Classify: {text}", Verdict, {"text": "…"})
```

You declare the flow + capabilities; the engine owns **all** orchestration (branch, retry, retrace,
fallback, fan-out, scheduling, side-effect/budget gates, trace, subworkflows) — you write no
coordinator loop (a static guard enforces this). Start from your product's guide above.

## Soul

The platform is an implementation-agnostic workflow engine plus a set of tool libraries:

- **Core owns domain-neutral mechanics and services.** The engine declares and runs workflows,
  fan-out, evaluator deepen loops, budgets, side-effect gates, trace, checkpoints, human gates,
  subworkflows-as-tools, artifact/evidence references, replay, scheduler/policy plumbing,
  capability registration, and the T1 workflow-memory seam. It does not import browser, TTS,
  camera, Telegram, or product schemas.
- **Tools are swappable libraries.** Provider and CLI details live in `ai_workflow_tools`, future
  sibling packages, or product adapters behind typed capabilities and `LLMCallable` clients.
- **One engine spans the complexity gradient.** A one-step Todoist reminder, Anki card generation,
  GoPro video/pipeline planning, and MageQA QA-session planning must all fit the same contracts.
  Simple workflows stay one-step simple; complex workflows opt into planners, fan-out, agents,
  subworkflows, memory, scheduling, evidence, and retrace without forking the runtime.
- **Workflow memory is not just conversation history.** The target model lets agents, planner
  nodes, and subworkflows opt into scoped memory of retries, discovered evidence, rejected plan
  branches, produced artifacts, evaluator decisions, and what worked or failed across runs. Memory
  remains byte-free, evidence-linked, explicit by scope, and disabled for flows that do not ask for
  it.
  Status: T1 is shipped for bounded agent prompt projection (`AgentMemory`, `FullReplayMemory`,
  `ImageEvictingMemory`) plus the deterministic
  `MemoryStore`/`MemoryNamespace(product, tenant, subject, kind)`/`InMemoryMemoryStore` contract.
  Durable/semantic stores, compacting/windowed/structured policies, and broader workflow/project
  scopes remain deferred until a named consumer proves the need.
- **Economics is part of the architecture.** Metered calls are budgeted explicitly; subscription or
  flat-rate workers report notional or unknown cost honestly instead of pretending to be free.
- **Rigidity is a three-axis choice.** Work item execution, work-set composition, and replay mode can
  each be rigid, semi-rigid, or flexible while staying inside the same workflow contracts.
- **Evidence goes both directions.** Inputs enter agent episodes as fingerprinted `input_assets`;
  outputs leave as salvaged `EvidenceRef`s and counts. Bytes stay out of state, trace, and
  checkpoints.
- **Workers are universal, not "LLM nodes."** Deterministic Python, API LLMs, local models,
  `claude -p`, `codex exec`, CLI/browser agents, media tools, humans, and subworkflows all fit the
  same capability contract: declared input/output schemas, policy, budget, trace, artifact/evidence,
  and normalized `CapabilityResult` semantics. The engine owns validation, retry/retrace/fallback,
  state transitions, and branch-label enforcement around every worker.
- **Decisions are RPG scenes (the goblin rule).** At every decision state the decider — LLM or
  deterministic — receives, as data generated from the machine itself, the current state PLUS the full
  set of legal outgoing transitions with their descriptions and live gate budgets ("you are in a
  forest, you have a stick, there is a goblin — fight / flee / negotiate, each explained, retries
  remaining shown"). Never a hand-maintained option list in a prompt. Two channels, by design: stable
  option *semantics* live on the transition (`describe` — data, so the machine stays serializable and
  replayable); situation-specific *expansions* (costs, odds, predicted consequences — "fight: lose
  ~5hp, 60% the goblin is gone") are computed facts produced by an upstream assess step and carried in
  the payload the decider already receives. A transition description must never become code or a
  callable.
- **Failures are loud.** Missing capabilities, denied side effects, and exhausted budgets produce
  explicit failures and trace events.

## Layered architecture — domain-neutral core, infinitely extensible edges

The framework is a swiss-army knife in the good sense: a domain-neutral engine that runs *any*
scenario, from a single deterministic task to a long-running agentic pipeline, plus pluggable layers
that extend it **by addition, never by editing the layer below**.

| Layer | What | Extend by |
|---|---|---|
| **L0 Core engine + universal services** | Graph execution, branch/retry/retrace/replan, fan-out, scheduling + cancellation, budgets, side-effect/privacy gates, trace, checkpoints, model binding, plan-as-data, artifact/evidence references, replay, human approval, evaluator/adjudicator mechanics, capability registry, policy/secrets plumbing, and the T1 workflow-memory seam. Zero domain knowledge. LangGraph is the hidden backend. | New generic mechanics only — never special cases |
| **L1 Universal executors** | HOW a model/tool is reached, behind one socket: the `LLMCallable` protocol (LangChain `.invoke` clients are a peer family, not a privilege). Today: LangChain clients, plain async callables (raw HTTP / Ollama), and `ConsoleLLMClient` in `ai_workflow_tools` for non-interactive `claude -p` / `codex exec` text-to-JSON calls. Future no-API executors can implement the same socket. | Implement `LLMCallable` — zero engine edits |
| **L2 Modality/tool packs** | Reusable packages and `WorkflowPack`s for non-mandatory modalities: CLI/browser agents, MCP suites, TTS/STT, image generation, video analysis/production, OCR, VLM frame analysis. Today: `ai_workflow_tools` ships a **described tool catalog** (`TOOL_CATALOG` / `render_tool_catalog()` / `register_from_catalog(engine, name)` — every shipped tool listed with description, kind, side effects; a completeness guard fails when a tool ships undocumented), CLI-agent capability support (`CliAgentCapability`, tri-state `allowed_tools`, MCP config env, salvage provenance), console clients (text-only completions run `--tools ""`), tool presets (`READ_ONLY`/`WEB`/`INVESTIGATION`/`NO_TOOLS` — plain tuples, `DEFAULT_AGENT_TOOLS is INVESTIGATION`), and media generation capabilities. Engine `vision.py` stays in core because image input is part of the LLM protocol. | Ship a package/pack, `register_pack(...)` |
| **L3 Domain packs** | Todoist, Anki, GoPro, MageQA, CRM, or other product-family packs. They package workflow definitions, prompts, rubrics, schemas, and adapters on top of L0-L2. | Product/family package |
| (L4 Products) | Deployment, UI/transport, account config, delivery. | `WorkflowBuilder` + capabilities |

Non-negotiable invariant across L1: parse/repair/`pre_parse`, metering (incl. `cost_known=false`
for subscription/no-API clients), budgets, timeouts, and per-node model binding apply
**identically through every executor** — policy is engine-owned, transport is pluggable. And
heterogeneous executors coexist per node in one workflow (strong API model on the planner step,
weak local model on extraction, browser-bridged on a zero-budget step) via `model_profile`.

## Universal worker contract

The engine is not a DAG runner for prompt calls. A prompt call is just one worker implementation
behind a capability. The executor is the main actor: it builds the invocation context, applies
policy and budget gates, invokes the worker, validates the result, records trace/usage/artifact
metadata, and chooses the next transition.

Every worker is normalized through the same shape:

- invocation: typed payload, node/task context, input `EvidenceRef`s, expected output schema,
  allowed tools/side effects, model profile, timeout/budget, branch/evaluator options, trace ids,
  and declared workflow-memory read/write scope where a built memory policy supports it;
- result: `CapabilityResult(status="accepted" | "rejected" | "partial" | "failed")` or a
  workflow-level suspension such as `requires_user_input`, plus structured output, branch label or
  evaluator criticism, usage/cost, artifact/evidence refs, `new_artifact_count`, metadata, and
  failure details.

This is a semantic contract, not a mandate to merge every adapter into one `WorkerInvocation` class
or a central inheritance tree. Transport-specific DTOs such as `LLMRequest` and `CliAgentRequest`
stay focused; the engine enforces the shared policy around them.

That is the anti-drift rule for future work: adding a smarter model, a weaker local model, a CLI
agent, a browser bridge, or a video tool must mean registering a capability/client/pack, not adding
a product-side orchestration loop or a special node class that bypasses validation. Simple flows can
leave artifacts, memory, agents, and planners disabled; complex GoPro/MageQA flows opt into them
under the same executor.

## Contract guardrails for consumers

Products should treat these as framework rules, not local style preferences:

- **One provider door.** Construct provider clients only in sanctioned factory/adapter modules. In
  this repo today: `services/llm_factory.py`, `ai_workflow_tools.media.image_generation`,
  `ai_workflow_tools.chatgpt_browser`, and `ai_workflow_tools.catalog` (the catalog's builders are
  the L2 construction door). Product workflow code should not create raw provider
  clients beside the engine path; doing so bypasses model selection, budget, trace, usage, and
  observability. Add a new construction site only as an explicit reviewed allow-list entry.
- **No product orchestration loop.** Product packs declare workflows and register capabilities; they
  do not hand-write retry, retrace, branch, fan-out, scheduling, or side-effect/budget loops around
  `runtime.invoke(...)`.
- **Declared statuses.** Capability results use `accepted`, `rejected`, `partial`, or `failed`;
  workflow-level projections use the engine statuses or a product enum mapped deterministically from
  them. Dashboard/API labels like `done`, `ok`, or `empty` are domain presentation only, not hidden
  engine states.

## Memory Status And Modes

The shipped memory slice is intentionally narrow and replay-safe:

- `FullReplayMemory` is the default and renders canonical agent history exactly as before.
- `ImageEvictingMemory(keep_last_images=N)` keeps only the most recent image-bearing tool turns as
  LLM images and replaces evicted images with explicit evidence/fingerprint audit markers.
- `StructuredStateMemory` (v0.8.0, GoPro P1) derives compact working-state facts from history via a
  PURE reducer (default: per-tool calls/args/ok/error tallies; product reducers injectable with
  `reducer_label` for observability) and appends them as the last memory-produced message —
  the weak-model anti-repeat fix, proven behaviorally in tests. Reducer/renderer output is
  validated byte-free LOUDLY (never silently stripped). Composes: `base=image_evicting`.
- `WindowedMemory(max_turns=N)` (v0.8.0) keeps the last N turns verbatim; dropped turns leave an
  activity tally + bounded excerpts of their outputs — findings survive by default, never a
  silent drop.
- `CompactingMemory(inner=…, token_threshold=…, keep_last_turns=…)` (v0.8.0) passes through
  byte-identical under the threshold; above it, old turns become a rule-based summary (pluggable
  compactor — code, not a hidden model call).
- Per-NODE selection: `.step("agent", memory={"mode": "structured_state", …})` mirrors
  `model_profile` — validated loudly at graph build, delivered per call, overrides the
  capability's constructed default.
- `MemoryStore` and `InMemoryMemoryStore` define deterministic exact/filter memory storage for tests
  and development. Durable stores and semantic search are not shipped.
- `MemoryNamespace(product, tenant, subject, kind)` is the public memory scope. `tenant` is the hard
  isolation boundary; `subject` is the product-owned target such as a site origin, camera, project, or
  run family; `kind` is the record family inside that subject.

Config/profile mode names are exact: `full_replay`, `image_evicting`, `structured_state`,
`windowed`, and `compacting` (nested `base`/`inner` configs supported; unknown options rejected
loudly). Alias strings such as
`full`, `default`, `image_eviction`, and `semantic_search` fail loudly. Memory is prompt input, not
control state; snapshot/resume replay must fast-forward recorded nodes without consulting live
memory.

## Generated process authoring status

`FlowArtifact` is shipped as constrained flow-as-data: an AI or deterministic builder can emit
validated `step` / `branch` / `evaluate` machines over registered capabilities, and
`engine.run_authored_flow(...)` runs them through the same executor, budgets, preflight, and trace
as hand-written workflows. That is enough for a narrow goal compiler, but it is not full arbitrary
pipeline authoring.

Near-term scope is `FlowArtifact` v1.5: add authorable `fanout`, registered `subworkflow` references,
and explicit `input_key` / `output_key` mapping while keeping the existing safety model. Authored
flows still must not contain planner or flow-author capabilities, register tools, execute arbitrary
code, or bypass side-effect/model-profile validation.

The endgame is a separate `ProcessArtifact` / `FlowArtifact` v2 for durable generated processing
pipelines: richer DAG/dataflow, video/segment fan-out, artifact routing, workflow-memory scopes, and
compile/register/reuse lifecycle. That is future-stage only and requires explicit Artem approval
after v1.5, T2 memory/storage needs, and the artifact/evidence store have proven functional in
narrower scope.

## Feature Requests And Gaps

When a product needs something the engine or tools do not cover, file the request against the
smallest reusable layer instead of patching product orchestration around the engine:

- workload and named consumer proving the need;
- why `WorkflowBuilder` plus registered capabilities/packs cannot express it today;
- proposed layer: L0 core mechanic, L1 executor/client, L2 modality/tool pack, or L3 domain pack;
- data, privacy, side-effect, budget, replay, and artifact/evidence impact;
- acceptance tests or product smoke checks, run through the repo-approved wrappers.

Default pushback rule: product-specific behavior starts as an adapter, capability, or pack. Core
changes need a reusable mechanic, fail-loud validation, and tests that prove simple flows keep their
low overhead.


## Capabilities

The full set in `engine-v0.6.6` (older tags are unsupported):

- **Declare + run.** `WorkflowBuilder` (step / branch / evaluate / fanout / plan / subworkflow /
  human), `WorkflowEngine.from_config` (DI for models/budget/safety), `await engine.run(...)`. A branch
  label that closes a loop requires a pre-set gate (`bounds=`/`exhausted=`) — ungated cycles fail loudly. An `inject_machine=True` branch additionally requires a `describe` entry for every decision label (goblin rule) — undescribed options fail validation.
- **Bring your own LLM client.** Any `async def __call__(request: LLMRequest) -> LLMResponse` is a
  first-class `llm=` for the structured nodes — no LangChain wrapper needed (direct Ollama/HTTP works).
- **Weak-model hardening.** `StructuredLLMNode(..., pre_parse=WEAK_MODEL_CLEANER, max_repair_rounds=N)`
  sanitizes weak-model output (think-tags, json fences, chatter) before each parse; stock cleaners in
  `ai_workflow_engine.parsing`. Payload changes logged with hashes/lengths.
- **Vision (byte-free).** `StructuredVisionLLMNode.run(values, images=[ImageInput(...)])` attaches
  images (path/base64/URL) to the call and every repair; bytes never enter traces/usage/checkpoints —
  only `ImageInput.fingerprint()` (sha12/length/role) is recorded. Persistent state keeps `EvidenceRef`s.
- **Agent brain.** `LLMAgentPlanner` (vision loop, per-turn budget, structured finish + repair) +
  `ReplayPlanner` (freeze an episode into a zero-LLM regression); `build_llm_agent_capability(...)`.
- **Flow-as-data.** `engine.run_authored_flow(FlowArtifact, payload)` — an LLM emits a constrained
  workflow, validated before compiling, run on the same rails (recursion firewall). Bounded recursive
  planning; parallel sub-workflows (failure-isolated).
- **Budget + honest cost.** Per-call token/image/USD caps; `metered` vs `subscription_notional`
  (`cost_known=false`, never phantom $0).
- **Durable suspend/resume + memory.** `result.snapshot` + `engine.resume` (cross-process); cross-run
  memory via the `MemoryStore` seam (bring your own sqlite for durability).
- **Media** lives in `ai_workflow_tools.media` (image/voice); the engine stays provider-neutral
  (`vision`/image-input stays in the engine as LLM protocol).
- **One-call façade.** `run_single_llm(llm, template, OutputModel, values)` /
  `run_single_step(fn, payload)` — a 1-node workflow on the real engine (usage + trace + budget +
  loud failure included); `engine=` reuses a configured engine for volume.
- **Discoverable toolset.** `from ai_workflow_tools import TOOL_CATALOG, render_tool_catalog,
  register_from_catalog` — every shipped tool described (catalog reuses
  `CapabilitySpec.description`); presets `READ_ONLY/WEB/INVESTIGATION/NO_TOOLS`; CLI tool policy is
  explicit per call type (`--tools ""` completions, scoped-Read staged vision, tri-state agent
  allow-list with Bash-honest side-effect declaration + pre-spawn denial).

## Config-first observation (v0.6.4) + evidence resolution (v0.6.5)

Declare `observation: {enabled, bundle_dir, retention_limit, capture, artifacts,
artifact_max_bytes}` in the application config and the engine owns everything per run:
auto-opened bundle, routed trace/details/usage, truthful finalization (failures included),
retention pruning, and `result.observation_bundle_path`. Run artifacts (anything returned in
`CapabilityResult.artifacts`) are archived into the bundle with an `artifacts.json` manifest
(source_path → bundle_path + sha256 + honest `skip_reason`), so dashboards resolve archived
artifact refs durably — and evidence prunes WITH its bundle: `retention_limit` is the single
cleanup policy. If a product emits a custom non-file `EvidenceRef.uri`, it must also return the
matching `WorkflowArtifact.path` (or keep the URI equal to that path) so the dashboard can join the
evidence to `artifacts.json`. Products write ZERO bundle code; `observation_bundle=` stays as the
explicit escape hatch.

## Prompt files + bundle ownership (v0.6.3)

Prompts can live as files under a locked root with STRICT rendering (missing variable = loud
failure before the model call; optional Jinja dialect, never a core dependency):
`StructuredLLMNode(prompt_ref=PromptRef("team/route.txt"), prompt_renderer=engine.prompt_renderer)`.
And the observation bundle has ONE owner: pass `observation_bundle=` to `engine.run(...)` — the run
session finalizes it exactly once with the true terminal status (raise → failed; the
`terminal_status=` hook lets product post-validation mark it failed before it is written).

## Machine identity + suspension rules (v0.6.2)

Definitions carry a content digest; compiled graphs/registries key on `(workflow_id, digest)` —
re-registering a changed same-id definition runs the new machine, never a stale compile. Nested
suspension is rejected loudly (subworkflow children may not contain `human` nodes; runtime child
suspensions fail the parent node) — waits live at the top level until nested snapshots ship.
`result.trace` is a per-run session buffer (sink-independent, no cross-run growth), and planner
fanout enforces `max_total_planned_tasks`.

## Hardened seams (contract guards + run isolation)

Mutation-verified guards ship in the engine tests: one provider door, no product orchestration
loop, closed run-state vocabulary, engine-imports-no-products, and the executor/node boundary
(`NodeExecutionServices` — handlers run against a fake services object in tests). Each run gets an
internal run session: concurrent runs on one engine are isolation-tested (trace/usage/detail
partition by run id), resume rebuilds without re-executing completed nodes, and an attached
observation bundle finalizes with the run's terminal status, failures included. Usage accounting
is split (budget/pricing/provider/events/rendering) behind the stable `ai_workflow_engine.usage`
facade.

## Observability — log → bundle → viewer

A run appends durable events to a per-run **bundle**
(`observations/<run_id>/{trace,details,usage}.jsonl` + `meta.json` + `definition.json`); nothing is
rendered in the hot path. The separate **`ai_workflow_viewer`** package reads bundles (via
`EventSource`/`FileEventSource`) and builds every view on demand — the state-machine diagram
(`workflow_to_mermaid` / `save_workflow_html`), the observation graph + timeline
(`save_observation_html`), and a run chooser / JSON-detail server (`JsonlObservationViewer` /
`serve_viewer`). Capture has an off-switch; the bundle dir defaults to `data/observations` (override
`OBSERVATION_DIR`). The disk bundle is transport #1 — the design stays swappable to a bus/live consumer.

**One door for model calls (consumers):** construct model clients through sanctioned factory/adapter
modules. A direct provider client in workflow/product code is a reviewed escape hatch, not the
default; otherwise you lose observability, cost accounting, and model-swap.
