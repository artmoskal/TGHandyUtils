# Misuse And Risk Guide

These are the failures most likely to make a correct engine integration unreliable or unmaintainable.

## Architecture Risks

### Product supervisor loop

**Smell:** product code repeatedly chooses a capability, calls it, retries, updates status, or decides
the next node.

**Why it is wrong:** it creates a second runtime outside engine budgets, trace, waits, replay, and
validation.

**Correct shape:** declare the graph and register the workers. Product loops may ingest events,
deliver due wait events, and dispatch already-approved effects; they may not interpret the machine.

### Second worker abstraction

**Smell:** a special “agent node” bypasses `CapabilitySpec` and `CapabilityRuntime`.

**Risk:** inconsistent schemas, side-effect policy, model routing, timeout, cost, and observation.

**Correct shape:** adapt the worker to the universal capability contract or add an optional tool pack.

### Product logic in engine core

**Smell:** engine modules import product schemas, prompts, providers, or databases.

**Correct shape:** product capability/pack plus injected adapters. A core change must describe a
domain-neutral mechanic and a named consumer acceptance test.

## Runtime Risks

### Hidden or unbounded cycles

Every retry, retrace, fallback, fan-out, planner episode, and wait recovery path needs a declared
bound. Never use provider retries as business retries or `max_resume_attempts` as the product's
classification-attempt budget.

### Pretending inline work is interruptible

A finite engine window is enforceable only for cooperative async or process-backed work. Do not mark
arbitrary inline code as `process`, swallow `CancelledError`, or put network/subprocess work inside a
sync wrapper. The engine rejects synchronous handlers under finite windows; use the declared process
door when a hard stop matters. A graph containment failure means the caller was released, not that
Python killed cancellation-resistant code; recycle the worker before trusting it with more work.

Do not call `subprocess.run`/`Popen` behind a capability and label it process-enforced. The shared
process owner is what bounds stdin, records cleanup, and stops spawned descendants. A private
subprocess side door can outlive the run while its trace falsely claims containment.

When a capability object publishes `handler.spec`, that spec owns schemas, effects, metering, and
timeout enforcement. Configure it at construction; duplicate registration kwargs are rejected so a
safety policy cannot disappear silently.

### Replacing engine cancellation finalization

Do not catch caller cancellation around `engine.run()` and write a product-owned substitute bundle.
The engine finalizes the active segment as `cancelled` before re-raising, using its run-local
artifact journal to preserve completed fan-out and child evidence that may not have reached graph
state. A product wrapper may log or translate at its outer API boundary only after the engine task
has settled. If bundle finalization raises, treat that storage failure as operationally distinct;
do not replace it with a successful cancellation acknowledgement.

### Side effects inside undeclared code

Declare all effects on the capability. The engine can deny a declared external write before spawn;
it cannot protect an undeclared network call hidden in a “read-only” handler. Effects derived from a
durable event must use `context.metadata["wait_idempotency"]` in the product sink/outbox.

### Treating at-least-once as exactly-once

Durable events are deduplicated and only one claimant runs at a time, but crashes can cause the same
accepted event to be retried. Product effects must be idempotent. Do not claim coordinator/outbox
atomicity unless a product adapter implements and proves that transaction itself.

The generic wait conformance kit creates distinct adapter instances in one interpreter. It detects
instance-local state, but cannot detect a class-level/process-local cache shared by those instances.
Persistent adapters must also force creator/reuser and compensation/delivery races from separate OS
processes against the real Redis/DB transaction boundary.

### Delivering by wait id or reconstructing a handle

Persist the complete `WaitHandle` returned by a durable suspension and pass it back unchanged for
both signal and timeout delivery. `wait_id` is deterministic and identifies the logical suspension;
`registration_id` identifies the accepted registration incarnation. A bare wait id, a reconstructed
handle, or a handle retained from a retired incarnation must never authorize delivery into a newer
registration.

If the process dies before exposing the handle, retry the identical run so the engine recovers the
stored receipt. If the caller observes cancellation or a handled registration failure without a
handle, do not hunt for and deliver the hidden wait: the engine must settle its own unexposed
registration or raise `WaitRegistrationSettlementError`. A cancelled exact retry can produce that
loud error when the same registration may already have been exposed by another caller; this is
intentional truthfulness, not permission to reconstruct a handle.

