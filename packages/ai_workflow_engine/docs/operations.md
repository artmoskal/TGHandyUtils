# Runtime And Release Operations

This guide covers the lifecycle after a product has declared and registered a workflow. It assumes
the ownership model in [`concepts.md`](concepts.md).

## Normal Run Lifecycle

1. Product maps an external request to a typed payload and `WorkflowGoal`.
2. `engine.run(workflow_id, payload, goal=goal)` resolves one registered definition and profile.
3. The engine validates capabilities, model bindings, side effects, budgets, and machine structure.
4. A run session assigns a logical `run_id`, initializes cumulative usage, and opens an observation
   bundle when configured.
5. Nodes execute through `CapabilityRuntime`; transitions and retries remain engine-owned.
6. The result becomes `completed`, `partial`, `failed`, or `requires_user_input`.
7. Observation finalization records the true terminal/suspended state even when post-processing
   fails.

Consumers should persist product facts only from a result shape they explicitly accept. A partial
result is not a completed result with fewer fields.

## Execution Window Lifecycle

`RuntimeLimits.timeout_s` is the run's finite work budget. For each capability the engine
intersects the task request, capability limit, remaining run time, and parent soft deadline into one
persisted `ExecutionWindowDecision`.

- Async in-process handlers are cooperatively cancelled at the soft deadline. A clean stop is
  `partial`; cancellation suppression is `failed` and marked
  `cancellation_containment_failed`.
- Synchronous inline handlers cannot be killed. Under any finite task/capability/run/parent window,
  they are rejected before invocation. Make bounded code async or process-backed.
- Process-backed handlers use soft time for work and reserve time inside hard for terminate/kill,
  reap, stream settlement, artifact salvage, and result construction. The process bound is recorded
  in trace and shown by the viewer. The engine process owner includes stdin delivery in work and,
  on POSIX, isolates and terminates the full spawned process group; CLI adapters must use that owner
  rather than opening their own subprocess path.
- The graph fail-safe covers hangs outside capability invocation. It returns after a separate,
  recorded cancellation allowance. If in-process graph code suppresses cancellation, Python cannot
  kill it; the run fails loudly and operators should recycle the worker. Side-effectful or untrusted
  hard-bounded code belongs in a subprocess.

An explicit worker timeout may narrow the engine window, never widen it. A process call outside an
engine run must declare its own timeout; no hidden default is selected.

## Local Wait Lifecycle

Use a local wait only when the caller can retain the returned snapshot:

```python
.human("approve", wait_policy=LocalWaitPolicy())
```

The first run returns `requires_user_input` and `result.snapshot`. Resume with
`engine.resume(definition_or_id, snapshot, event_payload)`. If the caller loses the snapshot, the
engine cannot reconstruct it from an observation bundle.

## Durable Wait Lifecycle

Durable waits separate machine mechanics from product infrastructure:

```text
engine suspension
  -> coordinator.register(wait record + snapshot + definition)
  -> public WaitHandle
  -> product clock/event ingress
  -> engine.deliver_wait_event(wait_id, WaitEvent)
  -> coordinator claim/lease
  -> engine resume
  -> coordinator complete/fail
  -> observation segment committed
```

### Product responsibilities

- Implement persistent `WaitCoordinator` storage and pass the conformance kit.
- Run the external clock/ingress that calls `due(now)` and `stalled(now)`.
- Redeliver the same accepted `event_id` after a crashed claimant.
- Cancel dead work explicitly with `engine.cancel_wait`.
- Make external effects idempotent using `wait_idempotency`.
- Alert on coordinator integrity errors, failed waits, stalled leases, and scheduler silence.

### Engine responsibilities

- Register before exposing a handle.
- Seal the durable snapshot against direct `resume` bypass.
- Deduplicate accepted event identity and enforce one active lease claimant.
- Bound recovery attempts and validate the stored machine digest.
- Record terminal outcome and repair observation markers on redelivery.

The guarantee is deduplicated acceptance plus one active claimant plus at-least-once recovery. It is
not exactly-once business execution.

### Scheduler health

The product should publish at least:

- last successful scheduler pass;
- pending, claimed, overdue, stalled, failed, and cancelled wait counts;
- oldest pending deadline;
- event-delivery failures and recovery-attempt exhaustion.

A timeout callback is machine data (`timeout_to`), but the product clock must actually deliver it.
The engine does not hide a timer thread or scheduling service.

## Observation Lifecycle

With config-first observation enabled, one logical run may span multiple physical segment
directories:

- initial segment;
- one segment per local/durable resume attempt;
- terminal evidence segment when execution cannot resume;
- non-canonical abandoned/superseded attempts retained for honest cost and diagnosis.

Use `FileEventSource.read_group(run_id)` or the served viewer. Do not concatenate JSONL files or sum
segment summaries manually; the group reader selects canonical history and reports actual spend
including abandoned attempts.

`correlation_id` filters multiple independent run groups belonging to one case. It never merges their
state or results.

**Bundle schema is versioned.** Each bundle's `meta.json` carries `bundle_schema_version` (currently
`2` — the value of `ai_workflow_engine.observation_bundle.BUNDLE_SCHEMA_VERSION`). A dashboard that
reads bundle files directly must check it and **fail loudly on an unknown version** rather than
mis-parse a future layout; prefer the engine's `load_bundle_meta_v2`, which is that check. Resolve a run's disposition through the group reader
(`read_group(...).status`) rather than re-deriving it from raw events, and project result/segment
status into product state through an exhaustive closed mapping (partial vs failed vs rejected vs
requires_user_input vs completed) so a new status can never fall through silently.

