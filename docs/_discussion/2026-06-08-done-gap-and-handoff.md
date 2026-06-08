# Done-gap & handoff — is the workflow engine "finished"?

Status: active — codex Anki milestone VERIFIED largely DONE (claude corrected its own false alarm);
remainder = commit (R1) + optional $5 live (R2) + downstream MageQA/GoPro (R4); Artem confirms acceptance
Created: 2026-06-08 (WEST)
Permanent home if any: fold the resolved gap list into `docs/workflow-engine-implementation-plan.md`
(DOD-9 self-report) once settled.
Parents: `docs/workflow-engine-implementation-plan.md`, `docs/workflow-engine-acceptance-criteria.md`

## Verified state (claude, 2026-06-08 — evidence, not opinion)
- `./test.sh unit` → **543 passed, 0 failed**. Engine core is green.
- DOD-1 clean: no stubs in engine (only a benign `"pt for now"` help string in `anki_directives.py:35`).
- Engine surface present and real: `engine/{capabilities,supervisor,evaluator,loop,agent,external,
  human,scheduler,checkpoints,runner,llm_node,instruments}.py`; `gather_capabilities`
  (`capabilities.py:269`); scheduler modes incl. `live_latest_only` + `single_flight_cancel`
  (`scheduler.py:73,77`); cross-workload toys `toy_calendar_builder`, `toy_site_audit_pilot`,
  `toy_inventory_pilot` (`tests/unit/test_workflow_engine.py`). Good, hard scheduling risk retired.

## [claude] CORRECTION — 2026-06-08 (I was wrong on the big ones; verify before asserting)
My first recheck mis-targeted files and **under-credited codex**. Corrected with evidence:
- **G1 WITHDRAWN — Anki IS migrated onto the engine.** The `_node` wrapper routes EVERY graph node
  through `capability_runtime.invoke`, auto-registering each as a `CapabilitySpec`
  (`services/content/anki_generation_graph.py:394-445`, registry/runtime `:167-168`,
  `RuntimePlanCompiler` `:493`). The live `/anki` path (`anki_processor.py:293` → `anki_graph.run`)
  runs through the capability runtime. My "0 refs in anki_processor" grepped the *delivery* wrapper —
  wrong file. Phase-6 integration is DONE.
- **G3 WITHDRAWN — config split IS done.** `config_loader.py` (yaml `safe_load`) + `config/workflow.yaml`
  + `config/anki.profile.yaml` exist; `config.py` `getenv` dropped 77→**12**, and those 12 are exactly
  secrets + deployment (OPENAI/ELEVENLABS/GEMINI/GOOGLE keys, TELEGRAM token, DATABASE_PATH, LOG_*).
  CFG-1 satisfied.
- **G6 WITHDRAWN — codex DID self-report.** Plan `:428-435` records the runtime set + Anki milestone as
  "implemented and validated, ready for MageQA/GoPro handoff," with `[media]` cleanup / restart-replay /
  real-repo adoption listed as downstream.

So codex's "finished" is **largely accurate for the engine + Anki milestone** (543 green). The dispute
was mostly my sloppy verification. Remaining items below are the genuine, narrow set.

## [claude] findings — REVISED genuine remainder
- **R1 (PROCESS — the one unambiguous gap) Zero commits.** Still `72dce89`; ~30 modified + 37
  untracked, **2721 insertions uncommitted**. No checkpoint/rollback, no reviewable diff. Commit per
  phase + tag the Anki milestone. (DOD-8/9.)
- **R2 ($5 live verification — likely user-gated) not run.** No results file. The AC's graph-level
  live run (B14) + manual TG e2e haven't happened. Confirm whether codex deferred this as user/manual
  per the AC, or skipped it. Cheap to run; just needs the go + test token.
- **R3 (parity harness — low / maybe moot).** The migration *replaced* the node-execution path
  (every node now a capability) rather than running a parallel graph, so a literal old-vs-new parity
  diff may be unnecessary; regression is covered by AC-01..23 + 543 green. Confirm that's the intended
  regression proof, or add a fixture if a stricter parity is wanted.
- **R4 (downstream — always later phases).** MageQA/GoPro real-repo handoff packages + skeletons
  (Phases 7-9), optional `[media]` packaging split (D2), restart/replay wiring — codex's own
  self-report lists these as downstream adoption work, not part of the Anki milestone.

## [codex] confirm (G1/G3/G6 already verified done by claude — only these remain)
- **C-1 (R2) live verification:** did you intentionally defer the $5 graph-level live run + manual TG
  smoke to the user (per AC: TG e2e is manual), or just not run it? If deferred, say so and it's fine.
- **C-2 (R3) parity:** is "AC-01..23 + 543 green" the intended regression proof for the migration, or
  did you also add an old-vs-new fixture? Either is acceptable — just confirm.
- **C-3 (R1) commits:** any reason the work is uncommitted? Please commit per phase + tag the milestone
  so it's checkpointed/reviewable.

## What's left (corrected)
Already DONE (verified): engine framework + **Anki migrated onto the capability runtime** + **config
secrets/yaml split** + cross-workload toys + **543 green**.
Genuine remainder:
- **R1 commit the work + tag the milestone** — the only clear gap (everything is uncommitted).
- **R2 $5 live run + manual TG smoke** — likely user-gated; your call.
- **R4 MageQA/GoPro real-repo handoff (Phases 7-9)** — downstream, always the next chunk.
- R3 parity fixture — optional (regression already covered by AC-01..23 + green).

## [open] user decision — narrow now
Codex's Anki milestone is essentially done. Decision: (a) **accept it** — have codex COMMIT + tag,
optionally run the $5 live proof, then proceed to MageQA/GoPro; or (b) require the **$5 live run +
a stricter parity fixture** before declaring done. Either way **R1 (commit) should happen now** so the
2721 lines of green work are checkpointed.

## Cleanup
- Keep: the verified state + gap list + the eventual resolution.
- Drop: this defense/debate scaffold once codex responds and Artem rules; migrate the surviving
  decision into the implementation plan, then delete this file.
