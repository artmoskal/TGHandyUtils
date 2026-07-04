# Run durability plan — no silent loss, no double-spend

Status: implementation-ready design (owner approved scope 2026-07-04); codex challenge welcome inline
Scope: TGHandyUtils product layer only (zero engine changes)
Complexity verdict: B1 easy (~½ day), B2 medium (~1 day)

## Why we need this

An Anki run now legitimately takes 2–20+ minutes (sequential subscription backends,
throttle waits). The bot keeps the ENTIRE run in process memory: verified — zero
`MachineSnapshot`/`resume` usage anywhere in `services/` or `handlers_modular/`. A deploy,
crash, or Pi power blip mid-run therefore loses the run **silently**: the user's chat
shows "🃏 Still working…" forever, the spent subscription quota is gone, and a manual
retry re-spends everything. This violates two soul rules at the product seam: *fail-loud*
(silent loss) and *every wait resumable*.

## What we need

1. **B1 — loud loss (minimum honesty):** a restart can never leave a zombie status
   message; every interrupted run is visibly marked lost.
2. **B2 — recovery without double-spend:** lost runs re-dispatch from their recorded
   input; duplicate sends while a run is live attach instead of re-generating.

**Non-goal (do not oversell):** mid-node crash resume. The engine's snapshot/resume is
for *deliberate* suspensions (human gates), not arbitrary crash checkpoints. B2 is
re-run-from-input with delivery-once semantics, stated as exactly that.

## How

### Data
New table `workflow_runs` in the existing sqlite DB (via the `database/` layer):

| column | notes |
|---|---|
| `run_id` TEXT PK | the engine run id (already generated per run) |
| `user_id`, `chat_id`, `status_msg_id` | for TG status edits on sweep |
| `workflow_type` | `anki_generation` first; reminders later |
| `input_payload` TEXT | JSON of thread content/directives needed to re-run (B2) |
| `state` | `running` → `delivered` \| `failed` \| `lost` |
| `dedup_key` TEXT | sha256(user_id + normalized input) |
| `started_at`, `finished_at` | UTC ISO |

Privacy note: `input_payload` stores user content in the DB (same DB that already stores
task content). Retention: delete rows older than 14 days in the existing scheduler tick.

### B1 flow
- `anki_processor.process`: insert `running` row after the status message exists; update
  to `delivered`/`failed` in the existing finally/error paths.
- `main.py` startup (after bot init): sweep `state='running'` rows → try
  `edit_text("❌ This generation was interrupted by a bot restart — please resend.")`,
  mark `lost` regardless of edit success (message may be deleted; log and continue).

### B2 flow
- Startup sweep gains a mode knob `run_recovery: loud_only | redispatch` (default
  `loud_only` until B2 is proven): `redispatch` re-enqueues the stored `input_payload`
  through the normal processor path, editing the SAME status message ("resuming after
  restart…").
- Dedup: before starting any run, look up a live `running` row with the same `dedup_key`;
  if found, reply "already generating this — hang on" and attach (no second run).
  Delivery-once: terminal-state transition is guarded (`UPDATE … WHERE state='running'`);
  only the winner delivers.

### Ownership / failure modes
Product layer only (`database/`, `services/content/anki_processor.py`, `main.py`).
TG edit failures never block state transitions. DB write failure at run start → log
loudly, run proceeds (durability is best-effort, generation is the product).

## Phases & tasks

- [ ] B1.1 `workflow_runs` table + repository methods
  - AC: insert/transition/sweep methods unit-tested against a temp DB.
- [ ] B1.2 processor integration + startup sweep
  - AC: kill the bot mid-generation (manual scenario below) → after restart the status
    message reads the loud failure and the row is `lost`; normal runs end `delivered`.
- [ ] B2.1 dedup guard
  - AC: two identical sends within one live run → one generation, second gets the
    "already generating" reply (processor-flow test with FakeGraph slow-run).
- [ ] B2.2 re-dispatch mode
  - AC: with `run_recovery: redispatch`, the killed run's card arrives after restart,
    exactly once; `loud_only` behavior unchanged.

## Tests
- Unit: repository CRUD/transitions; dedup key normalization.
- Integration (existing `test_anki_processor_flow.py` harness): slow FakeGraph +
  simulated restart (new processor instance + sweep against same DB) → loud edit;
  dedup test; redispatch test.
- Manual: send `/anki` with an image card (long run), `docker compose restart bot`
  mid-run → expect the ❌ edit (B1) / delivered card (B2). Failure looks like: status
  message still says "Still working…" after restart.

## Open questions (codex challenge)
1. Duplicate handling: attach-with-reply (planned) vs silently ignore vs refuse — which
   is the least-surprise TG UX?
2. Is 14-day `input_payload` retention right, or should redispatchable inputs be dropped
   immediately after terminal state (privacy-lean)?
3. Sweep at startup only, or also a periodic stale-run check (running > 45 min ⇒ lost)?

## Definition of done
B1: no code path can leave a permanent "Still working…"; tier green; manual kill test
passes. B2: redispatch + dedup proven by the manual scenario; default stays `loud_only`
until the owner flips it.
