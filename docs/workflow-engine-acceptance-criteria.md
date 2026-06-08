# Workflow Engine — Acceptance Criteria (Architecture + Implementation)

Status: **active acceptance criteria** (Claude-authored 2026-06-08; Codex-reviewed against current
implementation)
Parents: `docs/workflow-engine-architecture.md`, `docs/workflow-engine-implementation-plan.md`,
`docs/workflow-engine-config-architecture.md`
Extends: `docs/anki-acceptance-criteria.md` (AC-01..AC-24 stay valid; must still hold after migration)
Verified against real repos: MageQA
(`/Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md`,
`/Users/artemm/PycharmProjects/MageQA/docs/14-agentic-tester-architecture.md`,
`/Users/artemm/PycharmProjects/MageQA/docs/02-architecture.md`); GoPro
(`/Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md`
= `req:`, `…/universal_event_descriptor/UNIVERSAL_PROFILE_SHEET.md` = `sheet:`,
`HOME_INVENTORY_CASE.md` = `inv:`).

## How to use this doc
- AC IDs: `DOD-*` enforcement, `UNI/CMP/WL-*` universality, `A-*` architecture, `B-*` implementation,
  `CFG-*` config. Every AC has: a statement, a **proof** (the test/guard that makes it objective), and
  a **why** citation where a specific workload forces it.
- **Blocking** = the phase/PR cannot be called done until it is green. Everything here is blocking
  unless marked `(stretch)`.
- "Proof" must be an automated test or an explicit guard, not a claim. "No proving test = not done."

---

# PART 0 — ENFORCEMENT (blocking, applies to every phase)

## 0.1 Completeness & anti-downgrade
The framework target is a real goal-driven capability runtime. The known failure mode is silently
replacing a hard primitive with a trivial stand-in (supervisor→linear cycle, agent→no-op, fan-out→
one item, retrace→fake) and declaring done. Forbidden.

- **DOD-1 No stubs in shipped code.** No `NotImplementedError`, `pass`-only body, `TODO`, `FIXME`,
  `"simplified"`, `"for now"`, `"placeholder"`, or dead `return None` standing in for behavior in any
  primitive marked done. Proof: `tests/unit/test_workflow_engine.py::test_engine_package_has_no_shipped_stub_markers`.
- **DOD-2 Supervisor is a real selector.** Proof: a toy where the supervisor chooses among ≥2 distinct
  capabilities and takes different paths for different inputs. Deterministic product graphs (Anki) are
  NOT evidence the supervisor works. Downgrading it to a linear runner is forbidden. Current proof:
  `test_workflow_supervisor_selects_different_instruments_for_different_goals`.
- **DOD-3 Agent/subprocess capability is real.** Proof: toy spawns a process/agent, enforces timeout,
  captures stdout/stderr + output files, salvages partial output on timeout, records `status ∈
  {completed,truncated,error}` and fallback reason. (MageQA `17:111-112,130`; GoPro `req:246-258`.)
- **DOD-4 Fan-out is real.** Proof: ≥3 children, ≥1 times out, others gathered, per-child trace +
  shared budget recorded, failure isolated. Current proof:
  `test_gather_capabilities_records_timeout_as_partial_failure`. (MageQA `17:126,269`.)
- **DOD-5 Evaluator/retrace is real.** Proof: reject→retry-one-capability-with-criticism→accept;
  reject→retrace-to-earlier-step→accept; cap→fallback/fail. Single accept/reject with no criticism
  or retrace target does not pass. Current proof: `test_evaluation_controller_retries_one_capability_with_criticism_then_accepts`,
  `test_evaluation_controller_retraces_to_earlier_capability_with_criticism`, and
  `test_evaluation_controller_falls_back_or_fails_when_policy_exhausts`. (MageQA deepen `17:128,162`.)
- **DOD-6 No silent downgrade / scope removal.** If codex thinks a primitive is unneeded/infeasible/
  should be simplified, it STOPS and flags Artem with the reason. Shipping a weaker version is
  forbidden (high-impact per collaborative skill + CLAUDE.md).
- **DOD-7 Per-primitive Definition of Done:** (a) real impl, (b) ≥1 behavior test on the non-trivial
  path, (c) wired into a toy or Anki, (d) trace+usage emitted, (e) no DOD-1 markers, (f) committed at
  the phase gate.
