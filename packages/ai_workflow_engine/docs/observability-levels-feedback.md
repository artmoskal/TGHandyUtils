# Observability — Trace, Usage, Details, And Runtime Graphs

This note records the current observability contract for `ai_workflow_engine`. It supersedes the
earlier feedback that said severity/details were missing.

## Current Contract

The engine emits three source streams and owns their execution-time contract:

- `WorkflowTraceEvent`: compact execution events. These carry node, attempt, phase, decision,
  severity, elapsed time, artifact ids, `run_id`, compact metadata, and `detail_refs`.
- `WorkflowUsageEvent`: provider accounting. These carry model/provider, operation, tokens, request
  id, success/error, and metered-vs-notional cost fields. Usage stays separate from trace.
- `ObservationDetail`: explicit internal investigation payloads. These hold full byte-free prompt,
  response, tool payload/result, planner output, memory projection, or artifact-preview data plus a
  digest.

The durable source of truth is an observation run bundle:

```text
WorkflowDefinition snapshot/reference + WorkflowTraceEvent[] + WorkflowUsageEvent[] + ObservationDetail[]
    -> ai_workflow_viewer builds ObservationGraph/view model -> HTML/runtime UI
```

Ownership rule:

- `ai_workflow_engine`: execution facts, source record models, sinks, capture modes, byte-free detail
  records, and enough workflow definition/topology data for an external tool to reconstruct a run.
- `ai_workflow_viewer`: the `ObservationGraph`/view-model projection, static/runtime graph UI,
  HTML/CSS/JS, run chooser/index, history write/prune, JSON tree/detail modal, JSONL/SSE server, and
  future live investigation UX.
- Product integrations such as Anki, MageQA, and GoPro: policy values only, such as capture on/off,
  output log location, data-retention limit, product labels, and whether to expose the viewer artifact.

Do not put viewer behavior into product processors, and do not fatten `WorkflowEngineBuilder` with
debug artifact retention settings. Retention/history is observation data/tool policy, not workflow
execution control. Do not render HTML in the application hot path; rendered HTML is a derived view that
can be rebuilt from the run bundle.

Run-bundle records should also carry a per-run monotonic sequence (`1, 2, 3...`) once the source-bundle
refactor lands. Timestamps and UUIDs identify records, but they do not reliably order them under
same-millisecond events, reconnects, retries, or future bus transports such as NATS/Kafka. The sequence
lets the viewer replay a run deterministically without merging trace, usage, and detail into one
physical model. Keep `event_id` for identity/dedup/correlation and `sequence` for order; do not replace
UUID-style event ids with counters unless the bundle writer explicitly owns all record creation and the
chosen id is a deliberate composite such as `<run_id>:<sequence>`.

## Capture Modes

Internal detail capture has one effective switch:

- Off: trace events stay compact. Payloads are not byte-free projected or hashed, and no fake
  digest-only detail card is emitted.
- Full: detail records preserve the full byte-free source payload in `text`/`json_value` plus a
  digest; linked trace events include the matching detail digests.

The engine no longer uses per-capability privacy/observation gates for internal debug capture. If
capture is enabled, every capability/LLM worker may emit full byte-free details. Export redaction,
retention, and product privacy are separate consumer policies on top of the internal source stream.

Raw media bytes still never enter trace/detail JSON. They are represented as artifacts, paths,
lengths, and fingerprints.

## Producers

The main producers are engine-owned:

- `CapabilityRuntime` emits `tool:request` and `tool:result` trace events with optional
  `tool_payload`, `tool_result`, and `artifact_preview` details.
- `StructuredLLMNode` emits rendered prompt and LLM response details at the call site, including
  clients built through `llm_factory`.
- `LLMAgentPlanner` emits memory projection, rendered prompt, and LLM response details.
- `PromptCapturingLLMClient` remains a bring-your-own adapter for external `LLMCallable` calls.
  Do not wrap clients passed into engine-owned LLM nodes/agents; those workers self-capture and mark
  their requests so the wrapper delegates without double-emitting.

## Viewer

The standalone viewer package reads a run bundle, filters by `run_id`, correlates
trace/usage/details, keeps metered and notional cost separate, and renders:

- an interactive investigation graph with overview/investigate/raw modes;
- node-level status, attempts, decisions, usage, details, and timeline;
- significant detail summaries first;
- raw byte-free detail JSON still available for full inspection;
- Mermaid/export and table fallbacks.

`packages/ai_workflow_viewer` is the owner of projection/rendering/history concerns.
`ObservationGraph`, `build_observation_graph()`, HTML rendering, workflow diagrams, bundle reading,
and run selection live there. The engine keeps event models, capture producers, sinks, bundle-writing
helpers, and static prompt-manifest introspection only.

