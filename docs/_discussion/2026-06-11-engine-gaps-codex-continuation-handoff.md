# Engine gaps G1–G7 — codex continuation handoff (G3 in-flight → G7 → G5b → G6)

Status: handoff to codex — claude will RECHECK every gate against §6 before acceptance
Created: 2026-06-11
Author: claude (engine developer so far; verifier from here)
Branch: `polish/workflow-engine` (NO pushes — local commits only)

---

## 1. What this is

GoPro requested 7 engine gaps (G1–G7) via
`gopro-streaming/docs/_discussion/2026-06-10-engine-gaps-handoff.md`. The **approved
implementation plan** (reviewed by gopro-claude: APPROVED with required changes RC1+RC2 and note
N1, all questions answered) is:

> `docs/_discussion/2026-06-10-engine-gaps-implementation-plan.md` ← **read it in full first.**
> Its §1b task table (≤4h tasks, commit at every green gate) is the work order. RC1/RC2/N1 are at
> the top of that file; the per-gap API sketches + test lists in §2–§8 are binding.

You are continuing a partially-done implementation. Do not redesign what is landed; do not
re-litigate the plan (gopro already approved it); push back via `[codex]:` notes in the plan doc
only if you find a real defect.

## 2. Current state — verified 2026-06-11

**Landed (committed, green, DONE — do not touch):**

| Commit | Gap | Content |
|---|---|---|
| `7f3a4d7` | G5a | package-owned test suite: `packages/ai_workflow_engine/tests/` (`test_engine.py` = 96 moved tests + `conftest.py`), pyproject `[tool.pytest.ini_options]` + `test` extras, `./test.sh` collects the package suite by default |
| `a99d8f4` | G2 | `ai_workflow_engine/parsing.py` (strip_think_tags / extract_fenced_json / string-aware extract_first_json_object / compose_cleaners / WEAK_MODEL_CLEANER) + `StructuredLLMNode(pre_parse=…, max_repair_rounds=…)`; 19 tests in `tests/test_parsing.py` |
| `8860178` | G1 | `ai_workflow_engine/vision.py` (`ImageInput` transport payload + `fingerprint()` + `from_evidence` bridge; `StructuredVisionLLMNode` re-sends images on repair); checkpoint guard rejects ImageInput; 8 tests in `tests/test_vision.py`; README section |
| `68b5433` | G4 | `ai_workflow_engine/llm_protocol.py` (`LLMRequest/LLMResponse/LLMCallable`, `record_callable_usage` with **RC2 cost integrity**: callable cost → price-table fallback → `cost_known=false`, never phantom $0); plain-callable async path in `StructuredLLMNode`; 9 tests in `tests/test_llm_protocol.py` (zero langchain imports); README "bring your own LLM client" |

Package suite at G4 gate: **132 passed**. Full repo suite at last full run: 581 passed.

**In flight (G3 — code FULLY WRITTEN, NOT yet run, NOT committed).** Uncommitted files:

- `ai_workflow_engine/model_binding.py` (new): `model_profile_scope(profile)` ContextVar +
  `current_model_profile()` — mirrors the `usage.py` scope pattern.
- `workflow.py`: `WorkflowNode.model_profile: Optional[str]`; builder params `model_profile=` on
  `.step()`, `.branch()`, `.evaluate()`.
- `models.py`: `CapabilityContext.model_profile: Optional[ModelProfile]` (additive).
- `executor.py`: `WorkflowExecutor.model_profiles` dict; `_invoke_bound(node, capability, payload,
  context, state, attempt=)` — resolves the profile, wraps the invoke in `model_profile_scope`,
  sets `context.model_profile` (model_copy), emits a `decision="model_binding"` trace event with
  `model_profile_requested` + `model_used` (model_used = last new usage-event model, diffed via
  `state["usage_summary"]` length before/after); step (both scheduled + retry paths), branch
  decider, and evaluate evaluator all route through `_invoke_bound`; `_preflight` →
  `_model_profile_error(node)`: unknown profile name = loud error; RC1 static check via handler
  attr `accepts_model_profile is False`.
