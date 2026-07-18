# AI Workflow Engine

`ai_workflow_engine` is a reusable state-machine runtime for AI and deterministic work. Products
declare a workflow, register typed capabilities, and call one engine door. The engine owns
transitions, retries, fan-out, budgets, waits, trace, usage, and observation bundles; products own
domain models, provider clients, storage adapters, clocks, and side-effect delivery.

> **`engine-v0.11.4` is the current release.** Pin immutable tags and build wheels; consumer
> canaries still decide whether each product changes its deployed pin. Never depend on a live
> branch. The binding contract is the repository-level
> [`executable-workflow-engine-spec.md`](../../docs/executable-workflow-engine-spec.md).
>
> **v0.11 is a latest-only line.** The engine supports exactly one current contract: current code,
> current persisted schemas (snapshot `v0.11`, observation bundle meta v2, wait records `wait-v1`),
> current docs. There are no importers, adapters, migration shims, or dual readers — data written
> by older lines is REJECTED with an error naming the historical route: inspect it with its
> matching historical tag (`engine-v0.10.1` and earlier keep working for their own data forever).
> Adoption is fresh: new consumers start on the current contract; existing consumers re-adopt the
> current surface rather than migrate state.
>
> **v0.11.4 release delta.** `RunExecutionRequest` gives callers a typed, invocation-local
> deadline for the complete graph. It narrows configured limits without mutating cached profiles,
> survives local suspend/resume, applies across retraces, and includes cancellation cleanup inside
> the declared wall time. Final status/error truth follows each node's latest attempt while every
> attempt remains visible in history. **v0.11.3 added** the explicit
> plan/machine/memory/model-profile binding contract, fan-out applies it per item, and subworkflows
> apply plan/machine cards while rejecting capability-only bindings. Retrace provenance is
> target-only: a retraced parent's provenance never reaches child-workflow invocations through
> either child door, while child-internal retrace keeps its own. Model-binding trace truth is
> invocation-local: `model_used` comes from the invocation's own usage events (bound fan-out items
> carry a deterministic `fanout_item_index`), never from shared-summary windows that concurrent
> siblings interleave — aggregate accounting is untouched. `ai-workflow-tools==0.5.1`
> also attaches staged images to `codex exec` through native `--image`; Claude keeps its separate
> path-scoped Read transport. A live two-image canary consumed both attachments in order and
> recorded exactly one engine usage event. The sealed behavior corpus is unchanged. **v0.11.2 was
> the corrective release over v0.11.1.** Planner retrace retains task outputs from
> earlier rounds when later work is merged, and successful tasks with no output no longer publish
> dangling output references. Public APIs and persisted schemas are unchanged. v0.11.1 introduced
> the runtime ownership decomposition: runtime compilation,
> node/scheduling, suspension/result, bundle, and viewer responsibilities have focused internal
> owners; public APIs and persisted schemas are unchanged. The annotated tag is the canonical
> release manifest for the exact source commit and reference wheel hashes.

## Start Here

| Need | Read |
|---|---|
| Choose the correct package and first workflow | [`docs/getting-started.md`](docs/getting-started.md) |
| Understand the engine model and ownership boundaries | [`docs/concepts.md`](docs/concepts.md) |
| Operate runs, waits, observations, upgrades, and failures | [`docs/operations.md`](docs/operations.md) |
| Avoid common architecture and runtime misuse | [`docs/misuse-risks.md`](docs/misuse-risks.md) |
| Request a framework feature or report adoption feedback | [`docs/extension-lifecycle.md`](docs/extension-lifecycle.md) |
| Navigate all framework and consumer documentation | [`docs/README.md`](docs/README.md) |
| Run small executable examples | [`docs/examples/README.md`](docs/examples/README.md) |

Product adopters should then read exactly one delta guide:

- [MageQA](docs/mageqa-handoff.md)
- [GoPro](docs/gopro-handoff.md)
- [SlackAzzCovered](docs/slackazz-handoff.md)
- [Voice brain](docs/voice-brain-handoff.md)

## Install

Build from the tag because this monorepo is not published to PyPI:

```bash
git clone <TGHandyUtils-repository> /tmp/tghandy-engine
git -C /tmp/tghandy-engine checkout --detach engine-v0.11.4
python -m pip wheel --no-deps -w ./vendor \
  /tmp/tghandy-engine/packages/ai_workflow_engine
python -m pip install ./vendor/ai_workflow_engine-0.11.4-py3-none-any.whl
python -c "import ai_workflow_engine as e; assert e.__version__ == '0.11.4'"
```

Optional packages:

| Package | Install when |
|---|---|
| `ai-workflow-engine==0.11.4` | Always. Core builder, executor, memory, waits, observation writer. |
| `ai-workflow-tools==0.5.1` | The product uses CLI agents, the tool catalog, or media helpers. |
| `ai-workflow-viewer==0.3.1` | A developer or product service renders observation bundles. |

Record the engine tag, source commit, and wheel SHA-256 in the consumer repository. A tag already
consumed by another repository is frozen; fixes require a new tag.

## Minimal Workflow

```python
from pydantic import BaseModel
from ai_workflow_engine import WorkflowBuilder, WorkflowEngineBuilder

class Input(BaseModel):
    text: str

class Output(BaseModel):
    normalized: str

def normalize(_context, payload: Input) -> Output:
    return Output(normalized=" ".join(payload.text.split()))

builder = WorkflowEngineBuilder()
builder.register_capability(
    "normalize", normalize, kind="deterministic",
    input_model=Input, output_model=Output,
)
builder.register_workflow(WorkflowBuilder("normalize_text").step("normalize").build())
engine = builder.build()

result = await engine.run("normalize_text", Input(text="too   many spaces"))
assert result.status == "completed"
assert result.output.normalized == "too many spaces"
```

