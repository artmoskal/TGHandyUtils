# Observability — consumer feedback (severity levels + live agent trace)

> From the twilio-ai voice brain ("jarvis") consuming `ai_workflow_engine` via
> `build_llm_agent_capability`. We built a classic-leveled brain log (DEBUG/INFO/WARNING/ERROR)
> with a durable per-call `brain_events.jsonl` + a live web-UI panel. This records our assessment
> of the engine's *own* observability and the one enhancement worth making.

## Verdict: the engine's observability is good — we were under-consuming it
The engine already exposes everything a host needs to observe an agent episode:

- **Trace-sink protocol** (`TraceSink.record(event)`) with five impls: `InMemoryTraceSink`,
  `JsonlTraceSink`, `CallbackTraceSink`, `AsyncQueueTraceSink` (drop-oldest + `.dropped`),
  `TeeTraceSink`.
- **`build_llm_agent_capability(..., trace_sink=...)`** routes the episode's per-tool / per-step
  trace into the host's sink — exactly the seam we needed.
- **`AgentRunResult.steps: List[AgentToolStep]`** (per tool call), `AgentStepDecision`,
  **`WorkflowUsageEvent`** (provider/operation/tokens/cost), and `WorkflowTraceEvent.elapsed_ms`
  (per-event timing — great for spotting a slow/looping step).

We simply weren't passing a `trace_sink`; we hand-rolled seam logging instead. Fix is on our side
(wire `AsyncQueueTraceSink` → our leveled logger). **No engine change is required for this.**

## The one real gap (answers "is it multi-level?"): trace is NOT severity-leveled
`WorkflowTraceEvent` = `node, attempt, decision, error, artifacts, elapsed_ms, metadata`. Rich and
structured, with an `error` field and a `decision` state — but **no `level`/`severity`**. So every
leveled consumer (our brain log, a UI, a Graylog bridge) must hand-map event semantics → severity,
and the mapping is reverse-engineered rather than a documented contract.

### Request 1 (primary) — add a severity hint to `WorkflowTraceEvent`
Add an optional `severity: Literal["debug","info","warning","error"] = "info"` (or `level: int`)
set by the emitter, e.g.:
- `error` set / budget exhausted / tool failure → **error**
- retry / retrace / fallback / dropped → **warning**
- node `start`, intermediate steps → **debug**
- tool result, finish, decision committed → **info**

Consumers that don't care ignore it; leveled consumers (and the UI) get consistent filtering for
free instead of each re-deriving it.

**Alternative if you'd rather not add a field:** publish a stable, documented
`decision`/event-name → severity mapping and the full enum of `decision` values as a public
contract, so consumers can map reliably without reading engine source.

### Request 2 (secondary) — confirm usage streams live, not just at the end
For a live observer to show running token cost and catch a runaway loop in real time, per-step
`WorkflowUsageEvent`s should reach the **trace sink** as they happen (not only via the end-of-run
`AgentRunResult`/`WorkflowUsageSummary`). If they already do, just document it; if not, emitting a
usage trace event per LLM step would close it.

### Request 3 (optional) — size-capped tool arg/result digest in the per-tool trace payload
A short, privacy-aware digest (consistent with "bytes never enter traces") in the per-tool event
`metadata` (e.g. `bind→relay-multiopt-test`, `read_output: 3 events`) lets a live observer render
useful lines without the host re-deriving them from its own tool wrappers.

## Priority
- **Request 1**: low-effort, materially simplifies *every* leveled consumer — recommended.
- **Requests 2–3**: nice-to-haves.
- **None block us** — we consume the existing trace today; this is about ergonomics + consistency
  for leveled/live observers.
