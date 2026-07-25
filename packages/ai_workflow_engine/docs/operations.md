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

## Caller Cancellation Lifecycle

Cancelling the task that awaits `engine.run(...)` or `engine.resume(...)` is different from an
engine execution-window timeout:

1. capability supervision stops and settles owned async/process work;
2. the run session finalizes its observation segment with status `cancelled`;
3. trace, detail, usage, and artifacts from capability invocations completed before cancellation
   remain in that segment;
4. the original `asyncio.CancelledError` is re-raised to the caller.

Artifacts are retained at the normalized capability-result boundary, before fan-out, child-
workflow, or graph-state aggregation can be interrupted. A resumed segment starts with the
snapshot's artifacts and adds newly completed artifacts in first-publication order. Identical
`artifact_id` records deduplicate; conflicting evidence under one id fails loudly.

If observation finalization itself fails, that storage error is raised with the cancellation as
its cause. The engine never reports ordinary cancellation while silently leaving an incomplete
archive. With observation disabled no bundle is created; process cleanup and cancellation
propagation are unchanged.

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
  -> product persists the complete handle
  -> product clock/event ingress
  -> engine.deliver_wait_event(WaitHandle, WaitEvent)
  -> coordinator claim/lease
  -> engine resume
  -> coordinator complete/fail
  -> observation segment committed
