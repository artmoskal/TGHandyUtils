# SlackAzzCovered Engine Adoption Guide

Status: **ready for consumer validation against immutable tag `engine-v0.11.0` (latest-only
line).** SlackAzzCovered
should pin tag `engine-v0.11.0`, implement its Redis coordinator, and pass the engine conformance kit plus product
transaction tests before enabling live effects.

This answers the consumer request in
`SlackAzzCovered/docs/_discussion/2026-07-10-engine-developer-request.md`. Shared mechanics live in
[concepts](concepts.md), [getting started](getting-started.md), [operations](operations.md), and
[misuse risks](misuse-risks.md).

## Product Outcome

SlackAzzCovered maps Slack/Redmine/email/code events into source-neutral cases, lets the engine run
classification and decision workflows, waits durably for answers/deadlines, and emits action intents.
The existing product outbox executes approved effects.

First machine:

```text
client event
  -> deterministic case state
  -> bounded classifier retry
  -> branch actionable/non-actionable/provider-outage
  -> draft/evaluate
  -> durable answer-or-SLA wait
  -> answer/ack | soft escalation | hard escalation | cancel
  -> final product state + idempotent action intents
```

## Package Choice

| Package | SlackAzzCovered use |
|---|---|
| `ai-workflow-engine==0.11.0` | Required workflow/wait runtime |
| `ai-workflow-tools==0.5.0` | Add for CLI agents/tool catalog if the product uses them |
| `ai-workflow-viewer==0.3.0` | Developer diagnostics or product-linked run inspection |

> **Current matrix:** `engine-v0.11.0` + `ai-workflow-tools==0.5.0` + `ai-workflow-viewer==0.3.0`
> (tools/viewer require `ai-workflow-engine>=0.11,<0.12`). The previous line
> (`engine-v0.10.1` + tools `0.4.1` + viewer `0.2.3`) remains available at its historical tag for
> historical data; the lines never mix in one environment.


Build from the immutable tag and record commit/hash. Never copy engine source or track the branch.

## Ownership Boundary

### Engine owns

- workflow execution, branching, evaluate/retrace/fallback, parse/repair, and budgets;
- capability side-effect policy, trace, usage, details, and observation bundles;
- durable wait identity, registration, claim/lease, bounded recovery, terminalization, and evidence;
- public delivery/cancellation doors.

### SlackAzzCovered owns

- Slack/Redmine/email/codebase adapters, credentials, product state, and source-neutral schemas;
- Redis implementation of `WaitCoordinator`;
- Celery/beat clock and external event ingress;
- Redis outbox and final Slack/Redmine/email side effects;
- Weaviate/product memory and business escalation policy.

The engine never imports Slack, Redis, Celery, or 1Password clients.

## Public Wait Contract

```python
from ai_workflow_engine import (
    DurableWaitPolicy,
    LocalWaitPolicy,
    WaitCoordinator,
    WaitDeliveryOutcome,
    WaitEvent,
    WaitRecord,
    WorkflowBuilder,
    WorkflowEngineBuilder,
    WorkflowGoal,
)
from ai_workflow_engine.testing import run_wait_registration_conformance
```

The 11 async coordinator members are:

- `register(record, snapshot_json, definition_json)`
- `get(wait_id)`
- `load_snapshot(wait_id)`
- `load_definition(wait_id)`
- `claim_event(wait_id, event, lease_until=...)`
- `complete(wait_id, claim, resolution_kind=...)`
- `fail(wait_id, claim, error=..., failure_kind=...)`
- `due(now)`
- `stalled(now)`
- `cancel(wait_id, reason=...)`
- `health()`

`builder.with_wait_coordinator(coordinator, clock=...)` rejects missing or synchronous members.

Public engine doors:

```python
outcome: WaitDeliveryOutcome = await engine.deliver_wait_event(wait_id, event)
record, observation = await engine.cancel_wait(wait_id, reason="case closed")
```

Delivery kinds are closed: `executed`, `duplicate`, `already_processing`, `not_accepted`, `terminal`,
`rejected`, and `attempts_exhausted`.

## Clock Driver

The engine never self-fires. Celery/beat calls the public door:

```python
async def deliver_due(coordinator, engine, now):
    for record in await coordinator.due(now):
        event = WaitEvent(
            kind="timeout",
            event_id=f"timeout:{record.wait_id}:{record.deadline_at.isoformat()}",
        )
        await engine.deliver_wait_event(record.wait_id, event)

    for record in await coordinator.stalled(now):
        # Redeliver the SAME accepted event from product ingress/outbox history,
        # or cancel a case that is deliberately abandoned.
        ...
```

Monitor scheduler heartbeat and `WaitHealth`. A broken Celery loop must become an alert, not an
infinite collection of invisible pending waits.

## Outbox And Atomicity

Capabilities return source-neutral action intents. SlackAzzCovered persists them in its outbox using
`context.metadata["wait_idempotency"]` as the deduplication key for effects derived from an accepted
wait event.

### What is atomic

- Coordinator registration atomically stores wait record, snapshot bytes, definition bytes, and
  accepted deadline in the adapter's transaction domain.
