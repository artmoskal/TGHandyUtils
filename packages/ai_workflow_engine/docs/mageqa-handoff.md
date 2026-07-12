# MageQA — AI Workflow Engine Usage Guide

> **PIN (2026-07-07):** pin tag `engine-v0.8.1` and build a wheel from it. The consumer model is
> breaking-allowed tag-to-tag — do NOT track the live branch. The tag carries the full current
> capability set (see **"What engine-v0.8.1 gives you"**):
> cross-run memory, config-first observation (log→bundle→viewer; HTML rendering lives in the
> separate `ai_workflow_viewer` package), strict prompt files, machine identity, and hardened seam
> guards.
> **Release gate: CLOSED 2026-07-07** — package version metadata is lockstep-guarded in both
> packages (engine 0.8.1, tools 0.3.0; pyproject ↔ `__version__` release-guard tests), the
> one-call façade exposes the full run envelope (`return_result=True`), and console tool-flag
> handling covers joined/kebab forms (regression-locked). The tag builds packages that report
> the matching versions.
> **MageQA current pin check (2026-07-06):** the MageQA repo currently vendors
> `ai_workflow_engine-0.7.0` / `ai_workflow_tools-0.3.0` and its canary asserts the v0.7.0 surface.
> So `0.7.0 -> 0.8.1` has **no additional breaking contract change**; it is an additive memory
> upgrade plus custom-reducer hardening. If a branch is older than v0.7.0, read the v0.7.0 contract
> changes below before re-pinning.
> **v0.8.1 delta (2026-07-07, additive hardening):** custom memory reducers and checkpoint payloads
> now use the same fail-closed byte-safety guard. Pydantic/dataclass/computed/private fields,
> mappings, concrete containers, scalar subclasses, enum values, exception args, partial args, and
> object attributes are inspected; raw bytes/media transport, actual rendered `data:*;base64,`
> state, iterators, unsafe mapping keys, wrapper-carried unsafe values, and opaque/arbitrary custom
> objects fail loudly. Reducers should emit dict/list/scalar/value objects or evidence refs, not
> product domain objects by repr. Unsafe `compacting(inner=structured_state)` is rejected in favor of
> `structured_state(base=compacting)`; compacting wording is neutral for custom compactors.
> **v0.8.0 delta (2026-07-05, additive):** T2 prompt-projection memory shipped for the GoPro
> named-consumer request and is yours too: `structured_state` (derived working-state block for
> weak-model agents), `windowed`, `compacting` modes + per-NODE `memory=` selection on
> `.step(...)`. No contract change; pick up if your browser agents want windowing or working-
> state facts. Durable/semantic memory stores remain deferred.
> **v0.7.0 delta (2026-07-05) — CONTRACT CHANGE, read before re-pinning:**
> 1. **`CliAgentRequest.allowed_tools` is tri-state.** `None` (the NEW field default) → the
>    documented `DEFAULT_AGENT_TOOLS` (`Read, Grep, Glob, LS, WebFetch, WebSearch, Bash`);
>    `[]` → explicit no-tools (`--tools ""`); a non-empty list → EXACT override, never merged.
>    Old behavior — default `[]` silently inheriting the CLI's own tool defaults — is gone.
>    `codex_exec` rejects a non-None list loudly (its surface is governed by `--sandbox`).
> 2. **Side-effect ledger honesty.** The default `CliAgentCapability` spec now declares
>    `workspace_write` + `external_call` (Bash is in the default tool set; codex_exec always
>    runs `--sandbox workspace-write`). Your workflow profile must ALLOW those effects for
>    agent nodes or the engine refuses pre-invocation. Migrate: allow both effects on real
>    agent flows. For `claude_p`, you may narrow by setting explicit `side_effects=[...]`
>    and a Bash-less `allowed_tools` list. For `codex_exec`, `allowed_tools` is rejected;
>    a narrowed spec that omits the Codex workspace-write/subscription side effects is
>    refused PRE-SPAWN.
> 3. **Text-only console completions are tool-free** (`claude -p … --tools ""`): the injection
>    surface on structured text calls is closed. Staged vision keeps ONLY scoped
>    `Read(./inputs/**)` — the exfiltration canary is re-verified in every live run.
> 4. **Crash fix you want:** external-process stream reading is chunked — a CLI emitting one
>    huge single-line JSON (>64KiB) no longer kills the call (the `LimitOverrunError` class
>    of failures is gone; regression-locked).
> 5. **NEW — discoverable toolset:** `from ai_workflow_tools import TOOL_CATALOG,
>    render_tool_catalog, register_from_catalog` — every shipped tool described (kind, side
>    effects, builder), plus presets `READ_ONLY / WEB / INVESTIGATION / NO_TOOLS`
>    (`DEFAULT_AGENT_TOOLS is INVESTIGATION`); a completeness guard keeps the catalog honest.
> 6. **NEW — one-call façade:** `from ai_workflow_engine import run_single_llm,
>    run_single_step` — a 1-node workflow on the real engine (usage/trace/budget/parse-repair
>    intact) for the trivial case; trace/usage stay reachable via `return_result=True` even when the
>    helper creates the engine internally.
> **MageQA migration check (2 minutes):** your browser agents pass explicit `mcp__*` tool
> lists → exact-override semantics, unaffected by (1). For (2): the audit profile must allow
> `workspace_write` + `external_call` on real agent steps. Only `claude_p` agents can narrow by
> registering a smaller `side_effects=[...]` and keeping `allowed_tools` Bash-less; `codex_exec`
> must declare its workspace-write/subscription side effects.
> **One-door rule for consumers:** construct provider clients only through sanctioned factory/adapter
> modules (in this repo: `services/llm_factory.py`, `ai_workflow_tools.media.image_generation`,
> `ai_workflow_tools.chatgpt_browser`, `ai_workflow_tools.catalog` — the catalog builders are the
> supported discovery door). A direct provider client in workflow code is a reviewed allow-list
> entry, not a local shortcut — otherwise you lose observability, cost accounting, and model-swap.

