# Framework Concepts And Ownership

The engine is an AI-capable state machine, not an LLM-node DAG toolkit. A workflow definition is
data; capabilities perform work; the executor owns legal movement through the machine.

## The One Contract

```text
typed product input
    -> WorkflowGoal + WorkflowDefinition
    -> engine.run(...)
    -> registered capability invocations
    -> engine-owned transitions, limits, retries, waits, trace and usage
    -> WorkflowRunResult + observation bundle + product-owned effects
```

Products may add workers and machines. They do not add a second node runner, retry controller,
planner loop, trace runtime, or state-status vocabulary.

## Core Objects

| Object | Meaning | Owner |
|---|---|---|
| `WorkflowDefinition` | Serializable nodes, transitions, descriptions, and gates | Product declares; engine validates/runs |
| `WorkflowBuilder` | Fluent constructor for a definition | Engine API |
| `CapabilitySpec` | Worker name, kind, schemas, side effects, timeout, and policy metadata | Engine contract; product supplies declarations |
| Capability handler | Deterministic function, model call, agent, adapter, human gate, or subworkflow | Product/tool pack |
| `WorkflowProfile` / `RuntimeLimits` | Safety, scheduling, budget, and boundedness | Application configuration; engine enforces |
| `WorkflowGoal` | Objective and stable product context for one run | Product supplies |
| `WorkflowRunResult` | Terminal/suspended status, output, node results, usage, trace, artifacts | Engine returns |
| `MachineSnapshot` | Engine control state for local suspend/resume | Engine creates; caller retains |
| `WaitCoordinator` | Product-owned persistence adapter for durable waits | Product implements; engine drives protocol |
| Observation bundle | Append-only execution evidence and artifacts | Engine writes; viewer/product reads |
| `MemoryStore` / `AgentMemory` | Prompt input derived from product or episode history | Product stores; engine projects; never control |

## Node Kinds

| Kind | Use it for | Do not use it for |
|---|---|---|
| `step` | One registered unit of work | Hidden retry/orchestration loop |
| `branch` | Choose one declared transition label | Inventing nodes or unbounded routing |
| `evaluate` | Accept, retry, retrace, replan, or fallback under declared bounds | Informal quality logging with no gate |
| `fanout` | Bounded parallel work with gathered results | Product-written `gather` around capabilities |
| `subworkflow` | Reuse a registered workflow under one complete-child execution window | Nested human suspension, currently rejected |
| `human` | Explicit local or durable wait | Polling inside a capability |
| `planner` | Produce bounded plans over registered capabilities | Runtime code/tool invention |

Every cyclic route has a declared traversal bound and exhausted route. Every machine-injected branch
has descriptions for every legal transition so an AI sees the current state and complete legal move
set.

## State, Memory, Evidence, And Observation

These are deliberately separate:

- **State** controls the current run. It is byte-safe, serializable, and checkpointable.
- **Memory** is selected history rendered into a worker prompt. It may inform a decision but cannot
  directly mutate transitions, limits, or wait state.
- **Evidence** points to externally inspectable facts/artifacts. Persist handles and fingerprints,
  not media bytes in state.
- **Observation** records what happened: compact trace and detail envelopes, canonical bodies when
  enabled, usage, artifacts, and lifecycle status. Bundle v4 stores small bodies inline and large
  bodies once in the logical run's SHA-256 value store. `ObservationReader` is the only physical
  read boundary; a viewer reads through it and execution never reads observation back as control.

## Run Identity

- `run_id` identifies one logical execution, including resumed segments.
- `correlation_id` identifies a wider product case containing multiple independent runs. The viewer
  calls it **Related-run ID**.
- `event_id` deduplicates one durable signal/timeout event.
- `wait_idempotency` identifies effects derived from one accepted wait event.

Do not reuse any of these for another role. In particular, `correlation_id` does not merge results,
deduplicate work, or choose transitions.

## Ownership And Layers

| Layer | Owns | Examples |
|---|---|---|
| L0 engine | Universal mechanics | graph execution, validation, budgets, waits, trace, memory projection |
| L1 executors | How a worker is reached | API LLM client, local model, `claude -p`, `codex exec` |
| L2 tool/modality packs | Reusable optional instruments | browser/MCP, image/TTS, probe catalog |
| L3 domain packs | Product-family semantics | MageQA audit pack, GoPro inventory pack |
| L4 product | Deployment and business ownership | UI, ingress, databases, secrets, outbox, clocks |

A new feature belongs at the lowest reusable layer that can own it without learning product concepts.
Product-specific behavior starts as a capability or domain pack. It moves into L0 only when at least
one named workload proves a universal mechanic is missing.

## Complexity Gradient

One engine covers both extremes:

- A one-step deterministic workflow pays for one definition, one registration, and one call.
- A long-running workflow may opt into agents, fan-out, memory, durable waits, observation, authored
  flow, and external adapters.

The framework minimizes concepts each consumer must configure; it does not reject useful universal
features merely because their implementation is sophisticated. Complexity must be opt-in, bounded,
owned by a clear module, and absent from the simple import/run path.

## Failure Philosophy

The engine fails loudly at trust boundaries and returns typed failure state for execution failures.
It does not silently drop malformed output, clamp an authored fan-out, invent an unknown transition,
downgrade a provider, hide cost, or convert missing evidence into a finding.

Next: adopt it in [`getting-started.md`](getting-started.md), then read
[`misuse-risks.md`](misuse-risks.md) before designing a product integration.
