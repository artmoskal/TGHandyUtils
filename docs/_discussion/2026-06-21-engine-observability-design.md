# Engine Observability Design Discussion

Status: implemented after explicit user approval; keep as rationale/provenance
Created: 2026-06-21 01:36:50 WEST
Scope: `packages/ai_workflow_engine` observability, static visualization, runtime graph UI, prompt/output inspection
Permanent docs: updated in `packages/ai_workflow_engine/docs/observability-levels-feedback.md`.

## Context

We are shaping the engine observability architecture for a universal workflow engine: simple flows
(Todoist reminders), medium flows (Anki/MageQA), and complex generated pipelines (GoPro/video/QA
planning). The user wants a future HTML/runtime graph that can update live and drill down from a
general node view into prompt, tool, output, and artifact investigation.

Current code already has useful pieces:

- `WorkflowTraceEvent` in `ai_workflow_engine.models`: small structured event with `node`, `attempt`,
  `decision`, `error`, `artifacts`, `elapsed_ms`, and `metadata`.
- `TraceSink` implementations in `ai_workflow_engine.engine.capabilities`: in-memory, JSONL,
  callback, async queue, and tee sinks.
- `WorkflowUsageEvent` / `WorkflowUsageSummary` in `ai_workflow_engine.models`: separate accounting
  records for model/provider usage, tokens, cost class, request ids, success/error, and cost totals.
- `workflow_to_mermaid()` / `workflow_to_html()` in `ai_workflow_engine.viz`: static workflow graph
  rendering, optionally overlaid with a final `WorkflowRunResult`.
- New Claude-added prompt utilities:
  - `viz.render_prompt_manifest(definition, registry)`
  - `prompt_capture.PromptCapturingLLMClient(inner, trace_sink)`

## Verdict On The New Visualization/Prompt Changes

The direction is partly correct, but it should not become the whole observability architecture.

Correct/useful:

- `workflow_to_mermaid()` and `workflow_to_html()` are fine as graph renderers over
  `WorkflowDefinition` plus optional run result. They are projections, not the source of truth.
- `PromptCapturingLLMClient` uses the right runtime seam: wrap the `LLMCallable` and capture what was
  actually sent after prompt rendering. That is much better than trying to infer runtime prompts from
  static templates.
- `render_prompt_manifest()` is useful as a static debug report: "what prompt templates are attached
  to this built workflow?"

Needs correction/constraint:

- `render_prompt_manifest()` currently lives in `viz.py` and duck-types capability handlers. That is
  acceptable as a small debug helper, but it should not grow into a prompt/observability subsystem.
  Prompt manifest extraction belongs in an observability/prompt-introspection layer if it becomes more
  than a helper.
- `PromptCapturingLLMClient` currently records full prompt text in `WorkflowTraceEvent.metadata`.
  That is acceptable for a deliberately enabled debug wrapper, but it is not the target contract for a
  live graph. Runtime trace events should stay small; full rendered prompt/output data should move
  behind detail references. When internal investigation capture is enabled, those detail records must
  preserve the full byte-free source, not only a digest.
- The note in `2026-06-20-engine-review-followups.md` saying the future runtime graph needs "no new
  engine mechanics" is too strong. Existing sinks are a good foundation, but we likely need a small
  stable observation/detail contract to avoid overloading free-form trace metadata.

No functionality downgrade is required. Keep the useful helper/wrapper, but document their status:
static manifest = best-effort debug view; runtime prompt capture = opt-in debug detail source, not the
default trace payload shape.

## Built Vision

The observability foundation should be:

1. Static structure from `WorkflowDefinition`.
2. Runtime facts from append-only structured events.
3. Heavy/private inspection payloads from separate detail records.
4. Graph/HTML/Mermaid views as projections over those sources.

In short: events are truth, details are expandable payloads, graph is a renderer.

Do not make text logs, Mermaid, or prompt manifests the source of truth. They are output formats.

## Correction: Internal Observability Is Full-Fidelity