- Claim is a CAS plus lease write.
- The product outbox atomically stores an intent and its product-state update in the product's own
  transaction domain. In other words: never claim coordinator/outbox atomicity.

### What is NOT atomic

The generic `WaitCoordinator.complete()` call does not receive a product action intent, so the engine
does not co-commit coordinator terminalization and product outbox delivery. Recovery relies on
at-least-once event delivery and an idempotent product outbox. **Never claim coordinator/outbox
atomicity** and never advertise exactly-once execution.

## Source-Neutral Mapping

| Product concept | Engine mapping |
|---|---|
| `EngineEventInput` | Typed payload to `engine.run` |
| `case_id` / `work_item_id` | `WorkflowGoal.correlation_id` (Related-run ID) |
| `source_event_id` | `WaitEvent.event_id` for wait signals; product event metadata otherwise |
| `WorkItemSnapshot` | Product capability input/output state |
| `ActionRequest` | Data-only capability output persisted by product outbox |
| `HumanApprovalRequest` | Top-level `.human(..., wait_policy=...)` node |
| `EngineDecisionOutput` | `WorkflowRunResult` plus product projection |

Example goal:

```python
goal = WorkflowGoal(
    workflow_type="client_message_triage",
    objective="classify the message and propose the next safe action",
    correlation_id=case_id,
    metadata={"source_event_id": source_event_id},
)
result = await engine.run("client_message_triage", event_input, goal=goal)
```

`correlation_id` groups separate runs for one case in the viewer. It never deduplicates events,
chooses transitions, or merges run outputs.

## Data And Approval Rules

- Dynamic schemas are product-owned mappings from data-defined specs to pre-registered strict
  Pydantic models. Never execute request-provided Python/model source.
- Destructive actions remain data intents until an explicit top-level approval gate and product
  outbox policy accept them.
- Nested human suspension is rejected; keep approvals at top-level workflow nodes.
- Weaviate may implement `MemoryStore`, but memory is prompt input and never control state.
- Hand-declared workflows are the iteration-one path. Authored flow is available but unnecessary.

## One Provider Door

All model/CLI clients are constructed in one SlackAzzCovered composition module and injected into
capabilities. No event handler or workflow module creates providers directly.

## Redis Adapter Gate

```python
async def test_redis_coordinator_contract():
    await run_wait_registration_conformance(
        make_coordinator=lambda: RedisWaitCoordinator(TEST_REDIS_URL),
        reconnect=lambda: RedisWaitCoordinator(TEST_REDIS_URL),
    )
```

Add product tests proving real Redis transaction boundaries, scheduler restart, outbox idempotency,
and reconnect after process loss. The generic kit cannot inspect Redis `MULTI/EXEC` internals.

## SlackAzz-Specific Misuse Risks

- Do not turn Celery into a second workflow executor; it only discovers/delivers events.
- Do not use `max_resume_attempts` as the classifier's business retry budget.
- Do not generate a new timeout event ID on every scheduler pass.
- Do not execute Slack writes inside a read-only/proposal capability.
- Do not let dashboard/product status invent a wider run vocabulary.
- Do not treat an acknowledgement and timeout race as two business actions; terminal/duplicate
  delivery outcomes are typed no-ops.

## Adopter Contract AC

1. **Pin integrity:** wheel version, tag commit, and hash match the consumer pin.
2. **One provider door:** no provider construction in workflow/event modules.
3. **No product orchestration loop:** Celery delivers events but never chooses nodes/retries workers.
4. **Coordinator conformance:** generic kit plus real Redis atomicity/reconnect tests pass.
5. **Fresh suspension:** restart returns the identical wait, snapshot, and definition.
6. **Timeout:** due event follows the declared transition once per accepted event.
7. **Crash recovery:** expired lease permits same-event reclaim with bounded attempts.
8. **Outbox idempotency:** duplicate/redelivered events create one action intent.
9. **Cancellation:** answer/ack closes future escalation; late timeout is terminal/no-op.
10. **Provider outage:** bounded fast retry routes to recoverable cooldown and alert proposal.
11. **Health:** pending/overdue/claimed/stalled/failed and scheduler heartbeat are observable.
12. **Safety:** external Slack action is denied before spawn in shadow/read-only mode.
13. **Observation:** suspension and resume render as one logical run with honest costs.
14. **Execution window:** provider/CLI work inherits the engine bound; cancellation suppression is a
    visible failure, and synchronous handlers are not registered under finite profiles.

Follow [`operations.md`](operations.md) for production recovery. File missing mechanics through
[`extension-lifecycle.md`](extension-lifecycle.md), keeping Slack policy in product capabilities.


## v0.11 Release Contract

`engine-v0.11.0` is the current release of the **latest-only** line: strict versioned persisted
contracts (snapshot `v0.11`, bundle meta v2, wait records `wait-v1`), one strict viewer loader, and
NO migration layer — the sealed current-contract corpus shows 0 behavior deltas vs v0.10.1, but old
persisted data is rejected loudly naming its historical tag. Adopt by re-pinning fresh (pin tag
`engine-v0.11.0`, rebuild wheels: engine `0.11.0`, tools `0.5.0`, viewer `0.3.0`) and re-run your
canaries before changing any deployed pin. Historical data stays inspectable with its own
historical tag.