- **DOD-8 Phase done only when its AC gate AND the full default suite are green.** No "done except X."
- **DOD-9 Self-report:** each phase commit records in the implementation plan which DOD/A-/B- gates
  passed, linking the proving tests. No proving test = not done.
- **DOD-10 No interface-only delivery.** A bare `Protocol`/ABC with no working default impl + behavior
  test + ≥1 wiring is not done. The engine ships runtime, not impl-pushed-to-products. Product seams
  (`ExternalAdapterCapability`, domain capability) still ship a default/reference impl + example.
- **DOD-11 Universal, not product-shaped.** Public API/names/fields pass the "third-project test"
  (make sense for calendar/MageQA/GoPro without Anki concepts). Product names in engine APIs = not done.
  Current proof: `test_engine_public_contract_has_no_product_specific_names`.
- **DOD-12 No silent fallback / no silent no-op.** Every fallback/partial/rejected output records the
  failed step, criticism, selected policy, cost impact, and user-visible effect. A configured
  capability that is missing must warn/error, never silently weaken. (GoPro `req:340-342`; MageQA `02:192`.)
- **DOD-13 No hidden compatibility bridges.** Architecture work must not preserve obsolete aliases,
  data shapes, or legacy behavior as a quiet default. If a bridge is needed for deployment, it must
  be product-local, documented with an explicit approval/sunset decision, and absent from
  `packages/ai_workflow_engine`. Current open bridge: TGHandyUtils `ANKI_*` env aliases during
  config migration.

## 0.2 Universality & completeness — ALL workloads (Anki, MageQA, GoPro, calendar)
- **UNI-1 Product-neutral public API.** No `anki/cloze/telegram/mageqa/browser/scenario/gopro/frame/
  calendar` term in engine public names, base classes, or runtime fields. Proof:
  `test_engine_public_contract_has_no_product_specific_names`.
- **UNI-2 Third-project pressure test = calendar-builder (REAL toy).** A `plan_build` workload (build a
  plan from availability, energy, priorities, task pool, focus projects) runs end-to-end through the
  engine from ONLY a capability pack, exercising profile→plan, capability selection, a **non-LLM
  optimizer/solver capability**, an evaluator decision, a retrace, trace/budget, and an
  infeasible-output path. Proof: a passing toy. (Calendar is neither generation nor QA nor streaming
  → proves universality; `inv:404-417,472-513`.)
- **CMP-1 Copy/completeness test.** The calendar toy + MageQA/GoPro skeletons reimplement ZERO engine
  runtime (supervisor, runner, capability invocation, structured-LLM+repair, model/profile selection,
  evaluator/retry/retrace/fallback/fail, loop/recursion limits, usage/budget/cost/cache, artifact
  ownership, trace sink, fan-out/gather, scheduling, external-agent wrappers). Packs contain ONLY
  domain schemas/prompts/capabilities/adapters/delivery. Copying engine runtime = framework gap to fix.
- **CMP-2 No interface-only primitives** = DOD-10.
- **UNI-3 Same-runtime proof.** Anki + calendar toy + MageQA/GoPro skeletons run on the SAME
  `WorkflowRunner`/`CapabilityRuntime`; no runner subclassing or per-workload fork. Proof: each
  registers a pack and runs.
- **WL-Anki:** full migration (Phase 6) + Part B parity + AC-01..AC-24.
- **WL-MageQA:** handoff (Phase 7) + verified mappings against the real repo + a runnable skeleton for
  ≥1 scenario (see B10). No "assumed" mappings.
- **WL-GoPro:** handoff (Phase 8) + a live-constraints toy + verified mappings (see B11).
- **WL-Calendar:** UNI-2.

---

# PART A — ARCHITECTURE ACCEPTANCE CRITERIA (the framework is "done" only if all hold)

Each AC names the workload(s) that force it. "Both" = MageQA + GoPro; "All" = +Anki +calendar.

### A1 — Goal, input, profile, runtime plan
- **A1.1** `WorkflowGoal`/`WorkflowInput` carry objective, constraints, profile ref, evidence refs,
  delivery target, session ref. Proof: model + toy. (All.)
- **A1.2** `WorkflowProfile` compiles via `RuntimePlanCompiler` to an **inspectable `RuntimePlan`
  persisted with every run** (audit artifact). Proof: toy compiles + plan appears in trace.
  (GoPro `req:122-145,296-309`; MageQA `TestPlan` `17:221`.)