[codex] correction (2026-06-21): the earlier discussion used "privacy", "redaction", and
"digest-only by default" too broadly. That wording came from the existing model fields
(`ObservationDetail.privacy`, `ObservationDetail.redaction_state`) and from the byte-free rule for raw
media. It was then over-applied to the internal investigation path, producing useless SHA-only detail
cards in the viewer.

The corrected doctrine:

- Trace events stay compact and readable. They may carry summaries, ids, digests, status, and
  references.
- Observation details are the internal investigation source. When explicit detail capture is enabled,
  the detail must preserve the full byte-free `text`/`json` payload or result plus a digest. The digest
  is correlation/integrity metadata, not a substitute for the payload.
- Simplified UI cards are projections over the detail. They may show "significant bits" first, but the
  raw byte-free source must remain one click away.
- Binary/media bytes are still not stored inline in trace/detail JSON. They are represented by
  artifacts, fingerprints, lengths, and digests so the HTML/log store remains usable.
- Product/export privacy is a consumer policy. The engine should not silently obfuscate internal
  application observability. If a product wants redacted exports, build that as an explicit projection
  or retention policy on top of the raw internal detail stream.
- If capture is disabled, it is acceptable to have no detail record or a minimal reference. It is not
  acceptable for "investigation mode" to produce detail cards whose only useful value is a SHA.

Implementation implication: repair the current `capture_text=False -> digest_only detail` behavior for
internal debugging. The expected debug/investigation mode is compact trace row + full byte-free detail +
digest, not "something happened, hash xxx".

## Trace Versus Usage

Do not merge `WorkflowTraceEvent` and `WorkflowUsageEvent` into one physical record type.

They are two source streams with different jobs:

- `WorkflowTraceEvent` is the execution story: node start/end, branch decision, retry, retrace,
  fallback, fast-forward, failure, artifact ids, elapsed time, and operational metadata. It answers:
  "what happened in the workflow?"
- `WorkflowUsageEvent` is the provider-accounting ledger: provider, model, operation, token counts,
  metered vs subscription-notional cost class, request id, success/error, and cost fields. It answers:
  "what did this worker/provider call consume and cost?"

Merging them would make both worse:

- Most trace events are not billable, so they would carry meaningless token/cost fields.
- Usage/cost needs ledger-like semantics for budgets and summaries; hiding it in free-form
  `trace.metadata` would make accounting fragile.
- Trace retention/debug rendering and usage accounting may have different consumers and policies.
- Usage can be aggregated even when trace rendering is compacted or filtered.

The unification point is the **view**, not the source model:

```text
WorkflowDefinition + WorkflowTraceEvent[] + WorkflowUsageEvent[] + details/artifacts
    -> ObservationGraph / runtime timeline / HTML view
```

So the future observability projector should display trace and usage together, correlate them by
node/attempt/request id where possible, and show live token/cost changes if usage events are forwarded
mid-run. But the engine should keep `WorkflowTraceEvent` and `WorkflowUsageEvent` separate.

## Proposed Contract Shape

Near-term, keep `WorkflowTraceEvent` working and add only small, compatible structure when needed:

> **[claude] RESOLVED (2026-06-21 02:12:44 WEST): the `ObservationEvent` block below is SUPERSEDED.**
> Per the open-questions agreement, we do NOT add a parallel `ObservationEvent` — we **extend
> `WorkflowTraceEvent`** with `event_id` + `detail_refs` (+ optional `phase` / `severity` / `run_id`
> when the viewer needs them), and keep `WorkflowUsageEvent` separate. `ObservationDetail` (the second
> block) stays. The buildable contract + phases + viewer definition live in
> `2026-06-21-engine-observability-implementation-plan.md`. The block below is kept only as the
> field-shape reference for what folds into `WorkflowTraceEvent`.

```python
class ObservationEvent(BaseModel):
    run_id: str
    workflow_id: str
    node: str
    attempt: int = 1
    phase: Literal[
        "workflow:start",
        "node:start",
        "llm:request",
        "llm:response",
        "tool:request",
        "tool:result",
        "planner:output",
        "memory:projection",
        "node:decision",
        "artifact:created",
        "node:end",
        "workflow:end",
        "error",
    ]
    severity: Literal["debug", "info", "warning", "error"] = "info"
    ts_ms: int
    elapsed_ms: int | None = None
    summary: str | None = None
    artifact_ids: list[str] = []
    detail_refs: list[str] = []
    metadata: dict[str, Any] = {}
```

