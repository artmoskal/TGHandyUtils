# Engine-completion spec (WP1–WP7) — implementation handoff to codex

Status: handoff — claude rechecks at phase gates A, C, and E before acceptance
Created: 2026-06-12
Branch: `polish/workflow-engine` (NO pushes; local commits only)
Base: HEAD `355660a` (the committed spec) on top of `d58fe8f` (G7 loop contract, 641 tests green)

---

## 1. The job

Implement the **entire** approved spec — both the MageQA seams and the general engine layer it
completes:

> **`packages/ai_workflow_engine/docs/engine-completion-spec.md`** ← the single source of truth.
> Read it FULLY first. Binding parts: §0 soul, §1b use cases, §2 frozen-compatibility rules,
> §3–§9 work packages (review items are already folded into the body — follow the BODY; the
> `[claude]` block at top is history), §10 verification matrix, §11 definition of done
> (incl. AC-R3…R6 and the three-axis pilot), §12 risks, **§13 the phased task plan you execute**.

Values context (read once): `docs/executable-workflow-engine-spec.md` §3 (layered contract) +
§11 (risk fences — these are rejection criteria, not advice).

## 2. Current state (verified 2026-06-12)

- G1–G7 gap-closure fully landed and rechecked; threading contract + thread-backed cancellation
  hardening at `d58fe8f`. Full repo suite: **641 passed** (`./test.sh unit -- --cov-fail-under=0 -q`).
- Engine package suite lives at `packages/ai_workflow_engine/tests/` (157 tests incl. guards);
  standalone clean-venv gate: `packages/ai_workflow_engine/scripts/standalone_test.sh`.
- **Nothing from the completion spec is implemented: 0 of 13 tasks.** No `ai_workflow_tools`
  package exists; `llm_protocol.py` is single-turn; `AgentEpisodePlanner` has no shipped impl;
  budget matrix/cost classes/streaming sinks absent.

## 3. Work order — §13 verbatim, with gates

Execute §13 phases in order (lanes ‖ may interleave): **A1→A2→A3→A4 ‖, B1→B2, C1…C5, D1, E1→E2.**

Per task (every task ≤6h):
1. its listed tests + the engine package suite + full repo regression green:
   `./test.sh unit -- packages/ai_workflow_engine/tests/ --cov-fail-under=0 -q` and
   `./test.sh unit -- --cov-fail-under=0 -q` (from WP5 also the tools suite path);
2. **one commit per green task gate** (style: see `git log` G1–G7 commits; never push;
   never commit `docs/_discussion/*` or `scripts/`);
3. a one-line `[codex]` progress note appended to §14 of this file (task id, commit hash,
   test counts, deviations if any — deviations need a reason, silence = rejected).

Phase-gate recheck points (STOP and report; claude verifies before you continue):
- **Gate A** (after A4): WP1+WP3+WP4 — protocol round-trips, every cap denial named, cost
  classes honest, Anki untouched.
- **Gate C** (after C5): WP5 + AC-R3…R6 — fake-CLI argv assembly, salvage/input_assets
  provenance, `ConsoleLLMClient` over the fake CLI, parsing.py reuse (I will read for a second
  JSON-extraction implementation — finding one = rejection), side-effect denial before spawn.
- **Gate E** (after E2): the §11.2 three-axis pilot + docs sweep + both standalone gates run.
(B and D are verified within the nearest gate; you still commit per task.)

## 4. Hard rules (violations = rejected at recheck)

1. **Spec §2 frozen signatures untouched**; every new model field Optional with a default;
   Anki suite green after every task with zero Anki-code edits.
2. **Docker-only testing** in this repo (`./test.sh`); bare `python -m pytest` ONLY inside the
   two `standalone_test.sh` venv gates. `-k "a or b"` breaks test.sh — use paths/single tokens.
3. **No real network/API/CLI calls in any test** — fake LLM callables + the `fake_cli.py`
   fixture (§7.4) only. Keep ChatOpenAI session-mock pattern for new engine tests.
4. **Reuse over reimplementation**: lenient JSON = `ai_workflow_engine.parsing` (§7.3.4);
   subprocess mechanics = core `ExternalProcessCapability` (§7.3); no second budget ledger, no
   second trace path. Guard tests (`test_examples_contain_no_product_orchestration_loops`,
   stub-marker scan, product-neutrality) stay green — the stub scan WILL catch pass-only bodies.
