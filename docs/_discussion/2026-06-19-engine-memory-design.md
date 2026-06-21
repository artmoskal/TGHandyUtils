# Engine memory & context — design (discussion)

Status: **draft — design discussion (codex + claude + user); no code yet**
Created: 2026-06-19
Authors: claude (engine side); reviewing: codex; arbiter: user (Artem)
Permanent home if accepted: new §"Memory" in `docs/executable-workflow-engine-spec.md` + a section
in each consumer handoff. This file is transient — delete after decisions migrate.

## Cross-references
- GoPro handoff (Tier-1 conversation memory, ACs/invariants): `gopro-streaming/docs/_discussion/2026-06-19-agent-memory-context-handoff.md`
- Voice-brain "Ask 2" (deferred session memory, now triggered): `packages/ai_workflow_engine/docs/voice-brain-handoff.md`
- Binding spec (architecture source of truth: layering, worker contract, risk fences, status table):
  `docs/executable-workflow-engine-spec.md` §3, §5, §11-§13
- Existing primitives: `engine/checkpoints.py` (`CheckpointStore`), `snapshot.py` (`MachineSnapshot`), `engine/agent_planner.py` (`_messages_from_history`)

## 1. Problem & three consumers
- **twilio voice brain** — multi-turn conversation; latency-critical; must stay short; resume across calls.
- **GoPro vision agent** — image-heavy episodes; weak local VLM drowns in re-sent crops; needs eviction/compaction (their P0).
- **MageQA orchestrator** — long-horizon; must remember **across runs** what tests ran, what failed, what worked.

These are NOT one feature. They span three tiers (CoALA taxonomy, the field standard):

Architecture clarification (Artem + codex, 2026-06-19): this is **workflow memory**, not merely
conversation memory. Conversation memory is one consumer. The engine needs a pluggable memory plane
so agents, planner nodes, and subworkflows can explicitly read/write prior workflow state when
allowed: retries already attempted, evidence discovered, rejected plan branches, produced artifacts,
evaluator decisions, and what worked or failed in earlier runs. The core ownership is the
domain-neutral protocol, scopes, budget/trace/privacy enforcement, provenance, and failure behavior;
products own record shapes and recall policy.

## 2. Memory taxonomy (three tiers)
| Tier | Scope | Question it answers | Consumer | Status |
|---|---|---|---|---|
| **T1 Working** | within one run | "what have I done THIS episode; what fits in context now" | GoPro vision, twilio turn | design target / build-now candidate |
| **Workflow state memory** | within one workflow/session, across retries/subworkflows | "what has this workflow already discovered, rejected, produced, or proven" | GoPro pipelines, MageQA plan execution, recursive agent/subworkflow runs | design target as engine-owned scoped memory |
| **T2 Episodic/long-term** | across runs | "what did I try before; what failed/worked" | MageQA orchestrator | contract design; implementation scope still to confirm |
| **T3 Execution persistence** | resume a suspended workflow run | "continue this workflow run from its checkpoint" | durable workflows | built mechanics (`MachineSnapshot`+`CheckpointStore`) |
| **Voice session memory** | across calls | "continue this caller's conversation history" | Twilio voice brain | product-owned for v1; possible future engine-memory consumer |

## 3. What exists vs what's needed
**Ours (verified):** `_messages_from_history` (T1 full-replay default), `CheckpointStore` +
`MachineSnapshot` for suspended workflow runs, budget matrix, unified trace, privacy (no raw bytes),
bounded loop. Voice cross-call turn history is not provided by those checkpoint primitives.

**LangGraph 1.2.4 (verified in our env):**
- `langgraph.checkpoint.*` (BaseCheckpointSaver/MemorySaver/sqlite) → T3 storage backend ✅
- `langgraph.store.BaseStore` + `InMemoryStore` + `SearchOp` → **T2 storage backend** (namespaced K/V + semantic/filter search) ✅
- Short-term summarization/compaction (T1) → **NOT a built-in facility**; a pattern you implement.