- **A1.3 Profile no-op detection.** Unknown/unsupported profile key → **explicit warning or hard error
  per strictness**; a profile must never "appear accepted while silently doing nothing." Proof:
  mapping-table validator test + round-trip test (sheet keys → runtime keys → back). (GoPro `sheet:107-134`,`req:95`.)
- **A1.4** Capability status surface: a capability referenced but `future/unsupported/planned` is
  reported, not silently skipped. (GoPro `sheet:243`.)

### A2 — Capability runtime / registry / spec
- **A2.1** `CapabilitySpec` carries name, kind, input/output schema, side-effect class, budget policy,
  timeout policy, trace policy, safety policy, prompt refs. `CapabilityRegistry` registers; runtime
  invokes via one path. Proof: register+invoke toy; schema validated in/out; one trace event; usage delta.
- **A2.2 Scripts-as-capabilities.** A plain Python function (e.g. a calendar optimizer) is a
  first-class capability with typed I/O, limits, trace, side-effect class — NOT a special case. Proof:
  the calendar optimizer capability (UNI-2). (Architecture "scripts are capabilities".)
- **A2.3 Non-LLM / non-media capability.** The registry + evidence/state models hold capabilities whose
  inputs are constraints/data (not prompts/media). Proof: calendar optimizer runs with constraint
  inputs, no LLM call. (Calendar; `inv:404-417`.)
- **A2.4** Capability kinds cover at least: structured-LLM, deterministic-python, media-provider,
  external-process/agent, subworkflow, evaluator, external/domain-adapter, human-clarification. Proof:
  one toy per kind. (All.)

### A3 — Model profiles, provider selection, execution mode
- **A3.1** `ModelProfile` registry: per-role model/temperature/timeout/retries, swappable **without
  code change** (cheap planner vs strong judge). Proof: switch profile via config, behavior changes.
  (MageQA `17:136`.)
- **A3.2 Subscription / flat-rate execution mode.** An agent capability can run via a flat-rate CLI
  (`claude -p`/`codex exec`) recorded as `subscription_call=True, notional_cost_usd, metered_usd=0`;
  metered API path is opt-in with its own lower cap and every fallback is visible in trace. Proof:
  toy agent in subscription mode records ~$0 metered + notional. (MageQA `17:133,255-259`.)

### A4 — Structured LLM
- **A4.1** `StructuredLLMNode`: Pydantic-validated output, exactly one repair retry on invalid JSON,
  injected LLM/factory (no provider hardcoded in engine), trace of both attempts. Proof: toy bad-then-
  repaired JSON. (All; existing AC-05.)

### A5 — Evaluator / retrace / retry / deepen
- **A5.1** `EvaluationDecision ∈ {accept, retry_capability, retrace_to, fallback, fail, ask_user}`,
  bounded; criticism (`CriticismEnvelope`) passed to the retried/retraced step. Proof: DOD-5 toys.
  Current proof: generic `EvaluationController` drives retry, retrace, fallback, and cap-exhaustion
  cases through `CapabilityRuntime` without Anki-specific logic.
- **A5.2 Bounded iterative deepening / re-dispatch** keyed to hot spots, budget per round, max rounds —
  not merely retry-on-failure. Proof: toy deepens once then stops at cap. (MageQA `17:128,162,184`.)
- **A5.3 Deterministic hooks between agent stages.** Code (dedup, scoring, grounding, severity-normalize)
  runs **between** plan/dispatch/review, not only pre/post. Proof: toy runs a deterministic hook
  between two capabilities and the result feeds the next. (MageQA `17:134`.)
- **A5.4 Invalid-retrace + infinite-loop guards.** Retrace to an unknown target errors; an explicit
  `recursion_limit` routes runaway loops to fallback/fail (never `GraphRecursionError`). Proof: toy.
  Current proof: `test_workflow_runner_forwards_optional_graph_config` and
  `test_graph_invokes_runner_with_explicit_recursion_limit` prove explicit graph controls are wired;
  `test_workflow_runner_routes_recursion_exhaustion_to_policy_fallback` proves the reusable runner
  routes recursion exhaustion to a caller-supplied fallback/fail policy; and
  `test_graph_recursion_exhaustion_uses_text_fallback_instead_of_leaking_error` proves an
  adversarial tiny-limit Anki graph returns a validated fallback card instead of leaking
  `GraphRecursionError`.

