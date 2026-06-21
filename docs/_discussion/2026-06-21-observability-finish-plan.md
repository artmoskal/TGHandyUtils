# Observability — rework & finish plan (for codex)

Status: accepted rework plan; implemented and wrapper-gated for `engine-v0.5.0`
Created: 2026-06-21 18:35:42 WEST
Rewritten: 2026-06-21 19:25 WEST
Scope: `packages/ai_workflow_engine` observability capture contract, worker-produced prompt/response
capture, viewer behavior, tests, Anki proof, commit/tag preparation.
Evidence: `2026-06-21-engine-observability-design.md`,
`2026-06-21-engine-observability-implementation-plan.md`, and local code reads of
`observability_capture.py`, `engine/capabilities.py`, `engine/llm_node.py`, `engine/agent_planner.py`,
and current digest-only tests.

## Decision Record

[claude] and [codex] agree that the current observability slice is not tag-ready as-is. It has the
right broad plumbing, but the capture policy and LLM wiring are too fragmented:

- capture is controlled by several switches: `detail_sink`, `capture_detail_text`,
  `CapabilitySpec.capture_observation_text`, `CapabilitySpec.capture_observation`,
  `observation_privacy`, and per-product wrapper wiring;
- `record_observation*()` can create digest-only detail cards that look clickable but contain no
  source data;
- prompt/response capture is wrapper-based, but `StructuredLLMNode` can create LLM clients lazily from
  `llm_factory`, so register-time auto-wrapping would miss real product paths such as Anki;
- tests currently lock in digest-only detail behavior.

[codex] validation (2026-06-21): Claude's revised analysis is valid. The user said "if valid then
process"; this doc treats DR1/DR2 as accepted for the rework plan below. Permanent docs/tag still need
their own explicit approval under the collaboration rule.

Accepted decisions:

- **DR1 — internal debug capture is all-or-nothing.** Engine debug capture has one product-neutral mode:
  `off` or `full`. When full capture is enabled, the engine stores full byte-free prompts, responses,
  tool payloads/results, planner output, memory projection, and artifact previews plus digests. Raw
  image/audio/video bytes stay out of JSON and are represented by artifact refs/fingerprints/digests.
  Product/export redaction is a separate future projection, not an engine-core gate.
- **DR2 — rework before tag.** Do not commit/tag the current slice until digest-only detail behavior,
  scattered capture gates, and wrapper-only prompt capture are repaired and tests reflect the accepted
  contract.

Non-goals:

- No public/export redaction feature now.
- No token-level streaming in engine core.
- No live HTTP server inside the engine.
- No compatibility bridge for obsolete digest-only internal investigation details.

## Target Architecture

One observability facility:

```text
compact trace event + linked full byte-free detail record(s) + separate usage ledger
```

- `WorkflowTraceEvent` remains the compact execution index: node, phase, decision, status, severity,
  elapsed, digest metadata, and `detail_refs`.
- `ObservationDetail` is the internal source payload: full byte-free `text`/`json_value` plus digest
  when capture mode is `full`.
- `WorkflowUsageEvent` remains separate because it is the cost/accounting ledger; the viewer merges it
  visually with trace/details.
- The viewer never treats a SHA-only detail as useful drill-down. If capture is off, it says raw
  detail capture is off.

Core implementation shape:

- Add one `ObservationCapture` object owned by `CapabilityRuntime`.
- Replace scattered booleans/gates with one capture mode: `off` or `full`.
- Engine workers emit details at the work site:
  - `CapabilityRuntime` for tool payload/result/artifacts;
  - planner node for planner output;
  - `LLMAgentPlanner` for agent prompt/response/memory projection;
  - `StructuredLLMNode` for structured prompt/response, covering plain callable, LangChain, and
    `llm_factory` paths.
- `PromptCapturingLLMClient` remains as a BYO adapter for clients outside engine workers, not the main
  engine path.

## Work Phases, AC, Gates

### R0 — Grounding Spike: capture seams and current test locks (1-2h)

Tasks:
- List all current producers and their capture path: capability tool I/O, planner output, memory
  projection, prompt wrapper, `LLMAgentPlanner`, `StructuredLLMNode`.