- `builder.py`: `WorkflowEngine.__init__` sets `self.model_profiles` ONCE and shares the same dict
  with `executor.model_profiles` (a duplicate later assignment was removed — do not reintroduce);
  `register_workflow` raises ValueError listing node→profile for unknown names (registration-time
  loudness for AC-G3).
- `engine/llm_node.py`: `accepts_model_profile` property (False only for fixed LangChain `llm=`;
  True for factory or plain callable); `_llm_for_profile(profile)` factory cache per model name;
  `run()` reads `current_model_profile()` and applies **RC1 call-time backstop** (fixed client +
  profile → loud ValueError, never silent client-wins); langchain path uses profile model for the
  metered call; callable path puts `profile.model_dump()` on `LLMRequest.metadata["model_profile"]`.
- `__init__.py`: exports `current_model_profile`, `model_profile_scope` (plus earlier G1/G2/G4
  exports already committed).
- `tests/test_model_binding.py` (new, NOT yet executed): 8 tests — two-profile AC with trace
  proof, default-behavior regression, unknown-name at registration AND at preflight (ad-hoc
  definition), RC1 static preflight rejection, RC1 call-time backstop, callable receives profile
  on request metadata.

Import sanity already verified (`from ai_workflow_engine import model_profile_scope, …` works).

Also present, intentionally uncommitted: `docs/_discussion/2026-06-10-engine-gaps-implementation-plan.md`
(the plan — transient workspace, never commit), `scripts/` (pre-existing, untracked, leave alone).

## 3. Your work order

**Step 1 — finish G3 (gate E):**
1. Run: `./test.sh unit -- packages/ai_workflow_engine/tests/test_model_binding.py --tb=short --cov-fail-under=0 -q`
2. Fix whatever fails (the code is believed complete; likely issues if any: trace-event ordering
   in `_invoke_bound` vs node record, or pydantic copy semantics of `context.model_copy`).
3. Then the whole package suite: `./test.sh unit -- packages/ai_workflow_engine/tests/ --tb=short --cov-fail-under=0 -q`
   (expect 140 = 132 + 8) and the FULL suite `./test.sh unit -- --cov-fail-under=0 -q`
   (expect 589 passed; Anki must stay green — invariant 3).
4. Commit gate E (message style: see `git log` for the G1/G2/G4 commits; tag line
   "feat(engine): G3 — declarative per-node model binding (incl. RC1 loud fixed-client conflict)";
   include `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` replaced by your own
   attribution if your tooling differs).

**Step 2 — G7 (tasks F1+F2, plan §7):** executor cancellation wiring + integration tests.
Key facts you need:
- `executor.py` scheduled path (in `_build_step_node`) currently handles only `drop`/`coalesce`
  decisions; a `cancel_previous` decision from `WorkflowScheduler.submit` (mode
  `single_flight_cancel`) **falls through to immediate invocation** — that's the bug-shaped gap.
- Build: per-lane registry of the running worker task (`dict[lane, asyncio.Task]` on the
  executor); on `cancel_previous`: `.cancel()` the previous task, **await its real completion**
  (its `finally: scheduler.complete(...)` frees the slot — never free early), then run the new
  request (scheduler queues it as latest; promote path). Cancelled run's node records
  `failed` + `error="cancelled: superseded by <run_id>"` + trace `schedule:cancel_previous`.
- Tests (package suite, `tests/test_scheduling_integration.py`): the four in plan §7 — incl.
  PORTING (not rewriting) `test_backend_slot_held_until_worker_completes_blocks_concurrent_call`
  out of `tests/test_engine.py` into the new module.
- Commit gate F.

**Step 3 — G5b (plan §2):** `packages/ai_workflow_engine/scripts/standalone_test.sh`
(fresh venv → `pip install -e ".[test]"` → `pytest tests/`), a `test_no_host_repo_imports` guard
(assert no `config`/`services`/`handlers` modules in `sys.modules` after importing the package),
README note. **Run the standalone script once and keep its output** — Artem approved venv usage
for exactly this (dual-track decision recorded in plan §9). Commit gate G5b.

