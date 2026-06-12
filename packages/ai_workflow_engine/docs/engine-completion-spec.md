# Engine Completion Spec — agentic loop, CLI workers, economics, observability

Status: **approved scope, ready to implement** (2026-06-12).
Audience: the implementing developer. This spec is self-contained — every operational fact needed
(including the MageQA CLI invocation details) is embedded with its source citation; you should not
need to re-derive anything from the MageQA repo.

> [claude]: **UPDATE 2026-06-12 (later): all review items below are now FOLDED INTO THE SPEC BODY**
> — #1 → §10 (docker test commands, tools-suite collection, standalone gates, per-WP commit
> cadence per the repo's standing rule); #3 → §7.2 (`McpServerConfig.env`); #4 → §7.3.4 (reuse
> `ai_workflow_engine.parsing`); #5 → §7.5 (`ConsoleLLMClient`) ; #6 → §7.2/§7.3.0
> (`input_assets`); ACs → §11.1 (AC-R3…AC-R6) + extended §11.2 pilot + §1b use cases. The
> implementer follows the BODY; this block remains as review history only.
>
> [claude]: **Engine-side alignment review, 2026-06-12 — APPROVED with 3 process corrections.**
> Conceptually this spec is fully aligned with the engine's layered contract
> (`docs/executable-workflow-engine-spec.md` §2b): §0 restates the soul faithfully; WP5's
> `ai_workflow_tools` is the L2 packaging convention + the planned L1 console executor; WP1 grows
> the `LLMRequest` protocol additively against a named consumer (the sanctioned bar — recorded in
> spec §2c risk #4); WP4 deepens RC2 cost-honesty; WP6 closes the known streaming-sink gap via the
> predicted extension point; WP2's `ReplayPlanner` adds the mode axis with ZERO new node kinds.
> No invariant violations found. Corrections for the implementer (process, not architecture):
> 1. **Testing commands (§10) violate TGHandyUtils repo rules.** Inside this repo, day-to-day runs
>    are docker-only: `./test.sh unit -- packages/ai_workflow_engine/tests/ --cov-fail-under=0 -q`
>    (never bare `python -m pytest` on the host; sole exception = the standalone clean-venv gate
>    `packages/ai_workflow_engine/scripts/standalone_test.sh`). WP5 must also wire
>    `packages/ai_workflow_tools/tests/` into `test.sh` collection (same pattern as the engine
>    package, test.sh TEST_TARGET line) and give the tools package its own standalone venv gate.
> 2. **"Do not commit anything" conflicts with the repo's commit-at-every-green-gate cadence**
>    (Artem-approved for the gap stream; uncommitted multi-WP trees were a documented pain point).
>    Owner decision required before implementation starts: per-WP commits (recommended) vs
>    working-tree review.
> 3. **`McpServerConfig` (§7.2) lacks an `env` field** — MCP servers commonly need env vars;
>    additive, cheap now, breaking later. Recommend adding `env: Dict[str, str] = {}` in WP5.
> 4. **WP5 §7.3.4 reimplements lenient JSON extraction — forbidden duplication.** The engine
>    already ships strictly better cleaners (`ai_workflow_engine.parsing`: fence-aware,
>    balanced-brace, string-aware `extract_first_json_object` vs the spec's naive
>    "first `{` to last `}`"). `CliAgentCapability` must reuse
>    `compose_cleaners(extract_fenced_json, extract_first_json_object)` — one implementation of
>    weak-output hygiene engine-wide, whether the text comes from an API, a local model, or a CLI.
> 5. **WP5 is missing the thin L1 chat client.** `CliAgentCapability` is an *agent-episode
>    substrate* (MCP, tools, workspace, salvage). The engine's layered contract also wants the
>    simple member: `ConsoleLLMClient(LLMCallable)` — same `CliFlavor` argv/envelope machinery,
>    no MCP/no tools, `LLMRequest → LLMResponse` — so **structured nodes** (parse/repair/
>    `pre_parse`/model-binding) can run on non-interactive `claude -p`/`codex exec` directly.
>    ~50 lines on top of WP5's flavors; ship it in `ai_workflow_tools/cli_agents/` beside them.
>    (Layering note for the record: **L1 is the socket in core; members may live anywhere** —
>    embedding vendor argv recipes in core would buy nothing and put CLI flag drift on the core
>    release cycle. The tools-lib placement follows the engine spec §2b, and is correct.)
> 6. **Assets-IN symmetry (small WP5 addition; Artem 2026-06-12).** Assets-OUT is contracted
>    (salvage → role-tagged `EvidenceRef` + `new_artifact_count`, salvage-always); assets-IN is
>    informal (product drops files into `workspace_dir`, untraced). Add
>    `CliAgentRequest.input_assets: List[EvidenceRef] = []`: the capability materializes each ref
>    into the workspace (product-injected loader, same pattern as `ImageInput.from_evidence`),
>    records fingerprints (never bytes) in trace metadata, and documents the prompt convention
>    (relative paths under an `inputs/` subdir). ~30 lines. Rationale: input provenance is the
>    other half of "evidence or it didn't happen", and freeze-to-replay (WP2 mode axis) is
>    incomplete if an episode's inputs aren't part of its record.
>    **Explicit non-goals, by rule (engine spec §2c #4):** no typed per-domain asset bundles
>    (video-job, screenshot-set…) in universal layers — that is role-tagged `EvidenceRef`s +
>    product schemas at prompt level until TWO products share a bundle shape (then it's an L2
>    pack). And "LLM returns a set of tasks to execute" needs NO new contract — that is the
>    existing `PlanArtifact` consumed by a planner node (allow-list validation included): wire a
>    CLI/console client as the planner capability with `output_model=PlanArtifact`.

Why: the engine's `mageqa-handoff.md` declares adoption-ready, but four runtime seams are missing or
unproven, and without the CLI-worker economics the whole product is non-viable (a metered-API site
audit costs $10s–$100s; subscription CLI workers make it ~flat-rate). This spec closes the gaps as
**purely additive** changes that extend — never break — the existing consumers (TGHandyUtils Anki,
GoPro case, example packs).

Sources of truth:
- Engine source: `packages/ai_workflow_engine/ai_workflow_engine/` (cited as `pkg/…`)
- MageQA proven runtime: `/Users/artemm/PycharmProjects/MageQA/src/mageqa/agent_browser.py`,
  `workers.py`, `trace.py` (facts embedded below; cited as `mageqa/…`)
- Consumer surface: `services/content/anki_generation_graph.py:29-60,318-572`,
  `services/openai_service.py:11-13`, `services/content/anki_scenario_planners.py:17-18`

---

## 0. Soul & values (propagate to every high-level README — WP7)

The platform is an **implementation-agnostic workflow engine plus a SET of tool libraries**:

1. **Engine core knows no domain.** It owns orchestration mechanics only: declare → run, fan-out,
   evaluator deepen-loops, budgets, side-effect gates, trace, checkpoints, human gates,
   subworkflows-as-tools. It never imports a browser, a TTS SDK, or a product schema.
2. **Tools are swappable libraries on stable seams.** Text→speech, reference-image generation,
   *run a CLI agent (`claude -p` / `codex exec`)*, browse a site, write a DB, and **whole
   subworkflows** are all "tools": typed capabilities with schemas, side-effect class, timeout,
   budget, trace. Provider/impl specifics live in the tool lib (or product), never in the core.
3. **Economics is a design axis, not an afterthought.** Subscription/flat-rate workers are the
   default execution substrate; metered API calls are the explicitly budgeted exception. Cost is
   always honest: known, estimated-with-source, or visibly unknown — never phantom `$0.00`.
4. **Dynamic, observable work sets — rigidity is a spectrum on THREE axes.**
   - *Execution axis (per work item):* **rigid** = deterministic capability, exact steps/asserts;
     **semi-rigid** = bounded agent episode (fixed skeleton, narrow tool scope, structured success
     criteria, AI judges outcomes); **flexible** = open agent episode (goal only, full tool scope,
     vision judgment).
   - *Set-composition axis (per use case):* the set of work items itself can be **rigid** (fixed
     predefined suite — e.g. regression), **semi-rigid** (predefined base suite + coordinator-added
     items), or **flexible** (plan fully emitted by the coordinator LLM from site signals + rubric).
     All three are plan-capability variants: deterministic plan, merge(static, LLM), pure LLM plan.
   - *Mode axis (same item over time):* a flexible episode is **recordable and freezable** — its
     step history becomes a rigid replay (reproducible regression) of what was once exploration.
   All variants run in one flow, and every step is traceable live.
5. **Evidence or it didn't happen — on BOTH sides of an episode.** Agent claims must reference
   salvaged artifacts (`EvidenceRef`); inputs handed to an episode are staged + fingerprint-traced
   the same way (`input_assets`), so provenance and freeze-to-replay cover what went in, not just
   what came out. Byte payloads never enter state/trace/checkpoints (fingerprints only).
6. **Fail closed, loudly.** Unregistered capability, denied side effect, exhausted budget →
   explicit failure + trace event, never a silent no-op.

Current state honesty: the voice/image generation *seams* (provider-neutral requests, lazy optional
deps) already follow value #2 in-package (`pkg/voice_generation.py`, `pkg/image_generation.py`),
but no separate tool library exists yet. WP5 creates the first one.

---

## 1. Target package layout

```
packages/
├── ai_workflow_engine/            # CORE — impl-agnostic (this package, extended in place)
│   └── ai_workflow_engine/
│       ├── llm_protocol.py        # WP1: + tools / multi-turn / tool_calls
│       ├── engine/agent_planner.py# WP2: NEW — shipped LLM planner for AgentCapability
│       ├── usage.py, models.py    # WP3+WP4: budget matrix + cost classes (optional fields)
│       └── engine/capabilities.py # WP6: + streaming trace sinks
└── ai_workflow_tools/             # NEW — first tool library (WP5)
    ├── pyproject.toml             # name "ai-workflow-tools", depends on ai-workflow-engine
    ├── scripts/standalone_test.sh # clean-venv release gate (mirrors the engine's)
    └── ai_workflow_tools/
        └── cli_agents/            # models.py (CliFlavor/requests) + capability.py
                                   #   (CliAgentCapability) + console.py (ConsoleLLMClient, §7.5)
                                   #   + claude_p / codex_exec flavor constants
```

Rule going forward (record in both READMEs): anything provider-, CLI-, or domain-specific lands in
`ai_workflow_tools` (or a future sibling lib / the product), not in core. The in-core media seams
stay where they are for now (Anki imports them); migrating them into the tools lib is a separate
later effort and out of scope here.

## 1b. Use cases → which contract carries them (Artem, 2026-06-12)

The dividing rule (now also engine spec §2b value (d)): **contract the mechanics every case
shares — transport, provenance, validation, salvage; never the per-domain shapes.**

| Use case | Contract that carries it | New code? |
|---|---|---|
| Simple console call: text in → JSON out | `ConsoleLLMClient` (§7.5) behind a structured node — parse/repair/`pre_parse`/model-binding for free | WP5 (§7.5) |
| Site audit: screenshots + JSON findings out | `CliAgentCapability` salvage → role-tagged `EvidenceRef`s + `new_artifact_count` grounding input | WP5 (already) |
| Asset transformation (e.g. video creation from clips, image set processing) | `input_assets` in (staged + fingerprinted) → work in workspace → salvage globs out (`*.mp4` is just another glob + role) | WP5 (§7.2/§7.3.0) |
| "LLM, give me a set of tasks to execute" | NO new contract: console/CLI client as a **planner capability** with `output_model=PlanArtifact` — the planner node validates every task against registry + side-effect allow-lists before anything runs | composition only |
| Exploration frozen into regression | `AgentRunResult.steps` recording → `ReplayPlanner` (WP2); inputs included via `input_assets` provenance | WP2 |
| Live progress UI over any of the above | `CallbackTraceSink` / `AsyncQueueTraceSink` / `TeeTraceSink` (WP6) | WP6 |
| Typed per-domain bundles ("video job", "screenshot suite") | **Deliberately uncontracted** — role-tagged refs + product schemas at prompt level; graduates to an L2 pack only when TWO products share the shape (engine spec §2c #4 bar) | none, by rule |

---

## 2. Hard compatibility rules (violating any of these = rejected change)

- **Frozen signatures** (43 import sites in TGHandyUtils depend on them):
  `invoke_metered_chat(...)`, `check_budget_before_call(operation, node)`,
  `record_usage_event(event)`, `estimate_cost_usd(...)`, `StructuredLLMNode.__init__`,
  `CapabilityRegistry.register`, `WorkflowEngine.register_capability/register_workflow/run`.
  Extensions: new keyword-only params with defaults, or new functions. Never positional changes.
- **All new model fields optional with defaults** (`RuntimeLimits`, `WorkflowBudget`,
  `WorkflowUsageEvent`, `LLMRequest`, `LLMResponse`). Anki constructs these today.
- **Guard test must stay green:** `test_examples_contain_no_product_orchestration_loops` forbids
  `runtime.invoke`, `StateGraph`, manual loops in `pkg/examples.py`. New examples go through
  `engine.run()` only.
- **No raw bytes in state/trace/checkpoints.** Artifacts → `EvidenceRef` + `ImageInput.fingerprint()`
  pattern (`pkg/vision.py:40-49`); the checkpoint guard already rejects `ImageInput` payloads.
- Run the full engine test suite plus TGHandyUtils Anki tests after every WP (see §10).

---

## 3. WP1 — tool-calling, multi-turn LLM protocol (`pkg/llm_protocol.py`)

Today `LLMRequest` is single-turn `system/user/images/metadata` (`llm_protocol.py:26-32`) and
`LLMResponse` is `text/model/tokens/estimated_usd/raw` (`:35-46`). Additive extension:

```python
class ToolSpec(BaseModel):
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)   # JSON Schema

class ToolCallRequest(BaseModel):
    call_id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

class ToolResult(BaseModel):
    call_id: str
    content: str = ""                                   # textual tool output
    images: List[ImageInput] = Field(default_factory=list)  # screenshot tool results → vision
    is_error: bool = False

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    images: List[ImageInput] = Field(default_factory=list)
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)  # assistant turns
    tool_results: List[ToolResult] = Field(default_factory=list)     # tool turns

class LLMRequest(BaseModel):
    system: Optional[str] = None
    user: str = ""                                  # CHANGED: default "" (see compat note)
    images: List[ImageInput] = Field(default_factory=list)
    messages: List[ChatMessage] = Field(default_factory=list)   # NEW: multi-turn; when non-empty,
    tools: List[ToolSpec] = Field(default_factory=list)         #   system/user/images are ignored
    tool_choice: Optional[str] = None               # NEW: None|"auto"|"required"|tool name
    metadata: Dict[str, Any] = Field(default_factory=dict)

class LLMResponse(BaseModel):
    text: str = ""                                  # CHANGED: default "" (tool-call-only turns)
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)  # NEW
    stop_reason: Optional[str] = None               # NEW: "stop"|"tool_use"|"length"|None
    # …existing fields unchanged
```

Compat notes:
- `user: str` → `user: str = ""`: existing callers always pass `user`, so relaxing the default is
  non-breaking; add a validator: at least one of `user`/`messages` must be non-empty.
- `text: str` → default `""`: existing code paths read `.text` after constructing with it set —
  verify no consumer relies on construction-time validation error (grep shows none).
- Single-turn behavior is bit-identical when `messages`/`tools` are empty.
- Clients that ignore `tools` (e.g. the README's Ollama example) keep working; the planner (WP2)
  detects `tool_calls == []` with `tool_choice="required"` and treats it as a finish/parse attempt.
- `record_callable_usage` (`llm_protocol.py:62-104`) unchanged; WP4 extends the *event*, not this
  function's signature (new keyword-only `cost_class`/`notional_usd` params with defaults allowed).

Tests (`tests/test_llm_protocol.py`, extend):
1. Round-trip: request with `messages` + `tools` reaches a fake client intact; single-turn requests
   unchanged (assert exact legacy field values).
2. `ChatMessage(role="tool")` carrying `ToolResult.images` serializes; fingerprints (not bytes)
   in any logged metadata.
3. Validator: empty `user` + empty `messages` → ValidationError.

---

## 4. WP2 — shipped LLM agent planner (`pkg/engine/agent_planner.py`, NEW)

`AgentCapability` runs a bounded episode but the brain is a bare Protocol
(`pkg/engine/agent.py:22-31`) — no implementation ships. Deliver one:

```python
class LLMAgentPlanner:
    """AgentEpisodePlanner driven by any LLMCallable with tool-calling (WP1)."""

    def __init__(
        self,
        llm: LLMCallable,
        *,
        tool_specs: Mapping[str, ToolSpec],        # built from CapabilityRegistry specs:
                                                   #   input_model → JSON schema (CapabilitySpec
                                                   #   already derives schema names, models.py:491)
        system_prompt: Optional[str] = None,
        output_model: Optional[type[BaseModel]] = None,   # parse "finish" output like
        pre_parse: Optional[Callable[[str], str]] = None, # StructuredLLMNode does
        max_repair_rounds: int = 1,
        node_name: str = "agent_planner",
    ) -> None: ...

    async def next_step(self, context, request, history) -> AgentStepDecision: ...
```

Behavior (follow the metering/repair idiom of `pkg/engine/llm_node.py:204-276`):
1. **Stateless reconstruction:** each `next_step` rebuilds `messages` from
   `request.prompt` (first user turn) + `history` (each `AgentToolStep` → one assistant turn with
   the `ToolCallRequest` + one tool turn with the `ToolResult`).
2. **Image tool results:** if a tool step's `output` contains `ImageInput` objects, or
   `EvidenceRef`s with `media_type` `image/*` plus a configured
   `image_loader: Callable[[EvidenceRef], bytes]`, attach them to the tool turn via
   `ImageInput.from_evidence` (`pkg/vision.py:62-79`). This is the screenshot→vision loop —
   the core operation of semi-rigid/flexible test scenarios.
3. **Budget per turn:** `check_budget_before_call("chat", node)` before each LLM call;
   `record_callable_usage(response, node=…, attempt=step_index)` after (`usage.py:120-135`,
   `llm_protocol.py:62-104`). WP3 caps apply automatically.
4. **Decision mapping:** `response.tool_calls[0]` → `AgentStepDecision(action="tool",
   tool_name=…, payload=arguments)`. Tool name not in `request.allowed_tools` → return it anyway;
   `AgentCapability` already rejects against the allow-list (`agent.py:105-116`) — do not
   duplicate enforcement.
5. **Finish:** no tool calls → treat `response.text` as final output; if `output_model` set, apply
   `pre_parse` + parse + up to `max_repair_rounds` repair turns (append a user turn carrying the
   parse error, same contract as `StructuredLLMNode`); parsed result →
   `AgentStepDecision(action="finish", output=parsed)`. Repair exhausted →
   `action="fail"` with the last error as rationale.

Also export a convenience builder so products write zero glue:

```python
def build_llm_agent_capability(llm, registry: CapabilityRegistry, *, allowed_tools, name=…,
                               side_effects=(), **planner_kwargs) -> AgentCapability
```

**Freeze-to-replay (`ReplayPlanner`, same file)** — the mode axis of soul value #4. A flexible
episode's `AgentRunResult.steps: List[AgentToolStep]` (`models.py:207-216`) *is* the recording:
tool names + payloads + observed outputs. Ship a second planner that re-executes it rigidly:

```python
class ReplayPlanner:
    """AgentEpisodePlanner that replays a recorded step history verbatim — a frozen flexible
    episode becomes a rigid, reproducible regression test. Zero LLM calls."""

    def __init__(
        self,
        recorded_steps: Sequence[AgentToolStep],
        *,
        on_divergence: Literal["fail", "finish_partial"] = "fail",
        compare: Optional[Callable[[AgentToolStep, AgentToolStep], Optional[str]]] = None,
        # default comparison: tool status equality; products inject deeper output comparators
    ) -> None: ...
```

Behavior: `next_step` emits `recorded_steps[len(history)].call` as the next tool decision; before
emitting step N+1 it compares `history[N]` (live) against `recorded_steps[N]` (recorded) via
`compare` — a divergence string → `action="fail"` (or finish with partial output per
`on_divergence`), rationale = the divergence description. History exhausted → `action="finish"`
with `output={"replayed_steps": N, "diverged": False}`. CLI episodes (WP5) record differently —
their salvaged `session*.md`/`page-*.yml` are the raw material; converting those into a replayable
step list or scenario skeleton is product-side (note in the tools README).

Tests (`tests/test_agent_planner.py`, NEW — fake client pattern from `test_llm_protocol.py`):
1. Scripted fake LLM: tool_call(navigate) → tool_call(screenshot, returns ImageInput) →
   finish(JSON). Assert: second LLM request contains the image as a tool-result part; final output
   parsed into `output_model`; usage events == 3 chat calls.
2. Step/tool caps from `AgentRunRequest` truncate with `status="truncated"` (existing
   `agent.py:117-125,156-163` paths) using this planner.
3. Budget: `WorkflowBudget(max_text_calls=2)` → third turn raises `WorkflowBudgetExceeded` and the
   episode surfaces a failed/partial result, not a crash.
4. Repair: malformed finish JSON then valid on repair turn → accepted; chatter/think-tags cleaned
   by `pre_parse=WEAK_MODEL_CLEANER`.
5. Replay: record a 3-step episode from test 1, re-run it through `ReplayPlanner` against the same
   fake tools → identical tool sequence, zero usage events (no LLM calls); mutate one fake tool's
   response → divergence detected at that step, `on_divergence="fail"` fails with the diff in the
   rationale, `"finish_partial"` finishes with `diverged=True` metadata.

---

## 5. WP3 — budget matrix (`pkg/usage.py`, `pkg/models.py`)

MageQA's proven caps (`mageqa/trace.py:20-27`, enforced `mageqa/workers.py:40-54`):
`max_worker_calls_per_run=80`, `max_input_tokens_per_call=20_000`,
`max_output_tokens_per_call=4_000`, `max_images_per_call=8`,
`max_estimated_metered_usd_per_run=0.50`, `max_estimated_metered_usd_per_call=0.10`.

Extend `WorkflowBudget` (`usage.py:24-28`) — all `Optional`, default `None` (= unlimited, current
behavior):

```python
max_worker_calls: Optional[int] = None        # chat + agent + external, per run
max_input_tokens_per_call: Optional[int] = None
max_output_tokens_per_call: Optional[int] = None
max_images_per_call: Optional[int] = None
max_estimated_usd_per_call: Optional[float] = None
```

Mirror the same optional fields on `RuntimeLimits` (`models.py:392-400`) and map them into the
usage context where the executor builds it, so YAML profiles can set them (`config_loader.py`
bundle → profile → plan path; follow how `max_estimated_usd` flows today).

Enforcement points:
- `check_budget_before_call` (`usage.py:120-135`): count `chat`+`agent`+`external` operations
  against `max_worker_calls` (keep existing `max_text_calls`/`max_image_calls` semantics intact).
- Per-call token caps: enforced where request size is known — `LLMAgentPlanner` and
  `StructuredLLMNode` check prompt-token estimate (price-table tokenizer heuristic is fine;
  document it) before calling; output cap checked after response, recording a
  `decision="truncated_by_budget"` trace event rather than retroactively failing.
- `max_images_per_call`: validate `len(request.images)+Σ tool_result images` in the planner and
  `StructuredVisionLLMNode.run`.
- `_enforce_usd_budget` (`usage.py:457-461`): unchanged for per-run; add per-call check inside
  `record_callable_usage`/`invoke_metered_chat` paths comparing the single event's
  `estimated_usd` to `max_estimated_usd_per_call` (keyword-only param threading, no signature
  breaks).

Tests (`tests/test_engine.py` or new `tests/test_budget_matrix.py`): one test per cap proving
denial raises `WorkflowBudgetExceeded` with the cap name in the message; one test proving
`None` caps change nothing (Anki regression proxy).

---

## 6. WP4 — subscription cost class (`pkg/models.py`, `pkg/usage.py`)

Gap: `AgentRunResult` knows `subscription_call`/`notional_cost_usd` (`models.py:207-216`) but
`WorkflowUsageEvent` (`models.py:229-248`) only carries `estimated_usd`; notional cost never
reaches the ledger, and budget must never debit flat-rate calls.

- `WorkflowUsageEvent` += `cost_class: Literal["metered","subscription_notional"] = "metered"`
  and `notional_usd: Optional[float] = None` (optional → constructors unchanged).
- `WorkflowUsageSummary` (`models.py:251-309`) += properties `metered_usd` (events with
  `cost_class=="metered"`, today's `estimated_usd` semantics), `notional_usd` (sum of
  `notional_usd`), and keep `estimated_usd` as alias of `metered_usd` for compat.
- `_enforce_usd_budget` and the new per-call USD check (WP3) consider **metered events only**.
- `record_callable_usage` and `invoke_metered_chat` get keyword-only
  `cost_class="metered"` / `notional_usd=None` passthroughs (defaults preserve behavior).
- `format_trace_events(…, usage=…)` renders both totals: `metered $X / notional $Y`.

Tests: subscription event with `notional_usd=0.42` → `metered_usd` unchanged, budget not debited,
summary shows both; mixed-run aggregation.

---

## 7. WP5 — `ai_workflow_tools` sub-library: CLI agent capability (the cost-cutter)

New package `packages/ai_workflow_tools/` (pyproject: `name="ai-workflow-tools"`,
`dependencies=["ai-workflow-engine"]`, same pytest/asyncio config; add
`-e ./packages/ai_workflow_tools` to TGHandyUtils `environment.yml` only when a TGHandyUtils
consumer appears — MageQA installs it directly).

### 7.1 Core-side prerequisite (small, in `pkg/engine/external.py`)

`ExternalProcessRequest` (`external.py:20-25`) += optional fields (additive):

```python
stdin_data: Optional[str] = None        # write to stdin then close (claude -p prompt delivery)
result_file: Optional[str] = None       # if set, read this file as the result after exit
                                        # (codex --output-last-message), fallback to stdout
kill_grace_s: float = 10.0              # terminate → wait(grace) → kill
```

Kill sequence change in `ExternalProcessCapability.__call__` (`external.py:50-66`): on timeout,
`terminate()` → `wait(kill_grace_s)` → `kill()` (today it hard-kills immediately). Streams keep
draining via the existing reader tasks. Proven sequence: `mageqa/agent_browser.py:235-244`.

### 7.2 Tool-lib models (`ai_workflow_tools/cli_agents/models.py`)

```python
class CliFlavor(BaseModel):
    """How to talk to one CLI agent runtime. Two shipped instances; products may add more."""
    name: str                                            # "claude_p" | "codex_exec" | custom
    prompt_delivery: Literal["stdin", "argv_last"]
    result_source: Literal["stdout_json_envelope", "result_file", "stdout_text"]
    base_argv: List[str]                                 # e.g. ["claude", "-p"]

class McpServerConfig(BaseModel):                        # impl-agnostic MCP description (DATA —
    name: str                                            #   Playwright specifics are product input)
    command: str                                         # e.g. "npx"
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)    # MCP servers commonly need env vars;
                                                         #   written into the per-flavor config
                                                         #   (claude mcp_config.json "env" key /
                                                         #   codex --config env entries)

class CliAgentRequest(BaseModel):
    prompt: str
    workspace_dir: str                                   # run-scoped dir; MCP output + salvage root
    timeout_s: float = 600.0
    mcp_servers: List[McpServerConfig] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)   # claude --allowedTools values
    input_assets: List[EvidenceRef] = Field(default_factory=list)
        # assets-IN provenance (the symmetric half of salvage): each ref is materialized into
        # `<workspace_dir>/inputs/` via the capability's product-injected
        # `asset_loader: Callable[[EvidenceRef], bytes]` (same pattern as
        # ImageInput.from_evidence); fingerprints — never bytes — recorded in trace metadata;
        # prompt convention: relative paths under `inputs/` (document in the tools README).
        # Inputs are part of the episode record → freeze-to-replay stays reproducible.
    salvage_globs: List[str] = Field(                    # ALWAYS collected, even on timeout
        default_factory=lambda: ["*.png", "*.jpg", "*.jpeg", "session*.md", "page-*.yml"])
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None               # codex --config model_reasoning_effort
    extra_argv: List[str] = Field(default_factory=list)
    subscription_mode: bool = True
    expect_json_result: bool = True                      # apply lenient JSON extraction to text
    metadata: Dict[str, Any] = Field(default_factory=dict)

class CliAgentResult(BaseModel):
    status: Literal["completed", "truncated", "error"]
    text: str = ""                                       # agent's final message
    parsed: Optional[Dict[str, Any]] = None              # lenient-JSON parse of text (if requested)
    artifacts: List[EvidenceRef] = Field(default_factory=list)   # salvaged files, role from glob
    new_artifact_count: int = 0                          # files created during THIS run — products
                                                         #   build grounding gates on this
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    num_turns: Optional[int] = None
    duration_ms: Optional[int] = None
    notional_cost_usd: Optional[float] = None
    returncode: Optional[int] = None
    stderr_tail: str = ""                                # last 800 chars, diagnostics only
```

### 7.3 `CliAgentCapability` (`ai_workflow_tools/cli_agents/capability.py`)

`spec = CapabilitySpec(kind="agent", side_effects=<ctor param>, metered=False, timeout_s=None)` —
timeout governed by `CliAgentRequest.timeout_s`; side effects are the product's declaration
(e.g. MageQA passes `["browser_drive"]`), enforced by the engine before the handler runs.

Execution (delegates subprocess mechanics to core `ExternalProcessCapability` — do not reimplement
spawn/drain/kill):

0. **Stage `input_assets`** (if any): materialize each `EvidenceRef` into
   `<workspace_dir>/inputs/` via the injected `asset_loader`; missing loader with non-empty
   `input_assets` → loud ValueError before spawn; record one trace event
   `decision="inputs_staged"` with fingerprints (sha12/length/role per file — never bytes).
1. **Snapshot workspace** matching `salvage_globs` (for `new_artifact_count` diffing) — taken
   AFTER staging, so input assets are never counted as new artifacts.
2. **Build argv per flavor.** Embedded proven facts:
   - `claude_p` (`mageqa/agent_browser.py:308-332`): write `mcp_config.json` into
     `workspace_dir` as `{"mcpServers": {name: {"command":…, "args":…}}}`; argv =
     `["claude","-p","--mcp-config",cfg_path,"--strict-mcp-config",
     "--allowedTools",*allowed_tools,"--output-format","json", *extra_argv]`; prompt via
     `stdin_data`.
   - `codex_exec` (`mageqa/agent_browser.py:333-361`): argv =
     `["codex","exec","--config",f'mcp_servers.{name}.command="{command}"',
     "--config",f'mcp_servers.{name}.args={json.dumps(args)}',
     "--sandbox","workspace-write","--cd",workspace_dir,
     "--output-last-message",result_file, *model/effort flags, *extra_argv, prompt]`;
     `result_source="result_file"`.
3. **Run** via core capability with `timeout_s`, `kill_grace_s=10`.
4. **Parse result:**
   - `stdout_json_envelope` (claude): fields `result`, `total_cost_usd`, `num_turns`,
     `duration_ms`, `usage.{input_tokens,output_tokens,cache_read_input_tokens,
     cache_creation_input_tokens}` (`mageqa/agent_browser.py:364-376`). Envelope unparseable →
     `text = raw stdout`, tokens 0, cost unknown.
   - `result_file` (codex): read file if present, else stdout. Codex reports no cost/tokens →
     leave zeros, `notional_cost_usd=None` (honest-unknown, value #3 — never fake `$0.00`).
   - Lenient JSON extraction when `expect_json_result`: **reuse the engine's cleaners — do NOT
     reimplement.** Apply `compose_cleaners(extract_fenced_json, extract_first_json_object)` from
     `ai_workflow_engine.parsing` (fence-aware, balanced, string-aware — strictly supersedes
     mageqa's naive first-`{`-to-last-`}` at `mageqa/agent_browser.py:416-427`), then `json.loads`;
     non-dict → `{"value": parsed}`; failure → `parsed=None` (not an error). One implementation of
     weak-output hygiene engine-wide, whether text comes from an API, a local model, or a CLI.
5. **Salvage ALWAYS** (timeout included): glob `salvage_globs` under `workspace_dir`
   (recursive for `session*.md`), diff against snapshot → `EvidenceRef(role=<glob family:
   "screenshot"|"session"|"page_record">, uri=abs_path, media_type=guessed)`; attach as both
   `CliAgentResult.artifacts` and `CapabilityResult.artifacts`
   (`WorkflowArtifact(kind="media"|"file")`).
6. **Status:** timeout → `"truncated"` (capability status `"partial"`); rc==0 → `"completed"`
   (`"accepted"`); else `"error"` (`"failed"`) with `stderr_tail`.
7. **Meter:** emit one `WorkflowUsageEvent(provider=flavor.name, operation="agent"…
   — reuse `"tool"` if extending the `operation` Literal is risky — `cost_class=
   "subscription_notional"` when `subscription_mode`, `notional_usd=total_cost_usd`,
   real token counts)`. Subscription events never debit the metered budget (WP4);
   `check_budget_before_call` still counts them against `max_worker_calls` (WP3).

Out of scope, deliberately (product-side, document in the tool README): mission-prompt authoring,
the grounding *gate* (product policy over `new_artifact_count` — MageQA clears findings when no
new screenshots exist, `mageqa/agent_browser.py:276-279`), finding schemas, Playwright specifics.

### 7.4 Tests (`packages/ai_workflow_tools/tests/`)

Fake-CLI fixture: `tests/fake_cli.py` — a Python script the tests invoke as the "claude"/"codex"
binary. Modes via env: emit canned JSON envelope to stdout; write a PNG + `session.md` into the
dir given by `--mcp-config`-referenced output dir (or an env-passed dir); sleep past timeout;
write result file (codex mode); emit garbage (envelope-parse fallback).

1. claude flavor happy path: argv assembled exactly as §7.3.2; prompt arrived on stdin; envelope
   fields land in `CliAgentResult`; usage event has `cost_class="subscription_notional"`,
   `notional_usd==total_cost_usd`, metered budget untouched.
2. codex flavor: result read from `--output-last-message` file; no cost → `notional_cost_usd is
   None` and usage metadata `cost_known=False`.
3. Timeout: fake CLI sleeps; PNG written before kill is still salvaged; `status="truncated"`,
   capability `"partial"`, `new_artifact_count==1`.
4. Lenient JSON: fenced/chattered output parses; garbage → `parsed=None`, `text` preserved.
5. Side-effect denial: spec `side_effects=["browser_drive"]` not in profile → engine rejects
   before spawn (no process started — assert via marker file absence).
6. Workspace snapshot: pre-existing PNG not counted in `new_artifact_count`.
7. MCP `env`: configured env vars land in `mcp_config.json` (claude) / `--config` entries (codex) —
   argv/config-assembly assertion, no process semantics needed.
8. `input_assets`: two refs staged under `inputs/`, fingerprints in the `inputs_staged` trace
   event, no bytes anywhere in trace; staged files NOT counted in `new_artifact_count`;
   non-empty `input_assets` without an `asset_loader` → loud error before spawn.

### 7.5 `ConsoleLLMClient` — the thin L1 chat member (same file family, `cli_agents/console.py`)

The simple contract: **text in → JSON out** through a non-interactive CLI, behind the engine's
`LLMCallable` socket — so structured nodes provide parse/repair/`pre_parse`/model-binding and the
usage ledger meters it like any other client. No MCP, no tools, no workspace.

```python
class ConsoleLLMClient:
    """LLMCallable over a CliFlavor (claude_p / codex_exec). Reuses the flavor argv assembly and
    envelope parsing from CliAgentCapability internals — one implementation, two surfaces."""

    def __init__(self, flavor: CliFlavor, *, timeout_s: float = 120.0,
                 subscription_mode: bool = True, extra_argv: Sequence[str] = ()) -> None: ...

    async def __call__(self, request: LLMRequest) -> LLMResponse: ...
    # system+user (and WP1 `messages`, flattened role-prefixed) → prompt text via the flavor's
    # prompt_delivery; envelope tokens/cost → LLMResponse fields; subscription_mode → the WP4
    # cost-class flows when metered through record_callable_usage (cost_class=
    # "subscription_notional", notional_usd from envelope; codex → honest-unknown).
```

Honest limits (document in the tools README): images are NOT deliverable over a bare `-p` chat —
`LLMRequest.images` non-empty → loud ValueError pointing at `StructuredVisionLLMNode` with an API
client or `CliAgentCapability` (workspace + vision-capable agent). `tools`/`tool_choice` set →
same loud refusal (a console chat call cannot host tool-calling turns).

Tests (`packages/ai_workflow_tools/tests/test_console_client.py`, fake-CLI fixture):
1. structured node + `ConsoleLLMClient(claude_p)` + `pre_parse=WEAK_MODEL_CLEANER`: fenced/chatty
   stdout parses into the output model with zero repair rounds; usage event carries envelope
   tokens + `cost_class="subscription_notional"`.
2. codex flavor: result-file read; cost honest-unknown (`cost_known=False`).
3. images / tools on the request → loud ValueError, no process spawned.
4. repair round: garbage first response, valid second; two processes spawned, repair prompt
   delivered via stdin/argv per flavor.

---

## 8. WP6 — streaming trace sinks (`pkg/engine/capabilities.py` or new `pkg/engine/trace_sinks.py`)

Events are recorded inline during runs (e.g. `agent.py:206-222`, `capabilities.py:140-202`), so
live observability only needs sinks:

```python
class CallbackTraceSink:        # sync callback per event; exceptions swallowed + logged
    def __init__(self, callback: Callable[[WorkflowTraceEvent], None]) -> None: ...
class AsyncQueueTraceSink:      # asyncio.Queue feed for SSE/websocket fan-out
    def __init__(self, queue: Optional[asyncio.Queue] = None, *, maxsize: int = 1000) -> None: ...
    # full queue: drop oldest + increment .dropped counter — live view must never block the run
class TeeTraceSink:             # fan one event to N sinks (live + JSONL persistence together)
    def __init__(self, *sinks: TraceSink) -> None: ...
```

Tests: events arrive during (not after) a multi-node run — assert via callback capturing event
count mid-execution; full-queue drop policy; tee writes both.

---

## 9. WP7 — documentation propagation (the soul, everywhere)

1. `pkg/README.md`: add a **"Soul"** section near the top = §0 of this spec (engine core
   impl-agnostic + tool libs on seams + economics axis + rigidity spectrum + evidence + fail
   closed). Delete/rewrite the now-stale "Missing before this is a finished workflow engine" items
   that WP1–WP6 close. Re-verify every remaining claim.
2. `packages/ai_workflow_tools/README.md` (new): soul recap + what belongs here vs core + the
   CLI-agent quickstart (10 lines: flavor, request, engine registration).
3. `docs/mageqa-handoff.md`: replace the "AgentCapability over scoped registered browser/MCP
   tools" paragraph with the real recipe: `CliAgentCapability` from `ai_workflow_tools` (worker
   economics) and/or `build_llm_agent_capability` (in-engine loop), plus `ConsoleLLMClient` for
   plain structured steps on subscription CLIs; add the budget-matrix and cost-class fields to the
   profile example; note `new_artifact_count` as the grounding-gate input and `input_assets` for
   staged evidence.
4. `docs/gopro-handoff.md`: one paragraph — the same `CliAgentCapability`/`ExternalProcess`
   extensions serve GoPro CLIs; scheduling/backpressure semantics unchanged.
5. TGHandyUtils root `README.MD` + `ARCHITECTURE.md`: one short subsection stating the
   engine+tools split and pointing here.
6. `pkg/examples.py`: extend the site-audit pilot so the fan-out scenario step runs through an
   `AgentCapability` with a scripted fake planner exercising the WP1 protocol (guard test must
   stay green — everything через `engine.run()`).

---

## 10. Regression & verification matrix

After every WP, all must pass. **TGHandyUtils repo rule: docker-only via `./test.sh` — never bare
`python -m pytest` on the host** (sole exception: the standalone clean-venv release gates):
- Engine suite: `./test.sh unit -- packages/ai_workflow_engine/tests/ --cov-fail-under=0 -q`
  (asyncio_mode=auto; ChatOpenAI mocked session-wide in `tests/conftest.py` — keep new tests
  offline-safe the same way).
- Tools suite (from WP5): wire `packages/ai_workflow_tools/tests/` into `test.sh` collection
  (extend the `TEST_TARGET` default exactly like the engine package), then
  `./test.sh unit -- packages/ai_workflow_tools/tests/ --cov-fail-under=0 -q`.
- Anki/consumer regression: `./test.sh unit -- --cov-fail-under=0 -q` (covers
  `anki_generation_graph`, `openai_service`, scenario planners — the frozen-signature consumers).
- Standalone gates (clean venv, on demand / per release): the existing
  `packages/ai_workflow_engine/scripts/standalone_test.sh`, plus an equivalent
  `packages/ai_workflow_tools/scripts/standalone_test.sh` (WP5 deliverable; installs the engine
  from the sibling path, then the tools package, then runs its tests — zero host-repo imports).
- **Commit cadence:** per the repo's standing rule (Artem, 2026-06-10), commit at every green
  WP gate on the working branch, no pushes — not a single uncommitted multi-WP tree.
- Named guards: `test_examples_contain_no_product_orchestration_loops`,
  `test_budget_exhaustion_denies_metered_capability`,
  `test_fanout_gather_isolates_partial_failure`,
  `test_evaluate_retrace_to_earlier_node_then_accepts` — unchanged and green.

No real network/API/CLI calls anywhere in tests (fake clients + fake-CLI script only).

## 11. Definition of done

1. WP1–WP6 implemented with the tests listed, full matrix (§10) green — **including the review
   additions now part of the body**: AC-R3 `McpServerConfig.env` reaches the flavor config
   (test §7.4.7); AC-R4 lenient JSON is `ai_workflow_engine.parsing` reuse, no second
   implementation (code inspection + behavior tests §7.4.4 must match the engine cleaners on
   fences/garbage); AC-R5 `ConsoleLLMClient` green per §7.5 tests 1–4 (structured node over a
   fake CLI, honest cost classes, loud image/tool refusal); AC-R6 `input_assets` staged, traced
   by fingerprint, never counted as new artifacts, loud without a loader (test §7.4.8).
2. A fake-backed end-to-end pilot exercising **all three rigidity axes**:
   - *Set composition:* the plan step is semi-rigid — merge(static base suite from config, items
     added by a fake coordinator LLM) — proving rigid/flexible set variants are the same node with
     a different plan capability.
   - *Execution:* fanout of 3 scenarios = 1 rigid (deterministic capability) + 1 semi-rigid
     (`LLMAgentPlanner`, narrow tools, structured criteria) + 1 flexible (`CliAgentCapability` on
     the fake CLI, **receiving one `input_assets` ref and salvaging one output artifact** — assets
     proven both directions) → adjudicate (deterministic, gates on `new_artifact_count`) →
     evaluator retrace once → report, where **the report step runs a structured node over
     `ConsoleLLMClient` on the fake CLI** (the simple text→JSON contract in the same flow). Run
     via `engine.run()`.
   - *Mode:* the flexible `LLMAgentPlanner` episode's recorded steps are replayed via
     `ReplayPlanner` in a second `engine.run()` — same outcome, zero LLM usage events: an
     exploration frozen into a regression test.
   `format_trace_events` dump shows live-sink ordering, metered vs notional totals, salvaged
   evidence refs.
3. WP7 docs landed; READMEs contain no claim this spec's author couldn't verify in source.
4. Zero changes to any frozen signature; Anki suite green without edits to Anki code.

## 12. Risks / decisions taken

- **`operation` Literal extension** (`"agent"` on `WorkflowUsageEvent`): pydantic Literal widening
  is additive for constructors but pattern-matching consumers may assume the closed set — grep
  TGHandyUtils for `operation ==` before widening; fall back to `operation="tool"` +
  `metadata["operation_detail"]="cli_agent"` if anything matches.
- **Token estimation for per-call input caps** is heuristic pre-call; document that the authoritative
  count lands in the usage event post-call. Acceptable: cap exists to stop runaways, not for billing.
- **Codex cost opacity**: no envelope → honest-unknown cost. Do not synthesize notional cost.
- **`claude`/`codex` CLI flag drift**: flavors centralize argv construction; pin known-good flag
  sets in `CliFlavor` defaults and cover with argv-assembly unit tests so drift breaks loudly.
- **Media seams stay in core for now** (Anki imports them); moving them to `ai_workflow_tools` is
  a follow-up with re-export shims, explicitly out of scope.

---

## 13. Technical plan — phases & tasks (each ≤6h; commit at every green task gate)

Every task ends with: its tests green + engine package suite green + full repo regression
(`./test.sh unit -- --cov-fail-under=0 -q`) + one commit. Lanes ‖ may run in parallel.

### Phase A — protocol & economics foundations (lane α ‖ lane β)

| # | Task | Spec | Est | Needs |
|---|---|---|---|---|
| A1 | WP1 models: `ToolSpec`/`ToolCallRequest`/`ToolResult`/`ChatMessage`; `LLMRequest` += messages/tools/tool_choice (+validator); `LLMResponse` += tool_calls/stop_reason; round-trip + serialization-fingerprint + validator tests (§3.1–3) | §3 | 4h | — |
| A2 | WP3 run-level: `WorkflowBudget`/`RuntimeLimits` new optional caps + profile→plan→usage-context plumbing; `max_worker_calls` counting (chat+agent+external) in `check_budget_before_call`; denial + None-noop tests | §5 | 5h | — |
| A3 | WP3 per-call: input-token heuristic pre-call (structured node), output cap → `truncated_by_budget` trace event, `max_images_per_call` in planner+vision run, per-call USD inside `record_callable_usage`/`invoke_metered_chat` (kw-only); one denial test per cap | §5 | 4h | A2 |
| A4 | WP4 cost class: `WorkflowUsageEvent` += `cost_class`/`notional_usd`; summary `metered_usd`/`notional_usd` (+`estimated_usd` alias); `_enforce_usd_budget` metered-only; kw-only passthroughs; `format_trace_events` dual totals; mixed-run tests | §6 | 4h | — |

Gate A: WP1+WP3+WP4 green. (A1 ‖ A2; A3/A4 follow in either order.)

### Phase B — agent brain (needs A1)

| # | Task | Spec | Est | Needs |
|---|---|---|---|---|
| B1 | `LLMAgentPlanner`: stateless message reconstruction from prompt+history, decision mapping (no allow-list duplication), finish-parse with `pre_parse` + bounded repair turns, per-turn budget+metering; tests §4.1–4 | §4 | 6h | A1 |
| B2 | Vision loop (tool-result images via `ImageInput.from_evidence` + loader), `build_llm_agent_capability`, `ReplayPlanner` (divergence compare, both `on_divergence` modes); tests §4.5 incl. zero-usage replay | §4 | 5h | B1 |

Gate B: WP2 green (AC: scripted episode, caps, budget, repair, replay).

### Phase C — core prereq + tools package (needs A4 for metering)

| # | Task | Spec | Est | Needs |
|---|---|---|---|---|
| C1 | Core prereq: `ExternalProcessRequest` += `stdin_data`/`result_file`/`kill_grace_s`; terminate→wait(grace)→kill sequence; core tests (stdin delivery, result-file, stubborn-child grace-kill) | §7.1 | 3h | — |
| C2 | Tools package scaffold: pyproject + pytest config + conftest, `tests/fake_cli.py` fixture (all modes: envelope/artifacts/sleep/result-file/garbage), `test.sh` TEST_TARGET wiring, `scripts/standalone_test.sh` (installs engine from sibling path) | §7.4/§10 | 4h | — |
| C3 | Models + flavors: `CliFlavor`/`McpServerConfig`(+env)/`CliAgentRequest`(+input_assets)/`CliAgentResult`; claude_p/codex_exec constants; argv/mcp-config assembly; assembly unit tests incl. env (§7.4.7) | §7.2 | 4h | C2 |
| C4 | `CliAgentCapability`: stage inputs (step 0: loader, `inputs_staged` fingerprint trace, loud no-loader) → snapshot → run via core → envelope/result-file parse (**reuse `ai_workflow_engine.parsing`**) → salvage→`EvidenceRef`+`new_artifact_count` → status map → meter with cost_class; tests §7.4.1–3,5,6,8 | §7.3 | 6h | C1,C3,A4 |
| C5 | `ConsoleLLMClient` (§7.5): flavor-backed `LLMCallable`, messages flattening, honest refusals (images/tools), envelope→`LLMResponse`; tests §7.5.1–4 (incl. two-spawn repair) | §7.5 | 4h | C3,A1 |

Gate C: WP5 + AC-R3/R4/R5/R6 green (incl. side-effect-denial-before-spawn §7.4.5).

### Phase D — observability (independent)

| # | Task | Spec | Est | Needs |
|---|---|---|---|---|
| D1 | WP6 sinks: `CallbackTraceSink` (exception-swallowing), `AsyncQueueTraceSink` (drop-oldest + `.dropped`), `TeeTraceSink`; mid-run-arrival, drop-policy, tee tests | §8 | 3h | — |

### Phase E — proof + docs (needs everything)

| # | Task | Spec | Est | Needs |
|---|---|---|---|---|
| E1 | §11.2 three-axis pilot in `examples.py` + example pack: semi-rigid plan merge, fanout rigid/semi/flexible (flexible takes one `input_assets` ref + salvages one artifact), adjudicate on `new_artifact_count`, one retrace, report via `ConsoleLLMClient`, live-sink ordering + metered/notional dump; second run = `ReplayPlanner`, zero LLM usage events; orchestration-guard stays green | §11.2 | 6h | B2,C4,C5,D1 |
| E2 | WP7 docs: engine README soul + stale-claims sweep (verify every remaining claim in source), tools README + quickstart, mageqa-handoff recipe (CliAgent + Console + budget/cost fields + `new_artifact_count` + `input_assets`), gopro-handoff paragraph, root README/ARCHITECTURE subsection; final full §10 matrix + both standalone gates run | §9 | 4h | E1 |

**Totals:** 13 tasks, ~58h ≈ 7–8 working days single-threaded; with the α‖β lanes Phase A+C
compresses by ~1.5 days. Critical path: A1 → B1 → B2 → E1 → E2.
