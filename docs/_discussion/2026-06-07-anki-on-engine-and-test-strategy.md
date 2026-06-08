# Anki-on-Engine Generalization + Test/AC Strategy

Status: superseded coordination artifact; durable decisions live in
`docs/workflow-engine-implementation-plan.md`, `docs/workflow-engine-acceptance-criteria.md`, and
`docs/anki-acceptance-criteria.md`
Created: 2026-06-07
Permanent home if any: fold into `docs/workflow-engine-implementation-plan.md` (Phase 6) + AC doc
Parents: `docs/workflow-engine-architecture.md`, `docs/workflow-engine-implementation-plan.md`,
`docs/_discussion/2026-06-07-workflow-engine-extraction.md`

## Goal
Make Phase-6 Anki **maturely** exercise the engine — every primitive it can, in real (non-toy) use —
so Anki is a genuine framework validator, not a parallel hand-built graph next to an unused engine.
Plus a **cost-aware** test/AC strategy codex can implement that covers corner cases **without burning
money on live LLM/image calls**.

## Part A — Anki stage → registered Capability (which primitive each exercises)

| Anki stage | Capability kind | Engine primitive exercised |
| --- | --- | --- |
| directive parse + source prep | python | `WorkflowProfile` (`[i ...]`+content_mode) → `RuntimePlanCompiler`/`RuntimePlan`; unknown `[i]` key → warning |
| image-asset plan | python | `EvidenceRef` + roles for uploaded images/OCR/summary (route via refs, not loose bytes) |
| card-set plan | structured-LLM | decision gate (closed set) invoked via supervisor/registry |
| text/cloze/visual scenario planners | structured-LLM | `StructuredLLMCapability`, model profile selection |
| image gen | external side-effect | timeout/cost/artifact/salvage/cleanup + budget cap |
| voice gen | external side-effect | same, audio artifact |
| render (text/cloze/visual) | python/LLM | capability execution + trace |
| quality eval | evaluator | `EvaluationDecision`: accept/retry_capability/retrace_to/fallback |
| 5s correction window | clarification | now routed through `HumanClarificationCapability`; Telegram remains the concrete timer/button channel |
| package build | python | terminal capability + artifact ownership |

Cross-cutting: every call goes through the capability runtime → `TraceSink` + `WorkflowUsageSummary`
(trace/budget engine-owned), `ModelProfile` registry for per-stage models, bounded loops with an
explicit `recursion_limit`.

Deliberately exercised "extra" to widen coverage:
- **FailMode:** add a `fail_closed` path (empty/unsafe content blocks instead of silently
  degrading) so fail modes are exercised beyond quality-fallback.
- **Fan-out:** model multi-image/multi-card as `gather_capabilities` (serial is fine); optionally run
  parallel image generation to also exercise the concurrency race (1-of-N timeout).

Explicitly NOT exercised by Anki (the two irreducible axes — stay toy + MageQA/GoPro):
open-ended agentic loop; streaming/durable/resumable lifetime.

## Part B — "Mature, not toy" rule
The risk (F3): if `AnkiGenerationGraph` stays a hand-built LangGraph **and** a parallel capability
path exists, the engine is decorative. Phase 6 must make Anki run **through** the capability runtime
as the single path (adapter or clean replacement — open question for codex). All LLM/image/voice go
through metered capability wrappers + `TraceSink`. No bypass.

## Part C — Test/AC strategy (cost-aware)
Tiers (cost in parentheses):
1. **Unit — default `./test.sh` (FREE):** all logic/branches/corner cases with **mocked LLM +
   Fake providers**. Deterministic. This is where corner-case coverage lives.
2. **Golden-fixture/contract (FREE):** canned LLM/image JSON responses → assert capability output
   schema + trace shape + routing. No live API.
3. **Integration/api (GATED, paid, tiny):** 1–2 real samples behind existing `integration`+`api`
   markers, **excluded from the default unit run**, opt-in/CI-nightly only.
4. **Manual Telegram e2e (human):** Phase-6 exit smoke.

Fakes (deterministic, no cost): `FakeImageGenerator`/`FakeVoiceGenerator`/`FakeAgentCapability`
returning tiny fixtures and able to simulate timeout / partial-output / failure / budget-exhaustion.

Corner-case matrix (all in tier 1, mocked):
empty content; conflicting directives; unknown `[i]` key (profile warns); image-gen fail → fallback;
voice fail → note; quality reject → retry-scenario → accept; quality reject → retry-card-plan;
quality reject → fallback; recursion cap reached → fallback (no `GraphRecursionError`); budget
exhausted → fallback (assert **no** paid retry); fan-out 1-of-N fails → partials gathered; `ask_user`
override flips the branch; fail-closed on empty/unsafe; deck buffer export/undo/clear unchanged.