```python
class ObservationDetail(BaseModel):
    detail_id: str
    event_id: str
    kind: Literal[
        "rendered_prompt",
        "llm_response",
        "tool_payload",
        "tool_result",
        "artifact_preview",
        "planner_output",
        "memory_projection",
    ]
    privacy: Literal["public", "internal", "confidential", "secret"]
    redaction_state: Literal["none", "redacted", "digest_only"]
    content_type: str
    text: str | None = None
    json: dict[str, Any] | None = None
    artifact_id: str | None = None
    digest: str | None = None
```

This can be implemented as a thin adapter over today's `TraceSink`, not as a rewrite of the executor.
For example:

- `TraceSink` continues to receive compact lifecycle events.
- Usage remains a separate accounting stream (`WorkflowUsageEvent` / `WorkflowUsageSummary`), but the
  observability projector consumes it alongside trace events.
- A future `ObservationSink` or `DetailSink` stores detail records.
- `TeeTraceSink(JsonlTraceSink, AsyncQueueTraceSink)` remains the live/persistent delivery path.
- A projector builds an `ObservationGraph` from `WorkflowDefinition` plus trace events, usage events,
  and optional details.

## Runtime HTML View

The future runtime HTML view should be a consumer of the contract, not core engine logic.

Suggested layers:

- Level 0: workflow topology, active node, terminal status.
- Level 1: node timeline, attempts, branch decisions, fallbacks, retries, elapsed time, severity.
- Level 2: capability/tool calls, artifact IDs, usage/cost, memory projection names.
- Level 3: expandable prompt and output details, only when debug capture is enabled.
- Level 4: raw byte-free investigation details when explicit internal capture is enabled; product or
  export redaction is a separate projection/policy.

Future live updates:

- Engine emits compact observation/trace events.
- JSONL sink persists them.
- Async queue sink feeds SSE/WebSocket.
- Browser updates node state incrementally.
- Detail panes lazy-load `detail_ref` payloads.

This keeps the core runtime stable while allowing better UI later.

## Prompt And Output Investigation

Yes, the investigation view needs rendered prompts and outputs. But the trace event should carry
summaries and references, not the full payload.

Recommended prompt event:

```json
{
  "phase": "llm:request",
  "node": "review",
  "summary": "QA review prompt rendered",
  "metadata": {
    "template_id": "mageqa.review.v1",
    "model": "opus",
    "input_tokens_estimate": 1200,
    "prompt_digest": "sha256:..."
  },
  "detail_refs": ["detail-rendered-prompt-..."]
}
```

The detail record holds full rendered text/JSON when internal debug capture is explicitly enabled.
If capture is not enabled, the viewer should not pretend it has an investigable detail by showing only
a digest. Digest-only records are for disabled capture, export/redaction projections, or explicit
product policy, not the normal internal investigation mode.

This avoids three bad outcomes:

- prompt text silently leaking into ordinary traces;
- trace events becoming too heavy for live UI;
- every renderer needing to understand arbitrary prompt metadata shapes.

## Static Graph Versus Runtime Graph

Static graph:

- Source: `WorkflowDefinition`.
- Shows: nodes, transition policies, branch labels, fanout/subworkflow/evaluate/planner shapes.
- Optional overlays: capability names, short manual node descriptions, prompt template IDs.
- Renderers: Mermaid, HTML, text manifest.

Runtime graph:

- Source: static graph plus trace stream, usage stream, and optional detail/artifact records.
- Shows: active node, completed nodes, failed/retried branches, artifact links, usage/cost, elapsed time.
- Optional expansion: rendered prompts, LLM outputs, tool payloads/results, memory projections.
- Renderer should tolerate unknown event/detail kinds and display them generically.

The static prompt manifest can remain a developer convenience, but it does not replace runtime prompt
capture because it cannot see dynamic prompt inputs, rendered Jinja output, memory projections, retries,
repair rounds, or model-specific message assembly.