### A6 — Agent / subprocess capability
- **A6.1** Bounded agent: caps on steps, tool calls, model calls, wall-clock, per-tool timeout, optional
  per-tool budget. Proof: toy hits each cap. (GoPro `req:246-258`; MageQA `17:111`.)
- **A6.2** Salvage-on-timeout: partial artifacts + parsed partial output retained; status reported.
  Proof: DOD-3 toy. (MageQA `17:112`.)
- **A6.3** Scoped tool/MCP allow-list passed per agent (see A11). Proof: toy denies an out-of-scope tool.
  Current proof: `AgentCapability` runs a bounded episode over registered tools, records per-tool
  history, denies tools absent from `AgentRunRequest.allowed_tools`, truncates on step cap, and marks
  subscription-mode episodes as `$0` metered. Remaining product work: bind MageQA browser/MCP CLI
  workers as project adapter capabilities.

### A7 — Fan-out / gather
- **A7.1** Bounded `gather_capabilities`: N children, shared budget guard, per-child trace IDs, failure
  isolation, partial gather. Wall-clock = slowest child, not sum. Proof: DOD-4 toy. (MageQA `17:126,269`.)
  Current proof: partial gather keeps successful child results when one async child times out and
  records `TimeoutError` instead of silently dropping the failure reason.

### A8 — Scheduling / concurrency  ← HIGHEST-RISK; the engine must span both extremes
- **A8.1 Per-profile scheduling policy** selectable across the full spectrum:
  `fan_out_gather` (MageQA parallel) ↔ `live_latest_only` / `drop_not_queue` (GoPro live) ↔
  `replay_process_all` (offline). Proof: a toy per mode. (MageQA `17:126`; GoPro `req:264-281`,`sheet:359-374`.)
- **A8.2 Single-flight with cancel-not-release.** One in-flight episode per camera/profile scope;
  **cancellation must NOT release the single-flight lock while the underlying model/tool call is still
  running.** Proof: toy proving the lock holds through cancel until the call truly ends. (GoPro `req:255-258`.)
- **A8.3 Coalescing + manual priority.** Background/timer triggers coalesce or drop per policy; manual/
  user work queues ahead of background. Proof: toy. (GoPro `req:256-257,271-281`.)
- **A8.4 Per-backend concurrency limits.** Proof: toy caps concurrent calls per backend. (GoPro `req:272`.)
- **A8.5 Sidecar / hot-path boundary.** The engine must NOT push live frame/stream processing through
  the agent loop; the agent runs as a bounded **sidecar** off the hot path. Proof: GoPro toy keeps a
  fast hot path while the agent runs async. (GoPro `sheet:262-284,334`.)
  Current proof for A8.1-A8.4: `test_workflow_scheduler_modes_priority_coalesce_and_backend_caps`
  covers fan-out-gather acceptance, replay queue priority, coalescing, live-latest-only queued latest,
  drop-not-queue, per-backend concurrency cap, and single-flight cancel-not-release is covered by
  `test_workflow_scheduler_drop_stale_and_single_flight_cancel`.

### A9 — Evidence refs (not raw bytes)
- **A9.1** `EvidenceRef` carries source id, time range, frame/file/URI ref, **typed role**
  (e.g. `location_context/transition/contents/readable_label/current_frame/history_sample/screenshot/
  dom_snapshot`), quality, provenance, retention, privacy, confidence. Proof: model + toy roundtrip.
  (GoPro `req:176-194`; MageQA `02:131-141`.)
- **A9.2 Raw bytes stay out of task state.** State holds refs; raw media is fetched only by a permitted
  tool. Proof: guard test that task/session state contains no large byte payloads. (GoPro `req:190-191,399`.)
- **A9.3 Grounding gate (code invariant, not model discretion).** A finding/observation survives only if
  the producing capability actually drove + recorded live evidence this run; "no evidence → no finding"
  is enforced in code. Proof: toy where a finding without evidence is dropped. (MageQA `17:238`,`14:133`.)
- **A9.4 Derived/annotated artifacts.** Evidence model carries derived artifacts (e.g. annotated
  screenshots), not only raw refs. Proof: toy attaches a derived artifact. (MageQA `02:129`.)