Cost guards: **zero live LLM/image calls in the default suite**; budget caps injected via config in
tests; an assertion that budget exhaustion never triggers another paid call.

Per-capability AC (uniform): input validates against schema; output validates; one trace event
emitted; usage recorded; each failure path routes to the declared fallback/fail.

## Open questions for codex
1. Adapter over `AnkiGenerationGraph` vs clean replacement onto the capability runtime — which is
   lower-risk and more "mature" (single path, no dead engine)?
2. Is the capability list in Part A complete? Missing seams or a wrong granularity?
3. Cheapest way to produce golden LLM/image fixtures — record-replay of one real run, or
   hand-authored canned responses? Which keeps the suite stable + free?
4. Corner cases missed in Part C?
5. Worth exercising real concurrency (parallel image gen) in Anki, or leave the race to MageQA toy?
6. Keep the default suite fast/free: target test count + runtime; anything that would force live
   calls into the default run?

## Cleanup
- Keep: the capability mapping (Part A), the mature-path rule (Part B), the test tiers + corner-case
  matrix + cost guards (Part C) → migrate into the implementation plan Phase 6 + AC doc.
- Drop: codex/claude debate chatter after agreement.

---

## Round 1 — codex + claude converged (2026-06-07)

**Framing correction (codex, agreed):** Part A reads as if the engine already provides
`CapabilityRuntime`/`EvidenceRef`/`EvaluationDecision`/`TraceSink`/fan-out — it does NOT
(`packages/ai_workflow_engine/ai_workflow_engine/engine/__init__.py:3`). Part A is the list of
capabilities/primitives we **will build**, then Anki uses them. Read it that way.

### Q-decisions (agreed)
- **Q1 clean replacement** (not adapter): lift the existing graph node *bodies*
  (`anki_generation_graph.py`) into capability handlers; move only wiring (edges/routers) onto the
  capability runtime. An adapter leaves the LangGraph topology as the real path = decorative engine.
- **Q3 hand-authored canned fixtures** (not record-replay): repo already has deterministic fake
  LLM/image/voice patterns (`tests/unit/test_anki_generation_graph.py:52,79`); branch-targeted,
  always free, never accidentally live. Record-replay adds cassette friction for no branch-coverage
  gain.
- **Q5 no real parallel image-gen in the default suite:** cover the fan-out race with an
  engine-level fake-`gather` toy (slow fake providers). Anki issues one image request today
  (`anki_generation_graph.py:1063,1110`); real concurrency tests infra, not Anki.
- **Q6 default-suite budget:** the free suite count is tracked in the permanent AC docs and should
  stay **≤5 min**
  while growing with new toys/parity cases. Guard: anything instantiating `OpenAIImageGenerator` or a
  real planner without a fully injected fake must be behind `integration`+`api` and excluded from the
  default run; fixture image bytes pre-baked, not generated at test time. (NOTE per Artem 2026-06-07: test *execution* cost is
  the budget to watch — keep live image-gen tests sufficient-but-minimal and gated; do NOT trade
  away coverage/quality. Strategizing depth/iterations are fine.)

### Part A — capabilities, corrected (Q2)
Add to the table:
- **source_assembly / evidence_normalization** — `anki_source.py` + graph pre-step is a distinct
  stage with its own error path (was missing).
- **fallback_validation gate** — separate branch that can raise independently
  (`anki_generation_graph.py:1075`); own capability, not folded into render.
- **delivery/package + buffer** — `.apkg` build + buffer-add live in `AnkiProcessor`
  (`anki_processor.py:318,324`), NOT the graph. `package_cards` is a **marker only** — my draft
  over-claimed it as a terminal graph capability. Phase 6 decides whether packaging/delivery becomes
  a real capability or stays in the processor.
- **split render** into (a) text/cloze **LLM** render (prompt+retry) vs (b) **deterministic** visual
  render (template/HTML) — different failure modes, different primitives.
- **ask_user correction (codex, agreed):** the 5s window is TODAY entirely outside graph execution
  (`services/content/auto_flow.py:42,84`); there is no clarification primitive. So it is NOT a
  currently-exercised capability. It IS a concrete instance of the pattern, and **Phase 6 must
  actually model it as the `ask_user` capability** to exercise the primitive — do not assume done.

### Part C — corner cases, added (Q4)
- help / empty preflight short-circuit before graph entry (`anki_processor.py:273,282`);
- fallback_validation failure (distinct from normal validation failure, `:1075`);
- reference-temp cleanup when image gen is interrupted (`:1148` finally-block);
- voice: disabled / cap-exceeded / empty-text are three branches, only one tested (`:1275`);
- **unknown-directive warning is a CODE GAP, not a test gap:** `parse_directives` has no
  unknown-flag warning today (`anki_directives.py:78,123`). To make `[i ...]`→profile compile warn
  on unknown keys (Phase 1 acceptance), we must ADD that branch, then test it.