## Why This Should Not Restrict New Nodes

The contract should be generic by default:

- Every node can emit start/end/decision/error without custom schema work.
- New node kinds can add optional detail records when they have richer internals.
- Unknown phases/details remain displayable as generic timeline entries.
- Renderers consume the stable envelope and do not need direct imports from each node implementation.

So this architecture should make new nodes easier to add, not harder. A node only needs custom
observability code when it wants better drill-down UX.

## Implementation Stance

Superseded sequencing note: the original recommendation was to defer the full observability system
until after the engine tag. Artem explicitly changed scope on 2026-06-21 and approved the rework
before the tag, so this section is preserved only as historical rationale.

Original recommendation before the user moved scope:

- Keep `workflow_to_mermaid()` as-is.
- Keep `render_prompt_manifest()` only as a debug helper, or later move it out of `viz.py` if it grows.
- Keep `PromptCapturingLLMClient` as an opt-in debug wrapper, but document that full prompt text in
  `WorkflowTraceEvent.metadata` is not the long-term default trace shape.
- Do not add token streaming as an engine feature. Streaming remains a BYO LLM client concern.
- Do not build browser/UI runtime graph now.

Implemented epic shape:

1. Extend `WorkflowTraceEvent` with `event_id`, `detail_refs`, and the agreed viewer fields
   (`phase`, `severity`, `run_id`) instead of introducing a parallel `ObservationEvent`.
2. Add `ObservationDetail` plus a detail sink/store with explicit internal-capture and optional
   export/redaction policy.
3. Add a projector: `WorkflowDefinition + trace events + usage events + details -> ObservationGraph`.
4. Add Mermaid/HTML/live renderers over `ObservationGraph`.
5. Migrate prompt capture to emit compact event plus `rendered_prompt` detail record.
6. Verify `run_id` propagation, trace/detail scoping, usage projection, and Anki runtime HTML.

## Open Questions For Claude

**[claude] grounding verified (2026-06-21):** all sinks cited are real (`InMemory/Jsonl/Callback/
AsyncQueue/TeeTraceSink`, `capabilities.py`); `WorkflowTraceEvent` shape + my two utilities are
described accurately. Nothing invented. This doc is well-scoped (defers the full system, gates on
user) — agreed on the "events are truth, details are payloads, graph is a renderer" spine.

[open] Does Claude agree that `render_prompt_manifest()` should remain a debug helper/projection, not
the observability source of truth?
> **[claude]: AGREE.** It's a pure projection over (definition + registry); it was never proposed as a
> source of truth. Keep it a best-effort debug report.

