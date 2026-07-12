# SlackAzzCovered adoption handoff — engine-v0.9.0

> Engine-owner response to `SlackAzzCovered/docs/_discussion/2026-07-10-engine-developer-request.md`
> (including the codex refresh of 2026-07-12 12:46 WEST). Every claim below is against the
> **`engine-v0.9.0` tag** — not branch state. Sibling docs: `gopro-handoff.md`, `mageqa-handoff.md`.

## 1. Pinned artifact (Deliverable 1)

- **Tag:** `engine-v0.9.0` (annotated, local to the TGHandyUtils repo — tags are never pushed;
  consumers build wheels from the tag, per `working-against-the-freeze.md`).
- **Versions:** `ai_workflow_engine 0.9.0`, `ai_workflow_viewer 0.2.0` (optional, for bundle
  inspection), `ai_workflow_tools 0.3.0` (optional, CLI/console executors + media).
- **Install:** build a wheel from the tag and pin it in your image (GoPro's
  `tools/build_engine_wheel.sh` pattern):

  ```bash
  git -C /path/to/TGHandyUtils checkout engine-v0.9.0
  pip wheel --no-deps -w dist/ packages/ai_workflow_engine   # + packages/ai_workflow_viewer if wanted
  pip install dist/ai_workflow_engine-0.9.0-py3-none-any.whl
  python -c "import ai_workflow_engine; assert ai_workflow_engine.__version__ == '0.9.0'"
  ```

- **Acceptance gate: NONE remaining.** The tag shipped after: independent Phase-4 review PASS;
  paid four-consumer live qualification **4/4** from a clean detached clone (run
  `2026-07-12T193714Z-bfb669f`, $0.2429 subscription-notional, $0 metered — includes a hermetic
  Slack triage scenario with a real durable suspend/resume on a live `claude -p` worker); user-side
  viewer acceptance; full tier 1221 passed / 2 skipped / 0 failed; clean-venv wheel smoke with zero
  repo access.
- Never track `polish/workflow-engine` or any live branch. Do not vendor source.

## 2. Integration boundary (Deliverable 2): ACCEPTED as proposed

Your split is exactly the engine's own ownership doctrine — confirmed verbatim:

- **You own:** source adapters, product state, Redis/Celery/timers (ALL clocks — the engine never
  self-fires), Slack/Redmine/email/codebase clients, secrets (no 1Password or client objects ever
  enter engine code — you inject configured capabilities), final side-effect execution through your
  existing Redis outbox.
- **Engine owns:** workflow execution, branching/evaluate/retrace/fallback, parse/repair,
  side-effect policy gates, budget checks, trace/usage/detail records, wait lifecycle
  (identity, claim, bounded attempts, terminalization, terminal evidence), capability invocation.
- **Trace-only/shadow first is supported by construction:** register capabilities that only
  *propose* (return state facts + action requests as data), run with
  `SafetyPolicy(allowed_side_effects=["read_only"])`, and let your product code apply/dispatch.
  The engine will loudly DENY any capability that declares an external side effect under that
  policy — that denial is itself part of your contract smoke.

## 3. The eight capability answers

