# Engine Review Follow-ups — Fail-Loud and Prompt Contracts

Status: active follow-up backlog
Created: 2026-06-20
Source: clean-code review relayed by Artem; codex triage against local code on 2026-06-20.
Permanent guardrail: `docs/executable-workflow-engine-spec.md` §5, "Implementation hygiene
guardrails."

Purpose: keep concrete code-review findings from being lost without overstating them as completed.
These are implementation cleanup items before the next engine acceptance/release gate. This doc also
records which fixes block memory and which should wait until after the minimal memory slice.

## Sequencing: Before vs After Memory

**Do before memory implementation:**
- Fail-loud hygiene from this doc: narrow validation catches, visible malformed-plan reasons,
  prompt-template exclusivity, and safe multimodal content handling.
- Minimal engine cycle break for memory-facing runtime state: move executor-private state keys and
  handler-registration glue out of `executor.py` so node handlers do not import executor internals.
  This is the seam memory will otherwise couple to.

**Do during the minimal memory slice:**
- `AgentMemory` seam, `FullReplayMemory`, `ImageEvictingMemory`, `MemoryStore` contract, and
  `InMemoryMemoryStore` only. Keep `WindowedMemory`, `StructuredStateMemory`, `CompactingMemory`,
  semantic search, durable T2 backends, and LangGraphStore deferred.

**Do after minimal memory, before larger authoring expansion:**
- Decompose `WorkflowDefinition.validate_graph` and `build_definition_from_artifact`. They are the
  largest quantified validator hotspots, but refactoring them before memory is avoidable churn.
  They should happen before FlowArtifact v1.5 / broader process-authoring work because those features
  will extend the same validation surface.
- Simplify `_apply_eval` with directive/effect dispatch if evaluator behavior is being touched.

**Defer outside the engine-memory path:**
- Root app `core` ↔ `database` cycle and product-service complexity. Real debt, but not an engine
  memory blocker unless product composition becomes part of the memory implementation.
- `ai_workflow_tools.media.image_generation` complexity. Tools package is cycle-clean; split provider
  request/response/cost helpers when media work resumes.

**Guardrail setup:**
- Do not commit a red `tach` baseline as if it were enforcement. Either fix the engine cycle and then
  enforce `tach check`, or commit an observe-mode baseline with the allowed cycle explicitly named.
- Add an import-linter forbidden contract for `ai_workflow_engine` importing `ai_workflow_tools` or
  product/app packages after the immediate cleanup; it formalizes the existing L0-purity guard.

## Implementation Plan: Phases, Gates, Tests

<!-- [claude] 2026-06-20: inserted Track S (spikes) before Track M; corrected order to match the
     section body (V before U); recorded D3 = T1-first / build-complete (MageQA waits for the full
     polished impl so every project reuses one good subsystem, per Artem). -->
Default implementation order: **Track E → Track S → Track M → Track V → Track U**. Track S is the
spike/investigation gate that resolves the two open unknowns before any memory code. Track U is the framework
user/adoption documentation pass required before handing the engine to GoPro/MageQA-style consumers.
Track P is product architecture debt and must not be interleaved with engine-memory work unless it
has a separate owner/branch. Each task below is intended to fit a 2-4 hour implementation window;
split again if the code forces a broader change.

Investigation rule: implementation spikes are embedded as time-boxed checkpoints inside the task
they de-risk; memory-wide unknowns live in Track S because they gate the whole memory slice. A spike
must produce one of: exact files/symbols to edit, a failing or characterization test, a yes/no
decision for an optional task, or a documented stop condition. Do not spike already rejected
directions (`WorkerInvocation` mega-type, central worker OOP tree, langmem in core, browser/no-API
executor, ProcessArtifact v2) unless a concrete implementation failure reopens them.

### Track E — Engine prerequisite cleanup before memory

Scope: clean the fail-loud defects and the memory-facing executor/node seam. Do **not** add memory,
decompose the large validators, clean root-app architecture, move public models/contracts, or add a
literal unified-envelope/worker class in this track.

**E0 — Preflight / baseline (0.5-1h).**
- Confirm worktree state and list unrelated user/agent changes.
- Identify focused tests for edited files before touching code.
- Do not commit generated `tach.toml`, `.importlinter`, or static-analysis reports unless explicitly
  approved.
- Spike S0 output: list unrelated dirty files, generated/static-analysis files to leave untouched,
  and the focused test command set for E1-E3.

AC/checks:
```bash
git status --short
./test.sh unit -- packages/ai_workflow_engine/tests/test_agent_planner.py packages/ai_workflow_engine/tests/test_planning.py packages/ai_workflow_engine/tests/test_vision.py packages/ai_workflow_engine/tests/test_engine.py --cov-fail-under=0 -q
```

**E1 — Fail-loud image/evidence extraction (2-3h).**
- Spike S1a first: characterize the current silent image/evidence downgrade with a focused test or
  a precise code-path note before editing.
- In `engine/agent_planner.py`, replace broad validation probes with narrow validation handling.
- Loader/runtime failures still surface.
- Near-miss image/evidence-shaped payloads preserve a visible reason via warning, trace, or result
  metadata; ordinary arbitrary dict misses do not become noisy warnings.

AC/tests:
- Invalid image/evidence-shaped payloads do not disappear silently.
- No broad `except Exception` remains in this probe path unless it logs/preserves the recoverable
  reason.
```bash
./test.sh unit -- packages/ai_workflow_engine/tests/test_agent_planner.py --cov-fail-under=0 -q
```

**E2 — Planner artifact failure visibility (2-3h).**
- Spike S1b first: characterize the malformed-plan path with a focused failing test or exact
  validation-error propagation point before editing.