```

### Product responsibilities

- Implement persistent `WaitCoordinator` storage and pass the distinct-instance conformance kit
  plus process-isolated transaction races against the real store. An in-process PASS cannot prove
  that participant state survives separate workers.
- Persist the complete `WaitHandle`, including `registration_id`; `wait_id` is an index, not
  authority to resume a registration incarnation.
- Run the external clock/ingress that calls `due(now)` and `stalled(now)`.
- Redeliver the same accepted `event_id` after a crashed claimant.
- Cancel dead work explicitly with `engine.cancel_wait`.
- Make external effects idempotent using `wait_idempotency`.
- Alert on coordinator integrity errors, failed waits, stalled leases, and scheduler silence.

### Engine responsibilities

- Register before exposing a handle.
- On observed cancellation or a handled registration error before exposure, atomically settle only
  the registration attempt the engine owns. If settlement cannot be proved, fail loudly rather
  than report clean cancellation/failure.
- Seal the durable snapshot against direct `resume` bypass.
- Deduplicate accepted event identity and enforce one active lease claimant.
- Bound recovery attempts and validate the stored machine digest.
- Record terminal outcome and repair observation markers on redelivery.

The guarantee is deduplicated acceptance plus one active claimant plus at-least-once recovery. It is
not exactly-once business execution.

An abrupt process death before handle exposure is not an observed cancellation. The committed
registration remains pending and visible, and an exact retry of the same machine recovers the same
stored receipt. By contrast, once a caller observes cancellation/failure without receiving a
handle, that attempt's unexposed registration is terminal or the caller receives a
`WaitRegistrationSettlementError`. In particular, cancellation during an exact retry is loud when
the store cannot prove whether another caller already received the reused handle; the engine
preserves the registration instead of revoking possibly exposed work. These paths must not be
collapsed into one cleanup rule.

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
`3` — the value of `ai_workflow_engine.observation_bundle.BUNDLE_SCHEMA_VERSION`) and a required
provider-evidence integrity fact. A dashboard that
reads bundle files directly must check it and **fail loudly on an unknown version** rather than
mis-parse a future layout; prefer the engine's `load_bundle_meta_v3`, which is that check. The
current reader rejects v2; use `engine-v0.11.9` only if historical v2 evidence must be inspected.
Resolve a run's disposition through the group reader
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
| Registration settlement failure | Treat as an operational incident: the engine could not prove an unexposed continuation terminal/absent; do not retry delivery by bare wait id |
| Observation write failed | Execution outcome remains visible; repair storage and use typed observation status |
| Viewer says group corrupt | Preserve files, inspect duplicate/drifting segment identity, do not delete evidence first |

## Subscription Usage And Notional Pricing

Provider adapters own structured CLI parsing; the engine owns normalization, rate selection, and
the one persisted pricing result on `WorkflowUsageEvent`.

- Codex runs with structured JSONL enabled while `--output-last-message` remains response-text
  authority. The final `turn.completed.usage` is cumulative and is selected, never summed.
- Codex input/cache and output/reasoning counters are inclusive subsets. Impossible relationships
  fail normalization; they are never clamped into a plausible amount.
- Claude's provider-reported `total_cost_usd` is retained as `provider_reported` notional. It wins
  over configured calculation.
- Configured pricing persists the exact catalog version, rate version, provider/model prefix, token
  quantities, rates, source, and amount used. The engine rechecks that provenance and arithmetic
  before summary or bundle persistence.
- Missing/malformed usage, a missing model, or an unmatched rate produces typed `unknown` pricing
  while preserving the response/failure and safe diagnostics.
- Failure, timeout, and caller cancellation retain the latest complete usage event once. Cancellation
  still kills/reaps the process and re-raises the original `CancelledError`.
- Notional is API-equivalent plan value, not subscription billing. It never debits
  `max_estimated_usd`; enforce runaway protection with call/token/worker/window limits.

Operators should update a public/proxy catalog deliberately, give it a new version, run the pricing
contract tests and a representative bundle/viewer check, then deploy the complete package matrix.
Do not silently change a rate under an existing catalog/rate version.

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
6. **Caller-cancellation truth.** Cancelling a fresh or resumed engine run finalizes its segment as
   `cancelled`, preserves evidence from completed direct/fan-out/child invocations, settles owned
   processes, and re-raises the original `CancelledError`; archive failure remains loud.

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
  old observation bundle root and START A NEW EMPTY bundle root (mixed roots expose historical
  bundles as corrupt by design; the current reader never interprets a pre-v3 layout);
  use a NEW wait-coordinator namespace, and explicitly settle or discard pending waits/snapshots
  under the OLD tag first (the current engine rejects them); keep the historical viewer/tag around
  only for reading historical data;
- a counterpart review checks code, docs, examples, and evidence from primary sources.

Framework requests and post-adoption feedback close through
[`extension-lifecycle.md`](extension-lifecycle.md).

## Release Artifacts And Verification (v0.11.14)

Two roles, two identities. A consumer NEVER rebuilds as verification: the **annotated tag identifies
source**, while the **published release directory identifies artifact bytes**. A release tag
annotation records source identity only and must not repeat wheel hashes; the published release
directory and its `SHA256SUMS` are the sole byte authority. Consumers pin artifact bytes from the
manifest and must never treat the annotation as the delivery. The bundle verifier detects
incomplete, malformed, unsafe, or byte-drifted deliveries; channel authenticity still comes from
the approved private cache or an independently pinned manifest/wheel hash.

`release-manifest.json` marks each artifact `published: true` to mean **declared for the published
channel**, not "already uploaded". A freshly assembled bundle is therefore **staged**: its `uri`
points at the approved-cache location it is destined for, which does not exist until the upload
step runs. Confirm the artifact is actually retrievable from that URI before repinning any consumer;
a staged bundle that verifies locally is not yet a delivery.

### Producer

The user creates the immutable annotated tag only after code review. From that point, a failed
artifact gate requires a new fix-forward version; never move the tag. Use two independent detached,
clean checkouts for wheel reproducibility and a third checkout for test/smoke evidence. Keep all
outputs outside those checkouts.

The root `./test.sh` wrapper is clean-checkout-safe under the repository's existing Docker Compose
v2 contract (verified on v2.20.2). It passes the repository `.env` to Compose when that file exists;
otherwise it creates a permission-restricted, empty file under the system temporary directory,
reports that choice, and removes the file on exit. Hermetic tiers therefore need no
operator-created `.env`, no secrets, and no source-tree mutation. Credentialed/live suites retain
their own explicit enablement and skip or fail by name when their required deployment
configuration is absent. Do not create an empty `.env` inside a release checkout and do not raise
the Compose version floor merely to make `env_file` optional.

The wrapper also assigns a deterministic Compose project name from the canonical checkout root.
Repeated runs in one checkout therefore clean up their own containers, while independent build or
gate checkouts cannot tear each other down when runs overlap. Release evidence continues to bind to
the recorded checkout path; the Compose project name is local execution state and is never manifest
identity.

Create the annotated tag with the exact source-only format enforced by `tagged_source()`. Do not
copy test counts, smoke results, wheel hashes, or other dynamic evidence into the annotation:

```bash
TAG=engine-v0.11.14
SOURCE="$(git rev-parse HEAD)"
git tag -a "$TAG" \
  -m "$TAG" \
  -m "Source: $SOURCE" \
  -m "Matrix: ai-workflow-engine 0.11.14 / ai-workflow-tools 0.6.5 / ai-workflow-viewer 0.3.7" \
  -m "Artifact bytes and gate evidence are identified only by the verified release directory."
```

```bash
TAG=engine-v0.11.14
BUILD_A=/tmp/engine-build-a
BUILD_B=/tmp/engine-build-b
GATE=/tmp/engine-gate
OUT=/tmp/engine-release-work
TOOL="$BUILD_A/packages/ai_workflow_engine/scripts/release_artifacts.py"

python3 "$TOOL" run-build --repo "$BUILD_A" --tag "$TAG" \
  --wheel-dir "$OUT/wheels-a" --record "$OUT/build.json" --log "$OUT/build.log"
