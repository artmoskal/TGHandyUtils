# Observability Contract

The engine records execution facts; `ai_workflow_viewer` turns those facts into a human investigation
interface. Products configure policy and storage location but do not build a second trace runtime.

See [`operations.md`](operations.md#observation-lifecycle) for run/segment lifecycle and
[`misuse-risks.md`](misuse-risks.md#observability-risks) for common mistakes.

## Record Types

The source contract intentionally has three streams:

| Record | Purpose | Typical content |
|---|---|---|
| `WorkflowTraceEvent` | Compact execution/control timeline | node, phase, attempt, status/decision, elapsed time, error, artifact/detail references |
| `WorkflowUsageEvent` | Provider economics and quota | provider/model, operation, success, tokens/images, metered or subscription-notional cost |
| `ObservationDetail` | Full internal investigation payload | rendered prompt, response, tool input/output, memory projection, artifact preview |

Trace and usage stay separate because “what happened” and “what it cost” have different truth and
aggregation rules. Full payloads stay out of compact trace metadata.

Every record has stable identity and a bundle-assigned monotonic sequence. IDs deduplicate/correlate;
sequence orders records deterministically across streams.

## Config-First Capture

Normal products configure observation once:

```yaml
observation:
  enabled: true
  bundle_dir: data/observations
  capture: full       # off | full
  artifacts: copy     # off | copy
  artifact_max_bytes: 26214400
  retention_limit: 100
```

The engine then opens, routes, finalizes, and prunes per-run bundles. Products do **not** create
JSONL sinks, tee records, swap runtime sinks per run, or call bundle writers in the normal path.

Capture modes:

- `off`: compact trace and usage remain; no detail payload projection/hashing.
- `full`: full byte-safe prompt/response/tool/memory details are linked to compact events.

Full capture is internal debugging evidence, not an export-redaction feature. Apply filesystem access,
retention, and separate export policy appropriate to transcripts/prompts. Raw media bytes still do not
enter JSON records; copied artifacts live under the bundle manifest.

## Bundle Contents

A logical run owns immutable value objects plus immutable physical segments. Each finalized segment
contains the workflow definition, metadata, compact trace/detail-envelope/usage streams, artifacts,
and terminal status. Large detail bodies are addressed by SHA-256 from compact envelopes and stored
once per logical run. Suspend/resume creates multiple segments that share that run-scoped store.

Use `ObservationReader` for exact compact or targeted body access and
`FileEventSource.read_group(run_id)` to obtain one logical lifecycle. The readers:

- selects one canonical attempt per logical segment index;
- keeps abandoned/superseded/provisional attempts inspectable;
- reports actual spend including non-canonical attempts;
- validates event identity, sequence, machine digest, and related-run consistency;
- reject older bundle schemas rather than guessing or adapting them.

Do not concatenate files or sum `meta.json` totals manually.

## Producer Ownership

- `CapabilityRuntime` records tool request/result and artifacts.
- `StructuredLLMNode` records rendered prompt and model response at the call site.
- `LLMAgentPlanner` records memory projection, prompt, response, and tool episode events.
- External/BYO clients may use `PromptCapturingLLMClient`; clients already called by engine workers
  must not be wrapped again.
- The run session enriches identity once before sinks/usage aggregation so result, snapshot, and bundle
  see the same records.

Adding a new node or capability does not require viewer-specific logging code. It emits the generic
trace/detail/usage contract through the runtime.

## Viewer

`ai_workflow_viewer` owns:

- bundle and grouped-run reading;
- graph projection, status/cost aggregation, run chooser, Related-run filtering;
- investigation/raw modes and node timelines;
- artifact links/previews and safe HTTP serving;
- static HTML and a small polling/SSE server.

The engine never imports the viewer. A production product dashboard may consume/link the bundle but
must not become the source of execution truth.

Example:

```python
from ai_workflow_viewer import FileEventSource, observation_group_to_html

group = FileEventSource("data/observations").read_group(run_id)
html = observation_group_to_html(group)
```

Runnable version: [`examples/observed_workflow.py`](examples/observed_workflow.py).

## Investigation Semantics

- Declared workflow nodes use typed engine terminal status.
- Activity whose node ID is outside the definition is labeled **EXTERNAL**. Without persisted parent
  metadata the viewer does not invent an edge.
- Mixed external events show accepted/failed tallies rather than last-event-wins “completed.”
- Metered and subscription-notional costs are shown separately; unknown cost remains unknown.
  Subscription events retain normalized cache/reasoning quantities and the exact provider-reported
  or configured catalog/rate basis. The viewer projects these persisted facts and never reprices.
- Archived artifacts are manifest-allowlisted. Raster images may preview inline; active/unknown
  content is downloaded rather than executed at the viewer origin.
- Raw JSON remains available but human summaries and significant fields are the default path.

## Static And Live Consumption

Static HTML, directory polling, file notifications, SSE, WebSocket, and future NATS/Kafka adapters are
ingestion modes over the same records. The persisted bundle is the replay/reconnect source. A live
stream does not become a second schema or control channel.

## Product Integration Checklist

1. Configure observation in application YAML/typed config.
2. Give the bundle directory appropriate permissions and retention monitoring.
3. Expose `result.observation_bundle_path` or a viewer link to operators.
4. Keep engine result/status as product control truth.
5. Test success, partial, failure, suspension, resume, and artifact rendering.
6. Verify concurrent runs never share mutable sink/session state.
7. Treat bundle content as sensitive internal evidence.
8. Keep artifacts and durable wait storage lifecycle distinct.

## Operational Rules

- Never merge trace, usage, and details into one record model.
- Never use observation or memory to choose transitions.
- Never emit digest-only fake detail cards when capture is off.
- Never prune in-flight directories as if they were finalized history.
- Never render HTML in the workflow hot path.
- Never add product-specific fields to core events when generic detail metadata suffices.
- Keep unknown node/detail kinds generically inspectable.
- Preserve source records; rendered HTML is disposable and rebuildable.