Status: **ready for adoption — pin `engine-v0.8.1`** and build a wheel; never track the live branch.
The `WorkflowDefinition` / `WorkflowExecutor` / DI layer is live and proven (Anki runs on it in
production; the product-neutral examples include site-audit fan-out and the three-axis pilot). The
sibling `ai_workflow_tools` package ships CLI-agent + console-LLM support (`claude -p` / `codex exec`)
and the media pack. See **"What engine-v0.8.1 gives you"** at the end for the full capability list.
Not in v0.8 (deferred): durable/semantic memory STORES beyond the `MemoryStore` seam, FlowArtifact v1.5,
ProcessArtifact/v2, browser/no-API executors. v0.9 BRANCH UPDATE (implemented on branch, pending acceptance + the `engine-v0.9.0` tag — pin
`engine-v0.8.1` until then): FlowArtifact v1.5a — authored
bounded `fanout` (max_items REQUIRED) + `build_flow_author_capability(...)`; true per-phase budget
scopes (N1) remain explicitly FUTURE-STAGE (activation on a concrete MageQA workflow needing
independent phase ceilings).
**v0.9 migration notes (apply ONLY when `engine-v0.9.0` exists; `engine-v0.8.1` is the current pin):**
strict `RuntimeLimits` (unknown limit keys now fail config load); typed limits are the ONLY budget
source (`budget_from_limits(RuntimeLimits|None)` — host-config/duck-typed objects are rejected; the
old usage-tracking flag no longer disables engine budgets); firewall markers are typed
`CapabilitySpec.is_planner`/`is_flow_author` fields (metadata/handler-attribute markers are ignored;
`CapabilitySpec` rejects unknown fields); application `0 = no cap` is normalized at the PRODUCT
boundary while engine `RuntimeLimits(...=0)` is an honest hard-zero cap; failed provider attempts now
consume call budgets and emit zero-cost/zero-token failure events on BOTH transports; `FlowArtifact`
nodes are a discriminated per-kind schema (plain `model_dump()`/`model_validate()` round-trips are
guaranteed; foreign/unknown node fields fail loudly).

Source needs: `/Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md`,
`/Users/artemm/PycharmProjects/MageQA/docs/14-agentic-tester-architecture.md`.

This is a **how-to**: declare your audit flow, register your capabilities, run it. The engine owns the
deterministic harness (plan→fan-out→adjudicate→deepen→report mechanics, scheduling, side-effect/budget
gates, trace, subworkflows); you own the agentic/domain core (browser agents, rubric, grounding,
report). **You write no coordinator loop** (a static guard enforces this).

---

## 0. Why pin the current tag — what YOUR workload gains (not just the generic delta)

**Direction verdict (owner + engine author, 2026-07-05): do NOT reroute your integration for
the current tag.** MageQA already sits on v0.7.0, so there is no new breaking change in v0.8.1.
Re-pin, run the migration check above, and continue as designed. The useful new surface is optional:
prompt-projection memory for long/deep browser-agent episodes, bounded windowing/compaction for
large transcripts, and per-node `memory=` so only the workers that need state get it. Durable/semantic
memory stores remain deferred; FlowArtifact v1.5a authored fan-out + the turnkey author arrive with
the engine-v0.9.0 tag (implemented on branch, NOT yet tagged — not in your v0.8.1 pin;
subworkflow/general-IO authoring still deferred).

Mapped to the MageQA shapes in this doc (browser episodes via `CliAgentCapability`+MCP,
adjudicate/deepen loops, rubric/report text roles, artifact salvage, cost dashboards):

| v0.7.0 change | Your concrete win |
|---|---|
| Chunked external-process read (G-0.1) | **Top reliability win for you.** Browser episodes return large single-line JSON envelopes (session transcripts, page text). Pre-v0.7.0 any >64KiB single line crashed the call AFTER the episode's quota was spent — and the same bug sat under timeout salvage of big partials. Long agent episodes stop dying on reply size. |
| Text-only completions run `--tools ""` | **Your injection chain gets a hard break.** MageQA feeds UNTRUSTED page content into downstream adjudicator/rubric/report completions; a hostile page that says "read /app/.env with your tools" is now inert at the CLI level on those roles — enforced by flag, not by prompt discipline (exfiltration canary re-verified live each run). |
| Tri-state `allowed_tools` | `[]` makes scoring/verdict completions provably tool-free; `None` gives the documented investigation default (`Read/Grep/Glob/LS/Web*/Bash`) — which enables a **new cheap episode type for deepen loops**: a non-MCP triage agent that greps/reads the SALVAGED artifacts (`session*.md`, `page-*.yml`, HAR dumps) in its workspace instead of re-driving a browser. Your explicit `mcp__browser__*` lists behave as before (exact override). |
| Pre-spawn side-effect denial | A mis-scoped agent config refuses BEFORE the process spawns — no quota burned on an episode policy would forbid, and your QA compliance story sharpens: the permitted-capability ledger provably cannot lie (a testing product gets asked exactly that). |
| `TOOL_CATALOG` / `render_tool_catalog()` / presets | Feed your session-planner the tool inventory the same way `render_capability_catalog` feeds deciders; reference `READ_ONLY`/`NO_TOOLS`/`INVESTIGATION` constants in configs instead of hand-listing tool strings. |
| `run_single_llm` / `run_single_step` | Your one-shot calls OUTSIDE audit workflows (failure classification, rubric refinement, flaky-check triage scripts) get engine-honest usage/trace/budget in 3 lines — kills the temptation for shadow LLM paths; `return_result=True` hands back the full run envelope. |

| v0.8.x additive memory surface | Your concrete win |
|---|---|
| `StructuredStateMemory` | Useful for long browser/vision/deepen loops where the worker must remember "already inspected", "already refuted", or "retry reason" without replaying the full transcript. Treat it as prompt input only, never control state. |
| `WindowedMemory` / `CompactingMemory` | Lets agent episodes stay bounded when browser transcripts, page text, or tool outputs get large. Dropped turns leave bounded activity/output notices; compactors are product code, not hidden model calls. |
| Per-node `memory=` | Use memory only on the nodes that need it: browser/deepen/replay/triage agents. Keep deterministic probes, report rendering, and short adjudicators on full replay or no extra memory unless they prove a need. |
| v0.8.1 reducer hardening | If MageQA writes custom reducers for target profile, learned flows, flaky checks, or finding history, bad reducer output fails loudly instead of slipping raw bytes/images/data URIs or nondeterministic object reprs into prompts. |