- In `nodes/planner.py`, malformed `PlanArtifact`-like outputs surface why validation failed instead
  of becoming unexplained `None`.
- Keep valid "no plan found" behavior distinct from "plan-shaped output failed validation."

AC/tests:
- Malformed planner output asserts the validation reason is visible in result error, trace, or log
  metadata.
- Valid non-plan output still follows the existing no-plan path.
```bash
./test.sh unit -- packages/ai_workflow_engine/tests/test_planning.py --cov-fail-under=0 -q
```

**E3 — Vision content and prompt-mode guards (2-3h).**
- In `vision.py`, remove the no-op conditional while preserving valid list content parts; do not
  coerce multimodal content lists to `str`.
- In `engine/llm_node.py`, add a loud mixed-prompt-mode error and a concise constructor docstring.

AC/tests:
- Existing multimodal list content parts remain lists after image attachment.
- `StructuredLLMNode(prompt_template=..., static_prompt_template=..., dynamic_prompt_template=...)`
  raises `ValueError`; half-split prompt mode still raises.
```bash
./test.sh unit -- packages/ai_workflow_engine/tests/test_vision.py packages/ai_workflow_engine/tests/test_engine.py --cov-fail-under=0 -q
```

**Gate E-A — fail-loud review stop.**
- E1-E3 focused tests pass.
- Engine package suite passes through the repo wrapper.
- Stop for review if any fix requires weakening fail-loud behavior, changing public payload schemas,
  or suppressing a validation failure instead of exposing it.
```bash
./test.sh unit -- packages/ai_workflow_engine/tests/ --cov-fail-under=0 -q
git diff --check
```

**E4 — Extract executor runtime-state keys (2h).**
<!-- [claude] 2026-06-20: renamed this E-internal "Spike S2" → "mapping prep (E4a)" to avoid the name
     collision with the new Track S / S2 (GoPro ImageEviction). Also updated the E5 cross-reference. -->
- Mapping prep (E4a) first: map all node imports of executor internals and list the symbols to move.
  Output must state whether E4 alone removes the memory-facing dependency or whether E5 is needed.
- Move executor-private state keys/lightweight helpers used by node handlers into a neutral internal
  module, for example `runtime_state.py`.
- Update node handlers to import that neutral module instead of executor private names.
- Do not preserve old private import paths unless the user explicitly asks for compatibility.

AC/tests:
- Node handlers no longer import executor internals.
- Branch/evaluate/planner/human/subworkflow behavior stays unchanged.
```bash
rg 'from ai_workflow_engine\.executor import|import ai_workflow_engine\.executor' packages/ai_workflow_engine/ai_workflow_engine/nodes
./test.sh unit -- packages/ai_workflow_engine/tests/test_engine.py packages/ai_workflow_engine/tests/test_state_machine.py packages/ai_workflow_engine/tests/test_planning.py --cov-fail-under=0 -q
```

**E5 — Node handler registration seam, only if still needed (2-4h).**
- Execute only if E4a/E4 proves runtime-state extraction is not enough. Move handler-registration
  glue into a neutral module so executor depends on registered handlers instead of nodes importing
  executor.
- Keep node-kind contracts and execution behavior unchanged.
- Do not start the larger contracts-leaf extraction here.

AC/tests:
- No node-handler dependency on executor internals remains.
- Scheduling, evaluate retry/retrace/fallback, fanout, planner, subworkflow, human, and budget tests
  still pass.
```bash
./test.sh unit -- packages/ai_workflow_engine/tests/test_engine.py packages/ai_workflow_engine/tests/test_state_machine.py packages/ai_workflow_engine/tests/test_planning.py packages/ai_workflow_engine/tests/test_scheduling_integration.py --cov-fail-under=0 -q
```

**Gate E-B — memory start gate.**
- Gate E-A is green.
- E4/E5 focused tests pass.
- Engine package suite passes through `./test.sh`.
- Full repo suite passes before memory starts, unless the user explicitly accepts a narrower gate.
```bash
./test.sh unit -- packages/ai_workflow_engine/tests/ --cov-fail-under=0 -q
./test.sh unit -- --cov-fail-under=0 -q
git diff --check
```

Execution notes:
- [codex] 2026-06-20: E0 confirmed unrelated dirty docs/static-analysis files and used the
  wrapper-only focused baseline for agent planner, planner, vision, and engine tests.
- [codex] 2026-06-20: E1-E3 implemented fail-loud warning/error visibility for image/evidence and
  planner validation probes, preserved multimodal vision content parts, and added the mixed prompt
  mode guard. Focused tests and Gate E-A engine package run passed.
- [codex] 2026-06-20: E4a found node handlers only needed executor runtime-state key constants;
  E4 moved them into `ai_workflow_engine._runtime_state`. E5 was skipped because no handler
  registration seam was needed for this dependency.
- [codex] 2026-06-20: Gate E-B passed through `./test.sh`: engine package suite `224 passed`; full
  repo suite `739 passed`, `0 failures`, `0 errors`.
- [codex] 2026-06-20 15:35:33 WEST: accepted Claude's review split: the coarse tach cycle remains
  deferred to contracts-leaf extraction; the local `_images_from_output` complexity bump was fixed
  by extracting image/evidence coercion helpers. Focused agent planner test passed (`13 passed`).

