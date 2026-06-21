# Engine Observability — Implementation Plan (buildable spec for codex)

Status: buildable PLAN, but not execution-ready until the sequencing/preflight items in §0 are closed.
Created: 2026-06-21 02:12:44 WEST
Scope: `packages/ai_workflow_engine` — observation event/detail contract, projector, renderers, and the
runtime viewer definition. Rationale + discussion: `2026-06-21-engine-observability-design.md` (read it
for the "why"; this doc is the "what/how"). Permanent home after build: a new engine
`docs/observability.md` + spec §Observability (Track U, user-approved).

## 0. Prerequisites & sequencing (READ FIRST)
- **This is post-tag work.** Per the design doc's Implementation Stance, do NOT start until the engine
  memory work is committed and tagged and the 90-file product/engine megapile is untangled
  (`2026-06-20-engine-review-followups.md`). Observability must not pile onto that uncommitted tree.
- **Land on a clean base, in its own commits.** This is an engine-only feature; it must not be braided
  with the product DI refactor (Track P).
- **Scope — DECIDED (Artem, 2026-06-21 02:17:10 WEST): build ALL phases O0–O5** (D-obs-2 "do all").
  The live viewer (O4) is **in scope** as a **separate standalone artifact** (D-obs-1), not engine core
  and not embedded in a product. `phase`/`severity`/`run_id` are **in scope from O0** (D-obs-3 — the
  larger build needs them). Order still O0→O1→O2→O3→O4→O5; nothing dropped.
- **Milestone (D-obs-3):** observability ships in the **"larger build"** and must be **done + validated
  on the Anki generation workflow** before Anki testing (see Gate O-Anki). The immediate engine-memory
  tag for MageQA is a *separate, earlier* milestone — observability rides the larger build, not that tag.
- **Code-grounded preflight before O0/O4:** codex verified the current code on 2026-06-21. Two seams
  need explicit implementation choices:
  - `run_id` propagation is not automatic today. Current state has two context ids:
    `engine_context.run_context.workflow_id` is seeded from the workflow definition id, while
    `WorkflowRunner` creates a per-run UUID at `workflow_context.workflow_id` for usage/logging. Most
    `WorkflowTraceEvent`s are emitted by capability/node code through `TraceSink` without automatic
    run-context enrichment. O0.4 must add a small trace-enrichment seam or another explicit context
    propagation mechanism; do not assume adding a field alone threads it everywhere, and do not use the
    definition id as the viewer run id.
  - live usage delivery is not present today. Usage is accumulated in `WorkflowUsageSummary` and logged
    from `usage.record_usage_event`; only trace events have `TraceSink`/`AsyncQueueTraceSink`.
    O1 can project usage post-run from `WorkflowRunResult.usage`; O4 live cost updates require a
    separate `UsageSink`/queue (kept separate from trace) or a conscious downgrade to post-run cost only.
- **Viewer artifact boundary still needs execution confirmation before creating files.** The architecture
  is settled as "separate artifact", but the concrete location/harness (`packages/ai_workflow_viewer`
  vs a standalone repo) is a high-impact repository/deployment boundary. Confirm it before O4.2 creates
  a new package/app.

## 1. Architecture invariants (binding)
1. **Events are truth, details are payloads, graph is a renderer.** Mermaid/HTML/text/manifest are
   output formats, never sources of truth.
2. **Two source streams stay physically separate** (codex, agreed): `WorkflowTraceEvent` = execution
   story; `WorkflowUsageEvent` = provider/cost ledger. Unify only at the **view** (projector),
   correlating by `node`+`attempt` (and `request_id` where present). Do NOT merge the record types.