**Coming from `engine-v0.6.5` or earlier? You ALSO pick up the v0.6.6 consumer-facing fixes**
(previously bannered here, restated because they matter to your exact stack): structured nodes
dispatched factory-built PLAIN clients (e.g. `ConsoleLLMClient` — your claude-p text roles) down
the LangChain path on their FIRST execution (crash on missing `.invoke`, recovered only on retry),
and a profile routed to a different plain backend could invoke the default client — both fixed;
usage captions split `billed (API)` from `subscription … plan value` (your cost dashboards), and
usage events carry honest `provider` attribution including on FAILED calls (your failure
analytics).

## 1. The whole adoption in three steps

```python
from ai_workflow_engine import (
    WorkflowBuilder, WorkflowEngine, Retrace, Fallback, BranchDecision,
)

# (1) DECLARE the audit flow — THE reference shape (surface -> plan -> instruments ->
#     verify -> deepen -> findings -> report). Your requirements §6→§7→§8→§9→§12 are not a
#     feature list; they are literally this machine. "Did we adopt correctly?" = diff against it.
audit_site = (
    WorkflowBuilder("audit_site")
    .subworkflow("surface", workflow=SURFACE_FLOW)   # §6 cheap probes/fingerprint/smoke (budgeted)
    .step("plan_qa_session")                         # §7 scenarios+facts+budget -> scenario inputs
    .fanout("run_scenarios", capability="run_browser_scenario",   # §8 browser/vision/probe workers
            items_key="plan_qa_session", max_parallel=4)          # partial-failure isolated
    .step("verify_facts")                            # §9 tri-state fact ledger (MageQA schema)
    .step("adjudicate_findings")                     # §9 keep only evidence-grounded findings
    .evaluate("coverage_gate", on_reject=Retrace("plan_qa_session"))  # deepen: re-plan if thin, bounded
    .subworkflow("report", workflow=REPORT_FLOW)     # §12 report-first (curate -> render -> write)
    .build()
)

# (2) WIRE dependencies via DI.
engine = WorkflowEngine.from_config("config/mageqa.audit.yaml")   # rubric/budget/per-role models/safety
engine.register_pack(SiteAuditPack())

# (3) RUN.
result = await engine.run("audit_site", SiteAuditInput(url="https://example.test", rubric=[...]))
# result.status / .output (report) / .node("run_scenarios").output / .usage / .trace
```

No MageQA-owned supervisor loop, fan-out loop, retry/deepen loop, or trace/budget runtime. The guard
`test_examples_contain_no_product_orchestration_loops` fails the build if a pack hand-rolls those.

---

## 1a. Adopter contract AC

Before MageQA treats the engine integration as ready, run these checks in the MageQA repo:

- **No product orchestration loop:** audit workflow packs declare `WorkflowDefinition`s and register
  capabilities; they do not hand-roll retry, retrace, fan-out, scheduler, or side-effect/budget loops
  around the engine.
- **One provider door:** `OpenAI`, `AsyncOpenAI`, `ChatOpenAI`, browser/CLI provider clients, and other
  paid/provider SDKs are constructed only in sanctioned MageQA factory/adapter modules.
- **Closed run-state statuses:** dashboard/API/orchestrator projections use engine statuses or a
  MageQA enum mapped deterministically from them. Ad-hoc progress states such as `ok`, `done`,
  `empty`, or `hollow` must fail schema/projection tests.
- **Suspend/resume remains engine-owned:** human review or blocked states use engine
  `requires_user_input`/snapshot paths instead of a separate MageQA wait loop.

Reference MageQA test shape: enum rejects ad-hoc statuses; engine result maps to deterministic
transition; failed/partial/blocked states remain visible and never collapse to silent-empty output.

---

## 1b. MageQA migration clarifications

These points answer the review questions that matter before paying someone to migrate MageQA.

- **The engine replaces local state/observation plumbing (since v0.6.4).** MageQA should not keep a local supervisor,
  state machine, trace collector, bundle writer, retry/deepen loop, or run-status vocabulary beside
  the engine. The MageQA product layer owns QA intelligence — prompts, rubrics, browser/CLI workers,
  evidence schemas, finding schemas, report/dashboard presentation — as capabilities and packs. The
  engine owns execution mechanics and the observation bundle.
- **Blocked scenario semantics.** "CAPTCHA", "browser unavailable", "MCP failed to connect", and
  "scenario could not run" are domain outcomes carried in the scenario capability output/metadata,
  not new engine statuses. Canonical pattern:
  - if a scenario produced useful evidence or a diagnosable blocker, return
    `CapabilityResult(status="partial", output=ScenarioResult(status="blocked", blocker={...},
    evidence_refs=[...]))`;
  - if a scenario produced no usable output, return `status="failed"` with `error` and blocker
    metadata;
  - reserve `status="rejected"` for policy/evaluator rejection, not infrastructure inability;
  - use workflow `requires_user_input` only for a real resumable human wait with a snapshot.
  A fan-out with some blocked/failed children becomes workflow `partial`; an all-blocked/all-failed
  fan-out becomes `failed` unless MageQA deliberately routes to a fallback/report path.
- **Bundle schema is versioned.** `meta.json` carries `bundle_schema_version` (currently `1`);
  a dashboard should check it and fail loudly on an unknown version instead of guessing. Fields:
  `run_id`, `workflow_id`/`workflow`, `status` (terminal, truthful), `timestamp`,
  `trace_path`/`detail_path`/`usage_path`/`definition_path`, `trace_count`/`detail_count`/
  `usage_count`, `total_tokens`, `metered_usd`, `notional_usd`, plus the evidence fields
  `artifact_manifest_path`, `artifact_root`, `artifact_count`, `artifacts_copied`. Records in the
  three JSONL files are `WorkflowTraceEvent` / `ObservationDetail` / `WorkflowUsageEvent` dumps,
  each stamped with `run_id` + a per-run monotonic `sequence`.