### langmem — what it offers and why it's valuable (do NOT casually replace this; read before re-deciding)
langmem (LangChain's long-term-memory SDK, built on `BaseStore`) is a **genuinely valuable tool**, not redundant. What it offers that raw `BaseStore` does not:
- **An LLM "memory manager"** that *reads conversations and infers what to remember*, then **consolidates** — merges related facts, resolves contradictions, updates/deletes stale memories. That consolidation logic is real, non-trivial work we would otherwise reinvent.
- **Three first-class memory kinds** (the CoALA standard): **semantic** (facts/preferences; "collections" or a structured "profile"), **episodic** (past experiences incl. success/**failure** few-shots), **procedural** (learned rules/persona that adapt behaviour).
- **Two modes:** hot-path (agent calls a save tool mid-task) and **background** (async extraction so the live path stays fast).

**Where it earns its place for us later:** the "learn-the-user-over-time" / chat-personalization case (twilio, and any conversational product) — auto-extracting preferences and consolidating across sessions is exactly its sweet spot, and it would save us building the extraction+consolidation engine. **Plan of record: consider a product-level langmem adapter for that semantic/episodic conversational-memory case, behind our own interface, only when a named product asks for it** (so its LangChain types don't leak, and its internal LLM calls route through our budget/metering/trace/privacy seam).

**Where it is the wrong tool:** the **T2 MageQA-orchestrator** case — there the orchestrator already *knows* the structured fact ("test X → fail, reason Y"); paying an LLM to *infer* it from a transcript is slower, costlier, and less auditable than a deterministic `put()`. So T2 orchestrator memory is built on **raw `BaseStore` behind our `MemoryStore` protocol**, NOT langmem.

**Net:** langmem is in our toolbox for future conversational long-term memory, not an engine-core dependency and not the orchestrator-memory substrate. Raw deterministic stores remain the path for MageQA-style orchestration memory. The open question is timing/wrapping (D1), not whether langmem has value — it does.

## 4. Proposed architecture — engine owns mechanics, LangGraph hidden, product owns shapes
- **T1 `AgentMemory` protocol** (GoPro's design, adopted): `render(request, history, working_state) -> messages` + `update(working_state, step)`. Build-now implementations: `FullReplayMemory` (default, byte-identical) and `ImageEvictingMemory` (GoPro P0 vision unblocker). Deferred implementations: `WindowedMemory`, `StructuredStateMemory`, and `CompactingMemory`. DI-wired, `WorkflowProfile`-selectable, unknown name → loud build error.
- **Workflow memory scopes** (new): every node/capability that uses memory declares read/write scope
  rather than receiving an implicit transcript dump. Target shape: `memory_read = none | run |
  workflow | project | domain`, `memory_write = none | working | workflow | project`, and
  `memory_mode = summary | cited_refs | full_records_when_allowed`. Simple flows keep all memory
  disabled. Complex flows can give agents/subworkflows cited prior state without making memory a
  hidden second orchestrator.
- **T2 `MemoryStore` protocol** (new, mirrors `CheckpointStore`/`TraceSink`): `put / get / search(query|filter) / delete`. **Namespace is a STRUCTURED TUPLE** (product, tenant/account, workflow_type, run-family/orchestrator, schema_version, memory_kind) — NOT an arbitrary string (codex). The protocol must define **idempotency, write ordering, and parallel-orchestration behavior** — T2 concurrency was the draft's biggest gap (codex). Recalled memory is **non-authoritative + evidence-linked** (every record carries source/provenance + schema_version). Build-now backend: `InMemoryMemoryStore` for single-writer/dev. Deferred backends: `LangGraphStore`/semantic recall and any durable adapter until embedding recall or production durability is a named need. `JsonlMemoryStore`, if added, is **dev/test only** — not a safe default for parallel orchestrators without locking / atomic append-replace / corruption detection / explicit single-writer limits (codex). Product owns the record SHAPE; engine owns store mechanics + namespacing + loud/privacy/budget rules.
- **Adjacent dependency (NOT a memory tier): an artifact/evidence store for raw media.** Raw bytes live there via `EvidenceRef` / `image_loader` (already in `agent_planner.py`), never inside T1/T2/T3 (codex). Memory carries refs + fingerprints + observations only.
- **Interaction with universal worker contract (2026-06-20).** Memory is exposed to workers only
  through declared invocation-envelope scope, not through implicit transcript dumps or hidden global
  state. Python workers, API/local LLMs, `claude -p` / `codex exec`, bounded agents, media tools,
  human clarification, and subworkflows must all receive memory the same way: scoped, byte-free,
  evidence-linked, replay-safe, and traceable. Workers may summarize or use memory, but
  engine-owned validation/retry/transition logic remains outside the worker.
- **T3** — already have; optionally wrap LangGraph checkpointer behind `CheckpointStore` for sqlite/postgres durability.
- **Future context-boundary compaction rule.** `CompactingMemory` is deferred until a real context-limit need appears. If built, it must summarize older turns into structured, cited notes using a product-supplied summarizer through our budget/trace/privacy seam; it must trace counts/reasons, preserve source refs, and fail loudly rather than silently dropping findings.
- **Interaction with generated process authoring (2026-06-20).** `FlowArtifact` v1.5 may add
  authorable `fanout`, registered `subworkflow` refs, and explicit `input_key` / `output_key`
  mapping, but it must not create its own hidden memory model. Authored nodes may declare memory
  scopes only after the engine-owned memory protocol exists; until then, process authoring remains
  byte-free and evidence-ref-only. Full `ProcessArtifact` / `FlowArtifact` v2, with artifact
  routing and workflow-memory scopes baked into generated pipelines, is future-stage and requires
  explicit Artem approval after v1.5 + memory/artifact-store proofs pass.

## 5. Invariants (from GoPro + ours)
1. Backward compatible — default = full replay; Anki/existing flows unchanged.
2. Fail loudly; compaction summarizes + traces, never silently loses findings.
3. No raw media bytes in trace/checkpoints/evicted context — fingerprints only.
4. Uniform budgets/bounds apply to every policy and every store-backed LLM call.
5. No LangGraph/LangChain types in the public API (T2 store adapter hides `BaseStore`).
6. Coverage is NOT a memory concern (tiling/fanout stays deterministic — GoPro invariant #5).
7. **Memory is non-authoritative + evidence-linked (codex).** Every compacted or recalled item carries source step/evidence IDs, provenance, schema_version, and a trace event; recall must never present stale/cross-run facts as authoritative; missing-safe-representation is a HARD error.
8. **`FullReplayMemory` is the byte-identical compatibility oracle** for `_messages_from_history` (`agent_planner.py:121`) — not "equivalent enough" (codex). Do not let T1 become a second hidden state machine.
9. **Image eviction drops BYTES, never findings (codex).** If a media-bearing result lacks a stable `EvidenceRef`/fingerprint/textual observation, eviction fails loudly.
10. **Compaction provenance (codex).** Summaries cite source step/evidence IDs, validate against a product-owned schema, run through the normal `LLMCallable` budget/trace path, and never compact summary-of-summary without source references.

## 6. Open decisions (for user, with codex input)
- **D1 — langmem (NOT "whether it's valuable" — it is):** adopt-behind-our-interface for the conversational semantic/episodic case now, or stage it after T1/T2 land? (Orchestrator T2 stays on raw `BaseStore` either way.) **codex converges:** not engine-core / not for orchestration; a product-level adapter behind our interface, metered, for chat personalization later — consistent with "langmem is valuable, adopt behind our seam."
- **D2 — T2 store backend:** engine-owned `MemoryStore` protocol + InMemory dev/default backend
  only for the build-now slice. LangGraph-Store/semantic adapter is deferred, not part of the
  initial implementation. Added: Jsonl is dev/test-only if introduced; the parallel-orchestrator
  default needs concurrency-safety. → looks settled pending user nod.
- **D3 — scope/sequence:** **codex: T1 first but NARROWLY** — `AgentMemory` seam + `FullReplayMemory` byte-equivalence proof + `ImageEvictingMemory` (GoPro P0) ONLY; do NOT build Windowed/StructuredState/Compacting until invariants are proven; then T2 with the namespace/concurrency fences. (vs T2 first for Artem's active need — user to pick.)
- **D4 — graduation:** **codex agrees graduate now — but only the MECHANICS** graduate; product schemas + domain recall policy stay outside core.
- **D5 — generated-process sequencing:** `FlowArtifact` v1.5 is allowed as the small near-term
  expansion, but it should stay independent of memory until memory has a real engine-owned protocol.
  Full `ProcessArtifact`/v2 is not approved by this doc; it needs a separate user decision after the
  narrower memory/artifact/process-authoring pieces prove functional.

## 7. Process
codex reviews this draft (architecture + D1/D2 especially) → user arbitrates D1–D5 → claude writes the
implementation plan (protocol signatures, AC-M1 default-equivalence proof, test list, sequencing) per
GoPro handoff §8 → implement T1 → T2, package tests green between each. No engine code until D1–D5 settled.

Prerequisite before memory implementation: close the 2026-06-20 engine-seam cleanup slice in
`docs/_discussion/2026-06-20-engine-review-followups.md` — fail-loud parsing/coercion hygiene and
the minimal executor/nodes runtime-state cycle break. Do not wait for broad validator decomposition
or root-app cleanup before the T1/T2 build-now memory slice.

## 8. codex review (run 1, 2026-06-19) — integrated above
First codex pass (gpt-5.5) verdict: **"3-tier split is right, but the draft needs stronger fences
before implementation."** Approved T1/T2/T3 + D4-mechanics-only; agreed D2 (engine-owned protocol)
and D1 (langmem not engine-core). Its material additions — now folded into §4/§5/§6 — were: T2
concurrency + **structured namespace tuple**; a separate **artifact/evidence store** for raw bytes;
`FullReplayMemory` as a **byte-identical oracle**; **compaction provenance** (source IDs + schema +
budget/trace, no summary-of-summary); eviction **fails loud** without a safe representation; Jsonl
unsafe as a parallel default. **Biggest risk it named: silent distortion at memory boundaries** —
fenced by "memory is non-authoritative + evidence-linked by default" (invariant 7).
Raw verdict: `/tmp/codex_memory_review.txt`. (Artem is running a second, headful codex pass in
parallel; integrate that when it returns.)

## 9. [claude] critical review — of this doc AND codex's vision (2026-06-19)
Not rubber-stamping. Five criticisms, classified:

- **[claude] critique — SCOPE/YAGNI (high).** The design has ballooned: 3 tiers + 5 T1 policies +
  MemoryStore + namespace tuples + evidence store + compaction provenance + langmem + semantic
  search. We have exactly TWO proven needs: GoPro `ImageEvictingMemory` (P0) and MageQA orchestrator
  recall. Everything else (Windowed/StructuredState/Compacting/semantic search/langmem) is
  speculative — and we have a fence against exactly that (spec §11 risk #4: named consumer required).
  Position: ship **seam + FullReplay + ImageEvicting**, design the rest as named-consumer-gated.
  codex's "T1 narrow first" agrees on sequence; make it **policy**, not just order.

- **[claude] critique — SOUL TENSION: memory vs determinism (high).** The engine's identity is a
  deterministic, replayable spine (FullReplay oracle, ReplayPlanner, MachineSnapshot). Memory —
  especially T2 recall feeding decisions — is hidden non-deterministic state that can make runs
  unreproducible. codex's "non-authoritative" fence helps but the doc never states the **memory ×
  replay/resume** rule. Gap: when a run resumes from `MachineSnapshot`, does memory/ compaction
  replay? Endgame invariant must be explicit: **memory is INPUT/context, never CONTROL; it must not
  break replay; a recorded run replays identically regardless of live memory state.**

- **[claude] critique — IS T2 EVEN ENGINE-CORE? (medium-high).** A cross-run episodic store is
  arguably PRODUCT/L2 domain state, not L0. The engine runs one execution; "what ran across runs"
  is the orchestrator product's concern. Position: engine **defines the `MemoryStore` contract +
  ships in-memory/dev only**; products/L2 bring real persistence, recall policy, and schemas. Don't
  let the engine drift into being a memory database. (codex's "mechanics-only graduate" + "Jsonl
  dev-only" point here but stop short of saying it.)

- **[claude] critique — EVIDENCE STORE OVERLAP (medium).** codex's "artifact/evidence store for raw
  bytes" likely duplicates EXISTING machinery: `EvidenceRef`, `WorkflowArtifact`, `input_assets`,
  checkpoints. Reconcile against what exists — almost certainly the SAME concept, not a new parallel
  store. Verify before naming a new component.

- **[claude] critique — codex's vision, meta (medium).** codex's catches (T2 concurrency/namespace,
  byte-identical oracle, evidence-linking) are real and valuable. But codex FORTIFIED the 3-tier
  framing rather than challenging its scope — it added fences to a system we haven't proven we need
  at full size. Its own "go narrow" rec is the antidote. Treat codex's fences as **constraints for
  WHEN each piece is built**, not a mandate to build all of it now.

**Net:** the 3-tier decomposition is sound and codex's fences are correct-in-principle; the risk is
**building a memory framework ahead of proven need and quietly eroding the deterministic spine.**
Endgame keeps memory subordinate: bounded, non-authoritative, replay-safe context — same discipline
as every other AI-on-rails mechanic.

## 10. Scope discipline (YAGNI) — build now / defer / cut (claude, 2026-06-19)
YAGNI = build only what has a present, named consumer; seams/contracts are cheap (build early),
implementations are expensive (defer). Project fence: spec §11 risk #4 (named consumer required).

| Component | Verdict | Why |
|---|---|---|
| `AgentMemory` **seam** (T1 protocol) | **Build now** | Enabling contract; cheap; GoPro P0 needs it. |
| `FullReplayMemory` (default) | **Build now** | Backward-compat; byte-identical oracle. |
| `ImageEvictingMemory` | **Build now** | GoPro's actual P0 — the one proven concrete need. |
| `MemoryStore` (T2) **contract** | **Define now, defer impl** | Cheap contract MageQA needs soon; ship in-memory/dev backend only. |
| `WindowedMemory` | **Defer** | No consumer demanding it; trivial later (likely voice). |
| `StructuredStateMemory` | **Defer** | GoPro's own P1; build when their vision flow needs it beyond eviction. |
| `CompactingMemory` (summarization) | **Defer** | Riskiest (provenance/schema/budget); build at a real context-limit need (MageQA long audits). |
| T2 semantic search (LangGraph-Store adapter) | **Defer** | Only when embedding recall is real; "failed/worked" is likely exact-match/filter first. |
| New "evidence/artifact store" | **Cut — use existing** | `EvidenceRef`/`WorkflowArtifact`/`input_assets` already exist; no parallel concept. |
| langmem in the engine | **Don't implement now (future, product-level)** | Future chat-personalization adapter behind our interface; not engine scope now. |
| T2 cross-run persistence as L0 core | **Cut from core** | Product/L2 domain state; engine only *defines* the contract. |

**Build-now footprint:** `AgentMemory` seam + `FullReplay` + `ImageEvicting`, plus *defining* (not
fully implementing) the `MemoryStore` contract. Everything else deferred behind the named-consumer
fence or cut. ≈ codex's "T1 narrow first," nothing more.

## 11. Identity framing correction (Artem, 2026-06-19) — affects spec §0
**The engine is NOT a "state machine" — it is a FRAMEWORK FOR UNIVERSAL AI PROCESSING.** The
state machine (LangGraph `StateGraph`) is a **hidden execution backend**, one mechanism — naming the
framework after it is wrong and would couple the identity to a replaceable backend. The bounded /
deterministic / replayable properties are framework GUARANTEES, not the identity. Affordances
(navigate / author / wait / **remember**) are capabilities of the framework, expressed on rails it
enforces.

Consequence for memory: memory is one more universal processing mechanic the framework owns
(mechanics) while products own shapes — consistent with the layering, independent of the state-machine
backend.

**Status update (codex, 2026-06-20 11:49:14 WEST):** the binding spec now reflects this identity
rewrite in §0: "framework for universal AI processing"; LangGraph/state-machine execution is the
hidden backend/engine room, not the product identity. This closes D0 for the permanent spec. The
remaining memory scope/sequence decisions below are still tracked separately.

## 12. Decisions to confirm (consolidated, for user + codex)
- **D0 — identity:** applied in `docs/executable-workflow-engine-spec.md` §0; not blocking memory work.
- **D1 — langmem:** valuable; future product-level adapter behind our interface for chat personalization; NOT engine-core, NOT for T2 orchestration. (codex + claude agree; user nod.)
- **D2 — T2 backend:** engine-owned `MemoryStore` protocol + InMemory dev/default backend now.
  LangGraphStore/semantic adapter is deferred until embedding recall is a named need; Jsonl remains
  dev/test-only if used at all. (codex + claude agree; user nod.)
- **D3 (open fork) — sequence:** T1-narrow-first (codex + GoPro P0) vs T2-first (Artem's active MageQA need)? [user]
- **D4 — graduate now, mechanics only** (product schemas/recall policy stay outside core). (agree; user nod.)
- **D5 (NEW, from §10) — scope cuts/defers:** confirm the build-now/defer/cut table — anything parked that you want built now? [user]

## 13. For codex (pass 2) — what to challenge
This doc is the handoff; the user runs codex manually. Focus codex on:
1. **D0 identity reframe:** does "framework for universal AI processing; state machine = hidden
   backend" hold — or does demoting the state machine lose the bounded/deterministic *guarantee*
   that the rails framing made explicit? How to keep the guarantee without the identity coupling.
2. **§10 scope cuts (D5):** is the build-now footprint (seam + FullReplay + ImageEvicting + define
   MemoryStore) right? Anything deferred that a GoPro/MageQA P0 actually needs now — especially is
   deferring `CompactingMemory` safe given MageQA long-horizon runs?
3. **memory × determinism (claude critique §9):** is "memory is input, never control; replay-safe;
   a recorded run replays identically regardless of live memory" sufficient, or are there concrete
   replay-break scenarios (e.g. compaction inside a run that later resumes from a snapshot)?
4. **T2 placement:** is cross-run episodic memory L0-core, or product/L2 with the engine only
   defining the contract (claude §9 / codex "mechanics-only")?
5. **D3 sequence:** T1-narrow-first vs T2-first.