This is the complete simple path. Do not add a product supervisor, node runner, retry loop, or
trace wrapper around it. More complex workflows opt into more engine facilities without changing
the execution door.

## Core Contract

1. **Declare the machine.** `WorkflowBuilder` creates serializable nodes and transitions.
2. **Register workers.** Capabilities declare schemas, kind, side effects, timeout, and cost class.
3. **Inject infrastructure.** Configuration, model clients, stores, coordinators, and sinks enter
   through builders/adapters; the engine never discovers product secrets.
4. **Run through the engine.** Use `engine.run`, `engine.resume` for local waits, or
   `engine.deliver_wait_event` for durable waits.
5. **Read the result and observation bundle.** The result is the execution contract; observations
   explain it but never control it.

The same worker contract covers deterministic Python, API LLMs, local models, `claude -p`,
`codex exec`, browser/MCP agents, media tools, humans, and subworkflows. New modalities belong in
capabilities or optional tool packs, not in a second runtime.

## Configuration Rule

Application behavior belongs in YAML or typed application configuration. Environment variables are
deployment overrides and secret references, not the primary configuration language. Provider
credentials never belong in engine YAML, workflow state, prompts, traces, or observation bundles.

See [`getting-started.md`](docs/getting-started.md#configuration) for the supported precedence and a
minimal profile.

## Release Highlights

`engine-v0.11.0` makes the line **latest-only**: strict versioned persisted contracts
(MachineSnapshot `schema_version="v0.11"` with required typed identity; observation bundle meta v2
with closed status vocabulary and segment identity; wait records `record_schema_version="wait-v1"`),
one strict viewer loader with typed status authority, a sealed current-contract oracle replacing
old-wheel equality, and removal of every compatibility fallback — old persisted data fails loudly
naming its historical tag. Earlier lines added, and v0.11 preserves behaviorally (sealed corpus:
0 behavior deltas vs v0.10.1):

- an engine-owned, **enforced** execution window (soft work deadline, hard timeout, completion
  reserve, named limiting sources/clamps) intersecting task request, capability limit, remaining run
  budget, and parent window; a timed-out capability is a truthful **`partial`**, never a laundered
  `failed` or false-green success;
- truthful terminal **`partial`** planner tasks (error/output/artifacts preserved, never rewritten to
  `done`), propagated through fan-out, nested plans, resume, trace, bundles, and workflow status;
- declared interruptibility (`process` / `cooperative` / `none`) so the engine never claims to
  hard-stop an uninterruptible inline handler; CLI/console/external-process doors inherit the same
  soft/hard window and record work, terminate, and reap/result-settlement time (no hidden 600s); the
  shared process owner bounds stdin delivery and terminates the complete spawned process tree;
- typed retrace provenance delivered to the retraced capability and projected — with the execution
  window and timeout reason — at the node in the generic viewer;

and, from earlier releases:

- declared step, branch, evaluate/retrace/fallback, bounded fan-out, subworkflow, planner, and human
  nodes;
- `FlowArtifact` v1.5a for validated AI-authored step/branch/evaluate/fan-out machines over already
  registered capabilities;
- strict prompt files, per-node model and memory selection, replay, evidence/artifact references;
- metered versus subscription-notional cost accounting and cumulative budgets;
- config-first observation bundles and the separate `ai_workflow_viewer` reader;
- local and durable waits, product-owned `WaitCoordinator` storage, at-least-once delivery with
  idempotent effects, and grouped observation segments;
- optional `correlation_id`, shown as Related-run ID, for finding separate runs belonging to one
  case without merging their execution identity.

Full built/deferred status is maintained only in
[`executable-workflow-engine-spec.md` section 13](../../docs/executable-workflow-engine-spec.md#13-status--exists-vs-intended-read-this-before-building-on-a-promise).

## Non-Negotiable Safety And Honesty

- Unknown capabilities, illegal transitions, unsafe state, denied side effects, malformed output,
  and exceeded budgets fail loudly.
- Raw bytes and rendered base64 media do not enter workflow state, memory, checkpoints, trace, or
  prompts. Use `ImageInput` for transport and `EvidenceRef`/artifacts for persistence.
- Memory and observability are inputs and records, never transition control.
- Subscription workers report notional or unknown cost; they are never represented as free API
  calls.
- Durable delivery is **at-least-once with deduplication and idempotent effects**, never
  exactly-once.
- The engine never starts a hidden timer or daemon. Products deliver due/stalled events through the
  public wait door.

## Development And Verification

In this repository, run tests only through the root wrapper:

```bash
./test.sh unit -- --cov-fail-under=0 -q packages/ai_workflow_engine
```

Consumer repositories should add contract tests for their adapter, pin, provider factory,
state-status projection, and absence of a product orchestration loop. Product-specific checklists
are in the handoffs.

## Feedback

Do not patch a missing universal mechanic into product orchestration. Use the request lifecycle in
[`docs/extension-lifecycle.md`](docs/extension-lifecycle.md): document the named workload, prove the
current seam is insufficient, classify the owning layer, provide acceptance evidence, and keep the
product on a pinned release until a reviewed tag exists.