- List tests that currently assert `digest_only` or per-capability privacy skips.
- Confirm exact `StructuredLLMNode` call sites for plain callable, LangChain, `llm_factory`, repair
  attempts, and vision subclass behavior.

AC:
- Written implementation note in this transient doc or the final implementation summary lists the
  files/tests that must change.
- No code behavior changes in R0.

Gate R0:
- Code read complete; no direct pytest; no permanent docs.

### R1 — Introduce `ObservationCapture` and one capture mode (2-4h)

Tasks:
- Add a small internal capture object, owned by `CapabilityRuntime`, carrying:
  `trace_sink`, `detail_sink`, `mode: off|full`, and helpers for linked trace/detail recording.
- Make `mode=off` when no detail sink or full capture is not enabled.
- Make `mode=full` when explicit internal capture is enabled.
- Preserve compact trace metadata and digest generation.
- Stop creating content-less detail records in `off` mode.

AC:
- `off`: no detail records are created by engine producers; compact trace still works.
- `full`: details have `redaction_state="none"` and populated byte-free `text`/`json_value` plus digest.
- Byte-free protections still fingerprint raw bytes, data URIs, base64-like strings, and media fields.
- Existing sinks keep their fail-loud behavior; do not wrap sink writes in broad silent catches.

Gate R1:
- Focused observability tests via `./test.sh` wrapper green.

### R2 — Remove internal per-capability capture/privacy gates (2-4h)

Tasks:
- Remove or neutralize `CapabilitySpec.capture_observation`,
  `CapabilitySpec.capture_observation_text`, `capture_artifact_previews`, and
  `observation_privacy` as internal debug-capture gates.
- Keep the public model only if needed for future export policy metadata, but it must not silently
  block internal debug capture.
- Update product wiring such as Anki so it no longer sets per-capability capture flags to make the
  viewer work.

AC:
- With full capture enabled, every invoked capability records byte-free payload/result details
  consistently.
- With capture off, no details are recorded.
- No capability can silently opt out of internal debug capture through stale privacy flags.
- Any retained `privacy`/redaction fields are clearly non-gating metadata or removed from the internal
  path.

Gate R2:
- Capability/runtime tests green via `./test.sh`; stale privacy-gate assertions removed or rewritten.

### R3 — Engine-worker prompt/response capture (3-4h)

Tasks:
- Add prompt/response capture directly in `LLMAgentPlanner` around its `LLMCallable` calls.
- Add prompt/response capture directly in `StructuredLLMNode` for:
  - plain callable path;
  - LangChain/blocking path through `invoke_metered_chat`;
  - `llm_factory` and model-profile-created clients;
  - repair attempts.
- Ensure node name, attempt, phase, run id, model/profile metadata, prompt payload, response payload,
  and errors are captured consistently.
- Keep `PromptCapturingLLMClient` as an adapter for external custom clients, with idempotent/no-double
  capture behavior if practical; otherwise document it as external-only.

AC:
- Anki-style `StructuredLLMNode(..., llm_factory=create_chat_llm)` emits prompt and response details
  without product-side manual wrapper wiring.
- Agent LLM calls emit prompt and response details without product-side manual wrapper wiring.
- Error responses are observed and then original exceptions are re-raised.
- Returned LLM responses and parsed node outputs are unchanged.

Gate R3:
- Prompt, agent planner, model-binding, and structured-node focused tests green via `./test.sh`.

### R4 — Viewer and tests: no SHA-only pseudo-details (2-4h)

Tasks:
- Update projector/viewer rendering so missing details mean "capture off/unavailable", not a raw JSON
  panel containing only a digest.
- Update significant-detail cards to show concise fields first and raw full byte-free JSON/text on
  expand.
- Rewrite digest-only tests to the accepted contract:
  - off -> no detail/raw drill-down unavailable;
  - full -> full byte-free detail plus digest.

AC:
- UI has no clickable SHA-only detail cards for internal capture.
- Raw source is available in full mode for prompt, response, tool I/O, planner output, memory, and
  artifacts.
- Digest remains visible as correlation/integrity metadata, not as the content.

Gate R4:
- Observability/viewer focused tests green via `./test.sh`.
- `git diff --check` clean.

