# Observability — Trace, Usage, Details, And Runtime Graphs

This note records the current observability contract for `ai_workflow_engine`. It supersedes the
earlier feedback that said severity/details were missing.

## Current Contract

The engine uses three source streams, then projects them into graph/HTML views:

- `WorkflowTraceEvent`: compact execution events. These carry node, attempt, phase, decision,
  severity, elapsed time, artifact ids, `run_id`, metadata digests, and `detail_refs`.
- `WorkflowUsageEvent`: provider accounting. These carry model/provider, operation, tokens, request
  id, success/error, and metered-vs-notional cost fields. Usage stays separate from trace.
- `ObservationDetail`: explicit internal investigation payloads. These hold full byte-free prompt,
  response, tool payload/result, planner output, memory projection, or artifact-preview data plus a
  digest.

The unification point is the view:

```text
WorkflowDefinition + WorkflowTraceEvent[] + WorkflowUsageEvent[] + ObservationDetail[]
    -> ObservationGraph -> HTML/runtime viewer
```

## Capture Modes

Internal detail capture has one effective switch:

- Off: trace events stay compact and may include digests, but no fake digest-only detail card is
  emitted.
- Full: detail records preserve the full byte-free source payload in `text`/`json_value` plus a
  digest.

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

`build_observation_graph()` filters by `run_id`, correlates trace/usage/details, and keeps metered
and notional cost separate. `observation_graph_to_html()` renders:

- an interactive investigation graph with overview/investigate/raw modes;
- node-level status, attempts, decisions, usage, details, and timeline;
- significant detail summaries first;
- raw byte-free detail JSON still available for full inspection;
- Mermaid export and table fallbacks.

Standalone JSONL consumers can use `packages/ai_workflow_viewer`.

## Operational Rules

- Do not merge trace and usage into one physical record type.
- Do not store prompt/output bodies directly in ordinary trace metadata.
- Do not emit SHA-only investigation cards when capture is off.
- Do not add product-specific observability fields to the engine core; use generic details and
  renderer projections.
- Keep unknown node/detail kinds renderable generically.