See [`observability-levels-feedback.md`](observability-levels-feedback.md) for capture and viewer
details.

## Backend Scheduling And Raw-Media Consent

Two per-node runtime mechanics exist so products do NOT reimplement concurrency control or media
consent around the engine:

- **Backend scheduling / backpressure** — `WorkflowNode.scheduling = SchedulingPolicy(...)` gates a
  node's capability through the engine scheduler. Modes: `run_immediately`, `queue`,
  `single_flight_cancel`, `live_latest_only`, `run_latest`, `drop_stale`, `drop_not_queue`,
  `coalesce`, `fan_out_gather`, `replay_process_all`. When `backend_key` is set the engine serialises
  that lane (`max_backend_concurrency` bounds it) and **holds the backend slot until the worker
  actually completes** — the correct tool for a single-flight local GPU/VLM lane. Do not build a
  second scheduler in product code around the engine.
- **Raw-media export double consent** — exporting raw media requires BOTH the profile/ceiling
  permission AND the per-node opt-in `WorkflowNode.allow_raw_media_export=True`. Neither alone exports
  raw bytes; this is a deliberate privacy gate, not a default.

## Retention

- Finalized groups follow `ObservationConfig.retention_limit`.
- Suspended groups are retained indefinitely by default.
- `evict_suspended_after_s` is opt-in and sacrifices viewer history only. A durable wait remains
  resumable from coordinator storage; a local wait remains resumable only if the caller retained its
  snapshot.
- Archived artifacts are pruned with their bundle.
- Product databases, outboxes, and memory stores have separate retention policies.

## Failure Handling

| Failure | Expected action |
|---|---|
| Invalid workflow/config/capability | Fix declaration; do not retry runtime blindly |
| Provider output malformed | Engine repair/retry under configured bound, then typed failure |
| Provider unavailable | Product workflow may route to declared cooldown/wait/fallback |
| Budget exceeded | Stop; inspect usage and raise limits only after proving the workload needs it |
| Side effect denied | Fix policy/spec mismatch or keep the effect prohibited |
| Wait definition digest changed | Deploy compatible machine or deliberately migrate/cancel pending waits |
| Coordinator integrity/conformance failure | Stop delivery and repair adapter/storage; never fabricate state |
| Observation write failed | Execution outcome remains visible; repair storage and use typed observation status |
| Viewer says group corrupt | Preserve files, inspect duplicate/drifting segment identity, do not delete evidence first |

## Upgrade Lifecycle

1. Read the current release guide and binding spec status table.
2. Build wheels from the new immutable tag in a clean source checkout.
3. Record source commit and wheel hashes.
4. Run engine contract tests and consumer adapter/canary tests.
5. Validate pending snapshots/waits against changed machine definitions.
6. Run one representative real workflow with observation enabled.
7. Inspect result, costs, artifacts, and viewer lifecycle.
8. Deploy with the previous image/wheel available for rollback.
9. Update the consumer handoff status and close the framework request that triggered the upgrade.

Never silently re-cut a tag already consumed by another repository.

## Normative Contract AC

These are the operational promises covered by the engine evidence registry. Product scheduling,
databases, outboxes, provider credentials, and deployment remain consumer-owned obligations.

1. **Execution-window truth.** A finite run/task/capability/parent window is intersected once,
   recorded, and enforced according to declared interruptibility; timeout and cancellation
   containment cannot be reported as successful completion.
2. **Process settlement.** Process-backed work is stopped and reaped inside its represented hard
   boundary; stream/result settlement is bounded, unsafe result paths fail loudly, and salvage plus
   truncation remain observable.
3. **Durable-wait mechanics.** Registration precedes handle exposure; accepted events are
   deduplicated and leased; crash recovery is bounded; `due()` and `health()` expose timeout and
   scheduler-health truth. The engine never self-fires the product clock.
4. **Observation truth.** A logical run's canonical segments, abandoned spend, terminal status,
   artifacts, and related-run identity are read through the strict current-schema group reader;
   corrupt or historical shapes fail loudly rather than rendering a plausible page.
5. **Release identity.** A consumable release has one coherent engine/tools/viewer matrix, exact
   source/tag identity, wheel hashes, clean installed-wheel smoke, consumer-shaped qualification,
   and counterpart review. Consumer adoption is a separate pin-and-canary gate.

## Release Qualification

An engine release is ready only when:

- version metadata and tag agree;
- clean wheels install without repository access;
- core, tools, and viewer contract suites pass through approved wrappers;
- named consumer scenarios pass hermetically;
- any approved paid/live qualification stays within recorded caps;
- a human inspects the observation UI when the release changes user-visible viewer behavior;
- permanent docs describe tagged truth and consumer handoffs identify re-adoption risk (the line is latest-only — no migration layer exists);
- **latest-only cutover procedure** (moving a deployment to a new line): stop writers; archive the
  old observation bundle root and START A NEW EMPTY bundle root (mixed roots fail scans by design —
  one pre-v0.11 bundle beside current segments poisons listing and finalize-time retention);
  use a NEW wait-coordinator namespace, and explicitly settle or discard pending waits/snapshots
  under the OLD tag first (the current engine rejects them); keep the historical viewer/tag around
  only for reading historical data;
- a counterpart review checks code, docs, examples, and evidence from primary sources.

Framework requests and post-adoption feedback close through
[`extension-lifecycle.md`](extension-lifecycle.md).