### A10 — Session / task state lifecycle
- **A10.1** `SessionState` store scoped by task/profile/session; lifecycle created/updated/expired/
  completed/failed/cancelled; supports **provisional values with confidence + evidence refs**. Proof:
  toy mutates provisional value + records confidence; mutations traceable. (GoPro `req:156-170`.)
  Current proof: `WorkflowLoopController` writes `WorkflowCheckpoint` records through swappable
  checkpoint stores, `JsonlCheckpointStore` reloads the latest checkpoint after a new instance is
  created, and checkpoint data rejects raw bytes. Remaining product work: decide how each product
  resumes from engine checkpoints versus product-owned domain state.

### A11 — Tool allow-list + side-effect classes
- **A11.1** Tools declare a **side-effect class** ∈ {read-only, local-write, external-call, notification,
  destructive}; **external-call + raw-media are disabled by default**; allow-list is a hard gate;
  disallowed tool attempts are recorded as denied. Proof: toy denies a destructive/external tool and
  traces the denial. (GoPro `req:219-228,452-459`; MageQA `17:123-128`.)
  Current proof: `CapabilityRuntime` rejects capabilities whose declared side effects are absent
  from `RuntimePlan.safety.allowed_side_effects` before the handler runs and traces the denial.
  `AgentCapability` also denies per-agent tool names outside `AgentRunRequest.allowed_tools`.

### A12 — Fail modes
- **A12.1** `FailMode ∈ {fail_open, fail_closed, product-defined}`; safety/risk profiles default
  fail-closed; **fail-closed must NOT silently downgrade to fail-open**. Proof: toy fail-closed blocks
  fallback and returns an auditable failure. (GoPro `req:428-446`; MageQA safety `17:113,260-262`.)
- **A12.2 Safety policy propagation.** A safety policy (e.g. public-surface-only) is carried into EVERY
  agent capability prompt and re-checked post-run; the supervisor cannot relax it. Proof: toy + a
  post-run safety gate. (MageQA `17:113,220,260-262`.)

### A13 — Human clarification
- **A13.1** `ask_user` is a traceable capability with pluggable channels; the workflow can **pause and
  resume**, or **continue with provisional values if unanswered**. Proof: toy emits a clarification,
  records the answer/provisional continuation, finishes. (GoPro `req:404-422`; Anki 5s window becomes this.)
  Current proof: `HumanClarificationCapability` emits pending clarification through an injected
  channel, `WorkflowLoopController` returns `waiting`, a submitted answer resumes through the same
  capability, and `continue_without_answer` returns a provisional value. TGHandyUtils auto mode now
  routes the 5-second Telegram correction window through `HumanClarificationCapability` with a
  Telegram channel, side-effect policy, and traceable pending/answered/provisional responses.

### A14 — Trace sink (D3)
- **A14.1** `TraceSink` is a swappable abstraction; engine owns the **emission contract**, not a single
  store. Ships in-memory + file/JSONL impls (SQLite optional). Proof: events captured by a test sink;
  swap impl without code change. (D3.)
- **A14.2 Append-only, framework-independent, versioned schema.** No LangGraph/LangChain types in trace
  records; trace records denied tools, fallbacks, timeout/cancel/error paths, cost. Proof: schema test
  + a guard that trace records contain no framework types. (GoPro `req:316-364`.)

### A15 — Artifacts
- **A15.1** Artifact ownership + cleanup: generated artifacts are owned by the producing node and cleaned
  on abandoned branches/fallback before retrace. Proof: cleanup toy. (Anki AC-19; All.)

### A16 — Usage / budget / cost / cache
- **A16.1** Per-run `WorkflowUsageSummary` (per-call model, node, attempt, tokens, request id, image/
  voice counts, estimated USD, cached tokens). Proof: usage test. (All; AC-17/18.)
- **A16.2 Budget inheritance** parent→child; **exhaustion routes to fallback/fail with zero further paid
  call**. Proof: budget-max toy asserts no extra provider call after cap. (All.)
- **A16.3** Prompt-cache-aware: stable instructions/schema/style first, dynamic tail second; cached
  tokens surfaced. Proof: prompt-order test. (AC-18.)