## Static And Live Consumption

Static and live observability must use the same source record shapes. The first implementation can be
disk-backed JSONL run bundles. A live viewer can then:

- poll the run directory every few seconds;
- tail JSONL files or use platform file notifications such as `inotify`;
- consume the same records from SSE/WebSocket, NATS, or another queue adapter.

Those are ingestion modes for the viewer, not different product contracts. The persisted bundle remains
the replay/recovery source, and any live stream should be replayable or reconnectable from it.

## Product Integration

The engine owns the observability source contract. The viewer package owns projection/rendering and
debug artifact management. Each product owns its capture policy values, log storage path, data
retention number, and whether to expose or ship the generated viewer.

For a product such as MageQA or GoPro, the normal recipe is:

1. Create durable sinks for the source streams and a run-bundle location:

   ```python
   trace_sink = JsonlTraceSink("runs/<run_id>/trace.jsonl")
   usage_sink = JsonlUsageSink("runs/<run_id>/usage.jsonl")
   detail_sink = JsonlDetailSink("runs/<run_id>/details.jsonl")
   # Also persist a workflow definition snapshot or a stable definition reference for the viewer.
   ```

2. Pass both into the product's workflow engine/runtime construction and enable full detail capture:

   ```python
   engine = (
       WorkflowEngineBuilder()
       .with_trace_sink(trace_sink)
       .with_usage_sink(usage_sink)
       .with_detail_sink(detail_sink)
       .with_detail_text_capture()
       # register capabilities/workflows...
       .build()
   )
   ```

   Product wrappers such as Anki's `AnkiGenerationGraph` can expose the same knobs directly
   (`detail_sink=...`, `capture_observation_detail_text=True`) while keeping the underlying engine API
   product-neutral.

   Do not cache one engine and swap its trace/detail/usage sinks per run. Runtime sinks, observation
   capture, and usage sinks are run-scoped state; sharing and mutating them can cross-contaminate
   concurrent runs. Build a run-scoped engine/runtime, or pass run-scoped sinks through a non-mutating
   execution API.

3. Inspect the run through the standalone viewer/tool:

   ```python
   viewer = JsonlObservationViewer.from_run_bundle("runs/<run_id>")
   Path("test-results/my-product-observations/<run_id>.html").write_text(viewer.html())
   ```

   A product may expose the run-bundle path or a link to a viewer service, but it should not render
   HTML inline during the workflow run.

Product-owned choices:

- observation log path naming (`runs/<run_id>/...`, per-session GoPro run directories, etc.);
- retention and cleanup of source logs/detail payloads;
- whether full capture is default-on during development or behind a config flag in production;
- redaction/export policy if captures leave the developer/operator machine.

Current Anki status in `TGHandyUtils`: Anki writes a per-run durable observation bundle via
`AnkiGenerationGraph`; `AnkiProcessor` does not render/copy/index HTML. Configuration:

- `ANKI_OBSERVATION_CAPTURE` controls full detail capture, default `true` while the feature settles.
- `ANKI_OBSERVATION_DIR` controls the bundle root, default `test-results/observations`.
- `ANKI_OBSERVATION_HISTORY_LIMIT` controls bundle retention, default `100`.

To inspect a run, open the bundle with `JsonlObservationViewer.from_run_bundle(...)` or the viewer
server. Products can expose a link to that tool, but should not render observation HTML inside the
workflow hot path.

Viewer-generated HTML contains two layers:

- human investigation summaries and deltas, which should be the default reading path;
- collapsed raw byte-free JSON for exact input/output evidence and replay.

Products should treat the raw JSON as serialized workflow state/object-model evidence, not as a UI
contract that users must read directly.

## Operational Rules

- Do not merge trace and usage into one physical record type.
- Do not store prompt/output bodies directly in ordinary trace metadata.
- Do not emit SHA-only investigation cards when capture is off.
- Do not prune observation directories that have not been finalized with `meta.json`; they may be active
  runs still writing source records.
- Do not tee bundled runs back into product-owned in-memory trace/detail sinks just to preserve legacy
  debug paths. The durable bundle is the source of truth; tests and viewers should read it through the
  viewer/EventSource side.
- Do not add product-specific observability fields to the engine core; use generic details and
  viewer projections.
- Do not treat rendered HTML as source of truth. Persist source records and workflow topology, then
  render from them.
- Do not render/prune/index observation HTML from the product hot path.
- Keep unknown node/detail kinds renderable generically.
