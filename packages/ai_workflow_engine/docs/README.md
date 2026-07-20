# AI Workflow Engine Documentation

This directory describes the `engine-v0.11.7` release candidate; `engine-v0.11.6` remains the
current immutable release until the candidate is tagged. It is written for both
engineers and coding agents: each file has one responsibility, links to the next action, and avoids
embedding release history in current instructions.

## Reading Paths

### First-time adopter

0. **Latest-only rule:** the current line supports exactly ONE contract — current code, current
   persisted schemas, current docs. There are no migration guides in this package: adoption is
   fresh, and data written by older lines is rejected with an error naming its historical tag
   (inspect old data with that tag's own docs and viewer).
1. [`getting-started.md`](getting-started.md) — packages, pinning, first workflow, configuration,
   and first contract tests.
2. [`concepts.md`](concepts.md) — machine, capability, state, result, memory, evidence, and ownership.
3. [`misuse-risks.md`](misuse-risks.md) — mistakes that create a second runtime or silently weaken
   guarantees.
4. One product handoff: [MageQA](mageqa-handoff.md), [GoPro](gopro-handoff.md),
   [SlackAzzCovered](slackazz-handoff.md), or [voice](voice-brain-handoff.md).

### Runtime operator

1. [`operations.md`](operations.md) — run lifecycle, local/durable waits, observation groups,
   retention, failures, and upgrades.
2. [`observability-levels-feedback.md`](observability-levels-feedback.md) — trace, usage, details,
   artifacts, and viewer behavior.
3. [`examples/observed_workflow.py`](examples/observed_workflow.py) and
   [`examples/durable_wait.py`](examples/durable_wait.py).

### Framework contributor or product requesting a feature

1. [`concepts.md`](concepts.md#ownership-and-layers) — decide which layer owns the need.
2. [`extension-lifecycle.md`](extension-lifecycle.md) — request template, review states, delivery,
   release, adoption, and feedback closure.
3. Repository-level [binding spec](../../../docs/executable-workflow-engine-spec.md) and
   [architecture](../../../docs/workflow-engine-architecture.md).

## Source-Of-Truth Map

| Question | Canonical source |
|---|---|
| What the engine is and its non-negotiable principles | [`concepts.md`](concepts.md) for consumers; repository [architecture](../../../docs/workflow-engine-architecture.md) for design |
| What is built, deferred, or rejected | [Binding spec section 13](../../../docs/executable-workflow-engine-spec.md#13-status--exists-vs-intended-read-this-before-building-on-a-promise) |
| Current installation and first run | [`getting-started.md`](getting-started.md) |
| Runtime lifecycle and operational recovery | [`operations.md`](operations.md) |
| Trace/detail/usage/bundle/viewer behavior | [`observability-levels-feedback.md`](observability-levels-feedback.md) |
| Product-specific ownership and adoption gates | The matching handoff in this directory |
| New framework request or adoption feedback | [`extension-lifecycle.md`](extension-lifecycle.md) |
| Historical implementation decisions | Binding spec appendix and Git history, not consumer guides |

If documents disagree, the binding spec wins for engine behavior, the product handoff wins only for
product-owned mapping, and executable code/tests win over stale prose. Report the contradiction using
the documentation-defect path in [`extension-lifecycle.md`](extension-lifecycle.md#documentation-defects).

## Stable Vocabulary

- **Run:** one logical engine execution, including its suspend/resume segments.
- **Related-run ID:** optional correlation key joining separate runs for one case; never execution
  identity.
- **Capability:** registered worker behind the one engine contract.
- **Workflow:** serializable machine of nodes and transitions.
- **Observation bundle:** durable record of definition, trace, details, usage, artifacts, and status.
- **Product loop:** scheduling/event ingress that calls public engine doors; it is allowed. A loop
  that chooses nodes, retries workers, or simulates transitions is forbidden orchestration.

## Documentation Maintenance Rule

Shared mechanics are documented once in the generic guides. Product handoffs contain only product
mapping, package choice, risks, and acceptance gates. A feature commit updates code, contract tests,
the generic guide, and affected handoffs together. Release history stays out of active instructions.