### A17 — Output schema (uncertainty)
- **A17.1** `WorkflowResult` natively expresses uncertainty: `unknown/uncertain/partial/low-confidence/
  insufficient-evidence/requires-user-input/external-tool-unavailable`. Proof: each value reachable in a
  toy; no hallucinated confident output when evidence is insufficient. (GoPro `req:303-311`; calendar infeasible.)
  Current proof: `test_workflow_result_exposes_all_uncertainty_states`.

### A18 — External / domain adapter
- **A18.1** `ExternalAdapterCapability` receives structured observations + evidence refs, performs
  **idempotent** writes, declares privacy level, and keeps durable domain state outside the engine.
  The package ships in-memory and JSONL sinks; products may inject direct/HTTP/MCP/queue sinks without
  changing workflow code. Proof:
  `tests/unit/test_workflow_engine.py::test_external_adapter_capability_writes_idempotently`,
  `test_external_adapter_capability_rejects_secret_payload_without_secure_sink`, and
  `test_jsonl_external_write_sink_records_machine_readable_audit`. (GoPro `req:385-401`,`inv:107-136`.)

### A19 — Public-contract neutrality / decoupling
- **A19.1** No framework-specific types (LangGraph/LangChain) in profiles, traces, tool schemas, or
  results. Proof: guard test. (GoPro `req:90-98`.)
- **A19.2** Engine package imports no `core/services/config/handlers` of any product. Proof: decoupling
  guard (Phase 0).

---

# PART B — IMPLEMENTATION ACCEPTANCE CRITERIA

## B0 — Config architecture (see `docs/workflow-engine-config-architecture.md`)
CFG-1..CFG-8 from that doc are blocking and supersede plan task P0.1 (no mass env rename; non-secret
config → YAML/profile; env = secrets/deployment knobs + explicit overrides; active required secrets
fail loud; no secret in committed files).
Current proof: `tests/unit/test_config_profile.py` and `tests/unit/test_workflow_config_loader.py`
are green; the combined proof command is
`./test.sh unit -- tests/unit/test_config_profile.py tests/unit/test_workflow_config_loader.py tests/unit/test_workflow_engine.py tests/unit/test_container_wiring.py --cov-fail-under=0`
plus Anki graph RuntimePlan wiring is covered by
`./test.sh unit -- tests/unit/test_config_profile.py tests/unit/test_workflow_config_loader.py tests/unit/test_workflow_engine.py tests/unit/test_container_wiring.py tests/unit/test_anki_generation_graph.py --cov-fail-under=0`
-> `112 passed`.

## B1 — Phase 0 gate (stabilize + boundary)
Tasks/AC: reframed config (B0/CFG); decoupling guard (A19.2) fails on injected `import core`; clean
temp-project import works; DOD-1 stub-grep guard exists; full unit suite green
(`./test.sh unit -- --cov-fail-under=0`).

## B2 — Phase 1 gate (capability runtime + plans)
A1.*, A2.*, A3.1, A4.1, A9.1-9.2, A10.1, A14.1, A16.1-16.2, A17.1 implemented + toy-proven, product-
neutral (UNI-1, DOD-11); Anki registers planners + media as capabilities with **no card-behavior
change** (parity slice). Gate: all Phase-1 A-* proofs green + suite green.

## B3 — Phase 2 gate (evaluator/retrace)
A5.* implemented + DOD-5 toys; Anki quality eval routed through generic `EvaluationDecision`
(retry-card-plan / retry-scenario / repair-render / fallback) with no behavior change.

## B4 — Phase 3 gate (agent/subprocess)  ·  B5 — Phase 4 gate (fan-out)
A6.* (DOD-3) and A7.1 (DOD-4) + A3.2 subscription mode + A11.1 tool side-effect gate, all toy-proven.

## B6 — Phase 5 gate (framework completeness bar — the universality proof)
- All 11 toy scenarios pass with **no anki/mageqa imports**: `toy_llm_retry`, `toy_retrace`,
  `toy_subworkflow`, `toy_external_agent_timeout`, `toy_fanout_partial`, `toy_media`,
  `toy_profile_compile`, `toy_evidence_strategy`, `toy_clarification`, `toy_fail_closed`,
  `toy_scheduler_drop`.
- **+ `toy_scheduler_modes`**: fan_out_gather, live_latest_only/drop_not_queue, replay_process_all,
  single-flight cancel-not-release, coalesce, manual-priority (A8.*).