| # | Question | Status at `engine-v0.9.0` | Contract |
|---|---|---|---|
| 1 | Durable time-triggered suspension/resume | **Supported now** | `.human(node, wait_policy=DurableWaitPolicy(timeout_s=...), timeout_to="escalate")`. There is no separate `requires_time` mechanic and no engine timer: a finite `timeout_s` + declared timeout route are MANDATORY on every durable wait, and **your** scheduler fires it by reading `coordinator.due(now)` and delivering a timeout event (§5). Celery stays your clock. |
| 2 | Durable external-write/outbox | **Product-owned is canonical (by design, not omission)** | The engine never persists pending external writes. Capabilities return action *intents* (data); your Redis `MULTI/EXEC` outbox is the single durable delivery mechanism. The engine gives you the idempotency spine: `context.metadata["wait_idempotency"]` is stable across redelivery of the same accepted event — key your outbox claims on it and duplicate deliveries collapse to ONE intent (proven in `test_hermetic_slack_durable_approval_full_lifecycle`). |
| 3 | Stable `case_id`/`work_item_id` correlation | **Supported now, first-class** | `WorkflowGoal(correlation_id="case-42")` (human label: *Related-run ID*). Enforced invariants: immutable registration truth on `WaitRecord`; projected into terminal/abandoned evidence; stamped on every trace/detail/usage event; survives snapshots and spans runs; conflicting/invented identities are REFUSED loudly before persistence; type-strict (int 42 never "matches" "42"); never controls storage/dedup/scheduling/execution. Viewer filters by it (`?related_run_id=` on the served HTTP viewer). Use it for `case_id`. `source_event_id` maps to `WaitEvent.event_id` (§5). |
| 4 | Source-neutral event/action mapping | **Supported now** | `EngineEventInput` → `engine.run(workflow_id, payload)` payload (plus `WorkflowGoal(correlation_id=case_id)`); `WorkItemSnapshot` → your capability outputs / state-fact proposals (data in `CapabilityResult`); `ActionRequest` → data-only intents returned by capabilities (dispatch via your outbox; use `ExternalWriteRequest` only when you later allow the engine's own gated writer path); `HumanApprovalRequest` → `.human(wait_policy=...)` node; `EngineDecisionOutput` → `result.output` + `result.trace` + observation bundle. Minimal `client_message_triage` pseudocode in §6. |
| 5 | Data-only dynamic schemas | **Keep dynamic-schema handling in your adapter** | The engine accepts output models registered BY PRODUCT CODE (pydantic classes you own, `extra="forbid"` recommended — malformed/extra fields are rejected by the engine, proven in the cooldown hermetic test). It never accepts request-provided Python or executable schema source. If you need runtime-selected schemas, map data-defined spec → one of YOUR pre-registered models in the adapter. Authored flows can only wire pre-registered capabilities (recursion firewall; no tool invention). |
| 6 | Human approvals for destructive actions | **Supported now, top-level** | `.human(node, wait_policy=...)`; local = snapshot back to caller, durable = registered wait + public `deliver_wait_event`. Known limitation, loud not silent: NESTED suspension (a human wait inside a subworkflow) is rejected at validation. Model approvals as top-level workflow nodes. Destructive actions additionally sit behind side-effect gates: undeclared `external`/`workspace_write` effects are denied pre-spawn. |
| 7 | Memory/event-log integration | **Confirmed: prompt input only, never control** | Adapt Weaviate behind the `MemoryStore` seam; memory projections are byte-safety-validated prompt inputs. Nothing the memory returns can alter transitions, gates, budgets, or wait state — that is a spec-level soul rule ("memory/observability is input — never control"), not a convention. |
| 8 | v0.9 authored flows | **Ignore for iteration one — hand-declared workflows** | You said you don't need user-authored pipelines; correct call. `FlowArtifact` v1.5a shipped in this tag anyway (bounded authored fanout + turnkey author), so nothing is "waiting" on it — adopt hand-declared `WorkflowBuilder` graphs now, revisit authored flows only when a real SlackAzz case needs them. |

## 4. Stable public contract (imports and signatures)

All from `ai_workflow_engine` top level (PEP 562 lazy surface; static-export guard keeps this list
honest):

```python
from ai_workflow_engine import (
    DurableWaitPolicy,      # mode="durable"; timeout_s REQUIRED finite >0; max_resume_attempts>=1; signal_correlation: dict[str,str]
    LocalWaitPolicy,        # mode="local" — exactly the old suspend/resume, snapshot to caller
    WaitCoordinator,        # Protocol you implement over Redis (11 members, all async)
    WaitEvent,              # kind: "signal"|"timeout"; event_id (non-empty, THE dedup key); payload (byte-safe, persisted)
    WaitDeliveryOutcome,    # typed result of deliver_wait_event — branch on outcome.kind
    InMemoryWaitCoordinator, # reference implementation — read it next to your Redis adapter
    WorkflowBuilder, WorkflowEngineBuilder, WorkflowGoal, ObservationConfig,
)
```

Engine methods:

- `await engine.deliver_wait_event(wait_id, event) -> WaitDeliveryOutcome` — public door for
  signals AND timeouts. `outcome.kind` is typed:
  `executed | duplicate | already_processing | not_accepted | terminal | rejected | attempts_exhausted`.
  Duplicates and late events get honest reports, never re-execution.
- `await engine.cancel_wait(wait_id, reason=...)` — escape hatch for pending/stalled waits
  (never preempts a live claimant); writes terminal evidence.
- `builder.with_wait_coordinator(coordinator)` — checks all 11 protocol members are async at wiring.

`WaitCoordinator` protocol — the EXACT 11 members your Redis adapter implements (this list is
test-locked against the Protocol; `builder.with_wait_coordinator()` checks all 11 are async):
`register(record, snapshot_json, definition_json) -> WaitReceipt`, `get(wait_id)`,
`load_snapshot(wait_id)`, `load_definition(wait_id)`,
`claim_event(wait_id, event, lease_until=...) -> WaitClaimOutcome`,
`complete(wait_id, claim, resolution_kind=...) -> WaitRecord`,
`fail(wait_id, claim, error=..., failure_kind=...) -> WaitRecord`, `due(now)`, `stalled(now)`,
`cancel(wait_id, reason=...) -> WaitRecord`, `health() -> WaitHealth(pending, claimed, overdue,
stalled, oldest_pending_deadline)`. `overdue` is DERIVED, never stored.

**Provisional (declared, not hidden):** none of the above. The only open viewer follow-up is
cosmetic (parent-edge metadata for out-of-graph trace activity, filed post-v0.9).

## 5. Wall-clock driver (your Celery loop) + atomicity boundary

```python
# Celery beat task — the ONLY clock in the system. Safe to crash anywhere and re-run.
async def fire_due_waits(coordinator, engine):
    now = datetime.now(timezone.utc)
    for record in await coordinator.due(now):
        event = WaitEvent(
            kind="timeout",
            # STABLE identity: same wait + same accepted deadline => same event_id, so a
            # crash between deliver and ack just redelivers the SAME event (deduplicated).
            event_id=f"timeout:{record.wait_id}:{record.deadline_at.isoformat()}",
        )
        outcome = await engine.deliver_wait_event(record.wait_id, event)
        # executed => resumed through the DECLARED timeout_to transition;
        # duplicate/terminal/... => typed no-op reports; nothing here retries business logic.

    for record in await coordinator.stalled(now):
        # claimed wait whose lease expired (worker died mid-resume): redeliver the SAME
        # accepted event (identity is FROZEN — a different event is typed "not_accepted"),
        # or cancel() if the case is dead. Bounded by max_resume_attempts.
        ...
```

**Atomic in your Redis adapter (same MULTI/EXEC):**
1. `register(record, snapshot_json, definition_json)` — wait row + snapshot bytes + canonical
   definition bytes commit together; the receipt must echo the ACCEPTED deadline (co-committed
   timeout intent). The conformance kit asserts this.
2. `claim_event` — CAS on (status, version) + lease write, one transaction.

**What is NOT atomic — and how correctness holds anyway:** coordinator completion
(`complete(...)`) is invoked by the ENGINE after the resumed run and carries no action intent, so
there is no API to co-commit terminalization with your outbox write. Your capability writes the
outbox intent DURING the resumed run, keyed by `context.metadata["wait_idempotency"]` — that key
is stable across redelivery of the same accepted event, so a crash between intent-write and
completion just redelivers and the idempotent key collapses duplicates to ONE intent. This is
deliberate at-least-once design, not a gap.

Wording that must survive into your docs: **deduplicated event acceptance + single active
claimant (CAS/lease) + at-least-once crash recovery with idempotent effects; a completed claim
never re-executes.** Never write "exactly-once", and never claim coordinator/outbox atomicity.

## 6. `client_message_triage` on engine primitives

```python
builder = (
    WorkflowEngineBuilder()
    .with_wait_coordinator(RedisWaitCoordinator(...))          # your adapter (§7 kit)
    .with_observation(ObservationConfig(enabled=True, bundle_dir=...))
)
builder.register_capability("classify_message", classify, kind="llm",
                            output_model=Classification)       # YOUR strict pydantic model
builder.register_capability("draft_reply", draft, kind="llm")
builder.register_capability("finalize_draft", finalize, kind="deterministic")
builder.register_guard("actionable_gate", lambda payload: payload["classified"])  # deterministic route

builder.register_workflow(
    WorkflowBuilder("client_message_triage")
    .step("classify_message")            # business retry: Retry(3) fast burst INSIDE the step;
                                         # provider outage => route to a durable COOLDOWN wait
                                         # (chained waits, healing via on_timeout) + emit a typed
                                         # OperationalAlertProposal intent — never a dead case
    .branch("actionable_gate", ...)      # not actionable => end (state fact still proposed)
    .step("draft_reply")
    .human("await_answer_or_sla",        # THE SLA timer: soft-ping deadline
           wait_policy=DurableWaitPolicy(
               timeout_s=SLA_SOFT_S,
               signal_correlation={"case_id": ..., "channel": ...}),
           timeout_to="escalate_soft")   # declared route: propose manager soft ping intent
    # escalate_soft -> another durable wait (hard ping) -> bounded backoff = CHAINED waits,
    # each with its own declared timeout route; ack/answer arrives as a SIGNAL:
    #   engine.deliver_wait_event(wait_id, WaitEvent(kind="signal",
    #       event_id=f"slack:{source_event_id}", payload={"kind": "ack", ...}))
    # a raced timeout after ack is a typed terminal report — zero duplicate intents.
    .step("finalize_draft")
    .build()
)
goal = WorkflowGoal(workflow_type="client_message_triage",
                    objective="triage inbound client message",
                    correlation_id=case_id)                     # Related-run ID (case identity)
result = await engine.run("client_message_triage", payload, goal=goal)
```

Engine proposes (data out) — your outbox executes. Both hermetic proofs of exactly this shape ship
in the tag's test suite: `test_hermetic_slack_durable_approval_full_lifecycle` (public doors,
due()-timeout, chained + duplicate delivery, ONE intent under redelivery, engine-DENIED external
send) and `test_hermetic_slack_cooldown_ack_and_membership_lifecycle` (outage → durable cooldown +
typed alert; ack → cancel; membership-refresh resume) in
`tests/unit/test_engine_qualification_support.py`.

