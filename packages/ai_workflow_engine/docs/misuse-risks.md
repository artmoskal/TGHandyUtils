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

### Side effects inside undeclared code

Declare all effects on the capability. The engine can deny a declared external write before spawn;
it cannot protect an undeclared network call hidden in a “read-only” handler. Effects derived from a
durable event must use `context.metadata["wait_idempotency"]` in the product sink/outbox.

### Treating at-least-once as exactly-once

Durable events are deduplicated and only one claimant runs at a time, but crashes can cause the same
accepted event to be retried. Product effects must be idempotent. Do not claim coordinator/outbox
atomicity unless a product adapter implements and proves that transaction itself.

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
  cost attribution.
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
- Read current migration notes, not historical discussion files.
- Run product contract tests and one real workflow before production rollout.
- Keep the previous wheel/image available for rollback; do not mix snapshots produced by an
  unvalidated machine definition with a changed definition.

When the correct shape cannot express a real need, use
[`extension-lifecycle.md`](extension-lifecycle.md) instead of adding a local workaround.