- **+ `toy_calendar_build`** (UNI-2) and **CMP-1 copy test** + **UNI-3 same-runtime** all green.
- Package imports from a clean temp project; README has examples for capability registration,
  subworkflow, evaluator/retrace, agent, media, scheduling, clarification, adapter.
- A8.5 sidecar boundary toy green. **This gate is the "engine is universal + complete" line.**

## B7 — Per-capability uniform AC (the 21 Anki capabilities)
For each (`source_assembly_evidence`, `directive_profile_compile`, `input_preflight_fail_mode`,
`ask_user_correction`, `image_asset_plan`, `card_set_plan`, `text_scenario_plan`, `cloze_scenario_plan`,
`visual_scenario_plan`, `scenario_validation`, `image_generation`, `voice_generation`, `text_cloze_render`,
`visual_render`, `rendered_validation`, `fallback_text_render`, `fallback_validation`, `quality_evaluation`,
`repair_guidance_prepare`, `package_delivery_marker`, `buffer_delivery_ops`): input schema validates ·
output schema validates · exactly one trace event · usage delta (deterministic = zero) · artifacts
owned/cleanup-tagged · every declared failure routes to declared fallback/fail with no bypass.

## B8 — Phase 6 gate (Anki migration — D1 milestone)
- **Parity** (old `AnkiGenerationGraph` vs new runtime, identical fakes, equal cards/media-roles/
  fallback flags+reasons/usage-presence/buffer state) for: plain `/anki` basic; `[i cloze]`/`[i cloze3]`;
  `[i visual gen]` gen-disabled→fallback; uploaded image reuse front/back; reference image;
  `[i langvoice src->pt gen]`; quality reject→retry-card-plan; buffer export/undo/clear.
- **Keep old graph until parity green, then delete (not before).**
- AC-01..AC-24 re-run green post-migration.
- Live `$5` verification (B14) passes.

## B9 — Phase 7 (MageQA handoff + skeleton)
Verify each mapping against the real repo and ship a runnable skeleton for ≥1 scenario:
`Coordinator.plan/review/curate`→agent capabilities (`17:79-83`); `Executor.run`→bounded agent w/ scoped
browser MCP (`17:85-88`); `ScenarioSpec`→profile unit (`17:208-211`); `Decision{stop|deepen|redispatch}`→
A5.2 deepen loop; `Observation+evidence[]`→A9; `Budget/Safety`→A6.1/A12.2; `ProcessingTrace`→A14
`TraceSink`; `WorkerBoundary`→A6 agent; subscription accounting→A3.2; grounding gate→A9.3; deterministic
hooks→A5.3. AC: skeleton runs one scenario end-to-end on fakes; no "assumed" mapping. Current package
proof: `run_toy_site_audit_pilot()` and
`test_toy_site_audit_pilot_fans_out_adjudicates_and_writes_report`.

## B10 — Phase 8 (GoPro handoff + live toy)
Verify + toy-prove: `UniversalProfileSheet`/`WorkflowProfile`→A1.2; `DescriptorRuntimePlan`→A1.2 plan;
evidence roles→A9.1; scheduling modes→A8.*; `AgentEpisodeRunner` sidecar→A8.5; `ExternalAdapterCapability`→A18;
fail-closed→A12.1; uncertainty output→A17.1; raw-bytes-out-of-state→A9.2; profile no-op detection→A1.3.
Current package proof: `run_toy_inventory_pilot()` and
`test_toy_inventory_pilot_uses_evidence_refs_scheduler_and_clarification`.

## B11 — Corner-case matrix (all tier-1 default/free)
help/empty preflight short-circuit (no paid calls); empty→fail-closed; unknown-directive warn+drop;
conflicting `[i i- gen]`→deny-media-wins; image-fail→text-fallback; ref-temp-cleanup-on-interrupt;
voice fail→note; voice disabled/cap/empty→distinct notes; quality retry-scenario→accept; quality
retry-card-plan→accept; quality reject→fallback+artifact-clean; recursion-cap→fallback (no
`GraphRecursionError`); budget-exhaustion→no paid retry; fan-out 1-of-N timeout→partials; ask_user
override flips branch; fallback-validation failure→declared fail (no loop); text/cloze=LLM vs
visual=deterministic; package marker emits trace only; buffer export/undo/clear parity;
scheduler drop/coalesce/single-flight-cancel; profile no-op warns; tool denial traced; fail-closed
not downgraded; evidence-grounding drop.