- **Evidence resolution is engine-owned.** Every run's `WorkflowArtifact`s (screenshots, page
  dumps, CLI salvage — anything a capability returns in `CapabilityResult.artifacts`) are archived
  INTO the bundle at finalize: copied under `artifacts/` and listed in `artifacts.json`. Resolution
  recipe for the dashboard: look up the artifact file path in `artifacts.json` by `source_path`,
  then open `bundle_path` relative to the bundle directory; `sha256` gives integrity,
  `media_type`/`role`/`owner_node` give rendering context. (The shipped viewer already does
  this: group pages render manifest entries as clickable links/inline image previews, and the
  served viewer serves the files via a manifest-allow-listed `/artifact/...` route — a custom
  dashboard is only needed beyond that.) CLI/browser capabilities already return
  `EvidenceRef.uri == WorkflowArtifact.path`, so evidence refs join directly. If MageQA emits a
  custom non-file URI such as `artifact://...`, it must also return the matching `WorkflowArtifact`
  path (or keep the URI equal to that path) so the dashboard can resolve it. Entries that could not
  be archived are honest, never missing: `copied: false` + `skip_reason`
  (`source_missing` / `exceeds_artifact_max_bytes` / `artifact_policy_off` / `copy_failed: …`).
  **Cleanup is ONE policy:** artifacts live inside the bundle, so `retention_limit` pruning deletes
  observations and evidence together — do not build a separate evidence store or a second retention
  job. Config: `observation.artifacts: copy|off` (default `copy`),
  `observation.artifact_max_bytes` (default 25 MiB per artifact). Failed runs archive their
  salvage too — failure evidence is the evidence that matters most.
- **Observation bundle consumption.** The durable bundle layout (v0.6.4+) is
  `trace.jsonl`, `details.jsonl`, `usage.jsonl`, `meta.json`, `definition.json`, plus the evidence
  manifest `artifacts.json` and the archived `artifacts/` directory, ordered by monotonic
  `sequence`. For the MageQA dashboard, treat `ai_workflow_viewer.EventSource` /
  `FileEventSource` / `JsonlObservationViewer` as the stable reader surface. Do not build an
  independent raw-JSON parser unless MageQA first asks the engine for an explicit schema-versioned
  export contract.
- **CLI-agent timeout salvage.** `CliAgentCapability` stages input assets, snapshots existing
  salvage files, runs the CLI, then salvages matching new files even when the process times out or
  exits non-zero. A timeout becomes engine capability `partial` and CLI result
  `status="truncated"`; stdout/result text, `stderr_tail`, return code if known, input fingerprints,
  salvaged `EvidenceRef`s, `new_artifact_count`, and subscription-notional usage metadata survive.
  A non-zero exit becomes capability `failed` and CLI result `status="error"`, still with salvage.
- **Adopter guard templates.** Copy the style from
  `packages/ai_workflow_engine/tests/test_contract_guards.py`: AST/source checks that fail with
  file:line evidence. MageQA should add repo-local tests for no product orchestration loop, one
  provider door, closed status projection, and no product bundle plumbing. These are load-bearing
  seams, not broad lint rules.
- **Memory recipe.** Implement a MageQA SQLite adapter behind the engine `MemoryStore` Protocol:
  `put(record, idempotency_key=None)`, `get(namespace, key)`, `search(namespace,
  metadata_filter=...)`, and `delete(namespace, key)`. Use
  `MemoryNamespace("mageqa", tenant_id, target_origin, record_kind)` where `record_kind` is one of
  `learned_flow`, `finding_history`, `target_profile`, or `flaky_check`. Store durable facts in
  `MemoryRecord.value`, provenance in `MemoryRecord.evidence_refs`, and schema/data versioning in
  `MemoryRecord.schema_version` / `metadata`. Memory is non-authoritative prompt/context input; it
  must not decide engine state transitions.
- **FlowArtifact boundary.** MageQA may let AI emit scenario plans and structured inputs for
  registered capabilities. Do not rely on AI-authored arbitrary workflows for the migration. Shipped
  `FlowArtifact` (v0.8.1, your pin) covers registered steps/branches/evaluators; the engine-v0.9.0
  tag (implemented on branch, NOT yet tagged) adds v1.5a bounded authored
  `fanout` with REQUIRED max_items and the turnkey `build_flow_author_capability(...)`; subworkflow
  references, richer dataflow, and reusable generated process pipelines are v1.5-remainder/v2
  future-stage.
- **Viewer integration.** `ai_workflow_viewer` supports both static HTML artifacts and a small local
  HTTP/SSE server. For the MageQA product dashboard, either link/embed generated HTML for a run or
  consume `EventSource`/`FileEventSource` and project the same records into Next.js. Keep the generic
  state/trace viewer separate from MageQA's product dashboard; the dashboard can add findings,
  evidence review, reports, and pitch views on top.

---

## 1c. Full requirements coverage (MageQA §1–20): engine-owned vs MageQA-owned

Verdict from the joint fit review: **all twenty requirement groups are expressible on the pinned
tag; none needs an engine rewrite or a product-side supervisor.** The split below is the contract —
if a migration step feels like writing plumbing from the left column, stop: that is a seam bug to
report, not code to write.

| § | Requirement | Engine gives (do NOT rebuild) | MageQA owns (write this) |
|---|---|---|---|
| 1 | NL scenarios → plan → bounded run | planner node, bounded depth/`max_total_planned_tasks`, fanout isolation, `evaluate`+`Retrace` deepen | planner prompts/rubrics, scenario schemas |
| 2 | Browser agent via CLI+MCP | `CliAgentCapability` (staging, salvage, timeout→partial, `--strict-mcp-config`; MCP startup failure is a loud failed episode — test-proven), notional cost | worker prompts, MCP config, a repo guard test forbidding silent parser fallback |
| 3 | Vision judgment | `ImageInput` (byte-free fingerprints), vision nodes, images on every attempt | consumption-PROOF prompt contract + output validation (engine proves what was SENT) |
| 4 | Deterministic probes | any-callable capabilities, schemas, side-effect classes, budget gates | the probes themselves (pack code) |
| 5 | Platform packs | `WorkflowPack`/`register_pack`, profiles/config switches | Magento/WordPress pack logic |
| 6 | Surface phase | steps/fanout, budget gate at surface exit (NOTE: sub-budgets check the shared CUMULATIVE spend — exact for the FIRST phase, approximate later; true per-phase scopes are a named-consumer slice if you prove the need) | probe capabilities, fingerprint schema |
| 7 | Analyze/plan | `.plan`, `PromptRef` strict prompt files, per-role `ModelProfile`, parse/repair, planner output in bundle | planner prompt files + plan schema |
| 8 | Deep-execution fanout | `.fanout`/subworkflow items, `max_parallel`, shared budget, per-item trace | instrument capabilities |
| 9 | Verification/fact ledger | plain steps+evaluate, partial/blocked normalization (§1b) | `FactClaim`/`FindingCandidate`/tri-state schemas, dedup, verifier capabilities |
| 10 | Evidence model | `EvidenceRef`, salvage, fingerprints, **archived artifacts + manifest + single retention policy (§1b)** | stable evidence semantics (roles), dashboard rendering |
| 11 | Regression/replay | `ReplayPlanner` (zero-LLM replay), recorded episodes, `MemoryStore` seam | learned-flow storage, baseline-diff capabilities |
| 12 | Report-first | report subworkflow position in the reference shape | report capabilities; **boundary: pitch/deck/video/design consume VERIFIED findings/report artifacts, never raw observations** |
| 13 | Pitch/deck/video | subworkflows + `ai_workflow_tools.media` seams (image/TTS) | storyboard/pitch schemas, video rendering (yours), evaluation prompts |
| 14 | Design/redesign | optional branch/subworkflow, image refs, human gate, budget | design generator capability, approval flow config |
| 15 | Human gates | top-level `.human`, durable snapshot/resume; nested suspension loudly unsupported | when to gate (config), review UI |
| 16 | Outreach seam | `external_write` side-effect class, fail-closed allow-list BEFORE spawn, denial traced | outreach templates, `auto_send` off by default (your config) |
| 17 | Dashboard/observability | config-first bundle, versioned schema, viewer/`FileEventSource`, `result.observation_bundle_path` | product dashboard views (findings/evidence/reports) on top |
| 18 | Cost ledger | usage events, metered vs notional split, `cost_known=false`, hard gates, denial trace | dashboard renders the split HONESTLY (never collapse notional into metered) |
| 19 | Durable memory | `MemoryStore`/`MemoryNamespace`/`MemoryRecord` seam (§1b recipe) | SQLite adapter + record kinds; memory = prompt input, never control |
| 20 | Safety | side-effect classes, fail-closed denial pre-invocation, traceable denials | public-surface policy, robots/rate rules, probe allow-lists |