**Step 4 — G6 (tasks H1–H4, plan §8):** PlannerNode + plan-as-artifact. Follow the §8 sketch
exactly; constraints that are non-negotiable:
- validation BEFORE any task executes (registered + side-effect-allow-listed + not-a-planner);
  loud abort listing every offending task; `max_tasks`; depth 1; `max_replans` via a new
  `Replan()` directive (sibling of Retry/Retrace in `workflow.py`) handled in the evaluate node's
  `_eval_decision`/`_apply_eval` path, retracing to the planner node, revising pending tasks only;
- **N1 (gopro note): plan context re-injection is OPT-IN per node** — add
  `inject_plan: bool = False` to `WorkflowNode` + builder params; when True the executor exposes
  `render_plan(plan)` via `context.metadata["plan"]` for that node only. Document in README.
- plan artifact lives in state under `plan_artifact` + `node_outputs`; pydantic-only (checkpoint-
  safe); every task status mutation = one `WorkflowTraceEvent` (`plan:task_started/done/failed`);
  partial-failure isolation like fanout; budget shared with the run.
- New modules: `ai_workflow_engine/planning.py` (PlanTask/PlanArtifact/render_plan), executor
  handler for `kind="planner"`, `WorkflowBuilder.plan(...)`; `KNOWN_NODE_KINDS` += "planner";
  tests `tests/test_planning.py` per plan §8 AC list (incl. checkpoint/resume mid-plan and the
  non-allow-listed-task loud abort). Commits at gates H1–H4 (or H1+H2 / H3+H4 if natural).

**Step 5 — wrap-up:** update the plan doc Status line (gaps done), update
`packages/ai_workflow_engine/README.md` status block (G3/G7/G6 lines), run the FULL suite one
final time, and write your completion summary as `[codex]:` section at the bottom of the plan doc
(per-gate: commit hash, test counts, deviations if any).

## 4. Hard rules (violations = rejected at recheck)

1. **Tests: docker only** — `./test.sh unit -- <paths> --cov-fail-under=0 -q`. NEVER bare pytest
   (sole exception: inside `standalone_test.sh`'s venv, which exists for that purpose).
   `-k "a or b"` breaks (`test.sh` word-splits) — pass file paths or single `-k` tokens.
2. **Commit at every green gate**, on `polish/workflow-engine`, **never push**. Never commit
   `docs/_discussion/*` or `scripts/`.
3. **Anki stays green** — the full repo suite after every gate; any product-code change outside
   `packages/ai_workflow_engine/` needs a written justification in your summary (expected: none).
4. **Fail loudly; no silent downgrade** (RC1 pattern is the reference). No stubs, no
   `NotImplementedError`, no "simplified for now" — the package has a guard test that greps for
   stub markers and will fail your commit.
5. Public API stays product-neutral (guard test exists); LangGraph/LangChain types never leak
   into public API/persisted schemas/traces (invariant 1); no raw media bytes in traces or
   checkpoints (invariant 5; ImageInput guard is the reference).
6. Keep estimates honest: if something in the plan turns out wrong, write the deviation down in
   your summary — do not silently re-scope.

## 5. Test commands cheat-sheet

```bash
# one module
./test.sh unit -- packages/ai_workflow_engine/tests/test_model_binding.py --tb=short --cov-fail-under=0 -q
# package suite
./test.sh unit -- packages/ai_workflow_engine/tests/ --tb=short --cov-fail-under=0 -q
# full repo (Anki regression; expect "N passed, 143 deselected")
./test.sh unit -- --cov-fail-under=0 -q
```

## 6. Recheck protocol (what claude will verify before acceptance)

1. `git log` shows one commit per gate (E, F, G5b, H*) with accurate messages; nothing pushed;
   no `_discussion`/`scripts` files committed.
2. Full suite green at HEAD (I will re-run it myself) + package suite count matches your summary.
3. AC spot-checks against the plan: AC-G3 trace proof; AC-G7 cancel test actually awaits real
   worker exit (no sleep-based fakery — I will read the test); G5b script runs in a clean venv on
   my machine; AC-G6 e2e covers failure + bounded replan + resume, and the allow-list abort
   happens BEFORE any task executes (I will read the handler).
4. RC1/RC2/N1 implemented as specified (N1: injection strictly opt-in).
5. No silent scope reductions vs plan §1b; deviations documented.

Anything failing recheck goes back with a `[claude]:` findings list; fix-forward, same rules.

---

## 7. [claude]: mid-flight review — 2026-06-11 (G3 gate + in-progress G7)

**Gate E (G3, `19f705e`): ACCEPTED.** Landed exactly per the written design (RC1 both layers,
registration + preflight loudness, shared model_profiles dict, callable metadata propagation).
No architecture drift.

**G7 in-flight: direction APPROVED — the design is right.** `SchedulingDecision.previous_run_id`
(decision carries data; executor never pokes scheduler internals), per-lane task registry,
cancel → await real exit → `finally: scheduler.complete` (slot freed only at true completion) →
promotion sanity-check, loud "superseded"/"cancelled: superseded by <id>" node failures, rich
trace (`schedule:cancel_request/cancelled/promote`). The post-hoc `_scheduled_cancellations`
marker (kills the result of a worker that swallows CancelledError and returns normally) is a
genuine improvement over the plan. Keep going. Three findings to fold in before the gate F commit:

1. **BLOCKER (your own suite catches it):** `test_engine_package_has_no_shipped_stub_markers`
   fails on `executor.py:593 contains pass-only body` — the
   `except asyncio.CancelledError: pass` in `_cancel_previous_scheduled_run`. Do NOT weaken the
   guard. Fix the except body to discriminate instead:
   ```python
   try:
       await task
   except asyncio.CancelledError:
       if not task.cancelled():
           raise  # OUR task was cancelled while awaiting; propagate our own cancellation
       previous_exited_cancelled = True  # expected: previous task ended cancelled; we only
                                         # needed its real exit before promotion
   ```
2. **LATENT BUG (same line, fixed by the same change):** the bare swallow also eats the *current*
   run's own cancellation if it is cancelled while awaiting the previous task (e.g. a third
   request supersedes it mid-promotion). `task.cancelled()` discrimination closes it.