## B12 — Test tiers, budget, fakes
- **Default `./test.sh` (FREE):** all unit + golden-fixture + parity + toy tests; mocked LLM + Fake
  providers; **zero live I/O**; selected count should grow with new toys while staying **≤5 min**.
- **Gated live (`integration`+`api`), exactly 3 smokes** + the B14 run; excluded from default.
- **Fakes:** `FakeImageGenerator`/`FakeVoiceGenerator`/`FakeAgentCapability`/`FakeStructuredLLM`
  (canned JSON by `(capability, fixture_id, attempt)`, **refuses unknown fixture_id so a default test
  can never hit a live provider**); simulate timeout/partial/failure/budget-exhaustion deterministically.
  Inject at container/runtime construction boundary, not by monkeypatching internals. Fixture media =
  tiny pre-baked bytes.

## B13 — Observability AC
- Every node emits a structured log + trace event with workflow_id, type, user_id, node, attempt,
  branch decision, validation error, elapsed_ms, artifacts, cost. Proof: trace test. (Existing graph
  already does this; keep through migration.)

## B14 — LIVE VERIFICATION PROTOCOL — $5 HARD CAP (overnight autonomy grant)
- **Prove the cap aborts first:** a fixture drives estimated spend past $5 and the run stops with no
  further paid call. Do not start the live run until this is green.
  Current proof:
  `tests/unit/test_workflow_engine.py::test_workflow_runner_aborts_after_estimated_usd_cap_before_second_paid_call`.
- **Pre-conditions:** full default suite green; DOD + Part-A/B gates for the implemented phases green.
- **Scope (each ONCE, graph/capability-runtime level — no Telegram):** 1 basic text card, 1 cloze, 1
  auto classify (real LLM); 1 `[i visual gen]` (real `gpt-image-2`, ONE image); 1 `[i langvoice]`
  (real voice, ONE clip). Estimated << $5.
- **Assert:** real valid cards/cloze/visual/audio produced; trace + `WorkflowUsageSummary` populated;
  total estimated USD recorded and **< $5**.
- **Record** per-call model/tokens/USD + total into `test-results/`.
- **Abort if** any default test is red or the cap is reached. Use the **test bot token**, never prod.
  Telegram UI e2e stays a manual step for Artem.

## B15 — Security / secrets AC
- Secrets and deployment/runtime knobs are the only direct env reads in app config; non-secret
  workflow defaults come from committed YAML/profile files and can be overridden through the explicit
  env override layer. Missing active required/provider-selected secret → loud error (never
  defaulted). No secret value in any committed config/yaml/test fixture (guard). Product-local
  transition aliases such as `ANKI_*` must be documented/approved with a sunset and must not become
  package API. (CFG-1, CFG-4, CFG-5, CFG-8.)

## B16 — Known issues to fix
- Engine `image_generation.py`/`voice_generation.py` still expose concrete provider adapter classes
  in the package namespace. The SDK boundary is import-safe now (provider SDK imports are lazy or
  optional), but before external publication this should be split or re-exported as a clean
  `[media]` capability pack per D2.

---

## Appendix — biggest insufficiency risks (watch during impl)
1. **Scheduling spectrum (A8).** MageQA needs parallel fan-out (collapse-to-slowest); GoPro live needs
   the opposite (single-flight, latest-only, drop-not-queue, cancel-not-release, coalesce). One
   policy-selectable scheduling layer must serve both, or the engine is insufficient for one workload.
   (`17:126`, `req:264-281`, `sheet:359-374`.)
2. **Subscription/flat-rate execution + per-role models (A3).** A per-call-API-billing assumption breaks
   MageQA's ~$0 cost model. (`17:133,255-259`.)
3. **Grounding gate + evidence roles + raw-bytes-out-of-state (A9).** Treating evidence as optional
   metadata drops MageQA findings and bloats GoPro state. (`17:238`, `req:184-194`.)
4. **Profile no-op detection (A1.3) + no-silent-fallback (DOD-12).** An accepted profile must never
   silently do nothing. (`sheet:107-134`, `req:340-342`.)
5. **Sidecar/hot-path boundary (A8.5).** A graph engine that owns the live hot path is rejected by GoPro.
   (`sheet:262-284`.)