## 7. Your Redis adapter: conformance kit

```python
from ai_workflow_engine.testing import run_wait_registration_conformance

async def test_redis_coordinator_conformance():
    await run_wait_registration_conformance(
        make_coordinator=lambda: RedisWaitCoordinator(redis_url=TEST_URL),
        reconnect=lambda: RedisWaitCoordinator(redis_url=TEST_URL),  # restart survival
    )
```

The kit covers the full lifecycle your smokes list demands: atomic registration attestation
(receipt echoes accepted deadline), restart/reconnect returning the same wait + snapshot +
definition BYTES (integrity seal recomputes the digest), CAS claim + lease + single active
claimant, frozen accepted-event identity across lease expiry, duplicate typed non-execution,
failure-kind persistence + bogus-kind rejection, cancel semantics (never preempts a live lease),
`stalled()` visibility, attempt-ordinal monotonicity. Run it against real Redis in your CI, plus
your own MULTI/EXEC atomicity tests (the kit cannot see your transaction boundaries).

## 8. Your listed contract smokes → engine evidence

Every smoke you listed is already proven engine-side; mirror them against your adapter:

| Your smoke | Engine evidence at the tag |
|---|---|
| fresh run suspends + registers; restart returns same wait/snapshot | conformance kit + `test_waits.py` registration/reuse suite |
| due timeout resumes through declared transition once per accepted event | hermetic approval lifecycle test |
| duplicate delivery typed, no duplicate Slack intent | same test — action intents keyed by `wait_idempotency`, ONE intent |
| classifier outage → recoverable cooldown + operational alert | hermetic cooldown test |
| ack/answer cancels future escalation | hermetic cooldown test (raced timeout = typed terminal, zero intents) |
| no-manager state resumable after membership refresh | hermetic cooldown test |
| adapter health: pending/overdue/claimed/expired-lease/failed/integrity | `WaitHealth` + kit + `health.stalled` tests |
| no product clients in engine code; no source copied | DI-only capability injection; wheel pin (§1) |

## 9. MageQA / GoPro compatibility

Same migration they already have documented (their handoffs carry the delta): the ONE breaking
change is `.human(node)` now requires an explicit `wait_policy` —
`LocalWaitPolicy()` is byte-for-byte the old suspend/resume semantics (mechanical edit).
Everything else is additive. Degradation evidence: full tier 1221/2/0 at the tag includes both
consumers' scenario suites, and the paid qualification ran all four consumer scenarios (Anki,
MageQA incl. authored flow + partial fanout, GoPro vision + artifact evidence, SlackAzz triage
incl. durable suspend/resume) live: 4/4.
