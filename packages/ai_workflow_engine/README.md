# AI Workflow Engine

`ai_workflow_engine` is a reusable state-machine runtime for AI and deterministic work. Products
declare a workflow, register typed capabilities, and call one engine door. The engine owns
transitions, retries, fan-out, budgets, waits, trace, usage, and observation bundles; products own
domain models, provider clients, storage adapters, clocks, and side-effect delivery.

> **`engine-v0.11.9` is the release candidate; do not re-pin until it is cut.**
> `engine-v0.11.8` remains the current release. Install only from that immutable tag and its
> verified release directory until the candidate release gates close.
> The engine owner publishes wheels bound to
> immutable tags; consumer
> canaries still decide whether each product changes its deployed pin. Never depend on a live
> branch. The binding contract is the repository-level
> [`executable-workflow-engine-spec.md`](../../docs/executable-workflow-engine-spec.md).
>
> **v0.11 is a latest-only line.** The engine supports exactly one current contract: current code,
> current persisted schemas (snapshot `v0.11`, observation bundle meta v2, wait records `wait-v2`),
> current docs. There are no importers, adapters, migration shims, or dual readers — data written
> by older lines is REJECTED with an error naming the historical route: inspect it with its
> matching historical tag (including `engine-v0.11.5` for `wait-v1` records). In particular,
> v0.11.6 adopters start a new wait-coordinator namespace after settling or discarding pending
> `wait-v1` records under v0.11.5.
> Adoption is fresh: new consumers start on the current contract; existing consumers re-adopt the
> current surface rather than migrate state.
>
> **v0.11.9 candidate delta.** `ai-workflow-tools==0.6.0` adds one generic, optional
> OpenAI-compatible HTTP provider pack outside engine core. An explicit endpoint can drive text,
> images, structured tool turns, and the engine's existing agent loop against OpenAI, Ollama,
> vLLM, LM Studio, or a compatible server. The adapter inherits the engine invocation window,
> performs no hidden retry/fallback, preserves caller cancellation, normalizes cached/reasoning
> token subsets once, and keeps missing/malformed usage visibly unknown. The reusable conformance
> kit rejects five broken-adapter families. Real local Ollama qualification passed text, tools, a
> complete engine-agent tool round trip, multimodal input, timeout, usage truth, and concurrent
> invocation isolation. Engine runtime and viewer behavior are unchanged.
>
> **v0.11.8 release delta.** The optional browser-backed tools share one authenticated transport
> contract, reject missing remote credentials before HTTP, use explicit `reuse|fresh` image modes,
> preserve caller-owned idempotency across the only retryable response (`429`), and validate
> PNG/JPEG/WebP bytes plus closed freshness evidence before publication. The Anki consumer derives
> privacy-safe continuity/operation identities and publishes generated media through the engine's
> normal artifact boundary. The attended live gate passed: one two-reference generation, an
> exact-key cached replay with no new generation, and one complete Anki graph generation in the
> same continuity scope produced validated PNG artifacts and an `.apkg`. Structured provider
> failures retain only sanitized status/task/code/retry facts; outage guidance is HTTP-502-only.
>
> **v0.11.7 release delta.** All supported Claude/Codex CLI doors retain typed normalized token
> usage on success, provider failure, timeout, and caller cancellation. Codex structured JSONL uses
> the final cumulative `turn.completed`; cached input and reasoning output remain explicit subsets,
> so neither is double-counted. The engine's one usage door applies an injected, versioned
> public/proxy catalog or preserves Claude provider-reported notional, then persists the complete
> basis through result, snapshot/resume, bundle/group, and viewer. Missing/malformed usage or an
> unmatched rate remains typed unknown. Notional is API-equivalent subscription plan value, not
> billed spend and not a hard monetary stop.
>
> **v0.11.6 release delta.** A durable continuation is now bound to the complete
> `WaitHandle`, including its opaque registration-incarnation identity; a deterministic
> `wait_id` alone cannot deliver. If cancellation or a registration error is observed before
> the handle is exposed, the engine atomically settles only the registration attempt it owns.
> If an exact retry reused a registration that another caller may already hold, cancellation is
> loud rather than revoking that handle or claiming clean settlement. Exact crash retry still
> recovers the same accepted receipt. Redis/DB adapters must implement
> the v0.11.6 `WaitCoordinator` contract and pass the current distinct-instance conformance kit
> plus process-isolated transaction tests against the real store. The generic in-process kit
> cannot prove that participant state survives separate workers. Do not implement a new adapter
> against v0.11.5's delivery signatures.
>
> **v0.11.5 release delta.** `WaitHealth` gains REQUIRED current-state `failed` and
> `integrity_errors` counts (no defaults — adapters without the current health contract fail
> loudly, and a backend outage must raise rather than report zero); the conformance kit asserts
> the failed count through both producers, and the new `run_wait_integrity_conformance` helper
> proves adapter-specific corruption reporting. Releases now ship a closed
> `release-manifest-v2` bundle with command-derived test/smoke/two-build evidence and reproducible
> published-wheel identity. The release also finalizes caller-cancelled observation runs as
> `cancelled`, retaining artifacts from completed invocations before graph-state commit while
> re-raising the original `CancelledError`.
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
> source identity. Published wheel hashes live in the release bundle, not in the tag.

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

Consumers download the complete published release directory, obtain the verifier scripts from
the tag (or another already trusted pin), verify the bundle before installation, and install only
the packages their product needs. Building from source is a producer operation, not consumer
verification. The exact producer and consumer commands are in
[`docs/operations.md`](docs/operations.md#release-artifacts-and-verification-v0118).
The required pre-install door is
`python3 release_artifacts.py verify-bundle --dir /path/to/engine-v0.11.8`.

Current release matrix:

| Package | Install when |
|---|---|
| `ai-workflow-engine==0.11.8` | Always. Core builder, executor, memory, waits, observation writer. |
| `ai-workflow-tools==0.5.3` | The product uses CLI agents, the tool catalog, or media helpers. |
| `ai-workflow-viewer==0.3.2` | A developer or product service renders observation bundles. |

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
   `engine.deliver_wait_event(complete_wait_handle, event)` for durable waits. Persist the complete
   handle; a bare deterministic `wait_id` cannot authorize a continuation.
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

`engine-v0.11.0` made the line **latest-only**: strict versioned persisted contracts,
one strict viewer loader with typed status authority, a sealed current-contract oracle replacing
old-wheel equality, and removal of every compatibility fallback. The current v0.11.8 release
keeps MachineSnapshot `schema_version="v0.11"` and observation bundle meta v2, and advances wait
records to `record_schema_version="wait-v2"` with required persisted registration-attempt
identity. Old persisted data fails loudly naming its historical tag. Earlier lines added, and
v0.11 preserves behaviorally (sealed corpus: 0 behavior deltas vs v0.10.1):

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
- typed normalized CLI token usage and versioned notional pricing: Claude provider totals remain
  provider-reported, Codex inclusive cache/reasoning counters are normalized once, and unknown
  usage/rates remain explicit rather than becoming fake zero;
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
- Subscription notional is API-equivalent plan value, not billed spend and not a monetary hard
  stop. It never debits metered USD limits; call, token, worker, and execution-window limits remain
  the safety boundary.
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