3. **NIT (defensive cleanup):** if the previous task has already left
   `_invoke_scheduled_bound` when you set `_scheduled_cancellations[(lane, prev)]`, the marker is
   never popped (tiny dict leak per rare race). After `await task` in
   `_cancel_previous_scheduled_run`, pop the marker as cleanup.

Remaining F-scope unchanged: `tests/test_scheduling_integration.py` with the 4 plan-§7 tests,
**porting** (moving, not copying) `test_backend_slot_held_until_worker_completes_blocks_concurrent_call`
out of `test_engine.py`, then full package + repo suites green, then the gate F commit.

---

## 8. [claude]: DECISION — G7 threading contract (2026-06-12, answers codex's question)

**Ruling: the engine declares a single executor-loop submission contract. Multi-loop/threaded
callers are NOT supported in-engine; sidecars marshal onto the engine's loop.** Rationale: asyncio
itself forbids cross-loop `task.cancel()`/`await task`, so "support" would mean a lock-based
redesign of scheduler + task registry + cancellation bridging — for zero named consumers (GoPro
sidecar, MageQA batch, Anki bot are all single-loop). Per engine-spec §11 risk #4, complexity needs a
named consumer; revisit ONLY if one appears, as an explicit spec change.

Implement (small, ≤3h, part of the G7 gate):
1. **Loud guard:** `WorkflowExecutor` captures `asyncio.get_running_loop()` on its first `run()`;
   any later `run()` (or scheduled-lane submission) from a different loop raises
   `RuntimeError("This WorkflowExecutor is bound to one event loop; from other threads/loops use "
   "asyncio.run_coroutine_threadsafe(engine.run(...), engine_loop)")` — explicit recipe in the
   message, never silent shared-state corruption. (Process-restart note: the binding refreshes if
   the prior loop is closed — supports sequential asyncio.run() calls in tests/CLIs.)
2. **Docs:** package README + gopro-handoff get a 3-line "Threading contract" note with the
   `run_coroutine_threadsafe` one-liner (the GoPro sidecar pattern).
3. **Tests** (package suite): (a) second loop in a thread calling `engine.run` → the loud
   RuntimeError; (b) the marshaled `run_coroutine_threadsafe` pattern works end-to-end;
   (c) sequential `asyncio.run()` invocations still work (loop-rebind on closed loop).

The `448b750` cancel-path hardening stays as defense-in-depth — error paths must not wedge lanes
even under contract violations.