3. **One trace event type — extend, don't fork.** Add fields to `WorkflowTraceEvent`; do NOT introduce
   a parallel `ObservationEvent` (would duplicate node/attempt/decision/metadata). [agreed; supersedes
   the design doc's `ObservationEvent` block.]
4. **Byte-free.** Raw image/audio/video bytes never enter trace OR detail records — fingerprints,
   digests, or `artifact_id` only. (Matches the existing engine privacy stance + `PromptCapturingLLMClient`.)
5. **Heavy/private payloads behind detail refs**, gated by privacy + an explicit debug-capture flag.
   The default trace payload stays small.
6. **Backward compatible.** Existing `TraceSink` consumers and `WorkflowTraceEvent` fields keep working;
   all new fields are optional with safe defaults.
7. **Generic by default.** Renderers tolerate unknown `phase`/detail `kind` and display generically, so
   new node kinds need zero renderer changes.
8. **Engine core vs viewer split (D-obs-1).** The engine ships: the event/detail contract, sinks, the
   projector, the `ObservationGraph`, static + text/JSON renderers, an async-queue sink, and a documented
   delivery adapter. The **live HTTP/SSE/WebSocket server + browser UI is a SEPARATE standalone artifact**
   (its own package/app — suggested `packages/ai_workflow_viewer` or a standalone repo) that depends on
   the engine's observability contract. It is NOT engine core and NOT embedded in a product; it is built
   and versioned on its own and consumes the same contract any product would. The engine never ships an
   HTTP server.

## 2. Reconciled contract (what to build)

### 2.1 Extend `WorkflowTraceEvent` (`models.py`)
Add, all optional/defaulted (backward compatible):
- `event_id: str = Field(default_factory=lambda: str(uuid4()))` — stable id for detail correlation.
- `detail_refs: list[str] = []` — ids of `ObservationDetail` records attached to this event.
- **(O0, in scope — D-obs-3)** `phase: Optional[str] = None` — lifecycle classifier
  (`workflow:start|node:start|llm:request|tool:request|node:decision|artifact:created|node:end|
  workflow:end|error`); `severity: Literal["debug","info","warning","error"] = "info"`;
  `run_id: Optional[str] = None` (multi-run persistence demux for the separate viewer artifact).
Keep `WorkflowUsageEvent` unchanged.

### 2.2 New `ObservationDetail` (`models.py`)
Heavy/private payload behind a ref:
`detail_id`, `event_id` (links to the trace event), `kind`
(`rendered_prompt|llm_response|tool_payload|tool_result|artifact_preview|planner_output|memory_projection`),
`privacy` (`public|internal|confidential|secret`), `redaction_state` (`none|redacted|digest_only`),
`content_type`, `text: str|None`, `json: dict|None`, `artifact_id: str|None`, `digest: str|None`.
Invariant: no raw media bytes — `artifact_id`/`digest` only for binary.

### 2.3 New `DetailSink` (`engine/capabilities.py`, mirrors `TraceSink`)
`Protocol` + `InMemoryDetailSink` + `JsonlDetailSink`. `record(detail: ObservationDetail) -> None`.
The engine writes details here; the trace event only carries `detail_refs`.

### 2.4 Projector → `ObservationGraph` (`observability.py`, new module)
`build_observation_graph(definition, trace_events, usage_events=(), details=()) -> ObservationGraph`.
Pure function. `ObservationGraph` = nodes from `WorkflowDefinition`, each annotated with: status
(from trace), attempts/decisions/retries/retrace/fallback/fast-forward (trace), elapsed (trace),
usage+cost (usage, correlated by node+attempt), `detail_refs` (trace), artifact ids. Tolerates missing
usage/details and unknown phase/kind.

### 2.5 Renderers (`viz.py` + `observability.py`)
- Re-base `workflow_to_mermaid` / `workflow_to_html` to optionally take an `ObservationGraph` overlay
  (keep the existing `WorkflowRunResult` overlay path working).
- `render_prompt_manifest` stays a static debug projection (keep in `viz.py` for now).
- Add `render_runtime_timeline(graph) -> str` (text/JSON) — the headless runtime view.

### 2.6 Migrate `PromptCapturingLLMClient` (`prompt_capture.py`)
Change WHAT it records (keep the API): emit a compact `WorkflowTraceEvent`
(`phase="llm:request"`, summary, `metadata={template_id?, model, input_tokens_estimate, prompt_digest}`)
+ an `ObservationDetail(kind="rendered_prompt", privacy=..., redaction_state=...)` whose full `text` is
populated only when debug capture is explicitly enabled (else `digest_only`). Stays byte-free.

## 3. Phases, tasks (2–4h), AC, tests

### Phase O0 — Event/detail contract + detail sink (foundation)
- **O0.1 (2-3h)** Extend `WorkflowTraceEvent` with `event_id` + `detail_refs` (defaults).
  AC: existing trace tests pass unchanged; new event has a unique `event_id`; `detail_refs` defaults `[]`.
- **O0.2 (2-3h)** Add `ObservationDetail` model + byte-free invariant (raw bytes → `artifact_id`/`digest`).
  AC: model validates; a binary detail with inline bytes fails loudly; privacy/redaction enums enforced.
- **O0.3 (2-3h)** Add `DetailSink` Protocol + `InMemoryDetailSink` + `JsonlDetailSink`.
  AC: record/round-trip; JSONL line-per-detail; no raw bytes on disk.
- **O0.4 (2-3h, in scope per D-obs-3)** Add `phase` + `severity` + `run_id` to `WorkflowTraceEvent`
  (defaults keep old events valid); add the trace-enrichment/context seam that threads the runner's
  per-run id into events so multi-run JSONL demuxes.
  AC: existing events still validate; new events carry phase/severity/run_id; JSONL across two runs is
  separable by `run_id`.
- **Gate O0:** `./test.sh unit -- packages/ai_workflow_engine/tests/test_models.py packages/ai_workflow_engine/tests/test_observability.py --cov-fail-under=0 -q` green; existing `TraceSink` consumers unaffected.

### Phase O1 — Projector + ObservationGraph
- **O1.1 (3-4h)** `build_observation_graph(...)` correlating trace+usage by node+attempt; attach detail_refs/artifacts.
  AC: over a recorded run, graph nodes carry status + usage + decisions; missing usage/details tolerated.
- **O1.2 (2-3h)** Generic handling: unknown `phase`/detail `kind` render as generic timeline entries.
  AC: an event with an unrecognized phase still appears in the graph/timeline, not dropped/erroring.
- **O1.3 (1-2h)** Projector reads `phase`/`severity`/`run_id` (added in O0.4): group by `run_id`, order
  by phase lifecycle, surface severity. AC: a multi-run event stream projects to per-run graphs.
- **Gate O1:** projector tests green; pure function (no engine import cycle — `observability.py` imports
  models/workflow only, not executor).

### Phase O2 — Migrate prompt capture to event + detail
- **O2.1 (3-4h)** `PromptCapturingLLMClient` emits compact event + `ObservationDetail(rendered_prompt)`;
  full text gated by an explicit `capture_text: bool` (default False → digest_only).
  AC: byte-free; with `capture_text=False` only a digest is stored; with `True` full text in the detail,
  never in the trace event; existing wrapper tests adapted.
- **Gate O2:** `test_prompt_observability.py` updated + green; no raw bytes; default is digest-only.

### Phase O3 — Static renderers over ObservationGraph
- **O3.1 (3-4h)** `workflow_to_mermaid`/`workflow_to_html` accept an `ObservationGraph` overlay
  (status colors, usage/cost annotations) while keeping the `WorkflowRunResult` path.
  AC: static graph renders with no run; with a graph, nodes show status + cost; existing viz tests pass.
- **O3.2 (2-3h)** `render_runtime_timeline(graph)` text/JSON headless view.
  AC: timeline lists nodes in execution order with attempts/decisions/elapsed/cost; deterministic output.
- **Gate O3:** viz/timeline tests green; renderers tolerate unknown kinds.

### Phase O4 — Live viewer as a SEPARATE artifact (in scope — D-obs-1/2)
Engine side = the delivery contract; the viewer = its own package/app (`ai_workflow_viewer`), built and
versioned separately, depending only on the engine's public observability contract.
- **O4.0 (1-2h, blocking preflight)** Decide live usage delivery and viewer artifact location/harness.
  If live cost updates are required, add a separate usage delivery seam (`UsageSink`/queue or equivalent)
  that keeps usage physically separate from trace. If not, document that cost updates are post-run only.
  Confirm whether the viewer lives in `packages/ai_workflow_viewer` or a standalone repo before creating
  files.
  AC: explicit decision recorded; no new viewer package/app is created before the boundary is confirmed.
- **O4.1 (2-3h, engine)** Live delivery contract: `AsyncQueueTraceSink` (+ a detail queue) with an
  ordering guarantee + a documented adapter for SSE/WS; lazy detail fetch by `detail_ref`/`event_id`;
  live usage queue included only if O4.0 chooses live cost updates.
  AC: a fake consumer reconstructs the graph incrementally from the queue; ordering preserved; details
  fetched on demand.
- **O4.2 (3-4h, viewer artifact)** Scaffold `ai_workflow_viewer`: an SSE/WS server that reads the
  engine's queue + JSONL and serves the contract.
  AC: streams events for a live run; reconnect replays from JSONL; imports only the engine's public
  observability contract, no engine internals.
- **O4.3 (3-4h, viewer artifact)** Browser UI rendering levels 0–4 over `ObservationGraph`: topology +
  live node state, timeline, calls/cost, expandable prompt/output details (debug-gated), generic
  fallback for unknown phases/kinds.
  AC: live node-state updates incrementally; detail panes lazy-load; unknown kinds render generically.
- **Gate O4:** engine-side contract test green (`./test.sh`); the viewer artifact has its own harness and
  ships separately from the engine.

### Phase O5 — Privacy/redaction policy + permanent docs
- **O5.1 (2-3h)** Enforce privacy defaults: details default `digest_only`; `confidential`/`secret`
  require explicit opt-in; raw media → artifact/digest. AC: default run leaks no full prompt text.
- **O5.2 (2-4h, USER-APPROVED)** Migrate durable docs (Track U): new `docs/observability.md` +
  spec §Observability. AC: docs state engine-vs-consumer split, the contract, and the debug-gate.
- **Gate O5:** privacy tests green; permanent-doc edits only after explicit user approval.

### Gate O-Anki — larger-build validation on the REAL Anki workflow (the done-trigger, D-obs-3)
Observability is "done" for the larger build only when it is demonstrated end-to-end on the live Anki
generation workflow (`services/content/anki_generation_graph.py`, which already runs on the engine).
- **O-Anki.1 (2-3h)** Run the Anki workflow with trace + usage + detail capture enabled; assert the
  projector produces an `ObservationGraph` with the Anki nodes, their decisions, per-node usage/cost, and
  `rendered_prompt` details (digest by default, full text when debug-enabled). Byte-free verified.
- **O-Anki.2 (2-3h)** Point the separate `ai_workflow_viewer` at a live Anki run; confirm levels 0–4
  render (topology → timeline → cost → prompts/outputs) and update live.
- **Gate O-Anki (milestone):** static + live viewer both show a real Anki run correctly; no raw bytes in
  trace/detail/JSONL; default capture leaks no full prompt text. This gate = observability ready for
  Anki testing in the larger build.

## 4. Viewer Definition (all planning attributes)
**Purpose:** drill from a topology view into per-node execution, cost, prompts, and outputs — live or
post-hoc. **Engine ships the data; the live server/UI is a consumer.**

| Level | Shows | Data source | Build phase |
|---|---|---|---|
| L0 Topology | nodes, transition policies, branch labels, fanout/subworkflow/evaluate/planner shapes; active node; terminal status | `WorkflowDefinition` + trace status | O3 (static) / O4 (live) |
| L1 Timeline | per-node attempts, branch decisions, retry/retrace/fallback/fast-forward, elapsed, severity | trace events | O1/O3 |
| L2 Calls & cost | capability/tool calls, artifact ids, usage tokens + cost (metered vs notional), memory projection names | usage events + trace | O1/O3 |
| L3 Prompt/output | expandable rendered prompt + LLM output, tool payload/result | `ObservationDetail` via `detail_refs` (debug-enabled) | O2/O4 |
| L4 Raw investigation | full payloads, artifact previews | details, gated by privacy/debug | O4/O5 |

**Live-update mechanics (O4):** engine emits trace events through `TraceSink`/`JsonlTraceSink`/
`AsyncQueueTraceSink`; detail records travel through the new `DetailSink`; usage remains a separate
stream and needs the O4.0 decision if live cost updates are required. The **separate
`ai_workflow_viewer` artifact** consumes those streams → browser updates node state incrementally →
detail panes lazy-load `detail_ref` payloads on expand. **Tech:** static = self-contained HTML + CDN
Mermaid (engine-side, as today); live = the **separate `ai_workflow_viewer` package/app** (SSE/WS server
reading queues/JSONL, plus a JS client). **Interactions:** click node → L1/L2; expand → L3 (if debug);
expand raw → L4 (if privacy allows). **Non-goals:** token streaming is NOT an engine feature (BYO LLM
client concern); the **engine** ships no HTTP server — that lives in the viewer artifact.

## 5. Test strategy
- **Free/default (`./test.sh unit`):** all O0–O3 + O5.1 — model validation, sink round-trips, projector
  over recorded fixtures, renderer golden outputs, byte-free/privacy assertions, backward-compat. New
  files: `tests/test_observability.py`, extend `tests/test_prompt_observability.py`, `tests/test_viz*`.
- **Gated/consumer:** O4.2 live SSE/UI lives in the consumer repo with its own harness — not in engine
  default suite.
- All engine tests via repo wrappers only (`./test.sh` / `./test_batch.sh`), never bare pytest.
- Mark engine tests `pytestmark = pytest.mark.unit`.

## 6. Risks
- **R1 — two event types creep:** if anyone reintroduces `ObservationEvent`, kill it — extend
  `WorkflowTraceEvent`. (Invariant 3.)
- **R2 — fat events:** keep full prompt/output in details, never inline in trace metadata by default
  (O2 migration). The current `PromptCapturingLLMClient` inline-text behavior is debug-only until O2.
- **R3 — engine scope creep into a web server:** the live server/UI is consumer-side (invariant 8).
- **R4 — cycle:** `observability.py` must import only models/workflow (+ capabilities for sinks), never
  executor — keep it a leaf projector.
- **R5 — sequencing:** do not start before the engine tag / megapile untangle (§0).
- **R6 — implicit context:** adding `run_id` fields without a trace-enrichment seam creates sparse,
  misleading multi-run graphs. O0.4 must prove every engine-emitted event is demuxable.
- **R7 — fake live cost:** usage is not trace. Do not pretend `AsyncQueueTraceSink` streams usage; add a
  separate usage stream if O4 promises live cost updates.

## 7. Decisions (RESOLVED — Artem, 2026-06-21 02:17:10 WEST)
- **D-obs-1 — RESOLVED:** live server/UI = **separate standalone artifact** (`ai_workflow_viewer`, own
  package/repo). Engine ships contract + sinks + projector + static/text renderers + queue + adapter;
  no HTTP server in the engine.
- **D-obs-2 — RESOLVED: do ALL** (O0–O5 + the viewer artifact). O4 is in scope, not deferred/gated.
- **D-obs-3 — RESOLVED:** `phase`/`severity`/`run_id` are **in scope from O0** (O0.4). Observability is
  part of the **larger build** and must pass **Gate O-Anki** (validated on the real Anki workflow) before
  Anki testing. Separate from / later than the immediate MageQA engine-memory tag.

No open **architecture** decisions remain on this plan: one trace event type, separate usage stream,
detail refs for heavy/private payloads, separate viewer artifact. Remaining execution gates before code:
land on a clean post-tag base, close the `run_id` propagation and live-usage delivery preflights above,
confirm the viewer artifact location/harness before O4.2, then build O0→O5 + viewer and pass Gate O-Anki.