### D3 phrasing fix (codex, agreed)
"trace/budget engine-owned" → engine owns the **event-emission contract**; `TraceSink` is swappable
(matches D3 "no single hard-owned store").

[agent-agreement]: 2026-06-07 — Round 1 converged on clean-replacement, hand-authored fixtures,
gated-live-tests, the corrected capability list, and the added corner cases. Open for Round 2:
finalize the implementation-ready per-capability AC + concrete test list + fakes/fixtures design.

---

## Round 2 — converged test/AC plan (ready to migrate; implementer = codex/TBD)

### Final Anki capability set (21)
`source_assembly_evidence`, `directive_profile_compile`, `input_preflight_fail_mode`,
`ask_user_correction`, `image_asset_plan`, `card_set_plan`, `text_scenario_plan`,
`cloze_scenario_plan`, `visual_scenario_plan`, `scenario_validation`, `image_generation`,
`voice_generation`, `text_cloze_render`, `visual_render`, `rendered_validation`,
`fallback_text_render`, `fallback_validation`, `quality_evaluation`, `repair_guidance_prepare`,
`package_delivery_marker`, `buffer_delivery_ops`.

### Uniform per-capability AC
input schema validates · output schema validates · exactly ONE trace event to the engine-owned
emission contract · `usage_delta` present (paid → metered; deterministic → zero-cost) · artifacts
owned/cleanup-tagged · every declared failure routes to fallback/fail with no bypass.
Per-capability extras: evidence-ref preservation (`source_assembly`); unknown/conflicting `[i]`
warning (`directive_profile_compile` — code branch to ADD, `anki_directives.py:78`); move 5s window
into runtime (`ask_user_correction`, `auto_flow.py:42`); caps+metadata+fallback
(`image_generation` `:1045`, `voice_generation` `:1134`); independent raise (`fallback_validation`
`:1275`).

### Test tiers + budget (default ≤5 min; current count lives in permanent AC docs)
- **Default/free:** all unit (mocked LLM + fake providers), 4 golden-fixture tests (canned JSON +
  tiny png/mp3 bytes), 8 Phase-6 parity tests (old graph vs new runtime, identical fakes, compare
  cards/media-roles/fallback flags/usage-presence/buffer state).
- **Gated (live, `integration`+`api`), exactly 3:** `test_phase6_live_text_planner_smoke`,
  `test_phase6_live_quality_evaluator_smoke`, `test_phase6_live_forced_generated_image_smoke`
  (+`image_generation` marker) — one image smoke only; auto-image breadth → fixtures/manual.
- **Manual (1 Telegram run, Phase-6 exit):** `/anki`, auto `ask_user` override, image reuse, forced
  gen image, language voice, fallback note, export/undo/clear.
- Cost guard: zero live I/O in default suite; budget-exhaustion test asserts no extra paid call;
  fixture media pre-baked, not generated at test time.

### Concrete test names (corner-case matrix → tests)
preflight help short-circuit; empty→fail-closed-no-calls; unknown-directive warn+drop; conflicting
`[i i- gen]`→deny-media-wins; image-fail→text-fallback; ref-temp-cleanup-on-interrupt; voice
fail→note; voice disabled/cap/empty→distinct notes; quality retry-scenario→accept; quality
retry-card-plan→accept; quality reject→fallback+artifact-clean; recursion-cap→fallback (no
`GraphRecursionError`); budget-exhaustion→no paid retry; engine `gather` 1-of-N timeout→partials;
ask_user override flips branch; fallback-validation failure→declared fail (no loop);
text/cloze=LLM vs visual=deterministic; package marker emits trace only; buffer export/undo/clear
parity. (Full table with assertions/tiers/fakes in the round-2 codex findings — migrate verbatim.)

### Fakes/fixtures
Inject at the container construction boundary (`core/container.py:97,126`) or build the runtime with
fake specs — not by monkeypatching provider internals. `FakeImageGenerator(mode)` /
`FakeVoiceGenerator(mode)` (success→tiny bytes; timeout/fail; partial; assert-no-call-under-budget;
pattern at `tests/unit/test_anki_generation_graph.py:52,79`); `FakeAgentCapability(mode,delay,partial)`
(engine-toy fan-out only); `FakeStructuredLLMCapability` serves canned JSON by
`(capability, fixture_id, attempt)`, invalid-then-repair, **refuses unknown fixture_id so a test
can never hit a live provider**. Fixture tree: `fixtures/{card_set_plan,scenario,quality_decision,
rendered_card_set,media}/`. `fixture_id` flows via `CapabilityContext.metadata`.

### Status
Design + test/AC strategy are **prepared for hand-off** (this doc). NOT executed further. Implementer
(codex/TBD per Artem) migrates this into `docs/workflow-engine-implementation-plan.md` Phase 6 + the
AC doc and builds it. Claude will not run Codex again without explicit instruction.