<!-- [claude] 2026-06-20: ADDED Track S below. The plan went straight E → M with no phase for the
     two open unknowns I flagged (memory×replay determinism; MageQA's unconfirmed T2 need). S0 is a
     hard gate before M; S1 informs the full T2 design but does not block T1. -->
### Track S — Spikes & investigations before memory  <!-- [claude] added 2026-06-20 -->

Scope: resolve the two open unknowns gating a safe memory build. Timeboxed; each produces a
decision/answer + (for S0) a test, NOT production memory code.

**Decision baked in (Artem, 2026-06-20):** MageQA will WAIT for the full, polished memory subsystem
so every project reuses one good implementation — so build it RIGHT (T1-first → complete polished
memory), NOT a rushed MageQA-specific T2. This resolves **D3 = T1-first / build-complete**; S1 below
is design *input* for the eventual full T2, not a delivery gate.

**S0 — Memory × replay/determinism spike (~0.5 day). HARD GATE before Track M.**  <!-- [claude] added -->
- Prototype a run with a NON-default memory (e.g. `ImageEvicting`) active, then `snapshot → resume`;
  confirm it replays identically. Probe whether later compaction could break replay.
- Output: the locked invariant — *"memory is input, never control; a recorded run replays
  identically regardless of live memory state"* — plus a determinism test Track M must keep green.
- **Gate S-A:** do not start Track M until that invariant + test exist. If the prototype shows memory
  CAN break replay, stop and redesign the seam before M1. (Closes the gap from the memory-design
  doc's "for codex pass 2" challenge #3, which was never resolved by a task.)

Execution notes:
- [codex] 2026-06-20 15:35:33 WEST: Added the first
  `test_memory_projection_is_input_only_for_snapshot_replay_determinism` characterization:
  a non-default image-evicting prompt projection changes LLM input, snapshot stores only
  `EvidenceRef`, and a fresh resume with a failing live planner still fast-forwards the recorded
  agent node. Locked invariant: memory may shape prompt input, but must never own control state or
  replay decisions. Focused state-machine test passed (`20 passed`).
- [codex] 2026-06-20 21:23:17 WEST: Tightened S0 proof after review: the test now uses
  `{"mode": "image_evicting", "keep_last_images": 0}` and asserts the actual
  `[memory:image_evicted]` prompt marker before `MachineSnapshot.to_json() ->
  fresh_engine.resume(...)`. No agent, LLM, or tool replay occurs after resume. Focused wrapper
  run for memory config + S0 + ImageEvicting behavior passed (`5 passed`).

**S1 — MageQA T2 access-pattern investigation (consumer handoff, not code).**  <!-- [claude] added -->
- Get from MageQA the concrete orchestrator-memory need: which records, exact-match vs semantic
  recall, concurrent orchestrations, record shape, retention.
- Output: a confirmed requirement that shapes the FULL T2 `MemoryStore` design. Informs, does NOT
  block, Track M (T1). Since MageQA waits for the full impl, this is design input — gather it before
  the T2 build, not before T1.

**S2 (optional) — GoPro `ImageEviction` policy confirmation.**  <!-- [claude] added -->
- Confirm keep-last-K + evidence-ref shape matches GoPro's weak-VLM P0 before M2. Low effort.

### Track M — Minimal memory slice

Scope: only `AgentMemory`, `FullReplayMemory`, `ImageEvictingMemory`, `MemoryStore`, and
`InMemoryMemoryStore`. Defer `WindowedMemory`, `StructuredStateMemory`, `CompactingMemory`,
semantic search, durable T2 backends, and LangGraphStore until a named consumer proves the need.

**M0 — Memory insertion-point probe (1-2h).**
- Spike S3: inspect where agent history is assembled, where tool-result images/evidence refs enter
  history, and where profile wiring can select memory without burdening simple flows.
- Output exact hook points, the byte-identical `FullReplayMemory` oracle test, and the
  `ImageEvictingMemory` evidence-ref/fingerprint test plan.

AC/checks:
- No memory code is added before the hook/test plan is written.
- The probe confirms whether M1 can stay inside `agent_planner.py` seams or needs a smaller support
  module.

Execution notes:
- [codex] 2026-06-20 15:35:33 WEST: M0 hook map complete. Control/replay history is assembled in
  `engine/agent.py` (`history.append(AgentToolStep(...))`) and must remain canonical. T1 memory
  belongs at `LLMAgentPlanner._messages_from_history(...)`, where raw history becomes LLM
  `ChatMessage` input. Image/evidence loading is already isolated at `_images_from_output(...)` /
  `ImageInput.from_evidence(...)`; `ImageEvictingMemory` should influence only the `ToolResult`
  images/content shown to the LLM, never `AgentToolStep.output`, `MachineSnapshot`, or
  `ReplayPlanner` comparison state.
- [codex] 2026-06-20 15:35:33 WEST: M1 can stay in `agent_planner.py` plus a small memory support
  module. Do not add a second agent-node abstraction. Suggested seam:
  `AgentMemory.render(request, history, *, base_renderer) -> list[ChatMessage]` or equivalent,
  with `FullReplayMemory` delegating to the existing renderer byte-for-byte.
- [codex] 2026-06-20 15:35:33 WEST: FullReplay oracle test plan: run the same scripted agent
  history with default/no explicit memory and `FullReplayMemory`, then compare serialized
  `LLMRequest.messages` byte-for-byte, including tool-call IDs, tool content, evidence refs,
  image fingerprints, and error flags.
- [codex] 2026-06-20 15:35:33 WEST: ImageEvicting test plan: use `EvidenceRef` image outputs and
  an `image_loader`; assert only the kept image refs become LLM `ToolResult.images`, evicted turns
  retain auditable URI/fingerprint/text content, no raw bytes enter trace/snapshot, and missing
  safe representation fails loudly instead of silently dropping the image.
- [codex] 2026-06-20 15:35:33 WEST: Profile wiring should be opt-in. Simple flows keep the current
  `FullReplayMemory` default with no config burden; unknown memory names must fail at build/profile
  compilation time. Candidate config home is the existing workflow profile/settings path, but
  public shape should be finalized in M4 after M1-M2 prove the protocol.

**M1 — `AgentMemory` protocol and `FullReplayMemory` default (2-4h).**
- Add the memory seam where agent history is assembled.
- Default behavior must be byte-for-byte equivalent to current full replay for simple flows.

AC/tests:
- Existing agent/planner tests pass unchanged.
- New tests prove no-memory/default-memory request history equivalence.

Execution notes:
- [codex] 2026-06-20 15:49:18 WEST: M1 implemented in `ai_workflow_engine.memory`:
  `AgentMemory`, `AgentMemoryRenderContext`, `FullReplayMemory`, and
  `render_full_replay_messages`. `LLMAgentPlanner` now delegates history rendering through this
  seam; default remains full replay. Added byte-identical renderer oracle test.

**M2 — `ImageEvictingMemory` for GoPro-style vision loops (2-4h).**
- Evict large image payloads while retaining text, evidence references, fingerprints, and enough
  trace detail to audit what was removed.
- Missing evidence-store integration or broken references fail loud.

AC/tests:
- Repeated image-heavy turns stay within the configured image budget.
- Evicted images are represented by auditable references, not silent deletion.

Execution notes:
- [codex] 2026-06-20 15:49:18 WEST: M2 implemented `ImageEvictingMemory(keep_last_images=N)`.
  It keeps recent image-bearing tool turns as LLM images, replaces evicted images with an explicit
  `[memory:image_evicted]` evidence-ref/fingerprint audit marker in tool content, and raises if it
  would evict raw image payloads without `EvidenceRef` or keep image evidence without a loader.
  No trace/checkpoint/raw-byte storage was added.

**M3 — `MemoryStore` contract and `InMemoryMemoryStore` (2-4h).**
- Define namespace/run/session key semantics and idempotency expectations.
- Implement only the in-memory development backend; no semantic search or LangGraphStore adapter.

AC/tests:
- Read/write/list/delete semantics are deterministic.
- Namespaces prevent cross-run or cross-agent leakage.

Execution notes:
- [codex] 2026-06-20 15:49:18 WEST: M3 implemented mechanics-only `MemoryStore`,
  `MemoryNamespace`, `MemoryRecord`, and `InMemoryMemoryStore`; search is deterministic exact/filter
  matching only, not semantic; idempotency-key reuse with a different record fails loud.
  LangGraphStore, durable stores, and semantic search remain deferred.
- [codex] 2026-06-21: P-mem-1 reconciled namespace shape before tag:
  `MemoryNamespace(product, tenant, subject, kind)` is now a tuple-compatible `NamedTuple`, so
  MageQA can scope by `target_origin` as `subject` without padding.
- [codex] 2026-06-21 00:07 WEST: static recheck corrected a small overclaim: `_image_evidence_refs`
  was cognitive 14, not <=12. Split image-evidence recursion into focused helpers; every
  `memory.py` function is now <=11 by `complexipy`.

**M4 — Memory wiring/profile selection (2-4h).**
- Make memory selectable from the existing workflow/profile wiring without burdening simple flows.
- Unknown memory mode/name fails at build time with a clear reason.

AC/tests:
- Simple reminder/Todoist-like flows run with no memory overhead beyond the default.
- Vision/agent flows can opt into `ImageEvictingMemory`.

Execution notes:
- [codex] 2026-06-20 15:49:18 WEST: M4 added `resolve_agent_memory(...)` and broadened
  `LLMAgentPlanner(memory=...)` to accept a memory instance, a string mode, or a config-shaped dict
  such as `{"mode": "image_evicting", "keep_last_images": 1}`. Dict config intentionally requires
  `mode`; legacy-looking aliases such as `strategy`/`name` are not accepted. Default is
  `FullReplayMemory`; unknown modes/configs fail at planner/factory construction time.
- [codex] 2026-06-20 21:23:17 WEST: Collapsed memory mode values to one canonical name per policy:
  `full_replay` and `image_evicting`. Aliases such as `full`, `default`, and `image_eviction`
  now fail loudly as unknown modes. Engine package wrapper run after the contract change passed
  (`236 passed`).

**Gate M-A — memory behavior gate.**
- M1-M4 focused tests pass.
- Engine package suite passes.
- Docs record what remains deferred and why.

Execution notes:
- [codex] 2026-06-20 15:55:04 WEST: Gate M-A green. Approved wrapper runs:
  `./test.sh unit -- packages/ai_workflow_engine/tests/test_memory.py packages/ai_workflow_engine/tests/test_agent_planner.py --cov-fail-under=0 -q`
  passed (`20 passed`), and
  `./test.sh unit -- packages/ai_workflow_engine/tests/ --cov-fail-under=0 -q`
  passed (`232 passed`). Deferred remains unchanged: Windowed/Structured/Compacting memory,
  semantic search, durable T2 backends, LangGraphStore, and full contracts-leaf cycle extraction
  are out of the minimal memory slice until a named consumer proves the need.

### Track V — Validator/authoring cleanup after minimal memory

Scope: reduce validator complexity before FlowArtifact v1.5/process-authoring expansion. This is
not a behavior rewrite.

**V1 — Decompose `WorkflowDefinition.validate_graph` (2-4h).**
- Split node-kind, transition, cycle-gate, and planner-field checks into independently testable
  helpers.
- Preserve all current validation errors unless a bug is intentionally fixed and documented.

AC/tests:
- Existing workflow validation tests pass.
- New helper-level tests cover at least one error from each validation concern.

Execution notes:
- [codex] 2026-06-20 16:04:42 WEST: V1 implemented as a behavior-preserving split of
  `WorkflowDefinition.validate_graph(...)` into private validators for graph identity, node
  shapes, transition gates/exhaustion, and cycle-gate law. Added direct validation tests for node
  shape, transition, and cycle concerns. Focused wrapper run passed (`7 passed`).
- [codex] 2026-06-21 00:07 WEST: static recheck found the residual cycle-gate DFS helper at
  cognitive 24. Split it into adjacency/filter/search/visit helpers; `_validate_cycle_gates` is now
  cognitive 1 and the deepest DFS helper is 8. Focused wrapper run passed (`41 passed` with memory,
  state-machine, and agent-planner tests).

**V2 — Decompose `build_definition_from_artifact` (2-4h).**
- Split capability, branch, evaluate, firewall, and retrace checks into helper functions.
- Keep authored-flow validation behavior unchanged.

AC/tests:
- Existing flow-authoring tests pass.
- Error messages remain at least as specific as before.

Execution notes:
- [codex] 2026-06-20 16:04:42 WEST: V2 implemented as a behavior-preserving split of
  `build_definition_from_artifact(...)` into artifact/id validation, per-node validation,
  capability/firewall checks, branch checks, evaluate checks, and compile-only helpers. Added
  authored-flow validation coverage for branch target, evaluate fallback, side-effect allow-list,
  and flow-author firewall errors. Focused wrapper run passed after fixing the test import
  (`6 passed`).

**V3 — Simplify `_apply_eval` directive dispatch (2-3h).**
- Replace the if/elif ladder with directive/effect dispatch only if evaluator code is already being
  touched in this pass.
- Preserve retry, retrace, replan, fallback, and terminal behavior.

AC/tests:
- Focused evaluate-path tests pass for every directive kind.

Execution notes:
- [codex] 2026-06-20 16:04:42 WEST: V3 intentionally skipped in this pass. The plan conditions this
  cleanup on evaluator behavior already being touched; V1/V2 touched validation/authoring only.
  `_apply_eval` remains a known complexity chip for the next evaluator behavior change.

**Gate V-A — authoring-readiness gate.**
- Validator/evaluator focused tests pass.
- Engine package suite passes.
- Only after this gate should FlowArtifact v1.5 / process-authoring expansion be reopened as a
  separate user-approved spec.

Execution notes:
- [codex] 2026-06-20 16:04:42 WEST: Gate V-A green for validator/authoring scope. Approved wrapper
  runs: V1 focused validation set passed (`7 passed`), V2 focused authored-flow set passed
  (`6 passed`), and `./test.sh unit -- packages/ai_workflow_engine/tests/ --cov-fail-under=0 -q`
  passed (`236 passed`). FlowArtifact v1.5/process-authoring expansion remains blocked on a
  separate user-approved spec.
- [codex] 2026-06-20 16:07:15 WEST: Extra full-repo wrapper check also passed:
  `./test.sh unit -- --cov-fail-under=0 -q` (`751 passed, 143 deselected`).

### Track U — Framework user docs and downstream adoption

Scope: documentation for framework consumers, not internal architecture debate. This track explains
how to use the engine, what it covers today, what is intended but not shipped, and how downstream
teams should request new engine/tool features without forcing product-specific code into core.
Because permanent docs require explicit user confirmation before durable edits, draft in this
discussion doc first and migrate only accepted content into package/root READMEs and consumer
handoffs.

**U0 — Docs inventory and claim audit (1-2h).**
- Spike S4: claim audit before writing new user docs.
- Review `packages/ai_workflow_engine/README.md`, `docs/executable-workflow-engine-spec.md`,
  `packages/ai_workflow_engine/docs/gopro-handoff.md`, and
  `packages/ai_workflow_engine/docs/mageqa-handoff.md`.
- Classify each user-visible claim as shipped, intended, future, or stale.
- Identify missing consumer docs for Anki/Todoist/simple-case adoption if needed.

AC/checks:
- No user-facing doc claims memory/FlowArtifact v1.5/ProcessArtifact/browser executor as shipped
  unless the matching implementation and tests exist.
- Each consumer-facing doc names the source-of-truth spec and its own jurisdiction.

<!-- [claude] 2026-06-20: pre-U audit. Track M+V changed reality (memory T1 BUILT, validators split);
     EVERY permanent doc still says memory is "intended/not shipped". codex MUST update these claims
     during U0, not migrate them verbatim — else it ships false "not built" claims OR over-claims T2. -->
**U0 pre-flight — stale-claim ledger (claude, code-verified 2026-06-20).** Fix each before/while
migrating; do not carry forward as-is. (Permanent-doc edits still need user approval per Gate U-A.)

| Permanent doc (file:line) | Stale claim | Corrected status |
|---|---|---|
| spec §13 table ("Workflow memory … [INTENDED] — in design, not built") | memory not built | **T1 BUILT** (`AgentMemory`/`FullReplayMemory`/`ImageEvictingMemory`) + **`MemoryStore` contract + `InMemoryMemoryStore` BUILT**; T2 durable/semantic backends, `Windowed`/`StructuredState`/`Compacting` still **[INTENDED/deferred]**. Split the row. |
| spec §0 memory clarification (`spec.md:55,60` "Not yet built; do not assume it exists") | not built | same split as above; drop "do not assume it exists" for the T1 slice. |
| spec §12 register (`spec.md:469` "Done & closed … v0.4.1") | missing latest | add: minimal memory slice (T1+MemoryStore), `validate_graph`/`build_definition_from_artifact` decomposition, `_images_from_output` complexity fix. |
| spec `:491` "shipped as v0.1.0–v0.4.1" | version behind | do **not** invent a v0.5 tag/version; state that v0.4.1 is the latest release/version marker and memory/validator work is a later working-tree delta until tagged. |
| `working-against-the-freeze.md:3,19` (header says v0.2.0 window; body says "v0.4.0 CURRENT") | internally inconsistent **and** behind | keep it as a tag-to-tag consumer protocol; call v0.4.0 the latest listed freeze tag and add the memory/validator live-branch delta. |
| `ARCHITECTURE.md:55,70-75,81,88` ("intended workflow-memory contract"; "binding design direction, not a shipped unified service yet"; "future memory scope") | T1 now shipped | reword to **shipped T1 vs intended T2**; keep it a summary that points to the spec. |
| `packages/ai_workflow_engine/README.md:27,34,35,63,86,99,116,118` ("intended workflow-memory seam"; "after … workflow memory … proven functional") | T1 now shipped | distinguish shipped T1 from deferred T2; keep README a summary. |
| `README.MD:29,36,39` ("intended workflow-memory contract") | T1 now shipped | same. |

**Two caveats codex MUST honor when documenting memory (from the Track M validation):**
- **Mode aliases RESOLVED (Artem, 2026-06-20):** canonical names are **`full_replay`** and
  **`image_evicting`** ONLY; all other strings (incl. `full`/`default`/`image_eviction`/`semantic_search`)
  raise loudly. Verified green via `test_memory.py` (`./test.sh`, 3 passed). Document exactly these two.
- **S0 replay-determinism CLOSED after review (codex, 2026-06-20):** the
  `ImageEvictingMemory` through `snapshot→resume` test landed and asserts the actual
  `[memory:image_evicted]` marker before fast-forwarding a fresh engine without agent/LLM/tool
  replay. Document the invariant exactly: memory is prompt input, never control state.
- **M4 wiring is DI/kwarg-only, NOT YAML-profile ([claude] verified 2026-06-20):** memory flows via
  `build_llm_agent_capability(..., **planner_kwargs)` → `LLMAgentPlanner(memory=...)`. There is no
  `WorkflowProfile.memory`/config-loader field. Track U must document the **DI/kwarg** path only and
  NOT claim config/YAML-profile selection. Optional polish: promote `memory=` to an explicit param on
  `build_llm_agent_capability` (discoverability) instead of implicit `**planner_kwargs`.

**Keep future as future:** FlowArtifact v1.5 stays **[INTENDED]**, ProcessArtifact/v2 + no-API executor
stay **[FUTURE]** — do not migrate the dynamic-builder plan as shipped. Keep the spec §13 PARTIAL
litmus row (master no-product-loop proof) as **[PARTIAL]**; U must not flip it to complete.

**U1 — General framework quickstart and API map (2-4h).**
- Provide a concise "how to use the framework" path: define `WorkflowDefinition`, register
  capabilities/packs, configure profile/limits/policy, run, inspect result/trace/usage/artifacts.
- Include both a simple one-step flow and a complex flow shape with branch/fanout/evaluate/human/
  subworkflow, without making the simple case look heavyweight.
- Explain main actors and inputs/outputs: engine/executor, workflow, node, capability, profile,
  trace, usage, evidence/artifact refs, memory scope once shipped.

AC/checks:
- A new consumer can answer: "what do I implement, what does the engine implement, and what do I
  pass into/out of `engine.run`?"
- Code snippets use public imports that exist in the package.

**U2 — Capability and coverage matrix (2-4h).**
- Add a matrix for shipped/current vs intended/future features: deterministic/tool/LLM/CLI agent,
  branch, fanout, evaluate/retrace, human suspend, scheduling, budgets, trace, artifacts/evidence,
  memory, authored flows, subworkflows, MCP/browser/tool packs, media packs.
- For each item, state where it lives: core engine, `ai_workflow_tools`, product/domain pack, or
  future feature request.

AC/checks:
- MageQA/GoPro can decide whether a need is already covered, requires a product adapter, requires an
  L2 tool pack, or is a legitimate engine feature request.
- Deferred items explicitly name the gate that unblocks them.

**U3 — Consumer recipes and migration guides (2-4h each).**
- Keep GoPro and MageQA handoffs as recipe docs: workflow skeleton, capability registration, config,
  trace/budget/evidence handling, "do not reimplement" section, migration checklist.
- Add/update simple-case recipes if needed: Todoist/reminder and Anki, emphasizing minimal overhead.
- After Track M, update recipes with the exact memory modes that are shipped and how to opt in.

AC/checks:
- Each recipe has a "done when" verification step and uses repo-approved test commands or product
  smoke checks.
- No recipe tells products to write orchestration loops that the engine should own.

**U4 — Feature-request / gap-intake contract (1-2h).**
- Document how a downstream product asks for new engine/tool capability: workload, why current
  capability seams are insufficient, proposed layer (`L0` core / `L1` executor / `L2` pack / `L3`
  domain), data/privacy/budget impact, acceptance tests, and named consumer proof.
- State the pushback rule: product-specific behavior starts as an adapter/pack; core changes need a
  reusable mechanic and tests.

AC/checks:
- A GoPro/MageQA request can be triaged without reopening architecture basics.
- The template prevents silent scope creep into "build everything in core."

**U5 — Final docs structure and migration (2-4h).**
- Create or update the accepted layered docs set after the code epics land: package docs index,
  getting-started guide, concepts/API map, workflow/capability docs, memory docs, artifacts/evidence
  docs, operations/debugging docs, feature-request template, and consumer recipes.
- Keep `docs/executable-workflow-engine-spec.md` as the architecture source of truth; package docs
  explain how to use the framework and link back to the spec for invariants/rejected decisions.
- Remove or archive stale transient discussion content after durable outcomes are migrated.

AC/checks:
- `packages/ai_workflow_engine/docs/index.md` or equivalent "start here" page links to every
  consumer-facing doc and states shipped/intended/future status.
- A simple consumer and a complex GoPro/MageQA-style consumer can follow docs without reading
  transient discussion files.
- Permanent docs contain no claims that are not backed by code, tests, or an explicit future-stage
  label.

**Gate U-A — consumer handoff gate.**
- U0-U5 content is migrated into permanent docs only after explicit user approval.
- User-facing docs are source-verifiable, contain no stale "ready" claims, and distinguish shipped,
  intended, and future features.
- GoPro/MageQA handoff docs cover their adoption needs or explicitly link to a feature-request gap.
- This gate is required before telling another product/team the framework is ready for adoption.

Execution notes:
- [codex] 2026-06-20 21:54:26 WEST: User approved continuing into permanent docs. U0/U5 migration
  updated root README, `ARCHITECTURE.md`, the binding spec, package README, GoPro/MageQA handoffs,
  and the freeze guide. Permanent docs now state T1 memory is built, T2/T3 memory is deferred,
  canonical modes are `full_replay` / `image_evicting`, S0 non-default replay determinism is closed,
  FlowArtifact v1.5 remains intended, and ProcessArtifact/browser executors remain future. Package
  README now carries the feature-request/gap-intake contract.

### Track P — Product-track follow-ups

Scope: whole-repo product architecture debt from `core`/`database`/`services`/`handlers_modular`.
This is separate from the engine-memory prerequisite. Default timing: after Track M; it may run in
parallel only with a separate owner/branch and no engine cleanup interleaving.

**P0 — Complete the tach map (1-2h).**
- Make `services/` and other namespace-package roots visible to tach before relying on cycle output.
- Prefer source-root configuration; adding `__init__.py` needs explicit approval if it can change
  package semantics.

AC/checks:
- tach/import-linter can see `services` and `handlers_modular`.
- No product code behavior changes.

Execution notes:
- [codex] 2026-06-20 22:07:27 WEST: P0 completed. `services/` is a namespace package, so no
  `services/__init__.py` was added. The existing analysis configs were updated/synced instead:
  `tach.toml` now includes `services` and `composition`, and `.importlinter` names `composition`
  as the outer wiring root. Initial synced map exposed the real cycle set before P1/P2.

**P1 — Extract product composition root (2-4h first slice; repeat as needed).**
- Move `core/container.py` composition concerns to a top-level bootstrap/composition module.
- `core` becomes inner kernel only: interfaces, exceptions, logging, and dependency-free contracts.
- Do not keep old import compatibility aliases unless explicitly approved.

AC/tests:
- `core` no longer imports `database`.
- App startup and repository/container tests pass through `./test.sh`.

Execution notes:
- [codex] 2026-06-20 22:07:27 WEST: P1 completed. Moved `core.container` and
  `core.initialization` into the new top-level `composition` package and rewrote app/tests to the
  new imports without compatibility shims. `core/` now imports no `database`, `services`,
  `handlers_modular`, or `composition` modules.

**P2 — Invert reminder processor handler dependency (2-3h).**
- Remove the service-layer dependency on handler response formatting with a callback/interface or
  shared formatter supplied from the outer wiring layer.
- Claude owns this slice unless reassigned.

AC/tests:
- import-linter contract `services` must not import `handlers_modular` can pass after P1/P2.
- Reminder migration/task-creation behavior remains green.

Execution notes:
- [codex] 2026-06-20 22:07:27 WEST: P2 completed. `ReminderProcessor` now receives parsing/task/
  recipient collaborators plus delivery callbacks from `composition.container`; `GoogleOAuthService`
  receives `oauth_state_manager` by constructor injection; `services.content.router` is configured
  by the composition root instead of importing it. Moved the shared task response helper into
  `helpers.task_responses` and deleted the obsolete handler-local helper module.
  Verification: `tach check` green, `lint-imports` green (`5 kept, 0 broken`), focused
  `./test.sh unit -- tests/unit/test_container_wiring.py tests/unit/test_content_processors.py --cov-fail-under=0 -q`
  green (`18 passed`), and full unit wrapper green (`751 passed, 143 deselected`).

**P3 — Product complexity chips (2-4h each, opportunistic).**
- P3a: split `ParsingService._calculate_precise_time`.
- P3b: split or isolate `with_timeout_and_retry` without changing retry policy.
- P3c: reduce fat handler functions one workflow at a time.
- P3d: split Anki `parse_directives` and `AnkiProcessor.process`.

AC/tests:
- Each chip has focused regression tests for the behavior it extracts.
- No broad product refactor is mixed into engine-memory work.

## Cross-review Evidence

- codex `python-arch-review`, 2026-06-20: root app cycle `core` ↔ `database`; engine package cycle
  `ai_workflow_engine` ↔ `engine` ↔ `nodes`; `ai_workflow_tools` has no tach cycles.
- codex correction, 2026-06-21: root `tach check` is green but does not model engine subpackages
  internally. A local AST import graph over `packages/ai_workflow_engine/ai_workflow_engine` shows the
  current coarse SCC as `ai_workflow_engine` ↔ `ai_workflow_engine.engine`; `nodes` depends on both but
  is not currently in that SCC. Full contracts-leaf extraction remains deferred.
- claude architecture review, 2026-06-20: agrees L0 purity holds (engine does not import
  `ai_workflow_tools`) and identifies the main quantified cluster as graph/flow validation:
  `WorkflowDefinition.validate_graph` cognitive 123 / radon F(64),
  `build_definition_from_artifact` cognitive 63 / radon E(34), `_apply_eval` cognitive 37 /
  radon D(29), and the top↔engine↔nodes cycle.
- Resolution: fix the memory-facing cycle seam before memory; defer validator decomposition until
  after minimal memory but before larger FlowArtifact/process-authoring work.

## Findings

1. **Silent broad-except downgrades — accepted, with narrower fix than proposed.**
   - `packages/ai_workflow_engine/ai_workflow_engine/engine/agent_planner.py`
     `_images_from_output()`: catch `pydantic.ValidationError`, not arbitrary `Exception`.
     Normal arbitrary dicts are expected during recursive probing, so do not warn on every miss.
     Warn/trace only for near-miss image/evidence-shaped payloads; loader/runtime failures must
     surface.
   - `packages/ai_workflow_engine/ai_workflow_engine/nodes/planner.py`
     `_coerce_plan_artifact()`: catch validation errors narrowly and preserve the validation reason
     in the planner failure/log/trace path. A malformed plan must not become unexplained `None`.

2. **`vision.py` no-op conditional — accepted, but reject the unsafe `str()` suggestion.**
   - `packages/ai_workflow_engine/ai_workflow_engine/vision.py` currently assigns identical values
     in both branches before deciding whether to wrap text or extend existing content parts.
   - Cleanup should use `text = last.content` and keep the existing string-vs-list behavior.
     Do not coerce non-string content to `str`, because LangChain messages may already contain a
     valid list of content parts.

3. **`StructuredLLMNode` prompt-template ambiguity — accepted as a narrow contract issue.**
   - `packages/ai_workflow_engine/ai_workflow_engine/engine/llm_node.py` already fails when only one
     split prompt template is provided.
   - Missing guard: passing `prompt_template` together with split static/dynamic templates silently
     chooses split mode. Add a `ValueError`, concise constructor docstring, and tests.
   - Pushback: do not turn this into a large constructor/config-object refactor unless a separate
     concrete need appears; the defect is ambiguity, not parameter count by itself.

## Product Evidence Integrated Into Track P

Source: a whole-repo `python-arch-review` relayed by Artem and checked against the code by Claude.
Those findings are now task-split under Track P above. The important retained facts are:

- `services/` is a namespace package, so tach visibility had to be fixed before trusting product
  cycle output. P0 resolved this through analysis config, without adding `services/__init__.py`.
- The former product composition root inside `core` imported database/service implementations while
  data modules imported inner `core` contracts. P1 resolved this by extracting `composition/`
  without compatibility shims.
- The former reminder processor handler-formatting dependency was inverted in P2 via constructor
  injection and a shared `helpers.task_responses` helper.
- Product complexity chips are real but opportunistic. They do not block Track E or Track M.

Track P timing decision resolved: P0-P2 ran after Track M/U and were not interleaved with engine
memory commits. Remaining Track P work is P3 complexity chips only.

## Global Acceptance / Ready State

- Use repo-approved wrappers only: `./test.sh`, `./test_batch.sh`, or `./test_all_batches.sh`.
- Record completed task IDs, test evidence, and deviations in this doc or the active handoff before
  moving gates.
- Gates E-B and S-A are the prerequisites for starting Track M. Track P is not an engine-memory
  blocker.
- Gate U-A is required before handing the framework to GoPro/MageQA/Todoist/Anki consumers as
  adoption-ready documentation.
- Current state: Tracks E, S0, M, V, U, P0, P1, and P2 are implemented/recorded. Remaining planned
  work is Track P3 complexity chips, each as its own small behavior-preserving refactor.

## [claude] P-obs — Prompt observability (DONE 2026-06-21, additive)

Built per Artem's request (while codex was discussing). **Additive only — touched no codex-dirty
files** (`viz.py` + a new module are clean), so it does not collide with the uncommitted memory work.

- **Static "structure + prompts" manifest:** `viz.render_prompt_manifest(definition, registry)` —
  per node: resolved capability + its prompt template(s) (`StructuredLLMNode` prompt/static/dynamic,
  agent planner `system_prompt`); promptless caps flagged. Read-only, no LLM calls, byte-free.
- **Runtime prompt capture:** `prompt_capture.PromptCapturingLLMClient(inner, trace_sink)` — an
  `LLMCallable` decorator that records each call's rendered prompt as a `WorkflowTraceEvent`
  (`decision="llm:prompt"`) into the same sink; **byte-free** (images → fingerprints, raw data never
  enters the trace). Idiomatic single mechanism (decorate the LLM seam), no engine-core change.
- **Tests:** `tests/test_prompt_observability.py` — 2 passed via `./test.sh` (manifest dumps prompts +
  flags promptless; wrapper records prompt, delegates unchanged, asserts raw bytes absent from trace).

**Deferred wiring (do AFTER codex commits the memory work, to avoid blurring its commit/tag):**
1. Export `render_prompt_manifest` + `PromptCapturingLLMClient` from `ai_workflow_engine/__init__.py`
   (currently importable by full path).
2. Track U: add an **Observability** subsection to the spec/README — structure (mermaid + machine
   card + prompt manifest), runtime (trace events + prompt capture + cost). Docstrings already cover
   usage directly.

**Future (Artem's vision, consider later — not built):** a static graph of the built workflow *with
prompts* (manifest + `workflow_to_mermaid`) and a runtime flow graph (a renderer over existing trace
events) — both are now straightforward renderers, no new engine mechanics needed.