### Missing wait timeout ownership

The engine never self-fires. Every durable wait has a finite timeout and declared timeout route;
every production product needs an external clock loop that checks `due()` and `stalled()` and either
redelivers the accepted event or cancels the wait. Monitor `WaitHealth` so a dead scheduler cannot
silently accumulate work.

### Reusing identity fields

`run_id`, `correlation_id`, `wait_id`, and `event_id` are not aliases. Reusing a case ID as run ID or
changing an event ID during redelivery breaks grouping or deduplication.

## Data And Prompt Risks

### Bytes in state or memory

Use `ImageInput` only at the worker transport boundary. Persist `EvidenceRef` and archived artifacts.
Raw bytes, base64 media, iterators, or opaque object reprs in state/checkpoints/memory fail loudly.

### Memory as authority

Memory may be stale, learned, or incomplete. Treat it as prompt context and link important records to
evidence. It cannot approve a side effect, skip a gate, or become hidden control state.

### Prompt templates outside strict rendering

Use `PromptRef`/`PromptRenderService` and explicit variables. Do not format prompts ad hoc across
products; missing variables must fail before a provider call.

### Capturing less than the investigation requires

Observation capture is off/full, configured by the application. Full capture stores rendered prompts,
responses, tool payloads, and artifacts; operators must apply suitable filesystem access and
retention. Export redaction is a separate concern and must not silently alter the internal record.

## Cost And Provider Risks

- Configure call, token, image, parallelism, timeout, and money ceilings in `RuntimeLimits`.
- Failed calls consume call budgets when a provider attempt occurred.
- Do not sum subscription-notional cost into metered spend.
- Do not bypass the reviewed provider factory; direct clients lose model routing, observation, and
  cost attribution, and engine call/token/budget enforcement. A direct subscription CLI call may
  drain quota while the engine sees nothing.
- Do not create one product-local Ollama/OpenAI executor per application. Configure the generic
  `ai_workflow_tools.providers.openai_compatible` client at the product composition root and inject
  it as an ordinary `LLMCallable`; product code still owns endpoint, secret, model, and rate policy.
- Do not enable SDK/provider retries under that adapter. One adapter call is one engine-visible
  provider attempt; `401`, `429`, timeout, malformed usage, and provider failure remain loud facts
  for engine/product policy.
- Notional pricing is explanatory API-equivalent plan value, not billed spend and not an account
  balance. Do not use it as the only runaway guard; keep call/token/worker/time limits finite.
- Never reinterpret provider JSON or recalculate catalog prices in a product/dashboard/viewer.
  Consume the persisted typed quantities, source, versions, and amount. Unknown stays unknown.
- Do not reuse an old catalog/rate version after changing rates, or label an internal proxy rate as
  public.
- CLI tool policy is explicit. Tool-free completion, scoped read, and workspace-writing agents have
  different side-effect declarations.

## Observability Risks

- A trace event is compact lifecycle/control evidence; detail records hold full payloads. Usage is a
  separate accounting ledger. Do not merge these contracts.
- Do not infer edges for activity outside the declared graph. Until parent metadata exists, the
  viewer labels such activity as external.
- Observation retention does not delete durable wait state. Conversely, retaining a bundle does not
  guarantee a product coordinator still has a resumable snapshot.
- Treat observation artifacts as untrusted content. The viewer serves only manifest-listed files and
  forces active/unknown content to download.

## Upgrade Risks

- Never move a consumed tag. Release a new tag and update pin, source commit, wheel hash, and canary.
- The line is latest-only: there are no migration guides or compatibility readers. Old persisted
  data (snapshots, wait records, observation bundles) is rejected loudly by the current line —
  inspect it with its matching historical tag; adopt the current contract fresh.
- Run product contract tests and one real workflow before production rollout.
- Keep the previous wheel/image available for rollback; do not mix snapshots produced by an
  unvalidated machine definition with a changed definition.

When the correct shape cannot express a real need, use
[`extension-lifecycle.md`](extension-lifecycle.md) instead of adding a local workaround.
