# Cost ledger & quota caps plan — durable economics across runs

Status: implementation-ready design (owner approved scope 2026-07-04); codex challenge welcome inline
Scope: TGHandyUtils product layer (engine's `UsageSink` protocol consumed as-is)
Complexity verdict: easy→medium (C1 ~½ day, C2 ~½ day, C3 ~½ day)

## Why we need this

The bot now spends from THREE pools: metered API dollars, **the owner's Claude plan quota
(shared with coding sessions)**, and the ChatGPT plan. Per-run captions are honest, but
nothing durable aggregates them: verified — no `usage_sink` wired in
`composition/container.py`; usage events exist only in logs and per-run observation
bundles, and bundles are retention-pruned, so history evaporates. Budgets are per-RUN
only. Consequences: no answer to "what did the bot cost this week, per pool"; nothing
stops 50 card runs from draining the week's Claude quota; the 2026-07-03 ChatGPT
rate-limit incident is exactly this risk class. Soul: *economics is part of the
architecture* — currently true per run, false across runs.

## What we need

1. **C1 — durable ledger:** every usage event lands in sqlite, surviving bundle pruning
   and restarts.
2. **C2 — daily caps per pool:** configured limits that LOUDLY refuse new runs when a
   pool's daily spend/plan-value is exhausted. Never silent degradation, never mid-run
   cutoff (wasting spend already made).
3. **C3 — `/usage` command:** today + 7-day rollup per provider, billed vs plan value,
   straight from the ledger.

**Non-goals:** forecasting, per-user billing, cross-repo (coding-session) quota tracking
— the ledger sees only what the bot spends.

## How

### C1 — `SqliteUsageSink`
Product-side class (in `database/` next to the other repositories) implementing the
engine's `UsageSink` protocol (`record(event)`); appends one row per `WorkflowUsageEvent`:
`ts, run_id, workflow_type, node, provider, model, cost_class, input_tokens,
output_tokens, estimated_usd, notional_usd, success`. Wire-up: the engine construction in
`services/content/anki_generation_graph.py` (and container factory) passes it as the
inner usage sink — the engine already tees session/bundle routing around whatever inner
sink is provided. Write failures: log loudly, never break the run (same contract as
observation projection).

**Honest coverage note:** the ledger captures ENGINE-run events. The off-engine
front-door paths (classifier/task parsing — see the documented deviation in
`infrastructure.md`) join automatically when that deferred migration lands; until then
their metered spend appears in logs only. Stated here so nobody mistakes the ledger for
complete-bot accounting on day one.

### C2 — daily caps
Config knobs (yaml settings, all OPTIONAL — no cap unless the owner sets one, per the
no-surprise-defaults rule):
`anki_daily_cap_notional_usd_claude_p`, `anki_daily_cap_notional_usd_chatgpt_browser`,
`anki_daily_cap_billed_usd`. Check at RUN START in `anki_processor`: one indexed
`SELECT sum(...) WHERE ts >= <local midnight, Europe/Lisbon>` per configured cap;
exceeded → TG reply "❌ Daily <pool> budget spent (~$X plan value of $CAP). Resets at
midnight." and no run. Mid-run spend is never cut off (rationale: the spend is already
made; cutting produces a failed run AND the cost).

### C3 — `/usage`
New handler: today + last-7-days table per provider — calls, billed $, plan value $ —
same wording as the caption ("billed (API)" vs "plan value") so the vocabulary stays
consistent everywhere.

## Phases & tasks

- [ ] C1.1 sink + table + wiring
  - AC: after any engine run, ledger rows exist matching the run's usage events; rows
    survive bundle pruning (delete the bundle dir, ledger intact); write-failure test
    proves the run still completes.
- [ ] C1.2 reconciliation test
  - AC: for one run, caption totals == ledger sums (billed and plan value) — the two
    surfaces can never tell different stories.
- [ ] C2.1 caps
  - AC: with a 1-cent cap configured and spent, the next `/anki` is refused with the
    loud message; with no cap configured, behavior is unchanged; midnight boundary uses
    Europe/Lisbon.
- [ ] C3.1 `/usage` command
  - AC: renders today/7d per provider from a seeded ledger; empty ledger renders a sane
    "no usage yet".

## Tests
Unit: sink append/rollup queries; cap arithmetic incl. timezone boundary. Integration:
processor-flow run with fake events → ledger rows + reconciliation; cap-refusal path.
Manual: generate one card, run `/usage`, compare with the card's caption; set a tiny cap,
verify the refusal message.

## Open questions (codex challenge)
1. Cap semantics: refuse-new-only (planned) — agree, or is a mid-run kill ever right?
2. Ledger retention: keep forever (rows are tiny) vs prune at 12 months?
3. Should the startup sweep from run-durability-plan.md also mark ledger rows for lost
   runs, so `/usage` can show "of which $X on runs that never delivered"?

## Definition of done
All three ACs green in the tier; manual `/usage`-vs-caption check matches; a configured
cap demonstrably refuses; docs (`infrastructure.md` external-services table) point at the
ledger as the durable economics surface.