### R5 — Anki proof, free first; paid optional (2-4h)

Tasks:
- Regenerate the free mocked Anki observation HTML after R1-R4.
- Verify the graph shows meaningful details, not digest-only placeholders.
- Optional paid/API-backed Anki run only on explicit user request.

AC:
- Free Anki HTML shows full byte-free details for engine-owned nodes that execute in the mocked flow.
- If paid run is requested, real prompt and response details appear for product LLM calls.
- Capture-off run shows no raw detail panels and clear unavailable state.

Gate R5:
- Anki focused tests green via `./test.sh`.
- Paid run is not executed without explicit user approval.

### R6 — Commit/tag preparation and durable docs gate (1-4h)

Tasks:
- Commit only the engine/viewer observability rework when tests are green.
- Do not include generated junk such as `arch-report.md` or `.importlinter.draft`.
- Prepare, but do not write permanent docs unless the user explicitly approves that durable migration.
- Tag only after code/tests/docs gate is satisfied and the user confirms tag timing.

AC:
- Engine-scoped commit(s), clean diff, wrapper tests green.
- Permanent docs state: one facility, off/full capture mode, full byte-free details under internal
  capture, usage ledger separate, export redaction separate/future.
- Tag is cut only after explicit user approval.

Gate R6:
- Relevant focused suite green, then broader wrapper suite as time permits.
- No permanent doc edit or tag without explicit user approval.

## Implementation Notes

- Prefer replacing scattered capture parameters with one runtime-owned capture object instead of adding
  another flag.
- Do not preserve digest-only internal detail behavior as a compatibility path.
- Keep raw media bytes out of state and details. "Full" means full byte-free source, not binary blobs.
- Keep usage/cost honest: do not merge `WorkflowUsageEvent` into trace/detail records.
- Use repo test wrappers only: `./test.sh`, `./test_batch.sh`, or `./test_all_batches.sh`.

## Verification Matrix

Run only through repo wrappers.

| Gate | Required verification |
|---|---|
| R0 | Code-read note in implementation summary: current producers, LLM seams, and digest-only/privacy-gate tests listed. No test run required. |
| R1 | `./test.sh unit -- packages/ai_workflow_engine/tests/test_observability.py packages/ai_workflow_engine/tests/test_trace_sinks.py --cov-fail-under=0` |
| R2 | `./test.sh unit -- packages/ai_workflow_engine/tests/test_engine.py packages/ai_workflow_engine/tests/test_observability.py --cov-fail-under=0` |
| R3 | `./test.sh unit -- packages/ai_workflow_engine/tests/test_prompt_observability.py packages/ai_workflow_engine/tests/test_agent_planner.py packages/ai_workflow_engine/tests/test_model_binding.py packages/ai_workflow_engine/tests/test_vision.py --cov-fail-under=0` |
| R4 | `./test.sh unit -- packages/ai_workflow_engine/tests/test_observability.py packages/ai_workflow_viewer/tests/test_viewer.py --cov-fail-under=0` |
| R5 free Anki | `./test.sh unit -- tests/unit/test_anki_generation_graph.py packages/ai_workflow_engine/tests/test_observability.py packages/ai_workflow_viewer/tests/test_viewer.py --cov-fail-under=0` |
| R5 paid Anki | Only with explicit user approval: `ALLOW_PAID_TESTS=1 ./test.sh integration -- tests/integration/test_anki_workflow_graph_integration.py --cov-fail-under=0` |
| R6 pre-commit | `git diff --check`, then focused gates above, then broader wrapper run (`./test.sh unit` or `./test_all_batches.sh` if timeout risk). |

Manual verification:

- Open the regenerated Anki HTML and confirm detail cards show meaningful summaries plus expandable raw
  byte-free source, not digest-only placeholders.
- Confirm a capture-off run shows raw detail unavailable instead of clickable SHA-only cards.
- Confirm no generated junk (`arch-report.md`, `.importlinter.draft`, transient screenshots, local
  reports) is included in commit/tag scope.

## Closeout State

R0-R6 are implemented. Permanent docs/tag were explicitly approved by Artem on 2026-06-21 after the
focused, paid Anki, and broad unit gates were green.