---

## 2. Node kinds → MageQA roles

| Builder call | Node | MageQA role |
|---|---|---|
| `.step("plan_qa_session")` (capability `kind="llm"` via `StructuredLLMNode`) | step | coordinator plan: schema-validated scenario list |
| `.fanout(id, capability="run_browser_scenario", items_key=…, max_parallel=N)` | fanout | bounded browser/agent scenario execution, partial-failure isolated, shared budget |
| `.step("adjudicate_findings")` (deterministic) | step | grounding/adjudication: accept only evidence-backed findings |
| `.evaluate(id, on_reject=Retrace("plan_qa_session"))` | evaluate | review/deepen: re-plan or re-run scenarios under a bounded round cap, with criticism |
| `.branch(id, {label: target}, decider=…)` | branch | route by severity / coverage / "needs human review" |
| `.subworkflow("report", workflow=…)` | subworkflow | report sub-flow (curate → render → write), recursive, own budget |
| `.human(id)` | human | optional human review/triage (pause / provisional / resume) |
| `.step("write_report", side_effects=["external_write"])` | step | idempotent report/store handoff |

Browser/CLI workers are **capabilities**, not raw subprocess calls in flow code:
- an in-engine **bounded agent**: `AgentCapability` over scoped registered browser/MCP tools (step
  caps, tool-call caps, scoped tool names, recorded steps, replay, and subscription-mode metadata);
- a shipped tools-library **CLI agent**: `ai_workflow_tools.cli_agents.CliAgentCapability` over
  `claude_p` or `codex_exec`, with MCP config/env assembly, timeout, input-asset staging,
  salvage-always artifact provenance, `new_artifact_count`, and subscription-notional usage; or
- a shipped tools-library **console client**: `ConsoleLLMClient` for simple text-to-JSON planner or
  report nodes that do not need MCP/tools/workspace assets.

---

## 3. Registering capabilities (a `WorkflowPack`)

```python
class SiteAuditPack:
    def register(self, builder) -> None:
        builder.register_capability("plan_qa_session", PlanQaSession(...),       # StructuredLLMNode-backed
                                    kind="llm", metered=True, output_model=SessionPlan)
        builder.register_capability("run_browser_scenario", browser_agent,       # AgentCapability or adapter
                                    kind="agent", side_effects=["browser_drive"], timeout_s=120)
        builder.register_capability("adjudicate_findings", Adjudicate(...), kind="deterministic")
        builder.register_capability("coverage_gate", CoverageEvaluator(...), kind="deterministic")
        builder.register_capability("write_report", ReportAdapter(sink),
                                    kind="external", side_effects=["external_write"])
        builder.register_workflow(audit_site, profile=audit_profile)
        builder.register_workflow(REPORT_FLOW)        # registered so the subworkflow node can call it
```

Handler signature: `def handler(context, payload) -> output | CapabilityResult` (sync or async); an
object exposing `.spec` (e.g. `AgentCapability`, `ExternalAdapterCapability`) is accepted directly.
The `CapabilitySpec` carries input/output schema, `kind`, `side_effects`, `metered` (cost class),
`timeout_s`, `max_attempts`; the engine enforces them and **denies a capability whose `side_effects`
are not in the profile's `allowed_side_effects` — before the handler runs**.

**Coordinator decisions are data, not loops:** `plan_qa_session` returns a structured plan
(schema-validated, JSON-repaired by `StructuredLLMNode`); `coverage_gate` returns
`CapabilityResult(status="rejected", metadata={"criticism": "thin coverage on checkout"})` and the
engine deepens via `Retrace`, bounded by `RuntimeLimits.max_retrace`.

### CLI and console worker recipe

```python
from ai_workflow_engine import EvidenceRef, StructuredLLMNode, WEAK_MODEL_CLEANER
from ai_workflow_tools.cli_agents import (
    CliAgentCapability,
    CliAgentRequest,
    ConsoleLLMClient,
    McpServerConfig,
    claude_p,
)

browser_worker = CliAgentCapability(
    claude_p,
    name="run_browser_scenario",
    side_effects=["workspace_write"],
    asset_loader=load_evidence_bytes,    # product-owned EvidenceRef -> bytes
    trace_sink=live_trace_sink,           # optional Callback/AsyncQueue/Tee sink
)

report_node = StructuredLLMNode(..., llm=ConsoleLLMClient(claude_p), pre_parse=WEAK_MODEL_CLEANER)

async def write_report(_context, payload):
    return await report_node.run(payload)

builder.register_capability_spec(browser_worker.spec, browser_worker)
builder.register_capability("write_report", write_report, kind="llm")

request = CliAgentRequest(
    prompt="Inspect checkout with the supplied seed screenshot and return JSON findings.",
    workspace_dir="/tmp/mageqa/session-123",
    mcp_servers=[
        McpServerConfig(
            name="browser",
            command="npx",
            args=["@modelcontextprotocol/server-puppeteer"],
            env={"BROWSER_CHANNEL": "chrome"},
        )
    ],
    allowed_tools=["mcp__browser__navigate", "mcp__browser__screenshot"],
    input_assets=[EvidenceRef(role="seed", uri="artifact://seed-checkout.png", media_type="image/png")],
    salvage_globs=["*.png", "session*.md", "page-*.yml"],
    subscription_mode=True,
)
```