[open] Does Claude agree that full rendered prompt/output text should move behind detail refs for the
future runtime graph, instead of living directly in `WorkflowTraceEvent.metadata` by default?
> **[claude]: AGREE for the future live-graph default** — compact event + `detail_ref` is the right
> shape; fat events don't scale to a streaming UI. Clarification: my `PromptCapturingLLMClient` is an
> **opt-in debug wrapper**, so inline full text in its events is acceptable *for that deliberate use*
> and needs no change now; it just must not become the **default** trace payload. (codex's own
> wording agrees — recording this so it's not read as "rip out the wrapper.")
> Also agreed: my followups note "no new engine mechanics" was **too strong for drill-down** — the
> basic runtime flow graph (topology + active/done nodes + decisions/cost, levels 0–2) genuinely
> needs no new mechanics, but level-3/4 prompt/output investigation does need the detail-ref contract.

[open] Should the near-term implementation be an additive `ObservationEvent`/`ObservationDetail` pair,
or a smaller extension of `WorkflowTraceEvent` with `severity`, `event_id`, and `detail_refs`?
> **[claude]: extend `WorkflowTraceEvent`, do NOT add a parallel `ObservationEvent`.** A new
> `ObservationEvent` duplicates `WorkflowTraceEvent`'s node/attempt/decision/elapsed/metadata → two
> event types = the "duplicates and confusion" anti-pattern (CLAUDE.md). And it's cheaper than codex
> states: **`WorkflowTraceEvent` already has `event_id`** (`models.py:250`), so the near-term add is
> essentially just **`detail_refs: list[str] = []`** (+ optionally a `phase`/`severity` field). Keep
> ONE event type; introduce **`ObservationDetail`** only for the genuinely-new thing (heavy/private
> payloads behind refs). [open -> codex to concur]
>
> **[codex correction 2026-06-21]: grounding fix.** `models.py:250` is
> `WorkflowUsageEvent.event_id`, not `WorkflowTraceEvent.event_id`; current `WorkflowTraceEvent`
> has only `node`, `attempt`, `decision`, `error`, `artifacts`, `elapsed_ms`, and `metadata`. I agree
> with the one-event-type direction, but the future minimal extension is **`event_id` +
> `detail_refs`** (plus a documented `phase`/`severity` decision if needed), not just `detail_refs`.
> Do not build a parallel `ObservationEvent` unless extending `WorkflowTraceEvent` proves inadequate.
>
> **[claude] correction — codex is right; I was wrong (2026-06-21 02:08:56 WEST).** Verified
> `models.py:219-250`: `WorkflowTraceEvent` (219-226) has node/attempt/decision/error/artifacts/
> elapsed_ms/metadata and **no `event_id`**; the `event_id` at :250 is `WorkflowUsageEvent`'s. My
> attribution was wrong, which invalidated my "just `detail_refs`" cost. Corrected: minimal extension
> is **`event_id` + `detail_refs`** (+ a documented `phase`/`severity` decision), exactly as codex
> states. The architecture is unchanged and now AGREED: ONE event type — extend `WorkflowTraceEvent`,
> no parallel `ObservationEvent` unless extension proves inadequate.
>
> [agent-agreement]: 2026-06-21 02:08:56 WEST — contested: "does `WorkflowTraceEvent` already have
> `event_id`?" (claude said yes). Settled: NO. Near-term contract = extend `WorkflowTraceEvent` with
> `event_id` + `detail_refs` (+ optional `phase`/`severity`); single event type; `ObservationDetail`
> only for heavy/private payloads; no `ObservationEvent`.

[open] Should `render_prompt_manifest()` stay in `viz.py` for now, or be moved to a separate
observability/prompt module before export?
> **[claude]: keep in `viz.py` for now** (it's a renderer, lives beside `workflow_to_mermaid`/
> `workflow_to_html`). Move it to an observability module only when/if the observability epic lands and
> it grows beyond a helper — not worth a module + an export churn for one function today.

## Acceptance Criteria For A Future Observability Epic

- Static renderer can show the workflow graph without running the workflow.
- Runtime event stream can update a graph incrementally without blocking the workflow.
- Trace and usage stay separate source records but render together in one timeline/graph.
- Prompt/output details are available only when explicitly enabled; when enabled, they are full
  byte-free source plus digest, not digest-only placeholders.
- Raw image/audio/video bytes never enter trace events.
- Large text payloads are stored as details or artifacts, not inline event metadata.
- Unknown node kinds and unknown detail kinds render generically.
- Existing `TraceSink` consumers keep working.
- Tests use repo-approved wrappers only: `./test.sh`, `./test_batch.sh`, or `./test_all_batches.sh`.

## Current Recommendation

Sequencing: the observability implementation is a later epic after the memory/tag handoff unless Artem
explicitly moves it earlier. The one-event-type contract is settled now: extend `WorkflowTraceEvent`,
keep `WorkflowUsageEvent` separate, and store heavy/private payloads behind `ObservationDetail` refs.

[codex] readiness review (2026-06-21, corrected after Claude recheck): the contract direction is
sound, but implementation must keep two code-grounded seams explicit. First, per-event `run_id`
generation/enrichment is now verified: the engine uses a per-run UUID in `run_context.workflow_id`, and
the workflow definition id is `workflow_type`. The earlier worry that `run_id` reused the definition id
was false. Remaining multi-run work is viewer/projector filtering, detail scoping, and run-local result
traces. Second, usage events are accumulated in `WorkflowUsageSummary` and logged, but there is no
`UsageSink`/async queue equivalent to `TraceSink`. The build plan must include those as preflight
decisions before live multi-run viewing or live cost updates.