python3 "$BUILD_B/packages/ai_workflow_engine/scripts/release_artifacts.py" run-build \
  --repo "$BUILD_B" --tag "$TAG" --wheel-dir "$OUT/wheels-b" \
  --record "$OUT/build-b.json" --log "$OUT/build-b.log"
python3 "$TOOL" compare-builds --repo "$BUILD_A" --tag "$TAG" \
  --first "$OUT/wheels-a" --second "$OUT/wheels-b"

python3 "$TOOL" run-gate --name test --record "$OUT/test.json" \
  --log "$OUT/test.log" --cwd "$GATE" --timeout-s 7200 -- ./test.sh unit
python3 "$TOOL" run-gate --name smoke --record "$OUT/smoke.json" \
  --log "$OUT/smoke.log" --cwd "$GATE" --timeout-s 900 -- \
  python3 "$TOOL" smoke-installed --venv-dir "$OUT/smoke-venv" \
  --work-dir "$OUT/smoke-work" \
  --wheel "$OUT/wheels-a/ai_workflow_engine-0.11.14-py3-none-any.whl" \
  --wheel "$OUT/wheels-a/ai_workflow_tools-0.6.5-py3-none-any.whl" \
  --wheel "$OUT/wheels-a/ai_workflow_viewer-0.3.7-py3-none-any.whl"

python3 "$TOOL" assemble --repo "$BUILD_A" --tag "$TAG" \
  --bundle-dir "$OUT/bundle" --uri-base "file:///approved-cache/$TAG/" \
  --build-evidence "$OUT/build.json" --second-build-evidence "$OUT/build-b.json" \
  --test-evidence "$OUT/test.json" \
  --smoke-evidence "$OUT/smoke.json" \
  --wheel "$OUT/wheels-a/ai_workflow_engine-0.11.14-py3-none-any.whl" \
  --wheel "$OUT/wheels-a/ai_workflow_tools-0.6.5-py3-none-any.whl" \
  --wheel "$OUT/wheels-a/ai_workflow_viewer-0.3.7-py3-none-any.whl" \
  --second-wheel "$OUT/wheels-b/ai_workflow_engine-0.11.14-py3-none-any.whl" \
  --second-wheel "$OUT/wheels-b/ai_workflow_tools-0.6.5-py3-none-any.whl" \
  --second-wheel "$OUT/wheels-b/ai_workflow_viewer-0.3.7-py3-none-any.whl"
python3 "$TOOL" verify-bundle --dir "$OUT/bundle"
```

The manifest validator binds these gates, not merely their zero exit codes. Test evidence must
record exactly `./test.sh unit`; focused paths, batching, and extra pytest arguments cannot certify
a release. Smoke evidence must invoke `release_artifacts.py smoke-installed`, declare one work
directory and one venv directory, and name exactly the three wheel artifacts carried by the
manifest. Producer assembly and consumer verification enforce the same rule.

Live-provider evidence follows the changed implementation. A release that changes provider code
must record a fresh live qualification for the affected provider in its immutable release
directory. A metadata-only release may inherit the last live qualification only when the provider
subtree is byte-identical and the exact new wheel passes installed transport smoke; record that
inheritance explicitly instead of silently deselecting the live suite. Prewarm local models before
the bounded qualification request so model startup time is not misclassified as adapter failure.

`run-build` derives `SOURCE_DATE_EPOCH` from the tagged commit, fixes the build umask, refuses a
dirty/wrong checkout, executes the build itself, and records the exact three wheel identities.
`run-gate` executes and records the real command, observed checkout commit, exit status,
timestamps, bounded log, and log hash. `assemble` requires both independent build records plus
both wheel matrices to be byte-identical, requires all gates to name the tagged source commit,
and accepts only passed records, genuine internally coherent wheels, and verifier sources that
byte-match the tag. The closed
`release-manifest-v2`, every evidence record/log, all three wheels, both verifier scripts, and
`SHA256SUMS` form one indivisible release directory.

### Consumer

Download the complete release directory. Obtain `release_artifacts.py` and
`release_contract.py` independently from the pinned annotated tag or another previously trusted
source, place them together, and use that trusted verifier before invoking `pip`:

```bash
BUNDLE=/path/to/downloaded/engine-v0.11.14
TRUSTED=/path/to/trusted/engine-v0.11.14-verifier
python3 "$TRUSTED/release_artifacts.py" verify-bundle --dir "$BUNDLE"
python -m pip install "$BUNDLE/ai_workflow_engine-0.11.14-py3-none-any.whl"
```

The verifier requires the exact manifest inventory, safely refuses traversal/symlink/FIFO/device
shapes without opening them, validates every checksum and evidence record, and validates wheel
ZIP/METADATA/WHEEL/RECORD coherence. A consumer may install only the engine wheel, but it still
verifies the complete three-package release directory first. Any mismatch means stop: do not
install, rebuild, or substitute bytes; report the release defect to the engine owner.