Prompt convention: staged `input_assets` appear under `inputs/` in the worker workspace. Do not copy
bytes into workflow state; the capability records `input_fingerprints` and emits `inputs_staged`
trace metadata. Salvaged outputs return `EvidenceRef`s plus `new_artifact_count`, and the trace gets
an `artifacts_salvaged` event. Adjudicators should gate on `new_artifact_count` or referenced
evidence, not on ungrounded agent prose.

---

## 4. Config & profile (`from_config`)

```python
profile = WorkflowProfile(
    workflow_type="audit_site",
    safety=SafetyPolicy(fail_mode="fail_closed",
                        allowed_side_effects=["browser_drive", "workspace_write", "external_write"]),
    limits=RuntimeLimits(
        max_parallel_children=4,
        max_retrace=2,
        max_estimated_usd=2.00,
        max_worker_calls=24,
        max_input_tokens_per_call=120_000,
        max_output_tokens_per_call=8_000,
        max_images_per_call=12,
    ),
)
```

The same application config file carries the observation policy (see "Config-first
observation" below) — one reviewable YAML for profile, models, and observation:

```yaml
observation:
  enabled: true
  bundle_dir: data/observations
  retention_limit: 100
  capture: full
```

Per-role models go in the YAML model registry (`ModelProfile` per capability/role); rubric/budget come
in via `engine.run(..., constraints={...})` or the goal. Swap the profile to change models/budget/
safety without touching the workflow (proven by `test_engine_from_config_swaps_profile`).
Metered API calls debit `WorkflowUsageSummary.metered_usd`; CLI subscription workers record
`WorkflowUsageEvent.cost_class="subscription_notional"` and `notional_usd` when the CLI reports it.
Unknown CLI cost stays explicit through `cost_known=false` metadata.

---

## 5. MageQA-specific engine features (do NOT reimplement)

- **Fan-out / gather.** `.fanout(..., max_parallel=N)` runs N scenarios concurrently with bounded
  parallelism and **partial-failure isolation** (a timed-out/failed scenario doesn't sink the run;
  the parent gets the successes, status `partial`) — proven by `test_fanout_gather_isolates_partial_failure`.
- **Bounded agents.** `AgentCapability` runs a scoped tool/reasoning episode with step + tool-call
  caps, allowed-tool scoping, recorded steps, structured finish parsing, replay, and
  subscription-mode metadata — so a runaway agent can't blow the budget. MCP/browser tools are
  reachable as registered capabilities.
- **CLI-agent episodes.** `CliAgentCapability` runs `claude -p` / `codex exec` through the same
  engine capability runtime, with side-effect denial before spawn, MCP config env, staged
  `input_assets`, artifact salvage, `new_artifact_count`, and shared engine JSON cleaners.
- **Console report/planner calls.** `ConsoleLLMClient` is an `LLMCallable` for simple CLI-backed
  structured nodes; images/tool-calling turns are refused before process spawn.
- **Deepen loop = evaluator retrace.** `.evaluate("coverage_gate", on_reject=Retrace("plan_qa_session"))`
  re-plans/re-runs under `max_retrace`, threading criticism — the engine owns the loop (proven by
  `test_evaluate_retrace_to_earlier_node_then_accepts`).
- **Grounding gate.** Adjudication is a deterministic capability that accepts only evidence-grounded
  findings; carry evidence as `EvidenceRef(role=…, uri=…)` (no raw bytes in state). Findings reference
  their `evidence_ref_id`.
- **Cost / subscription.** Mark metered capabilities `metered=True`; the engine tracks usage/cost and
  **denies metered calls once `max_estimated_usd` is exhausted** (proven by
  `test_budget_exhaustion_denies_metered_capability`). Subscription/flat-rate workers report
  `subscription_notional` usage; this is visible in traces and summaries but does not debit the
  metered budget.
- **Per-role models.** Register a `ModelProfile` per role; `from_config` selects them; swapping config
  changes models without code edits.
- **Trace.** `format_trace_events(result.trace, usage=result.usage)` renders capability name,
  decision, key output, timings, cost, artifacts, and deepen/fallback decisions — your
  `ProcessingTrace` maps onto the engine `TraceSink` (`InMemoryTraceSink`, `JsonlTraceSink`,
  `CallbackTraceSink`, `AsyncQueueTraceSink`, or `TeeTraceSink`).
- **Subworkflows (recursive).** `page_discovery` / `accessibility` / `performance` / `report` are
  separate `WorkflowDefinition`s registered as capabilities and composed via `.subworkflow(...)` on the
  same executor, with inherited/narrowed budget and parent/child trace.
- **Fail-closed safety propagation.** `SafetyPolicy(fail_mode="fail_closed")`; forbidden side effects /
  missing capabilities / unsupported nodes fail loudly + trace, never silently no-op.
- **Memory store contract.** The engine exposes deterministic `MemoryStore` plus dev-only
  `InMemoryMemoryStore`; MageQA owns the durable sqlite implementation behind that Protocol. Use
  `MemoryNamespace("mageqa", tenant_id, target_origin, record_kind)` where `tenant_id` is the hard
  isolation boundary, `target_origin` is the site/app target, and `record_kind` is a family such as
  `learned_flow`, `finding_history`, `target_profile`, or `flaky_check`. Recall is exact/filter-based
  (`get` / `search(metadata_filter=...)`), non-authoritative, and evidence-linked through
  `MemoryRecord.evidence_refs`; semantic/vector recall and LLM extraction are deferred.

---

## 6. Migration recipe

1. Keep your coordinator/agent/check/report functions — register each as a capability with a
   `CapabilitySpec` (schema + side-effect class + timeout + cost). Wrap browser/CLI workers as
   `AgentCapability` or `CliAgentCapability`; use `ConsoleLLMClient` for plain text-to-JSON planner
   or report calls.
2. Make the coordinator emit a **structured plan** (scenario list); make review/deepen an **evaluator**
   returning accept/reject + criticism; make routing a **branch decider**.
3. Express plan→fan-out→adjudicate→deepen→report with `WorkflowBuilder`; delete your coordinator loop,
   fan-out/gather loop, retry/deepen loop, and any trace/budget runtime.
4. Move per-role models / rubric defaults / budget / safety into a YAML profile; load via `from_config`.
5. If you need cross-run memory, implement a product sqlite `MemoryStore` and inject it into the
   capabilities that read/write learned flows, finding history, target profiles, and flaky-check
   state. Do not add a product coordinator loop around the engine for memory.
6. Replace your run entrypoint with `await engine.run("audit_site", site_input)`.

**Done when** you can delete MageQA orchestration loops and still run from
`WorkflowDefinition + registered capabilities` through `engine.run`. Verify with a `format_trace_events`
dump + the no-product-loop guard.

---

## 7. Reference (import from `ai_workflow_engine`)

`WorkflowBuilder, WorkflowDefinition, WorkflowNode, WorkflowEdge, WorkflowEngine,
WorkflowEngineBuilder, WorkflowPack, WorkflowExecutor, WorkflowRunResult, NodeResult,
BranchDecision, Retry, Retrace, Fallback, SubworkflowRef, AgentCapability, LLMAgentPlanner,
ReplayPlanner, EvidenceRef, ExternalWriteRequest, ExternalWriteResult, ExternalAdapterCapability,
ExternalProcessCapability, HumanClarificationCapability, StructuredLLMNode, SchedulingPolicy,
SafetyPolicy, RuntimeLimits, WorkflowProfile, ModelProfile, WorkflowGoal, InMemoryTraceSink,
JsonlTraceSink, CallbackTraceSink, AsyncQueueTraceSink, TeeTraceSink, MemoryNamespace, MemoryRecord,
MemoryStore, InMemoryMemoryStore, format_trace_events`.
Tools import from `ai_workflow_tools.cli_agents`: `CliAgentCapability`, `CliAgentRequest`,
`CliAgentResult`, `ConsoleLLMClient`, `McpServerConfig`, `claude_p`, `codex_exec`. Runnable
examples: `ai_workflow_engine/examples.py` (`build_demo_engine`, `SiteAuditPack`,
`run_toy_site_audit_pilot`, `run_toy_three_axis_site_audit_pilot`).


---

## What engine-v0.8.1 gives you

`engine-v0.8.1` gives MageQA the full capability set below (older tags
are unsupported — no deltas to track):

- **Discoverable toolset (v0.7.0).** `TOOL_CATALOG` / `render_tool_catalog()` /
  `register_from_catalog(engine, name, **kw)` in `ai_workflow_tools`; presets
  `READ_ONLY/WEB/INVESTIGATION/NO_TOOLS`; tri-state `allowed_tools` with Bash-honest
  side-effect declaration + pre-spawn denial.
- **One-call façade (v0.7.0).** `run_single_llm` / `run_single_step` — trivial calls on the
  real engine, with usage/trace/budget/loud failures inspectable from the returned run wrapper or
  the passed `engine=`.

- **Agent brain + LLM protocol.** Multi-turn, tool-calling protocol (`ChatMessage`/`ToolSpec`/
  `ToolCallRequest`/`ToolResult`); a shipped `LLMAgentPlanner` (screenshot→vision loop, per-turn
  budget, structured finish with repair) and `ReplayPlanner` (freeze a recorded episode into a
  zero-LLM regression). One-line wire: `build_llm_agent_capability(llm, engine.registry,
  allowed_tools=[...], runtime=engine.runtime)`.
- **CLI worker economics** (`ai_workflow_tools`): `CliAgentCapability` (`claude_p`/`codex_exec`, MCP
  config incl. `env`, workspace salvage→`EvidenceRef`s, `new_artifact_count`, fingerprinted
  `input_assets`) and `ConsoleLLMClient` (text→JSON over a subscription CLI behind structured nodes).
  The three-axis pilot (`ai_workflow_tools.pilots.run_toy_three_axis_site_audit_pilot`) is the
  canonical end-to-end recipe; its test proves the replay run makes ZERO LLM calls.
- **Budget + honest cost.** `max_worker_calls`, per-call token/image/USD caps (all Optional);
  `metered` vs `subscription_notional` (`cost_known=false`, never phantom $0); per-call output
  overflow records `truncated_by_budget` and the run continues; hard stops are `max_worker_calls` +
  USD caps. Accounting is split into focused modules (budget / pricing / provider_usage / usage_events / usage_rendering) behind the stable `ai_workflow_engine.usage` facade — public imports unchanged.
- **Self-describing machine.** Declare label semantics once (`.branch(..., describe={...})`); deciders
  get their legal moves + live gate budgets via `inject_machine=True` (`context.metadata["machine"]`).
  Any branch label that closes a loop REQUIRES a pre-set gate (`bounds={"label": N}`,
  `exhausted={"label": "escape"}`) — ungated cycles fail validation loudly. **Validation now requires a `describe` entry for EVERY decision label on an `inject_machine=True` branch**: an option the navigator cannot understand fails the build loudly instead of producing an opaque machine card. Zero-LLM routing via
  `engine.register_guard(...)` (traced `decision_policy`). Feed flow-author prompts with
  `render_capability_catalog(engine.registry, allowed)`.
- **Flow-as-data + planning.** `engine.run_authored_flow(FlowArtifact(...), payload)` — an LLM emits a
  constrained workflow (steps/branches/gates), validated before compiling, run on the same rails (no
  nested planners/flow-authors — recursion firewall). Bounded recursive planning (`.plan(...,
  max_plan_depth=, max_total_planned_tasks=)`); parallel sub-workflows
  (`register_workflow_capability` + `.fanout(..., capability="run_child")`, failure-isolated).
- **Durable suspend/resume + memory.** Long runs suspend/resume via `result.snapshot` +
  `engine.resume` (budgets cumulative). Cross-run memory via the `MemoryStore` seam
  (`MemoryNamespace`, `MemoryRecord`, `InMemoryMemoryStore`; bring your own sqlite for durability).
- **Media** (`ai_workflow_tools.media`): image/voice providers live in tool packs; the engine stays
  provider-neutral (`vision`/image-input stays in the engine as LLM protocol). Install
  `ai-workflow-tools[media]`.
- **Config-first observation (zero product plumbing).** Declare it once in the application
  config —

  ```yaml
  observation:
    enabled: true
    bundle_dir: data/observations
    retention_limit: 100
    capture: full
    artifacts: copy          # archive run evidence INTO each bundle (default)
    artifact_max_bytes: 26214400
  ```

  — and the ENGINE owns every per-run mechanic: it auto-opens the bundle, routes
  trace/details/usage into it, archives the run's artifacts (evidence) with an honest manifest,
  finalizes with the true terminal status, prunes old finalized bundles — evidence prunes WITH
  its bundle, one retention policy — and reports the location on `result.observation_bundle_path`. Products never call
  bundle mechanics in the normal path; `engine.run(observation_bundle=)` remains the explicit
  escape hatch (it takes precedence) and `terminal_status=` stays the post-validation hook —
  a raising run archives as failed, and the record can never say completed for a user-visible failure.
- **Prompt files with strict rendering.** Prompts can live as FILES under a locked prompt root:
  `StructuredLLMNode(prompt_ref=PromptRef("mageqa/route.txt"), prompt_renderer=engine.prompt_renderer)`
  (split static/dynamic refs supported). A missing variable fails LOUDLY before any model call;
  paths cannot escape the root; template+rendered digests are recorded. Jinja is one optional
  dialect (`renderer="jinja"`, strict-undefined) — requires the product to install `jinja2`;
  never an engine dependency. Raw string templates keep working; refs and raw are mutually
  exclusive per node.
- **Machine identity + fresh compiles.** Every `WorkflowDefinition` carries a content digest;
  compiled graphs and registries key on `(workflow_id, digest)`, so re-registering a changed
  definition under the same id runs the NEW machine (a `machine:re-registered` trace event marks
  the swap) — dynamic/authored flows can never execute a stale graph.
- **Nested suspension is rejected loudly (current restriction).** A subworkflow whose child
  (transitively) declares `human` nodes fails preflight, and a child that suspends at runtime fails
  the parent node with an explicit error instead of silently continuing — waits belong at the
  top-level workflow until nested snapshots ship (named-consumer gated).
- **Envelope trace is sink-independent.** `result.trace` comes from a per-run session buffer — it
  is complete with Jsonl/bundle sinks and long-lived engines no longer accumulate cross-run trace
  history for the envelope. Planner fanout now enforces `max_total_planned_tasks` exactly like
  sequential execution.
- **Hardened seams + per-run isolation.** Contract guards ship with the engine repo and are
  mutation-verified: one provider door (construction only in sanctioned factory/adapter modules),
  no product orchestration loop, closed run-state vocabulary, engine-imports-no-products, and the
  executor/node boundary (`NodeExecutionServices` — node handlers are testable against a fake
  services object; see `tests/test_contract_guards.py` for the guard style to copy). Every run gets
  its own internal run session: concurrent runs on ONE engine are isolation-tested
  (trace/usage/detail partition by run id), resume rebuilds the session without re-executing
  completed nodes, and an attached observation bundle finalizes with the run's terminal status —
  failed runs included.
- **Observability — log→bundle→viewer.** A run appends durable events to a per-run bundle
  (`observations/<run_id>/{trace,details,usage}.jsonl` + `meta.json` + `definition.json` +
  `artifacts.json` manifest + archived `artifacts/`); nothing is
  rendered in the hot path. The separate **`ai_workflow_viewer`** package reads bundles (via
  `EventSource`/`FileEventSource`) and builds every view on demand — the state-machine diagram
  (`workflow_to_mermaid` / `save_workflow_html`), the observation graph + timeline
  (`save_observation_html`), and a run chooser / JSON-detail server (`JsonlObservationViewer` /
  `serve_viewer`). Capture has an off-switch; bundle dir defaults to `data/observations` (override
  `OBSERVATION_DIR`). The disk bundle is transport #1 — swappable to a bus/live consumer later.

## v0.9 (branch) delta — durable waits: 2-minute migration

**Pin stays engine-v0.8.1 until the v0.9.0 tag exists.** When you move:

1. Every `.human(node)` now requires a wait policy:
   `.human("gate", wait_policy=LocalWaitPolicy())` = exactly the old suspend/resume;
   `.human("gate", wait_policy=DurableWaitPolicy(timeout_s=3600), timeout_to="escalate")`
   = durable (declared timeout route is mandatory graph structure).
2. Durable needs a coordinator: `builder.with_wait_coordinator(your_adapter, clock=...)`.
   Validate your adapter with `run_wait_registration_conformance` (full lifecycle:
   register/claim/lease/frozen-acceptance/CAS/failure kinds/cancel/stalled).
3. Your loop owns time (engine never self-fires): `due(now)` → deliver timeouts via
   `engine.deliver_wait_event(wait_id, {"kind": "timeout", ...})`; `stalled(now)` →
   redeliver the accepted event or `engine.cancel_wait(wait_id, reason=...)`.
4. Guarantee wording: at-least-once with idempotent effects (key external writes on
   `context.metadata["wait_idempotency"]`) — never advertise exactly-once.
5. Observation: one logical run = a GROUP of segments; use
   `FileEventSource.read_group(run_id)` / the served viewer (already grouped). Suspended
   groups are never evicted by default; opt-in cap = `ObservationConfig.evict_suspended_after_s`
   (evicts viewer history only; durable waits stay resumable via the coordinator's snapshot — local waits only if you kept yours).

### Run ID vs Related-run ID (identity contract)

| | `run_id` | `correlation_id` (shown as **Related-run ID**) |
|---|---|---|
| Means | ONE logical engine execution, incl. all suspension/resume segments | Optional caller-supplied grouping key shared by SEPARATE runs of one case/audit/batch |
| Set by | engine (or `goal.metadata["run_id"]`) | you: `WorkflowGoal(correlation_id="case-42")` |
| Controls | storage identity, observation group, dedup | NOTHING — observability metadata only |
| Where it appears | every event, bundle dir/meta | auto-stamped into every engine-written event's metadata + bundle meta; survives snapshots |

Viewer: the chooser shows a Related-run ID column; clicking it (or `?related_run_id=X`)
filters to that case's runs WITHOUT merging them. External writes: the engine never
mutates your command payloads — copy `context.run_context.correlation_id` into your
`ExternalWriteRequest.metadata`. Blank ids are rejected; conflicting ids on any event
fail loudly.