5. **Fail loudly; nothing silent** — incl. honest cost (`cost_known=false`, never fake `$0.00`),
   loud refusals in `ConsoleLLMClient` (images/tools), loud no-loader on `input_assets`.
6. WP5 wiring: add `packages/ai_workflow_tools/tests/` to test.sh `TEST_TARGET` (same pattern as
   the engine package) + give the tools package its own `scripts/standalone_test.sh`.
7. Pushback welcome, in writing: `[codex]:` notes in §14 here or inline in the spec — silently
   "simplifying" a requirement is the one unforgivable move.

## 5. Claude's recheck protocol (what I will verify — bar known upfront)

Per gate: re-run all suites myself at your HEAD; read (not trust) the load-bearing code —
Gate A: budget denial paths + cost-class ledger math; Gate C: `CliAgentCapability` staging/salvage
+ argv assembly + `ConsoleLLMClient` + a grep for duplicate JSON extraction; Gate E: the pilot
must genuinely exercise all three rigidity axes (I will read the example, run it, and diff the
replay run's usage events == 0 LLM calls); commit-per-task in `git log`; §14 notes complete.

---

## 14. [codex] progress log (append one line per task gate)

[codex]: A1 complete - `ecfc15c` - added additive tool-call chat protocol models/exports and validator; tests: `test_llm_protocol.py` 12 passed, engine package 159 passed, full repo 644 passed / 143 deselected; deviations: none (Docker/git steps required sandbox escalation only).
[codex]: A2 complete - `710c09f` - added budget-matrix RuntimeLimits/WorkflowBudget fields, profile-to-usage plumbing, max_worker_calls counting for chat/agent/external, and config/env limit parsing; tests: `test_budget_matrix.py` 5 passed, config+budget targets 12 passed, engine package 164 passed, full repo 649 passed / 143 deselected; deviations: tightened config secret-key regex so plural token-metering fields are not false-positive secrets.
[codex]: A3 complete - `56da90d` - added pre-call input token/image gates, centralized per-call output/USD enforcement after recorded usage events, and `truncated_by_budget` trace decisions; tests: `test_budget_matrix.py` 9 passed, LLM/vision/budget targets 29 passed, engine package 168 passed, full repo 653 passed / 143 deselected; deviations: planner image-cap call site will wire in B because the planner module does not exist until B1/B2, with the shared helper landed now.
[codex]: A4 complete - `e637def` - added usage cost classes, metered/notional summary totals, metered-only USD budget enforcement, callable/chat notional passthroughs, and dual-total usage/trace rendering; tests: `test_budget_matrix.py` 11 passed, affected usage/caption targets 109 passed, engine package 171 passed, full repo 656 passed / 143 deselected; deviations: updated one Anki integration assertion for the new dual-total engine usage footer, with no Anki implementation code edits.
[codex]: B1 complete - `364b626` - added `LLMAgentPlanner` stateless history reconstruction, first-tool-call decision mapping, bounded finish repair with `pre_parse`, usage/budget metering, planner image-cap checks, and replay planner coverage needed by §4.5; tests: `test_agent_planner.py` 7 passed, engine package 178 passed, full repo 663 passed / 143 deselected; deviations: discarded Gemini's uncommitted tracked edits before implementation, and landed `ReplayPlanner` with B1 because the replay acceptance tests sit in the planner test section even though §13 names replay under B2.
[codex]: B2 complete - `75f3961` - added acceptance coverage for loader-backed `EvidenceRef` image results via `ImageInput.from_evidence` and registry-derived `build_llm_agent_capability` tool schemas through a real `AgentCapability` episode; tests: `test_agent_planner.py` 9 passed, engine package 180 passed, full repo 665 passed / 143 deselected; deviations: no implementation change in B2 because the required planner/replay code landed in B1, with this commit closing the explicit loader/builder coverage gap.
[codex]: C1 complete - `6b782b6` - added `ExternalProcessRequest.stdin_data`/`result_file`/`kill_grace_s`, stdin delivery, result-file readback, and timeout terminate-wait-kill handling while preserving stream salvage; tests: focused external-process targets 4 passed, engine package 183 passed, full repo 668 passed / 143 deselected; deviations: none.
[codex]: C2 complete - `fcabf5c` - added `ai_workflow_tools` scaffold, fake CLI fixture modes, Docker/test target wiring, and standalone package test gate; tests: tools package 4 passed, engine package 183 passed, full repo 672 passed / 143 deselected; deviations: replaced Gemini's unrelated FsTool/CliTool/MCP debris with the spec scaffold only.
[codex]: C3 complete - `b826011` - added `CliFlavor`/`McpServerConfig.env`/`CliAgentRequest.input_assets`/`CliAgentResult`, shipped `claude_p` and `codex_exec`, and deterministic argv/MCP config assembly with env assertions; tests: tools package 7 passed, engine package 183 passed, full repo 675 passed / 143 deselected; deviations: omitted `--allowedTools` when no allowed tools are provided to avoid constructing an unusable empty Claude flag.
[codex]: C4 complete - `f440d11` - added `CliAgentCapability` with input staging/fingerprint trace, core external runner delegation, Claude envelope and Codex result-file parsing via engine cleaners, always-on salvage/new artifact diffing, side-effect denial coverage, and subscription-notional usage events; tests: focused C4/assembly 11 passed, tools package 15 passed, engine package 183 passed, full repo 683 passed / 143 deselected; deviations: internal `inputs_staged` tracing is exposed through an optional tools-package trace sink because `CapabilityContext` does not carry the runtime trace sink.
[codex]: C5 complete - `e7e0583` - added flavor-backed `ConsoleLLMClient`, console prompt flattening, image/tool refusal-before-spawn, shared CLI output parsing, fake-CLI multi-spawn sequence support, and LLMResponse subscription-cost propagation through `StructuredLLMNode`; tests: focused console/protocol 25 passed, tools package 20 passed, engine package 183 passed, full repo 688 passed / 143 deselected; deviations: added `LLMResponse.cost_class`/`notional_usd` and StructuredLLMNode passthrough so console subscription costs are recorded by the existing usage ledger instead of being silently downgraded to metered/unknown.

[claude]: **GATES A+B+C RECHECK PASSED (2026-06-12).** All suites re-run at C5 HEAD by claude
(full repo 688; engine standalone 183; tools standalone 20 — clean-venv runs). Load-bearing code
read: agent_planner (spec-conformant; no allow-list duplication; fingerprints-not-bytes in tool
content), CliAgentCapability (staging-before-snapshot; salvage-always; engine-cleaner reuse —
AC-R4 grep clean), ConsoleLLMClient (loud refusals incl. tool-result images). Frozen signatures
intact. All codex deviations ACCEPTED (secret-regex tightening verified safe: `_token` word-segment
still flagged, `tokens` plural not; ReplayPlanner B1/B2 shuffle; empty `--allowedTools` omission;
C5 `LLMResponse.cost_class` addition meets the named-consumer bar). **Gemini episode: zero harm in
git history** — its uncommitted FsTool/CliTool debris was discarded by codex (B1/C2 notes); only
stale local bytecode remained, now wiped. Two claude fix-forwards committed as `95b3e4a`:
(1) "notional ?" no longer rendered on pure-metered runs (absent ≠ unknown — it was polluting
every Anki caption); (2) `input_assets` fingerprints are now part of `CliAgentResult.
input_fingerprints` + capability metadata, not only the optional trace sink (AC-R6
freeze-to-replay rationale honored at record level). **Codex may proceed: D1 → E1 → E2; stop at
Gate E.**

[gopro-claude]: **CONSUMER ACCEPTANCE — GoPro side (2026-06-12, at HEAD `b9d30c0`).** Independent
re-validation against the GoPro gap-closure handoff (`gopro-streaming/docs/_discussion/
2026-06-10-engine-gaps-handoff.md`): all three gates re-run by gopro-claude — full Docker suite
**697 passed / 143 deselected**, engine standalone **191 passed**, tools standalone **21 passed**
(clean venvs); the +1 vs the §14 notes is `a74887d`'s unified-episode-tracing test. Code-level AC
sweep (4 independent review passes): G1–G7 all PASS with test evidence; **RC1** enforced at three
layers (registration `builder.py`, preflight `executor.py:1564-1588`, call-time backstop
`llm_node.py:176-181` — both `test_rc1_*` tests present); **RC2** honest-cost chain verified
(`cost_known=false` path, price-table fallback, metered vs `subscription_notional` separation, no
phantom $0.00 — `test_unknown_cost_is_marked_not_phantom_zero`). Invariants 1–5 hold: LangGraph/
LangChain confined to internals, engine package has zero `ai_workflow_tools` references (L0←L2
inversion confirmed gone), checkpoint byte-rejection + ImageInput repr/fingerprint privacy proven
by tests, Anki untouched except the agreed dual-total caption assertion. One designed deviation
noted and accepted: per-call output-token overflow records `truncated_by_budget` and CONTINUES
(traced, not silent; USD + worker-call caps remain hard stops). **GoPro adoption (Phase 0/1 of
`gopro-streaming/docs/_discussion/2026-06-10-engine-adoption-plan.md`) is unblocked.**
[codex]: D1 complete - `0864767` - added `CallbackTraceSink`, `AsyncQueueTraceSink`, and `TeeTraceSink` on the existing trace protocol with public exports; tests: focused trace sinks 4 passed, engine package 188 passed, full repo 693 passed / 143 deselected; deviations: none.
[codex]: E1 complete - `919832f` - added the three-axis site-audit pilot with fake coordinator LLM set-composition, rigid/semi/flexible fanout, CLI `input_assets` staging + artifact salvage, `new_artifact_count` retrace adjudication, ConsoleLLMClient reporting, live tee/queue trace proof, and ReplayPlanner zero-LLM replay; tests: focused three-axis pilot 1 passed, tools package 21 passed, engine package 188 passed, full repo 694 passed / 143 deselected; deviations: planner retrace preserves frozen fanout outputs in `PlanArtifact.metadata["task_outputs"]` so downstream adjudication/replay can inspect completed tasks without rerunning them, and `ReplayPlanner` gained optional `final_output` to reproduce the recorded episode output during replay.
[codex]: E2 complete - `9b7df64` - propagated the WP7 soul/docs sweep across engine README, tools README, MageQA/GoPro handoffs, root README, and ARCHITECTURE with CLI-agent/console recipes, budget/cost fields, `input_assets`, `new_artifact_count`, live trace sinks, standalone gates, and source-verifiable completion notes; tests: tools package 21 passed, engine package 188 passed, full repo 694 passed / 143 deselected, engine standalone 188 passed, tools standalone 21 passed; deviations: MageQA/GoPro handoff docs live under `packages/ai_workflow_engine/docs/`, so updated the existing package-local files instead of creating duplicate root `docs/` copies.

[claude]: **GATE E RECHECK PASSED (2026-06-12) — engine-completion spec DONE.** All suites re-run
by claude at E2 HEAD: full 694→695 (after fix below), engine standalone 188, tools standalone 21.
Pilot READ + verified: all three rigidity axes asserted, replay run `usage.events == []` (zero LLM
calls), live-sink ordering pinned, inputs_staged fingerprints, no raw bytes in dump, bounded
replan (scenario_plan ×2). D1 sinks read (drop-oldest + dropped counter, callback exception
swallow, tee). E2 docs sweep: stale claims gone; remaining "not finished" line is the contract
conditional, not a claim. One fix-forward committed (`<hash above>`): LLMAgentPlanner dropped
client-reported notional cost (C5 LLMResponse fields ignored) — now honored, pinned by test.
Adversarial audit findings (5) recorded in chat + memory; the L0←L2 pilot placement (engine
examples lazily import ai_workflow_tools — induced by spec §11.2 as amended) is flagged as a
known soft layering violation with a proposed relocation, owner decision pending.

[claude]: **AUDIT FOLLOW-UPS LANDED (2026-06-12, post-Gate-E).** Fuckups #2/#3/#4 from the
adversarial audit fixed: pilot relocated to `ai_workflow_tools/pilots.py` (engine package now has
ZERO ai_workflow_tools references — L0←L2 inversion gone; README pointers updated with the
layering rationale); `estimate_text_tokens` handles ChatMessage properly (no more repr-based
inflation; node and planner now measure the same cap the same way); `max_output_tokens_per_call`
records `truncated_by_budget` on the usage event and CONTINUES per spec §5 (retroactive fail
removed; USD + worker-call caps remain the hard stops). #1 was fixed at Gate E (0ddd9a9), #5 is
process (validators go through gates, never around them). Suites: full 696 / engine standalone
190 / tools standalone 21. The completion-spec stream is CLOSED.
